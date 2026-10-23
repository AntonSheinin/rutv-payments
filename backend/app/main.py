"""Private local staff API with direct checkout and no background payment processing."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import Config, User, authenticate
from app.auth import router as auth_router
from app.config import get_settings
from app.db import Db, migrate_database, session_factory
from app.errors import ReconciliationError
from app.logging_config import configure_logging
from app.models import Attempt, Payment, Subscription, Transaction
from app.paypal import PayPal, PayPalError
from app.reporting import Filters, listing, statistics, subscription_progress
from app.schemas import (
    CURRENCIES,
    PERIOD_LIMITS,
    AssociationInput,
    OutcomeInput,
    ReasonInput,
    RefundInput,
    SubmitInput,
    SyncInput,
)
from app.serialization import record
from app.services import (
    attempt_result,
    audit,
    can_cancel_attempt,
    cancel_attempt,
    cancel_subscription,
    checkout_outcome,
    close_checkout,
    refund,
    refund_eligibility,
    required,
    submit,
)
from app.storage import ensure_environment
from app.sync import synchronize

log = logging.getLogger("payments.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Acquire singleton ownership, migrate and share clients without spawning tasks."""
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.session_signer = URLSafeTimedSerializer(settings.app_secret, salt="staff-session-v2")
    app.state.checkout_signer = URLSafeTimedSerializer(
        settings.app_secret, salt="checkout-state-v1"
    )
    sessions = session_factory()
    app.state.database_session = sessions
    engine = sessions.kw["bind"]
    app.state.ownership_lost = False
    try:
        with engine.connect() as lock:
            try:
                if not lock.scalar(text("SELECT pg_try_advisory_lock(783417029)")):
                    raise RuntimeError("Another service instance owns this database")
                app.state.owner_pid = lock.scalar(text("SELECT pg_backend_pid()"))
                lock.commit()
                app.state.ownership = lock
                migrate_database(engine)
                with sessions() as db:
                    ensure_environment(db, settings)
                app.state.paypal = PayPal(settings)
                app.state.checkout_requests = set()
                log.info("api_ready environment=%s", settings.paypal_environment)
                try:
                    yield
                finally:
                    await app.state.paypal.client.aclose()
            finally:
                lock.invalidate()
    finally:
        engine.dispose()
        log.info("api_stopped")


app = FastAPI(
    title="Internal PayPal Payments",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
api = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


async def ownership(request: Request) -> None:
    """Reject mutations after losing the original ownership connection; never reacquire."""
    state = request.app.state
    lock = state.ownership
    if state.ownership_lost or lock.invalidated or lock.closed:
        state.ownership_lost = True
        raise HTTPException(503, "Database ownership lost; restart required")
    try:
        owned = lock.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                "AND pid=pg_backend_pid() AND pid=:pid AND classid=0 "
                "AND objid=783417029 AND granted)"
            ),
            {"pid": state.owner_pid},
        )
        lock.commit()
    except SQLAlchemyError:
        state.ownership_lost = True
        raise HTTPException(503, "Database ownership unavailable; restart required") from None
    if not owned:
        state.ownership_lost = True
        raise HTTPException(503, "Database ownership lost; restart required")


