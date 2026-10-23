"""Financial behavior of the direct flow, using PostgreSQL and mocked PayPal HTTP."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.models import Attempt, FinancialAction, Transaction, now
from app.services import refund_eligibility
from sqlalchemy import func, select

BODY = {
    "first_name": "Test",
    "last_name": "client",
    "agreement_number": "000123-A",
    "price_per_period": "50.00",
    "currency": "ILS",
    "intent_key": "intent-1",
}


def submit(client, headers, **changes):
    return client.post("/api/v1/payments", headers=headers, json={**BODY, **changes})


def outcome(client, headers, item, value="approved", **changes):
    return client.post(
        f"/api/v1/payments/{item['id']}/checkout-outcome",
        headers=headers,
        json={
            "attempt_id": item["attempt_id"],
            "proof": item["proof"],
            "provider_id": item["provider_id"],
            "outcome": value,
            **changes,
        },
    )


def mutations(provider, ending):
    return [r for r in provider["requests"] if r.method == "POST" and r.url.path.endswith(ending)]


def test_cancel_waiting_attempt_allows_new_submit_and_blocks_late_approval(
    client, headers, provider
):
    item = submit(client, headers).json()
    assert item["can_cancel"]
    listing = client.get("/api/v1/payments", headers=headers).json()["items"]
    assert listing[0]["cancellable_attempt_id"] == item["attempt_id"]
    detail = client.get("/api/v1/payments/" + item["id"], headers=headers).json()
    assert detail["attempts"]["items"][0]["can_cancel"]
    path = f"/api/v1/payments/{item['id']}/attempts/{item['attempt_id']}/cancel"
    count = len(provider["requests"])
    result = client.post(path, headers=headers)
    assert result.status_code == 200
    assert result.json()["status"] == "cancelled" and result.json()["checkout_status"] == "failed"
    assert not result.json()["can_cancel"]
    assert client.post(path, headers=headers).json()["status"] == "cancelled"
    assert len(provider["requests"]) == count
    assert outcome(client, headers, item).json()["status"] == "cancelled"
    assert not mutations(provider, "/capture")
    new = submit(
        client, {**headers, "Idempotency-Key": "operator-restart"}, intent_key="fresh-payment"
    ).json()
    assert new["id"] != item["id"] and new["attempt_id"] != item["attempt_id"]
    assert client.post(path, headers=headers).json()["status"] == "cancelled"
    assert (
        client.get("/api/v1/checkout-submissions/fresh-payment", headers=headers).json()["status"]
        == "awaiting_approval"
    )


@pytest.mark.parametrize("mode", ["ok", "capture_timeout"])
def test_claimed_or_collected_attempt_cannot_be_cancelled(client, headers, provider, mode):
    provider["mode"] = mode
    item = submit(client, headers).json()
    outcome(client, headers, item)
    count = len(provider["requests"])
    response = client.post(
        f"/api/v1/payments/{item['id']}/attempts/{item['attempt_id']}/cancel",
        headers=headers,
    )
    assert response.status_code == 409
    assert len(provider["requests"]) == count
    assert submit(client, {**headers, "Idempotency-Key": "unsafe-restart"}).status_code == 409


@pytest.mark.parametrize("status", ["APPROVAL_PENDING", "APPROVED", "ACTIVE", "SUSPENDED"])
def test_subscription_checkout_cancellation_is_rejected_without_provider_calls_or_state_changes(
    client, headers, provider, sessions, status
):
    """Never send PayPal's active-agreement cancel action for a checkout attempt."""
    item = submit(client, headers, is_subscription=True).json()
    assert item["can_cancel"] is False
    provider["subscription"]["status"] = status
    count = len(provider["requests"])
    path = f"/api/v1/payments/{item['id']}/attempts/{item['attempt_id']}/cancel"
    for _ in range(2):
        result = client.post(path, headers=headers)
        assert result.status_code == 409
        assert "active or suspended" in result.json()["detail"]
    assert len(provider["requests"]) == count
    saved = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    assert saved["status"] == "awaiting_approval"
    assert saved["cancellable_attempt_id"] is None
    assert saved["attempts"]["items"][0]["phase"] == "created"
    assert saved["attempts"]["items"][0]["browser_state"] == "open"
    assert not saved["attempts"]["items"][0]["can_cancel"]
    assert (
        submit(
            client, {**headers, "Idempotency-Key": "blocked-restart"}, is_subscription=True
        ).status_code
        == 409
    )


