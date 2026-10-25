"""Refunds rules for direct, single-dispatch financial requests."""

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.errors import ReconciliationError
from app.models import (
    FinancialAction,
    Payment,
    Transaction,
    now,
)
from app.normalize import (
    money,
    transaction,
)
from app.paypal import PayPal, PayPalError, definite_rejection, failure_code, provider_id
from app.responses import TransactionResponse
from app.schemas import RefundInput
from app.storage import action_key, audit, fingerprint, required

log = logging.getLogger("payments.refunds")


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
            if r.kind == "refund" and (r.status == "pending" or r.reservation_active)
        ),
        Decimal(0),
    )
    restricted = original.provider_status in {"REFUNDED", "REVERSED"} or any(
        r.kind == "reversal" for r in adjustments
    )
    balance_unavailable = original.provider_status == "PARTIALLY_REFUNDED" and refunded == 0
    remaining = max(Decimal(0), original.amount - refunded - reserved)
    capable = original.api_family in {"capture", "sale"}
    eligible = (
        original.kind == "collection"
        and original.status == "completed"
        and capable
        and not restricted
        and not balance_unavailable
        and now() < deadline
        and remaining > 0
    )
    return {
        "can_refund": eligible,
        "refundable_amount": str(remaining) if not balance_unavailable else None,
        "refunded_amount": str(refunded),
        "refund_deadline": deadline.isoformat(),
    }


async def refund(
    db: AsyncSession,
    identifier: str,
    data: RefundInput,
    user: Principal,
    key: str,
    paypal: PayPal,
) -> dict[str, Any]:
    """Reserve one exact refund and retain reservations for uncertain results."""
    scope, payload = "refund:" + identifier, data.model_dump(mode="json")
    original = await required(db, Transaction, identifier)
    if original.payment_id:
        await required(db, Payment, original.payment_id, lock=True)
    original = await required(db, Transaction, identifier, lock=True)
    previous = await db.scalar(
        select(FinancialAction).where(
            FinancialAction.actor == user.name,
            FinancialAction.scope == scope,
            FinancialAction.submission_key == key,
        )
    )
    if previous:
        if previous.input_hash != fingerprint(payload):
            raise HTTPException(409, "Submission key belongs to different refund input")
        return {
            **TransactionResponse.model_validate(
                await required(db, Transaction, previous.resource_id)
            ).model_dump(mode="json"),
            "error": previous.error,
        }
    adjustments = list(
        await db.scalars(select(Transaction).where(Transaction.original_id == identifier))
    )
    eligibility = refund_eligibility(original, adjustments)
    if not eligibility["can_refund"] or data.amount > Decimal(eligibility["refundable_amount"]):
        raise HTTPException(409, "Payment is not eligible for the requested refund amount/date")
    item = Transaction(
        provider_id="reserved-" + action_key(user.name + key, scope),
        api_family=original.api_family,
        kind="refund",
        status="failed",
        provider_status="UNCONFIRMED",
        reservation_active=True,
        original_id=identifier,
        payment_id=original.payment_id,
        subscription_id=original.subscription_id,
        client_name=original.client_name,
        agreement_number=original.agreement_number,
        amount=data.amount,
        currency=original.currency,
    )
    db.add(item)
    await db.flush()
    action = FinancialAction(
        actor=user.name,
        scope=scope,
        submission_key=key,
        input_hash=fingerprint(payload),
        resource_id=item.id,
    )
    db.add(action)
    audit(db, user.name, "refund.confirmed", item.id)
    await db.commit()
    log.info(
        "refund_reserved transaction=%s action=%s collection=%s", item.id, action.id, identifier
    )
    action_id = action.id
    dispatched = False
    try:
        await paypal.access_token()
        action.status = "dispatched"
        await db.commit()
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
        await transaction(
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
        action.reservation_active = item.reservation_active
        await db.commit()
    except (PayPalError, httpx.TransportError, ReconciliationError) as error:
        await db.rollback()
        action = await required(db, FinancialAction, action_id)
        item = await required(db, Transaction, action.resource_id)
        if isinstance(error, ReconciliationError):
            log.debug("refund_verification_failed transaction=%s reason=%s", item.id, error)
        action.error = failure_code(error)
        action.debug_id = error.debug_id if isinstance(error, PayPalError) else None
        item.status = action.status = "failed"
        # The label is Failed for every unsuccessful result. Release the money
        # only when dispatch never happened or PayPal definitively rejected it.
        item.reservation_active = action.reservation_active = dispatched and not definite_rejection(
            error
        )
        await db.commit()
    log.info(
        "refund_result transaction=%s action=%s status=%s "
        "reservation_active=%s error=%s debug_id=%r",
        item.id,
        action.id,
        item.status,
        item.reservation_active,
        action.error,
        action.debug_id,
    )
    return {
        **TransactionResponse.model_validate(item).model_dump(mode="json"),
        "error": action.error,
    }
