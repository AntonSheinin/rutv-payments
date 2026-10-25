"""Async responsiveness, singleton probing and financial request-boundary regressions."""

import asyncio
import time

import pytest
from app import checkout, lifecycle
from app.models import FinancialAction, Payment
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from support import BODY, collection, outcome, submit


async def test_blocked_database_read_does_not_block_unrelated_requests(client, headers, sessions):
    """A real PostgreSQL lock wait must yield the event loop, not stall the process."""
    started = asyncio.Event()
    query_started = 0.0
    engine = sessions.kw["bind"]

    def before_query(connection, cursor, statement, parameters, context, executemany):
        nonlocal query_started
        if "FROM payments" in statement:
            # Bound a broken implementation's DB wait so this regression cannot hang forever.
            cursor.execute("SET LOCAL lock_timeout = '3s'")
            query_started = time.monotonic()
            started.set()

    event.listen(engine.sync_engine, "before_cursor_execute", before_query)
    async with sessions() as blocker:
        await blocker.execute(text("LOCK TABLE payments IN ACCESS EXCLUSIVE MODE"))
        blocked = asyncio.create_task(client.get("/api/v1/payments", headers=headers))
        try:
            await asyncio.wait_for(started.wait(), 3)
            for path in ("/health/live", "/api/v1/auth/me"):
                assert (
                    await asyncio.wait_for(client.get(path, headers=headers), 1)
                ).status_code == 200
            assert time.monotonic() - query_started < 1
            assert not blocked.done()
        finally:
            await blocker.rollback()
            await blocked
            event.remove(engine.sync_engine, "before_cursor_execute", before_query)
    assert blocked.result().status_code == 200


