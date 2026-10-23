"""Regressions for review findings and cumulative partial-refund limits."""

from decimal import Decimal

import pytest
from app.models import Attempt, Payment, Transaction, now
from app.normalize import subscription, transaction
from app.services import refund_eligibility
from sqlalchemy import func, select
from test_payments import outcome, submit


def capture(status: str = "COMPLETED", order: str | None = None) -> dict:
    """Build one provider capture, optionally correlated to its checkout order."""
    result = {
        "id": "REVIEW-CAPTURE",
        "status": status,
        "amount": {"value": "50.00", "currency_code": "ILS"},
        "create_time": now().isoformat(),
    }
    if order:
        result["supplementary_data"] = {"related_ids": {"order_id": order}}
    return result


def test_used_identity_cannot_be_reused_even_if_summary_is_wrong(
    sessions, client, headers, provider
):
    item = submit(client, headers).json()
    with sessions() as db:
        db.get(Payment, item["id"]).status = "failed"
        db.commit()
    calls = len(provider["requests"])
    response = submit(client, {**headers, "Idempotency-Key": "explicit-second-submit"})
    assert response.status_code == 409
    assert len(provider["requests"]) == calls
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attempt)) == 1


def test_import_enrichment_moves_capture_and_refund_to_checkout(
    sessions, client, headers, provider
):
    item = submit(client, headers).json()
    with sessions() as db:
        original = transaction(db, capture(), "capture", origin="imported")
        original_id, imported_id = original.id, original.payment_id
        refund = transaction(
            db,
            {
                **capture(),
                "id": "EARLY-REFUND",
                "amount": {"value": "20.00", "currency_code": "ILS"},
            },
            "capture",
            "refund",
            original=original,
            origin="imported",
        )
        refund_id = refund.id
        db.commit()
        transaction(db, capture(order=item["provider_id"]), "capture", origin="imported")
        db.commit()
        assert db.get(Payment, imported_id) is None
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.get(Transaction, original_id).payment_id == item["id"]
        assert db.get(Transaction, refund_id).payment_id == item["id"]
        assert db.get(Transaction, refund_id).original_id == original_id
        assert db.get(Payment, item["id"]).status == "partially_refunded"
    calls = len(provider["requests"])
    assert outcome(client, headers, item).json()["status"] == "completed"
    assert len(provider["requests"]) == calls


def test_declined_capture_updates_pending_payment_and_allows_explicit_restart(
    sessions, client, headers, provider
):
    item = submit(client, headers).json()
    with sessions() as db:
        transaction(db, capture("PENDING", item["provider_id"]), "capture", origin="imported")
        db.commit()
        assert db.get(Payment, item["id"]).status == "pending"
        transaction(db, capture("DECLINED", item["provider_id"]), "capture", origin="imported")
        db.commit()
        assert db.get(Payment, item["id"]).status == "failed"
        assert db.get(Attempt, item["attempt_id"]).status == "failed"
    response = submit(
        client, {**headers, "Idempotency-Key": "explicit-restart"}, intent_key="fresh-after-decline"
    )
    assert response.status_code == 200
    assert response.json()["id"] != item["id"]
    assert response.json()["attempt_id"] != item["attempt_id"]


@pytest.mark.parametrize("stale", ["COMPLETED", "PENDING", "DECLINED", "PARTIALLY_REFUNDED"])
def test_refunded_evidence_survives_stale_capture_reads(sessions, stale):
    with sessions() as db:
        row = transaction(db, capture("REFUNDED"), "capture", origin="imported")
        db.commit()
        transaction(db, capture(stale), "capture", origin="imported")
        db.commit()
        assert row.provider_status == "REFUNDED"
        assert db.get(Payment, row.payment_id).status == "refunded"
        eligibility = refund_eligibility(row, [])
        assert not eligibility["can_refund"]
        assert Decimal(eligibility["refundable_amount"]) == 0
        assert Decimal(eligibility["refunded_amount"]) == 50


