"""Direct checkout and financial actions with durable, never-replayed mutation claims."""

import hashlib
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.config import Settings
from app.errors import ReconciliationError
from app.models import (
    Attempt,
    AuditEvent,
    FinancialAction,
    Payment,
    Record,
    Subscription,
    Transaction,
    now,
)
from app.normalize import (
    money,
    set_subscription_status,
    subscription,
    transaction,
    update_payment_status,
)
from app.paypal import PayPal, PayPalError, provider_id
from app.schemas import OutcomeInput, RefundInput, SubmitInput
from app.serialization import record

log = logging.getLogger("payments.services")


def required[T: Record](db: Session, model: type[T], identifier: str, *, lock: bool = False) -> T:
    """Load a required resource, refreshing locked values after concurrent requests."""
    statement = select(model).where(model.id == identifier)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    item = db.scalar(statement)
    if item is None:
        raise HTTPException(404, "Record not found")
    return item


def fingerprint(payload: dict[str, Any]) -> str:
    """Bind an idempotency identity to canonical immutable input."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def action_key(identifier: str, phase: str) -> str:
    """Derive a distinct stable provider identity for each single outbound mutation."""
    return str(uuid5(NAMESPACE_URL, f"payments:{identifier}:{phase}"))


def audit(db: Session, actor: str, action: str, identifier: str) -> None:
    """Stage safe attribution without customer data or provider payloads."""
    db.add(AuditEvent(actor=actor, action=action, resource_id=identifier))


def proof(signer: URLSafeTimedSerializer, attempt: Attempt) -> str:
    """Sign an attempt/operator correlation proof with a dedicated signing purpose."""
    return signer.dumps({"attempt": attempt.id, "actor": attempt.actor})


def attempt_result(attempt: Attempt, signer: URLSafeTimedSerializer) -> dict[str, Any]:
    """Expose immutable form terms and outcome without a financial replay action."""
    return {
        "id": attempt.payment_id,
        "attempt_id": attempt.id,
        "provider_id": attempt.provider_id,
        "status": attempt.status,
        "checkout_status": attempt.checkout_status,
        "browser_state": attempt.browser_state,
        "error": attempt.error,
        "debug_id": attempt.debug_id,
        "terms": attempt.terms,
        "proof": proof(signer, attempt)
        if attempt.status == "awaiting_approval" and attempt.checkout_status is None
        else None,
        "can_cancel": can_cancel_attempt(attempt),
    }


def can_cancel_attempt(attempt: Attempt) -> bool:
    """Only a local one-time checkout can be closed before approval."""
    return (
        attempt.status == "awaiting_approval"
        and attempt.browser_state == "open"
        and attempt.phase == "created"
        and attempt.checkout_status is None
        and not attempt.terms["is_subscription"]
    )


async def cancel_attempt(
    db: Session,
    payment_id: str,
    attempt_id: str,
    user: Principal,
    signer: URLSafeTimedSerializer,
) -> dict[str, Any]:
    """Close a one-time checkout without dispatching any PayPal mutation."""
    attempt = required(db, Attempt, attempt_id, lock=True)
    if attempt.payment_id != payment_id:
        raise HTTPException(409, "Attempt does not belong to this payment")
    if attempt.status == "cancelled":
        return attempt_result(attempt, signer)
    if attempt.terms["is_subscription"]:
        raise HTTPException(
            409,
            "PayPal only permits cancellation of active or suspended subscriptions. "
            "This subscription checkout has not been cancelled.",
        )
    newest = db.scalar(
        select(Attempt.id)
        .where(Attempt.payment_id == payment_id)
        .order_by(Attempt.created_at.desc(), Attempt.id.desc())
        .limit(1)
    )
    if newest != attempt.id or not can_cancel_attempt(attempt):
        raise HTTPException(409, "Only an unclaimed attempt awaiting approval can be cancelled")
    # This shares checkout_outcome's row lock, so cancellation and capture cannot both claim.
    attempt.browser_state, attempt.phase = "closed", "cancel_claimed"
    audit(db, user.name, "checkout.cancel_requested", attempt.id)
    # Orders are captured only by our claimed callback; a closed attempt cannot capture.
    # Subscription approval can activate billing at PayPal independently, so that
    # product is rejected before changing durable state or making provider calls.
    set_attempt(db, attempt, "cancelled")
    finish_checkout(db, attempt, "failed")
    return attempt_result(attempt, signer)


def set_attempt(db: Session, attempt: Attempt, status: str) -> None:
    """Persist an attempt result and payment status together."""
    # An explicit read may confirm collection while the original request is still
    # waiting on HTTP. Its later timeout/error must not undo that verified result.
    with db.no_autoflush:
        persisted = db.scalar(
            select(Attempt.status).where(Attempt.id == attempt.id).with_for_update()
        )
    if persisted == "completed" and status != "completed":
        db.rollback()
        db.refresh(attempt)
        return
    attempt.status = status
    payment = required(db, Payment, attempt.payment_id, lock=True)
    payment.status = status
    update_payment_status(db, payment)
    db.commit()
    log.info("checkout_outcome attempt=%s status=%s", attempt.id, status)


def finish_checkout(db: Session, attempt: Attempt, outcome: str) -> None:
    """Freeze an operator checkout result; later financial reads cannot promote it."""
    with db.no_autoflush:
        saved = db.scalar(
            select(Attempt.checkout_status).where(Attempt.id == attempt.id).with_for_update()
        )
    if saved is None:
        attempt.checkout_status = outcome
        db.commit()
    else:
        db.refresh(attempt)


def close_checkout(db: Session, attempt: Attempt, signer: URLSafeTimedSerializer) -> dict[str, Any]:
    """End an unsuccessful local checkout without cancelling or retrying at PayPal."""
    attempt = required(db, Attempt, attempt.id, lock=True)
    if attempt.checkout_status is None:
        attempt.browser_state = "closed"
        finish_checkout(db, attempt, "failed")
    return attempt_result(attempt, signer)


def failure_code(error: Exception) -> str:
    """Return only safe bounded diagnostics, without provider or SQL payloads."""
    return error.code if isinstance(error, PayPalError) else type(error).__name__


def definite_rejection(error: Exception) -> bool:
    """Only explicit provider input/auth rejection establishes no accepted mutation."""
    return (
        isinstance(error, PayPalError)
        and error.status in {400, 401, 403, 404, 422}
        and error.code not in {"ORDER_ALREADY_CAPTURED", "DUPLICATE_REQUEST_ID"}
    )


async def submit(
    db: Session,
    data: SubmitInput,
    user: Principal,
    key: str,
    paypal: PayPal,
    settings: Settings,
    signer: URLSafeTimedSerializer,
) -> dict[str, Any]:
    """Create one new payment and attempt for explicit Submit; duplicates never contact PayPal."""
    payload = data.model_dump(mode="json")
    existing = db.scalar(
        select(Attempt).where(Attempt.actor == user.name, Attempt.submission_key == key)
    )
    if existing:
        if existing.input_hash != fingerprint(payload):
            raise HTTPException(409, "Submission key already belongs to different input")
        return attempt_result(existing, signer)
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal configuration is incomplete")
    if data.price_per_period > settings.maximum_amount:
        raise HTTPException(422, "Amount exceeds the configured maximum")
    payment = db.scalar(
        select(Payment)
        .where(Payment.actor == user.name, Payment.intent_key == data.intent_key)
        .with_for_update()
    )
    terms = data.model_dump(mode="json", exclude={"intent_key"})
    terms["client_name"] = f"{data.first_name} {data.last_name}"
    request_values = data.model_dump(exclude={"intent_key", "first_name", "last_name"})
    request_values["client_name"] = terms["client_name"]
    if payment:
        raise HTTPException(
            409, "This payment identity is already used. Start a new payment with a new intent key."
        )
    unfinished = db.scalars(
        select(Attempt)
        .where(Attempt.actor == user.name, Attempt.checkout_status.is_(None))
        .with_for_update()
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
        environment=settings.paypal_environment,
    )
    db.add(payment)
    db.flush()
    attempt = Attempt(
        payment_id=payment.id,
        actor=user.name,
        submission_key=key,
        input_hash=fingerprint(payload),
        terms=terms,
    )
    db.add(attempt)
    db.flush()
    audit(db, user.name, "checkout.submitted", attempt.id)
    db.commit()
    log.info("checkout_submitted attempt=%s", attempt.id)
    try:
        await paypal.access_token()
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        attempt.error, attempt.browser_state = failure_code(error), "closed"
        set_attempt(db, attempt, "failed")
        finish_checkout(db, attempt, "failed")
        return attempt_result(attempt, signer)
    attempt.phase = "create_dispatched"
    db.commit()
    try:
        if data.is_subscription:
            result = await create_subscription(db, attempt, paypal, settings, signer)
        else:
            result = await paypal.request(
                "POST",
                "/v2/checkout/orders",
                request_id=action_key(attempt.id, "create"),
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
                            "custom_id": attempt.id,
                            "payee": {"merchant_id": settings.paypal_merchant_id},
                            "amount": {
                                "value": str(data.price_per_period),
                                "currency_code": data.currency,
                            },
                        }
                    ],
                },
            )
        attempt.provider_id = provider_id(result.get("id"))
        attempt = required(db, Attempt, attempt.id, lock=True)
        attempt.phase = "created"
        if attempt.checkout_status is not None:
            attempt.browser_state = "closed"
            db.commit()
            return attempt_result(attempt, signer)
        set_attempt(db, attempt, "awaiting_approval")
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        attempt.error = failure_code(error)
        attempt.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        attempt.browser_state = "closed"
        set_attempt(db, attempt, "failed" if definite_rejection(error) else "unknown")
        finish_checkout(db, attempt, "failed")
    return attempt_result(attempt, signer)


async def create_subscription(
    db: Session,
    attempt: Attempt,
    paypal: PayPal,
    settings: Settings,
    signer: URLSafeTimedSerializer,
) -> dict[str, Any]:
    """Create the product/plan/subscription sequence once for this attempt."""
    terms = attempt.terms
    product = await paypal.request(
        "POST",
        "/v1/catalogs/products",
        request_id=action_key(attempt.id, "product"),
        data={"name": "Service subscription", "type": "SERVICE"},
    )
    plan = await paypal.request(
        "POST",
        "/v1/billing/plans",
        request_id=action_key(attempt.id, "plan"),
        data={
            "product_id": provider_id(product.get("id")),
            "name": "Service billing",
            "status": "ACTIVE",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": terms["period_unit"],
                        "interval_count": terms["period_count"],
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": terms["payment_cycles"] or 0,
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": terms["price_per_period"],
                            "currency_code": terms["currency"],
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
    attempt.plan_id = provider_id(plan.get("id"))
    db.commit()
    state = proof(signer, attempt)
    return await paypal.request(
        "POST",
        "/v1/billing/subscriptions",
        request_id=action_key(attempt.id, "subscription"),
        data={
            "plan_id": attempt.plan_id,
            "custom_id": attempt.id,
            "subscriber": {
                "name": {"given_name": terms["first_name"], "surname": terms["last_name"]}
            },
            "application_context": {
                "shipping_preference": "NO_SHIPPING",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": settings.paypal_return_url + "?state=" + state,
                "cancel_url": settings.paypal_cancel_url + "?state=" + state,
            },
        },
    )


def verify_order(attempt: Attempt, data: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Require exact merchant, identity, intent, currency and amount before capture."""
    units = data.get("purchase_units", [])
    if data.get("id") != attempt.provider_id or data.get("intent") != "CAPTURE" or len(units) != 1:
        raise ReconciliationError("Unexpected provider order identity or intent")
    unit: dict[str, Any] = units[0]
    if (
        unit.get("custom_id") != attempt.id
        or unit.get("payee", {}).get("merchant_id") != settings.paypal_merchant_id
        or unit.get("amount", {}).get("currency_code") != attempt.terms["currency"]
        or money(unit.get("amount")) != Decimal(attempt.terms["price_per_period"])
    ):
        raise ReconciliationError("Provider order does not match saved terms")
    return unit


