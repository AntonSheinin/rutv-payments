"""Provider normalization shared by direct checkout and explicit read-only imports."""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ReconciliationError
from app.models import AuditEvent, Payment, Subscription, Transaction, now
from app.schemas import CURRENCIES

log = logging.getLogger("payments.normalize")
SUBSCRIPTION_PAYMENT_STATUSES = {
    "ACTIVE": "active",
    "SUSPENDED": "suspended",
    "CANCELLED": "cancelled",
    "EXPIRED": "cancelled",
    "APPROVAL_PENDING": "pending",
    "APPROVED": "pending",
}


def timestamp(value: object) -> datetime:
    """Require the original aware provider timestamp; never invent refund eligibility."""
    if not value:
        raise ReconciliationError("Provider timestamp is required")
    if not isinstance(value, str):
        raise ReconciliationError("Provider timestamp must be a string")
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError("Provider timestamp must include timezone")
        return result
    except ValueError:
        raise ReconciliationError("Provider timestamp is invalid") from None


def money(data: object) -> Decimal:
    """Read v1/v2 money shapes as finite decimals without float arithmetic."""
    if not isinstance(data, dict):
        raise ReconciliationError("Provider money must be an object")
    if "value" not in data and "total" not in data:
        raise ReconciliationError("Provider amount is missing")
    try:
        amount = Decimal(str(data.get("value", data.get("total"))))
    except InvalidOperation:
        raise ReconciliationError("Provider money is invalid") from None
    exponent = amount.as_tuple().exponent
    if not amount.is_finite() or not isinstance(exponent, int) or exponent < -2:
        raise ReconciliationError("Provider money must be finite")
    return amount


def normalized_status(raw: str) -> str:
    """Map provider statuses to collection-ledger states, preserving confirmed money movement."""
    status = raw.upper()
    # Refunds/reversals are separate ledger movements; they do not erase the collection.
    if status in {"COMPLETED", "REFUNDED", "PARTIALLY_REFUNDED", "REVERSED", "S"}:
        return "completed"
    if status in {"PENDING", "P"}:
        return "pending"
    return "failed"


