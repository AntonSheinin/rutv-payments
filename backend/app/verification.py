"""Verification rules for direct, single-dispatch financial requests."""

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ReconciliationError
from app.models import (
    Payment,
    Subscription,
)
from app.normalize import (
    money,
    subscription,
    transaction,
)


def verify_order(payment: Payment, data: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Require exact merchant, identity, intent, currency and amount before capture."""
    units = data.get("purchase_units", [])
    if data.get("id") != payment.provider_id or data.get("intent") != "CAPTURE" or len(units) != 1:
        raise ReconciliationError("Unexpected provider order identity or intent")
    unit: dict[str, Any] = units[0]
    if (
        unit.get("custom_id") != payment.provider_correlation
        or unit.get("payee", {}).get("merchant_id") != settings.paypal_merchant_id
        or unit.get("amount", {}).get("currency_code") != payment.currency
        or money(unit.get("amount")) != payment.price_per_period
    ):
        raise ReconciliationError("Provider order does not match saved terms")
    return unit


async def ingest_order(
    db: AsyncSession, payment: Payment, data: dict[str, Any], settings: Settings
) -> str:
    """Record verified collections; approval alone is never collected money."""
    unit = verify_order(payment, data, settings)
    captures = unit.get("payments", {}).get("captures", [])
    if len(captures) > 1:
        raise ReconciliationError("Unexpected split capture")
    status = "failed"
    for capture in captures:
        if (
            money(capture.get("amount")) != payment.price_per_period
            or capture.get("amount", {}).get("currency_code") != payment.currency
        ):
            raise ReconciliationError("Capture does not match saved amount")
        row = await transaction(db, capture, "capture", payment_id=payment.id)
        row.client_name, row.agreement_number = (
            payment.client_name,
            payment.agreement_number,
        )
        status = row.status
    return status


async def verify_subscription(
    db: AsyncSession, payment: Payment, details: dict[str, Any]
) -> Subscription:
    """Verify correlation and recurring terms without using Orders capture."""
    if (
        details.get("id") != payment.provider_id
        or details.get("custom_id") != payment.provider_correlation
        or details.get("plan_id") != payment.plan_id
    ):
        raise ReconciliationError("Subscription correlation mismatch")
    # fields=plan returns the effective subscription terms, including overrides.
    # Reading only the base plan could miss quantity, tax or setup-fee changes.
    plan = details.get("plan")
    if not isinstance(plan, dict) or str(details.get("quantity", "1")) != "1":
        raise ReconciliationError("Subscription effective terms are missing or quantity changed")
    if details.get("shipping_amount") and money(details["shipping_amount"]) != 0:
        raise ReconciliationError("Unexpected subscription shipping charge")
    preferences = plan.get("payment_preferences", {})
    if preferences.get("setup_fee") and money(preferences["setup_fee"]) != 0:
        raise ReconciliationError("Unexpected subscription setup fee")
    try:
        tax = Decimal(str(plan.get("taxes", {}).get("percentage", "0")))
    except InvalidOperation:
        raise ReconciliationError("Invalid subscription tax") from None
    if not tax.is_finite() or tax != 0:
        raise ReconciliationError("Unexpected subscription tax")
    cycles = plan.get("billing_cycles", [])
    cycle = cycles[0] if len(cycles) == 1 else {}
    price = cycle.get("pricing_scheme", {}).get("fixed_price", {})
    if (
        cycle.get("tenure_type") != "REGULAR"
        or cycle.get("frequency")
        != {
            "interval_unit": payment.period_unit,
            "interval_count": payment.period_count,
        }
        or cycle.get("total_cycles") != (payment.payment_cycles or 0)
        or money(price) != payment.price_per_period
        or price.get("currency_code") != payment.currency
    ):
        raise ReconciliationError("Subscription plan terms mismatch")
    return await subscription(db, details, payment=payment)
