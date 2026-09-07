"""Security primitives: webhook signatures, field encryption, auth, SSRF guard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import socket
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from app.core.exceptions import SignatureVerificationError, SSRFError

SIGNATURE_HEADER = "X-Hub-Signature-256"
_SIGNATURE_PREFIX = "sha256="


# ===================================================================== webhooks
def compute_signature(payload: bytes, app_secret: str) -> str:
    """Compute the Meta ``X-Hub-Signature-256`` value for a raw request body.

    Meta signs the *exact bytes* it transmitted. Re-serialising parsed JSON
    changes key order and whitespace and will not match, so callers must pass
    the untouched body.
    """
    digest = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_webhook_signature(payload: bytes, header_value: str | None, app_secret: str) -> None:
    """Verify an inbound Meta webhook signature.

    Raises:
        SignatureVerificationError: if the header is missing, malformed, or does
            not match. Comparison is constant-time to avoid timing oracles.
    """
    if not app_secret:
        raise SignatureVerificationError(
            "META_APP_SECRET is not configured; refusing to accept webhooks."
        )
    if not header_value:
        raise SignatureVerificationError("Missing X-Hub-Signature-256 header.")
    if not header_value.startswith(_SIGNATURE_PREFIX):
        raise SignatureVerificationError("Malformed X-Hub-Signature-256 header.")

    expected = compute_signature(payload, app_secret)
    if not hmac.compare_digest(expected, header_value):
        raise SignatureVerificationError("Webhook signature mismatch.")


def verify_verify_token(candidate: str | None, expected: str) -> bool:
    """Constant-time check of the webhook subscription verify token."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate, expected)


def verify_meta_signature(payload: bytes, header_value: str | None, app_secret: str) -> bool:
    """Convenience boolean helper for signature verification."""
    try:
        verify_webhook_signature(payload, header_value, app_secret)
        return True
    except SignatureVerificationError:
        return False


# =================================================================== admin auth
def verify_admin_key(candidate: str | None, expected: str) -> bool:
    """Constant-time comparison of the operator API key."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate, expected)


# ============================================================ field encryption
def _derive_fernet_key(secret: str, *, salt: bytes = b"wa-bot-field-v1") -> bytes:
    """Derive a stable 32-byte Fernet key from the application secret."""
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200_000, dklen=32)
    return base64.urlsafe_b64encode(digest)


class FieldCipher:
    """Symmetric encryption for sensitive database columns.

    Used for message bodies and raw phone numbers so a database dump alone does
    not disclose customer content. The key is derived from ``APP_SECRET_KEY``;
    rotating that value requires the documented re-encryption procedure.
    """

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("A non-empty secret is required for field encryption.")
        self._fernet = Fernet(_derive_fernet_key(secret))

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str | None:
        if ciphertext is None:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            # Never raise into a request path over unreadable stored data.
            return None


# ===================================================================== SSRF
_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
_ALLOWED_SCHEMES = {"https"}


def assert_safe_outbound_url(url: str, *, allow_http: bool = False) -> None:
    """Validate a URL before the application fetches it.

    Blocks non-HTTP(S) schemes and any host resolving to a private, loopback,
    link-local or reserved address, which is the standard SSRF pivot.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if scheme in _BLOCKED_SCHEMES or not scheme:
        raise SSRFError(f"Blocked URL scheme: {scheme or '(none)'}")
    allowed = _ALLOWED_SCHEMES | ({"http"} if allow_http else set())
    if scheme not in allowed:
        raise SSRFError(f"URL scheme {scheme!r} is not permitted.")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host component.")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve host {host!r}.") from exc

    for info in infos:
        address = info[4][0]
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SSRFError(f"Host {host!r} resolves to a blocked address range.")
