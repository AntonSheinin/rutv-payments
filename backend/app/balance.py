"""Explicit balance-only provider reads and one durable account snapshot."""

import logging

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ReconciliationError
from app.models import SyncState, now
from app.normalize import money, timestamp
from app.paypal import PayPal
from app.responses import BalanceAmount, BalanceSnapshot
from app.schemas import CURRENCIES

log = logging.getLogger("payments.balance")
BALANCE_SCOPE = "paypal_balance"


async def synchronize_balance(
    db: AsyncSession, paypal: PayPal, settings: Settings
) -> BalanceSnapshot:
    """Read only balances and atomically retain the newest validated provider snapshot."""
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal configuration is incomplete")
    result = await paypal.request("GET", "/v1/reporting/balances")
    if result.get("account_id") != settings.paypal_merchant_id:
        raise ReconciliationError("Balance account does not match the configured merchant")
    entries = result.get("balances")
    if not isinstance(entries, list):
        raise ReconciliationError("Provider balances must be a list")
    amounts: dict[str, BalanceAmount] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("currency"), str):
            raise ReconciliationError("Provider balance has no currency")
        currency = entry["currency"]
        if currency not in CURRENCIES:
            continue
        total = entry.get("total_balance")
        if (
            currency in amounts
            or not isinstance(total, dict)
            or total.get("currency_code") != currency
        ):
            raise ReconciliationError("Provider balance currency is inconsistent")
        amounts[currency] = BalanceAmount(currency=currency, total=money(total))
    snapshot = BalanceSnapshot(
        balances=list(amounts.values()),
        as_of=timestamp(result.get("last_refresh_time")),
        last_synced_at=now(),
    )
    # Serialize first insertion as well as updates, after HTTP has finished.
    # An older response from another tab must not replace newer provider facts.
    await db.execute(text("SELECT pg_advisory_xact_lock(783417031)"))
    state = await db.scalar(select(SyncState).where(SyncState.scope == BALANCE_SCOPE))
    if state is None:
        state = SyncState(scope=BALANCE_SCOPE)
        db.add(state)
    elif BalanceSnapshot.model_validate(state.checkpoint).as_of > snapshot.as_of:
        await db.commit()
        return BalanceSnapshot.model_validate(state.checkpoint)
    state.checkpoint = snapshot.model_dump(mode="json")
    state.last_success = snapshot.last_synced_at
    await db.commit()
    log.info("balance_synchronization_completed currencies=%s", len(snapshot.balances))
    return snapshot
