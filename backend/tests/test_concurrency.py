"""Concurrency behavior with dedicated PostgreSQL and mocked provider HTTP."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from app.models import now
from support import BODY, collection


@pytest.mark.asyncio
async def test_concurrent_refund_cannot_spend_reserved_funds_or_hold_lock_over_http(sessions):
    from app.auth import Principal
    from app.refunds import refund
    from app.schemas import RefundInput
    from fastapi import HTTPException

    identifier = await collection(sessions)
    started, release = asyncio.Event(), asyncio.Event()

    async def request(*args, **kwargs):
        started.set()
        await release.wait()
        return {
            "id": "REFUND-CONCURRENT",
            "status": "COMPLETED",
            "amount": {"value": "70.00", "currency_code": "ILS"},
            "create_time": now().isoformat(),
        }

    paypal = AsyncMock()
    paypal.request.side_effect = request
    async with sessions() as first_db, sessions() as second_db:
        first = asyncio.create_task(
            refund(
                first_db,
                identifier,
                RefundInput(amount="70.00", reason="Partial"),
                Principal("admin"),
                "first",
                paypal,
            )
        )
        await asyncio.wait_for(started.wait(), 3)
        try:
            with pytest.raises(HTTPException) as error:
                await refund(
                    second_db,
                    identifier,
                    RefundInput(amount="31.00", reason="Excess"),
                    Principal("staff2"),
                    "second",
                    paypal,
                )
            assert error.value.status_code == 409
        finally:
            await second_db.rollback()
            release.set()
            assert (await first)["status"] == "completed"
    assert paypal.request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon", [False, True])
async def test_concurrent_approval_claims_only_one_capture(sessions, settings, abandon):
    from app.auth import Principal
    from app.checkout import checkout_outcome, close_checkout
    from app.checkout import submit as direct_submit
    from app.schemas import OutcomeInput, SubmitInput
    from itsdangerous import URLSafeTimedSerializer

    started, release = asyncio.Event(), asyncio.Event()
    terms = {}
    captured = False

    async def request(method, path, **kwargs):
        nonlocal terms, captured
        if path == "/v2/checkout/orders":
            terms = kwargs["data"]
            return {"id": "ORDER-CONCURRENT"}
        if method == "POST":
            started.set()
            await release.wait()
            captured = True
            return {}
        unit = dict(terms["purchase_units"][0])
        if captured:
            unit["payments"] = {
                "captures": [
                    {
                        "id": "CAP-CONCURRENT",
                        "status": "COMPLETED",
                        "amount": unit["amount"],
                        "create_time": now().isoformat(),
                    }
                ]
            }
        return {
            "id": "ORDER-CONCURRENT",
            "intent": "CAPTURE",
            "status": "COMPLETED" if captured else "APPROVED",
            "purchase_units": [unit],
        }

    paypal = AsyncMock()
    paypal.request.side_effect = request
    signer, principal = URLSafeTimedSerializer(settings.app_secret), Principal("admin")
    async with sessions() as first_db, sessions() as second_db:
        item = await direct_submit(
            first_db, SubmitInput(**BODY), principal, "first", paypal, settings, signer
        )
        data = OutcomeInput(proof=item["proof"], outcome="approved")
        first = asyncio.create_task(
            checkout_outcome(first_db, item["id"], data, principal, paypal, settings, signer)
        )
        await asyncio.wait_for(started.wait(), 3)
        try:
            duplicate = await checkout_outcome(
                second_db, item["id"], data, principal, paypal, settings, signer
            )
            assert duplicate["status"] == "failed"
            if abandon:
                closed = await close_checkout(second_db, item["id"], signer)
                assert closed["checkout_status"] == "failed"
        finally:
            await second_db.rollback()
            release.set()
            finished = await first
            assert finished["status"] == "completed"
            assert finished["checkout_status"] == ("failed" if abandon else "success")
    assert (
        sum(
            call.args[0] == "POST" and call.args[1].endswith("/capture")
            for call in paypal.request.await_args_list
        )
        == 1
    )
