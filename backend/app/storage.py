"""Database identity guard preventing cross-merchant or Sandbox/live reuse."""

import hashlib
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, Record, SyncState
from app.settings import Settings


async def ensure_environment(db: AsyncSession, settings: Settings) -> None:
    """One database belongs to exactly one PayPal environment and merchant."""
    # A row lock cannot serialize the first startup, when the row does not exist yet.
    await db.execute(text("SELECT pg_advisory_xact_lock(783417030)"))
    state = await db.scalar(
        select(SyncState).where(SyncState.scope == "database_identity").with_for_update()
    )
    expected = {"environment": settings.paypal_environment, "merchant": settings.paypal_merchant_id}
    if state:
        previous = state.checkpoint
        if previous.get("environment") != expected["environment"]:
            raise RuntimeError("Database belongs to a different PayPal environment")
        if previous.get("merchant") and previous["merchant"] != expected["merchant"]:
            raise RuntimeError("Database belongs to a different PayPal merchant")
        state.checkpoint = expected
    else:
        db.add(SyncState(scope="database_identity", checkpoint=expected))
    await db.commit()


async def required[T: Record](
    db: AsyncSession, model: type[T], identifier: str, *, lock: bool = False
) -> T:
    """Load a required resource, refreshing locked values after concurrent requests."""
    statement = select(model).where(model.id == identifier)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    item = await db.scalar(statement)
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


def audit(db: AsyncSession, actor: str, action: str, identifier: str) -> None:
    """Stage safe attribution without customer data or provider payloads."""
    db.add(AuditEvent(actor=actor, action=action, resource_id=identifier))
