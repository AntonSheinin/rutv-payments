"""Authenticated payments HTTP contracts and read assembly."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on

from app.auth import Config, User, authenticate
from app.checkout import (
    checkout_outcome,
    checkout_result,
    close_checkout,
    submit,
)
from app.db import Db, Mutation
from app.models import Payment, Transaction
from app.refunds import refund, refund_eligibility
from app.reporting import Filters, listing
from app.responses import (
    CheckoutResponse,
    Page,
    PaymentDetailResponse,
    PaymentRecord,
    PaymentResponse,
    RefundResponse,
    TransactionDetailResponse,
    TransactionResponse,
)
from app.routes import Key
from app.schemas import (
    OutcomeInput,
    RefundInput,
    SubmitInput,
)
from app.storage import required

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])


async def checkout_request(request: Request, user: User, guard: Mutation) -> AsyncGenerator[None]:
    """Allow one open checkout mutation per operator, without queueing or retrying."""
    # Singleton ownership guarantees one process. This async dependency claims the
    # operator on that process's event loop, without a DB lock over provider HTTP.
    active: set[str] = request.app.state.checkout_requests
    if user.name in active:
        raise HTTPException(409, "A checkout request is still running. Wait for its response.")
    active.add(user.name)
    try:
        yield
    finally:
        active.remove(user.name)


@router.post("/payments", dependencies=[Depends(checkout_request)], response_model=CheckoutResponse)
async def new_payment(
    data: SubmitInput,
    request: Request,
    db: Db,
    user: User,
    key: Key,
    settings: Config,
    guard: Mutation,
) -> dict[str, Any]:
    """Submit immutable terms and return the initial checkout directly."""
    return await submit(
        db, data, user, key, request.app.state.paypal, settings, request.app.state.checkout_signer
    )


@router.post(
    "/payments/{identifier}/checkout-outcome",
    dependencies=[Depends(checkout_request)],
    response_model=CheckoutResponse,
)
async def outcome(
    identifier: str,
    data: OutcomeInput,
    request: Request,
    db: Db,
    user: User,
    settings: Config,
    guard: Mutation,
) -> dict[str, Any]:
    """Verify and record the original outcome; never replay a claimed capture."""
    return await checkout_outcome(
        db,
        identifier,
        data,
        user,
        request.app.state.paypal,
        settings,
        request.app.state.checkout_signer,
    )


@router.get("/checkout-submissions/{intent_key}", response_model=CheckoutResponse)
async def submission_result(
    intent_key: str, request: Request, db: Db, user: User
) -> dict[str, Any]:
    """Read the latest result after response loss without issuing another mutation."""
    item = await db.scalar(
        select(Payment).where(Payment.actor == user.name, Payment.intent_key == intent_key)
    )
    if item is None:
        raise HTTPException(404, "Submission not recorded; no replacement was started")
    return checkout_result(item, request.app.state.checkout_signer)


@router.post(
    "/payments/{identifier}/close",
    dependencies=[Depends(checkout_request)],
    response_model=CheckoutResponse,
)
async def close_submission(
    identifier: str, request: Request, db: Db, user: User, guard: Mutation
) -> dict[str, Any]:
    """Close this payment's local checkout without claiming provider cancellation."""
    return await close_checkout(db, identifier, request.app.state.checkout_signer)


@router.get("/checkout-return", response_model=CheckoutResponse)
async def checkout_return(state: str, request: Request, db: Db, user: User) -> dict[str, Any]:
    """Resolve a signed redirect state before the browser sends its CSRF-protected POST."""
    from itsdangerous import BadData

    try:
        signed = request.app.state.checkout_signer.loads(state, max_age=8 * 60 * 60)
    except BadData:
        raise HTTPException(400, "Invalid or expired checkout state") from None
    if (
        not isinstance(signed, dict)
        or signed.get("actor") != user.name
        or not isinstance(signed.get("payment"), str)
    ):
        raise HTTPException(403, "Checkout state does not belong to this staff member")
    return checkout_result(
        (await required(db, Payment, signed["payment"])), request.app.state.checkout_signer
    )