async def update_payment_status(db: AsyncSession, payment: Payment) -> None:
    """Project verified ledger facts onto the payment without erasing its checkout state."""
    await db.flush()
    locked = await db.scalar(
        select(Payment)
        .where(Payment.id == payment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert locked is not None
    collection = await db.scalar(
        select(Transaction)
        .where(Transaction.payment_id == payment.id, Transaction.kind == "collection")
        .order_by((Transaction.status == "failed").asc(), Transaction.effective_at.desc())
    )
    if collection is None:
        return
    if collection.status == "failed" and payment.intent_key is not None:
        return
    payment.status = collection.status
    if collection.status != "completed":
        return
    totals = dict(
        (
            await db.execute(
                select(Transaction.kind, func.sum(Transaction.amount))
                .where(Transaction.original_id == collection.id, Transaction.status == "completed")
                .group_by(Transaction.kind)
            )
        ).all()
    )
    refunded = totals.get("refund", Decimal(0))
    if collection.provider_status == "REFUNDED" or refunded >= collection.amount:
        payment.status = "refunded"
    elif collection.provider_status == "PARTIALLY_REFUNDED" or refunded > 0:
        payment.status = "partially_refunded"
    elif totals.get("reversal", Decimal(0)) > totals.get("adjustment", Decimal(0)):
        payment.status = "reversed"


async def link_payment(db: AsyncSession, row: Transaction, checkout: Payment | None) -> None:
    """Give each collection one payment; adjustments belong to their original payment."""
    if row.kind != "collection":
        if row.original_id:
            original = await db.get(Transaction, row.original_id)
            if original:
                row.payment_id = original.payment_id
        return
    if checkout:
        if row.currency != checkout.currency or row.amount != checkout.price_per_period:
            raise ReconciliationError("Imported collection does not match checkout terms")
        if row.payment_id and row.payment_id != checkout.id:
            raise ReconciliationError("Imported collection belongs to another payment")
        row.payment_id = checkout.id
        checkout.status, checkout.browser_state = row.status, "closed"
    if row.payment_id:
        return
    payment = Payment(
        status=row.status,
        price_per_period=row.amount,
        currency=row.currency,
        is_subscription=row.subscription_id is not None,
        client_name=row.client_name,
        agreement_number=row.agreement_number,
        origin=row.origin,
        created_at=row.effective_at,
    )
    db.add(payment)
    await db.flush()
    row.payment_id = payment.id


async def move_collection(db: AsyncSession, row: Transaction, payment_id: str) -> None:
    """Join an imported collection and its adjustments to its exact checkout identity."""
    previous = row.payment_id
    if not previous or previous == payment_id:
        row.payment_id = payment_id
        return
    imported = await db.scalar(select(Payment).where(Payment.id == previous).with_for_update())
    if imported is None or imported.intent_key is not None or row.kind != "collection":
        raise ReconciliationError("Collection already belongs to another payment")
    row.payment_id = payment_id
    for adjustment in await db.scalars(
        select(Transaction).where(Transaction.original_id == row.id)
    ):
        adjustment.payment_id = payment_id
    await db.flush()
    await db.delete(imported)


async def transaction(
    db: AsyncSession,
    resource: dict[str, Any],
    family: str,
    kind: str = "collection",
    *,
    payment_id: str | None = None,
    subscription_id: str | None = None,
    original: Transaction | None = None,
    origin: str = "created_here",
    existing: Transaction | None = None,
) -> Transaction:
    """Upsert one canonical financial movement and flush without committing.

    Reject changed money/currency; a completed collection cannot be undone by a stale pending event.
    """
    identifier = resource.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ReconciliationError("Provider transaction has no valid identity")
    order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
    # All correlated ingestion takes payment before ledger locks.
    checkout = (
        (await db.scalar(select(Payment).where(Payment.provider_id == order_id).with_for_update()))
        if kind == "collection" and order_id
        else None
    )
    target_id = payment_id or (original.payment_id if original else None)
    if target_id and checkout is None:
        await db.scalar(select(Payment).where(Payment.id == target_id).with_for_update())
    row = (
        (
            await db.scalar(
                select(Transaction)
                .where(Transaction.id == existing.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if existing
        else (
            await db.scalar(
                select(Transaction)
                .where(
                    Transaction.provider_id == identifier,
                    Transaction.api_family == family,
                    Transaction.kind == kind,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
    )
    amount = resource.get("amount", {})
    if not isinstance(amount, dict):
        raise ReconciliationError("Provider transaction amount must be an object")
    currency = amount.get("currency_code", amount.get("currency"))
    if currency not in CURRENCIES:
        raise ReconciliationError("Unsupported provider currency")
    if money(amount) < 0:
        raise ReconciliationError("Ledger amounts must be non-negative")
    raw = resource.get("status", resource.get("state"))
    if not isinstance(raw, str):
        raise ReconciliationError("Provider status must be a string")
    status = normalized_status(raw)
    raw = raw.upper()
    if row is None:
        row = Transaction(
            provider_id=identifier,
            api_family=family,
            kind=kind,
            amount=money(amount),
            currency=currency,
            status=status,
            provider_status=raw,
            origin=origin,
        )
        db.add(row)
    elif row.amount != money(amount) or row.currency != currency:
        raise ReconciliationError("Provider transaction amount changed unexpectedly")
    row.provider_id = identifier
    # Refund evidence is monotonic: an older capture read must not restore funds
    # that PayPal already confirmed refunded, including refunds imported later.
    previous_status = row.provider_status.upper()
    if kind == "collection" and (
        previous_status in {"REFUNDED", "REVERSED"}
        or (previous_status == "PARTIALLY_REFUNDED" and raw not in {"REFUNDED", "REVERSED"})
        or (row.status == "completed" and status != "completed")
    ):
        raw = previous_status
    row.provider_status = raw
    # A stale pending notification must never undo a confirmed financial movement.
    if row.status != "completed" or status == "completed":
        row.status = status
    # Refund acceptance is separate from the displayed result. Unrecognized
    # provider results fail locally while retaining their financial reservation.
    row.reservation_active = (
        kind == "refund"
        and row.status != "completed"
        and raw not in {"DENIED", "DECLINED", "FAILED", "CANCELLED", "CANCELED", "D", "V"}
    )
    target = checkout.id if checkout else payment_id
    if target:
        await move_collection(db, row, target)
    row.subscription_id = subscription_id or row.subscription_id
    row.original_id = original.id if original else row.original_id
    effective = resource.get("create_time", resource.get("time"))
    if effective or not row.effective_at:
        row.effective_at = timestamp(effective)
    row.last_synced_at = now()
    if row.subscription_id and not row.payment_id:
        sub = await db.get(Subscription, row.subscription_id)
        if sub:
            row.client_name, row.agreement_number = sub.client_name, sub.agreement_number
    if row.payment_id:
        request = await db.get(Payment, row.payment_id)
        if request:
            row.client_name = request.client_name
            row.agreement_number = request.agreement_number
    breakdown = resource.get(
        "seller_payable_breakdown" if kind == "refund" else "seller_receivable_breakdown", {}
    )
    fee = breakdown.get("paypal_fee", resource.get("transaction_fee"))
    if kind == "refund" and family == "sale":
        fee = resource.get("refund_from_transaction_fee")
    net = breakdown.get("net_amount")
    if fee and fee.get("currency_code", fee.get("currency")) == currency:
        # Refund breakdown fees reduce the merchant's debit: store an explicit
        # credit, never estimate it from the original fee or refund proportion.
        row.fee = -money(fee) if kind == "refund" else money(fee)
    if net and net.get("currency_code") == currency:
        row.net = money(net)
    payer = resource.get("payer", {}).get("payer_info", resource.get("payer", {}))
    row.payer_email = payer.get("email_address", payer.get("email")) or row.payer_email
    name = payer.get("name", payer.get("payer_name", {}))
    row.payer_name = (
        name.get("full_name")
        or " ".join(filter(None, [name.get("given_name"), name.get("surname")]))
        or row.payer_name
    )
    await db.flush()
    await link_payment(db, row, checkout)
    await db.flush()
    if row.payment_id:
        payment = await db.get(Payment, row.payment_id)
        if payment:
            await update_payment_status(db, payment)
    log.debug(
        "transaction_reconciled transaction=%s family=%s kind=%s status=%s",
        row.id,
        family,
        kind,
        row.status,
    )
    return row


async def subscription(
    db: AsyncSession, resource: dict[str, Any], *, payment: Payment | None = None
) -> Subscription:
    """Merge current provider subscription state and stage audited status changes."""
    if (
        not isinstance(resource.get("id"), str)
        or not resource["id"]
        or not isinstance(resource.get("status"), str)
    ):
        raise ReconciliationError("Provider subscription has incomplete identity/status")
    cycles = resource.get("plan", {}).get("billing_cycles", [])
    regular: dict = next((c for c in cycles if c.get("tenure_type") == "REGULAR"), {})
    fixed = regular.get("pricing_scheme", {}).get("fixed_price")
    if not isinstance(fixed, dict):
        raise ReconciliationError("Subscription effective price is missing")
    amount, currency = money(fixed), fixed.get("currency_code")
    if amount < 0 or currency not in CURRENCIES:
        raise ReconciliationError("Unsupported subscription price or currency")
    row = await db.scalar(select(Subscription).where(Subscription.provider_id == resource["id"]))
    if row is None:
        row = Subscription(
            provider_id=resource["id"],
            status=resource["status"],
            origin="created_here" if payment else "imported",
        )
        db.add(row)
        await db.flush()
        if resource.get("create_time"):
            row.created_at = timestamp(resource["create_time"])
    previous = row.status
    if payment:
        row.payment_id = payment.id
        row.client_name, row.agreement_number = payment.client_name, payment.agreement_number
    await set_subscription_status(db, row, resource["status"])
    if previous != row.status:
        db.add(
            AuditEvent(
                actor="paypal",
                action="subscription.status",
                resource_id=row.id,
                details={"from": previous, "to": row.status},
            )
        )
    row.amount, row.currency = amount, currency
    info = resource.get("billing_info", {})
    row.next_billing_at = (
        timestamp(info["next_billing_time"]) if info.get("next_billing_time") else None
    )
    outstanding = info.get("outstanding_balance")
    row.outstanding = money(outstanding) if outstanding else None
    subscriber = resource.get("subscriber", {})
    row.payer_email = subscriber.get("email_address") or row.payer_email
    name = subscriber.get("name", {})
    row.payer_name = (
        " ".join(filter(None, [name.get("given_name"), name.get("surname")])) or row.payer_name
    )
    row.metadata_json = {
        "plan_id": resource.get("plan_id"),
        "frequency": regular.get("frequency"),
        "total_cycles": regular.get("total_cycles"),
        "cycle_executions": info.get("cycle_executions", []),
        "failed_payments_count": info.get("failed_payments_count", 0),
    }
    row.last_synced_at = now()
    await db.flush()
    log.debug("subscription_reconciled subscription=%s status=%s", row.id, row.status)
    return row


async def set_subscription_status(db: AsyncSession, item: Subscription, status: str) -> None:
    """Keep subscription activation/cancellation and its payment checkout in agreement."""
    if item.status in {"CANCELLED", "EXPIRED"}:
        status = item.status
    mapped = {
        "ACTIVE": "active",
        "SUSPENDED": "suspended",
        "CANCELLED": "cancelled",
        "EXPIRED": "cancelled",
    }.get(status)
    if item.payment_id and mapped:
        with db.no_autoflush:
            payment = await db.scalar(
                select(Payment).where(Payment.id == item.payment_id).with_for_update()
            )
        if payment and payment.provider_id == item.provider_id:
            payment.status, payment.browser_state = mapped, "closed"
            await update_payment_status(db, payment)
    item.status = status
