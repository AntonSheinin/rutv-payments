import time

import httpx
import pytest
from app.errors import ReconciliationError
from app.paypal import PayPal, PayPalError
from psycopg.errors import CheckViolation, UniqueViolation
from sqlalchemy.exc import IntegrityError, OperationalError


@pytest.mark.parametrize(
    "payload", [[], {"name": "admin"}, {"name": "admin", "version": [], "csrf": "x"}]
)
def test_malformed_signed_sessions_return_401(client, application, payload):
    client.cookies.set("staff_session", application.state.session_signer.dumps(payload))
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize(
    "payload", [[], None, {"name": []}, {"details": "invalid"}, {"details": [None]}]
)
def test_provider_error_shapes_keep_provider_error_type(payload):
    with pytest.raises(PayPalError) as failure:
        PayPal.check(httpx.Response(422, json=payload))
    assert failure.value.code == "PROVIDER_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not-json", b"[]"])
async def test_invalid_successful_provider_responses_require_review(settings, content):
    paypal = PayPal(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    paypal.token, paypal.token_expiry = "cached", time.monotonic() + 300
    try:
        with pytest.raises(ReconciliationError):
            await paypal.request("GET", "/v2/checkout/orders/ORDER1")
    finally:
        await paypal.client.aclose()


@pytest.mark.parametrize(
    "original,expected", [(UniqueViolation(), 409), (CheckViolation(), 500), (None, 503)]
)
def test_database_errors_are_classified_without_exposing_payloads(
    client, sessions, headers, monkeypatch, payment_logs, original, expected
):
    error = (
        IntegrityError("PRIVATE SQL", {"comment": "PRIVATE VALUE"}, original)
        if original is not None
        else OperationalError("PRIVATE SQL", {}, Exception("PRIVATE DATABASE"))
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(sessions.class_, "scalar", fail)
    response = client.get("/api/v1/transactions", headers=headers)
    assert response.status_code == expected
    assert "PRIVATE" not in response.text
    assert "database_" in payment_logs.text
    assert "PRIVATE" not in payment_logs.text


@pytest.mark.asyncio
async def test_oauth_transport_failures_are_logged_without_credentials(settings, payment_logs):
    def fail(request):
        raise httpx.ConnectTimeout("PRIVATE exception text", request=request)

    paypal = PayPal(settings, transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(httpx.ConnectTimeout):
            await paypal.access_token()
    finally:
        await paypal.client.aclose()
    assert "oauth_transport_error" in payment_logs.text
    assert "PRIVATE" not in payment_logs.text
    assert settings.paypal_client_secret not in payment_logs.text
