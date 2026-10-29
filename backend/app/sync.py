"""Explicit bounded provider reads; no capture, mutation replay or scheduled work."""

import logging
from datetime import UTC, datetime, time, timedelta
from time import monotonic
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ReconciliationError
from app.models import (
    FinancialAction,
    Payment,
    Subscription,
    SyncState,
    Transaction,
    now,
)
from app.normalize import (
    SUBSCRIPTION_PAYMENT_STATUSES,
    money,
    subscription,
    transaction,
    update_payment_status,
)
from app.paypal import PayPal, PayPalError, provider_id
from app.schemas import CURRENCIES, SyncInput
from app.settings import Settings
from app.storage import required
from app.verification import ingest_order, verify_subscription

log = logging.getLogger("payments.sync")


def response_items(result: dict[str, Any], field: str) -> list[dict[str, Any]]:
    """Require an explicit provider result list; missing data is not an empty page."""
    items = result.get(field)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ReconciliationError("Invalid provider result list: " + field)
    return items


async def refresh_payment(
    db: AsyncSession, payment: Payment, paypal: PayPal, settings: Settings
) -> None:
    """Read this payment's provider facts without capture or mutation replay."""
    identifier, remote, recurring = payment.id, payment.provider_id, payment.is_subscription
    await db.commit()
    if not remote:
        return
    if recurring:
        details = await paypal.request(
            "GET", "/v1/billing/subscriptions/" + provider_id(remote), params={"fields": "plan"}
        )
        payment = await required(db, Payment, identifier, lock=True)
        verified = await verify_subscription(db, payment, details)
        observed = SUBSCRIPTION_PAYMENT_STATUSES.get(verified.status, "failed")
    else:
        details = await paypal.request("GET", "/v2/checkout/orders/" + provider_id(remote))
        payment = await required(db, Payment, identifier, lock=True)
        observed = await ingest_order(db, payment, details, settings)
    payment.status = observed
    await update_payment_status(db, payment)
    await db.commit()


async def refresh_subscription(db: AsyncSession, remote: str, paypal: PayPal) -> Subscription:
    """Read one agreement/plan; installment history requires an explicit date window."""
    details = await paypal.request(
        "GET", "/v1/billing/subscriptions/" + provider_id(remote), params={"fields": "plan"}
    )
    if details.get("id") != remote:
        raise ReconciliationError("Provider subscription identity mismatch")
    if not details.get("plan", {}).get("billing_cycles"):
        raise ReconciliationError("Subscription effective plan was not returned")
    payment = await db.scalar(select(Payment).where(Payment.provider_id == remote))
    if payment:
        return await verify_subscription(db, payment, details)
    return await subscription(db, details)


async def refresh_transaction(db: AsyncSession, item: Transaction, paypal: PayPal) -> None:
    """Read a known capture/sale/refund through its selected product's exact API."""
    if item.provider_id.startswith("reserved-"):
        raise HTTPException(
            409, "Provider refund identity is unknown; no mutation will be replayed"
        )
    identifier, remote, family, kind = item.id, item.provider_id, item.api_family, item.kind
    await db.commit()
    if family == "capture":
        path = "/v2/payments/" + ("refunds/" if kind == "refund" else "captures/")
    elif family == "sale":
        path = "/v1/payments/" + ("refund/" if kind == "refund" else "sale/")
    else:
        raise HTTPException(409, "Unsupported provider resource")
    details = await paypal.request("GET", path + provider_id(remote))
    if details.get("id") != remote:
        raise ReconciliationError("Provider resource identity mismatch")
    item = await required(db, Transaction, identifier)
    original = (await required(db, Transaction, item.original_id)) if item.original_id else None
    await transaction(
        db,
        details,
        family,
        kind,
        existing=item,
        original=original,
        payment_id=item.payment_id,
        subscription_id=item.subscription_id,
    )
    for action in await db.scalars(
        select(FinancialAction).where(FinancialAction.resource_id == item.id)
    ):
        action.status = item.status
        action.reservation_active = item.reservation_active
    await db.commit()


