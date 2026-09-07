"""Phone number normalisation and pseudonymisation.

Every phone number entering the system passes through :func:`normalise_phone`
exactly once, at the boundary. Internally only the E.164 form, its keyed hash
and its mask are used.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException

from app.core.exceptions import ValidationError
from app.core.redaction import mask_phone


@dataclass(frozen=True, slots=True)
class NormalisedPhone:
    """A validated phone number in the three forms the application uses."""

    e164: str
    hash: str
    masked: str
    country_code: int
    region: str | None


def hash_phone(e164: str, *, key: str) -> str:
    """Keyed HMAC-SHA256 of an E.164 number, used as the lookup identifier.

    A keyed hash rather than a plain digest: a plain SHA-256 of a phone number
    is trivially reversible by brute force over the small number space.
    """
    return hmac.new(key.encode("utf-8"), e164.encode("utf-8"), hashlib.sha256).hexdigest()


def normalise_phone(
    raw: str,
    *,
    key: str,
    default_region: str | None = None,
) -> NormalisedPhone:
    """Parse and validate a phone number into E.164.

    Args:
        raw: The number as supplied by an operator or by Meta.
        key: HMAC key (the application secret) used for the lookup hash.
        default_region: ISO country used when ``raw`` has no ``+`` prefix.

    Raises:
        ValidationError: when the number is unparseable or not a valid number.
            The message never contains the full number.
    """
    if raw is None or not str(raw).strip():
        raise ValidationError("Phone number is empty.")

    candidate = str(raw).strip()
    # Meta sends wa_id without a leading '+'; treat a bare international number
    # as already being in international form.
    if not candidate.startswith("+") and candidate.isdigit() and default_region is None:
        candidate = f"+{candidate}"

    try:
        parsed = phonenumbers.parse(candidate, default_region)
    except NumberParseException as exc:
        raise ValidationError(
            f"Could not parse phone number ({mask_phone(candidate)}): {exc.args[0]}"
        ) from exc

    if not phonenumbers.is_possible_number(parsed):
        raise ValidationError(f"Phone number has an impossible length ({mask_phone(candidate)}).")
    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError(f"Phone number is not valid ({mask_phone(candidate)}).")

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return NormalisedPhone(
        e164=e164,
        hash=hash_phone(e164, key=key),
        masked=mask_phone(e164),
        country_code=parsed.country_code or 0,
        region=phonenumbers.region_code_for_number(parsed),
    )


def to_wa_id(e164: str) -> str:
    """Convert E.164 to the digits-only form the Cloud API expects."""
    return e164.lstrip("+")
