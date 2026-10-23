"""Terminal checkout results never gate another explicit payment or replay money actions."""

import asyncio
import hashlib
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from app.auth import Principal
from app.models import Attempt, Payment, Transaction
from app.normalize import transaction
from app.schemas import SubmitInput
from app.services import close_checkout
from app.services import submit as direct_submit
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select
from test_payments import BODY, mutations, outcome, submit


@pytest.mark.parametrize("recurring", [False, True])
def test_open_checkout_cannot_gate_an_independent_payment(client, headers, provider, recurring):
    """Even an unfinished checkout is never a global gate on an explicit new intent."""
    old = submit(client, headers, is_subscription=recurring).json()
    fresh = submit(
        client,
        {**headers, "Idempotency-Key": "independent-submit"},
        intent_key="independent-intent",
        is_subscription=recurring,
    )
    assert fresh.status_code == 200
    assert fresh.json()["id"] != old["id"]
    assert fresh.json()["provider_id"] != old["provider_id"]
    saved = client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()
    assert saved["checkout_status"] == "failed" and saved["browser_state"] == "closed"
    calls = len(provider["requests"])
    assert outcome(client, headers, old).json()["checkout_status"] == "failed"
    assert len(provider["requests"]) == calls
    assert not mutations(provider, "/capture")


@pytest.mark.asyncio
async def test_abandonment_during_create_cannot_return_a_reopened_checkout(sessions, settings):
    """Persist the provider ID but suppress presentation and proof after concurrent closure."""
    started, release = asyncio.Event(), asyncio.Event()

    async def request(*args, **kwargs):
        started.set()
        await release.wait()
        return {"id": "LATE-ORDER"}

    paypal = AsyncMock()
    paypal.request.side_effect = request
    signer = URLSafeTimedSerializer(settings.app_secret)
    with sessions() as first_db, sessions() as second_db:
        running = asyncio.create_task(
            direct_submit(
                first_db,
                SubmitInput(
                    first_name="Test",
                    last_name="Client",
                    agreement_number="1",
                    price_per_period="50.00",
                    intent_key="in-flight",
                ),
                Principal("admin"),
                "in-flight",
                paypal,
                settings,
                signer,
            )
        )
        await asyncio.wait_for(started.wait(), 3)
        try:
            attempt = second_db.scalar(select(Attempt))
            assert close_checkout(second_db, attempt, signer)["checkout_status"] == "failed"
        finally:
            second_db.rollback()
            release.set()
            result = await running
        assert result["checkout_status"] == "failed" and result["proof"] is None
        assert result["provider_id"] == "LATE-ORDER"
        assert "checkout_url" not in result
    assert paypal.request.await_count == 1


@pytest.mark.parametrize("recurring", [False, True])
@pytest.mark.parametrize("ending", ["close", "cancelled", "error"])
def test_failed_checkout_stays_failed_and_new_submit_is_independent(
    client, headers, provider, recurring, ending
):
    """Abandon/error/cancel close both product checkouts without provider cancellation."""
    item = submit(client, headers, is_subscription=recurring).json()
    calls = len(provider["requests"])
    if ending == "close":
        path = f"/api/v1/payments/{item['id']}/attempts/{item['attempt_id']}/close"
        result = client.post(path, headers=headers).json()
        assert client.post(path, headers=headers).json()["checkout_status"] == "failed"
    else:
        result = outcome(client, headers, item, ending).json()
    assert result["checkout_status"] == "failed"
    assert result["proof"] is None
    assert result["browser_state"] == "closed"
    assert outcome(client, headers, item).json()["checkout_status"] == "failed"
    assert len(provider["requests"]) == calls
    assert not mutations(provider, "/capture")
    assert not mutations(provider, "/cancel")
    replacement = submit(
        client,
        {**headers, "Idempotency-Key": "new-explicit-submit"},
        intent_key="new-payment",
        is_subscription=recurring,
    )
    assert replacement.status_code == 200
    fresh = replacement.json()
    assert fresh["id"] != item["id"] and fresh["attempt_id"] != item["attempt_id"]
    assert fresh["checkout_status"] is None and fresh["proof"]
    # An old success URL still resolves only the old, terminal failure.
    assert outcome(client, headers, item).json()["checkout_status"] == "failed"


