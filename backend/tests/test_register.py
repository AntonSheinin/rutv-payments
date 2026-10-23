"""One payment identity across unpaid checkout, collection, import and refund."""

from decimal import Decimal

import pytest
from app.models import Payment, Subscription, Transaction, now
from app.normalize import transaction
from sqlalchemy import func, select


def test_one_payment_row_survives_collection_and_partial_full_refunds(client, headers, provider):
    """Refund ledger movements update the original payment rather than adding list rows."""
    body = {
        "first_name": "Test",
        "last_name": "Client",
        "agreement_number": "001",
        "price_per_period": "50.00",
        "intent_key": "register",
    }
    item = client.post("/api/v1/payments", headers=headers, json=body).json()
    result = client.get("/api/v1/payments", headers=headers).json()
    assert result["total"] == 1 and result["items"][0]["status"] == "awaiting_approval"
    assert result["items"][0]["collection_id"] is None
    assert client.get("/api/v1/payment-requests", headers=headers).status_code == 404
    outcome = client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "attempt_id": item["attempt_id"],
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert outcome.json()["status"] == "completed"
    payment = client.get("/api/v1/payments", headers=headers).json()["items"][0]
    assert payment["id"] == item["id"] and payment["can_refund"]
    for index, amount, status in [(1, "20.00", "partially_refunded"), (2, "30.00", "refunded")]:
        response = client.post(
            f"/api/v1/transactions/{payment['collection_id']}/refunds",
            headers={**headers, "Idempotency-Key": f"refund-register-{index}"},
            json={"amount": amount, "reason": "Test refund"},
        )
        assert response.status_code == 200
        result = client.get("/api/v1/payments", headers=headers).json()
        assert result["total"] == 1
        assert result["items"][0]["status"] == status
        assert result["items"][0]["amount"] == "50.00"
        assert client.get("/api/v1/payments?kind=refund", headers=headers).json()["total"] == 1
    assert not result["items"][0]["can_refund"]
    assert client.get("/api/v1/payments?kind=collection", headers=headers).json()["total"] == 0
    assert (
        len(
            client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()["transactions"][
                "items"
            ]
        )
        == 3
    )


def test_cancelled_payment_is_in_register_without_a_collection(client, headers, provider):
    """Cancellation stays visible and no financial row is invented to display it."""
    item = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "first_name": "Test",
            "last_name": "Client",
            "agreement_number": "001",
            "price_per_period": "50.00",
            "intent_key": "cancel-register",
        },
    ).json()
    client.post(
        f"/api/v1/payments/{item['id']}/attempts/{item['attempt_id']}/cancel", headers=headers
    )
    result = client.get("/api/v1/payments?status=cancelled", headers=headers).json()
    assert result["total"] == 1 and result["items"][0]["id"] == item["id"]
    assert result["items"][0]["collection_id"] is None
    assert client.get("/api/v1/transactions", headers=headers).json()["total"] == 0


def test_imported_collection_has_one_payment_and_its_refund_is_linked(sessions, client, headers):
    """Repeated imports merge by exact provider identity without duplicate payments."""
    with sessions() as db:
        data = {
            "id": "IMPORTED-CAPTURE",
            "status": "PENDING",
            "amount": {"value": "10.00", "currency_code": "USD"},
            "create_time": now().isoformat(),
        }
        collection = transaction(db, data, "capture", origin="imported")
        payment_id = collection.payment_id
        db.commit()
        assert db.get(Payment, payment_id).status == "pending"
        data["status"] = "COMPLETED"
        collection = transaction(db, data, "capture", origin="imported")
        transaction(
            db,
            {**data, "id": "IMPORTED-REFUND", "amount": {"value": "3.00", "currency_code": "USD"}},
            "capture",
            "refund",
            original=collection,
            origin="imported",
        )
        db.commit()
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.get(Payment, payment_id).status == "partially_refunded"
        assert set(db.scalars(select(Transaction.payment_id))) == {payment_id}
    listing = client.get("/api/v1/payments?kind=refund", headers=headers).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == payment_id
    assert listing["items"][0]["refunded_amount"] == "3.00"


def test_exact_capture_identity_merges_import_into_existing_checkout(
    sessions, client, headers, provider
):
    """An early import cannot leave a duplicate payment after its callback is verified."""
    item = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "first_name": "Test",
            "last_name": "Client",
            "agreement_number": "001",
            "price_per_period": "50.00",
            "intent_key": "import-before-callback",
        },
    ).json()
    with sessions() as db:
        transaction(
            db,
            {
                "id": "CAP" + item["provider_id"],
                "status": "COMPLETED",
                "amount": {"value": "50.00", "currency_code": "ILS"},
                "create_time": now().isoformat(),
            },
            "capture",
            origin="imported",
        )
        db.commit()
        assert db.scalar(select(func.count()).select_from(Payment)) == 2
    response = client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "attempt_id": item["attempt_id"],
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert response.status_code == 200
    result = client.get("/api/v1/payments", headers=headers).json()
    assert result["total"] == 1 and result["items"][0]["id"] == item["id"]


def test_import_correlated_by_order_closes_attempt_without_another_capture(
    sessions, client, headers, provider
):
    """Verified imported collection closes its checkout; a later callback is read-only."""
    item = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "first_name": "Test",
            "last_name": "Client",
            "agreement_number": "001",
            "price_per_period": "50.00",
            "intent_key": "order-correlated-import",
        },
    ).json()
    with sessions() as db:
        transaction(
            db,
            {
                "id": "CAP" + item["provider_id"],
                "status": "COMPLETED",
                "amount": {"value": "50.00", "currency_code": "ILS"},
                "create_time": now().isoformat(),
                "supplementary_data": {"related_ids": {"order_id": item["provider_id"]}},
            },
            "capture",
            origin="imported",
        )
        db.commit()
    count = len(provider["requests"])
    response = client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "attempt_id": item["attempt_id"],
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert response.json()["status"] == "completed"
    assert len(provider["requests"]) == count
    assert client.get("/api/v1/payments", headers=headers).json()["total"] == 1


