"""Pydantic schemas for LLM structured output, retrieval and the operator API.

The LLM is never trusted. Its output is parsed into :class:`IntentResult`, and
any field it gets wrong -- an unknown intent, an out-of-range confidence, an
over-long reply -- is coerced or rejected here rather than reaching a customer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import (
    ConsentStatus,
    ConversationState,
    EscalationReason,
    Intent,
    SafetyFlag,
)

MAX_MODEL_REPLY_CHARS = 4096


# ============================================================== LLM contract
class IntentResult(BaseModel):
    """Validated structured output from the intent/response model.

    ``model_config`` forbids extra keys so a model that invents fields fails
    validation instead of smuggling unexpected instructions through.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    intent: Intent = Intent.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requested_action: str | None = Field(default=None, max_length=200)
    entities: dict[str, str] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    proposed_response: str = Field(default="", max_length=MAX_MODEL_REPLY_CHARS)
    requires_human: bool = False
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    #: Knowledge chunk ids the answer was grounded in. Empty means ungrounded.
    sources: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("intent", mode="before")
    @classmethod
    def _coerce_intent(cls, value: Any) -> Any:
        """Map an unrecognised intent string to UNKNOWN rather than erroring."""
        if isinstance(value, str):
            normalised = value.strip().lower().replace(" ", "_").replace("-", "_")
            valid = {item.value for item in Intent}
            return normalised if normalised in valid else Intent.UNKNOWN.value
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> Any:
        """Clamp instead of rejecting: models routinely emit 1.2 or -0.1."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(1.0, max(0.0, number))

    @field_validator("safety_flags", mode="before")
    @classmethod
    def _coerce_flags(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        valid = {item.value for item in SafetyFlag}
        return [
            item
            for item in (str(entry).strip().lower() for entry in value)
            if item in valid and item != SafetyFlag.NONE.value
        ]

    @field_validator("entities", mode="before")
    @classmethod
    def _coerce_entities(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        return {str(k)[:64]: str(v)[:256] for k, v in list(value.items())[:20]}

    @model_validator(mode="after")
    def _safety_forces_human(self) -> IntentResult:
        """A safety flag always means a human, whatever the model claimed."""
        if self.safety_flags:
            object.__setattr__(self, "requires_human", True)
        return self

    @property
    def is_grounded(self) -> bool:
        return bool(self.sources)


#: JSON Schema handed to the model to constrain its output shape.
INTENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [item.value for item in Intent]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "requested_action": {"type": ["string", "null"]},
        "entities": {"type": "object", "additionalProperties": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "proposed_response": {"type": "string"},
        "requires_human": {"type": "boolean"},
        "safety_flags": {
            "type": "array",
            "items": {"type": "string", "enum": [item.value for item in SafetyFlag]},
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "confidence", "proposed_response", "requires_human"],
}


# ================================================================== retrieval
class KnowledgeChunk(BaseModel):
    """One indexed, citable piece of business knowledge."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    business_id: str
    source: str
    title: str
    text: str
    #: Where it came from: 'business_config' or 'file'.
    origin: Literal["business_config", "file"] = "file"
    metadata: dict[str, str] = Field(default_factory=dict)


class RetrievalHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk: KnowledgeChunk
    score: float
    lexical_score: float = 0.0
    semantic_score: float = 0.0

    @property
    def citation(self) -> str:
        return f"[{self.chunk.chunk_id}] {self.chunk.title}"


class RetrievalResult(BaseModel):
    """Retrieval output plus the metadata needed to debug a bad answer."""

    query: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    strategy: str = "bm25"
    embeddings_used: bool = False
    took_ms: float = 0.0

    @property
    def has_context(self) -> bool:
        return bool(self.hits)

    def source_ids(self) -> list[str]:
        return [hit.chunk.chunk_id for hit in self.hits]

    def as_context_block(self, max_chars: int = 4000) -> str:
        """Render hits as a citable context block for the prompt."""
        parts: list[str] = []
        used = 0
        for hit in self.hits:
            entry = f"[{hit.chunk.chunk_id}] {hit.chunk.title}\n{hit.chunk.text}"
            if used + len(entry) > max_chars:
                break
            parts.append(entry)
            used += len(entry)
        return "\n\n".join(parts)


# ================================================================= bot output
class BotReply(BaseModel):
    """The final decision about what (if anything) to send back."""

    text: str
    intent: Intent = Intent.UNKNOWN
    confidence: float = 0.0
    escalate: bool = False
    escalation_reason: EscalationReason | None = None
    pause_bot: bool = False
    sources: list[str] = Field(default_factory=list)
    buttons: list[dict[str, str]] = Field(default_factory=list)
    #: True when the reply came from a deterministic path, not the model.
    deterministic: bool = False
    suppress_send: bool = False


# =============================================================== operator API
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    app_env: str
    version: str
    business_id: str | None = None


class ReadinessComponent(BaseModel):
    name: str
    ready: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    components: list[ReadinessComponent]


class BotStatusResponse(BaseModel):
    business_id: str
    business_name: str
    paused: bool
    pause_reason: str | None = None
    llm_provider: str
    llm_model: str
    graph_api_version: str
    meta_configured: bool
    campaigns_enabled: bool


class PauseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
    actor: str = Field(default="operator", max_length=128)


class ContactPauseRequest(PauseRequest):
    phone: str = Field(min_length=4, max_length=32)


class ContactSummary(BaseModel):
    """Contact representation for operators -- masked, never raw."""

    id: int
    phone_masked: str
    phone_pseudonym: str
    first_name: str | None = None
    locale: str
    consent_status: ConsentStatus
    bot_paused: bool
    last_inbound_at: datetime | None = None


class EscalationSummary(BaseModel):
    id: int
    contact: ContactSummary
    reason: str
    status: str
    summary: str
    detected_intent: str | None
    confidence: float | None
    created_at: datetime
    assigned_to: str | None = None


class ConversationSummary(BaseModel):
    id: int
    contact: ContactSummary
    state: ConversationState
    last_intent: str | None
    escalated: bool
    message_count: int
    created_at: datetime


class DeliveryStatsResponse(BaseModel):
    window_hours: int
    queued: int = 0
    sent: int = 0
    delivered: int = 0
    read: int = 0
    failed: int = 0

    @property
    def failure_rate(self) -> float:
        total = self.sent + self.delivered + self.read + self.failed
        return (self.failed / total) if total else 0.0


class FailedMessageSummary(BaseModel):
    id: int
    contact_masked: str
    error_code: int | None
    error_detail: str | None
    template_name: str | None
    created_at: datetime


class CampaignSummary(BaseModel):
    id: int
    name: str
    template_name: str
    template_language: str
    status: str
    total: int
    eligible: int
    rejected: int
    sent: int
    delivered: int
    read: int
    failed: int
    pause_reason: str | None = None


class ConsentHistoryEntry(BaseModel):
    status: ConsentStatus
    purpose: str
    source: str
    occurred_at: datetime
    evidence: str | None = None


class SuppressionEntrySummary(BaseModel):
    phone_masked: str
    reason: str
    detail: str | None
    suppressed_at: datetime


class TemplateSummary(BaseModel):
    name: str
    language: str
    category: str
    status: str
    purpose: str
    variable_count: int
    sendable: bool


class ConfigValidationResponse(BaseModel):
    valid: bool
    business_id: str | None = None
    problems: list[str] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)
