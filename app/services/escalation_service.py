"""Human escalation and CRM hand-off.

When no CRM is configured, escalations are stored in the database and exposed
through the protected operator API. :class:`CRMAdapter` exists so GoHighLevel,
HubSpot, Salesforce or anything else can be added later without the chatbot
core knowing which one is in use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.logging import get_correlation_id, get_logger
from app.core.redaction import mask_phone
from app.domain.config_schema import BusinessConfig
from app.domain.enums import ConversationState, Direction, EscalationReason
from app.domain.models import Contact, Conversation, Escalation, Message
from app.repositories.consent import EscalationRepository
from app.repositories.contacts import ContactRepository
from app.repositories.conversations import ConversationRepository, MessageRepository

logger = get_logger(__name__)

MAX_SUMMARY_CHARS = 2000


@dataclass(frozen=True, slots=True)
class EscalationPayload:
    """Provider-neutral description of an escalation, safe to transmit."""

    business_id: str
    contact_name: str
    contact_masked: str
    reason: str
    detected_intent: str | None
    confidence: float | None
    summary: str
    correlation_id: str | None


class CRMAdapter(Protocol):
    """Interface for pushing escalations to an external CRM."""

    @property
    def name(self) -> str: ...

    async def create_ticket(self, payload: EscalationPayload) -> str | None:
        """Create a ticket and return its external reference, or None."""
        ...


class NullCRMAdapter:
    """Default adapter: keeps everything local, in the database."""

    @property
    def name(self) -> str:
        return "none"

    async def create_ticket(self, payload: EscalationPayload) -> str | None:
        _ = payload
        return None


class EscalationService:
    """Creates escalations, pauses the bot and manages human takeover."""

    def __init__(
        self,
        *,
        business: BusinessConfig,
        escalations: EscalationRepository,
        conversations: ConversationRepository,
        messages: MessageRepository,
        contacts: ContactRepository,
        crm: CRMAdapter | None = None,
        decrypt: object | None = None,
    ) -> None:
        self._business = business
        self._escalations = escalations
        self._conversations = conversations
        self._messages = messages
        self._contacts = contacts
        self._crm = crm or NullCRMAdapter()
        self._decrypt = decrypt

    # ---------------------------------------------------------------- summary
    def _render_message(self, message: Message) -> str:
        """Render one history line, decrypting content only if permitted."""
        who = "Customer" if message.direction is Direction.INBOUND else "Bot"
        text = message.content_preview or ""
        if self._decrypt is not None and message.content_encrypted:
            decrypted = self._decrypt(message.content_encrypted)  # type: ignore[operator]
            if decrypted:
                text = str(decrypted)
        return f"{who}: {text}".strip()

    async def build_summary(
        self,
        conversation: Conversation,
        contact: Contact,
        *,
        reason: EscalationReason,
        history_limit: int = 10,
    ) -> str:
        """Human-readable brief for the agent picking this up."""
        history = await self._messages.recent_history(conversation.id, limit=history_limit)
        lines = [
            f"Escalation reason: {reason.value.replace('_', ' ')}",
            f"Customer: {contact.first_name or 'Unknown'} ({contact.phone_masked})",
            f"Detected intent: {conversation.last_intent or 'unknown'}"
            + (
                f" (confidence {conversation.last_confidence:.2f})"
                if conversation.last_confidence is not None
                else ""
            ),
            f"Consent status: {contact.consent_status.value}",
            "",
            "Conversation:",
        ]
        lines.extend(f"  {self._render_message(message)}" for message in history)
        return "\n".join(lines)[:MAX_SUMMARY_CHARS]

    # -------------------------------------------------------------- escalate
    async def escalate(
        self,
        *,
        conversation: Conversation,
        contact: Contact,
        reason: EscalationReason,
        confidence: float | None = None,
        pause_bot: bool = True,
    ) -> Escalation:
        """Create (or reuse) an escalation and pause the bot for this contact.

        Reusing an existing open escalation prevents a customer who sends five
        angry messages from generating five duplicate tickets.
        """
        existing = await self._escalations.get_open_for_conversation(conversation.id)
        if existing is not None:
            logger.info(
                "escalation_already_open",
                escalation_id=existing.id,
                conversation_id=conversation.id,
            )
            if pause_bot and not contact.bot_paused:
                await self._contacts.set_bot_paused(
                    contact, paused=True, reason=f"escalation:{existing.id}"
                )
            return existing

        summary = await self.build_summary(conversation, contact, reason=reason)
        escalation = await self._escalations.create(
            business_id=conversation.business_id,
            conversation_id=conversation.id,
            contact_id=contact.id,
            reason=reason.value,
            summary=summary,
            detected_intent=conversation.last_intent,
            confidence=confidence if confidence is not None else conversation.last_confidence,
            correlation_id=get_correlation_id(),
        )

        await self._conversations.mark_escalated(
            conversation, human_agent=self._business.escalation.human_agent_name
        )
        if pause_bot:
            await self._contacts.set_bot_paused(
                contact, paused=True, reason=f"escalation:{escalation.id}"
            )

        # Best-effort CRM push. A CRM outage must never lose the escalation,
        # which is already durably stored above.
        try:
            reference = await self._crm.create_ticket(
                EscalationPayload(
                    business_id=conversation.business_id,
                    contact_name=contact.first_name or "Unknown",
                    contact_masked=mask_phone(contact.phone_masked),
                    reason=reason.value,
                    detected_intent=conversation.last_intent,
                    confidence=escalation.confidence,
                    summary=summary,
                    correlation_id=get_correlation_id(),
                )
            )
        except Exception as exc:  # noqa: BLE001 - CRM must not break the bot
            logger.warning("crm_push_failed", adapter=self._crm.name, error=type(exc).__name__)
        else:
            if reference:
                escalation.crm_reference = reference
                await self._escalations.flush()

        logger.info(
            "escalation_created",
            escalation_id=escalation.id,
            reason=reason.value,
            crm=self._crm.name,
        )
        return escalation

    # ------------------------------------------------------------- resolution
    async def resolve(
        self,
        *,
        business_id: str,
        escalation_id: int,
        note: str | None = None,
        actor: str = "operator",
        resume_bot: bool = True,
    ) -> Escalation | None:
        """Close an escalation and optionally hand the conversation back."""
        escalation = await self._escalations.get_by_id(business_id, escalation_id)
        if escalation is None:
            return None

        await self._escalations.resolve(escalation, note=note, actor=actor)

        conversation = await self._conversations.get_by_id(
            business_id, escalation.conversation_id
        )
        contact = await self._contacts.get_by_id(business_id, escalation.contact_id)

        if resume_bot:
            if contact is not None:
                await self._contacts.set_bot_paused(contact, paused=False)
            if conversation is not None:
                await self._conversations.set_state(conversation, ConversationState.ACTIVE)

        logger.info("escalation_resolved", escalation_id=escalation_id, resumed=resume_bot)
        return escalation

    async def acknowledge(
        self, *, business_id: str, escalation_id: int, actor: str
    ) -> Escalation | None:
        escalation = await self._escalations.get_by_id(business_id, escalation_id)
        if escalation is None:
            return None
        await self._escalations.acknowledge(escalation, actor=actor)
        return escalation

    async def list_open(self, business_id: str, *, limit: int = 50) -> list[Escalation]:
        return await self._escalations.list_open(business_id, limit=limit)
