"""Refunds behavior with dedicated PostgreSQL and mocked provider HTTP."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.models import FinancialAction, Transaction, now
from app.normalize import transaction
from app.refunds import refund_eligibility
from sqlalchemy import func, select
from support import capture, collection, mutations, outcome, submit


@pytest.mark.parametrize("api_family", ["capture", "sale"])
async def test_partial_refund_remaining_button_and_duplicate_confirmation(
    client, headers, provider, sessions, api_family
):
    identifier = await collection(sessions, api_family=api_family)
    path = f"/api/v1/transactions/{identifier}/refunds"
    before = (await client.get("/api/v1/transactions", headers=headers)).json()["items"][0]
    assert before["can_refund"] and Decimal(before["refundable_amount"]) == 100
    body = {"amount": "30.00", "reason": "Requested correction"}
    result = await client.post(path, headers=headers, json=body)
    assert result.status_code == 200 and result.json()["status"] == "completed"
    assert (await client.post(path, headers=headers, json=body)).json()["id"] == result.json()["id"]
    assert (
        await client.post(path, headers=headers, json={**body, "amount": "31.00"})
    ).status_code == 409
    amount_field = "value" if api_family == "capture" else "total"
    assert len(provider["refunds"]) == 1
    assert provider["refunds"][0]["amount"][amount_field] == "30.00"
    assert mutations(
        provider, f"/{'captures' if api_family == 'capture' else 'sale'}/CAPTURE1/refund"
    )
    detail = (await client.get(f"/api/v1/transactions/{identifier}", headers=headers)).json()
    assert detail["can_refund"] and Decimal(detail["refundable_amount"]) == 70
    assert (
        await client.post(
            path,
            headers={**headers, "Idempotency-Key": "too-large"},
            json={**body, "amount": "71.00"},
        )
    ).status_code == 409
    assert (
        await client.post(
            path,
            headers={**headers, "Idempotency-Key": "remaining"},
            json={**body, "amount": "70.00"},
        )
    ).json()["status"] == "completed"
    assert not (await client.get(f"/api/v1/transactions/{identifier}", headers=headers)).json()[
        "can_refund"
    ]


@pytest.mark.parametrize(
    "mode,status", [("refund_timeout", "failed"), ("refund_pending", "pending")]
)
async def test_uncertain_pending_refunds_reserve_exact_amount(
    client, headers, provider, sessions, payment_logs, mode, status
):
    identifier = await collection(sessions)
    provider["mode"] = mode
    path = f"/api/v1/transactions/{identifier}/refunds"
    result = await client.post(path, headers=headers, json={"amount": "70.00", "reason": "Partial"})
    assert result.json()["status"] == status
    assert result.json()["reservation_active"]
    await client.post(path, headers=headers, json={"amount": "70.00", "reason": "Partial"})
    assert len(provider["refunds"]) == 1
    assert (
        await client.post(
            path,
            headers={**headers, "Idempotency-Key": "overspend"},
            json={"amount": "31.00", "reason": "Partial"},
        )
    ).status_code == 409
    async with sessions() as db:
        assert (await db.scalar(select(func.count()).select_from(FinancialAction))) == 1
        assert (await db.scalar(select(FinancialAction.reservation_active))) is True
    assert payment_logs.text.count("refund_reserved") == 1
    assert payment_logs.text.count("refund_result") == 1
    assert f"status={status} reservation_active=True" in payment_logs.text


@pytest.mark.parametrize("dispatched", [False, True])
async def test_definitive_refund_failure_releases_reservation(
    client, headers, provider, sessions, monkeypatch, dispatched
):
    """Both pre-dispatch auth failure and an explicit rejection safely release money."""
    from app.paypal import PayPalError

    identifier = await collection(sessions)

    async def reject(*args, **kwargs):
        raise PayPalError(422, "REFUND_NOT_ALLOWED")

    monkeypatch.setattr(
        client.app.state.paypal, "request" if dispatched else "access_token", reject
    )
    result = await client.post(
        f"/api/v1/transactions/{identifier}/refunds",
        headers=headers,
        json={"amount": "70.00", "reason": "Partial"},
    )
    assert result.json()["status"] == "failed"
    assert not result.json()["reservation_active"]
    detail = (await client.get(f"/api/v1/transactions/{identifier}", headers=headers)).json()
    assert Decimal(detail["refundable_amount"]) == 100
    async with sessions() as db:
        assert (await db.scalar(select(FinancialAction.reservation_active))) is False


@pytest.mark.parametrize("defect", ["amount", "currency", "timestamp"])
async def test_unverified_refund_response_keeps_reservation_and_cannot_replay(
    client, headers, provider, sessions, monkeypatch, defect
):
    """A successful HTTP response is insufficient when its financial facts cannot be verified."""
    identifier = await collection(sessions)
    response = {
        "id": "UNVERIFIED-REFUND",
        "status": "COMPLETED",
        "amount": {"value": "70.00", "currency_code": "ILS"},
        "create_time": now().isoformat(),
    }
    if defect == "amount":
        response["amount"]["value"] = "69.00"
    elif defect == "currency":
        response["amount"]["currency_code"] = "USD"
    else:
        response["create_time"] = "invalid"
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(client.app.state.paypal, "request", request)
    path = f"/api/v1/transactions/{identifier}/refunds"
    body = {"amount": "70.00", "reason": "Operator request"}
    failed = await client.post(path, headers=headers, json=body)
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed" and failed.json()["reservation_active"]
    assert (await client.post(path, headers=headers, json=body)).json()["id"] == failed.json()["id"]
    excess = await client.post(
        path,
        headers={**headers, "Idempotency-Key": "another-refund"},
        json={**body, "amount": "31.00"},
    )
    assert excess.status_code == 409 and request.await_count == 1
    async with sessions() as db:
        saved = await db.get(Transaction, failed.json()["id"])
        assert saved.provider_id.startswith("reserved-")
        assert (await db.scalar(select(FinancialAction.reservation_active))) is True


@pytest.mark.parametrize(
    "provider_status,remaining", [("COMPLETED", "30.00"), ("FAILED", "100.00")]
)
async def test_explicit_refund_read_resolves_failed_result_reservation(
    sessions, provider_status, remaining
):
    """A provider-confirmed result resolves the hold without repeating the refund."""
    from app.sync import refresh_transaction

    identifier = await collection(sessions)
    async with sessions() as db:
        original = await db.get(Transaction, identifier)
        refund_row = Transaction(
            provider_id="REFUND-RESERVED",
            api_family="capture",
            kind="refund",
            status="failed",
            provider_status="UNCONFIRMED",
            reservation_active=True,
            original_id=identifier,
            amount=Decimal("70.00"),
            currency="ILS",
            effective_at=now(),
        )
        db.add(refund_row)
        await db.flush()
        action = FinancialAction(
            actor="staff",
            scope="refund:" + identifier,
            submission_key="failed-response",
            input_hash="hash",
            resource_id=refund_row.id,
            status="failed",
        )
        db.add(action)
        await db.commit()
        paypal = AsyncMock()
        paypal.request.return_value = {
            "id": refund_row.provider_id,
            "status": provider_status,
            "amount": {"value": "70.00", "currency_code": "ILS"},
            "create_time": now().isoformat(),
        }
        await refresh_transaction(db, refund_row, paypal)
        assert not refund_row.reservation_active and not action.reservation_active
        assert refund_eligibility(original, [refund_row])["refundable_amount"] == remaining
        assert paypal.request.await_args.args[0] == "GET"
        assert paypal.request.await_count == 1


@pytest.mark.parametrize("offset,eligible", [(-1, True), (0, False), (1, False)])
def test_refund_deadline_original_provider_timestamp(monkeypatch, offset, eligible):
    captured = now() - timedelta(days=180)
    row = Transaction(
        provider_id="CAP1",
        api_family="capture",
        kind="collection",
        status="completed",
        provider_status="COMPLETED",
        amount=Decimal("5.00"),
        currency="EUR",
        effective_at=captured,
    )
    monkeypatch.setattr("app.refunds.now", lambda: captured + timedelta(days=180, seconds=offset))
    assert refund_eligibility(row, [])["can_refund"] is eligible


async def test_partial_refund_can_reach_original_amount_but_never_exceed_it(
    sessions, client, headers, provider
):
    item = (await submit(client, headers)).json()
    assert (await outcome(client, headers, item)).json()["status"] == "completed"
    payment = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    path = f"/api/v1/transactions/{payment['collection_id']}/refunds"
    first = await client.post(path, headers=headers, json={"amount": "20.00", "reason": "Partial"})
    assert first.json()["status"] == "completed"
    async with sessions() as db:
        original = await db.get(Transaction, payment["collection_id"])
        data = {
            "id": original.provider_id,
            "status": "PARTIALLY_REFUNDED",
            "amount": {"value": "50.00", "currency_code": "ILS"},
            "create_time": original.effective_at.isoformat(),
        }
        await transaction(db, data, "capture", existing=original)
        await transaction(db, {**data, "status": "COMPLETED"}, "capture", existing=original)
        await db.commit()
    partial = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    assert partial["status"] == "partially_refunded" and partial["can_refund"]
    assert Decimal(partial["amount"]) == 50
    assert Decimal(partial["refunded_amount"]) == 20
    assert Decimal(partial["refundable_amount"]) == 30
    too_much = await client.post(
        path,
        headers={**headers, "Idempotency-Key": "too-much"},
        json={"amount": "30.01", "reason": "Excess"},
    )
    assert too_much.status_code == 409 and len(provider["refunds"]) == 1
    second = await client.post(
        path,
        headers={**headers, "Idempotency-Key": "remaining"},
        json={"amount": "30.00", "reason": "Remaining"},
    )
    assert second.json()["status"] == "completed"
    final = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    assert final["status"] == "refunded" and not final["can_refund"]
    assert Decimal(final["refunded_amount"]) == 50 and Decimal(final["refundable_amount"]) == 0
    assert (
        await client.post(
            path,
            headers={**headers, "Idempotency-Key": "extra"},
            json={"amount": "0.01", "reason": "Excess"},
        )
    ).status_code == 409
    assert sum(Decimal(refund["amount"]["value"]) for refund in provider["refunds"]) == 50


@pytest.mark.parametrize("mode", ["refund_pending", "refund_timeout"])
async def test_partial_refund_reservation_limits_remaining_payment(client, headers, provider, mode):
    item = (await submit(client, headers)).json()
    await outcome(client, headers, item)
    payment = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    path = f"/api/v1/transactions/{payment['collection_id']}/refunds"
    await client.post(path, headers=headers, json={"amount": "20.00", "reason": "Partial"})
    provider["mode"] = mode
    await client.post(
        path,
        headers={**headers, "Idempotency-Key": "reserved"},
        json={"amount": "10.00", "reason": "Next"},
    )
    summary = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    assert Decimal(summary["refundable_amount"]) == 20
    calls = len(provider["requests"])
    assert (
        await client.post(
            path,
            headers={**headers, "Idempotency-Key": "excess"},
            json={"amount": "20.01", "reason": "Excess"},
        )
    ).status_code == 409
    assert len(provider["requests"]) == calls


async def test_unknown_external_partial_refund_is_not_replaced_by_a_pending_reservation(sessions):
    """A reservation is not proof of the amount PayPal already refunded externally."""
    async with sessions() as db:
        original = await transaction(
            db, capture("PARTIALLY_REFUNDED"), "capture", origin="imported"
        )
        reserved = await transaction(
            db,
            {
                **capture("PENDING"),
                "id": "PENDING-REFUND",
                "amount": {"value": "10.00", "currency_code": "ILS"},
            },
            "capture",
            "refund",
            original=original,
        )
        await db.commit()
        eligibility = refund_eligibility(original, [reserved])
        assert not eligibility["can_refund"]
        assert eligibility["refundable_amount"] is None
