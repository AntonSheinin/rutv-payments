"""One payment identity across unpaid checkout, collection, import and refund."""

from decimal import Decimal

import pytest
from app.models import Payment, Subscription, Transaction, now
from app.normalize import transaction
from sqlalchemy import func, select


async def test_one_payment_row_survives_collection_and_partial_full_refunds(
    client, headers, provider
):
    """Refund ledger movements update the original payment rather than adding list rows."""
    body = {
        "first_name": "Test",
        "last_name": "Client",
        "agreement_number": "001",
        "price_per_period": "50.00",
        "intent_key": "register",
    }
    item = (await client.post("/api/v1/payments", headers=headers, json=body)).json()
    result = (await client.get("/api/v1/payments", headers=headers)).json()
    assert result["total"] == 1 and result["items"][0]["status"] == "pending"
    assert result["items"][0]["collection_id"] is None
    outcome = await client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert outcome.json()["status"] == "completed"
    payment = (await client.get("/api/v1/payments", headers=headers)).json()["items"][0]
    assert payment["id"] == item["id"] and payment["can_refund"]
    for index, amount, status in [(1, "20.00", "partially_refunded"), (2, "30.00", "refunded")]:
        response = await client.post(
            f"/api/v1/transactions/{payment['collection_id']}/refunds",
            headers={**headers, "Idempotency-Key": f"refund-register-{index}"},
            json={"amount": amount, "reason": "Test refund"},
        )
        assert response.status_code == 200
        result = (await client.get("/api/v1/payments", headers=headers)).json()
        assert result["total"] == 1
        assert result["items"][0]["status"] == status
        assert result["items"][0]["amount"] == "50.00"
        assert (await client.get("/api/v1/payments?kind=refund", headers=headers)).json()[
            "total"
        ] == 1
    assert not result["items"][0]["can_refund"]
    assert (await client.get("/api/v1/payments?kind=collection", headers=headers)).json()[
        "total"
    ] == 0
    assert (
        len(
            (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()[
                "transactions"
            ]["items"]
        )
        == 3
    )


async def test_cancelled_payment_is_in_register_without_a_collection(client, headers, provider):
    """Cancellation stays visible and no financial row is invented to display it."""
    item = (
        await client.post(
            "/api/v1/payments",
            headers=headers,
            json={
                "first_name": "Test",
                "last_name": "Client",
                "agreement_number": "001",
                "price_per_period": "50.00",
                "intent_key": "cancel-register",
            },
        )
    ).json()
    await client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={"proof": item["proof"], "outcome": "cancelled"},
    )
    result = (await client.get("/api/v1/payments?status=cancelled", headers=headers)).json()
    assert result["total"] == 1 and result["items"][0]["id"] == item["id"]
    assert result["items"][0]["collection_id"] is None
    assert (await client.get("/api/v1/transactions", headers=headers)).json()["total"] == 0