@pytest.mark.parametrize("mode", ["create_timeout", "capture_timeout", "capture_decline"])
def test_uncertain_or_declined_action_never_blocks_new_explicit_identity(
    client, headers, provider, mode
):
    """Keep original financial uncertainty while allowing a deliberate independent attempt."""
    provider["mode"] = mode
    item = submit(client, headers).json()
    if mode != "create_timeout":
        item = outcome(client, headers, item).json()
    assert item["checkout_status"] == "failed"
    calls = len(provider["requests"])
    assert submit(client, headers).json()["checkout_status"] == "failed"
    assert len(provider["requests"]) == calls
    provider["mode"] = "ok"
    fresh = submit(
        client, {**headers, "Idempotency-Key": "new-attempt"}, intent_key="fresh-intent"
    ).json()
    assert fresh["id"] != item["id"]
    assert outcome(client, headers, fresh).json()["checkout_status"] == "success"
    saved = client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()
    assert saved["checkout_status"] == "failed"


def test_failed_subscription_return_is_not_promoted_by_provider_refresh(client, headers, provider):
    """A provider agreement may be active independently; reads cannot repair checkout failure."""
    item = submit(client, headers, is_subscription=True).json()
    assert outcome(client, headers, item, "error").json()["checkout_status"] == "failed"
    refreshed = client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert refreshed.status_code == 200
    saved = client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()
    assert saved["status"] == "active" and saved["checkout_status"] == "failed"
    count = len(provider["requests"])
    assert outcome(client, headers, item).json()["checkout_status"] == "failed"
    assert len(provider["requests"]) == count
    listing = client.get("/api/v1/payments?checkout_status=failed", headers=headers).json()
    assert listing["total"] == 1 and listing["items"][0]["checkout_status"] == "failed"
    assert (
        client.get("/api/v1/payments?checkout_status=success", headers=headers).json()["total"] == 0
    )
    subscriptions = client.get(
        "/api/v1/subscriptions?checkout_status=failed", headers=headers
    ).json()
    assert subscriptions["total"] == 1
    assert subscriptions["items"][0]["checkout_status"] == "failed"


def test_failed_checkout_keeps_actual_collection_evidence(sessions, client, headers, provider):
    """Closing the UI never erases a charge that was accepted before its response was lost."""
    item = submit(client, headers).json()
    provider["mode"] = "capture_timeout"
    assert outcome(client, headers, item).json()["checkout_status"] == "failed"
    with sessions() as db:
        transaction(
            db,
            {
                "id": "CONFIRMED-AFTER-TIMEOUT",
                "status": "COMPLETED",
                "amount": {"value": "50.00", "currency_code": "ILS"},
                "create_time": "2026-10-23T08:00:00Z",
            },
            "capture",
            payment_id=item["id"],
        )
        db.commit()
        assert db.get(Payment, item["id"]).status == "completed"
        assert db.get(Attempt, item["attempt_id"]).checkout_status == "failed"
        assert db.scalar(select(Transaction)).amount == 50


def test_subscription_success_url_requires_verified_approval(client, headers, provider):
    """A success-path URL cannot falsely approve an agreement still waiting at PayPal."""
    item = submit(client, headers, is_subscription=True).json()
    provider["subscription"]["status"] = "APPROVAL_PENDING"
    result = outcome(client, headers, item).json()
    assert result["checkout_status"] == "failed"
    count = len(provider["requests"])
    provider["subscription"]["status"] = "ACTIVE"
    assert outcome(client, headers, item).json()["checkout_status"] == "failed"
    assert len(provider["requests"]) == count


@pytest.mark.parametrize("recurring", [False, True])
def test_success_stays_success_when_checkout_is_closed(client, headers, provider, recurring):
    """Closing/reloading a completed browser flow cannot rewrite its result."""
    item = submit(client, headers, is_subscription=recurring).json()
    assert outcome(client, headers, item).json()["checkout_status"] == "success"
    calls = len(provider["requests"])
    path = f"/api/v1/payments/{item['id']}/attempts/{item['attempt_id']}/close"
    assert client.post(path, headers=headers).json()["checkout_status"] == "success"
    assert outcome(client, headers, item, "cancelled").json()["checkout_status"] == "success"
    assert len(provider["requests"]) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["create", "capture"])