Mutation = Annotated[None, Depends(ownership)]


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


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Apply private-workspace headers and log route templates without user-supplied values."""
    started = time.monotonic()
    response = await call_next(request)
    # Route templates omit identifiers, queries and arbitrary user-controlled paths.
    route = getattr(request.scope.get("route"), "path", "unmatched")
    log.debug(
        "http_request method=%s route=%s status=%s elapsed_ms=%.1f",
        request.method,
        route,
        response.status_code,
        (time.monotonic() - started) * 1000,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "script-src 'self' https://www.sandbox.paypal.com https://www.paypal.com "
        "https://www.paypalobjects.com; "
        "connect-src 'self' https://*.paypal.com https://*.paypalobjects.com; "
        "frame-src https://*.paypal.com https://*.paypalobjects.com; frame-ancestors 'none'"
    )
    return response


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, error: SQLAlchemyError) -> JSONResponse:
    """Classify database failures without logging SQL statements or bound customer data."""
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if isinstance(error, IntegrityError) and sqlstate == "23505":
        log.warning("database_conflict reason=unique_constraint")
        return JSONResponse(
            status_code=409,
            content={"detail": "Concurrent request conflict; inspect the existing record first"},
        )
    # SQLAlchemy exception text includes SQL and bound customer data; log metadata only.
    log.error(
        "database_request_failed method=%s route=%s type=%s sqlstate=%r",
        request.method,
        route,
        type(error).__name__,
        sqlstate,
    )
    unavailable = isinstance(error, OperationalError)
    return JSONResponse(
        status_code=503 if unavailable else 500,
        content={
            "detail": "Database temporarily unavailable"
            if unavailable
            else "Database operation failed"
        },
    )


@app.exception_handler(PayPalError)
async def provider_error(request: Request, error: PayPalError) -> JSONResponse:
    """Expose safe provider diagnostics for explicit reads without retrying them."""
    return JSONResponse(status_code=502, content={"detail": error.code, "debug_id": error.debug_id})


@app.exception_handler(httpx.TransportError)
async def transport_error(request: Request, error: httpx.TransportError) -> JSONResponse:
    """Report provider unavailability without exposing request headers or URLs."""
    return JSONResponse(
        status_code=502, content={"detail": "PayPal connection failed; no automatic retry"}
    )


@app.exception_handler(ReconciliationError)
async def reconciliation_error(request: Request, error: ReconciliationError) -> JSONResponse:
    """Keep unexpected provider data visible without returning raw payloads."""
    return JSONResponse(status_code=502, content={"detail": str(error)})


@api.get("/options")
def options(settings: Config) -> dict[str, Any]:
    """Expose supported choices and the browser-safe PayPal client ID."""
    return {
        "currencies": CURRENCIES,
        "period_limits": PERIOD_LIMITS,
        "environment": settings.paypal_environment,
        "paypal_configured": settings.paypal_configured,
        "paypal_client_id": settings.paypal_client_id,
        "maximum_amount": str(settings.maximum_amount),
    }


@api.post("/payments", dependencies=[Depends(checkout_request)])
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


@api.post("/payments/{identifier}/checkout-outcome", dependencies=[Depends(checkout_request)])
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


@api.post(
    "/payments/{identifier}/attempts/{attempt_id}/cancel", dependencies=[Depends(checkout_request)]
)
async def cancel_checkout_attempt(
    identifier: str,
    attempt_id: str,
    request: Request,
    db: Db,
    user: User,
    guard: Mutation,
) -> dict[str, Any]:
    """Cancel the selected unclaimed attempt through explicit authenticated operator action."""
    return await cancel_attempt(
        db,
        identifier,
        attempt_id,
        user,
        request.app.state.checkout_signer,
    )


@api.get("/checkout-submissions/{intent_key}")
def submission_result(intent_key: str, request: Request, db: Db, user: User) -> dict[str, Any]:
    """Read the latest result after response loss without issuing another mutation."""
    item = db.scalar(
        select(Payment).where(Payment.actor == user.name, Payment.intent_key == intent_key)
    )
    if item is None:
        raise HTTPException(404, "Submission not recorded; no replacement was started")
    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.payment_id == item.id)
        .order_by(Attempt.created_at.desc())
        .limit(1)
    )
    assert attempt is not None
    return attempt_result(attempt, request.app.state.checkout_signer)


@api.post(
    "/payments/{identifier}/attempts/{attempt_id}/close", dependencies=[Depends(checkout_request)]
)
def close_submission(
    identifier: str, attempt_id: str, request: Request, db: Db, user: User, guard: Mutation
) -> dict[str, Any]:
    """End a checkout locally; it never cancels an agreement or repeats a charge."""
    attempt = required(db, Attempt, attempt_id, lock=True)
    if attempt.payment_id != identifier:
        raise HTTPException(409, "Attempt does not belong to this payment")
    return close_checkout(db, attempt, request.app.state.checkout_signer)


@api.get("/checkout-return")
def checkout_return(state: str, request: Request, db: Db, user: User) -> dict[str, Any]:
    """Resolve a signed redirect state before the browser sends its CSRF-protected POST."""
    from itsdangerous import BadData

    try:
        signed = request.app.state.checkout_signer.loads(state, max_age=8 * 60 * 60)
    except BadData:
        raise HTTPException(400, "Invalid or expired checkout state") from None
    if (
        not isinstance(signed, dict)
        or signed.get("actor") != user.name
        or not isinstance(signed.get("attempt"), str)
    ):
        raise HTTPException(403, "Checkout state does not belong to this staff member")
    return attempt_result(
        required(db, Attempt, signed["attempt"]), request.app.state.checkout_signer
    )


@api.get("/payments")
def payments(db: Db, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Search all payments, including unpaid checkouts and refunded collections."""
    result = listing(db, Payment, filters)
    ids = [item["id"] for item in result["items"]]
    latest = (
        {
            attempt.payment_id: attempt
            for attempt in db.scalars(
                select(Attempt)
                .where(Attempt.payment_id.in_(ids))
                .ext(distinct_on(Attempt.payment_id))
                .order_by(Attempt.payment_id, Attempt.created_at.desc())
            )
        }
        if ids
        else {}
    )
    collections = (
        {
            item.payment_id: item
            for item in db.scalars(
                select(Transaction)
                .where(Transaction.payment_id.in_(ids), Transaction.kind == "collection")
                .ext(distinct_on(Transaction.payment_id))
                .order_by(
                    Transaction.payment_id,
                    (Transaction.status == "failed").asc(),
                    Transaction.effective_at.desc(),
                )
            )
        }
        if ids
        else {}
    )
    collection_ids = [item.id for item in collections.values()]
    adjustments = (
        list(db.scalars(select(Transaction).where(Transaction.original_id.in_(collection_ids))))
        if collection_ids
        else []
    )
    for row in result["items"]:
        attempt = latest.get(row["id"])
        collection = collections.get(row["id"])
        row.update(payment_fields(row, collection, adjustments, attempt))
    return result


