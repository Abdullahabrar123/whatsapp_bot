"""Strict Pydantic schemas for config/business.yaml and config/bot.yaml.

These schemas are the contract between a non-technical operator and the running
application. They are deliberately strict (``extra="forbid"``) so that a typo in
a key name fails loudly at startup instead of silently disabling a policy.

Nothing here may contain a secret. Secrets live only in environment variables.
"""

from __future__ import annotations

import re
from datetime import time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import Intent, TemplateCategory, TemplateStatus

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_LANG_RE = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")

NonEmptyStr = Annotated[str, Field(min_length=1)]

# Keys that must never appear in a YAML config file; enforced by a scanner so an
# operator cannot accidentally paste a token where it would be committed.
FORBIDDEN_CONFIG_KEYS: tuple[str, ...] = (
    "access_token",
    "app_secret",
    "api_key",
    "apikey",
    "password",
    "secret_key",
    "verify_token",
    "private_key",
    "token",
)


class StrictModel(BaseModel):
    """Base config model: unknown keys are an error, values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# ============================================================ shared fragments
class DayHours(StrictModel):
    """Opening hours for a single day. ``closed: true`` ignores open/close."""

    closed: bool = False
    open: str | None = None
    close: str | None = None

    @field_validator("open", "close")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _TIME_RE.match(value):
            raise ValueError(f"Time must be 24-hour HH:MM, got {value!r}")
        return value

    @model_validator(mode="after")
    def _check_range(self) -> DayHours:
        if self.closed:
            return self
        if not self.open or not self.close:
            raise ValueError("Both 'open' and 'close' are required unless 'closed: true'.")
        if self.as_open() >= self.as_close():
            raise ValueError(f"Opening time {self.open} must be before closing time {self.close}.")
        return self

    def as_open(self) -> time:
        hour, minute = (self.open or "00:00").split(":")
        return time(int(hour), int(minute))

    def as_close(self) -> time:
        hour, minute = (self.close or "00:00").split(":")
        return time(int(hour), int(minute))


class BusinessHours(StrictModel):
    """Weekly opening hours keyed by lowercase English weekday name."""

    monday: DayHours
    tuesday: DayHours
    wednesday: DayHours
    thursday: DayHours
    friday: DayHours
    saturday: DayHours
    sunday: DayHours

    def for_weekday(self, weekday: int) -> DayHours:
        """Return hours for a ``datetime.weekday()`` value (Monday == 0)."""
        order = (
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        )
        return order[weekday]


class ContactInfo(StrictModel):
    phone_display: NonEmptyStr = Field(
        description="Human-readable contact number shown to customers. "
        "This is NOT the sender identity -- sending uses META_PHONE_NUMBER_ID."
    )
    email: NonEmptyStr
    support_url: str | None = None


class Address(StrictModel):
    line1: NonEmptyStr
    line2: str | None = None
    city: NonEmptyStr
    region: str | None = None
    postal_code: NonEmptyStr
    country: NonEmptyStr = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")

    @field_validator("country")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class ServiceItem(StrictModel):
    """A product or service. Prices are strings so currency/qualifiers survive."""

    id: NonEmptyStr
    name: NonEmptyStr
    description: NonEmptyStr
    price: str | None = Field(
        default=None,
        description="Exact price text shown to customers, e.g. '£45.00'. "
        "Leave null if price varies; the bot will then refuse to quote.",
    )
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    available: bool = True
    tags: list[str] = Field(default_factory=list)


class FAQItem(StrictModel):
    id: NonEmptyStr
    question: NonEmptyStr
    answer: NonEmptyStr
    tags: list[str] = Field(default_factory=list)


class PolicyItem(StrictModel):
    id: NonEmptyStr
    title: NonEmptyStr
    body: NonEmptyStr


class DeliveryInfo(StrictModel):
    offers_delivery: bool = False
    methods: list[str] = Field(default_factory=list)
    lead_time: str | None = None
    fees: str | None = None
    notes: str | None = None


class AppointmentRules(StrictModel):
    enabled: bool = False
    min_notice_hours: int = Field(default=24, ge=0, le=720)
    max_advance_days: int = Field(default=90, ge=1, le=730)
    slot_minutes: int = Field(default=30, gt=0, le=480)
    cancellation_notice_hours: int = Field(default=24, ge=0, le=720)
    requires_confirmation: bool = True
    notes: str | None = None


class RefundRules(StrictModel):
    enabled: bool = False
    window_days: int = Field(default=14, ge=0, le=3650)
    conditions: list[str] = Field(default_factory=list)
    process: str | None = None
    # Refunds are consequential: the bot explains policy but never promises one.
    bot_may_approve: Literal[False] = False


class EscalationRules(StrictModel):
    enabled: bool = True
    triggers: list[str] = Field(default_factory=list)
    human_agent_name: NonEmptyStr
    human_agent_contact: NonEmptyStr
    hours: str | None = None
    sla_minutes: int = Field(default=60, ge=1, le=10080)


class EmergencyInfo(StrictModel):
    """Shown verbatim when a safety-critical situation is detected."""

    enabled: bool = False
    message: str | None = None
    contact: str | None = None

    @model_validator(mode="after")
    def _check_message(self) -> EmergencyInfo:
        if self.enabled and not self.message:
            raise ValueError("emergency.message is required when emergency.enabled is true.")
        return self


class KnowledgePaths(StrictModel):
    """Filesystem sources for retrieval, resolved relative to the project root."""

    directories: list[str] = Field(default_factory=lambda: ["data/knowledge"])
    include_business_config: bool = True
    file_extensions: list[str] = Field(default_factory=lambda: [".md", ".txt"])

    @field_validator("directories")
    @classmethod
    def _no_traversal(cls, value: list[str]) -> list[str]:
        for item in value:
            if ".." in item or item.startswith(("/", "\\")):
                raise ValueError(
                    f"Knowledge path {item!r} must be a relative path inside the project."
                )
        return value


# ================================================================ business.yaml
class BusinessConfig(StrictModel):
    """Complete, validated description of one business."""

    business_id: NonEmptyStr = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    name: NonEmptyStr
    industry: NonEmptyStr
    description: NonEmptyStr = Field(min_length=20)
    website: str | None = None
    address: Address
    contact: ContactInfo
    timezone: NonEmptyStr
    supported_languages: list[str] = Field(min_length=1)
    default_language: NonEmptyStr
    brand_voice: NonEmptyStr
    greeting: NonEmptyStr
    business_hours: BusinessHours
    services: list[ServiceItem] = Field(default_factory=list)
    pricing_notes: str | None = None
    faqs: list[FAQItem] = Field(default_factory=list)
    policies: list[PolicyItem] = Field(default_factory=list)
    delivery: DeliveryInfo = Field(default_factory=DeliveryInfo)
    appointments: AppointmentRules = Field(default_factory=AppointmentRules)
    refunds: RefundRules = Field(default_factory=RefundRules)
    supported_locations: list[str] = Field(default_factory=list)
    emergency: EmergencyInfo = Field(default_factory=EmergencyInfo)
    escalation: EscalationRules
    restricted_subjects: list[str] = Field(default_factory=list)
    legal_disclaimer: NonEmptyStr
    knowledge: KnowledgePaths = Field(default_factory=KnowledgePaths)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"Unknown IANA timezone {value!r}. Use e.g. 'Europe/London'."
            ) from exc
        return value

    @field_validator("supported_languages")
    @classmethod
    def _check_languages(cls, value: list[str]) -> list[str]:
        for lang in value:
            if not _LANG_RE.match(lang):
                raise ValueError(f"Language must be 'en' or 'en_GB' style, got {lang!r}")
        if len(set(value)) != len(value):
            raise ValueError("supported_languages contains duplicates.")
        return value

    @field_validator("website")
    @classmethod
    def _check_website(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("website must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> BusinessConfig:
        if self.default_language not in self.supported_languages:
            raise ValueError(
                f"default_language {self.default_language!r} is not in supported_languages "
                f"{self.supported_languages}."
            )
        service_ids = [item.id for item in self.services]
        if len(set(service_ids)) != len(service_ids):
            raise ValueError("services contains duplicate ids.")
        faq_ids = [item.id for item in self.faqs]
        if len(set(faq_ids)) != len(faq_ids):
            raise ValueError("faqs contains duplicate ids.")
        return self

    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


# ===================================================================== bot.yaml
class ConfidenceThresholds(StrictModel):
    #: Below this the bot must not answer autonomously.
    reply: float = Field(default=0.55, ge=0.0, le=1.0)
    #: Below this the conversation is handed to a human.
    escalate: float = Field(default=0.40, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordering(self) -> ConfidenceThresholds:
        if self.escalate > self.reply:
            raise ValueError("thresholds.escalate must be <= thresholds.reply.")
        return self


class QuietHours(StrictModel):
    """Local-time window during which business-initiated sends are forbidden."""

    enabled: bool = True
    start: str = "21:00"
    end: str = "08:00"

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError(f"Quiet-hours time must be HH:MM, got {value!r}")
        return value


class RateLimits(StrictModel):
    inbound_per_contact_per_minute: int = Field(default=20, ge=1, le=600)
    outbound_per_second: float = Field(default=10.0, gt=0, le=80.0)
    llm_concurrent: int = Field(default=4, ge=1, le=64)


class CampaignLimits(StrictModel):
    batch_size: int = Field(default=50, ge=1, le=1000)
    delay_between_sends_seconds: float = Field(
        default=0.2,
        ge=0.05,
        le=60.0,
        description="Pacing between approved sends. Lower bound is enforced; "
        "this control exists to be gentler than Meta's limits, never to evade them.",
    )
    # Automatic safety brakes. These are floors, not suggestions.
    max_failure_rate: float = Field(default=0.10, gt=0.0, le=0.5)
    max_block_rate: float = Field(default=0.02, gt=0.0, le=0.2)
    min_sample_before_pause: int = Field(default=20, ge=5, le=1000)
    pause_on_quality_warning: Literal[True] = True


class DataRetention(StrictModel):
    messages_days: int = Field(default=365, ge=1, le=3650)
    conversations_days: int = Field(default=365, ge=1, le=3650)
    escalations_days: int = Field(default=730, ge=1, le=3650)
    #: Consent and suppression records are retained as compliance evidence and
    #: are deliberately not deletable by the retention job.
    delete_consent_records: Literal[False] = False


class BusinessHoursBehaviour(StrictModel):
    reply_outside_hours: bool = True
    outside_hours_message: NonEmptyStr = (
        "Thanks for your message. We're currently closed and will reply when we reopen."
    )
    escalate_outside_hours: bool = False


class FeatureFlags(StrictModel):
    knowledge_retrieval: bool = True
    llm_responses: bool = True
    campaigns_enabled: bool = True
    auto_mark_read: bool = True
    typing_indicator: bool = False
    store_message_content: bool = True
    embeddings_enabled: bool = True


class BotConfig(StrictModel):
    """Non-secret behavioural configuration."""

    enabled_intents: list[Intent] = Field(min_length=1)
    thresholds: ConfidenceThresholds = Field(default_factory=ConfidenceThresholds)
    max_response_chars: int = Field(default=900, ge=50, le=4096)
    response_tone: NonEmptyStr = "friendly, concise, professional"
    enabled_languages: list[str] = Field(min_length=1)
    history_limit: int = Field(default=10, ge=0, le=100)
    model_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    model_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_retries: int = Field(default=2, ge=0, le=10)
    conversation_expiry_hours: int = Field(default=24, ge=1, le=720)
    business_hours_behaviour: BusinessHoursBehaviour = Field(
        default_factory=BusinessHoursBehaviour
    )
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    campaign: CampaignLimits = Field(default_factory=CampaignLimits)
    supported_message_types: list[str] = Field(min_length=1)
    stop_words: list[str] = Field(min_length=1)
    handover_keywords: list[str] = Field(min_length=1)
    opt_in_keywords: list[str] = Field(default_factory=lambda: ["start", "subscribe", "yes"])
    retention: DataRetention = Field(default_factory=DataRetention)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    @field_validator("stop_words", "handover_keywords", "opt_in_keywords")
    @classmethod
    def _normalise_keywords(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("Keyword list must contain at least one non-empty entry.")
        return cleaned

    @field_validator("enabled_languages")
    @classmethod
    def _check_languages(cls, value: list[str]) -> list[str]:
        for lang in value:
            if not _LANG_RE.match(lang):
                raise ValueError(f"Language must be 'en' or 'en_GB' style, got {lang!r}")
        return value

    @model_validator(mode="after")
    def _mandatory_compliance_intents(self) -> BotConfig:
        """Opt-out handling can never be switched off by configuration."""
        required = {Intent.OPT_OUT, Intent.HUMAN_AGENT_REQUEST}
        missing = required - set(self.enabled_intents)
        if missing:
            raise ValueError(
                "enabled_intents must always include "
                f"{sorted(item.value for item in required)}; missing "
                f"{sorted(item.value for item in missing)}. "
                "These are compliance controls and cannot be disabled."
            )
        return self


# =============================================================== templates.yaml
class TemplateVariable(StrictModel):
    name: NonEmptyStr
    position: int = Field(ge=1, le=50)
    example: NonEmptyStr
    required: bool = True


class TemplateDefinition(StrictModel):
    """Local registry entry mirroring a template as approved inside Meta.

    The registry never *creates* approval; it records what Meta reports so the
    application can refuse to send anything unapproved.
    """

    name: NonEmptyStr = Field(pattern=r"^[a-z0-9_]{1,512}$")
    language: NonEmptyStr
    category: TemplateCategory
    status: TemplateStatus = TemplateStatus.PENDING
    purpose: NonEmptyStr
    body_preview: NonEmptyStr
    variables: list[TemplateVariable] = Field(default_factory=list)
    requires_opt_in: bool = True
    header_format: Literal["NONE", "TEXT", "IMAGE", "DOCUMENT", "VIDEO"] = "NONE"

    @model_validator(mode="after")
    def _check(self) -> TemplateDefinition:
        positions = [variable.position for variable in self.variables]
        if sorted(positions) != list(range(1, len(positions) + 1)):
            raise ValueError(
                f"Template {self.name!r} variable positions must be 1..N with no gaps, "
                f"got {sorted(positions)}."
            )
        if self.category is TemplateCategory.MARKETING and not self.requires_opt_in:
            raise ValueError(
                f"Template {self.name!r} is MARKETING and must set requires_opt_in: true."
            )
        return self

    @property
    def key(self) -> str:
        return f"{self.name}:{self.language}"

    def is_sendable(self) -> bool:
        return self.status is TemplateStatus.APPROVED


class TemplateRegistry(StrictModel):
    templates: list[TemplateDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> TemplateRegistry:
        keys = [item.key for item in self.templates]
        if len(set(keys)) != len(keys):
            raise ValueError("templates.yaml contains duplicate name+language entries.")
        return self

    def get(self, name: str, language: str) -> TemplateDefinition | None:
        for item in self.templates:
            if item.name == name and item.language == language:
                return item
        return None

    def by_name(self, name: str) -> list[TemplateDefinition]:
        return [item for item in self.templates if item.name == name]


# ================================================================== secret scan
def scan_for_secrets(data: Any, *, path: str = "") -> list[str]:
    """Return human-readable findings for secret-looking keys in parsed YAML.

    Run at load time so an operator who pastes a token into YAML is stopped
    before the value can reach a log, a commit or a backup.
    """
    findings: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_CONFIG_KEYS) and value:
                findings.append(
                    f"{here}: secret-like key is not allowed in YAML; use an environment variable."
                )
            findings.extend(scan_for_secrets(value, path=here))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            findings.extend(scan_for_secrets(item, path=f"{path}[{index}]"))
    elif isinstance(data, str) and data.startswith("EA") and len(data) > 40 and data.isalnum():
        findings.append(f"{path}: value looks like a Meta access token.")
    return findings
