"""Single-access staff authentication and browser CSRF protection."""

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadData, URLSafeTimedSerializer
from pwdlib import PasswordHash

from app.models import Payment
from app.schemas import LoginInput, SessionOptions, StaffSession
from app.settings import Settings, get_settings

password_hash = PasswordHash.recommended()
log = logging.getLogger("payments.auth")
login_attempts: dict[str, list[float]] = {}
router = APIRouter(prefix="/api/v1/auth")
SESSION_SECONDS = 8 * 60 * 60
Config = Annotated[Settings, Depends(get_settings)]


@dataclass
class Principal:
    """Authenticated staff or API-key identity; never contains credentials."""

    name: str
    csrf: str = ""


def require_staff_ip(request: Request, settings: Config) -> None:
    """Restrict browser access using the ASGI peer, never arbitrary forwarded headers."""
    if not settings.allowed_ips:
        return
    try:
        source = ip_address(request.client.host) if request.client else None
    except ValueError:
        source = None
    # Only Uvicorn's explicitly trusted proxy handling may replace the socket peer.
    if source not in settings.allowed_ips:
        log.warning("staff_access_rejected reason=source_ip")
        raise HTTPException(403, "Staff access is not allowed from this IP")


def authenticate(request: Request, settings: Config) -> Principal:
    """Resolve an API key or signed session and enforce browser CSRF protection."""
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        for name, key in settings.api_keys.items():
            if secrets.compare_digest(digest, key["sha256"]):
                return Principal(name)
        log.debug("authentication_rejected reason=invalid_bearer")
        raise HTTPException(401, "Invalid credentials")
    require_staff_ip(request, settings)
    try:
        data = request.app.state.session_signer.loads(
            request.cookies.get("staff_session", ""), max_age=SESSION_SECONDS
        )
    except BadData:
        log.debug("authentication_rejected reason=invalid_session")
        raise HTTPException(401, "Authentication required") from None
    if not isinstance(data, dict) or not all(
        isinstance(data.get(field), str) for field in ("name", "version", "csrf")
    ):
        raise HTTPException(401, "Invalid session")
    if not data["version"].isascii() or not data["csrf"].isascii():
        raise HTTPException(401, "Invalid session")
    user = settings.auth_users.get(data["name"])
    # A password change invalidates existing sessions without a separate session table.
    if not user or not secrets.compare_digest(
        data["version"], hashlib.sha256(user["password_hash"].encode()).hexdigest()
    ):
        raise HTTPException(401, "Session expired")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
        if (
            request.headers.get("origin") not in settings.allowed_origins
            or not csrf.isascii()
            or not secrets.compare_digest(csrf, data["csrf"])
        ):
            log.warning("authentication_rejected reason=invalid_csrf_or_origin")
            raise HTTPException(403, "Invalid request origin or CSRF token")
    return Principal(data["name"], data["csrf"])


User = Annotated[Principal, Depends(authenticate)]


def session_response(user: Principal, settings: Settings) -> StaffSession:
    """Assemble public session configuration without exposing provider credentials."""
    return StaffSession(
        name=user.name,
        csrf=user.csrf,
        options=SessionOptions(
            environment=settings.paypal_environment,
            paypal_configured=settings.paypal_configured,
            paypal_client_id=settings.paypal_client_id,
            maximum_amount=str(settings.maximum_amount),
        ),
    )


@router.post("/login", dependencies=[Depends(require_staff_ip)])
def login(
    data: LoginInput,
    request: Request,
    response: Response,
    settings: Config,
) -> StaffSession:
    """Authenticate staff and issue a signed, HttpOnly session with a separate CSRF token."""
    if request.headers.get("origin") not in settings.allowed_origins:
        log.warning("staff_login_rejected reason=invalid_origin")
        raise HTTPException(403, "Invalid origin")
    source = request.client.host if request.client else "unknown"
    current = time.monotonic()
    recent = [t for t in login_attempts.get(source, []) if t > current - 60]
    if len(recent) >= 10:
        log.warning("login_rate_limited")
        raise HTTPException(429, "Too many login attempts; retry in one minute")
    recent.append(current)
    login_attempts[source] = recent
    if len(login_attempts) > 2000:
        login_attempts.clear()
    user = settings.auth_users.get(data.username)
    if not user or not password_hash.verify(data.password, user["password_hash"]):
        log.info("staff_login_rejected")
        raise HTTPException(401, "Invalid username or password")
    csrf = secrets.token_urlsafe(32)
    session = request.app.state.session_signer.dumps(
        {
            "name": data.username,
            "csrf": csrf,
            "version": hashlib.sha256(user["password_hash"].encode()).hexdigest(),
        }
    )
    response.set_cookie(
        "staff_session",
        session,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=SESSION_SECONDS,
        path="/",
    )
    log.info("staff_login_succeeded")
    return session_response(Principal(data.username, csrf), settings)


@router.get("/me")
def me(user: User, settings: Config) -> StaffSession:
    """Return identity, CSRF and public configuration in one authenticated read."""
    return session_response(user, settings)


@router.post("/logout", dependencies=[Depends(authenticate)])
def logout(response: Response) -> dict[str, bool]:
    """Expire the browser session after authentication and CSRF validation."""
    response.delete_cookie("staff_session", path="/")
    log.info("staff_logout")
    return {"ok": True}


def checkout_proof(signer: URLSafeTimedSerializer, payment: Payment) -> str:
    """Sign the exact payment and initiating operator without a child identity."""
    return signer.dumps({"payment": payment.id, "actor": payment.actor})