@pytest.mark.parametrize("status", ["ACTIVE", "SUSPENDED"])
def test_real_subscription_cancellation_retains_uncertain_action_and_never_retries(
    client, headers, provider, status
):
    """A lost result for a supported cancellation remains blocked without another POST."""
    item = submit(client, headers, is_subscription=True).json()
    outcome(client, headers, item)
    sub = client.get("/api/v1/subscriptions", headers=headers).json()["items"][0]
    if status == "SUSPENDED":
        provider["subscription"]["status"] = status
        client.post(
            "/api/v1/synchronizations",
            headers=headers,
            json={"scope": "subscription", "resource_id": sub["id"]},
        )
    provider["mode"] = "subscription_cancel_timeout"
    path = f"/api/v1/subscriptions/{sub['id']}/cancel"
    response = client.post(path, headers=headers, json={"reason": "Operator request"})
    assert response.status_code == 200 and response.json()["status"] == "unknown"
    assert (
        client.post(path, headers=headers, json={"reason": "Operator request"}).json()["status"]
        == "unknown"
    )
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": "new-key"},
            json={"reason": "Operator request"},
        ).status_code
        == 409
    )
    assert len(mutations(provider, "/cancel")) == 1


def test_submit_capture_duplicate_callback_and_reload_never_repeat(
    client, headers, provider, sessions
):
    response = submit(client, headers)
    assert response.status_code == 200
    item = response.json()
    assert item["status"] == "awaiting_approval"
    assert item["terms"]["first_name"] == "Test"
    assert item["terms"]["last_name"] == "client"
    assert provider["orders"][item["provider_id"]]["payment_source"]["paypal"]["name"] == {
        "given_name": "Test",
        "surname": "client",
    }
    assert (
        provider["orders"][item["provider_id"]]["payment_source"]["paypal"]["experience_context"][
            "shipping_preference"
        ]
        == "NO_SHIPPING"
    )
    assert submit(client, headers).json()["attempt_id"] == item["attempt_id"]
    result = outcome(client, headers, item)
    assert result.status_code == 200 and result.json()["status"] == "completed"
    assert outcome(client, headers, item).json()["status"] == "completed"
    for _ in range(2):
        assert (
            client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()["status"]
            == "completed"
        )
    assert len(mutations(provider, "/orders")) == len(mutations(provider, "/capture")) == 1
    assert submit(client, {**headers, "Idempotency-Key": "new-key"}).status_code == 409
    with sessions() as db:
        row = db.scalar(select(Transaction).where(Transaction.kind == "collection"))
        assert row.client_name == "Test client" and row.agreement_number == "000123-A"
        assert row.amount == Decimal("50.00")


@pytest.mark.parametrize("callback", ["cancelled", "error"])
def test_closed_attempt_cannot_capture_and_only_explicit_submit_restarts(
    client, headers, provider, sessions, callback
):
    item = submit(client, headers).json()
    assert outcome(client, headers, item, callback).json()["checkout_status"] == "failed"
    assert outcome(client, headers, item).json()["status"] in {"failed", "cancelled"}
    assert not mutations(provider, "/capture")
    replacement = submit(
        client,
        {**headers, "Idempotency-Key": "deliberate-new-attempt"},
        price_per_period="60.00",
        intent_key="fresh-replacement",
    ).json()
    assert replacement["id"] != item["id"] and replacement["attempt_id"] != item["attempt_id"]
    with sessions() as db:
        assert db.get(Attempt, item["attempt_id"]).terms["price_per_period"] == "50.00"
    assert not mutations(provider, "/capture")


@pytest.mark.parametrize(
    "mode", ["create_timeout", "capture_timeout", "wrong_merchant", "post_capture_read_error"]
)
def test_uncertain_or_mismatched_attempt_stays_reserved_without_replay(
    client, headers, provider, mode
):
    provider["mode"] = mode
    item = submit(client, headers).json()
    if mode != "create_timeout":
        item = outcome(client, headers, item).json()
    assert item["status"] == "unknown" and item["checkout_status"] == "failed"
    before = len(provider["requests"])
    assert submit(client, headers).status_code == 200
    assert submit(client, {**headers, "Idempotency-Key": "replacement"}).status_code == 409
    assert (
        client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()["status"]
        == "unknown"
    )
    assert len(provider["requests"]) == before
    if mode == "wrong_merchant":
        assert not mutations(provider, "/capture")


def test_capture_decline_stops_until_operator_starts_again(client, headers, provider):
    item = submit(client, headers).json()
    provider["mode"] = "capture_decline"
    assert outcome(client, headers, item).json()["status"] == "failed"
    assert outcome(client, headers, item).json()["status"] == "failed"
    assert len(mutations(provider, "/capture")) == 1


