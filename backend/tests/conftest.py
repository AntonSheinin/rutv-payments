import hashlib
import json
import logging
import os

import httpx
import pytest
from app.auth import password_hash
from app.config import Settings, get_settings
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://payments:local-development-only@127.0.0.1:54329/payments_test",
)


@pytest.fixture
def payment_logs(caplog):
    logger = logging.getLogger("payments")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="payments")
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


@pytest.fixture(scope="session")
def engine():
    if not (make_url(TEST_URL).database or "").startswith("payments_test"):
        raise RuntimeError("Tests require a dedicated payments_test database")
    engine = create_engine(TEST_URL, connect_args={"connect_timeout": 5})
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(engine):
    actual = MetaData()
    actual.reflect(engine)
    actual.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url=TEST_URL,
        app_secret="test-secret-with-more-than-thirty-two-characters",
        auth_users={
            "admin": {
                "password_hash": password_hash.hash("test-admin-password"),
            },
            "staff2": {
                "password_hash": password_hash.hash("test-staff2-password"),
            },
        },
        api_keys={"test": {"sha256": hashlib.sha256(b"test-api-key").hexdigest()}},
        allowed_origins=["http://testserver"],
        cookie_secure=False,
        paypal_client_id="test-client",
        paypal_client_secret="test-secret",
        paypal_merchant_id="MERCHANT",
        paypal_return_url="http://localhost:8100/paypal/success",
        paypal_cancel_url="http://localhost:8100/paypal/fail",
    )


@pytest.fixture
def application(settings, sessions, monkeypatch):
    import app.main as main

    monkeypatch.setattr("app.auth.login_attempts", {})
    monkeypatch.setattr(main, "session_factory", lambda: sessions)
    # Fixtures own the schema; migration startup is covered on a dedicated empty test DB.
    monkeypatch.setattr(main, "migrate_database", lambda engine: None)
    main.app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    yield main.app
    main.app.dependency_overrides.clear()


@pytest.fixture
def client(application, monkeypatch):
    with TestClient(application) as client:
        yield client


@pytest.fixture
def headers():
    return {"Authorization": "Bearer test-api-key", "Idempotency-Key": "create-1"}


@pytest.fixture
def provider(client):
    """Exercise the real HTTP boundary against deterministic provider responses."""
    state = {"requests": [], "orders": {}, "captured": set(), "refunds": [], "mode": "ok"}

    def handle(request):
        state["requests"].append(request)
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        if path == "/v1/catalogs/products":
            return httpx.Response(201, json={"id": "PRODUCT1"})
        if path == "/v1/billing/plans":
            state["plan"] = {**json.loads(request.content), "id": "PLAN1"}
            return httpx.Response(201, json=state["plan"])
        if path == "/v1/billing/plans/PLAN1":
            return httpx.Response(200, json=state["plan"])
        if path == "/v1/billing/subscriptions":
            state["subscription_count"] = state.get("subscription_count", 0) + 1
            state["subscription"] = {
                **json.loads(request.content),
                "id": "I-SUB" + str(state["subscription_count"]),
                "status": "ACTIVE",
                "create_time": "2026-10-19T08:00:00Z",
            }
            return httpx.Response(
                201,
                json={
                    **state["subscription"],
                    "links": [
                        {
                            "rel": "approve",
                            "href": "https://www.sandbox.paypal.com/webapps/billing/approve",
                        }
                    ],
                },
            )
        if path.startswith("/v1/billing/subscriptions/"):
            if path.endswith("/cancel"):
                if state["subscription"]["status"] not in {"ACTIVE", "SUSPENDED"}:
                    return httpx.Response(
                        422, json={"details": [{"issue": "SUBSCRIPTION_STATUS_INVALID"}]}
                    )
                if state["mode"] == "subscription_cancel_timeout":
                    raise httpx.ReadTimeout("Unconfirmed cancellation", request=request)
                state["subscription"]["status"] = "CANCELLED"
                return httpx.Response(204)
            if state["mode"] == "subscription_missing":
                return httpx.Response(404, json={"details": [{"issue": "INVALID_RESOURCE_ID"}]})
            return httpx.Response(200, json={**state["subscription"], "plan": state["plan"]})
        if path == "/v2/checkout/orders" and request.method == "POST":
            if state["mode"] == "create_timeout":
                raise httpx.ReadTimeout("uncertain creation", request=request)
            identifier = "ORDER" + str(len(state["orders"]) + 1)
            state["orders"][identifier] = json.loads(request.content)
            return httpx.Response(201, json={"id": identifier, "status": "CREATED"})
        if path.startswith("/v2/checkout/orders/"):
            identifier = path.split("/")[4]
            if request.method == "POST":
                if state["mode"] == "capture_timeout":
                    raise httpx.ReadTimeout("uncertain capture", request=request)
                if state["mode"] == "capture_decline":
                    return httpx.Response(422, json={"details": [{"issue": "INSTRUMENT_DECLINED"}]})
                state["captured"].add(identifier)
                return httpx.Response(201, json={"id": identifier, "status": "COMPLETED"})
            if identifier in state["captured"] and state["mode"] == "post_capture_read_error":
                return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
            order = json.loads(json.dumps(state["orders"][identifier]))
            order.update(
                id=identifier, status="COMPLETED" if identifier in state["captured"] else "APPROVED"
            )
            if state["mode"] == "wrong_merchant":
                order["purchase_units"][0]["payee"]["merchant_id"] = "WRONG"
            if identifier in state["captured"]:
                order["purchase_units"][0]["payments"] = {
                    "captures": [
                        {
                            "id": "CAP" + identifier,
                            "status": state.get("capture_status", "COMPLETED"),
                            "amount": order["purchase_units"][0]["amount"],
                            "create_time": "2026-10-19T08:00:00Z",
                        }
                    ]
                }
            return httpx.Response(200, json=order)
        if path.endswith("/refund"):
            state["refunds"].append(json.loads(request.content))
            if state["mode"] == "refund_timeout":
                raise httpx.ReadTimeout("uncertain refund", request=request)
            status = "PENDING" if state["mode"] == "refund_pending" else "COMPLETED"
            return httpx.Response(
                201,
                json={
                    "id": "REFUND" + str(len(state["refunds"])),
                    **(
                        {"state": status.lower()}
                        if path.startswith("/v1/payments/sale/")
                        else {"status": status}
                    ),
                    "amount": state["refunds"][-1]["amount"],
                    "create_time": "2026-10-19T08:10:00Z",
                },
            )
        raise AssertionError(f"Unexpected provider request: {request.method} {path}")

    client.app.state.paypal.client = httpx.AsyncClient(
        base_url="https://api-m.sandbox.paypal.com", transport=httpx.MockTransport(handle)
    )
    return state