def test_partial_refund_can_reach_original_amount_but_never_exceed_it(
    sessions, client, headers, provider
):
    item = submit(client, headers).json()
    assert outcome(client, headers, item).json()["status"] == "completed"
    payment = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    path = f"/api/v1/transactions/{payment['collection_id']}/refunds"
    first = client.post(path, headers=headers, json={"amount": "20.00", "reason": "Partial"})
    assert first.json()["status"] == "completed"
    with sessions() as db:
        original = db.get(Transaction, payment["collection_id"])
        data = {
            "id": original.provider_id,
            "status": "PARTIALLY_REFUNDED",
            "amount": {"value": "50.00", "currency_code": "ILS"},
            "create_time": original.effective_at.isoformat(),
        }
        transaction(db, data, "capture", existing=original)
        transaction(db, {**data, "status": "COMPLETED"}, "capture", existing=original)
        db.commit()
    partial = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    assert partial["status"] == "partially_refunded" and partial["can_refund"]
    assert Decimal(partial["amount"]) == 50
    assert Decimal(partial["refunded_amount"]) == 20
    assert Decimal(partial["refundable_amount"]) == 30
    too_much = client.post(
        path,
        headers={**headers, "Idempotency-Key": "too-much"},
        json={"amount": "30.01", "reason": "Excess"},
    )
    assert too_much.status_code == 409 and len(provider["refunds"]) == 1
    second = client.post(
        path,
        headers={**headers, "Idempotency-Key": "remaining"},
        json={"amount": "30.00", "reason": "Remaining"},
    )
    assert second.json()["status"] == "completed"
    final = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    assert final["status"] == "refunded" and not final["can_refund"]
    assert Decimal(final["refunded_amount"]) == 50 and Decimal(final["refundable_amount"]) == 0
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": "extra"},
            json={"amount": "0.01", "reason": "Excess"},
        ).status_code
        == 409
    )
    assert sum(Decimal(refund["amount"]["value"]) for refund in provider["refunds"]) == 50


@pytest.mark.parametrize("mode", ["refund_pending", "refund_timeout"])
def test_partial_refund_reservation_limits_remaining_payment(client, headers, provider, mode):
    item = submit(client, headers).json()
    outcome(client, headers, item)
    payment = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    path = f"/api/v1/transactions/{payment['collection_id']}/refunds"
    client.post(path, headers=headers, json={"amount": "20.00", "reason": "Partial"})
    provider["mode"] = mode
    client.post(
        path,
        headers={**headers, "Idempotency-Key": "reserved"},
        json={"amount": "10.00", "reason": "Next"},
    )
    summary = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    assert Decimal(summary["refundable_amount"]) == 20
    calls = len(provider["requests"])
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": "excess"},
            json={"amount": "20.01", "reason": "Excess"},
        ).status_code
        == 409
    )
    assert len(provider["requests"]) == calls


def test_subscription_cancel_updates_payment_and_preserves_installment(
    sessions, client, headers, provider
):
    item = submit(client, headers, is_subscription=True).json()
    outcome(client, headers, item)
    sub = client.get("/api/v1/subscriptions", headers=headers).json()["items"][0]
    with sessions() as db:
        installment = transaction(db, capture(), "sale", subscription_id=sub["id"])
        installment_payment_id = installment.payment_id
        db.commit()
    result = client.post(
        f"/api/v1/subscriptions/{sub['id']}/cancel",
        headers=headers,
        json={"reason": "Operator cancellation"},
    )
    assert result.status_code == 200
    assert (
        client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()["status"]
        == "cancelled"
    )
    assert (
        client.get(f"/api/v1/payments/{installment_payment_id}", headers=headers).json()["status"]
        == "completed"
    )
    with sessions() as db:
        data = {**provider["subscription"], "status": "ACTIVE", "plan": provider["plan"]}
        subscription(db, data, payment=db.get(Payment, item["id"]))
        db.commit()
        assert db.get(Payment, item["id"]).status == "cancelled"
        assert db.get(Attempt, item["attempt_id"]).status == "cancelled"


def test_unknown_external_partial_refund_is_not_replaced_by_a_pending_reservation(sessions):
    """A reservation is not proof of the amount PayPal already refunded externally."""
    with sessions() as db:
        original = transaction(db, capture("PARTIALLY_REFUNDED"), "capture", origin="imported")
        reserved = transaction(
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
        db.commit()
        eligibility = refund_eligibility(original, [reserved])
        assert not eligibility["can_refund"]
        assert eligibility["refundable_amount"] is None
