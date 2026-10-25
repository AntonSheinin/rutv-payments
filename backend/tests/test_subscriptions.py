"""Subscriptions behavior with dedicated PostgreSQL and mocked provider HTTP."""

import pytest
from app.models import FinancialAction, Payment
from app.normalize import subscription, transaction
from app.paypal import PayPalError
from sqlalchemy import select
from support import capture, mutations, outcome, submit


@pytest.mark.parametrize("dispatched", [False, True])
async def test_rejected_cancellation_releases_claim_without_replaying_the_original_action(
    client, headers, provider, sessions, monkeypatch, payment_logs, dispatched
):
    """Pre-dispatch OAuth and explicit provider rejection permit only a fresh staff action."""
    item = (await submit(client, headers, is_subscription=True)).json()
    await outcome(client, headers, item)
    sub = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"][0]
    attempts = 0

    async def reject(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise PayPalError(422, "SUBSCRIPTION_STATUS_INVALID", "safe-debug-id")

    monkeypatch.setattr(
        client.app.state.paypal, "request" if dispatched else "access_token", reject
    )
    path = f"/api/v1/subscriptions/{sub['id']}/cancel"
    body = {"reason": "PRIVATE-REASON"}
    result = await client.post(path, headers=headers, json=body)
    assert result.json()["status"] == "failed"
    assert (await client.post(path, headers=headers, json=body)).json() == result.json()
    assert attempts == 1
    assert payment_logs.text.count("subscription_cancel_reserved") == 1
    assert payment_logs.text.count("subscription_cancel_result") == 1
    assert "reservation_active=False" in payment_logs.text
    assert "debug_id='safe-debug-id'" in payment_logs.text
    assert "PRIVATE-REASON" not in payment_logs.text
    assert (
        await client.post(path, headers=headers, json={"reason": "Changed input"})
    ).status_code == 409
    async with sessions() as db:
        assert (await db.scalar(select(FinancialAction.reservation_active))) is False
    fresh = await client.post(
        path, headers={**headers, "Idempotency-Key": "fresh-cancellation"}, json=body
    )
    assert fresh.json()["status"] == "failed" and attempts == 2


@pytest.mark.parametrize("status", ["ACTIVE", "SUSPENDED"])
async def test_real_subscription_cancellation_retains_uncertain_action_and_never_retries(
    client, headers, provider, status
):
    """A lost result for a supported cancellation remains blocked without another POST."""
    item = (await submit(client, headers, is_subscription=True)).json()
    await outcome(client, headers, item)
    sub = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"][0]
    if status == "SUSPENDED":
        provider["subscription"]["status"] = status
        await client.post(
            "/api/v1/synchronizations",
            headers=headers,
            json={"scope": "subscription", "resource_id": sub["id"]},
        )
    provider["mode"] = "subscription_cancel_timeout"
    path = f"/api/v1/subscriptions/{sub['id']}/cancel"
    response = await client.post(path, headers=headers, json={"reason": "Operator request"})
    assert response.status_code == 200 and response.json()["status"] == "failed"
    assert (await client.post(path, headers=headers, json={"reason": "Operator request"})).json()[
        "status"
    ] == "failed"
    assert (
        await client.post(
            path,
            headers={**headers, "Idempotency-Key": "new-key"},
            json={"reason": "Operator request"},
        )
    ).status_code == 409
    assert len(mutations(provider, "/cancel")) == 1


@pytest.mark.parametrize("cycles", [3, None])
async def test_subscription_approval_is_not_collection_or_orders_capture(
    client, headers, provider, cycles
):
    item = (await submit(client, headers, is_subscription=True, payment_cycles=cycles)).json()
    assert provider["subscription"]["subscriber"]["name"] == {
        "given_name": "Test",
        "surname": "client",
    }
    assert item["status"] == "pending" and item["provider_id"]
    assert "checkout_url" not in item
    assert (await outcome(client, headers, item)).json()["status"] == "active"
    assert (await outcome(client, headers, item)).json()["status"] == "active"
    assert not mutations(provider, "/orders") and not mutations(provider, "/capture")
    subscriptions = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"]
    assert len(subscriptions) == 1 and subscriptions[0]["client_name"] == "Test client"
    assert (await client.get("/api/v1/transactions", headers=headers)).json()["total"] == 0
    path = "/api/v1/subscriptions/" + subscriptions[0]["id"] + "/cancel"
    assert (await client.post(path, headers=headers, json={"reason": "Stop renewals"})).json()[
        "status"
    ] == "completed"
    assert (await client.post(path, headers=headers, json={"reason": "Stop renewals"})).json()[
        "status"
    ] == "completed"
    assert len(mutations(provider, "/cancel")) == 1 and not provider["refunds"]


@pytest.mark.parametrize(
    "changed_term", ["tax", "quantity", "shipping", "setup_fee", "frequency", "cycles"]
)
async def test_subscription_effective_terms_must_match_the_saved_agreement(
    client, headers, provider, changed_term
):
    item = (await submit(client, headers, is_subscription=True, payment_cycles=3)).json()
    if changed_term == "tax":
        provider["plan"]["taxes"] = {"percentage": "10", "inclusive": False}
    elif changed_term == "quantity":
        provider["subscription"]["quantity"] = "2"
    elif changed_term == "shipping":
        provider["subscription"]["shipping_amount"] = {"value": "1.00", "currency_code": "ILS"}
    elif changed_term == "setup_fee":
        provider["plan"]["payment_preferences"]["setup_fee"] = {
            "value": "1.00",
            "currency_code": "ILS",
        }
    elif changed_term == "frequency":
        provider["plan"]["billing_cycles"][0]["frequency"]["interval_count"] = 2
    else:
        provider["plan"]["billing_cycles"][0]["total_cycles"] = 4
    assert (await outcome(client, headers, item)).json()["status"] == "failed"
    assert not mutations(provider, "/capture")
    assert (await client.get("/api/v1/subscriptions", headers=headers)).json()["total"] == 0


@pytest.mark.parametrize("currency,amount", [("GBP", "99.00"), ("ILS", "99.00"), ("USD", "50.00")])
async def test_subscription_refresh_rejects_changed_effective_terms(
    client, headers, provider, currency, amount
):
    item = (await submit(client, headers, is_subscription=True)).json()
    assert (await outcome(client, headers, item)).json()["status"] == "active"
    local = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"][0]
    provider["plan"]["billing_cycles"][0]["pricing_scheme"]["fixed_price"] = {
        "value": amount,
        "currency_code": currency,
    }
    response = await client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "subscription", "resource_id": local["id"]},
    )
    assert response.status_code == 502
    saved = (await client.get("/api/v1/subscriptions/" + local["id"], headers=headers)).json()
    assert saved["amount"] == "50.00" and saved["currency"] == "ILS"
    assert saved["last_synced_at"] == local["last_synced_at"]
    assert not mutations(provider, "/capture")


