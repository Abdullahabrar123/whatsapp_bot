"""SQLAlchemy 2.0 ORM models.

Storage rules applied throughout:

* Phone numbers are stored encrypted (``phone_encrypted``) alongside a keyed
  HMAC (``phone_hash``) used for lookups and a mask used for display, so no
  query, index or log ever needs the raw value.
* Message bodies are encrypted at rest when ``features.store_message_content``
  is enabled and omitted entirely when it is not.
* Consent and suppression rows are append-mostly compliance evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import (
    CampaignRecipientStatus,
    CampaignStatus,
    ConsentPurpose,
    ConsentStatus,
    ConversationState,
    DeliveryStatus,
    Direction,
    EscalationStatus,
    MessageType,
    TemplateCategory,
    TemplateStatus,
)


def enum_column(enum_cls: type[StrEnum], length: int) -> Enum:
    """VARCHAR-backed enum that SQLAlchemy coerces back to the enum on load.

    Declaring these columns as plain ``String`` stored the value fine but read
    it back as ``str``, so every ``is``/``is not`` comparison against an enum
    member silently evaluated False -- including the opt-in checks in the
    send-eligibility rules. ``values_callable`` keeps the persisted text equal
    to the member value, so existing rows and the migration stay valid.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


def utcnow() -> datetime:
    """Timezone-aware UTC now (never naive, so comparisons are unambiguous)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base with a portable JSON type."""

    type_annotation_map = {dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


# ==================================================================== contacts
class Contact(Base, TimestampMixin):
    """A person who can be messaged. Identified by a hash, never a raw number."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: HMAC-SHA256 of the E.164 number; the lookup key everywhere.
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Fernet ciphertext of the E.164 number.
    phone_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    #: Display-safe mask such as "+44*******23".
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)

    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locale: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    timezone_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    consent_status: Mapped[ConsentStatus] = mapped_column(
        enum_column(ConsentStatus, 24), default=ConsentStatus.UNKNOWN, nullable=False, index=True
    )
    #: True while a human agent owns the conversation.
    bot_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pause_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    consents: Mapped[list[ConsentRecord]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "phone_hash", name="uq_contact_business_phone"),
        Index("ix_contact_business_consent", "business_id", "consent_status"),
    )


# =============================================================== conversations
class Conversation(Base, TimestampMixin):
    """A service conversation window with one contact."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    state: Mapped[ConversationState] = mapped_column(
        enum_column(ConversationState, 32),
        default=ConversationState.ACTIVE,
        nullable=False,
        index=True,
    )
    last_intent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    human_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Meta's 24-hour customer-service window expiry, if known.
    service_window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_conv_business_state", "business_id", "state"),)


class Message(Base, TimestampMixin):
    """One inbound or outbound message."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Meta's message id (wamid...). Unique per business: the idempotency key.
    meta_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[Direction] = mapped_column(enum_column(Direction, 16), nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        enum_column(MessageType, 40), nullable=False
    )

    #: Encrypted body; null when content storage is disabled by feature flag.
    content_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Non-reversible summary kept even when content storage is off.
    content_preview: Mapped[str | None] = mapped_column(String(80), nullable=True)
    media_reference: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    intent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    delivery_status: Mapped[DeliveryStatus | None] = mapped_column(
        enum_column(DeliveryStatus, 24), nullable=True, index=True
    )
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    template_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )

    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("business_id", "meta_message_id", name="uq_message_business_meta_id"),
        Index("ix_message_business_created", "business_id", "created_at"),
    )


# ===================================================================== consent
class ConsentRecord(Base, TimestampMixin):
    """Append-only evidence of a consent state change."""

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[ConsentStatus] = mapped_column(enum_column(ConsentStatus, 24), nullable=False)
    purpose: Mapped[ConsentPurpose] = mapped_column(
        enum_column(ConsentPurpose, 24), nullable=False
    )
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    #: When the customer actually consented (not when we recorded it).
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    contact: Mapped[Contact] = relationship(back_populates="consents")

    __table_args__ = (Index("ix_consent_contact_purpose", "contact_id", "purpose"),)


class SuppressionEntry(Base, TimestampMixin):
    """Permanent do-not-message list. Rows are never deleted by the app."""

    __tablename__ = "suppression_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    suppressed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("business_id", "phone_hash", name="uq_suppression_business_phone"),
    )


# =================================================================== campaigns
class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    template_name: Mapped[str] = mapped_column(String(512), nullable=False)
    template_language: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        enum_column(CampaignStatus, 24), default=CampaignStatus.DRAFT, nullable=False, index=True
    )
    pause_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    recipients: Mapped[list[CampaignRecipient]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("business_id", "name", name="uq_campaign_business_name"),)


class MessageTemplate(Base, TimestampMixin):
    """Operator-managed template mirror for the business messaging registry."""

    __tablename__ = "message_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[TemplateCategory] = mapped_column(
        enum_column(TemplateCategory, 24), nullable=False
    )
    status: Mapped[TemplateStatus] = mapped_column(
        enum_column(TemplateStatus, 24), default=TemplateStatus.PENDING, nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_opt_in: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    variables: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "name", "language", name="uq_template_business_name_language"),
    )


class CampaignRecipient(Base, TimestampMixin):
    __tablename__ = "campaign_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[CampaignRecipientStatus] = mapped_column(
        enum_column(CampaignRecipientStatus, 24),
        default=CampaignRecipientStatus.PENDING,
        nullable=False,
        index=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_variables: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    meta_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_recipient"),
    )


# ================================================================== escalation
class Escalation(Base, TimestampMixin):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    reason: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[EscalationStatus] = mapped_column(
        enum_column(EscalationStatus, 24), default=EscalationStatus.OPEN, nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detected_intent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    crm_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)


# ============================================================== infrastructure
class WebhookEvent(Base, TimestampMixin):
    """Idempotency ledger for inbound Meta webhook events."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Meta message/status id, or a hash of the payload when neither exists.
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("business_id", "event_key", name="uq_webhook_business_event"),
    )


class DeadLetter(Base, TimestampMixin):
    """Events that exhausted their retries and need operator attention."""

    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    event_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Already-redacted payload; safe to display in the operator API.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BotState(Base, TimestampMixin):
    """Global operator switches, one row per business."""

    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pause_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paused_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