@pytest.mark.parametrize(
    "changes", [{"proof": "forged"}, {"provider_id": "WRONG"}, {"attempt_id": "not-this-attempt"}]
)
def test_callback_correlation_rejects_forgery(client, headers, provider, changes):
    item = submit(client, headers).json()
    assert outcome(client, headers, item, **changes).status_code in {400, 403, 404, 409}
    assert not mutations(provider, "/capture")


def test_submission_key_input_binding_and_currency_rules(client, headers, provider):
    assert submit(client, headers).status_code == 200
    assert submit(client, headers, price_per_period="51.00").status_code == 409
    assert client.get("/api/v1/options", headers=headers).json()["currencies"] == [
        "ILS",
        "USD",
        "EUR",
    ]
    assert client.get("/api/v1/transactions?currency=GBP", headers=headers).status_code == 422


@pytest.mark.parametrize("cycles", [3, None])
def test_subscription_approval_is_not_collection_or_orders_capture(
    client, headers, provider, cycles
):
    item = submit(client, headers, is_subscription=True, payment_cycles=cycles).json()
    assert provider["subscription"]["subscriber"]["name"] == {
        "given_name": "Test",
        "surname": "client",
    }
    assert item["status"] == "awaiting_approval" and item["provider_id"]
    assert "checkout_url" not in item
    assert outcome(client, headers, item).json()["status"] == "active"
    assert outcome(client, headers, item).json()["status"] == "active"
    assert not mutations(provider, "/orders") and not mutations(provider, "/capture")
    subscriptions = client.get("/api/v1/subscriptions", headers=headers).json()["items"]
    assert len(subscriptions) == 1 and subscriptions[0]["client_name"] == "Test client"
    assert client.get("/api/v1/transactions", headers=headers).json()["total"] == 0
    path = "/api/v1/subscriptions/" + subscriptions[0]["id"] + "/cancel"
    assert (
        client.post(path, headers=headers, json={"reason": "Stop renewals"}).json()["status"]
        == "completed"
    )
    assert (
        client.post(path, headers=headers, json={"reason": "Stop renewals"}).json()["status"]
        == "completed"
    )
    assert len(mutations(provider, "/cancel")) == 1 and not provider["refunds"]


def test_subscription_checkout_is_available_without_capability_configuration(
    client, headers, provider
):
    result = submit(client, headers, is_subscription=True, payment_cycles=3)
    assert result.status_code == 200
    assert result.json()["status"] == "awaiting_approval"
    assert len(mutations(provider, "/billing/subscriptions")) == 1
    assert not mutations(provider, "/capture")


def test_subscription_effective_plan_changes_do_not_pass_base_plan_validation(
    client, headers, provider
):
    item = submit(client, headers, is_subscription=True, payment_cycles=3).json()
    provider["plan"]["taxes"] = {"percentage": "10", "inclusive": False}
    assert outcome(client, headers, item).json()["status"] == "unknown"
    assert not mutations(provider, "/capture")


@pytest.mark.parametrize("status", ["DECLINED", "FAILED"])
def test_refresh_failed_capture_allows_only_explicit_new_attempt(client, headers, provider, status):
    provider["capture_status"] = "PENDING"
    item = submit(client, headers).json()
    assert outcome(client, headers, item).json()["status"] == "pending"
    provider["capture_status"] = status
    response = client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert response.status_code == 200
    saved = client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()
    assert saved["status"] == "failed" and saved["checkout_status"] == "success"
    requests = client.get("/api/v1/payments", headers=headers).json()["items"]
    assert requests[0]["status"] == "failed"
    assert len(mutations(provider, "/orders")) == len(mutations(provider, "/capture")) == 1
    replacement = submit(
        client,
        {**headers, "Idempotency-Key": "operator-new-attempt"},
        intent_key="fresh-after-provider-decline",
    ).json()
    assert replacement["id"] != item["id"] and replacement["attempt_id"] != item["attempt_id"]
    assert len(mutations(provider, "/capture")) == 1


