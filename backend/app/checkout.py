"""Checkout rules for direct, single-dispatch financial requests."""

import logging
from typing import Any

import httpx
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, checkout_proof
from app.config import Settings
from app.errors import ReconciliationError
from app.models import Payment
from app.normalize import (
    SUBSCRIPTION_PAYMENT_STATUSES,
    update_payment_status,
)
from app.paypal import PayPal, PayPalError, failure_code, provider_id
from app.schemas import OutcomeInput, SubmitInput
from app.storage import action_key, audit, fingerprint, required
from app.subscriptions import create_subscription
from app.verification import ingest_order, verify_order, verify_subscription

log = logging.getLogger("payments.checkout")


def checkout_result(payment: Payment, signer: URLSafeTimedSerializer) -> dict[str, Any]:
    """Return immutable payment terms and checkout state without a replay action."""
    return {
        "id": payment.id,
        "provider_id": payment.provider_id,
        "status": payment.status,
        "checkout_status": payment.checkout_status,
        "browser_state": payment.browser_state,
        "error": payment.error,
        "debug_id": payment.debug_id,
        "terms": {
            "first_name": payment.first_name,
            "last_name": payment.last_name,
            "client_name": payment.client_name,
            "agreement_number": payment.agreement_number,
            "price_per_period": format(payment.price_per_period, ".2f"),
            "currency": payment.currency,
            "period_unit": payment.period_unit,
            "period_count": payment.period_count,
            "payment_cycles": payment.payment_cycles,
            "is_subscription": payment.is_subscription,
            "comments": payment.comments,
        },
        "proof": checkout_proof(signer, payment)
        if payment.phase == "created"
        and payment.browser_state == "open"
        and payment.checkout_status is None
        else None,
        "can_close": payment.checkout_status is None and payment.browser_state == "open",
    }


async def set_payment_status(db: AsyncSession, payment: Payment, status: str) -> None:
    """Persist the payment result without undoing verified financial evidence."""
    # An explicit read may confirm collection while the original request is still
    # waiting on HTTP. Its later timeout/error must not undo that verified result.
    with db.no_autoflush:
        persisted = await db.scalar(
            select(Payment.status).where(Payment.id == payment.id).with_for_update()
        )
    if persisted == "completed" and status != "completed":
        await db.rollback()
        await db.refresh(payment)
        return
    payment.status = status
    await update_payment_status(db, payment)
    await db.commit()
    if persisted != payment.status:
        log.debug(
            "payment_status_changed payment=%s previous=%s status=%s",
            payment.id,
            persisted,
            payment.status,
        )


async def finish_checkout(db: AsyncSession, payment: Payment, outcome: str) -> None:
    """Freeze an operator checkout result; later financial reads cannot promote it."""
    with db.no_autoflush:
        saved = await db.scalar(
            select(Payment.checkout_status).where(Payment.id == payment.id).with_for_update()
        )
    if saved is None:
        payment.checkout_status = outcome
        await db.commit()
        log.info(
            "checkout_finished payment=%s checkout_status=%s status=%s "
            "phase=%s error=%s debug_id=%r",
            payment.id,
            outcome,
            payment.status,
            payment.phase,
            payment.error,
            payment.debug_id,
        )
    else:
        await db.refresh(payment)


async def close_checkout(
    db: AsyncSession, identifier: str, signer: URLSafeTimedSerializer
) -> dict[str, Any]:
    """End an unsuccessful local checkout without cancelling or retrying at PayPal."""
    payment = await required(db, Payment, identifier, lock=True)
    if payment.checkout_status is None:
        payment.browser_state = "closed"
        await finish_checkout(db, payment, "failed")
    return checkout_result(payment, signer)


