"""Payments own checkout execution and immutable financial action identities."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid7

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    """Return an aware UTC timestamp for persisted service events."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """SQLAlchemy declarative registry for the service schema."""


class Record:
    """Common string identity and aware UTC creation/update timestamps."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid7()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Payment(Record, Base):
    """One payment through checkout, collection and refund, including imported payments."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("actor", "intent_key"),
        UniqueConstraint("actor", "submission_key"),
        CheckConstraint(
            "checkout_status IN ('success', 'failed')", name="ck_payment_checkout_status"
        ),
        CheckConstraint("currency IN ('ILS', 'USD', 'EUR')"),
        CheckConstraint("price_per_period >= 0"),
    )
    intent_key: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(140))
    last_name: Mapped[str | None] = mapped_column(String(140))
    submission_key: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    # Immutable provider correlation also preserves already-dispatched request keys.
    provider_correlation: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid7())
    )
    provider_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    plan_id: Mapped[str | None] = mapped_column(String(128))
    checkout_status: Mapped[str | None] = mapped_column(String(10))
    browser_state: Mapped[str | None] = mapped_column(String(20))
    phase: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(100))
    debug_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(30), default="failed")
    price_per_period: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    period_unit: Mapped[str | None] = mapped_column(String(8))
    period_count: Mapped[int | None] = mapped_column(Integer)
    payment_cycles: Mapped[int | None] = mapped_column(Integer)
    is_subscription: Mapped[bool] = mapped_column(default=False)
    client_name: Mapped[str | None] = mapped_column(String(255), index=True)
    agreement_number: Mapped[str | None] = mapped_column(String(255), index=True)
    comments: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(10))
    origin: Mapped[str] = mapped_column(String(20), default="created_here")


class Subscription(Record, Base):
    """Latest synchronized provider agreement state and available billing metadata."""

    __tablename__ = "subscriptions"
    provider_id: Mapped[str] = mapped_column(String(128), unique=True)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"))
    client_name: Mapped[str | None] = mapped_column(String(255), index=True)
    agreement_number: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    payer_name: Mapped[str | None] = mapped_column(String(300))
    payer_email: Mapped[str | None] = mapped_column(String(255))
    next_billing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outstanding: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(20), default="imported")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Transaction(Record, Base):
    """Canonical collection, refund, reversal or restoration; never stores card data."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("provider_id", "kind", "api_family"),
        CheckConstraint("currency IN ('ILS', 'USD', 'EUR')"),
        CheckConstraint("amount >= 0"),
        Index("ix_transactions_effective", "effective_at", "currency", "status"),
        Index(
            "uq_payment_collection",
            "payment_id",
            unique=True,
            postgresql_where=text("kind = 'collection' AND status <> 'failed'"),
        ),
    )
    provider_id: Mapped[str] = mapped_column(String(128))
    api_family: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    provider_status: Mapped[str] = mapped_column(String(40))
    # A failed response cannot release money that PayPal may still have refunded.
    reservation_active: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"))
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    original_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"))
    client_name: Mapped[str | None] = mapped_column(String(255), index=True)
    agreement_number: Mapped[str | None] = mapped_column(String(255), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    net: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    payer_name: Mapped[str | None] = mapped_column(String(300))
    payer_email: Mapped[str | None] = mapped_column(String(255))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    origin: Mapped[str] = mapped_column(String(20), default="created_here")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FinancialAction(Record, Base):
    """Single direct refund/cancellation identity; never a queued or retryable job."""

    __tablename__ = "financial_actions"
    __table_args__ = (UniqueConstraint("actor", "scope", "submission_key"),)
    actor: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(255))
    submission_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    # Independent of the result label: retain a dispatched claim until resolved.
    reservation_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    error: Mapped[str | None] = mapped_column(String(100))
    debug_id: Mapped[str | None] = mapped_column(String(128))


class SyncState(Record, Base):
    """Reporting coverage, import progress or database environment identity."""

    __tablename__ = "sync_state"
    scope: Mapped[str] = mapped_column(String(100), unique=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditEvent(Record, Base):
    """Operator/provider action recorded in the same transaction as its local change."""

    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(128), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