@pytest.mark.parametrize("currency,amount", [("GBP", "99.00"), ("ILS", "99.00"), ("USD", "50.00")])
def test_subscription_refresh_rejects_changed_effective_terms(
    client, headers, provider, currency, amount
):
    item = submit(client, headers, is_subscription=True).json()
    assert outcome(client, headers, item).json()["status"] == "active"
    local = client.get("/api/v1/subscriptions", headers=headers).json()["items"][0]
    provider["plan"]["billing_cycles"][0]["pricing_scheme"]["fixed_price"] = {
        "value": amount,
        "currency_code": currency,
    }
    response = client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "subscription", "resource_id": local["id"]},
    )
    assert response.status_code == 502
    saved = client.get("/api/v1/subscriptions/" + local["id"], headers=headers).json()
    assert saved["amount"] == "50.00" and saved["currency"] == "ILS"
    assert saved["last_synced_at"] == local["last_synced_at"]
    assert not mutations(provider, "/capture")


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "GBP"},
        {"currency": "CAD"},
        {"price_per_period": 5.0},
        {"price_per_period": "0.00"},
        {"price_per_period": "1.001"},
        {"first_name": "  "},
        {"last_name": "  "},
        {"first_name": "A" * 141},
        {"first_name": "A" * 140, "last_name": "B" * 140},
        {"agreement_number": ""},
        {"payment_cycles": 3},
        {"external_reference": "old-contract"},
    ],
)
def test_invalid_inputs_never_create_a_checkout(client, headers, provider, changes):
    assert submit(client, headers, **changes).status_code == 422
    assert not mutations(provider, "/orders")


def collection(sessions, *, api_family="capture", **changes):
    with sessions() as db:
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
        db.commit()
        return row.id


@pytest.mark.parametrize("api_family", ["capture", "sale"])
def test_partial_refund_remaining_button_and_duplicate_confirmation(
    client, headers, provider, sessions, api_family
):
    identifier = collection(sessions, api_family=api_family)
    path = f"/api/v1/transactions/{identifier}/refunds"
    before = client.get("/api/v1/transactions", headers=headers).json()["items"][0]
    assert before["can_refund"] and Decimal(before["refundable_amount"]) == 100
    body = {"amount": "30.00", "reason": "Requested correction"}
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == 200 and result.json()["status"] == "completed"
    assert client.post(path, headers=headers, json=body).json()["id"] == result.json()["id"]
    amount_field = "value" if api_family == "capture" else "total"
    assert len(provider["refunds"]) == 1
    assert provider["refunds"][0]["amount"][amount_field] == "30.00"
    assert mutations(
        provider, f"/{'captures' if api_family == 'capture' else 'sale'}/CAPTURE1/refund"
    )
    detail = client.get(f"/api/v1/transactions/{identifier}", headers=headers).json()
    assert detail["can_refund"] and Decimal(detail["refundable_amount"]) == 70
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": "too-large"},
            json={**body, "amount": "71.00"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": "remaining"},
            json={**body, "amount": "70.00"},
        ).json()["status"]
        == "completed"
    )
    assert not client.get(f"/api/v1/transactions/{identifier}", headers=headers).json()[
        "can_refund"
    ]


@pytest.mark.parametrize(
    "mode,status", [("refund_timeout", "unknown"), ("refund_pending", "pending")]
)
def test_uncertain_pending_refunds_reserve_exact_amount(
    client, headers, provider, sessions, mode, status
):
    identifier = collection(sessions)
    provider["mode"] = mode
    path = f"/api/v1/transactions/{identifier}/refunds"
    result = client.post(path, headers=headers, json={"amount": "70.00", "reason": "Partial"})
    assert result.json()["status"] == status
    client.post(path, headers=headers, json={"amount": "70.00", "reason": "Partial"})
    assert len(provider["refunds"]) == 1
    assert (
        client.post(
            path,
            headers={**headers, "Idempotency-Key": "overspend"},
            json={"amount": "31.00", "reason": "Partial"},
        ).status_code
        == 409
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(FinancialAction)) == 1


@pytest.mark.parametrize("offset,eligible", [(-1, True), (0, False), (1, False)])
def test_refund_deadline_original_provider_timestamp(monkeypatch, offset, eligible):
    captured = now() - timedelta(days=180)
    row = Transaction(
        provider_id="CAP1",
        api_family="capture",
        kind="collection",
        status="completed",
        provider_status="COMPLETED",
        amount=Decimal("5.00"),
        currency="EUR",
        effective_at=captured,
    )
    monkeypatch.setattr("app.services.now", lambda: captured + timedelta(days=180, seconds=offset))
    assert refund_eligibility(row, [])["can_refund"] is eligible


def test_no_worker_webhook_roles_or_legacy_contracts(client, headers):
    schema = client.get("/api/v1/openapi.json", headers=headers).json()
    assert not any(
        "operations" in p or "webhooks" in p or p.endswith("/checkout") for p in schema["paths"]
    )
    assert "role" not in str(schema)
    assert client.post("/webhooks/paypal", json={}).status_code == 404
    assert client.get("/health/ready").status_code == 401
    assert client.get("/health/ready", headers=headers).status_code == 200


