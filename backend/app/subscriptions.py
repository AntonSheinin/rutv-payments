"""Subscriptions rules for direct, single-dispatch financial requests."""

import logging
from typing import Any

import httpx
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, checkout_proof
from app.config import Settings
from app.errors import ReconciliationError
from app.models import (
    FinancialAction,
    Payment,
    Subscription,
)
from app.normalize import set_subscription_status
from app.paypal import PayPal, PayPalError, definite_rejection, failure_code, provider_id
from app.storage import action_key, audit, fingerprint, required

log = logging.getLogger("payments.subscriptions")


async def create_subscription(
    db: AsyncSession,
    payment: Payment,
    paypal: PayPal,
    settings: Settings,
    signer: URLSafeTimedSerializer,
) -> dict[str, Any]:
    """Create the product/plan/subscription sequence once for this payment."""
    product = await paypal.request(
        "POST",
        "/v1/catalogs/products",
        request_id=action_key(payment.provider_correlation, "product"),
        data={"name": "Service subscription", "type": "SERVICE"},
    )
    plan = await paypal.request(
        "POST",
        "/v1/billing/plans",
        request_id=action_key(payment.provider_correlation, "plan"),
        data={
            "product_id": provider_id(product.get("id")),
            "name": "Service billing",
            "status": "ACTIVE",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": payment.period_unit,
                        "interval_count": payment.period_count,
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": payment.payment_cycles or 0,
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": str(payment.price_per_period),
                            "currency_code": payment.currency,
                        }
                    },
                }
            ],
            "payment_preferences": {
                "auto_bill_outstanding": settings.auto_bill_outstanding,
                "payment_failure_threshold": settings.payment_failure_threshold,
            },
        },
    )
    payment.plan_id = provider_id(plan.get("id"))
    await db.commit()
    state = checkout_proof(signer, payment)
    return await paypal.request(
        "POST",
        "/v1/billing/subscriptions",
        request_id=action_key(payment.provider_correlation, "subscription"),
        data={
            "plan_id": payment.plan_id,
            "custom_id": payment.provider_correlation,
            "subscriber": {
                "name": {"given_name": payment.first_name, "surname": payment.last_name}
            },
            "application_context": {
                "shipping_preference": "NO_SHIPPING",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": settings.paypal_return_url + "?state=" + state,
                "cancel_url": settings.paypal_cancel_url + "?state=" + state,
            },
        },
    )


async def cancel_subscription(
    db: AsyncSession, identifier: str, reason: str, user: Principal, key: str, paypal: PayPal
) -> dict[str, Any]:
    """Cancel future billing once without refunds, debt settlement or replay."""
    item = await required(db, Subscription, identifier, lock=True)
    scope, digest = "cancel:" + identifier, fingerprint({"reason": reason})
    previous = await db.scalar(
        select(FinancialAction).where(
            FinancialAction.actor == user.name,
            FinancialAction.scope == scope,
            FinancialAction.submission_key == key,
        )
    )
    if previous:
        if previous.input_hash != digest:
            raise HTTPException(409, "Submission key belongs to different input")
        # A durable dispatch claim is not a verified result. Keep its reservation,
        # but expose every unfinished cancellation as Failed without replay.
        return {
            "id": item.id,
            "status": "completed" if previous.status == "completed" else "failed",
            "error": previous.error,
        }
    if item.status not in {"ACTIVE", "SUSPENDED"} or (
        await db.scalar(
            select(FinancialAction.id).where(
                FinancialAction.scope == scope,
                FinancialAction.reservation_active.is_(True),
            )
        )
    ):
        raise HTTPException(409, "Cancellation unavailable or previous cancellation unconfirmed")
    action = FinancialAction(
        actor=user.name, scope=scope, submission_key=key, input_hash=digest, resource_id=item.id
    )
    db.add(action)
    await db.commit()
    log.info("subscription_cancel_reserved subscription=%s action=%s", item.id, action.id)
    dispatched = False
    try:
        await paypal.access_token()
        action.status = "dispatched"
        await db.commit()
        dispatched = True
        await paypal.request(
            "POST",
            "/v1/billing/subscriptions/" + provider_id(item.provider_id) + "/cancel",
            data={"reason": reason},
            request_id=action.id,
        )
        action.status = "completed"
        action.reservation_active = False
        await set_subscription_status(db, item, "CANCELLED")
        audit(db, user.name, "subscription.cancelled", item.id)
        await db.commit()
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        if isinstance(error, ReconciliationError):
            log.debug(
                "subscription_cancel_verification_failed subscription=%s reason=%s", item.id, error
            )
        action.error = failure_code(error)
        action.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        action.status = "failed"
        action.reservation_active = dispatched and not definite_rejection(error)
        await db.commit()
    log.info(
        "subscription_cancel_result subscription=%s action=%s status=%s "
        "reservation_active=%s error=%s debug_id=%r",
        item.id,
        action.id,
        action.status,
        action.reservation_active,
        action.error,
        action.debug_id,
    )
    return {"id": item.id, "status": action.status, "error": action.error}
