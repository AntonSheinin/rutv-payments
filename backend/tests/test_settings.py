"""Explicit configuration requirements and documented environment coverage."""

from ipaddress import ip_address
from pathlib import Path

import pytest
from app.auth import Principal, session_response
from app.settings import Settings
from dotenv import dotenv_values
from pydantic import ValidationError


def test_every_setting_is_required(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing values, including nullable values, must never fall back to defaults."""
    values = settings.model_dump()
    for name, field in Settings.model_fields.items():
        assert field.is_required(), name
        monkeypatch.delenv(name.upper(), raising=False)
        supplied = {key: value for key, value in values.items() if key != name}
        with pytest.raises(ValidationError) as error:
            Settings(_env_file=None, **supplied)
        assert any(
            item["loc"] == (name,) and item["type"] == "missing" for item in error.value.errors()
        )


def test_example_documents_every_setting_and_only_known_infrastructure_values() -> None:
    """The committed template must cover the complete application and Compose contract."""
    path = Path(__file__).resolve().parents[2] / ".env.example"
    values = dotenv_values(path)
    application = {name.upper() for name in Settings.model_fields}
    infrastructure = {
        "FORWARDED_ALLOW_IPS",
    }
    assert set(values) == application | infrastructure
    lines = path.read_text().splitlines()
    for number, line in enumerate(lines):
        if line and not line.startswith("#"):
            assert number > 0 and lines[number - 1].startswith("#"), line.split("=", 1)[0]


def test_explicit_empty_staff_ip_is_local_only(settings: Settings) -> None:
    """An empty local value is intentional; live staff access cannot disable filtering."""
    values = settings.model_dump()
    values["allowed_ip"] = ""
    assert Settings(_env_file=None, **values).allowed_ip is None
    values.update(paypal_environment="live", cookie_secure=True)
    with pytest.raises(ValidationError, match="Live staff access requires ALLOWED_IP"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("threshold", [-1, 1000])
def test_invalid_subscription_failure_threshold_is_rejected(
    settings: Settings, threshold: int
) -> None:
    """Reject provider-invalid values before any plan creation request."""
    values = {**settings.model_dump(), "payment_failure_threshold": threshold}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("password", ["ordinary-password", "special:@/ ?%$password"])
def test_database_url_uses_only_the_password_and_fixed_compose_identity(
    settings: Settings, password: str
) -> None:
    """Preserve password characters without URL escaping or exposing them in representations."""
    configured = Settings(
        _env_file=None, **{**settings.model_dump(), "postgres_password": password}
    )
    url = configured.database_url
    assert (url.drivername, url.host, url.port, url.username, url.database) == (
        "postgresql+psycopg",
        "db",
        5432,
        "payments",
        "payments",
    )
    assert url.password == password
    assert password not in str(url)
    assert password not in repr(configured)


def test_empty_database_password_is_rejected(settings: Settings) -> None:
    """Keep password authentication required even though PostgreSQL has no published port."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**settings.model_dump(), "postgres_password": ""})


@pytest.mark.parametrize("environment", ["sandbox", "live"])
def test_environment_alone_selects_provider_and_browser_mode(
    settings: Settings, environment: str
) -> None:
    """One explicit selector controls the provider endpoint and browser session mode."""
    configured = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "paypal_environment": environment,
            "cookie_secure": True,
            "allowed_ip": ip_address("62.90.52.94"),
        },
    )
    host = "api-m.paypal.com" if environment == "live" else "api-m.sandbox.paypal.com"
    assert configured.paypal_base_url == "https://" + host
    assert session_response(Principal("staff"), configured).options.environment == environment
    assert "live_enabled" not in Settings.model_fields


def test_live_still_requires_secure_cookies(settings: Settings) -> None:
    """Removing the duplicate live flag must preserve HTTPS-only production sessions."""
    with pytest.raises(ValidationError, match="Live mode requires secure cookies"):
        Settings(
            _env_file=None,
            **{
                **settings.model_dump(),
                "paypal_environment": "live",
                "allowed_ip": ip_address("62.90.52.94"),
                "cookie_secure": False,
            },
        )
