"""Pure compliance and scheduling rules.

Everything here is a deterministic function of its inputs -- no database, no
network, no clock reads except the one passed in. That makes the rules that
govern consent and sending exhaustively unit-testable, which is exactly where
correctness matters most.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.config_schema import BotConfig, BusinessConfig, TemplateDefinition
from app.domain.enums import (
    ConsentPurpose,
    ConsentStatus,
    Intent,
    TemplateCategory,
    TemplateStatus,
)

# Strip punctuation/emoji so "STOP!" and "stop." both match.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

#: An opt-out is only honoured as a standalone command up to this many words,
#: so "please don't stop sending these" is not misread as an opt-out.
MAX_OPTOUT_WORDS = 4


def normalise_keyword_text(text: str) -> str:
    """Lowercase, strip accents and punctuation, and collapse whitespace."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _PUNCT_RE.sub(" ", without_marks.lower())
    return _WS_RE.sub(" ", cleaned).strip()


def _matches_keyword(text: str, keywords: list[str]) -> str | None:
    """Return the matched keyword, treating the message as a whole command."""
    normalised = normalise_keyword_text(text)
    if not normalised:
        return None
    words = normalised.split()

    for keyword in keywords:
        candidate = normalise_keyword_text(keyword)
        if not candidate:
            continue
        if normalised == candidate:
            return keyword
        # Allow a short message that contains the phrase, e.g. "please stop".
        if len(words) <= MAX_OPTOUT_WORDS and re.search(
            rf"(?:^|\s){re.escape(candidate)}(?:\s|$)", normalised
        ):
            return keyword
    return None


def detect_opt_out(text: str, bot: BotConfig) -> str | None:
    """Return the matched stop word if the message is an opt-out command."""
    return _matches_keyword(text, bot.stop_words)


def detect_opt_in(text: str, bot: BotConfig) -> str | None:
    """Return the matched keyword if the message is an explicit re-subscribe."""
    return _matches_keyword(text, bot.opt_in_keywords)


def detect_handover_request(text: str, bot: BotConfig) -> str | None:
    """Return the matched keyword if the customer asked for a human.

    Unlike opt-out this is matched anywhere in the message, because a request
    for a person is usually embedded in a sentence.
    """
    normalised = normalise_keyword_text(text)
    if not normalised:
        return None
    for keyword in bot.handover_keywords:
        candidate = normalise_keyword_text(keyword)
        if candidate and re.search(
            rf"(?:^|\s){re.escape(candidate)}(?:\s|$)", normalised
        ):
            return keyword
    return None


# ==================================================================== schedule
def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def is_within_business_hours(moment: datetime, business: BusinessConfig) -> bool:
    """True if ``moment`` falls inside opening hours in the business timezone."""
    local = moment.astimezone(business.tzinfo())
    hours = business.business_hours.for_weekday(local.weekday())
    if hours.closed:
        return False
    return hours.as_open() <= local.time() < hours.as_close()


def is_within_quiet_hours(moment: datetime, business: BusinessConfig, bot: BotConfig) -> bool:
    """True if business-initiated sending is forbidden right now.

    Handles windows that cross midnight (e.g. 21:00 -> 08:00).
    """
    quiet = bot.quiet_hours
    if not quiet.enabled:
        return False
    local_time = moment.astimezone(business.tzinfo()).time()
    start = _parse_hhmm(quiet.start)
    end = _parse_hhmm(quiet.end)
    if start == end:
        return False
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def next_allowed_send_time(
    moment: datetime, business: BusinessConfig, bot: BotConfig
) -> datetime:
    """Return the earliest time at or after ``moment`` outside quiet hours."""
    if not is_within_quiet_hours(moment, business, bot):
        return moment
    tz: ZoneInfo = business.tzinfo()
    local = moment.astimezone(tz)
    end = _parse_hhmm(bot.quiet_hours.end)
    candidate = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(moment.tzinfo)


# ================================================================= service window
#: Meta's customer-service window: free-form replies are allowed for 24 hours
#: after the customer's last message. Outside it, only templates may be sent.
SERVICE_WINDOW = timedelta(hours=24)


def service_window_open(last_inbound_at: datetime | None, now: datetime) -> bool:
    """True when free-form (non-template) replies are permitted."""
    if last_inbound_at is None:
        return False
    return (now - last_inbound_at) < SERVICE_WINDOW


