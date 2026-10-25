"""PayPal HTTP boundary, OAuth caching and safe diagnostics."""

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from app.config import Settings
from app.errors import ReconciliationError

log = logging.getLogger("payments.paypal")


class PayPalError(Exception):
    """Provider HTTP failure carrying only status, error code and support debug ID."""

    def __init__(self, status: int, code: str, debug_id: str = "") -> None:
        """Retain bounded provider error identifiers without retaining the response body."""
        self.status, self.code, self.debug_id = status, code[:100], debug_id[:128]
        super().__init__(f"PayPal {status}: {self.code}")


class PayPal:
    """Reusable PayPal adapter with cached OAuth and stable mutation request IDs."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Create a reusable HTTP client; an injected transport supports contract tests."""
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.paypal_base_url,
            transport=transport,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
        )
        self.token = ""
        self.token_expiry = 0.0
        self.token_lock = asyncio.Lock()

    async def access_token(self) -> str:
        """Refresh OAuth under a lock and cache the token until shortly before expiry."""
        async with self.token_lock:
            if self.token_expiry <= time.monotonic():
                try:
                    r = await self.client.post(
                        "/v1/oauth2/token",
                        data={"grant_type": "client_credentials"},
                        auth=(self.settings.paypal_client_id, self.settings.paypal_client_secret),
                    )
                except httpx.TransportError as error:
                    log.warning("oauth_transport_error type=%s", type(error).__name__)
                    raise
                self.check(r)
                data = self.json_object(r)
                if (
                    not isinstance(data.get("access_token"), str)
                    or not data["access_token"]
                    or (
                        not isinstance(data.get("expires_in"), (int, float))
                        or data["expires_in"] <= 0
                    )
                ):
                    raise ReconciliationError("PayPal returned an invalid OAuth response")
                self.token = data["access_token"]
                self.token_expiry = time.monotonic() + max(0, data["expires_in"] - 60)
                log.debug("oauth_token_refresh_completed expires_in_seconds=%s", data["expires_in"])
        return self.token

    @staticmethod
    def check(response: httpx.Response) -> None:
        """Raise PayPalError for failed HTTP responses using safe, bounded provider codes."""
        if response.is_error:
            try:
                body = response.json()
            except json.JSONDecodeError, UnicodeDecodeError:
                body = {}
            code = "PROVIDER_ERROR"
            if isinstance(body, dict):
                if isinstance(body.get("name"), str):
                    code = body["name"]
                details = body.get("details")
                if isinstance(details, list) and details and isinstance(details[0], dict):
                    if isinstance(details[0].get("issue"), str):
                        code = details[0]["issue"]
            log.warning(
                "provider_http_error status=%s code=%r debug_id=%r",
                response.status_code,
                code[:100],
                response.headers.get("paypal-debug-id", "")[:128],
            )
            raise PayPalError(
                response.status_code, code, response.headers.get("paypal-debug-id", "")
            )

    @staticmethod
    def json_object(response: httpx.Response) -> dict[str, Any]:
        """Decode an external JSON object or raise a safe reconciliation error."""
        try:
            data = response.json()
        except json.JSONDecodeError, UnicodeDecodeError:
            raise ReconciliationError("PayPal returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ReconciliationError("PayPal returned a non-object response")
        return data

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        request_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]:
        """Call a provider resource with an optional stable mutation key; never retry implicitly."""
        if not path.startswith("/v1/") and not path.startswith("/v2/"):
            raise ValueError("Provider paths must be relative API resources")
        headers = {
            "Authorization": f"Bearer {await self.access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        started = time.monotonic()
        try:
            response = await self.client.request(
                method, path, json=data, params=params, headers=headers
            )
        except httpx.TransportError as error:
            log.warning(
                "provider_transport_error method=%s path=%s operation=%s type=%s elapsed_ms=%.1f",
                method,
                path,
                request_id,
                type(error).__name__,
                (time.monotonic() - started) * 1000,
            )
            raise
        log.debug(
            "provider_request_completed method=%s path=%s operation=%s status=%s "
            "elapsed_ms=%.1f debug_id=%r",
            method,
            path,
            request_id,
            response.status_code,
            (time.monotonic() - started) * 1000,
            response.headers.get("paypal-debug-id", "")[:128],
        )
        if response.status_code == 401:
            # Invalidate for a future explicit action; never replay this request.
            self.token_expiry = 0
        self.check(response)
        return self.json_object(response) if response.content else {}


def provider_id(value: object) -> str:
    """Validate untrusted provider IDs before interpolating them into API paths."""
    if (
        not isinstance(value, str)
        or not value
        or not value.replace("-", "").isalnum()
        or len(value) > 128
    ):
        raise ReconciliationError("Invalid provider ID")
    return value


def failure_code(error: Exception) -> str:
    """Return only safe bounded diagnostics, without provider or SQL payloads."""
    return error.code if isinstance(error, PayPalError) else type(error).__name__


def definite_rejection(error: Exception) -> bool:
    """Only explicit provider input/auth rejection establishes no accepted mutation."""
    return (
        isinstance(error, PayPalError)
        and error.status in {400, 401, 403, 404, 422}
        and error.code not in {"ORDER_ALREADY_CAPTURED", "DUPLICATE_REQUEST_ID"}
    )