async def test_imported_collection_has_one_payment_and_its_refund_is_linked(
    sessions, client, headers
):
    """Repeated imports merge by exact provider identity without duplicate payments."""
    async with sessions() as db:
        data = {
            "id": "IMPORTED-CAPTURE",
            "status": "PENDING",
            "amount": {"value": "10.00", "currency_code": "USD"},
            "create_time": now().isoformat(),
        }
        collection = await transaction(db, data, "capture", origin="imported")
        payment_id = collection.payment_id
        await db.commit()
        assert (await db.get(Payment, payment_id)).status == "pending"
        data["status"] = "COMPLETED"
        collection = await transaction(db, data, "capture", origin="imported")
        await transaction(
            db,
            {
                **data,
                "id": "IMPORTED-REFUND",
                "amount": {"value": "3.00", "currency_code": "USD"},
            },
            "capture",
            "refund",
            original=collection,
            origin="imported",
        )
        await db.commit()
        assert (await db.scalar(select(func.count()).select_from(Payment))) == 1
        assert (await db.get(Payment, payment_id)).status == "partially_refunded"
        assert set(await db.scalars(select(Transaction.payment_id))) == {payment_id}
    listing = (await client.get("/api/v1/payments?kind=refund", headers=headers)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == payment_id
    assert listing["items"][0]["refunded_amount"] == "3.00"


async def test_exact_capture_identity_merges_import_into_existing_checkout(
    sessions, client, headers, provider
):
    """An early import cannot leave a duplicate payment after its callback is verified."""
    item = (
        await client.post(
            "/api/v1/payments",
            headers=headers,
            json={
                "first_name": "Test",
                "last_name": "Client",
                "agreement_number": "001",
                "price_per_period": "50.00",
                "intent_key": "import-before-callback",
            },
        )
    ).json()
    async with sessions() as db:
        await transaction(
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
        await db.commit()
        assert (await db.scalar(select(func.count()).select_from(Payment))) == 2
    response = await client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert response.status_code == 200
    result = (await client.get("/api/v1/payments", headers=headers)).json()
    assert result["total"] == 1 and result["items"][0]["id"] == item["id"]


async def test_import_correlated_by_order_closes_attempt_without_another_capture(
    sessions, client, headers, provider
):
    """Verified imported collection closes its checkout; a later callback is read-only."""
    item = (
        await client.post(
            "/api/v1/payments",
            headers=headers,
            json={
                "first_name": "Test",
                "last_name": "Client",
                "agreement_number": "001",
                "price_per_period": "50.00",
                "intent_key": "order-correlated-import",
            },
        )
    ).json()
    async with sessions() as db:
        await transaction(
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
        await db.commit()
    count = len(provider["requests"])
    response = await client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert response.json()["status"] == "completed"
    assert len(provider["requests"]) == count
    assert (await client.get("/api/v1/payments", headers=headers)).json()["total"] == 1


async def test_each_subscription_installment_has_its_own_payment(
    sessions, client, headers, provider
):
    """Agreement activation and separate collected installments cannot share a refund identity."""
    item = (
        await client.post(
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
        )
    ).json()
    response = await client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "proof": item["proof"],
            "outcome": "approved",
            "provider_id": item["provider_id"],
        },
    )
    assert response.json()["status"] == "active"
    async with sessions() as db:
        subscription = await db.scalar(select(Subscription))
        for index in range(2):
            await transaction(
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
        await db.commit()
        assert (await db.scalar(select(func.count()).select_from(Payment))) == 3
        assert len(set(await db.scalars(select(Transaction.payment_id)))) == 2
    results = (await client.get("/api/v1/payments", headers=headers)).json()
    assert results["total"] == 3
    assert sorted(row["status"] for row in results["items"]) == ["active", "completed", "completed"]


async def test_explicit_new_payment_does_not_reuse_failed_payment(
    sessions, client, headers, provider
):
    """A failed ledger observation cannot block the next explicitly authorized attempt."""
    body = {
        "first_name": "Test",
        "last_name": "Client",
        "agreement_number": "001",
        "price_per_period": "50.00",
        "intent_key": "restart-after-decline",
    }
    item = (await client.post("/api/v1/payments", headers=headers, json=body)).json()
    provider["mode"] = "capture_decline"

    async def approve(item):
        return await client.post(
            f"/api/v1/payments/{item['id']}/checkout-outcome",
            headers=headers,
            json={
                "proof": item["proof"],
                "outcome": "approved",
                "provider_id": item["provider_id"],
            },
        )

    assert (await approve(item)).json()["status"] == "failed"
    async with sessions() as db:
        await transaction(
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
        await db.commit()
    provider["mode"] = "ok"
    second = (
        await client.post(
            "/api/v1/payments",
            headers={**headers, "Idempotency-Key": "new-explicit-attempt"},
            json={**body, "intent_key": "fresh-after-decline"},
        )
    ).json()
    assert second["id"] != item["id"]
    assert (await approve(second)).json()["status"] == "completed"
    listing = (await client.get("/api/v1/payments", headers=headers)).json()
    assert listing["total"] == 2
    collected = next(row for row in listing["items"] if row["id"] == second["id"])
    assert collected["status"] == "completed"
    assert collected["provider_id"] == "CAP" + second["provider_id"]
    assert collected["can_refund"]


async def test_total_charged_uses_agreement_currency_without_combining_currencies(
    sessions, client, headers
):
    """Successful sums stay separate; no recorded charges are zero, not a guessed cycle value."""
    async with sessions() as db:
        empty = Subscription(provider_id="I-EMPTY", status="ACTIVE", currency="ILS")
        mixed = Subscription(provider_id="I-MIXED", status="SUSPENDED", currency="ILS")
        usd = Subscription(provider_id="I-USD", status="ACTIVE", currency="USD")
        db.add_all([empty, mixed, usd])
        await db.flush()
        for index, (sub, currency, amount) in enumerate(
            [(mixed, "ILS", "10.00"), (mixed, "USD", "5.00"), (usd, "USD", "2.75")]
        ):
            await transaction(
                db,
                {
                    "id": f"SUM-{index}",
                    "status": "COMPLETED",
                    "amount": {"value": amount, "currency_code": currency},
                    "create_time": "2026-10-19T08:00:00Z",
                },
                "sale",
                subscription_id=sub.id,
            )
        await db.commit()
    rows = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"]
    totals = {row["provider_id"]: row["total_charged"] for row in rows}
    assert totals == {"I-EMPTY": "0.00", "I-MIXED": None, "I-USD": "2.75"}


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
async def test_subscription_list_and_details_show_recorded_charges_and_remaining(
    sessions, client, headers, provider, status, total, executions, remaining, unlimited
):
    """Separate successful ledger charges from provider cycle counters and trial cycles."""
    async with sessions() as db:
        item = Subscription(
            provider_id="I-PROGRESS",
            status=status,
            amount=Decimal("10.00"),
            currency="ILS",
            metadata_json={"total_cycles": total, "cycle_executions": executions},
        )
        db.add(item)
        await db.flush()
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
            await transaction(
                db,
                {
                    "id": f"PROGRESS-{index}",
                    "status": raw,
                    "amount": {
                        "value": "7.25" if index == 0 else "12.50" if index == 1 else "10.00",
                        "currency_code": "ILS",
                    },
                    "create_time": "2026-10-19T08:00:00Z",
                },
                "sale",
                kind,
                subscription_id=identifier,
            )
        await db.commit()
    rows = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"]
    detail = (await client.get(f"/api/v1/subscriptions/{identifier}", headers=headers)).json()
    for row in [rows[0], detail]:
        assert row["charged_payments"] == 2
        assert row["total_charged"] == "19.75"
        assert row["remaining_payments"] == remaining
        assert row["payments_unlimited"] is unlimited
    assert not provider["requests"]
