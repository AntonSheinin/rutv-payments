"""Application security rules and derived settings, beyond Pydantic's field validation."""

from ipaddress import ip_address

import pytest
from app.auth import Principal, session_response
from app.settings import Settings
from pydantic import ValidationError


def test_explicit_empty_staff_ip_is_local_only(settings: Settings) -> None:
    """An empty local value is intentional; live staff access cannot disable filtering."""
    values = settings.model_dump()
    values["allowed_ips"] = ""
    assert Settings(_env_file=None, **values).allowed_ips == []
    values.update(paypal_environment="live", cookie_secure=True)
    with pytest.raises(ValidationError, match="Live staff access requires ALLOWED_IPS"):
        Settings(_env_file=None, **values)


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
            "allowed_ips": [ip_address("62.90.52.94")],
        },
    )
    host = "api-m.paypal.com" if environment == "live" else "api-m.sandbox.paypal.com"
    assert configured.paypal_base_url == "https://" + host
    assert session_response(Principal("staff"), configured).options.environment == environment


def test_live_still_requires_secure_cookies(settings: Settings) -> None:
    """Removing the duplicate live flag must preserve HTTPS-only production sessions."""
    with pytest.raises(ValidationError, match="Live mode requires secure cookies"):
        Settings(
            _env_file=None,
            **{
                **settings.model_dump(),
                "paypal_environment": "live",
                "allowed_ips": [ip_address("62.90.52.94")],
                "cookie_secure": False,
            },
        )
