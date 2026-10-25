"""Every financial and checkout-history row remains reachable through bounded pages."""

from decimal import Decimal

from app.models import Payment, Subscription, Transaction, now


async def test_large_detail_histories_are_bounded_complete_and_stably_sorted(
    sessions, client, headers
):
    """More than 100 rows with tied timestamps must paginate without gaps or duplicates."""
    instant = now()
    async with sessions() as db:
        payment = Payment(
            status="partially_refunded", price_per_period=Decimal("100.00"), currency="ILS"
        )
        sub = Subscription(
            provider_id="I-PAGINATION", status="ACTIVE", amount=Decimal("10.00"), currency="ILS"
        )
        db.add_all([payment, sub])
        await db.flush()
        original = Transaction(
            provider_id="PAGINATION-CAPTURE",
            api_family="capture",
            kind="collection",
            status="completed",
            provider_status="COMPLETED",
            amount=Decimal("100.00"),
            currency="ILS",
            payment_id=payment.id,
            effective_at=instant,
        )
        db.add(original)
        await db.flush()
        payment_rows = [original]
        sub_rows = []
        for index in range(101):
            refund = Transaction(
                provider_id=f"PAGINATION-REFUND-{index}",
                api_family="capture",
                kind="refund",
                status="completed",
                provider_status="COMPLETED",
                amount=Decimal("0.01"),
                currency="ILS",
                payment_id=payment.id,
                original_id=original.id,
                effective_at=instant,
            )
            installment = Transaction(
                provider_id=f"PAGINATION-SALE-{index}",
                api_family="sale",
                kind="collection",
                status="completed",
                provider_status="COMPLETED",
                amount=Decimal("10.00"),
                currency="ILS",
                subscription_id=sub.id,
                effective_at=instant,
            )
            db.add_all([refund, installment])
            payment_rows.append(refund)
            sub_rows.append(installment)
        await db.commit()
        payment_id, sub_id = payment.id, sub.id
        expected_payment = sorted(row.id for row in payment_rows)
        expected_sub = sorted(row.id for row in sub_rows)
    for resource, identifier, expected in [
        ("payments", payment_id, expected_payment),
        ("subscriptions", sub_id, expected_sub),
    ]:
        seen = []
        for page in range(1, 7):
            response = await client.get(
                f"/api/v1/{resource}/{identifier}",
                params={"history_page": page, "history_page_size": 20},
                headers=headers,
            )
            assert response.status_code == 200
            data = response.json()["transactions"]
            assert data["total"] == len(expected) and len(data["items"]) <= 20
            assert (data["page"], data["page_size"]) == (page, 20)
            seen.extend(row["id"] for row in data["items"])
        assert seen == expected
    empty = (
        await client.get(
            f"/api/v1/payments/{payment_id}",
            params={"history_page": 100},
            headers=headers,
        )
    ).json()
    assert empty["transactions"]["items"] == []
    assert empty["transactions"]["total"] == 102
    assert Decimal(empty["refundable_amount"]) == Decimal("98.99")
