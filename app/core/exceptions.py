"""Structured application exceptions.

Every exception carries a stable ``code`` so that logs, metrics and the operator
API can group failures without parsing human-readable text.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every error raised deliberately by this application."""

    code: str = "app_error"
    http_status: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


# --------------------------------------------------------------- configuration
class ConfigurationError(AppError):
    """Raised at startup when configuration is missing, unreadable or invalid."""

    code = "configuration_error"
    http_status = 500


# --------------------------------------------------------------- security
class SignatureVerificationError(AppError):
    """Raised when an inbound webhook signature is absent or does not match."""

    code = "invalid_signature"
    http_status = 403


class AuthenticationError(AppError):
    code = "unauthenticated"
    http_status = 401


class AuthorizationError(AppError):
    code = "forbidden"
    http_status = 403


class PayloadTooLargeError(AppError):
    code = "payload_too_large"
    http_status = 413


# --------------------------------------------------------------- domain
class ValidationError(AppError):
    code = "validation_error"
    http_status = 422


class ConsentError(AppError):
    """Raised when a send is attempted without valid, current opt-in."""

    code = "consent_error"
    http_status = 409


class SuppressionError(AppError):
    """Raised when a send targets a permanently suppressed recipient."""

    code = "suppressed_recipient"
    http_status = 409


class TemplateError(AppError):
    code = "template_error"
    http_status = 409


class QuietHoursError(AppError):
    code = "quiet_hours"
    http_status = 409


# --------------------------------------------------------------- integrations
class IntegrationError(AppError):
    code = "integration_error"
    http_status = 502


class WhatsAppAPIError(IntegrationError):
    """Error returned by the Meta Cloud API.

    ``retryable`` distinguishes throttling / transient server faults from
    permanent client errors which must never be retried.
    """

    code = "whatsapp_api_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        meta_code: int | None = None,
        meta_subcode: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.status_code = status_code
        self.meta_code = meta_code
        self.meta_subcode = meta_subcode
        self.retryable = retryable
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "status_code": self.status_code,
                "meta_code": self.meta_code,
                "meta_subcode": self.meta_subcode,
                "retryable": self.retryable,
            }
        )
        return data


class CircuitOpenError(IntegrationError):
    """Raised while the circuit breaker is open and calls are short-circuited."""

    code = "circuit_open"
    retryable = True


class LLMError(IntegrationError):
    code = "llm_error"
    retryable = True


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    retryable = True


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"
    retryable = True


class SSRFError(AppError):
    """Raised when a URL fails egress validation."""

    code = "ssrf_blocked"
    http_status = 400
