"""Bound search queries, snapshot pagination and per-currency financial statistics."""

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal, Self
from typing import cast as type_cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Select, SQLColumnExpression, String, cast, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Attempt, Payment, Subscription, SyncState, Transaction
from app.schemas import Currency
from app.serialization import record

log = logging.getLogger("payments.reporting")


class Filters(BaseModel):
    """Validated search, local-calendar boundaries, sort order and pagination."""

    model_config = ConfigDict(extra="forbid")
    q: str = Field(default="", max_length=255)
    currency: Currency | None = None
    status: str | None = Field(default=None, max_length=40)
    checkout_status: Literal["success", "failed"] | None = None
    kind: Literal["collection", "refund", "reversal", "adjustment"] | None = None
    is_subscription: bool | None = None
    origin: Literal["created_here", "imported"] | None = None
    subscription_id: str | None = Field(default=None, max_length=128)
    payment_id: str | None = Field(default=None, max_length=128)
    start_date: date | None = None
    end_date: date | None = None
    min_amount: Decimal | None = Field(default=None, ge=0)
    max_amount: Decimal | None = Field(default=None, ge=0)
    timezone: str = "UTC"
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=25, ge=1, le=100)
    sort: Literal["date_desc", "date_asc", "amount_desc", "amount_asc"] = "date_desc"
    bucket: Literal["day", "week", "month"] = "day"

    @model_validator(mode="after")
    def ranges(self) -> Self:
        """Validate calendar, amount and timezone bounds before constructing SQL."""
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError("Unknown timezone") from None
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Invalid date range")
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("Invalid amount range")
        return self


