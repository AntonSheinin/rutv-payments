import subprocess
import sys

import httpx
import pytest
from app.paypal import PayPal


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_console_levels_are_repeatable_and_library_payload_logging_stays_off(level):
    script = """
import logging
import sys
from app.logging_config import configure_logging
configure_logging(sys.argv[1])
configure_logging(sys.argv[1])
logging.getLogger('payments.test').info('activity_marker')
logging.getLogger('payments.test').debug('detail_marker')
for name in ('httpx', 'httpcore', 'sqlalchemy.engine'):
    logging.getLogger(name).debug('library_payload_marker')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, level], capture_output=True, text=True, check=True
    )
    assert result.stderr.count("activity_marker") == 1
    assert result.stderr.count("detail_marker") == (1 if level == "DEBUG" else 0)
    assert "library_payload_marker" not in result.stderr


def test_api_logs_omit_customer_data_credentials_and_queries(
    client, headers, payment_logs, provider
):
    private_values = ["PRIVATE-REFERENCE", "PRIVATE-COMMENT", "PRIVATE-KEY", "PRIVATE-PASSWORD"]
    result = client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": private_values[2]},
        json={
            "price_per_period": "50.00",
            "first_name": private_values[0],
            "last_name": "Client",
            "agreement_number": "0001",
            "intent_key": "logging-intent",
            "comments": private_values[1],
        },
    )
    assert result.status_code == 200
    assert (
        client.get("/api/v1/payments", params={"q": private_values[0]}, headers=headers).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": "PRIVATE-USERNAME", "password": private_values[3]},
        ).status_code
        == 401
    )
    assert "checkout_submitted" in payment_logs.text
    assert "http_request" in payment_logs.text
    assert "staff_login_rejected" in payment_logs.text
    for value in [*private_values, "PRIVATE-USERNAME", "test-api-key", "Bearer"]:
        assert value not in payment_logs.text


@pytest.mark.asyncio
async def test_provider_debug_logs_omit_request_response_and_exception_payloads(
    settings, payment_logs
):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "PRIVATE-TOKEN", "expires_in": 300})
        if request.method == "POST":
            return httpx.Response(201, json={"id": "ORDER1", "email": "PRIVATE-PAYER"})
        raise httpx.ReadTimeout("PRIVATE-TRANSPORT-DETAIL", request=request)

    paypal = PayPal(settings, transport=httpx.MockTransport(handler))
    try:
        await paypal.request(
            "POST",
            "/v2/checkout/orders",
            request_id="operation-1",
            data={"customer": "PRIVATE-CUSTOMER"},
            params={"note": "PRIVATE-QUERY"},
        )
        with pytest.raises(httpx.ReadTimeout):
            await paypal.request("GET", "/v2/checkout/orders/ORDER1")
    finally:
        await paypal.client.aclose()
    assert "provider_request_completed" in payment_logs.text
    assert "operation=operation-1" in payment_logs.text
    assert "provider_transport_error" in payment_logs.text
    assert "PRIVATE-" not in payment_logs.text
    assert settings.paypal_client_secret not in payment_logs.text
