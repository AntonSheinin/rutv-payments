"""Balance-only reads preserve the last verified account snapshot on failures."""

import copy

import httpx
import pytest

BALANCE = {
    "account_id": "MERCHANT",
    "last_refresh_time": "2026-10-25T08:00:00Z",
    "balances": [
        {"currency": "ILS", "total_balance": {"currency_code": "ILS", "value": "123.45"}},
        {"currency": "USD", "total_balance": {"currency_code": "USD", "value": "0.00"}},
        {"currency": "GBP", "total_balance": {"currency_code": "GBP", "value": "9.00"}},
    ],
}


@pytest.fixture
async def balance_provider(client):
    """Use the real OAuth/HTTP boundary while rejecting every non-balance request."""
    state = {"body": copy.deepcopy(BALANCE), "status": 200, "reads": 0}

    def handle(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        assert request.method == "GET"
        assert request.url.path == "/v1/reporting/balances"
        assert not request.url.query
        state["reads"] += 1
        return httpx.Response(state["status"], json=state["body"])

    await client.app.state.paypal.client.aclose()
    client.app.state.paypal.client = httpx.AsyncClient(
        base_url="https://api-m.sandbox.paypal.com", transport=httpx.MockTransport(handle)
    )
    return state


async def test_balance_is_explicit_and_independent_of_reporting_filters(
    client, headers, balance_provider
):
    """Local reads never contact PayPal and balance data does not become history coverage."""
    initial = await client.get("/api/v1/statistics", headers=headers)
    assert initial.json()["balance"] is None
    assert balance_provider["reads"] == 0
    response = await client.post("/api/v1/balance/synchronize", headers=headers)
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["balances"] == [
        {"currency": "ILS", "total": "123.45"},
        {"currency": "USD", "total": "0.00"},
    ]
    result = (
        await client.get(
            "/api/v1/statistics?q=absent&currency=EUR&start_date=2020-01-01&end_date=2020-01-02",
            headers=headers,
        )
    ).json()
    assert result["balance"] == snapshot
    assert result["currencies"] == []
    assert result["coverage"] == []
    assert balance_provider["reads"] == 1


@pytest.mark.parametrize("failure", ["merchant", "duplicate", "money", "timestamp", "403"])
async def test_failed_balance_read_preserves_snapshot(client, headers, balance_provider, failure):
    """Invalid facts and denied permission cannot replace the last success with zero."""
    saved = (await client.post("/api/v1/balance/synchronize", headers=headers)).json()
    body = balance_provider["body"]
    if failure == "merchant":
        body["account_id"] = "OTHER"
    elif failure == "duplicate":
        body["balances"].append(copy.deepcopy(body["balances"][0]))
    elif failure == "money":
        body["balances"][0]["total_balance"]["value"] = "NaN"
    elif failure == "timestamp":
        body["last_refresh_time"] = "2026-10-25"
    else:
        balance_provider.update(status=403, body={"name": "NOT_AUTHORIZED"})
    response = await client.post("/api/v1/balance/synchronize", headers=headers)
    assert response.status_code >= 400
    assert (await client.get("/api/v1/statistics", headers=headers)).json()["balance"] == saved


async def test_older_balance_cannot_replace_newer_snapshot(client, headers, balance_provider):
    """A slower tab returning older provider data must retain the newer snapshot."""
    saved = (await client.post("/api/v1/balance/synchronize", headers=headers)).json()
    balance_provider["body"]["last_refresh_time"] = "2026-10-24T08:00:00Z"
    balance_provider["body"]["balances"][0]["total_balance"]["value"] = "1.00"
    result = await client.post("/api/v1/balance/synchronize", headers=headers)
    assert result.json() == saved


async def test_balance_requires_authentication_and_browser_csrf(client, balance_provider):
    """The dedicated action retains the standard private mutation boundary."""
    assert (await client.post("/api/v1/balance/synchronize")).status_code == 401
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
        headers={"Origin": "http://testserver"},
    )
    assert login.status_code == 200
    assert (await client.post("/api/v1/balance/synchronize")).status_code == 403
    assert balance_provider["reads"] == 0