@router.get("/payments", response_model=Page[PaymentResponse])
async def payments(db: Db, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Search all payments, including unpaid checkouts and refunded collections."""
    result = await listing(db, Payment, filters)
    ids = [item["id"] for item in result["items"]]
    records = (
        {item.id: item for item in (await db.scalars(select(Payment).where(Payment.id.in_(ids))))}
        if ids
        else {}
    )
    collections = (
        {
            item.payment_id: item
            for item in (
                await db.scalars(
                    select(Transaction)
                    .where(Transaction.payment_id.in_(ids), Transaction.kind == "collection")
                    .ext(distinct_on(Transaction.payment_id))
                    .order_by(
                        Transaction.payment_id,
                        (Transaction.status == "failed").asc(),
                        Transaction.effective_at.desc(),
                    )
                )
            )
        }
        if ids
        else {}
    )
    collection_ids = [item.id for item in collections.values()]
    adjustments = (
        list(
            await db.scalars(select(Transaction).where(Transaction.original_id.in_(collection_ids)))
        )
        if collection_ids
        else []
    )
    for row in result["items"]:
        collection = collections.get(row["id"])
        row.update(payment_fields(records[row["id"]], collection, adjustments))
    return result


@router.get("/payments/{identifier}", response_model=PaymentDetailResponse)
async def payment_detail(
    identifier: str,
    db: Db,
    history_page: Annotated[int, Query(ge=1, le=100000)] = 1,
    history_page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Return payment and checkout facts with paginated financial history."""
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    payment = await required(db, Payment, identifier)
    collection = await db.scalar(
        select(Transaction)
        .where(Transaction.payment_id == identifier, Transaction.kind == "collection")
        .order_by(
            (Transaction.status == "failed").asc(),
            Transaction.effective_at.desc(),
            Transaction.id.desc(),
        )
    )
    adjustments = (
        list(await db.scalars(select(Transaction).where(Transaction.original_id == collection.id)))
        if collection
        else []
    )
    return {
        **PaymentRecord.model_validate(payment).model_dump(mode="json"),
        **payment_fields(payment, collection, adjustments),
        "transactions": (
            await listing(
                db,
                Transaction,
                Filters(payment_id=identifier, page=history_page, page_size=history_page_size),
            )
        ),
    }


def payment_fields(
    payment: Payment,
    collection: Transaction | None,
    adjustments: list[Transaction],
) -> dict[str, Any]:
    """Join presentation and refund eligibility to the one stable payment identity."""
    if collection and collection.status == "failed" and payment.status != "failed":
        collection = None
    result: dict[str, Any] = {
        "amount": format(payment.price_per_period, ".2f"),
        "checkout_status": payment.checkout_status,
        "collection_id": collection.id if collection else None,
        "can_close": payment.checkout_status is None and payment.browser_state == "open",
        "provider_id": collection.provider_id if collection else payment.provider_id,
        "can_refund": False,
        "effective_at": collection.effective_at.isoformat()
        if collection
        else payment.created_at.isoformat(),
    }
    if collection:
        serialized = TransactionResponse.model_validate(collection).model_dump(mode="json")
        result.update(
            {
                key: serialized[key]
                for key in ("fee", "net", "payer_name", "payer_email", "last_synced_at")
            }
        )
        result.update(
            refund_eligibility(
                collection,
                [item for item in adjustments if item.original_id == collection.id],
            )
        )
    return result


@router.get("/transactions", response_model=Page[TransactionResponse])
async def transactions(db: Db, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Search collections/adjustments with one batched refund-reservation lookup."""
    result = await listing(db, Transaction, filters)
    ids = [r["id"] for r in result["items"]]
    items = (
        {
            item.id: item
            for item in (await db.scalars(select(Transaction).where(Transaction.id.in_(ids))))
        }
        if ids
        else {}
    )
    adjustments = (
        list(await db.scalars(select(Transaction).where(Transaction.original_id.in_(ids))))
        if ids
        else []
    )
    for row in result["items"]:
        item = items[row["id"]]
        row.update(refund_eligibility(item, [r for r in adjustments if r.original_id == item.id]))
    return result


@router.get("/transactions/{identifier}", response_model=TransactionDetailResponse)
async def transaction_detail(identifier: str, db: Db) -> dict[str, Any]:
    """Inspect collection and adjustment facts; reading performs no provider calls."""
    item = await required(db, Transaction, identifier)
    adjustments = list(
        await db.scalars(select(Transaction).where(Transaction.original_id == item.id))
    )
    return {
        **TransactionResponse.model_validate(item).model_dump(mode="json"),
        **refund_eligibility(item, adjustments),
        "adjustments": [
            TransactionResponse.model_validate(row).model_dump(mode="json") for row in adjustments
        ],
    }


@router.post("/transactions/{identifier}/refunds", response_model=RefundResponse)
async def refund_payment(
    identifier: str,
    data: RefundInput,
    request: Request,
    db: Db,
    user: User,
    key: Key,
    guard: Mutation,
) -> dict[str, Any]:
    """Reserve and submit exactly the confirmed refund; never queue it."""
    return await refund(db, identifier, data, user, key, request.app.state.paypal)
