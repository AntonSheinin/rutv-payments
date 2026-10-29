"""Staff authentication, credential integrity and request access boundaries."""

from ipaddress import ip_address

import httpx
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


async def test_public_api_key_access_is_independent_of_staff_source_ip(
    client: httpx.AsyncClient, headers: dict[str, str], settings: Settings
) -> None:
    """Keep valid integration keys usable while denying cookies from another source IP."""
    settings.allowed_ip = ip_address("127.0.0.1")
    login = await client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert login.status_code == 200
    settings.allowed_ip = ip_address("62.90.52.94")
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