def filtered[T: (Payment, Subscription, Transaction)](
    model: type[T], f: Filters
) -> tuple[Select[T], SQLColumnExpression[datetime], SQLColumnExpression[Decimal | None]]:
    """Build a register query and its date/amount columns for filtering and sorting."""
    statement = select(model)
    if model is Transaction:
        statement = statement.outerjoin(
            Subscription, Transaction.subscription_id == Subscription.id
        )
        statement = statement.outerjoin(Payment, Transaction.payment_id == Payment.id)
    amount: SQLColumnExpression[Decimal | None]
    date_field: SQLColumnExpression[datetime]
    fields: list[SQLColumnExpression[str | None]]
    if model is Transaction:
        reference = func.coalesce(
            Transaction.agreement_number,
            Subscription.agreement_number,
            Payment.agreement_number,
            "",
        )
        date_field, amount = Transaction.effective_at, Transaction.amount
        fields = [
            Transaction.id,
            Transaction.provider_id,
            reference,
            Transaction.client_name,
            Subscription.client_name,
            Payment.client_name,
            Transaction.payer_name,
            Transaction.payer_email,
        ]
    elif model is Subscription:
        date_field, amount = Subscription.created_at, Subscription.amount
        fields = [
            Subscription.id,
            Subscription.provider_id,
            Subscription.agreement_number,
            Subscription.client_name,
            Subscription.payer_name,
            Subscription.payer_email,
        ]
    else:
        statement = statement.outerjoin(
            Transaction,
            (Transaction.payment_id == Payment.id)
            & (Transaction.kind == "collection")
            & (Transaction.status != "failed"),
        )
        date_field, amount = (
            func.coalesce(Transaction.effective_at, Payment.created_at),
            Payment.price_per_period,
        )
        fields = [
            Payment.id,
            Payment.client_name,
            Payment.agreement_number,
            Transaction.provider_id,
            Transaction.payer_name,
            Transaction.payer_email,
        ]
    result: list[ColumnElement[bool]] = []
    if f.q:
        matches = [field.icontains(f.q, autoescape=True) for field in fields]
        if model is Payment:
            matches.append(
                select(Transaction.id)
                .where(
                    Transaction.payment_id == Payment.id,
                    Transaction.provider_id.icontains(f.q, autoescape=True),
                )
                .correlate(Payment)
                .exists()
            )
            matches.append(
                select(Attempt.id)
                .where(
                    Attempt.payment_id == Payment.id,
                    Attempt.provider_id.icontains(f.q, autoescape=True),
                )
                .exists()
            )
        result.append(or_(*matches))
    if f.checkout_status:
        if model is Payment:
            latest_outcome = (
                select(Attempt.checkout_status)
                .where(Attempt.payment_id == Payment.id)
                .order_by(Attempt.created_at.desc(), Attempt.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            result.append(latest_outcome == f.checkout_status)
        elif model is Subscription:
            result.append(
                select(Attempt.id)
                .where(
                    Attempt.provider_id == Subscription.provider_id,
                    Attempt.checkout_status == f.checkout_status,
                )
                .exists()
            )
    if f.currency:
        result.append(model.currency == f.currency)
    if f.status and hasattr(model, "status"):
        result.append(model.status == f.status)
    if f.kind and model is Transaction:
        result.append(Transaction.kind == f.kind)
    elif f.kind and model is Payment:
        if f.kind == "collection":
            result.append(Payment.status.in_(["completed", "partially_refunded"]))
        elif f.kind == "refund":
            result.append(Payment.status.in_(["refunded", "partially_refunded"]))
        else:
            result.append(
                Payment.status == "reversed" if f.kind == "reversal" else Payment.id.is_(None)
            )
    if f.origin and hasattr(model, "origin"):
        result.append(model.origin == f.origin)
    if f.min_amount is not None:
        result.append(amount >= f.min_amount)
    if f.max_amount is not None:
        result.append(amount <= f.max_amount)
    if f.is_subscription is not None:
        if model is Transaction:
            result.append(
                Transaction.subscription_id.is_not(None)
                if f.is_subscription
                else Transaction.subscription_id.is_(None)
            )
        elif model is Payment:
            result.append(Payment.is_subscription == f.is_subscription)
    if f.subscription_id and model is Transaction:
        result.append(Transaction.subscription_id == f.subscription_id)
    if f.payment_id and model in {Payment, Transaction}:
        result.append((Payment.id if model is Payment else Transaction.payment_id) == f.payment_id)
    # Convert each local midnight independently: DST days can be shorter or longer.
    zone = ZoneInfo(f.timezone)
    if f.start_date:
        result.append(date_field >= datetime.combine(f.start_date, time.min, zone).astimezone(UTC))
    if f.end_date:
        result.append(
            date_field
            < datetime.combine(f.end_date + timedelta(days=1), time.min, zone).astimezone(UTC)
        )
    return statement.where(*result), date_field, amount


def listing[T: (Payment, Subscription, Transaction)](
    db: Session, model: type[T], f: Filters
) -> dict[str, Any]:
    """Count and page the same snapshot, returning resolved client/agreement references."""
    if not db.in_transaction():
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    statement, date_field, amount = filtered(model, f)
    column = amount if f.sort.startswith("amount") else date_field
    ordering = column.asc() if f.sort.endswith("asc") else column.desc()
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    statement = (
        statement.order_by(ordering, model.id).offset((f.page - 1) * f.page_size).limit(f.page_size)
    )
    items = [record(row) for row in db.scalars(statement)]
    log.debug(
        "records_listed model=%s page=%s page_size=%s total=%s",
        model.__name__,
        f.page,
        f.page_size,
        total,
    )
    return {
        "items": items,
        "total": total,
        "page": f.page,
        "page_size": f.page_size,
    }


def statistics(db: Session, f: Filters, environment: str) -> dict[str, Any]:
    """Aggregate all matches per currency; missing fees remain explicitly unknown."""
    if not db.in_transaction():
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    statement, _, _ = filtered(Transaction, f)
    source = statement.subquery()
    rows = db.execute(
        select(
            source.c.currency,
            source.c.kind,
            source.c.status,
            func.count().label("count"),
            func.sum(source.c.amount).label("amount"),
            func.sum(source.c.fee).label("fee"),
            func.count(source.c.fee).label("fee_count"),
        ).group_by(source.c.currency, source.c.kind, source.c.status)
    ).all()
    result: dict[str, Any] = {}
    # Aggregation columns come from a dynamic subquery; declare its selected row layout.
    grouped = type_cast(Sequence[tuple[str, str, str, int, Decimal, Decimal | None, int]], rows)
    for currency, kind, status, count, amount, fee, fee_count in grouped:
        totals = result.setdefault(
            currency,
            {
                "currency": currency,
                "collected": Decimal(0),
                "refunds": Decimal(0),
                "reversals": Decimal(0),
                "restored": Decimal(0),
                "fees": Decimal(0),
                "completed_count": 0,
                "pending_count": 0,
                "failed_count": 0,
                "fees_complete": True,
            },
        )
        if status == "completed":
            key = {
                "collection": "collected",
                "refund": "refunds",
                "reversal": "reversals",
                "adjustment": "restored",
            }.get(kind)
            if key:
                totals[key] += amount
            totals["completed_count"] += count if kind == "collection" else 0
            totals["fees"] += fee or 0
            totals["fees_complete"] &= fee_count == count
        elif status == "pending":
            totals["pending_count"] += count
        elif status == "failed":
            totals["failed_count"] += count
    for totals in result.values():
        totals["net_before_fees"] = (
            totals["collected"] - totals["refunds"] - totals["reversals"] + totals["restored"]
        )
        # Missing fee data is unknown, not zero; avoid claiming a complete net amount.
        totals["net_after_fees"] = (
            totals["net_before_fees"] - totals["fees"] if totals["fees_complete"] else None
        )
        totals["known_fees"] = totals.pop("fees")
    bucket = func.date_trunc(f.bucket, func.timezone(f.timezone, source.c.effective_at))
    trends = db.execute(
        select(cast(bucket, String), source.c.currency, source.c.kind, func.sum(source.c.amount))
        .where(source.c.status == "completed")
        .group_by(bucket, source.c.currency, source.c.kind)
        .order_by(bucket)
    ).all()
    # Current subscription counts are independent of transaction date/status filters.
    sub_query = select(Subscription.status, func.count()).group_by(Subscription.status)
    if f.currency:
        sub_query = sub_query.where(Subscription.currency == f.currency)
    if f.origin:
        sub_query = sub_query.where(Subscription.origin == f.origin)
    if f.q:
        sub_query = sub_query.where(
            or_(
                Subscription.agreement_number.icontains(f.q, autoescape=True),
                Subscription.client_name.icontains(f.q, autoescape=True),
                Subscription.payer_email.icontains(f.q, autoescape=True),
            )
        )
    log.debug("statistics_aggregated currencies=%s trend_points=%s", len(result), len(trends))
    return {
        "currencies": list(result.values()),
        "trends": [
            {"date": d, "currency": c, "kind": k, "amount": str(a)} for d, c, k, a in trends
        ],
        "current_subscriptions": dict(db.execute(sub_query).all()),
        "coverage": [
            record(s)
            for s in db.scalars(select(SyncState).where(SyncState.scope != "database_identity"))
        ],
        "scope": "Records created or imported into this service; coverage may be incomplete",
        "timezone": f.timezone,
        "environment": environment,
        "filters": f.model_dump(mode="json"),
    }


def subscription_progress(db: Session, items: list[dict[str, Any]]) -> None:
    """Add recorded collections and provider-reported future regular installments."""
    if not items:
        return
    counts = {
        identifier: count
        for identifier, count in db.execute(
            select(Transaction.subscription_id, func.count())
            .where(
                Transaction.subscription_id.in_([item["id"] for item in items]),
                Transaction.kind == "collection",
                Transaction.status == "completed",
            )
            .group_by(Transaction.subscription_id)
        ).all()
    }
    outcomes = dict(
        db.execute(
            select(Attempt.provider_id, Attempt.checkout_status).where(
                Attempt.provider_id.in_([item["provider_id"] for item in items])
            )
        ).all()
    )
    for item in items:
        item["checkout_status"] = outcomes.get(item["provider_id"])
        metadata = item["metadata_json"]
        executions = [
            cycle
            for cycle in metadata.get("cycle_executions", [])
            if cycle.get("tenure_type") == "REGULAR"
        ]
        # Completed cycles are not proof of collection; count verified ledger
        # collections instead, without claiming the imported history is complete.
        remaining = executions[0].get("cycles_remaining") if len(executions) == 1 else None
        if type(remaining) is not int or remaining < 0:
            remaining = None
        unlimited = metadata.get("total_cycles") == 0
        if item["status"] in {"CANCELLED", "EXPIRED"}:
            remaining, unlimited = 0, False
        item.update(
            charged_payments=counts.get(item["id"], 0),
            remaining_payments=None if unlimited else remaining,
            payments_unlimited=unlimited,
        )
