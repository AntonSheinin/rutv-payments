"""Checkout behavior with dedicated PostgreSQL and mocked provider HTTP."""

from decimal import Decimal

import pytest
from app.models import Payment, Transaction
from sqlalchemy import func, select
from support import mutations, outcome, submit


async def test_oauth_rejection_finishes_checkout_once_without_dispatch(
    client, headers, provider, sessions, monkeypatch, payment_logs
):
    """The shared creation error boundary retains identity even before provider dispatch."""
    from app.paypal import PayPalError

    async def reject():
        raise PayPalError(401, "INVALID_CLIENT", "oauth-debug-id")

    monkeypatch.setattr(client.app.state.paypal, "access_token", reject)
    result = await submit(client, headers)
    assert result.status_code == 200
    assert result.json()["checkout_status"] == "failed"
    assert result.json()["debug_id"] == "oauth-debug-id"
    assert (await submit(client, headers)).json()["id"] == result.json()["id"]
    assert provider["requests"] == []
    assert payment_logs.text.count("checkout_finished") == 1
    async with sessions() as db:
        assert (await db.get(Payment, result.json()["id"])).phase == "reserved"


async def test_close_checkout_allows_new_submit_and_blocks_late_approval(client, headers, provider):
    item = (await submit(client, headers)).json()
    assert item["can_close"]
    listing = (await client.get("/api/v1/payments", headers=headers)).json()["items"]
    assert listing[0]["can_close"]
    detail = (await client.get("/api/v1/payments/" + item["id"], headers=headers)).json()
    assert detail["can_close"]
    path = f"/api/v1/payments/{item['id']}/close"
    count = len(provider["requests"])
    result = await client.post(path, headers=headers)
    assert result.status_code == 200
    assert result.json()["status"] == "pending" and result.json()["checkout_status"] == "failed"
    assert not result.json()["can_close"]
    assert (await client.post(path, headers=headers)).json()["status"] == "pending"
    assert len(provider["requests"]) == count
    assert (await outcome(client, headers, item)).json()["status"] == "pending"
    assert not mutations(provider, "/capture")
    new = (
        await submit(
            client, {**headers, "Idempotency-Key": "operator-restart"}, intent_key="fresh-payment"
        )
    ).json()
    assert new["id"] != item["id"]
    assert (await client.post(path, headers=headers)).json()["status"] == "pending"
    assert (await client.get("/api/v1/checkout-submissions/fresh-payment", headers=headers)).json()[
        "status"
    ] == "pending"


async def test_submit_capture_duplicate_callback_and_reload_never_repeat(
    client, headers, provider, sessions
):
    response = await submit(client, headers)
    assert response.status_code == 200
    item = response.json()
    assert item["status"] == "pending"
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
    assert (await submit(client, headers)).json()["id"] == item["id"]
    result = await outcome(client, headers, item)
    assert result.status_code == 200 and result.json()["status"] == "completed"
    assert (await outcome(client, headers, item)).json()["status"] == "completed"
    for _ in range(2):
        assert (await client.get("/api/v1/checkout-submissions/intent-1", headers=headers)).json()[
            "status"
        ] == "completed"
    assert len(mutations(provider, "/orders")) == len(mutations(provider, "/capture")) == 1
    assert (await submit(client, {**headers, "Idempotency-Key": "new-key"})).status_code == 409
    async with sessions() as db:
        row = await db.scalar(select(Transaction).where(Transaction.kind == "collection"))
        assert row.client_name == "Test client" and row.agreement_number == "000123-A"
        assert row.amount == Decimal("50.00")


@pytest.mark.parametrize(
    "mode", ["create_timeout", "capture_timeout", "wrong_merchant", "post_capture_read_error"]
)
async def test_uncertain_or_mismatched_attempt_stays_reserved_without_replay(
    client, headers, provider, mode
):
    provider["mode"] = mode
    item = (await submit(client, headers)).json()
    if mode != "create_timeout":
        item = (await outcome(client, headers, item)).json()
    assert item["status"] == "failed" and item["checkout_status"] == "failed"
    before = len(provider["requests"])
    closed = await client.post(f"/api/v1/payments/{item['id']}/close", headers=headers)
    assert closed.json()["status"] == item["status"]
    assert closed.json()["checkout_status"] == item["checkout_status"]
    assert (await submit(client, headers)).status_code == 200
    assert (await submit(client, {**headers, "Idempotency-Key": "replacement"})).status_code == 409
    assert (await client.get("/api/v1/checkout-submissions/intent-1", headers=headers)).json()[
        "status"
    ] == "failed"
    assert len(provider["requests"]) == before
    if mode == "wrong_merchant":
        assert not mutations(provider, "/capture")


@pytest.mark.parametrize("changes", [{"proof": "forged"}, {"provider_id": "WRONG"}])
async def test_callback_correlation_rejects_forgery(client, headers, provider, changes):
    item = (await submit(client, headers)).json()
    assert (await outcome(client, headers, item, **changes)).status_code in {400, 403, 404, 409}
    assert not mutations(provider, "/capture")


