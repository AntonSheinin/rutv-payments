"""Staff authentication, credential integrity and request access boundaries."""

from ipaddress import ip_address

import httpx
import pytest
from app.auth import password_hash
from app.settings import Settings


async def test_simple_staff_login_csrf_cookie_and_same_access(client, settings):
    for name, password in [("admin", "test-admin-password"), ("staff2", "test-staff2-password")]:
        result = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": name, "password": password},
        )
        assert result.status_code == 200 and set(result.json()) == {"name", "csrf", "options"}
        assert settings.paypal_client_secret not in result.text
        assert settings.app_secret not in result.text
        assert "samesite=lax" in result.headers["set-cookie"].lower()
        assert (await client.get("/api/v1/auth/me")).json() == result.json()
        assert (await client.post("/api/v1/auth/logout")).status_code == 403
        assert (
            await client.post(
                "/api/v1/auth/logout",
                headers={"Origin": "http://testserver", "X-CSRF-Token": result.json()["csrf"]},
            )
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_password_whitespace_is_preserved(client, settings):
    """Only the exact configured password authenticates, including its whitespace."""
    settings.auth_users["admin"]["password_hash"] = password_hash.hash(" password with spaces ")
    for password, expected in [("password with spaces", 401), (" password with spaces ", 200)]:
        response = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": " admin ", "password": password},
        )
        assert response.status_code == expected


async def test_private_system_endpoints_require_authentication(client, headers):
    for path in ("/health/live", "/health/ready", "/api/v1/openapi.json"):
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers=headers)).status_code == 200


@pytest.mark.parametrize(
    ("source", "expected"),
    [("62.90.52.94", 200), ("203.0.113.10", 200), ("2001:db8::1", 200), ("203.0.113.11", 403)],
)
async def test_staff_login_accepts_only_configured_addresses(
    client: httpx.AsyncClient, settings: Settings, source: str, expected: int
) -> None:
    """Every listed IPv4/IPv6 source can log in; a spoofed header cannot grant access."""
    configured = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "allowed_ips": "62.90.52.94, 203.0.113.10, 2001:db8::1",
        },
    )
    settings.allowed_ips = configured.allowed_ips
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app, client=(source, 12345)),
        base_url="http://testserver",
    ) as browser:
        response = await browser.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver", "X-Forwarded-For": "62.90.52.94"},
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert response.status_code == expected


@pytest.mark.parametrize("source", ["127.0.0.1", "::1", "172.18.0.1", "203.0.113.10"])
async def test_internal_readiness_requires_container_loopback(client, settings, source):
    """Container-local checks need no integration key; Docker/proxy peers cannot enter."""
    settings.allowed_ips = [ip_address("62.90.52.94")]
    settings.api_keys = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app, client=(source, 12345)),
        base_url="http://testserver",
    ) as probe:
        result = await probe.get("/internal/ready")
        assert result.status_code == (200 if source in {"127.0.0.1", "::1"} else 403)
        assert (await probe.get("/health/ready")).status_code == 403


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded"])
async def test_internal_readiness_rejects_forwarded_loopback(client, header):
    """Even a proxy-restored loopback address cannot authenticate internal readiness."""
    assert (await client.get("/internal/ready", headers={header: "127.0.0.1"})).status_code == 403


async def test_public_api_key_access_is_independent_of_staff_source_ip(
    client: httpx.AsyncClient, headers: dict[str, str], settings: Settings
) -> None:
    """Keep valid integration keys usable while denying cookies from another source IP."""
    settings.allowed_ips = [ip_address("127.0.0.1")]
    login = await client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert login.status_code == 200
    settings.allowed_ips = [ip_address("62.90.52.94")]
    assert (await client.get("/api/v1/auth/me")).status_code == 403
    assert (
        await client.get("/api/v1/auth/me", headers={"X-Forwarded-For": "62.90.52.94"})
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf"]},
        )
    ).status_code == 403
    assert (await client.get("/api/v1/payments", headers=headers)).status_code == 200
    assert (
        await client.get("/api/v1/payments", headers={"Authorization": "Bearer invalid"})
    ).status_code == 401
