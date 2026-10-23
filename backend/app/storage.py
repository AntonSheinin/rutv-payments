"""Database identity guard preventing cross-merchant or Sandbox/live reuse."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SyncState


def ensure_environment(db: Session, settings: Settings) -> None:
    """One database belongs to exactly one PayPal environment and merchant."""
    # A row lock cannot serialize the first startup, when the row does not exist yet.
    db.execute(text("SELECT pg_advisory_xact_lock(783417030)"))
    state = db.scalar(
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
    db.commit()
