"""Authenticated reporting HTTP contracts and read assembly."""

import asyncio
import logging
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder

from app.auth import Config, User, authenticate
from app.db import Db, Mutation
from app.reporting import Filters, statistics
from app.responses import StatisticsResponse, SyncResponse
from app.schemas import SyncInput
from app.sync import synchronize

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])
log = logging.getLogger("payments.reporting")


@router.get("/statistics", response_model=StatisticsResponse)
async def stats(db: Db, settings: Config, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Return Decimal statistics separately for each supported currency."""
    return jsonable_encoder(
        (await statistics(db, filters, settings.paypal_environment)),
        custom_encoder={Decimal: lambda v: format(v, ".2f")},
    )


@router.post("/synchronizations", response_model=SyncResponse)
async def synchronization(
    data: SyncInput, request: Request, db: Db, user: User, settings: Config, guard: Mutation
) -> dict[str, Any]:
    """Perform only the explicitly requested bounded provider reads."""
    try:
        async with asyncio.timeout(120):
            return await synchronize(db, data, request.app.state.paypal, settings)
    except TimeoutError:
        log.warning("synchronization_timed_out scope=%s page=%s", data.scope, data.page)
        raise HTTPException(
            504, "Read-only synchronization timed out; no automatic continuation"
        ) from None
