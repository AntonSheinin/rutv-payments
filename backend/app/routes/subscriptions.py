"""Authenticated subscriptions HTTP contracts and read assembly."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import User, authenticate
from app.db import Db, Mutation
from app.models import Subscription, Transaction
from app.reporting import Filters, listing, subscription_progress
from app.responses import (
    CancellationResponse,
    Page,
    SubscriptionDetailResponse,
    SubscriptionResponse,
)
from app.routes import Key
from app.schemas import ReasonInput
from app.storage import required
from app.subscriptions import cancel_subscription

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])


@router.get("/subscriptions", response_model=Page[SubscriptionResponse])
async def subscriptions(db: Db, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Search known agreements; no automatic renewal synchronization."""
    result = await listing(db, Subscription, filters)
    await subscription_progress(db, result["items"])
    return result


@router.get("/subscriptions/{identifier}", response_model=SubscriptionDetailResponse)
async def subscription_detail(
    identifier: str,
    db: Db,
    history_page: Annotated[int, Query(ge=1, le=100000)] = 1,
    history_page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Inspect the verified agreement and every page of its recorded financial history."""
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    item = SubscriptionResponse.model_validate(
        await required(db, Subscription, identifier)
    ).model_dump(mode="json")
    await subscription_progress(db, [item])
    return {
        **item,
        "transactions": (
            await listing(
                db,
                Transaction,
                Filters(subscription_id=identifier, page=history_page, page_size=history_page_size),
            )
        ),
    }


@router.post("/subscriptions/{identifier}/cancel", response_model=CancellationResponse)
async def cancel(
    identifier: str,
    data: ReasonInput,
    request: Request,
    db: Db,
    user: User,
    key: Key,
    guard: Mutation,
) -> dict[str, Any]:
    """Cancel future subscription billing directly once."""
    return await cancel_subscription(
        db, identifier, data.reason, user, key, request.app.state.paypal
    )