async def submit(
    db: AsyncSession,
    data: SubmitInput,
    user: Principal,
    key: str,
    paypal: PayPal,
    settings: Settings,
    signer: URLSafeTimedSerializer,
) -> dict[str, Any]:
    """Create one new payment for explicit Submit; duplicates never contact PayPal."""
    payload = data.model_dump(mode="json")
    existing = await db.scalar(
        select(Payment).where(Payment.actor == user.name, Payment.submission_key == key)
    )
    if existing:
        if existing.input_hash != fingerprint(payload):
            raise HTTPException(409, "Submission key already belongs to different input")
        return checkout_result(existing, signer)
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal configuration is incomplete")
    if data.price_per_period > settings.maximum_amount:
        raise HTTPException(422, "Amount exceeds the configured maximum")
    payment = await db.scalar(
        select(Payment)
        .where(Payment.actor == user.name, Payment.intent_key == data.intent_key)
        .with_for_update()
    )
    request_values = data.model_dump(exclude={"intent_key"})
    request_values["client_name"] = f"{data.first_name} {data.last_name}"
    if payment:
        raise HTTPException(
            409, "This payment identity is already used. Start a new payment with a new intent key."
        )
    unfinished = (
        await db.scalars(
            select(Payment)
            .where(
                Payment.actor == user.name,
                Payment.submission_key.is_not(None),
                Payment.checkout_status.is_(None),
            )
            .with_for_update()
        )
    ).all()
    for previous in unfinished:
        # Close only local checkout permission. Never claim that PayPal cancelled
        # an agreement, erase money evidence, or release a financial reservation.
        previous.checkout_status, previous.browser_state = "failed", "closed"
        audit(db, user.name, "checkout.superseded", previous.id)
    payment = Payment(
        **request_values,
        actor=user.name,
        intent_key=data.intent_key,
        submission_key=key,
        input_hash=fingerprint(payload),
        environment=settings.paypal_environment,
        browser_state="open",
        phase="reserved",
    )
    db.add(payment)
    await db.flush()
    audit(db, user.name, "checkout.submitted", payment.id)
    await db.commit()
    log.info("checkout_submitted payment=%s", payment.id)
    try:
        await paypal.access_token()
        payment.phase = "create_dispatched"
        await db.commit()
        if data.is_subscription:
            result = await create_subscription(db, payment, paypal, settings, signer)
        else:
            result = await paypal.request(
                "POST",
                "/v2/checkout/orders",
                request_id=action_key(payment.provider_correlation, "create"),
                data={
                    "intent": "CAPTURE",
                    "payment_source": {
                        "paypal": {
                            "name": {"given_name": data.first_name, "surname": data.last_name},
                            "experience_context": {"shipping_preference": "NO_SHIPPING"},
                        }
                    },
                    "purchase_units": [
                        {
                            "custom_id": payment.provider_correlation,
                            "payee": {"merchant_id": settings.paypal_merchant_id},
                            "amount": {
                                "value": str(data.price_per_period),
                                "currency_code": data.currency,
                            },
                        }
                    ],
                },
            )
        payment.provider_id = provider_id(result.get("id"))
        payment = await required(db, Payment, payment.id, lock=True)
        payment.phase = "created"
        if payment.checkout_status is not None:
            payment.browser_state = "closed"
            await db.commit()
            return checkout_result(payment, signer)
        await set_payment_status(db, payment, "pending")
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        if isinstance(error, ReconciliationError):
            log.debug("checkout_verification_failed payment=%s reason=%s", payment.id, error)
        payment.error = failure_code(error)
        payment.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        payment.browser_state = "closed"
        await set_payment_status(db, payment, "failed")
        await finish_checkout(db, payment, "failed")
    return checkout_result(payment, signer)


async def checkout_outcome(
    db: AsyncSession,
    identifier: str,
    data: OutcomeInput,
    user: Principal,
    paypal: PayPal,
    settings: Settings,
    signer: URLSafeTimedSerializer,
) -> dict[str, Any]:
    """Claim the initial callback once; approved one-time orders may capture once."""
    try:
        signed = signer.loads(data.proof, max_age=8 * 60 * 60)
    except BadData:
        raise HTTPException(400, "Invalid or expired checkout state") from None
    if signed != {"payment": identifier, "actor": user.name}:
        raise HTTPException(403, "Checkout state belongs to another payment or staff member")
    payment = await required(db, Payment, identifier, lock=True)
    if payment.actor != user.name or (data.provider_id and payment.provider_id != data.provider_id):
        raise HTTPException(409, "Callback does not match this payment")
    if (
        payment.checkout_status is not None
        or payment.phase != "created"
        or payment.browser_state != "open"
    ):
        return checkout_result(payment, signer)
    payment.browser_state, payment.phase = "closed", "verification_claimed"
    await set_payment_status(db, payment, "failed")
    if data.outcome != "approved":
        await set_payment_status(
            db,
            payment,
            "failed"
            if payment.is_subscription
            else "cancelled"
            if data.outcome == "cancelled"
            else "failed",
        )
        await finish_checkout(db, payment, "failed")
        return checkout_result(payment, signer)
    try:
        if payment.is_subscription:
            details = await paypal.request(
                "GET",
                "/v1/billing/subscriptions/" + provider_id(payment.provider_id),
                params={"fields": "plan"},
            )
            verified = await verify_subscription(db, payment, details)
            status = SUBSCRIPTION_PAYMENT_STATUSES.get(verified.status, "failed")
        else:
            path = "/v2/checkout/orders/" + provider_id(payment.provider_id)
            order = await paypal.request("GET", path)
            unit = verify_order(payment, order, settings)
            # Resolve OAuth before locking: closure must remain possible during HTTP.
            await paypal.access_token()
            payment = await required(db, Payment, payment.id, lock=True)
            if payment.checkout_status is not None:
                return checkout_result(payment, signer)
            if not unit.get("payments", {}).get("captures"):
                if order.get("status") != "APPROVED":
                    raise ReconciliationError("Order is not approved; no capture sent")
                payment.phase = "capture_dispatched"
                await db.commit()
                await paypal.request(
                    "POST",
                    path + "/capture",
                    data={},
                    request_id=action_key(payment.provider_correlation, "capture"),
                )
                # A rejected verification GET cannot invalidate an accepted capture.
                payment.phase = "capture_accepted"
                await db.commit()
                order = await paypal.request("GET", path)
            status = await ingest_order(db, payment, order, settings)
        payment.phase = "finished"
        await set_payment_status(db, payment, status)
        await finish_checkout(
            db,
            payment,
            "success"
            if status in {"completed", "active", "suspended", "pending"}
            and (
                not payment.is_subscription
                or verified.status in {"ACTIVE", "APPROVED", "SUSPENDED"}
            )
            else "failed",
        )
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        await db.rollback()
        payment = await required(db, Payment, identifier)
        if isinstance(error, ReconciliationError):
            log.debug("checkout_verification_failed payment=%s reason=%s", identifier, error)
        payment.error = failure_code(error)
        payment.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        await set_payment_status(db, payment, "failed")
    if payment.checkout_status is None:
        await finish_checkout(db, payment, "failed")
    return checkout_result(payment, signer)