async def import_reporting_item(db: AsyncSession, entry: dict[str, Any], paypal: PayPal) -> bool:
    """Import only confirmed supported resources; exclude ambiguity without API fallback."""
    info = entry.get("transaction_info", {})
    currency = info.get("transaction_amount", {}).get("currency_code")
    if currency not in CURRENCIES:
        return False
    code, identifier = info.get("transaction_event_code", ""), info.get("transaction_id")
    if not identifier:
        return False
    existing = await db.scalar(select(Transaction).where(Transaction.provider_id == identifier))
    if existing:
        await refresh_transaction(db, existing, paypal)
        return True
    await db.commit()
    if code == "T1107":
        details = await paypal.request("GET", "/v2/payments/refunds/" + provider_id(identifier))
        capture_id = details.get("supplementary_data", {}).get("related_ids", {}).get("capture_id")
        for link in details.get("links", []):
            path = urlparse(link.get("href", "")).path
            if link.get("rel") == "up" and path.startswith("/v2/payments/captures/"):
                capture_id = path.rsplit("/", 1)[-1]
        original = await db.scalar(
            select(Transaction).where(
                Transaction.provider_id == capture_id,
                Transaction.kind == "collection",
                Transaction.api_family == "capture",
            )
        )
        if not original:
            return False
        await transaction(
            db,
            details,
            "capture",
            "refund",
            original=original,
            payment_id=original.payment_id,
            subscription_id=original.subscription_id,
            origin="imported",
        )
        return True
    if code in {"T1106", "T1202"}:
        original = await db.scalar(
            select(Transaction).where(
                Transaction.provider_id == info.get("paypal_reference_id"),
                Transaction.kind == "collection",
            )
        )
        amount = money(info.get("transaction_amount"))
        if not original or abs(amount) != original.amount or original.currency != currency:
            return False
        kind = (
            "reversal"
            if code == "T1106" and amount < 0
            else "adjustment"
            if code == "T1202" and amount > 0
            else None
        )
        if not kind:
            return False
        await transaction(
            db,
            {
                "id": identifier,
                "status": "COMPLETED",
                "amount": {"value": str(abs(amount)), "currency_code": currency},
                "create_time": info.get("transaction_initiation_date"),
            },
            original.api_family,
            kind,
            original=original,
            payment_id=original.payment_id,
            subscription_id=original.subscription_id,
            origin="imported",
        )
        return True
    if not code.startswith("T00") or money(info.get("transaction_amount")) <= 0:
        return False
    details = await paypal.request("GET", "/v2/payments/captures/" + provider_id(identifier))
    if (
        money(details.get("amount")) != money(info.get("transaction_amount"))
        or details.get("amount", {}).get("currency_code") != currency
    ):
        raise ReconciliationError("Reporting resource amount mismatch")
    await transaction(db, details, "capture", origin="imported")
    return True


