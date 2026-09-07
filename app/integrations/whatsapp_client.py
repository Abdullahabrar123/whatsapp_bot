"""Typed asynchronous client for the Meta WhatsApp Cloud API.

Behaviour that matters in production:

* The access token travels only in the ``Authorization`` header -- never in a
  query string, never in a log line, never in an exception message.
* Retries apply to 429 and retryable 5xx only. A 4xx such as "template does not
  exist" is permanent: retrying it wastes quota and delays the failure report.
* ``Retry-After`` is honoured when Meta supplies it.
* A circuit breaker stops hammering an API that is already failing.
* Every request carries a correlation ID so one customer interaction can be
  traced from webhook to outbound send.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol, Self

import httpx

from app.core.exceptions import CircuitOpenError, WhatsAppAPIError
from app.core.logging import get_correlation_id, get_logger
from app.core.settings import Settings
from app.domain.enums import DeliveryStatus

logger = get_logger(__name__)

#: Meta error codes that indicate throttling or a transient condition.
RETRYABLE_META_CODES: frozenset[int] = frozenset(
    {
        4,  # Application request limit reached
        80007,  # Rate limit issues
        130429,  # Cloud API message throughput reached
        131048,  # Spam rate limit hit
        131056,  # Pair rate limit hit
        133016,  # Rate limit hit during registration
        1,  # Unknown/transient API error
        2,  # Temporary service problem
    }
)

#: Permanent failures. Retrying these never succeeds and burns quota.
PERMANENT_META_CODES: frozenset[int] = frozenset(
    {
        131047,  # Re-engagement required (outside 24h window)
        131051,  # Unsupported message type
        131026,  # Message undeliverable / not a WhatsApp user
        132000,  # Template param count mismatch
        132001,  # Template does not exist
        132005,  # Template hydrated text too long
        132007,  # Template format character policy violated
        132012,  # Template parameter format mismatch
        132015,  # Template is paused
        132016,  # Template is disabled
        131008,  # Required parameter missing
        131009,  # Parameter value invalid
        100,  # Invalid parameter
        190,  # Access token expired/invalid
        131064,  # Messaging limit exceeded due to template classification
    }
)


# ================================================================== responses
@dataclass(frozen=True, slots=True)
class SendResult:
    """Outcome of a successful send call."""

    meta_message_id: str
    recipient_wa_id: str
    status: DeliveryStatus = DeliveryStatus.SENT
    raw: dict[str, Any] = field(default_factory=dict)


# ============================================================ circuit breaker
class CircuitBreaker:
    """Minimal three-state breaker (closed -> open -> half-open)."""

    def __init__(self, *, fail_threshold: int, reset_seconds: float) -> None:
        self._fail_threshold = fail_threshold
        self._reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        # Once the reset window elapses the breaker is half-open, so the next
        # request is allowed through as a trial.
        return (time.monotonic() - self._opened_at) < self._reset_seconds

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "open" if self.is_open else "half_open"

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._fail_threshold:
            self._opened_at = time.monotonic()

    def reset(self) -> None:
        self._failures = 0
        self._opened_at = None


# =================================================================== interface
class WhatsAppClientProtocol(Protocol):
    """Interface the services depend on, so Meta can be faked in tests."""

    async def send_text(
        self, to: str, body: str, *, preview_url: bool = False
    ) -> SendResult: ...

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        *,
        body_variables: list[str] | None = None,
    ) -> SendResult: ...

    async def send_interactive_buttons(
        self, to: str, body: str, buttons: list[dict[str, str]], *, header: str | None = None
    ) -> SendResult: ...

    async def send_interactive_list(
        self,
        to: str,
        body: str,
        button_text: str,
        sections: list[dict[str, Any]],
        *,
        header: str | None = None,
    ) -> SendResult: ...

    async def mark_as_read(self, meta_message_id: str) -> bool: ...

    async def health_check(self) -> bool: ...


# ====================================================================== client
class WhatsAppClient:
    """Async Cloud API client with retry, backoff and circuit breaking."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.meta_request_timeout_seconds),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._breaker = CircuitBreaker(
            fail_threshold=settings.meta_circuit_fail_threshold,
            reset_seconds=settings.meta_circuit_reset_seconds,
        )

    # -------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def circuit_state(self) -> str:
        return self._breaker.state

    # ---------------------------------------------------------------- headers
    def _headers(self) -> dict[str, str]:
        """Auth header only. The token is never placed in a URL or a log."""
        headers = {
            "Authorization": f"Bearer {self._settings.meta_access_token.get_secret_value()}",
            "Content-Type": "application/json",
        }
        correlation = get_correlation_id()
        if correlation:
            headers["X-Correlation-Id"] = correlation
        return headers

    # ------------------------------------------------------------ error mapping
    @staticmethod
    def _map_error(response: httpx.Response) -> WhatsAppAPIError:
        """Turn a Meta error response into a typed, retry-aware exception."""
        status = response.status_code
        meta_code: int | None = None
        meta_subcode: int | None = None
        message = f"Meta API returned HTTP {status}"
        details: dict[str, Any] = {}

        try:
            body = response.json()
        except ValueError:
            body = {}

        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                raw_code = error.get("code")
                raw_subcode = error.get("error_subcode")
                meta_code = int(str(raw_code)) if str(raw_code).isdigit() else None
                meta_subcode = int(str(raw_subcode)) if str(raw_subcode).isdigit() else None
                # Meta's own message text is safe: it never contains our token.
                message = str(error.get("message") or message)[:512]
                details = {
                    "type": error.get("type"),
                    "details": str(
                        (error.get("error_data") or {}).get("details", "")
                    )[:512],
                    "fbtrace_id": error.get("fbtrace_id"),
                }

        if meta_code in PERMANENT_META_CODES:
            retryable = False
        elif meta_code in RETRYABLE_META_CODES:
            retryable = True
        else:
            # Default by HTTP class: throttling and server faults are retryable.
            retryable = status == 429 or 500 <= status < 600

        retry_after: float | None = None
        header_value = response.headers.get("Retry-After")
        if header_value:
            try:
                retry_after = float(header_value)
            except ValueError:
                retry_after = None

        return WhatsAppAPIError(
            message,
            status_code=status,
            meta_code=meta_code,
            meta_subcode=meta_subcode,
            retryable=retryable,
            retry_after=retry_after,
            details=details,
        )

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        """Exponential backoff with full jitter, capped, honouring Retry-After."""
        if retry_after is not None:
            return min(retry_after, 60.0)
        base = min(2.0**attempt, 30.0)
        return random.uniform(0, base)  # noqa: S311 - retry jitter, not crypto  # nosec B311

    # ---------------------------------------------------------------- request
    async def _request(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform a request with retry/backoff and circuit breaking."""
        if self._breaker.is_open:
            raise CircuitOpenError(
                "Meta API circuit breaker is open; refusing to send.",
                details={"circuit": self._breaker.state},
            )

        last_error: WhatsAppAPIError | None = None
        attempts = self._settings.meta_max_retries + 1

        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, url, json=json_body, headers=self._headers()
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Network-level failure: transient by nature.
                last_error = WhatsAppAPIError(
                    f"Network error contacting Meta API: {type(exc).__name__}",
                    status_code=0,
                    retryable=True,
                )
                self._breaker.record_failure()
            else:
                if response.is_success:
                    self._breaker.record_success()
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    return payload if isinstance(payload, dict) else {}

                error = self._map_error(response)
                last_error = error
                if error.retryable:
                    self._breaker.record_failure()
                else:
                    # A permanent error is not an infrastructure fault; it must
                    # not push the breaker toward open.
                    self._breaker.record_success()
                    logger.warning(
                        "meta_api_permanent_error",
                        status_code=error.status_code,
                        meta_code=error.meta_code,
                        attempt=attempt + 1,
                    )
                    raise error

            if attempt < attempts - 1:
                delay = self._backoff_delay(attempt, getattr(last_error, "retry_after", None))
                logger.info(
                    "meta_api_retry",
                    attempt=attempt + 1,
                    max_attempts=attempts,
                    delay_seconds=round(delay, 3),
                    status_code=getattr(last_error, "status_code", None),
                    meta_code=getattr(last_error, "meta_code", None),
                )
                await self._sleep(delay)

        if last_error is None:  # pragma: no cover - loop always sets it
            raise WhatsAppAPIError(
                "Meta request failed without a recorded error.", status_code=502
            )
        logger.error(
            "meta_api_exhausted",
            attempts=attempts,
            status_code=last_error.status_code,
            meta_code=last_error.meta_code,
        )
        raise last_error

    # ------------------------------------------------------------ send helpers
    @staticmethod
    def _extract_result(payload: dict[str, Any], fallback_to: str) -> SendResult:
        messages = payload.get("messages")
        contacts = payload.get("contacts")
        message_id = ""
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            message_id = str(messages[0].get("id") or "")
        recipient = fallback_to
        if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict):
            recipient = str(contacts[0].get("wa_id") or fallback_to)
        return SendResult(
            meta_message_id=message_id,
            recipient_wa_id=recipient,
            status=DeliveryStatus.SENT,
            raw=payload,
        )

    async def _send(self, payload: dict[str, Any], to: str) -> SendResult:
        result = await self._request(
            "POST", self._settings.messages_endpoint, json_body=payload
        )
        send_result = self._extract_result(result, to)
        logger.info(
            "whatsapp_message_sent",
            meta_message_id=send_result.meta_message_id,
            message_kind=payload.get("type"),
        )
        return send_result

    # ------------------------------------------------------------------ public
    async def send_text(self, to: str, body: str, *, preview_url: bool = False) -> SendResult:
        """Send a free-form text message (only valid inside the service window)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body[:4096], "preview_url": preview_url},
        }
        return await self._send(payload, to)

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        *,
        body_variables: list[str] | None = None,
    ) -> SendResult:
        """Send an approved template message."""
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language},
        }
        if body_variables:
            template["components"] = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(value)} for value in body_variables
                    ],
                }
            ]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template,
        }
        return await self._send(payload, to)

    async def send_interactive_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict[str, str]],
        *,
        header: str | None = None,
    ) -> SendResult:
        """Send up to three reply buttons (Cloud API maximum)."""
        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": item["id"][:256], "title": item["title"][:20]},
                    }
                    for item in buttons[:3]
                ]
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self._send(payload, to)

    async def send_interactive_list(
        self,
        to: str,
        body: str,
        button_text: str,
        sections: list[dict[str, Any]],
        *,
        header: str | None = None,
    ) -> SendResult:
        """Send a list picker (max 10 rows across all sections)."""
        interactive: dict[str, Any] = {
            "type": "list",
            "body": {"text": body[:1024]},
            "action": {"button": button_text[:20], "sections": sections[:10]},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self._send(payload, to)

    async def mark_as_read(self, meta_message_id: str) -> bool:
        """Mark an inbound message as read. Failure here is never fatal."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": meta_message_id,
        }
        try:
            await self._request("POST", self._settings.messages_endpoint, json_body=payload)
        except (WhatsAppAPIError, CircuitOpenError) as exc:
            logger.info("mark_as_read_failed", error_code=getattr(exc, "code", "unknown"))
            return False
        return True

    async def health_check(self) -> bool:
        """Check the configured phone number is reachable and the token works."""
        if not self._settings.meta_is_configured():
            return False
        url = f"{self._settings.graph_api_root}/{self._settings.meta_phone_number_id}"
        try:
            await self._request("GET", url)
        except (WhatsAppAPIError, CircuitOpenError):
            return False
        return True
