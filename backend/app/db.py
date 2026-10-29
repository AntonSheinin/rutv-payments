"""Async request sessions and probes of the original singleton ownership connection."""

import logging
from collections.abc import AsyncGenerator
from typing import Annotated

from alembic import command
from alembic.config import Config
from fastapi import Depends, HTTPException, Request
from sqlalchemy import Connection, text
from sqlalchemy.exc import (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    PendingRollbackError,
    ResourceClosedError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.settings import Settings

log = logging.getLogger("payments.db")


def session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Create the application's async engine on its owning lifespan/event loop."""
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession]:
    """Close/rollback the request's session before returning its assembled response."""
    async with request.app.state.database_session() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db, scope="function")]


def migrate_database(connection: Connection) -> None:
    """Run the one Alembic chain using a supplied synchronous/adapted connection."""
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def ownership(request: Request) -> None:
    """Fail closed after losing the original owner; never reacquire or share probe transactions."""
    state = request.app.state
    async with state.ownership_probe:
        lock = state.ownership
        if state.ownership_lost or lock.invalidated or lock.closed:
            if not state.ownership_lost:
                log.error(
                    "database_ownership_lost pid=%s reason=connection_closed", state.owner_pid
                )
            state.ownership_lost = True
            raise HTTPException(503, "Database ownership lost; restart required")
        try:
            owned = await lock.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND pid=pg_backend_pid() AND pid=:pid AND classid=0 "
                    "AND objid=783417029 AND granted)"
                ),
                {"pid": state.owner_pid},
            )
            await lock.commit()
        except (
            OperationalError,
            InterfaceError,
            DisconnectionError,
            ResourceClosedError,
            PendingRollbackError,
        ) as error:
            state.ownership_lost = True
            log.error(
                "database_ownership_lost pid=%s reason=%s", state.owner_pid, type(error).__name__
            )
            raise HTTPException(503, "Database ownership unavailable; restart required") from None
        # Programming defects propagate to the safe global 500 boundary before dispatch.
        if not owned:
            state.ownership_lost = True
            log.error("database_ownership_lost pid=%s reason=lock_missing", state.owner_pid)
            raise HTTPException(503, "Database ownership lost; restart required")


Mutation = Annotated[None, Depends(ownership)]