def ingest_order(db: Session, attempt: Attempt, data: dict[str, Any], settings: Settings) -> str:
    """Record verified collections; approval alone is never collected money."""
    unit = verify_order(attempt, data, settings)
    captures = unit.get("payments", {}).get("captures", [])
    if len(captures) > 1:
        raise ReconciliationError("Unexpected split capture")
    status = "unknown"
    for capture in captures:
        if (
            money(capture.get("amount")) != Decimal(attempt.terms["price_per_period"])
            or capture.get("amount", {}).get("currency_code") != attempt.terms["currency"]
        ):
            raise ReconciliationError("Capture does not match saved amount")
        row = transaction(db, capture, "capture", payment_id=attempt.payment_id)
        row.client_name, row.agreement_number = (
            attempt.terms["client_name"],
            attempt.terms["agreement_number"],
        )
        status = row.status
    return status


async def checkout_outcome(
    db: Session,
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
    if signed != {"attempt": data.attempt_id, "actor": user.name}:
        raise HTTPException(403, "Checkout state belongs to another attempt or staff member")
    attempt = required(db, Attempt, data.attempt_id, lock=True)
    if (
        attempt.payment_id != identifier
        or attempt.actor != user.name
        or (data.provider_id and attempt.provider_id != data.provider_id)
    ):
        raise HTTPException(409, "Callback does not match this attempt")
    if (
        attempt.checkout_status is not None
        or attempt.status != "awaiting_approval"
        or attempt.browser_state != "open"
    ):
        return attempt_result(attempt, signer)
    attempt.browser_state, attempt.phase = "closed", "verification_claimed"
    set_attempt(db, attempt, "unknown")
    if data.outcome != "approved":
        set_attempt(
            db,
            attempt,
            "unknown"
            if attempt.terms["is_subscription"]
            else "cancelled"
            if data.outcome == "cancelled"
            else "failed",
        )
        finish_checkout(db, attempt, "failed")
        return attempt_result(attempt, signer)
    try:
        if attempt.terms["is_subscription"]:
            details = await paypal.request(
                "GET",
                "/v1/billing/subscriptions/" + provider_id(attempt.provider_id),
                params={"fields": "plan"},
            )
            verified = verify_subscription(db, attempt, details)
            status = (
                "active"
                if verified.status == "ACTIVE"
                else "suspended"
                if verified.status == "SUSPENDED"
                else "cancelled"
                if verified.status in {"CANCELLED", "EXPIRED"}
                else "pending"
            )
        else:
            path = "/v2/checkout/orders/" + provider_id(attempt.provider_id)
            order = await paypal.request("GET", path)
            unit = verify_order(attempt, order, settings)
            # Resolve OAuth before locking: closure must remain possible during HTTP.
            await paypal.access_token()
            attempt = required(db, Attempt, attempt.id, lock=True)
            if attempt.checkout_status is not None:
                return attempt_result(attempt, signer)
            if not unit.get("payments", {}).get("captures"):
                if order.get("status") != "APPROVED":
                    raise ReconciliationError("Order is not approved; no capture sent")
                attempt.phase = "capture_dispatched"
                db.commit()
                await paypal.request(
                    "POST", path + "/capture", data={}, request_id=action_key(attempt.id, "capture")
                )
                # A rejected verification GET cannot invalidate an accepted capture.
                attempt.phase = "capture_accepted"
                db.commit()
                order = await paypal.request("GET", path)
            status = ingest_order(db, attempt, order, settings)
        attempt.phase = "finished"
        set_attempt(db, attempt, status)
        finish_checkout(
            db,
            attempt,
            "success"
            if status in {"completed", "active", "suspended", "pending"}
            and (
                not attempt.terms["is_subscription"]
                or verified.status in {"ACTIVE", "APPROVED", "SUSPENDED"}
            )
            else "failed",
        )
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        db.rollback()
        attempt = required(db, Attempt, data.attempt_id)
        attempt.error = failure_code(error)
        attempt.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        set_attempt(
            db,
            attempt,
            "failed"
            if attempt.phase == "capture_dispatched" and definite_rejection(error)
            else "unknown",
        )
    if attempt.checkout_status is None:
        finish_checkout(db, attempt, "failed")
    return attempt_result(attempt, signer)


def verify_subscription(db: Session, attempt: Attempt, details: dict[str, Any]) -> Subscription:
    """Verify correlation and recurring terms without using Orders capture."""
    if (
        details.get("id") != attempt.provider_id
        or details.get("custom_id") != attempt.id
        or details.get("plan_id") != attempt.plan_id
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
            "interval_unit": attempt.terms["period_unit"],
            "interval_count": attempt.terms["period_count"],
        }
        or cycle.get("total_cycles") != (attempt.terms["payment_cycles"] or 0)
        or money(price) != Decimal(attempt.terms["price_per_period"])
        or price.get("currency_code") != attempt.terms["currency"]
    ):
        raise ReconciliationError("Subscription plan terms mismatch")
    details["plan"] = plan
    return subscription(db, details, payment=required(db, Payment, attempt.payment_id))


