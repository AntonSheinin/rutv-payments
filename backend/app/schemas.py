"""Strict API inputs for immutable payment terms and operator actions."""

from datetime import date
from decimal import Decimal
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Currency = Literal["ILS", "USD", "EUR"]
CURRENCIES = ("ILS", "USD", "EUR")
PERIOD_LIMITS = {"DAY": 365, "WEEK": 52, "MONTH": 12, "YEAR": 1}


class Input(BaseModel):
    """Reject unknown input fields and trim operator-entered text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaymentInput(Input):
    """Immutable final-charge terms shared by manual and API payment creation."""

    price_per_period: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    period_unit: Literal["DAY", "WEEK", "MONTH", "YEAR"] = "MONTH"
    period_count: int = Field(default=1, ge=1, le=36500)
    currency: Currency = "ILS"
    first_name: str = Field(min_length=1, max_length=140)
    last_name: str = Field(min_length=1, max_length=140)
    agreement_number: str = Field(min_length=1, max_length=255)
    comments: str = Field(default="", max_length=4000)
    payment_cycles: int | None = Field(default=None, ge=1, le=999)
    is_subscription: bool = False

    @field_validator("price_per_period", mode="before")
    @classmethod
    def money_string(cls, value: object) -> str:
        """Require decimal text so JSON floating-point rounding cannot affect money."""
        if not isinstance(value, str):
            raise ValueError("Money must be supplied as a decimal string")
        return value

    @model_validator(mode="after")
    def billing(self) -> Self:
        """Normalize subscription duration and enforce provider interval limits."""
        if len(f"{self.first_name} {self.last_name}") > 255:
            raise ValueError("Combined client name must not exceed 255 characters")
        if self.is_subscription:
            # Omission means one cycle; an explicit null means renew until canceled.
            if "payment_cycles" not in self.model_fields_set:
                self.payment_cycles = 1
            if self.period_count > PERIOD_LIMITS[self.period_unit]:
                raise ValueError("Subscription interval exceeds PayPal's supported range")
        elif self.payment_cycles is not None:
            raise ValueError("Payment cycles applies only to subscriptions")
        return self


class LoginInput(Input):
    """Staff credentials accepted only by the login endpoint."""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class ReasonInput(Input):
    """Bounded reason required for a provider-visible cancellation or refund."""

    reason: str = Field(min_length=1, max_length=127)


class RefundInput(ReasonInput):
    """Exact partial/full refund amount against an existing collection."""

    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)

    @field_validator("amount", mode="before")
    @classmethod
    def money_string(cls, value: object) -> str:
        """Require decimal text so JSON floating-point rounding cannot affect money."""
        return PaymentInput.money_string(value)


class AssociationInput(Input):
    """Local client/agreement association, independent of provider billing terms."""

    client_name: str = Field(min_length=1, max_length=255)
    agreement_number: str = Field(min_length=1, max_length=255)


class SyncInput(Input):
    """Read-only provider synchronization scope and its required target."""

    page: int = Field(default=1, ge=1, le=100000)
    scope: Literal["subscriptions", "history", "subscription", "payment", "transaction"]
    resource_id: str | None = Field(default=None, max_length=128)
    start_date: date | None = None
    end_date: date | None = None
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        """Require a known timezone for interpreting operator calendar dates."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError("Unknown timezone") from None
        return value

    @model_validator(mode="after")
    def valid_target(self) -> Self:
        """Require the resource identifier or calendar range needed by the selected sync."""
        if self.scope in {"subscription", "payment", "transaction"} and not self.resource_id:
            raise ValueError("A resource ID is required")
        if self.scope == "history":
            if not self.start_date or not self.end_date or self.start_date > self.end_date:
                raise ValueError("History sync requires a valid date range")
        return self


class SubmitInput(PaymentInput):
    """Explicit intent identity survives a lost initial submission response."""

    intent_key: str = Field(min_length=1, max_length=128)


class OutcomeInput(Input):
    """Browser observations are correlation context, never financial evidence."""

    attempt_id: str = Field(min_length=1, max_length=36)
    proof: str = Field(min_length=1, max_length=1000)
    outcome: Literal["approved", "cancelled", "error"]
    provider_id: str | None = Field(default=None, max_length=128)
