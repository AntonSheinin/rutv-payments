"""Imports behavior with dedicated PostgreSQL and mocked provider HTTP."""

from decimal import Decimal

import pytest
from app.models import Payment, Transaction
from app.normalize import transaction
from app.refunds import refund_eligibility
from sqlalchemy import func, select
from support import capture, outcome, submit


async def test_import_enrichment_moves_capture_and_refund_to_checkout(
    sessions, client, headers, provider
):
    item = (await submit(client, headers)).json()
    async with sessions() as db:
        original = await transaction(db, capture(), "capture", origin="imported")
        original_id, imported_id = original.id, original.payment_id
        refund = await transaction(
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
        await db.commit()
        await transaction(db, capture(order=item["provider_id"]), "capture", origin="imported")
        await db.commit()
        assert (await db.get(Payment, imported_id)) is None
        assert (await db.scalar(select(func.count()).select_from(Payment))) == 1
        assert (await db.get(Transaction, original_id)).payment_id == item["id"]
        assert (await db.get(Transaction, refund_id)).payment_id == item["id"]
        assert (await db.get(Transaction, refund_id)).original_id == original_id
        assert (await db.get(Payment, item["id"])).status == "partially_refunded"
    calls = len(provider["requests"])
    assert (await outcome(client, headers, item)).json()["status"] == "partially_refunded"
    assert len(provider["requests"]) == calls


async def test_declined_capture_updates_pending_payment_and_allows_explicit_restart(
    sessions, client, headers, provider
):
    item = (await submit(client, headers)).json()
    async with sessions() as db:
        await transaction(db, capture("PENDING", item["provider_id"]), "capture", origin="imported")
        await db.commit()
        assert (await db.get(Payment, item["id"])).status == "pending"
        await transaction(
            db, capture("DECLINED", item["provider_id"]), "capture", origin="imported"
        )
        await db.commit()
        assert (await db.get(Payment, item["id"])).status == "failed"
    response = await submit(
        client, {**headers, "Idempotency-Key": "explicit-restart"}, intent_key="fresh-after-decline"
    )
    assert response.status_code == 200
    assert response.json()["id"] != item["id"]


@pytest.mark.parametrize("stale", ["COMPLETED", "PENDING", "DECLINED", "PARTIALLY_REFUNDED"])
async def test_refunded_evidence_survives_stale_capture_reads(sessions, stale):
    async with sessions() as db:
        row = await transaction(db, capture("REFUNDED"), "capture", origin="imported")
        await db.commit()
        await transaction(db, capture(stale), "capture", origin="imported")
        await db.commit()
        assert row.provider_status == "REFUNDED"
        assert (await db.get(Payment, row.payment_id)).status == "refunded"
        eligibility = refund_eligibility(row, [])
        assert not eligibility["can_refund"]
        assert Decimal(eligibility["refundable_amount"]) == 0
        assert Decimal(eligibility["refunded_amount"]) == 50