async def test_operator_waits_for_open_request_other_operators_remain_independent(
    client, headers, provider, settings, monkeypatch, phase
):
    """Reject another tab's checkout while HTTP runs; never hold a DB lock or queue it."""
    settings.api_keys["other"] = {"sha256": hashlib.sha256(b"other-key").hexdigest()}
    started, release = asyncio.Event(), asyncio.Event()
    paypal = client.app.state.paypal
    request = paypal.request

    async def held_request(method, path, **kwargs):
        target = path == "/v2/checkout/orders" if phase == "create" else path.endswith("/capture")
        if method == "POST" and target and not started.is_set():
            started.set()
            await release.wait()
        return await request(method, path, **kwargs)

    initial = submit(client, headers).json() if phase == "capture" else None
    monkeypatch.setattr(paypal, "request", held_request)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
    ) as browser:
        if initial:
            first = asyncio.create_task(
                browser.post(
                    f"/api/v1/payments/{initial['id']}/checkout-outcome",
                    headers=headers,
                    json={
                        "attempt_id": initial["attempt_id"],
                        "proof": initial["proof"],
                        "outcome": "approved",
                        "provider_id": initial["provider_id"],
                    },
                )
            )
        else:
            first = asyncio.create_task(
                browser.post("/api/v1/payments", headers=headers, json=BODY)
            )
        await asyncio.wait_for(started.wait(), 3)
        try:
            calls = len(provider["requests"])
            rejected = await browser.post(
                "/api/v1/payments",
                headers={**headers, "Idempotency-Key": "too-early"},
                json={**BODY, "intent_key": "too-early"},
            )
            assert rejected.status_code == 409 and "still running" in rejected.json()["detail"]
            assert len(provider["requests"]) == calls
            old = (
                await browser.get("/api/v1/checkout-submissions/intent-1", headers=headers)
            ).json()
            closed = await browser.post(
                f"/api/v1/payments/{old['id']}/attempts/{old['attempt_id']}/close",
                headers=headers,
            )
            assert closed.status_code == 409
            other = await browser.post(
                "/api/v1/payments",
                headers={"Authorization": "Bearer other-key", "Idempotency-Key": "other-submit"},
                json={**BODY, "intent_key": "other-intent"},
            )
            assert other.status_code == 200
        finally:
            release.set()
            response = await asyncio.wait_for(first, 5)
        assert response.status_code == 200
        assert client.app.state.checkout_requests == set()
        fresh = await browser.post(
            "/api/v1/payments",
            headers={**headers, "Idempotency-Key": "after-response"},
            json={**BODY, "intent_key": "after-response"},
        )
        assert fresh.status_code == 200
        saved = (await browser.get("/api/v1/checkout-submissions/intent-1", headers=headers)).json()
        assert saved["checkout_status"] == ("success" if phase == "capture" else "failed")
        assert client.app.state.checkout_requests == set()


def test_new_submit_closes_all_own_unfinished_checkouts_only(sessions, client, headers, provider):
    """Close old local permissions atomically, preserving other staff and verified money."""
    old = submit(client, headers).json()
    with sessions() as db:
        original = db.get(Attempt, old["attempt_id"])
        actor, terms = original.actor, original.terms
        db.get(Payment, old["id"]).status = "completed"
        original.status = "completed"
        for key, owner, checkout_status in [
            ("unfinished", actor, None),
            ("finished", actor, "success"),
            ("other", "other", None),
        ]:
            payment = Payment(
                actor=owner, intent_key=key, price_per_period=Decimal("50"), currency="ILS"
            )
            db.add(payment)
            db.flush()
            db.add(
                Attempt(
                    payment_id=payment.id,
                    actor=owner,
                    submission_key=key,
                    input_hash=key,
                    terms=terms,
                    status="unknown",
                    checkout_status=checkout_status,
                    phase="create_dispatched",
                )
            )
        db.commit()
    fresh = submit(client, {**headers, "Idempotency-Key": "new-submit"}, intent_key="new-intent")
    assert fresh.status_code == 200
    with sessions() as db:
        rows = {r.submission_key: r for r in db.scalars(select(Attempt))}
        assert rows["create-1"].checkout_status == rows["unfinished"].checkout_status == "failed"
        assert rows["create-1"].browser_state == rows["unfinished"].browser_state == "closed"
        assert rows["finished"].checkout_status == "success"
        assert rows["other"].checkout_status is None and rows["other"].browser_state == "open"
        assert db.get(Payment, old["id"]).status == "completed"
        assert rows["create-1"].status == "completed"
    calls = len(provider["requests"])
    assert (
        submit(
            client, {**headers, "Idempotency-Key": "new-submit"}, intent_key="new-intent"
        ).status_code
        == 200
    )
    assert len(provider["requests"]) == calls
    assert not mutations(provider, "/cancel") and not mutations(provider, "/capture")


def test_checkout_guard_releases_after_rejected_request(client, headers, provider):
    """Rejected input cannot strand the operator's next explicit request."""
    submit(client, headers)
    rejected = submit(client, headers, price_per_period="60.00")
    assert rejected.status_code == 409
    assert client.app.state.checkout_requests == set()
    assert (
        submit(client, {**headers, "Idempotency-Key": "fresh"}, intent_key="fresh").status_code
        == 200
    )
