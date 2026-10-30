"""Required settings from the selected dotenv file, injected by Docker Compose."""

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal, Self, TypedDict

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    IPvAnyAddress,
    SecretStr,
    field_validator,
    model_validator,
    with_config,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL


def argon2_hash(value: str) -> str:
    """Reject malformed configured hashes before a staff login reaches password verification."""
    try:
        extract_parameters(value)
    except InvalidHashError:
        raise ValueError("Staff password hashes must use encoded Argon2 format") from None
    return value


@with_config(ConfigDict(extra="forbid"))
class StaffCredentials(TypedDict):
    """One configured staff password hash, with no unrelated credential fields."""

    password_hash: Annotated[str, AfterValidator(argon2_hash)]


@with_config(ConfigDict(extra="forbid"))
class ApiKeyCredentials(TypedDict):
    """One integration's lowercase SHA-256 token digest."""

    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def origin(value: str) -> str:
    """Validate and normalize a browser origin without paths, credentials or queries."""
    url = HttpUrl(value)
    if (
        url.username is not None
        or url.password is not None
        or url.path not in {None, "/"}
        or url.query is not None
        or url.fragment is not None
    ):
        raise ValueError("Browser origins must contain only scheme, host and optional port")
    return str(url).removesuffix("/")


def comma_separated(value: object) -> object:
    """Split environment lists before Pydantic validates each item."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",")] if value.strip() else []
    return value


class Settings(BaseSettings):
    """Validate explicit configuration; every field must be supplied by the environment."""

    # Compose selects .env.dev or .env.prod. Never implicitly read another profile.
    # The same profile also configures PostgreSQL and Uvicorn directly.
    model_config = SettingsConfigDict(extra="forbid", hide_input_in_errors=True)

    postgres_password: SecretStr = Field(min_length=1)
    app_secret: str = Field(min_length=32)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    auth_users: dict[Annotated[str, Field(min_length=1)], StaffCredentials]
    api_keys: dict[Annotated[str, Field(min_length=1)], ApiKeyCredentials]
    allowed_ips: Annotated[list[IPvAnyAddress], NoDecode, BeforeValidator(comma_separated)]
    cookie_secure: bool
    allowed_origins: Annotated[
        list[Annotated[str, AfterValidator(origin)]], NoDecode, BeforeValidator(comma_separated)
    ] = Field(min_length=1)
    forwarded_allow_ips: Annotated[list[IPvAnyAddress], NoDecode, BeforeValidator(comma_separated)]
    paypal_environment: Literal["sandbox", "live"]
    paypal_client_id: str
    paypal_client_secret: str
    paypal_merchant_id: str
    paypal_return_url: HttpUrl
    paypal_cancel_url: HttpUrl
    maximum_amount: Decimal = Field(gt=0)
    auto_bill_outstanding: bool
    payment_failure_threshold: int = Field(ge=0, le=999)

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
        if self.paypal_environment == "live":
            if self.auth_users and not self.allowed_ips:
                raise ValueError("Live staff access requires ALLOWED_IPS")
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
        """Report credential availability; callback URLs are always validated settings."""
        return bool(self.paypal_client_id and self.paypal_client_secret and self.paypal_merchant_id)


@lru_cache
def get_settings() -> Settings:
    """Load and cache the selected profile's injected environment once per process."""
    return Settings()  # type: ignore[call-arg]  # Settings loads required fields from the environment.
