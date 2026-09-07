"""Redaction helpers.

Two rules govern everything written to a log or returned by the operator API:

1. Secrets never appear -- not partially, not hashed-in-place, not at all.
2. Personal data is minimised: phone numbers are masked and, where an operator
   needs a stable join key, replaced by a keyed HMAC pseudonym.

The functions here are pure so they can be unit-tested exhaustively.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

MASK = "[REDACTED]"

#: Keys whose values must never be logged, matched case-insensitively as substrings.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "signature",
    "x-hub-signature",
    "access_token",
    "private",
    "cookie",
    "session",
)

#: Keys holding raw personal data that must be masked rather than removed.
PII_KEY_PARTS: tuple[str, ...] = (
    "phone",
    "wa_id",
    "msisdn",
    "recipient",
    "from",
    "to",
    "email",
    "address",
)

# A bare international number, with or without a leading +.
_PHONE_RE = re.compile(r"\+?\d{7,15}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Meta long-lived tokens and generic bearer material.
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")
_META_TOKEN_RE = re.compile(r"\bEA[A-Za-z0-9]{20,}\b")

_MAX_DEPTH = 6


def mask_phone(value: str) -> str:
    """Mask a phone number keeping only country prefix and last two digits.

    ``+447700900123`` -> ``+44*******23``. Never returns the full number.
    """
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 5:
        return "*" * len(digits)
    prefix = digits[:2]
    suffix = digits[-2:]
    hidden = "*" * max(1, len(digits) - 4)
    lead = "+" if value.strip().startswith("+") else ""
    return f"{lead}{prefix}{hidden}{suffix}"


def pseudonymise(value: str, *, key: str) -> str:
    """Return a stable, non-reversible identifier for operational correlation.

    Uses HMAC-SHA256 with the application secret so that two deployments cannot
    cross-reference the same person, and a leaked log cannot be brute-forced
    back to a phone number without the key.
    """
    digest = hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256)
    return f"ph_{digest.hexdigest()[:16]}"


def redact_text(text: str) -> str:
    """Strip obvious secrets and personal identifiers out of free text."""
    if not text:
        return text
    result = _BEARER_RE.sub(f"Bearer {MASK}", text)
    result = _META_TOKEN_RE.sub(MASK, result)
    result = _EMAIL_RE.sub(MASK, result)
    return _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), result)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _is_pii(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in PII_KEY_PARTS)


def redact_value(key: str, value: Any, *, depth: int = 0) -> Any:
    """Redact a single key/value pair according to its key name and content."""
    if depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if _is_sensitive(key):
        return MASK
    if isinstance(value, dict):
        return redact_mapping(value, depth=depth + 1)
    if isinstance(value, list | tuple):
        return [redact_value(key, item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if _is_pii(key):
            return mask_phone(value) if any(c.isdigit() for c in value) else MASK
        return redact_text(value)
    return value


def redact_mapping(data: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    """Recursively redact a mapping destined for a log line or API response."""
    if depth > _MAX_DEPTH:
        return {"_": "[TRUNCATED]"}
    return {key: redact_value(key, value, depth=depth) for key, value in data.items()}
