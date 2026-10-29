"""Own one database, migrate it and clean up resources on their original event loop."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import text

from app.db import migrate_database, session_factory
from app.logging_config import configure_logging
from app.paypal import PayPal
from app.settings import get_settings
from app.storage import ensure_environment

log = logging.getLogger("payments.lifecycle")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Keep advisory ownership pinned; startup and shutdown never replay financial work."""
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.session_signer = URLSafeTimedSerializer(settings.app_secret, salt="staff-session-v2")
    app.state.checkout_signer = URLSafeTimedSerializer(
        settings.app_secret, salt="checkout-state-v1"
    )
    sessions = session_factory(settings)
    engine = sessions.kw["bind"]
    app.state.database_session = sessions
    app.state.ownership_lost = False
    app.state.ownership_probe = asyncio.Lock()
    app.state.checkout_requests = set()
    try:
        async with engine.connect() as owner:
            try:
                if not await owner.scalar(text("SELECT pg_try_advisory_lock(783417029)")):
                    raise RuntimeError("Another service instance owns this database")
                app.state.owner_pid = await owner.scalar(text("SELECT pg_backend_pid()"))
                await owner.commit()
                app.state.ownership = owner
                # Alembic uses a separate adapted connection; ownership stays pinned.
                async with engine.begin() as migration:
                    await migration.run_sync(migrate_database)
                async with sessions() as db:
                    await ensure_environment(db, settings)
                paypal = PayPal(settings)
                app.state.paypal = paypal
                try:
                    log.info("api_ready environment=%s", settings.paypal_environment)
                    yield
                finally:
                    await paypal.client.aclose()
            finally:
                # Invalidation releases the physical owner connection, not a pooled lease.
                await owner.invalidate()
    finally:
        await engine.dispose()
        log.info("api_stopped")
