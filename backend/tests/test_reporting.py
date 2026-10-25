"""Financial reporting across currencies, unavailable fees and local calendar boundaries."""

from decimal import Decimal

from app.normalize import transaction


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
