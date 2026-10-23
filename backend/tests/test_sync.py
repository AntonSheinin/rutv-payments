"""Malformed provider reads must not masquerade as verified financial data."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.errors import ReconciliationError
from app.models import Subscription, SyncState, now
from app.schemas import SyncInput
from app.sync import refresh_subscription, synchronize


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "price", [None, {}, {"value": "99.00"}, {"value": "99.00", "currency_code": "GBP"}]
)
async def test_missing_or_unsupported_imported_price_preserves_verified_snapshot(sessions, price):
    """An existing imported price must not be certified by an incomplete provider response."""
    previous = now() - timedelta(days=1)
    with sessions() as db:
        row = Subscription(
            provider_id="SUBPRICE",
            status="ACTIVE",
            amount=Decimal("50.00"),
            currency="ILS",
            last_synced_at=previous,
        )
        db.add(row)
        db.commit()
        paypal = AsyncMock()
        paypal.request.return_value = {
            "id": row.provider_id,
            "status": "CANCELLED",
            "plan": {
                "billing_cycles": [
                    {"tenure_type": "REGULAR", "pricing_scheme": {"fixed_price": price}}
                ]
            },
        }
        with pytest.raises(ReconciliationError):
            await refresh_subscription(db, row.provider_id, paypal)
        db.rollback()
        db.refresh(row)
        assert (row.amount, row.currency, row.status, row.last_synced_at) == (
            Decimal("50.00"),
            "ILS",
            "ACTIVE",
            previous,
        )
        assert paypal.request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,payload",
    [
        ("subscriptions", {}),
        ("subscriptions", {"subscriptions": None, "links": []}),
        ("subscriptions", {"subscriptions": [None], "links": []}),
        ("subscriptions", {"subscriptions": []}),
        ("subscriptions", {"subscriptions": [], "links": [{}]}),
        ("history", {}),
        ("history", {"transaction_details": [], "total_pages": None}),
        ("history", {"transaction_details": [], "total_pages": True}),
        ("history", {"transaction_details": [], "total_pages": -1}),
        ("history", {"transaction_details": None, "total_pages": 1}),
        ("subscription", {}),
    ],
)
async def test_malformed_import_never_advances_success(sessions, settings, scope, payload):
    """Missing collections or pagination are errors, not successfully imported empty pages."""
    previous = now() - timedelta(days=1)
    with sessions() as db:
        state = SyncState(scope=scope, last_success=previous, checkpoint={"page": 9})
        row = Subscription(
            provider_id="SUBLIST", status="ACTIVE", amount=Decimal("50.00"), currency="ILS"
        )
        db.add_all([state, row])
        db.commit()
        paypal = AsyncMock()

        async def request(method, path, **kwargs):
            if path == "/v1/billing/subscriptions/SUBLIST":
                return {
                    "id": "SUBLIST",
                    "status": "ACTIVE",
                    "plan": {
                        "billing_cycles": [
                            {
                                "tenure_type": "REGULAR",
                                "pricing_scheme": {
                                    "fixed_price": {"value": "50.00", "currency_code": "ILS"}
                                },
                            }
                        ]
                    },
                }
            return payload

        paypal.request.side_effect = request
        data = SyncInput(
            scope=scope,
            resource_id=row.id if scope == "subscription" else None,
            start_date="2026-10-01",
            end_date="2026-10-02",
        )
        with pytest.raises(ReconciliationError):
            await synchronize(db, data, paypal, settings)
        db.rollback()
        db.refresh(state)
        assert state.last_success == previous and state.checkpoint == {"page": 9}
        assert all(call.args[0] == "GET" for call in paypal.request.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,payload,next_page",
    [
        ("subscriptions", {"subscriptions": [], "links": []}, None),
        ("subscriptions", {"subscriptions": [], "links": [{"rel": "next"}]}, 2),
        ("history", {"transaction_details": [], "total_pages": 0}, None),
    ],
)
async def test_explicit_empty_page_is_valid_and_does_not_fetch_another_page(
    sessions, settings, scope, payload, next_page
):
    """A verified empty result remains distinct from absent data and automatic pagination."""
    with sessions() as db:
        paypal = AsyncMock()
        paypal.request.return_value = payload
        result = await synchronize(
            db,
            SyncInput(scope=scope, start_date="2026-10-01", end_date="2026-10-02"),
            paypal,
            settings,
        )
        assert result["processed"] == result["excluded"] == 0
        assert result["next_page"] == next_page
        assert paypal.request.await_count == 1


@pytest.mark.parametrize(
    "timezone,start,end",
    [
        ("America/Los_Angeles", "2026-10-19T07:00:00+00:00", "2026-10-20T07:00:00+00:00"),
        ("Asia/Tokyo", "2026-10-18T15:00:00+00:00", "2026-10-19T15:00:00+00:00"),
    ],
)
def test_installment_window_uses_operator_timezone(timezone, start, end):
    """The selected date means midnight in the operator timezone, not server midnight."""
    from app.sync import window

    actual = window(
        SyncInput(
            scope="subscription",
            resource_id="local-subscription",
            start_date="2026-10-19",
            end_date="2026-10-19",
            timezone=timezone,
        )
    )
    assert tuple(value.isoformat() for value in actual) == (start, end)


def test_calendar_window_accepts_31_days_across_dst_and_rejects_32(monkeypatch):
    """A repeated daylight-saving hour must not make a 31-calendar-day window invalid."""
    from datetime import UTC, datetime

    from app.sync import window
    from fastapi import HTTPException

    monkeypatch.setattr("app.sync.now", lambda: datetime(2026, 12, 1, tzinfo=UTC))
    data = SyncInput(
        scope="history",
        start_date="2026-10-10",
        end_date="2026-11-09",
        timezone="America/Los_Angeles",
    )
    start, end = window(data)
    assert end - start == timedelta(days=31, hours=1)
    data.end_date += timedelta(days=1)
    with pytest.raises(HTTPException):
        window(data)


def test_invalid_calendar_timezone_is_rejected():
    """An unknown timezone cannot silently use UTC or the server timezone."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Unknown timezone"):
        SyncInput(
            scope="history", start_date="2026-10-19", end_date="2026-10-19", timezone="invalid/zone"
        )
