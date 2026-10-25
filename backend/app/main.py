"""Private API composition, security boundaries and static frontend serving."""

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import ResponseValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import (
    IntegrityError,
    OperationalError,
    SQLAlchemyError,
)

from app.auth import User, authenticate
from app.auth import router as auth_router
from app.config import get_settings
from app.db import Db, Mutation
from app.errors import ReconciliationError
from app.lifecycle import lifespan
from app.paypal import PayPalError
from app.responses import HealthResponse
from app.routes.payments import router as payments_router
from app.routes.reporting import router as reporting_router
from app.routes.subscriptions import router as subscriptions_router

log = logging.getLogger("payments.api")


app = FastAPI(
    title="Internal PayPal Payments",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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
        log.warning(
            "database_conflict method=%s route=%s reason=unique_constraint", request.method, route
        )
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
    route = getattr(request.scope.get("route"), "path", "unmatched")
    # Reconciliation messages are application-authored rules, never provider payloads.
    log.warning("provider_verification_failed route=%s reason=%s", route, error)
    return JSONResponse(status_code=502, content={"detail": str(error)})


@app.exception_handler(ResponseValidationError)
async def response_validation_error(
    request: Request, error: ResponseValidationError
) -> JSONResponse:
    """Expose a response defect without logging customer values or replaying an action."""
    route = getattr(request.scope.get("route"), "path", "unmatched")
    log.error("response_contract_failed method=%s route=%s", request.method, route)
    return JSONResponse(
        status_code=500, content={"detail": "Invalid server response; inspect the saved result"}
    )


@app.get("/api/v1/openapi.json", dependencies=[Depends(authenticate)])
def private_openapi() -> dict[str, Any]:
    """Expose API documentation only to authenticated staff/integrations."""
    return app.openapi()


@app.get("/health/live", response_model=HealthResponse)
def live(user: User) -> dict[str, str]:
    """Report private process liveness."""
    return {"status": "ok"}


@app.get("/health/ready", response_model=HealthResponse)
async def ready(user: User, db: Db, guard: Mutation) -> dict[str, str]:
    """Check the database and pinned ownership without a periodic heartbeat."""
    await db.execute(text("SELECT 1"))
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
app.include_router(payments_router)
app.include_router(subscriptions_router)
app.include_router(reporting_router)