def refund_eligibility(original: Transaction, adjustments: list[Transaction]) -> dict[str, Any]:
    """Share date/capability/balance rules between lists and locked submissions."""
    deadline = original.effective_at + timedelta(days=180)
    refunded = sum(
        (r.amount for r in adjustments if r.kind == "refund" and r.status == "completed"),
        Decimal(0),
    )
    if original.provider_status == "REFUNDED":
        # A full provider-confirmed refund exhausts the balance even when its
        # individual refund entries have not been imported into the ledger yet.
        refunded = max(refunded, original.amount)
    reserved = sum(
        (
            r.amount
            for r in adjustments
            if r.kind == "refund" and r.status in {"pending", "unknown"}
        ),
        Decimal(0),
    )
    restricted = original.provider_status in {"REFUNDED", "REVERSED"} or any(
        r.kind == "reversal" for r in adjustments
    )
    unknown = original.provider_status == "PARTIALLY_REFUNDED" and refunded == 0
    remaining = max(Decimal(0), original.amount - refunded - reserved)
    capable = original.api_family in {"capture", "sale"}
    eligible = (
        original.kind == "collection"
        and original.status == "completed"
        and capable
        and not restricted
        and not unknown
        and now() < deadline
        and remaining > 0
    )
    return {
        "can_refund": eligible,
        "refundable_amount": str(remaining) if not unknown else None,
        "refunded_amount": str(refunded),
        "refund_deadline": deadline.isoformat(),
    }