@pytest.mark.parametrize(
    "field,value",
    [
        ("custom_id", "WRONG-PAYMENT"),
        ("value", "51.00"),
        ("currency_code", "USD"),
        ("value", "NaN"),
    ],
)
async def test_changed_provider_order_terms_cannot_dispatch_capture(
    client, headers, provider, field, value
):
    """Provider money/correlation changes cannot turn an SDK approval into a charge."""
    item = (await submit(client, headers)).json()
    unit = provider["orders"][item["provider_id"]]["purchase_units"][0]
    if field == "custom_id":
        unit[field] = value
    else:
        unit["amount"][field] = value
    result = await outcome(client, headers, item)
    assert result.json()["status"] == result.json()["checkout_status"] == "failed"
    count = len(provider["requests"])
    assert (await outcome(client, headers, item)).json()["checkout_status"] == "failed"
    assert len(provider["requests"]) == count and not mutations(provider, "/capture")
    assert (await client.get("/api/v1/transactions", headers=headers)).json()["total"] == 0


async def test_signed_checkout_proof_cannot_complete_another_payment(client, headers, provider):
    """Removing the child identifier must not weaken exact payment correlation."""
    original = (await submit(client, headers)).json()
    current = (
        await submit(
            client,
            {**headers, "Idempotency-Key": "different-payment"},
            intent_key="different-payment",
        )
    ).json()
    response = await outcome(client, headers, current, proof=original["proof"])
    assert response.status_code == 403
    assert not mutations(provider, "/capture")
    assert (await client.get(f"/api/v1/payments/{current['id']}", headers=headers)).json()[
        "can_close"
    ]


async def test_submission_key_binds_the_original_input(client, headers, provider):
    assert (await submit(client, headers)).status_code == 200
    assert (await submit(client, headers, price_per_period="51.00")).status_code == 409
    assert len(mutations(provider, "/orders")) == 1


@pytest.mark.parametrize("status", ["DECLINED", "FAILED"])
async def test_refresh_failed_capture_allows_only_explicit_new_attempt(
    client, headers, provider, status
):
    provider["capture_status"] = "PENDING"
    item = (await submit(client, headers)).json()
    assert (await outcome(client, headers, item)).json()["status"] == "pending"
    provider["capture_status"] = status
    response = await client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert response.status_code == 200
    saved = (await client.get("/api/v1/checkout-submissions/intent-1", headers=headers)).json()
    assert saved["status"] == "failed" and saved["checkout_status"] == "success"
    requests = (await client.get("/api/v1/payments", headers=headers)).json()["items"]
    assert requests[0]["status"] == "failed"
    assert len(mutations(provider, "/orders")) == len(mutations(provider, "/capture")) == 1
    replacement = (
        await submit(
            client,
            {**headers, "Idempotency-Key": "operator-new-attempt"},
            intent_key="fresh-after-provider-decline",
        )
    ).json()
    assert replacement["id"] != item["id"]
    assert len(mutations(provider, "/capture")) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "GBP"},
        {"price_per_period": 5.0},
        {"price_per_period": "0.00"},
        {"price_per_period": "1.001"},
        {"price_per_period": "NaN"},
        {"price_per_period": "Infinity"},
        {"first_name": "  "},
        {"first_name": "A" * 140, "last_name": "B" * 140},
        {"agreement_number": ""},
        {"payment_cycles": 3},
        {"external_reference": "old-contract"},
    ],
)
async def test_invalid_inputs_never_create_a_checkout(client, headers, provider, changes):
    assert (await submit(client, headers, **changes)).status_code == 422
    assert not mutations(provider, "/orders")


async def test_database_failure_after_capture_keeps_claim_and_sync_is_read_only(
    client, headers, provider, sessions, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    item = (await submit(client, headers)).json()
    commit = sessions.class_.commit
    failed = False

    def interrupt(db):
        nonlocal failed
        if provider["captured"] and not failed:
            failed = True
            raise OperationalError("PRIVATE SQL", {}, Exception("PRIVATE persistence failure"))
        return commit(db)

    monkeypatch.setattr(sessions.class_, "commit", interrupt)
    response = await outcome(client, headers, item)
    assert response.status_code == 503 and "PRIVATE" not in response.text
    assert (await outcome(client, headers, item)).json()["status"] == "failed"
    assert (await submit(client, {**headers, "Idempotency-Key": "new"})).status_code == 409
    response = await client.post(
        "/api/v1/synchronizations",
        headers=headers,
        json={"scope": "payment", "resource_id": item["id"]},
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/checkout-submissions/intent-1", headers=headers)).json()[
        "status"
    ] == "completed"
    assert len(mutations(provider, "/capture")) == 1


async def test_used_identity_cannot_be_reused_even_if_summary_is_wrong(
    sessions, client, headers, provider
):
    item = (await submit(client, headers)).json()
    async with sessions() as db:
        (await db.get(Payment, item["id"])).status = "failed"
        await db.commit()
    calls = len(provider["requests"])
    response = await submit(client, {**headers, "Idempotency-Key": "explicit-second-submit"})
    assert response.status_code == 409
    assert len(provider["requests"]) == calls
    async with sessions() as db:
        assert (await db.scalar(select(func.count()).select_from(Payment))) == 1
