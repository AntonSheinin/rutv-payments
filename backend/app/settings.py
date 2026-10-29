"""Required settings from the selected dotenv file, injected by Docker Compose."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, IPvAnyAddress, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Validate explicit configuration; every field must be supplied by the environment."""

    # Compose selects .env.dev or .env.prod. Never implicitly read another profile.
    # Extra values belong to Compose/PostgreSQL/Uvicorn, sharing the same dotenv file.
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    postgres_password: SecretStr = Field(min_length=1)
    app_secret: str = Field(min_length=32)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    auth_users: dict[str, dict[str, str]]
    api_keys: dict[str, dict[str, str]]
    allowed_ip: IPvAnyAddress | None
    cookie_secure: bool
    allowed_origins: list[str] = Field(min_length=1)
    paypal_environment: Literal["sandbox", "live"]
    paypal_client_id: str
    paypal_client_secret: str
    paypal_merchant_id: str
    paypal_return_url: str = Field(min_length=1)
    paypal_cancel_url: str = Field(min_length=1)
    maximum_amount: Decimal = Field(gt=0)
    auto_bill_outstanding: bool
    payment_failure_threshold: int = Field(ge=0, le=999)

    @field_validator("allowed_ip", mode="before")
    @classmethod
    def explicit_unrestricted_staff_ip(cls, value: object) -> object:
        """An explicitly empty Sandbox value disables IP filtering; omission is invalid."""
        return None if value == "" else value

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
            if self.auth_users and self.allowed_ip is None:
                raise ValueError("Live staff access requires ALLOWED_IP")
            if not self.cookie_secure:
                raise ValueError("Live mode requires secure cookies")
            if not self.paypal_configured:
                raise ValueError("Live PayPal configuration is incomplete")
        return self

    @property
    def database_url(self) -> URL:
        """Connect to the fixed Compose database without escaping or duplicating its password."""
        return URL.create(
            "postgresql+psycopg",
            username="payments",
            password=self.postgres_password.get_secret_value(),
            host="db",
            port=5432,
            database="payments",
        )

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
    """Load and cache the selected profile's injected environment once per process."""
    return Settings()  # type: ignore[call-arg]  # Settings loads required fields from the environment.