async def synchronize(
    db: AsyncSession, data: SyncInput, paypal: PayPal, settings: Settings
) -> dict[str, Any]:
    """Read an explicit resource/window or all Active/Suspended subscription pages."""
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal configuration is incomplete")
    started = monotonic()
    log.info("synchronization_started scope=%s page=%s", data.scope, data.page)
    count, excluded, next_page = 0, 0, None
    if data.scope == "payment":
        payment = await required(db, Payment, data.resource_id or "")
        if payment.submission_key is not None:
            await refresh_payment(db, payment, paypal, settings)
            count = 1
        else:
            collection = await db.scalar(
                select(Transaction)
                .where(Transaction.payment_id == data.resource_id, Transaction.kind == "collection")
                .order_by((Transaction.status == "failed").asc(), Transaction.effective_at.desc())
            )
            if collection:
                await refresh_transaction(db, collection, paypal)
                count = 1
        adjustments = list(
            await db.scalars(
                select(Transaction).where(
                    Transaction.payment_id == data.resource_id, Transaction.kind != "collection"
                )
            )
        )
        for adjustment in adjustments:
            await refresh_transaction(db, adjustment, paypal)
    elif data.scope == "transaction":
        await refresh_transaction(
            db, (await required(db, Transaction, data.resource_id or "")), paypal
        )
        count = 1
    elif data.scope == "subscription":
        item = await required(db, Subscription, data.resource_id or "")
        remote, local_id = item.provider_id, item.id
        await db.commit()
        await refresh_subscription(db, remote, paypal)
        if data.start_date and data.end_date:
            start, end = window(data)
            await db.commit()
            result = await paypal.request(
                "GET",
                "/v1/billing/subscriptions/" + provider_id(remote) + "/transactions",
                params={"start_time": start.isoformat(), "end_time": end.isoformat()},
            )
            for entry in response_items(result, "transactions"):
                entry["amount"] = entry.get("amount_with_breakdown", {}).get("gross_amount", {})
                if entry["amount"].get("currency_code") not in CURRENCIES:
                    excluded += 1
                    continue
                await transaction(
                    db,
                    entry,
                    "sale",
                    subscription_id=local_id,
                    origin=item.origin,
                )
                count += 1
        await db.commit()
    elif data.scope == "subscriptions":
        # One explicit import authorizes all matching pages in this request only.
        # The route timeout bounds the operation; no task continues after timeout.
        page = 1
        seen: set[str] = set()
        while True:
            await db.commit()
            result = await paypal.request(
                "GET",
                "/v1/billing/subscriptions",
                params={"page_size": 20, "page": page, "statuses": "ACTIVE,SUSPENDED"},
            )
            items = response_items(result, "subscriptions")
            links = response_items(result, "links")
            if any(not isinstance(link.get("rel"), str) for link in links):
                raise ReconciliationError("Invalid subscription pagination links")
            for index, entry in enumerate(items):
                try:
                    remote = provider_id(entry.get("id"))
                    if entry.get("status") not in {"ACTIVE", "SUSPENDED"}:
                        excluded += 1
                        continue
                    if remote in seen:
                        continue
                    seen.add(remote)
                    await refresh_subscription(db, remote, paypal)
                    await db.commit()
                    count += 1
                except ReconciliationError as error:
                    await db.rollback()
                    excluded += 1
                    log.debug(
                        "synchronization_item_excluded scope=%s page=%s index=%s reason=%s",
                        data.scope,
                        page,
                        index,
                        error,
                    )
            if not any(link["rel"] == "next" for link in links):
                break
            if not items:
                raise ReconciliationError("Empty subscription page has a next link")
            page += 1
    else:
        start, end = window(data)
        await db.commit()
        result = await paypal.request(
            "GET",
            "/v1/reporting/transactions",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "fields": "all",
                "page_size": 20,
                "page": data.page,
            },
        )
        entries = response_items(result, "transaction_details")
        total_pages = result.get("total_pages")
        if type(total_pages) is not int or total_pages < 0 or (entries and total_pages == 0):
            raise ReconciliationError("Invalid reporting pagination")
        for index, entry in enumerate(entries):
            try:
                if await import_reporting_item(db, entry, paypal):
                    count += 1
                    await db.commit()
                else:
                    await db.rollback()
                    excluded += 1
            except (PayPalError, ReconciliationError) as error:
                await db.rollback()
                if isinstance(error, PayPalError) and error.status not in {400, 403, 404, 422}:
                    raise
                excluded += 1
                log.debug(
                    "synchronization_item_excluded scope=%s page=%s index=%s reason=%s",
                    data.scope,
                    data.page,
                    index,
                    error.code if isinstance(error, PayPalError) else error,
                )
        if data.page < total_pages:
            next_page = data.page + 1
    state = await db.scalar(select(SyncState).where(SyncState.scope == data.scope))
    if state is None:
        state = SyncState(scope=data.scope)
        db.add(state)
    state.last_success = now()
    coverage_note = (
        "All returned Active/Suspended agreement pages read; installment history was not imported"
        if data.scope == "subscriptions"
        else "Only this resource/page was read; provider history may be incomplete"
    )
    state.checkpoint = {
        "page": data.page,
        "next_page": next_page,
        "excluded": excluded,
        "coverage_note": coverage_note,
    }
    await db.commit()
    log.info(
        "synchronization_completed scope=%s page=%s processed=%s excluded=%s "
        "next_page=%s elapsed_ms=%.1f",
        data.scope,
        data.page,
        count,
        excluded,
        next_page,
        (monotonic() - started) * 1000,
    )
    return {
        "processed": count,
        "excluded": excluded,
        "next_page": next_page,
        "coverage_note": state.checkpoint["coverage_note"],
    }


def window(data: SyncInput) -> tuple[datetime, datetime]:
    """Validate a single bounded calendar window, without implicit historical backfill."""
    if not data.start_date or not data.end_date:
        raise HTTPException(422, "An explicit date window is required")
    zone = ZoneInfo(data.timezone)
    start = datetime.combine(data.start_date, time.min, zone).astimezone(UTC)
    end = min(
        datetime.combine(data.end_date + timedelta(days=1), time.min, zone).astimezone(UTC), now()
    )
    # Limit calendar days, including 31-day windows with a daylight-saving shift.
    if not start < end or (data.end_date - data.start_date).days >= 31:
        raise HTTPException(422, "Choose a past/current window of at most 31 days")
    return start, end