async def refund(
    db: Session,
    identifier: str,
    data: RefundInput,
    user: Principal,
    key: str,
    paypal: PayPal,
) -> dict[str, Any]:
    """Reserve one exact refund and retain reservations for uncertain results."""
    scope, payload = "refund:" + identifier, data.model_dump(mode="json")
    original = required(db, Transaction, identifier, lock=True)
    previous = db.scalar(
        select(FinancialAction).where(
            FinancialAction.actor == user.name,
            FinancialAction.scope == scope,
            FinancialAction.submission_key == key,
        )
    )
    if previous:
        if previous.input_hash != fingerprint(payload):
            raise HTTPException(409, "Submission key belongs to different refund input")
        return {**record(required(db, Transaction, previous.resource_id)), "error": previous.error}
    adjustments = list(db.scalars(select(Transaction).where(Transaction.original_id == identifier)))
    eligibility = refund_eligibility(original, adjustments)
    if not eligibility["can_refund"] or data.amount > Decimal(eligibility["refundable_amount"]):
        raise HTTPException(409, "Payment is not eligible for the requested refund amount/date")
    item = Transaction(
        provider_id="reserved-" + action_key(user.name + key, scope),
        api_family=original.api_family,
        kind="refund",
        status="unknown",
        provider_status="UNCONFIRMED",
        original_id=identifier,
        payment_id=original.payment_id,
        subscription_id=original.subscription_id,
        client_name=original.client_name,
        agreement_number=original.agreement_number,
        amount=data.amount,
        currency=original.currency,
    )
    db.add(item)
    db.flush()
    action = FinancialAction(
        actor=user.name,
        scope=scope,
        submission_key=key,
        input_hash=fingerprint(payload),
        resource_id=item.id,
    )
    db.add(action)
    audit(db, user.name, "refund.confirmed", item.id)
    db.commit()
    log.info("refund_confirmed transaction=%s", item.id)
    dispatched = False
    try:
        await paypal.access_token()
        action.status = "dispatched"
        db.commit()
        dispatched = True
        if original.api_family == "capture":
            path, amount = (
                "/v2/payments/captures/" + provider_id(original.provider_id) + "/refund",
                {"value": str(data.amount), "currency_code": original.currency},
            )
        else:
            path, amount = (
                "/v1/payments/sale/" + provider_id(original.provider_id) + "/refund",
                {"total": str(data.amount), "currency": original.currency},
            )
        result = await paypal.request(
            "POST",
            path,
            data={"amount": amount, "note_to_payer": data.reason},
            request_id=action.id,
        )
        if (
            money(result.get("amount")) != data.amount
            or result.get("amount", {}).get(
                "currency_code", result.get("amount", {}).get("currency")
            )
            != original.currency
        ):
            raise ReconciliationError("Refund amount/currency mismatch")
        transaction(
            db,
            result,
            original.api_family,
            "refund",
            original=original,
            existing=item,
            payment_id=original.payment_id,
            subscription_id=original.subscription_id,
        )
        action.status = item.status
        db.commit()
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        db.rollback()
        action = required(db, FinancialAction, action.id)
        item = required(db, Transaction, action.resource_id)
        action.error = failure_code(error)
        action.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        item.status = action.status = (
            "failed" if not dispatched or definite_rejection(error) else "unknown"
        )
        db.commit()
    return {**record(item), "error": action.error}


