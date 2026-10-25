"""Malformed provider reads must not masquerade as verified financial data."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.errors import ReconciliationError
from app.models import Subscription, SyncState, Transaction, now
from app.reporting import Filters, statistics
from app.schemas import SyncInput
from app.sync import refresh_subscription, synchronize
from sqlalchemy import select
from support import capture


async def test_subscription_import_reads_all_active_suspended_pages(sessions, settings):
    """One explicit import traverses filtered pages, deduplicates IDs and excludes other states."""
    paypal = AsyncMock()

    async def request(method, path, **kwargs):
        assert method == "GET"
        if path == "/v1/billing/subscriptions":
            params = kwargs["params"]
            assert params["statuses"] == "ACTIVE,SUSPENDED" and params["page_size"] == 20
            if params["page"] == 1:
                return {
                    "subscriptions": [
                        {"id": "I-ACTIVE", "status": "ACTIVE"},
                        {"id": "I-CANCELLED", "status": "CANCELLED"},
                    ],
                    "links": [{"rel": "next"}],
                }
            assert params["page"] == 2
            return {
                "subscriptions": [
                    {"id": "I-ACTIVE", "status": "ACTIVE"},
                    {"id": "I-SUSPENDED", "status": "SUSPENDED"},
                ],
                "links": [],
            }
        remote = path.rsplit("/", 1)[-1]
        assert remote in {"I-ACTIVE", "I-SUSPENDED"}
        return {
            "id": remote,
            "status": "ACTIVE" if remote == "I-ACTIVE" else "SUSPENDED",
            "plan": {
                "billing_cycles": [
                    {
                        "tenure_type": "REGULAR",
                        "pricing_scheme": {
                            "fixed_price": {"value": "10.00", "currency_code": "ILS"}
                        },
                    }
                ]
            },
        }

    paypal.request.side_effect = request
    async with sessions() as db:
        result = await synchronize(db, SyncInput(scope="subscriptions"), paypal, settings)
        assert result["processed"] == 2 and result["excluded"] == 1
        assert result["next_page"] is None
        assert set(await db.scalars(select(Subscription.status))) == {"ACTIVE", "SUSPENDED"}
        assert not list(await db.scalars(select(Transaction)))
    assert paypal.request.await_count == 4


async def test_failed_later_subscription_page_does_not_claim_complete_import(sessions, settings):
    """Previously committed agreement facts survive; coverage never advances on page failure."""
    paypal = AsyncMock()
    paypal.request.side_effect = [
        {"subscriptions": [{"id": "I-FIRST", "status": "ACTIVE"}], "links": [{"rel": "next"}]},
        {
            "id": "I-FIRST",
            "status": "ACTIVE",
            "plan": {
                "billing_cycles": [
                    {
                        "tenure_type": "REGULAR",
                        "pricing_scheme": {
                            "fixed_price": {"value": "10.00", "currency_code": "ILS"}
                        },
                    }
                ]
            },
        },
        ReconciliationError("Invalid provider result list: subscriptions"),
    ]
    async with sessions() as db:
        with pytest.raises(ReconciliationError):
            await synchronize(db, SyncInput(scope="subscriptions"), paypal, settings)
        await db.rollback()
        assert list(await db.scalars(select(Subscription.provider_id))) == ["I-FIRST"]
        assert await db.scalar(select(SyncState).where(SyncState.scope == "subscriptions")) is None


async def test_repeated_installment_window_keeps_one_collection(sessions, settings, monkeypatch):
    """Explicitly re-reading an installment preserves its subscription/payment links and totals."""
    from datetime import datetime

    monkeypatch.setattr("app.sync.now", lambda: datetime.fromisoformat("2026-10-25T12:00:00+00:00"))
    paypal = AsyncMock()
    remote = {
        "id": "I-HEALTH",
        "status": "ACTIVE",
        "create_time": "2026-10-21T10:00:00Z",
        "plan": {
            "billing_cycles": [
                {
                    "tenure_type": "REGULAR",
                    "pricing_scheme": {"fixed_price": {"currency_code": "ILS", "value": "10.00"}},
                }
            ]
        },
    }
    installment = {
        "id": "INSTALLMENT-ONCE",
        "status": "COMPLETED",
        "time": "2026-10-21T12:00:00Z",
        "amount_with_breakdown": {"gross_amount": {"currency_code": "ILS", "value": "10.00"}},
    }
    paypal.request.side_effect = [
        remote,
        {"transactions": [installment]},
        remote,
        {"transactions": [installment]},
    ]
    async with sessions() as db:
        sub = Subscription(provider_id="I-HEALTH", status="ACTIVE", currency="ILS")
        db.add(sub)
        await db.commit()
        data = SyncInput(
            scope="subscription", resource_id=sub.id, start_date="2026-10-21", end_date="2026-10-21"
        )
        for _ in range(2):
            await synchronize(db, data, paypal, settings)
        rows = (await db.scalars(select(Transaction))).all()
        assert len(rows) == 1
        assert rows[0].subscription_id == sub.id and rows[0].payment_id is not None
        result = await statistics(db, Filters(), "sandbox")
        assert result["trends"][0]["amount"] == "10.00"
        assert result["currencies"][0]["completed_count"] == 1
    assert all(call.args[0] == "GET" for call in paypal.request.await_args_list)


async def test_history_import_commits_verified_items_and_reports_exclusions(
    sessions, settings, payment_logs
):
    """One bad record cannot certify invalid money or discard another verified collection."""
    paypal = AsyncMock()
    entries = [
        {
            "transaction_info": {
                "transaction_id": identifier,
                "transaction_event_code": "T0006",
                "transaction_amount": {"value": "50.00", "currency_code": currency},
            }
        }
        for identifier, currency in [("VALID", "ILS"), ("MISMATCH", "ILS"), ("UNSUPPORTED", "GBP")]
    ]

    async def request(method, path, **kwargs):
        if path == "/v1/reporting/transactions":
            return {"transaction_details": entries, "total_pages": 1}
        identifier = path.rsplit("/", 1)[-1]
        return {
            **capture(),
            "id": identifier,
            "amount": {
                "value": "51.00" if identifier == "MISMATCH" else "50.00",
                "currency_code": "ILS",
            },
        }

    paypal.request.side_effect = request
    async with sessions() as db:
        result = await synchronize(
            db,
            SyncInput(scope="history", start_date="2026-10-21", end_date="2026-10-22"),
            paypal,
            settings,
        )
        assert result["processed"] == 1 and result["excluded"] == 2
        assert result["next_page"] is None
        assert list(await db.scalars(select(Transaction.provider_id))) == ["VALID"]
    assert paypal.request.await_count == 3
    assert all(call.args[0] == "GET" for call in paypal.request.await_args_list)
    assert payment_logs.text.count("synchronization_started") == 1
    assert payment_logs.text.count("synchronization_completed") == 1
    assert "processed=1 excluded=2 next_page=None" in payment_logs.text
    assert "Reporting resource amount mismatch" in payment_logs.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "price", [None, {}, {"value": "99.00"}, {"value": "99.00", "currency_code": "GBP"}]
)
async def test_missing_or_unsupported_imported_price_preserves_verified_snapshot(sessions, price):
    """An existing imported price must not be certified by an incomplete provider response."""
    previous = now() - timedelta(days=1)
    async with sessions() as db:
        row = Subscription(
            provider_id="SUBPRICE",
            status="ACTIVE",
            amount=Decimal("50.00"),
            currency="ILS",
            last_synced_at=previous,
        )
        db.add(row)
        await db.commit()
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
        await db.rollback()
        await db.refresh(row)
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
        ("subscriptions", {"subscriptions": [], "links": [{"rel": "next"}]}),
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
    async with sessions() as db:
        state = SyncState(scope=scope, last_success=previous, checkpoint={"page": 9})
        row = Subscription(
            provider_id="SUBLIST", status="ACTIVE", amount=Decimal("50.00"), currency="ILS"
        )
        db.add_all([state, row])
        await db.commit()
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
        await db.rollback()
        await db.refresh(state)
        assert state.last_success == previous and state.checkpoint == {"page": 9}
        assert all(call.args[0] == "GET" for call in paypal.request.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,payload,next_page",
    [
        ("subscriptions", {"subscriptions": [], "links": []}, None),
        ("history", {"transaction_details": [], "total_pages": 0}, None),
    ],
)
async def test_explicit_empty_page_is_valid_and_does_not_fetch_another_page(
    sessions, settings, scope, payload, next_page
):
    """A verified empty result remains distinct from absent data and automatic pagination."""
    async with sessions() as db:
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


@pytest.mark.parametrize(
    "status,expected", [("APPROVAL_PENDING", "pending"), ("UNRECOGNIZED", "failed")]
)
async def test_subscription_status_projection_is_consistent(
    client, headers, provider, status, expected
):
    """Unknown provider outcomes fail in both initial callbacks and explicit reads."""
    from support import mutations, outcome, submit

    item = (await submit(client, headers, is_subscription=True)).json()
    provider["subscription"]["status"] = status
    assert (await outcome(client, headers, item)).json()["status"] == expected
    response = await client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert response.status_code == 200
    saved = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    assert saved["status"] == expected
    assert not mutations(provider, "/capture")