async def test_missing_subscription_preserves_financial_uncertainty_and_original_identity(
    client, headers, provider, sessions
):
    """An existing uncertain cancellation remains unresolved when a read returns 404."""
    item = (await submit(client, headers, is_subscription=True)).json()
    await outcome(client, headers, item)
    sub = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"][0]
    provider["mode"] = "subscription_cancel_timeout"
    path = f"/api/v1/subscriptions/{sub['id']}/cancel"
    body = {"reason": "Operator request"}
    assert (await client.post(path, headers=headers, json=body)).json()["status"] == "failed"
    provider["mode"] = "subscription_missing"
    result = await client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "subscription", "resource_id": sub["id"]},
    )
    assert result.status_code == 502
    saved = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    assert saved["status"] == "active" and saved["provider_id"] == item["provider_id"]
    async with sessions() as db:
        action = await db.scalar(select(FinancialAction))
        assert action.status == "failed" and action.reservation_active
    assert (await client.post(path, headers=headers, json=body)).json()["status"] == "failed"
    assert (
        await client.post(path, headers={**headers, "Idempotency-Key": "cannot-replace"}, json=body)
    ).status_code == 409
    assert len(mutations(provider, "/cancel")) == 1


async def test_subscription_cancel_updates_payment_and_preserves_installment(
    sessions, client, headers, provider
):
    item = (await submit(client, headers, is_subscription=True)).json()
    await outcome(client, headers, item)
    sub = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"][0]
    async with sessions() as db:
        installment = await transaction(db, capture(), "sale", subscription_id=sub["id"])
        installment_payment_id = installment.payment_id
        await db.commit()
    result = await client.post(
        f"/api/v1/subscriptions/{sub['id']}/cancel",
        headers=headers,
        json={"reason": "Operator cancellation"},
    )
    assert result.status_code == 200
    assert (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()[
        "status"
    ] == "cancelled"
    assert (await client.get(f"/api/v1/payments/{installment_payment_id}", headers=headers)).json()[
        "status"
    ] == "completed"
    async with sessions() as db:
        data = {**provider["subscription"], "status": "ACTIVE", "plan": provider["plan"]}
        await subscription(db, data, payment=(await db.get(Payment, item["id"])))
        await db.commit()
        assert (await db.get(Payment, item["id"])).status == "cancelled"
