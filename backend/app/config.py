"""Validated environment settings and explicit production launch gates."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_secret: str = Field(min_length=32)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    auth_users: dict[str, dict[str, str]] = Field(default_factory=dict)
    api_keys: dict[str, dict[str, str]] = Field(default_factory=dict)
    cookie_secure: bool = True
    allowed_origins: list[str] = ["http://localhost:8100"]
    paypal_environment: Literal["sandbox", "live"] = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_merchant_id: str = ""
    paypal_return_url: str = "http://localhost:8100/paypal/success"
    paypal_cancel_url: str = "http://localhost:8100/paypal/fail"
    live_enabled: bool = False
    maximum_amount: Decimal = Field(default=Decimal("1000000.00"), gt=0)
    auto_bill_outstanding: bool = False
    payment_failure_threshold: int = 2
    frontend_dir: str = "frontend/dist"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Normalize environment input before Pydantic validates supported levels."""
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_security(self) -> Self:
        """Reject anonymous configuration and incomplete or insecure live deployments."""
        if not self.auth_users and not self.api_keys:
            raise ValueError("Configure AUTH_USERS or API_KEYS; anonymous access is disabled")
        for user in self.auth_users.values():
            if set(user) != {"password_hash"} or not user["password_hash"]:
                raise ValueError("AUTH_USERS entries contain only password_hash")
        for key in self.api_keys.values():
            if set(key) != {"sha256"} or len(key["sha256"]) != 64:
                raise ValueError("API_KEYS entries contain only a SHA-256 token hash")
        if self.paypal_environment == "live":
            if not self.live_enabled or not self.cookie_secure:
                raise ValueError("Live mode requires LIVE_ENABLED and secure cookies")
            if not self.paypal_configured:
                raise ValueError("Live PayPal configuration is incomplete")
        return self

    @property
    def paypal_base_url(self) -> str:
        """Select the provider API host from the validated environment."""
        return (
            "https://api-m.paypal.com"
            if self.paypal_environment == "live"
            else "https://api-m.sandbox.paypal.com"
        )

    @property
    def paypal_configured(self) -> bool:
        """Report whether credentials, merchant identity and return URLs are configured."""
        return bool(
            self.paypal_client_id
            and self.paypal_client_secret
            and self.paypal_merchant_id
            and self.paypal_return_url
            and self.paypal_cancel_url
        )


@lru_cache
def get_settings() -> Settings:
    """Load and cache environment settings once per process."""
    return Settings()  # type: ignore[call-arg]  # Settings loads required fields from the environment.
