"""Shared HTTP scenario inputs and dedicated-test-database collection setup."""

from datetime import timedelta
from decimal import Decimal

from app.models import Transaction, now

BODY = {
    "first_name": "Test",
    "last_name": "client",
    "agreement_number": "000123-A",
    "price_per_period": "50.00",
    "currency": "ILS",
    "intent_key": "intent-1",
}


async def submit(client, headers, **changes):
    return await client.post("/api/v1/payments", headers=headers, json={**BODY, **changes})


async def outcome(client, headers, item, value="approved", **changes):
    return await client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "proof": item["proof"],
            "provider_id": item["provider_id"],
            "outcome": value,
            **changes,
        },
    )


def mutations(provider, ending):
    return [r for r in provider["requests"] if r.method == "POST" and r.url.path.endswith(ending)]


async def collection(sessions, *, api_family="capture", **changes):
    async with sessions() as db:
        row = Transaction(
            provider_id="CAPTURE1",
            api_family=api_family,
            kind="collection",
            status="completed",
            provider_status="COMPLETED",
            amount=Decimal("100.00"),
            currency="ILS",
            effective_at=now() - timedelta(days=1),
            client_name="Client",
            agreement_number="0001",
            **changes,
        )
        db.add(row)
        await db.commit()
        return row.id


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