# ==================================================================== eligibility
@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """Outcome of the pre-send compliance gate."""

    allowed: bool
    reason: str
    code: str = "ok"

    @classmethod
    def ok(cls) -> EligibilityDecision:
        return cls(allowed=True, reason="Eligible", code="ok")

    @classmethod
    def deny(cls, code: str, reason: str) -> EligibilityDecision:
        return cls(allowed=False, reason=reason, code=code)


@dataclass(frozen=True, slots=True)
class SendContext:
    """Everything the eligibility gate needs to judge one business-initiated send."""

    consent_status: ConsentStatus
    consent_purpose: ConsentPurpose | None
    suppressed: bool
    template: TemplateDefinition | None
    now: datetime
    last_inbound_at: datetime | None = None
    already_sent_in_campaign: bool = False
    contact_locale: str | None = None


def evaluate_send_eligibility(
    context: SendContext,
    business: BusinessConfig,
    bot: BotConfig,
) -> EligibilityDecision:
    """Decide whether one business-initiated message may be sent.

    The checks are ordered so the most absolute prohibitions are evaluated
    first. Every path returns an explicit decision -- there is no implicit
    "allow" and no way to reach a send without passing through here.
    """
    # 1. Suppression is permanent and overrides everything, including consent.
    if context.suppressed:
        return EligibilityDecision.deny(
            "suppressed", "Recipient is on the permanent suppression list."
        )

    # 2. An opt-out is honoured immediately and is not overridden by an old
    #    opt-in record.
    if context.consent_status in (ConsentStatus.OPTED_OUT, ConsentStatus.SUPPRESSED):
        return EligibilityDecision.deny(
            "opted_out", "Recipient has opted out of business-initiated messages."
        )

    # 3. Campaigns can be disabled globally by feature flag.
    if not bot.features.campaigns_enabled:
        return EligibilityDecision.deny(
            "campaigns_disabled", "Business-initiated sending is disabled by feature flag."
        )

    # 4. Deduplication within a campaign.
    if context.already_sent_in_campaign:
        return EligibilityDecision.deny(
            "duplicate", "Recipient has already received this campaign."
        )

    # 5. A template is mandatory for business-initiated messages.
    template = context.template
    if template is None:
        return EligibilityDecision.deny(
            "no_template", "No template mapped for this recipient."
        )
    if template.status is not TemplateStatus.APPROVED:
        return EligibilityDecision.deny(
            "template_not_approved",
            f"Template {template.name!r} has status {template.status.value}, not APPROVED.",
        )

    # 6. Marketing always requires a recorded opt-in, regardless of any
    #    open service window.
    if template.category is TemplateCategory.MARKETING:
        if context.consent_status is not ConsentStatus.OPTED_IN:
            return EligibilityDecision.deny(
                "no_opt_in",
                "Marketing template requires a recorded opt-in; "
                f"recipient status is {context.consent_status.value}.",
            )
        if (
            context.consent_purpose is not None
            and context.consent_purpose is not ConsentPurpose.MARKETING
        ):
            return EligibilityDecision.deny(
                "wrong_consent_purpose",
                f"Recorded consent purpose is {context.consent_purpose.value}, "
                "which does not cover marketing.",
            )
    elif template.requires_opt_in and context.consent_status is not ConsentStatus.OPTED_IN:
        # 7. Utility templates that the operator marked as needing opt-in.
        return EligibilityDecision.deny(
            "no_opt_in",
            f"Template {template.name!r} requires opt-in; "
            f"recipient status is {context.consent_status.value}.",
        )

    # 8. Language must be one the business actually supports.
    if template.language not in business.supported_languages:
        return EligibilityDecision.deny(
            "unsupported_language",
            f"Template language {template.language!r} is not supported by this business.",
        )

    # 9. Quiet hours protect the recipient's local evening/night.
    if is_within_quiet_hours(context.now, business, bot):
        return EligibilityDecision.deny(
            "quiet_hours",
            f"Currently within quiet hours "
            f"({bot.quiet_hours.start}-{bot.quiet_hours.end} {business.timezone}).",
        )

    return EligibilityDecision.ok()


# ================================================================== escalation
def should_escalate(
    intent: Intent,
    confidence: float,
    bot: BotConfig,
    *,
    consecutive_failures: int = 0,
    safety_flagged: bool = False,
) -> bool:
    """Deterministic escalation decision.

    The LLM proposes; this function disposes. A model that claims high
    confidence on a complaint still escalates.
    """
    from app.domain.enums import ALWAYS_ESCALATE_INTENTS

    if safety_flagged:
        return True
    if intent in ALWAYS_ESCALATE_INTENTS:
        return True
    if consecutive_failures >= 3:
        return True
    return confidence < bot.thresholds.escalate