def test_each_subscription_installment_has_its_own_payment(sessions, client, headers, provider):
    """Agreement activation and separate collected installments cannot share a refund identity."""
    item = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "first_name": "Test",
            "last_name": "Client",
            "agreement_number": "001",
            "price_per_period": "50.00",
            "intent_key": "subscription-register",
            "is_subscription": True,
        },
    ).json()
    response = client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "attempt_id": item["attempt_id"],
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert response.json()["status"] == "active"
    with sessions() as db:
        subscription = db.scalar(select(Subscription))
        for index in range(2):
            transaction(
                db,
                {
                    "id": f"SALE-{index}",
                    "status": "COMPLETED",
                    "amount": {"value": "50.00", "currency_code": "ILS"},
                    "create_time": now().isoformat(),
                },
                "sale",
                subscription_id=subscription.id,
            )
        db.commit()
        assert db.scalar(select(func.count()).select_from(Payment)) == 3
        assert len(set(db.scalars(select(Transaction.payment_id)))) == 2
    results = client.get("/api/v1/payments", headers=headers).json()
    assert results["total"] == 3
    assert sorted(row["status"] for row in results["items"]) == ["active", "completed", "completed"]


def test_explicit_new_payment_does_not_reuse_failed_payment(sessions, client, headers, provider):
    """A failed ledger observation cannot block the next explicitly authorized attempt."""
    body = {
        "first_name": "Test",
        "last_name": "Client",
        "agreement_number": "001",
        "price_per_period": "50.00",
        "intent_key": "restart-after-decline",
    }
    item = client.post("/api/v1/payments", headers=headers, json=body).json()
    provider["mode"] = "capture_decline"

    def approve(item):
        return client.post(
            f"/api/v1/payments/{item['id']}/checkout-outcome",
            headers=headers,
            json={
                "attempt_id": item["attempt_id"],
                "proof": item["proof"],
                "outcome": "approved",
                "provider_id": item["provider_id"],
            },
        )

    assert approve(item).json()["status"] == "failed"
    with sessions() as db:
        transaction(
            db,
            {
                "id": "FAILED-CAPTURE",
                "status": "DECLINED",
                "amount": {"value": "50.00", "currency_code": "ILS"},
                "create_time": now().isoformat(),
            },
            "capture",
            payment_id=item["id"],
        )
        db.commit()
    provider["mode"] = "ok"
    second = client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": "new-explicit-attempt"},
        json={**body, "intent_key": "fresh-after-decline"},
    ).json()
    assert second["id"] != item["id"]
    assert approve(second).json()["status"] == "completed"
    listing = client.get("/api/v1/payments", headers=headers).json()
    assert listing["total"] == 2
    collected = next(row for row in listing["items"] if row["id"] == second["id"])
    assert collected["status"] == "completed"
    assert collected["provider_id"] == "CAP" + second["provider_id"]
    assert collected["can_refund"]


@pytest.mark.parametrize(
    "status,total,executions,remaining,unlimited",
    [
        ("ACTIVE", 12, [{"tenure_type": "REGULAR", "cycles_remaining": 9}], 9, False),
        ("ACTIVE", 0, [{"tenure_type": "REGULAR", "cycles_remaining": 0}], None, True),
        ("ACTIVE", 12, [], None, False),
        ("ACTIVE", 12, [{"tenure_type": "TRIAL", "cycles_remaining": 1}], None, False),
        ("ACTIVE", 12, [{"tenure_type": "REGULAR", "cycles_remaining": -1}], None, False),
        ("CANCELLED", 12, [{"tenure_type": "REGULAR", "cycles_remaining": 9}], 0, False),
        ("EXPIRED", 0, [], 0, False),
        ("SUSPENDED", 12, [{"tenure_type": "REGULAR", "cycles_remaining": 9}], 9, False),
    ],
)
def test_subscription_list_and_details_show_recorded_charges_and_remaining(
    sessions, client, headers, provider, status, total, executions, remaining, unlimited
):
    """Separate successful ledger charges from provider cycle counters and trial cycles."""
    with sessions() as db:
        item = Subscription(
            provider_id="I-PROGRESS",
            status=status,
            amount=Decimal("10.00"),
            currency="ILS",
            metadata_json={"total_cycles": total, "cycle_executions": executions},
        )
        db.add(item)
        db.flush()
        identifier = item.id
        for index, (kind, raw) in enumerate(
            [
                ("collection", "COMPLETED"),
                ("collection", "REFUNDED"),
                ("collection", "PENDING"),
                ("collection", "DENIED"),
                ("refund", "COMPLETED"),
            ]
        ):
            transaction(
                db,
                {
                    "id": f"PROGRESS-{index}",
                    "status": raw,
                    "amount": {"value": "10.00", "currency_code": "ILS"},
                    "create_time": "2026-10-19T08:00:00Z",
                },
                "sale",
                kind,
                subscription_id=identifier,
            )
        db.commit()
    rows = client.get("/api/v1/subscriptions", headers=headers).json()["items"]
    detail = client.get(f"/api/v1/subscriptions/{identifier}", headers=headers).json()
    for row in [rows[0], detail]:
        assert row["charged_payments"] == 2
        assert row["remaining_payments"] == remaining
        assert row["payments_unlimited"] is unlimited
    assert not provider["requests"]