@api.get("/payments/{identifier}")
def payment_detail(
    identifier: str,
    db: Db,
    attempt_page: Annotated[int, Query(ge=1, le=100000)] = 1,
    attempt_page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    history_page: Annotated[int, Query(ge=1, le=100000)] = 1,
    history_page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Return payment facts and separately paginated attempts and financial history."""
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    payment = record(required(db, Payment, identifier))
    statement = select(Attempt).where(Attempt.payment_id == identifier)
    ordered = statement.order_by(Attempt.created_at.desc(), Attempt.id.desc())
    latest = db.scalar(ordered.limit(1))
    attempts = list(
        db.scalars(ordered.offset((attempt_page - 1) * attempt_page_size).limit(attempt_page_size))
    )
    collection = db.scalar(
        select(Transaction)
        .where(Transaction.payment_id == identifier, Transaction.kind == "collection")
        .order_by(
            (Transaction.status == "failed").asc(),
            Transaction.effective_at.desc(),
            Transaction.id.desc(),
        )
    )
    adjustments = (
        list(db.scalars(select(Transaction).where(Transaction.original_id == collection.id)))
        if collection
        else []
    )
    return {
        **payment,
        **payment_fields(payment, collection, adjustments, latest),
        "attempts": {
            "items": [
                {
                    **record(a),
                    "can_cancel": bool(latest and a.id == latest.id and can_cancel_attempt(a)),
                }
                for a in attempts
            ],
            "total": db.scalar(select(func.count()).select_from(statement.subquery())),
            "page": attempt_page,
            "page_size": attempt_page_size,
        },
        "transactions": listing(
            db,
            Transaction,
            Filters(payment_id=identifier, page=history_page, page_size=history_page_size),
        ),
    }


def payment_fields(
    payment: dict[str, Any],
    collection: Transaction | None,
    adjustments: list[Transaction],
    attempt: Attempt | None,
) -> dict[str, Any]:
    """Join presentation and refund eligibility to the one stable payment identity."""
    if collection and collection.status == "failed" and payment["status"] != "failed":
        collection = None
    result: dict[str, Any] = {
        "amount": payment["price_per_period"],
        "checkout_status": attempt.checkout_status if attempt else None,
        "collection_id": collection.id if collection else None,
        "cancellable_attempt_id": attempt.id if attempt and can_cancel_attempt(attempt) else None,
        "provider_id": collection.provider_id
        if collection
        else attempt.provider_id
        if attempt
        else None,
        "can_refund": False,
        "effective_at": collection.effective_at.isoformat()
        if collection
        else payment["created_at"],
    }
    if collection:
        result.update(
            {
                key: record(collection)[key]
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


def financial_record(db: Session, item: Transaction) -> dict[str, Any]:
    """Serialize a transaction with shared refund eligibility."""
    adjustments = list(db.scalars(select(Transaction).where(Transaction.original_id == item.id)))
    return {**record(item), **refund_eligibility(item, adjustments)}


@api.get("/transactions")
def transactions(db: Db, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Search collections/adjustments with one batched refund-reservation lookup."""
    result = listing(db, Transaction, filters)
    ids = [r["id"] for r in result["items"]]
    items = (
        {item.id: item for item in db.scalars(select(Transaction).where(Transaction.id.in_(ids)))}
        if ids
        else {}
    )
    adjustments = (
        list(db.scalars(select(Transaction).where(Transaction.original_id.in_(ids)))) if ids else []
    )
    for row in result["items"]:
        item = items[row["id"]]
        row.update(refund_eligibility(item, [r for r in adjustments if r.original_id == item.id]))
    return result


@api.get("/transactions/{identifier}")
def transaction_detail(identifier: str, db: Db) -> dict[str, Any]:
    """Inspect collection and adjustment facts; reading performs no provider calls."""
    item = required(db, Transaction, identifier)
    return {
        **financial_record(db, item),
        "adjustments": [
            record(t)
            for t in db.scalars(select(Transaction).where(Transaction.original_id == item.id))
        ],
    }


@api.get("/subscriptions")
def subscriptions(db: Db, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Search known agreements; no automatic renewal synchronization."""
    result = listing(db, Subscription, filters)
    subscription_progress(db, result["items"])
    return result


@api.get("/subscriptions/{identifier}")
def subscription_detail(
    identifier: str,
    db: Db,
    history_page: Annotated[int, Query(ge=1, le=100000)] = 1,
    history_page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Inspect the verified agreement and every page of its recorded financial history."""
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    item = record(required(db, Subscription, identifier))
    subscription_progress(db, [item])
    return {
        **item,
        "transactions": listing(
            db,
            Transaction,
            Filters(subscription_id=identifier, page=history_page, page_size=history_page_size),
        ),
    }


@api.post("/subscriptions/{identifier}/cancel")
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


@api.post("/transactions/{identifier}/refunds")
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


def associate(
    db: Session,
    model: type[Payment] | type[Subscription] | type[Transaction],
    identifier: str,
    data: AssociationInput,
    actor: str,
) -> dict[str, Any]:
    """Update internal client/agreement metadata without changing provider terms."""
    item = required(db, model, identifier, lock=True)
    item.client_name, item.agreement_number = data.client_name, data.agreement_number
    audit(db, actor, "association.updated", identifier)
    db.commit()
    return record(item)


@api.patch("/payments/{identifier}/association")
def payment_association(
    identifier: str, data: AssociationInput, db: Db, user: User, guard: Mutation
) -> dict[str, Any]:
    """Associate an imported payment with internal client metadata."""
    return associate(db, Payment, identifier, data, user.name)


@api.patch("/subscriptions/{identifier}/association")
def subscription_association(
    identifier: str, data: AssociationInput, db: Db, user: User, guard: Mutation
) -> dict[str, Any]:
    """Associate an imported agreement with internal metadata."""
    return associate(db, Subscription, identifier, data, user.name)


@api.patch("/transactions/{identifier}/association")
def transaction_association(
    identifier: str, data: AssociationInput, db: Db, user: User, guard: Mutation
) -> dict[str, Any]:
    """Associate an imported financial record with internal metadata."""
    return associate(db, Transaction, identifier, data, user.name)


@api.get("/statistics")
def stats(db: Db, settings: Config, filters: Annotated[Filters, Query()]) -> dict[str, Any]:
    """Return Decimal statistics separately for each supported currency."""
    return jsonable_encoder(
        statistics(db, filters, settings.paypal_environment),
        custom_encoder={Decimal: lambda v: format(v, ".2f")},
    )


@api.post("/synchronizations")
async def synchronization(
    data: SyncInput, request: Request, db: Db, user: User, settings: Config, guard: Mutation
) -> dict[str, Any]:
    """Perform only the explicitly requested bounded provider reads."""
    try:
        async with asyncio.timeout(120):
            return await synchronize(db, data, request.app.state.paypal, settings)
    except TimeoutError:
        raise HTTPException(
            504, "Read-only synchronization timed out; no automatic continuation"
        ) from None


@api.get("/openapi.json")
def private_openapi() -> dict[str, Any]:
    """Expose API documentation only to authenticated staff/integrations."""
    return app.openapi()


@app.get("/health/live")
def live(user: User) -> dict[str, str]:
    """Report private process liveness."""
    return {"status": "ok"}


@app.get("/health/ready")
def ready(user: User, db: Db, guard: Mutation) -> dict[str, str]:
    """Check the database and pinned ownership without a periodic heartbeat."""
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/")
@app.get("/paypal/success")
@app.get("/paypal/fail")
def frontend() -> FileResponse:
    """Serve the local app/return pages; GET never changes financial state."""
    path = Path(get_settings().frontend_dir) / "index.html"
    if not path.is_file():
        raise HTTPException(503, "Build the frontend first")
    return FileResponse(path)


@app.get("/assets/{asset:path}")
def assets(asset: str) -> FileResponse:
    """Serve only built static assets contained within the assets directory."""
    base = (Path(get_settings().frontend_dir) / "assets").resolve()
    path = (base / asset).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "Asset not found")
    return FileResponse(path)


app.include_router(auth_router)
app.include_router(api)
