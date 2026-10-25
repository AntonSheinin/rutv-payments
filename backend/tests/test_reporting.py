"""Financial reporting across currencies, unavailable fees and local calendar boundaries."""

from datetime import datetime
from decimal import Decimal

import pytest
from app.models import Payment, Subscription
from app.normalize import transaction
from sqlalchemy import select


@pytest.mark.parametrize("family", ["capture", "sale"])
async def test_net_includes_only_verified_refund_fee_credits(client, headers, sessions, family):
    """Refund credits are signed fee facts, not estimated proportional reimbursements."""
    async with sessions() as db:
        original = await transaction(
            db,
            {
                "id": "FEE-CAPTURE",
                "status": "COMPLETED",
                "amount": {"value": "100.00", "currency_code": "ILS"},
                "create_time": "2026-10-21T16:00:00Z",
                "seller_receivable_breakdown": {
                    "paypal_fee": {"value": "3.00", "currency_code": "ILS"}
                },
            },
            family,
        )
        for identifier, credit, amount in [
            ("REFUND-1", "0.00", "20.00"),
            ("REFUND-2", "0.50", "10.00"),
        ]:
            await transaction(
                db,
                {
                    "id": identifier,
                    "status": "COMPLETED",
                    "amount": {"value": amount, "currency_code": "ILS"},
                    "create_time": "2026-10-22T16:00:00Z",
                    **(
                        {
                            "seller_payable_breakdown": {
                                "paypal_fee": {"value": credit, "currency_code": "ILS"}
                            }
                        }
                        if family == "capture"
                        else {"refund_from_transaction_fee": {"value": credit, "currency": "ILS"}}
                    ),
                },
                family,
                "refund",
                original=original,
            )
        await db.commit()
    totals = (await client.get("/api/v1/statistics", headers=headers)).json()["currencies"][0]
    assert totals["refunds"] == "30.00"
    assert totals["known_fees"] == "2.50"
    assert totals["fees_complete"]
    assert totals["net_after_fees"] == "67.50"


async def test_health_filters_and_collection_activity_include_installments(
    client, headers, sessions
):
    """Health filters agreements; trends include only completed one-time/installment facts."""
    async with sessions() as db:
        for index, status in enumerate(
            ["ACTIVE", "SUSPENDED", "APPROVAL_PENDING", "CANCELLED", "EXPIRED", "FAILED"]
        ):
            db.add(
                Subscription(
                    provider_id=f"SUB-{index}",
                    status=status,
                    currency="ILS",
                    amount=Decimal("10.00"),
                    client_name="Matching client",
                    created_at=datetime.fromisoformat("2026-10-21T10:00:00+00:00"),
                )
            )
        await db.flush()
        sub = await db.scalar(select(Subscription).where(Subscription.provider_id == "SUB-0"))
        for identifier, subscription_id, status in [
            ("ONE-TIME", None, "COMPLETED"),
            ("INSTALLMENT", sub.id, "COMPLETED"),
            ("PENDING", sub.id, "PENDING"),
            ("FAILED", sub.id, "FAILED"),
        ]:
            row = await transaction(
                db,
                {
                    "id": identifier,
                    "status": status,
                    "amount": {"value": "10.00", "currency_code": "ILS"},
                    "create_time": "2026-10-21T16:00:00Z",
                },
                "sale" if subscription_id else "capture",
                subscription_id=subscription_id,
            )
            if identifier == "ONE-TIME":
                payment = await db.get(Payment, row.payment_id)
                payment.checkout_status = "failed"
        await db.commit()
    result = (await client.get("/api/v1/statistics", headers=headers)).json()
    assert result["current_subscriptions"] == {"ACTIVE": 1, "SUSPENDED": 1}
    assert result["trends"][0]["amount"] == "20.00"
    for params in [
        {"q": "absent"},
        {"currency": "USD"},
        {"min_amount": "11"},
        {"is_subscription": "false"},
        {"origin": "created_here"},
    ]:
        response = await client.get("/api/v1/statistics", headers=headers, params=params)
        assert response.json()["current_subscriptions"] == {}
    for dates in [{"start_date": "2026-11-01"}, {"end_date": "2026-09-30"}]:
        response = await client.get("/api/v1/statistics", headers=headers, params=dates)
        assert response.json()["current_subscriptions"] == {"ACTIVE": 1, "SUSPENDED": 1}
        assert response.json()["trends"] == []
    result = (await client.get("/api/v1/statistics?is_subscription=true", headers=headers)).json()
    assert result["trends"][0]["amount"] == "10.00"
    result = (await client.get("/api/v1/statistics?checkout_status=failed", headers=headers)).json()
    assert result["trends"][0]["amount"] == "10.00"
    assert result["current_subscriptions"] == {}
    result = (
        await client.get("/api/v1/statistics?checkout_status=success", headers=headers)
    ).json()
    assert result["trends"] == []


async def test_totals_keep_currencies_and_unknown_fees_separate_from_pagination(
    client, headers, sessions
):
    """Totals use all matching movements; unknown fees must not produce a claimed net."""
    async with sessions() as db:
        original = await transaction(
            db,
            {
                "id": "ILS-COLLECTION",
                "status": "COMPLETED",
                "amount": {"value": "50.00", "currency_code": "ILS"},
                "create_time": "2026-10-21T16:00:00Z",
                "seller_receivable_breakdown": {
                    "paypal_fee": {"value": "1.50", "currency_code": "ILS"}
                },
            },
            "capture",
        )
        await transaction(
            db,
            {
                "id": "ILS-REFUND",
                "status": "COMPLETED",
                "amount": {"value": "10.00", "currency_code": "ILS"},
                "create_time": "2026-10-21T17:00:00Z",
            },
            "capture",
            "refund",
            original=original,
        )
        for identifier, status, amount, currency in [
            ("USD-COLLECTION", "COMPLETED", "20.00", "USD"),
            ("EUR-PENDING", "PENDING", "30.00", "EUR"),
            ("EUR-DECLINED", "DECLINED", "40.00", "EUR"),
        ]:
            await transaction(
                db,
                {
                    "id": identifier,
                    "status": status,
                    "amount": {"value": amount, "currency_code": currency},
                    "create_time": "2026-10-22T16:00:00Z",
                    "seller_receivable_breakdown": {
                        "paypal_fee": {"value": "0.00", "currency_code": currency}
                    },
                },
                "capture",
            )
        await db.commit()
    response = await client.get("/api/v1/statistics?page_size=1", headers=headers)
    assert response.status_code == 200
    totals = {row["currency"]: row for row in response.json()["currencies"]}
    ils, usd, eur = totals["ILS"], totals["USD"], totals["EUR"]
    assert (ils["collected"], ils["refunds"], ils["net_before_fees"]) == ("50.00", "10.00", "40.00")
    assert ils["known_fees"] == "1.50" and not ils["fees_complete"]
    assert ils["net_after_fees"] is None
    assert usd["fees_complete"] and usd["net_after_fees"] == "20.00"
    assert eur["pending_count"] == eur["failed_count"] == 1
    assert Decimal(eur["collected"]) == 0
    day = await client.get(
        "/api/v1/statistics",
        headers=headers,
        params={"start_date": "2026-10-22", "end_date": "2026-10-22", "timezone": "Asia/Tokyo"},
    )
    assert day.status_code == 200
    assert [row["currency"] for row in day.json()["currencies"]] == ["ILS"]
    assert all(row["date"].startswith("2026-10-22") for row in day.json()["trends"])
