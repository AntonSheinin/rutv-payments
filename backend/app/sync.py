"""Explicit bounded provider reads; no capture, mutation replay or scheduled work."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ReconciliationError
from app.models import (
    Attempt,
    FinancialAction,
    Payment,
    Subscription,
    SyncState,
    Transaction,
    now,
)
from app.normalize import money, subscription, transaction, update_payment_status
from app.paypal import PayPal, PayPalError, provider_id
from app.schemas import CURRENCIES, SyncInput
from app.services import ingest_order, required, verify_subscription


def response_items(result: dict[str, Any], field: str) -> list[dict[str, Any]]:
    """Require an explicit provider result list; missing data is not an empty page."""
    items = result.get(field)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ReconciliationError("Invalid provider result list: " + field)
    return items


async def refresh_attempt(
    db: Session, attempt: Attempt, paypal: PayPal, settings: Settings
) -> None:
    """Observe an existing checkout; never capture or declare an unknown creation absent."""
    identifier = attempt.id
    remote = attempt.provider_id
    recurring = attempt.terms["is_subscription"]
    db.commit()
    if not remote:
        return
    if recurring:
        details = await paypal.request(
            "GET", "/v1/billing/subscriptions/" + provider_id(remote), params={"fields": "plan"}
        )
        attempt = required(db, Attempt, identifier, lock=True)
        verified = verify_subscription(db, attempt, details)
        observed = (
            "active"
            if verified.status == "ACTIVE"
            else "suspended"
            if verified.status == "SUSPENDED"
            else "cancelled"
            if verified.status in {"CANCELLED", "EXPIRED"}
            else "pending"
        )
    else:
        details = await paypal.request("GET", "/v2/checkout/orders/" + provider_id(remote))
        attempt = required(db, Attempt, identifier, lock=True)
        observed = ingest_order(db, attempt, details, settings)
    attempt = required(db, Attempt, identifier, lock=True)
    if observed in {"completed", "active", "suspended", "cancelled", "pending", "failed"}:
        attempt.status = observed
        parent = required(db, Payment, attempt.payment_id, lock=True)
        newest = db.scalar(
            select(Attempt.id)
            .where(Attempt.payment_id == parent.id)
            .order_by(Attempt.created_at.desc())
            .limit(1)
        )
        if newest == attempt.id:
            parent.status = observed
            update_payment_status(db, parent)
    db.commit()


async def refresh_subscription(db: Session, remote: str, paypal: PayPal) -> Subscription:
    """Read one agreement/plan; installment history requires an explicit date window."""
    details = await paypal.request(
        "GET", "/v1/billing/subscriptions/" + provider_id(remote), params={"fields": "plan"}
    )
    if not details.get("plan", {}).get("billing_cycles"):
        raise ReconciliationError("Subscription effective plan was not returned")
    attempt = db.scalar(select(Attempt).where(Attempt.provider_id == remote))
    if attempt:
        return verify_subscription(db, attempt, details)
    return subscription(db, details)


async def refresh_transaction(db: Session, item: Transaction, paypal: PayPal) -> None:
    """Read a known capture/sale/refund through its selected product's exact API."""
    if item.provider_id.startswith("reserved-"):
        raise HTTPException(
            409, "Provider refund identity is unknown; no mutation will be replayed"
        )
    identifier, remote, family, kind = item.id, item.provider_id, item.api_family, item.kind
    db.commit()
    if family == "capture":
        path = "/v2/payments/" + ("refunds/" if kind == "refund" else "captures/")
    elif family == "sale":
        path = "/v1/payments/" + ("refund/" if kind == "refund" else "sale/")
    else:
        raise HTTPException(409, "Unsupported provider resource")
    details = await paypal.request("GET", path + provider_id(remote))
    if details.get("id") != remote:
        raise ReconciliationError("Provider resource identity mismatch")
    item = required(db, Transaction, identifier)
    original = required(db, Transaction, item.original_id) if item.original_id else None
    transaction(
        db,
        details,
        family,
        kind,
        existing=item,
        original=original,
        payment_id=item.payment_id,
        subscription_id=item.subscription_id,
    )
    for action in db.scalars(select(FinancialAction).where(FinancialAction.resource_id == item.id)):
        action.status = item.status
    db.commit()


