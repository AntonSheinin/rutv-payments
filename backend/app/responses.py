"""Explicit public JSON contracts; ORM additions never expand API output implicitly."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, JsonValue, PlainSerializer

from app.schemas import Currency

Money = Annotated[Decimal, PlainSerializer(lambda value: format(value, ".2f"), return_type=str)]
CheckoutStatus = Literal["success", "failed"] | None


class PublicRecord(BaseModel):
    """Common public identity and original aware timestamps."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime


class Page[T](BaseModel):
    """One bounded page with the total from the same database snapshot."""

    items: list[T]
    total: int
    page: int
    page_size: int


class PaymentRecord(PublicRecord):
    """Selected public payment facts, including explicit nullable values."""

    first_name: str | None
    last_name: str | None
    provider_id: str | None
    plan_id: str | None
    checkout_status: CheckoutStatus
    browser_state: str | None
    error: str | None
    debug_id: str | None
    status: str
    price_per_period: Money
    currency: str
    period_unit: str | None
    period_count: int | None
    payment_cycles: int | None
    is_subscription: bool
    client_name: str | None
    agreement_number: str | None
    comments: str
    actor: str | None
    environment: str | None
    origin: str


class TransactionResponse(PublicRecord):
    """Selected public transaction facts, including explicit nullable values."""

    provider_id: str
    api_family: str
    kind: str
    status: str
    provider_status: str
    reservation_active: bool
    payment_id: str | None
    subscription_id: str | None
    original_id: str | None
    client_name: str | None
    agreement_number: str | None
    amount: Money
    currency: str
    fee: Money | None
    net: Money | None
    payer_name: str | None
    payer_email: str | None
    effective_at: datetime
    origin: str
    last_synced_at: datetime
    can_refund: bool = False
    refundable_amount: Money | None = None
    refunded_amount: Money | None = None
    refund_deadline: datetime | None = None


class SubscriptionResponse(PublicRecord):
    """Selected public subscription facts, including explicit nullable values."""

    provider_id: str
    payment_id: str | None
    client_name: str | None
    agreement_number: str | None
    status: str
    amount: Money | None
    currency: str | None
    payer_name: str | None
    payer_email: str | None
    next_billing_at: datetime | None
    outstanding: Money | None
    metadata_json: dict[str, JsonValue]
    origin: str
    last_synced_at: datetime | None
    checkout_status: CheckoutStatus = None
    charged_payments: int = 0
    remaining_payments: int | None = None
    payments_unlimited: bool = False


class CoverageResponse(PublicRecord):
    """Selected public syncstate facts, including explicit nullable values."""

    scope: str
    last_success: datetime | None
    checkpoint: dict[str, JsonValue]


class PaymentResponse(PaymentRecord):
    """Payment register projection with financial facts and action eligibility."""

    amount: Money
    collection_id: str | None
    can_close: bool
    can_refund: bool
    effective_at: datetime
    fee: Money | None = None
    net: Money | None = None
    payer_name: str | None = None
    payer_email: str | None = None
    last_synced_at: datetime | None = None
    refundable_amount: Money | None = None
    refunded_amount: Money | None = None
    refund_deadline: datetime | None = None


class PaymentDetailResponse(PaymentResponse):
    """Payment facts plus its paginated financial history."""

    transactions: Page[TransactionResponse]


class TransactionDetailResponse(TransactionResponse):
    """One financial movement and the adjustments linked to its exact identity."""

    adjustments: list[TransactionResponse]


class SubscriptionDetailResponse(SubscriptionResponse):
    """Agreement facts plus the explicitly read installment history."""

    transactions: Page[TransactionResponse]


class CheckoutTerms(BaseModel):
    """Immutable terms shown for the selected checkout, never editable output."""

    first_name: str | None
    last_name: str | None
    client_name: str | None
    agreement_number: str | None
    price_per_period: Money
    currency: Currency
    period_unit: str | None
    period_count: int | None
    payment_cycles: int | None
    is_subscription: bool
    comments: str


class CheckoutResponse(BaseModel):
    """Saved checkout result with permission to submit its first correlated outcome."""

    id: str
    provider_id: str | None
    status: str
    checkout_status: CheckoutStatus
    browser_state: str | None
    error: str | None
    debug_id: str | None
    terms: CheckoutTerms
    proof: str | None
    can_close: bool


class RefundResponse(TransactionResponse):
    """The single refund result; failure does not release an uncertain reservation."""

    error: str | None


class CancellationResponse(BaseModel):
    """Saved result of one explicit future-billing cancellation."""

    id: str
    status: Literal["completed", "failed"]
    error: str | None


class SyncResponse(BaseModel):
    """Explicit bounded provider-read result and its limited coverage."""

    processed: int
    excluded: int
    next_page: int | None
    coverage_note: str


class CurrencyTotals(BaseModel):
    """Available financial totals for one currency without assuming missing fees."""

    currency: Currency
    collected: Money
    refunds: Money
    reversals: Money
    restored: Money
    net_before_fees: Money
    net_after_fees: Money | None
    known_fees: Money
    fees_complete: bool
    completed_count: int
    pending_count: int
    failed_count: int


class TrendPoint(BaseModel):
    """One calendar bucket, currency and movement kind."""

    date: str
    currency: Currency
    kind: str
    amount: Money


class StatisticsResponse(BaseModel):
    """Local aggregates and the provider coverage behind those observations."""

    currencies: list[CurrencyTotals]
    trends: list[TrendPoint]
    current_subscriptions: dict[str, int]
    coverage: list[CoverageResponse]
    scope: str
    timezone: str
    environment: str
    filters: dict[str, JsonValue]


class HealthResponse(BaseModel):
    """Private process/readiness result without configuration or customer data."""

    status: Literal["ok", "ready"]