def test_simple_staff_login_csrf_cookie_and_same_access(client):
    for name, password in [("admin", "test-admin-password"), ("staff2", "test-staff2-password")]:
        result = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": name, "password": password},
        )
        assert result.status_code == 200 and set(result.json()) == {"name", "csrf"}
        assert "samesite=lax" in result.headers["set-cookie"].lower()
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert (
            client.post(
                "/api/v1/auth/logout",
                headers={"Origin": "http://testserver", "X-CSRF-Token": result.json()["csrf"]},
            ).status_code
            == 200
        )
        assert client.get("/api/v1/auth/me").status_code == 401


def test_database_failure_after_capture_keeps_claim_and_sync_is_read_only(
    client, headers, provider, sessions, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    item = submit(client, headers).json()
    commit = sessions.class_.commit
    failed = False

    def interrupt(db):
        nonlocal failed
        if provider["captured"] and not failed:
            failed = True
            raise OperationalError("PRIVATE SQL", {}, Exception("PRIVATE persistence failure"))
        return commit(db)

    monkeypatch.setattr(sessions.class_, "commit", interrupt)
    response = outcome(client, headers, item)
    assert response.status_code == 503 and "PRIVATE" not in response.text
    assert outcome(client, headers, item).json()["status"] == "unknown"
    assert submit(client, {**headers, "Idempotency-Key": "new"}).status_code == 409
    response = client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert response.status_code == 200
    assert (
        client.get("/api/v1/checkout-submissions/intent-1", headers=headers).json()["status"]
        == "completed"
    )
    assert len(mutations(provider, "/capture")) == 1


@pytest.mark.asyncio
async def test_concurrent_refund_cannot_spend_reserved_funds_or_hold_lock_over_http(sessions):
    from app.auth import Principal
    from app.schemas import RefundInput
    from app.services import refund
    from fastapi import HTTPException

    identifier = collection(sessions)
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
    with sessions() as first_db, sessions() as second_db:
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
            second_db.rollback()
            release.set()
            assert (await first)["status"] == "completed"
    assert paypal.request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon", [False, True])
async def test_concurrent_approval_claims_only_one_capture(sessions, settings, abandon):
    from app.auth import Principal
    from app.schemas import OutcomeInput, SubmitInput
    from app.services import cancel_attempt, checkout_outcome, close_checkout
    from app.services import submit as direct_submit
    from fastapi import HTTPException
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
    with sessions() as first_db, sessions() as second_db:
        item = await direct_submit(
            first_db, SubmitInput(**BODY), principal, "first", paypal, settings, signer
        )
        data = OutcomeInput(attempt_id=item["attempt_id"], proof=item["proof"], outcome="approved")
        first = asyncio.create_task(
            checkout_outcome(first_db, item["id"], data, principal, paypal, settings, signer)
        )
        await asyncio.wait_for(started.wait(), 3)
        try:
            duplicate = await checkout_outcome(
                second_db, item["id"], data, principal, paypal, settings, signer
            )
            assert duplicate["status"] == "unknown"
            with pytest.raises(HTTPException) as cancellation:
                await cancel_attempt(second_db, item["id"], item["attempt_id"], principal, signer)
            assert cancellation.value.status_code == 409
            second_db.rollback()
            if abandon:
                closed = close_checkout(
                    second_db, second_db.get(Attempt, item["attempt_id"]), signer
                )
                assert closed["checkout_status"] == "failed"
        finally:
            second_db.rollback()
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


def test_missing_subscription_preserves_financial_uncertainty_and_original_identity(
    client, headers, provider, sessions
):
    """An existing uncertain cancellation remains unresolved when a read returns 404."""
    from app.models import Attempt, Payment

    item = submit(client, headers, is_subscription=True).json()
    with sessions() as db:
        attempt = db.get(Attempt, item["attempt_id"])
        attempt.status = "unknown"
        attempt.phase = "cancel_dispatched"
        attempt.browser_state = "closed"
        attempt.error = "INVALID_RESOURCE_ID"
        db.get(Payment, item["id"]).status = "unknown"
        db.commit()
    provider["mode"] = "subscription_missing"
    result = client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert result.status_code == 502
    saved = client.get(f"/api/v1/payments/{item['id']}", headers=headers).json()
    assert (
        saved["status"] == "unknown"
        and saved["attempts"]["items"][0]["phase"] == "cancel_dispatched"
    )
    assert saved["cancellable_attempt_id"] is None
    assert not mutations(provider, "/cancel")
    assert (
        submit(
            client, {**headers, "Idempotency-Key": "cannot-replace"}, is_subscription=True
        ).status_code
        == 409
    )