async def cancel_subscription(
    db: Session, identifier: str, reason: str, user: Principal, key: str, paypal: PayPal
) -> dict[str, Any]:
    """Cancel future billing once without refunds, debt settlement or replay."""
    item = required(db, Subscription, identifier, lock=True)
    scope, digest = "cancel:" + identifier, fingerprint({"reason": reason})
    previous = db.scalar(
        select(FinancialAction).where(
            FinancialAction.actor == user.name,
            FinancialAction.scope == scope,
            FinancialAction.submission_key == key,
        )
    )
    if previous:
        if previous.input_hash != digest:
            raise HTTPException(409, "Submission key belongs to different input")
        return {"id": item.id, "status": previous.status, "error": previous.error}
    if item.status not in {"ACTIVE", "SUSPENDED"} or db.scalar(
        select(FinancialAction.id).where(
            FinancialAction.scope == scope,
            FinancialAction.status.in_(["reserved", "dispatched", "unknown"]),
        )
    ):
        raise HTTPException(409, "Cancellation unavailable or previous cancellation unconfirmed")
    action = FinancialAction(
        actor=user.name, scope=scope, submission_key=key, input_hash=digest, resource_id=item.id
    )
    db.add(action)
    db.commit()
    dispatched = False
    try:
        await paypal.access_token()
        action.status = "dispatched"
        db.commit()
        dispatched = True
        await paypal.request(
            "POST",
            "/v1/billing/subscriptions/" + provider_id(item.provider_id) + "/cancel",
            data={"reason": reason},
            request_id=action.id,
        )
        action.status = "completed"
        set_subscription_status(db, item, "CANCELLED")
        audit(db, user.name, "subscription.cancelled", item.id)
        db.commit()
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        action.error = failure_code(error)
        action.status = "failed" if not dispatched or definite_rejection(error) else "unknown"
        db.commit()
    return {"id": item.id, "status": action.status, "error": action.error}