async def import_reporting_item(db: Session, entry: dict[str, Any], paypal: PayPal) -> bool:
    """Import only confirmed supported resources; exclude ambiguity without API fallback."""
    info = entry.get("transaction_info", {})
    currency = info.get("transaction_amount", {}).get("currency_code")
    if currency not in CURRENCIES:
        return False
    code, identifier = info.get("transaction_event_code", ""), info.get("transaction_id")
    if not identifier:
        return False
    existing = db.scalar(select(Transaction).where(Transaction.provider_id == identifier))
    if existing:
        await refresh_transaction(db, existing, paypal)
        return True
    db.commit()
    if code == "T1107":
        details = await paypal.request("GET", "/v2/payments/refunds/" + provider_id(identifier))
        capture_id = details.get("supplementary_data", {}).get("related_ids", {}).get("capture_id")
        for link in details.get("links", []):
            path = urlparse(link.get("href", "")).path
            if link.get("rel") == "up" and path.startswith("/v2/payments/captures/"):
                capture_id = path.rsplit("/", 1)[-1]
        original = db.scalar(
            select(Transaction).where(
                Transaction.provider_id == capture_id,
                Transaction.kind == "collection",
                Transaction.api_family == "capture",
            )
        )
        if not original:
            return False
        transaction(
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
        original = db.scalar(
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
        transaction(
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
    transaction(db, details, "capture", origin="imported")
    return True


async def synchronize(
    db: Session, data: SyncInput, paypal: PayPal, settings: Settings
) -> dict[str, Any]:
    """Perform one requested resource or page; additional pages need explicit requests."""
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal configuration is incomplete")
    count, excluded, next_page = 0, 0, None
    if data.scope == "payment":
        required(db, Payment, data.resource_id or "")
        attempt = db.scalar(
            select(Attempt)
            .where(Attempt.payment_id == data.resource_id)
            .order_by(Attempt.created_at.desc())
            .limit(1)
        )
        if attempt:
            await refresh_attempt(db, attempt, paypal, settings)
            count = 1
        else:
            collection = db.scalar(
                select(Transaction)
                .where(Transaction.payment_id == data.resource_id, Transaction.kind == "collection")
                .order_by((Transaction.status == "failed").asc(), Transaction.effective_at.desc())
            )
            if collection:
                await refresh_transaction(db, collection, paypal)
                count = 1
        adjustments = list(
            db.scalars(
                select(Transaction).where(
                    Transaction.payment_id == data.resource_id, Transaction.kind != "collection"
                )
            )
        )
        for adjustment in adjustments:
            await refresh_transaction(db, adjustment, paypal)
    elif data.scope == "transaction":
        await refresh_transaction(db, required(db, Transaction, data.resource_id or ""), paypal)
        count = 1
    elif data.scope == "subscription":
        item = required(db, Subscription, data.resource_id or "")
        remote, local_id = item.provider_id, item.id
        db.commit()
        await refresh_subscription(db, remote, paypal)
        if data.start_date and data.end_date:
            start, end = window(data)
            db.commit()
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
                transaction(
                    db,
                    entry,
                    "sale",
                    subscription_id=local_id,
                    origin=item.origin,
                )
                count += 1
        db.commit()
    elif data.scope == "subscriptions":
        db.commit()
        result = await paypal.request(
            "GET", "/v1/billing/subscriptions", params={"page_size": 5, "page": data.page}
        )
        items = response_items(result, "subscriptions")
        links = response_items(result, "links")
        if any(not isinstance(link.get("rel"), str) for link in links):
            raise ReconciliationError("Invalid subscription pagination links")
        for entry in items:
            try:
                await refresh_subscription(db, provider_id(entry.get("id")), paypal)
                db.commit()
                count += 1
            except ReconciliationError:
                db.rollback()
                excluded += 1
        if any(link["rel"] == "next" for link in links):
            next_page = data.page + 1
    else:
        start, end = window(data)
        db.commit()
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
        for entry in entries:
            try:
                if await import_reporting_item(db, entry, paypal):
                    count += 1
                    db.commit()
                else:
                    db.rollback()
                    excluded += 1
            except PayPalError as error:
                db.rollback()
                if error.status not in {400, 403, 404, 422}:
                    raise
                excluded += 1
            except ReconciliationError:
                db.rollback()
                excluded += 1
        if data.page < total_pages:
            next_page = data.page + 1
    state = db.scalar(select(SyncState).where(SyncState.scope == data.scope))
    if state is None:
        state = SyncState(scope=data.scope)
        db.add(state)
    state.last_success = now()
    state.checkpoint = {
        "page": data.page,
        "next_page": next_page,
        "excluded": excluded,
        "coverage_note": "Only this resource/page was read; provider history may be incomplete",
    }
    db.commit()
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