async def test_overlapping_readiness_probes_share_no_connection_transaction(
    client, headers, monkeypatch
):
    """The pinned owner is serialized while unrelated request sessions remain independent."""
    owner = client.app.state.ownership
    original = type(owner).scalar
    active = 0
    maximum = 0

    async def probe(connection, *args, **kwargs):
        nonlocal active, maximum
        if connection is not owner:
            return await original(connection, *args, **kwargs)
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0)
            return await original(connection, *args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(type(owner), "scalar", probe)
    results = await asyncio.gather(
        *(client.get("/health/ready", headers=headers) for _ in range(8))
    )
    assert all(result.status_code == 200 for result in results)
    assert maximum == 1


@pytest.mark.parametrize(
    "failure,status,sticky", [(OperationalError, 503, True), (ProgrammingError, 500, False)]
)
async def test_probe_failures_prevent_dispatch_and_programming_errors_stay_visible(
    client, headers, provider, monkeypatch, payment_logs, failure, status, sticky
):
    """Expected connection loss sticks; a programming defect is not relabelled as availability."""
    owner = client.app.state.ownership
    original = type(owner).scalar

    async def fail(connection, *args, **kwargs):
        if connection is owner:
            raise failure("PRIVATE SQL", {}, Exception("PRIVATE driver detail"))
        return await original(connection, *args, **kwargs)

    with monkeypatch.context() as changes:
        changes.setattr(type(owner), "scalar", fail)
        result = await client.post("/api/v1/payments", headers=headers, json=BODY)
    assert result.status_code == status
    assert "PRIVATE" not in result.text
    assert client.app.state.ownership_lost is sticky
    assert (await client.get("/health/ready", headers=headers)).status_code == (
        503 if sticky else 200
    )
    assert (await client.get("/api/v1/payments", headers=headers)).status_code == 200
    assert provider["requests"] == []
    assert payment_logs.text.count("database_ownership_lost") == (1 if sticky else 0)
    assert "PRIVATE" not in payment_logs.text


async def test_cancelled_capture_request_keeps_claim_and_releases_operator(
    client, headers, provider, sessions, monkeypatch
):
    """Cancellation cannot release durable mutation identity or leave the operator guard held."""
    item = (await submit(client, headers)).json()
    paypal = client.app.state.paypal
    original = paypal.request
    started = asyncio.Event()
    attempts = 0

    async def held(method, path, **kwargs):
        nonlocal attempts
        if method == "POST" and path.endswith("/capture"):
            attempts += 1
            started.set()
            await asyncio.Event().wait()
        return await original(method, path, **kwargs)

    monkeypatch.setattr(paypal, "request", held)
    running = asyncio.create_task(outcome(client, headers, item))
    await asyncio.wait_for(started.wait(), 3)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert client.app.state.checkout_requests == set()
    async with sessions() as db:
        saved = await db.get(Payment, item["id"])
        assert saved.phase == "capture_dispatched" and saved.status == "failed"
    assert (await outcome(client, headers, item)).status_code == 200
    assert attempts == 1


@pytest.mark.parametrize("operation", ["refund", "subscription_cancel"])
async def test_cancelled_financial_request_retains_reservation_and_returns_failed_without_replay(
    client, headers, provider, sessions, monkeypatch, operation
):
    """Request cancellation leaves a durable unresolved claim, never a new outbound action."""
    body = {"reason": "Operator request"}
    if operation == "refund":
        identifier = await collection(sessions)
        path = f"/api/v1/transactions/{identifier}/refunds"
        body["amount"] = "70.00"
    else:
        payment = (await submit(client, headers, is_subscription=True)).json()
        await outcome(client, headers, payment)
        sub = (await client.get("/api/v1/subscriptions", headers=headers)).json()["items"][0]
        path = f"/api/v1/subscriptions/{sub['id']}/cancel"
    started = asyncio.Event()
    attempts = 0

    async def held(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(client.app.state.paypal, "request", held)
    running = asyncio.create_task(client.post(path, headers=headers, json=body))
    await asyncio.wait_for(started.wait(), 3)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    replay = await client.post(path, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["status"] == "failed"
    assert attempts == 1
    async with sessions() as db:
        assert (await db.scalar(select(FinancialAction.reservation_active))) is True


async def test_response_failure_after_capture_preserves_collection_without_replay(
    client, headers, provider, monkeypatch
):
    """A broken output contract is a server defect, never permission to repeat collection."""
    item = (await submit(client, headers)).json()
    original = checkout.checkout_result
    broken = False

    def bad_once(payment, signer):
        nonlocal broken
        data = original(payment, signer)
        if payment.status == "completed" and not broken:
            broken = True
            data.pop("id")
        return data

    monkeypatch.setattr(checkout, "checkout_result", bad_once)
    failed = await outcome(client, headers, item)
    assert failed.status_code == 500
    saved = (await client.get("/api/v1/checkout-submissions/intent-1", headers=headers)).json()
    assert saved["status"] == "completed" and saved["checkout_status"] == "success"
    assert (await outcome(client, headers, item)).json()["status"] == "completed"
    assert (
        len(
            [
                r
                for r in provider["requests"]
                if r.method == "POST" and r.url.path.endswith("/capture")
            ]
        )
        == 1
    )
    public = (await client.get(f"/api/v1/payments/{item['id']}", headers=headers)).json()
    assert (
        not {"phase", "input_hash", "submission_key", "intent_key", "provider_correlation"}
        & public.keys()
    )
    assert isinstance(public["amount"], str)


async def test_partial_startup_and_repeated_lifespan_release_resources(application, monkeypatch):
    """Failed migrations release the original owner, and the app can start and stop again."""
    with monkeypatch.context() as changes:

        def fail(connection):
            raise RuntimeError("Migration failure")

        changes.setattr(lifecycle, "migrate_database", fail)
        with pytest.raises(RuntimeError, match="Migration failure"):
            async with application.router.lifespan_context(application):
                pass
    assert application.state.ownership.closed
    for _ in range(2):
        async with application.router.lifespan_context(application):
            owner, paypal = application.state.ownership, application.state.paypal
            assert not owner.closed and not paypal.client.is_closed
        assert owner.closed and paypal.client.is_closed
