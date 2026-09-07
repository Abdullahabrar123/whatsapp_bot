"""Inbound message orchestration -- the core vertical slice.

Pipeline for one inbound message::

    idempotency claim
      -> resolve contact + conversation
      -> opt-out check (before anything else)
      -> global / per-contact pause check
      -> retrieval
      -> intent analysis (LLM or deterministic)
      -> response composition
      -> escalation (if required)
      -> outbound send
      -> persistence

Ordering is deliberate: consent is honoured before the bot spends a single
token, and a paused conversation never generates an automated reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.exceptions import (
    CircuitOpenError,
    LLMError,
    WhatsAppAPIError,
)
from app.core.logging import get_correlation_id, get_logger
from app.core.security import FieldCipher
from app.domain.config_schema import BotConfig, BusinessConfig
from app.domain.enums import (
    ConsentStatus,
    ConversationState,
    DeliveryStatus,
    Direction,
    EscalationReason,
    Intent,
    MessageType,
)
from app.domain.models import Contact, Conversation
from app.domain.phone import normalise_phone
from app.domain.rules import service_window_open
from app.domain.schemas import BotReply, IntentResult, RetrievalResult
from app.integrations.webhook_parser import InboundMessage, StatusUpdate
from app.repositories.campaigns import CampaignRecipientRepository, CampaignRepository
from app.repositories.consent import BotStateRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.consent_service import ConsentService
from app.services.escalation_service import EscalationService
from app.services.intent_service import IntentService
from app.services.knowledge_service import KnowledgeService
from app.services.response_service import ResponseService

logger = get_logger(__name__)

PREVIEW_CHARS = 60


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    """What happened to one inbound message. Used by tests and metrics."""

    handled: bool
    reason: str
    intent: Intent | None = None
    replied: bool = False
    escalated: bool = False
    reply_text: str | None = None


class ConversationService:
    """Processes inbound messages and delivery statuses."""

    def __init__(
        self,
        *,
        business: BusinessConfig,
        bot: BotConfig,
        cipher: FieldCipher,
        secret_key: str,
        contacts: ContactRepository,
        conversations: ConversationRepository,
        messages: MessageRepository,
        suppressions: SuppressionRepository,
        bot_state: BotStateRepository,
        campaigns: CampaignRepository,
        campaign_recipients: CampaignRecipientRepository,
        consent_service: ConsentService,
        escalation_service: EscalationService,
        intent_service: IntentService,
        response_service: ResponseService,
        knowledge_service: KnowledgeService,
        whatsapp_client: object,
    ) -> None:
        self._business = business
        self._bot = bot
        self._cipher = cipher
        self._secret = secret_key
        self._contacts = contacts
        self._conversations = conversations
        self._messages = messages
        self._suppressions = suppressions
        self._bot_state = bot_state
        self._campaigns = campaigns
        self._campaign_recipients = campaign_recipients
        self._consent = consent_service
        self._escalation = escalation_service
        self._intent = intent_service
        self._response = response_service
        self._knowledge = knowledge_service
        self._whatsapp = whatsapp_client

    # ------------------------------------------------------------- persistence
    def _store_content(self, text: str | None) -> tuple[str | None, str | None]:
        """Return (ciphertext, preview) honouring the content-storage flag."""
        if not text:
            return None, None
        preview = text[:PREVIEW_CHARS]
        if not self._bot.features.store_message_content:
            # Metadata-only mode: keep a short preview for operators, no body.
            return None, preview
        return self._cipher.encrypt(text), preview

    async def _resolve_contact(self, event: InboundMessage) -> Contact:
        """Find or create the contact for an inbound wa_id."""
        phone = normalise_phone(event.wa_id, key=self._secret)
        contact, created = await self._contacts.get_or_create(
            business_id=self._business.business_id,
            phone_hash=phone.hash,
            phone_encrypted=self._cipher.encrypt(phone.e164) or "",
            phone_masked=phone.masked,
            first_name=event.profile_name,
            locale=self._business.default_language,
        )
        if created:
            logger.info("contact_created", contact_id=contact.id)
        elif event.profile_name and not contact.first_name:
            contact.first_name = event.profile_name
        return contact

    async def _history(self, conversation: Conversation) -> list[tuple[str, str]]:
        """Recent turns as (role, text) for the prompt."""
        if not self._bot.history_limit:
            return []
        rows = await self._messages.recent_history(
            conversation.id, limit=self._bot.history_limit
        )
        history: list[tuple[str, str]] = []
        for message in rows:
            role = "Customer" if message.direction is Direction.INBOUND else "Assistant"
            text = ""
            if message.content_encrypted:
                text = self._cipher.decrypt(message.content_encrypted) or ""
            if not text:
                text = message.content_preview or ""
            if text:
                history.append((role, text))
        return history

    # ------------------------------------------------------------------- send
    async def _send_reply(
        self, contact: Contact, conversation: Conversation, reply: BotReply
    ) -> str | None:
        """Send a free-form reply, respecting the 24-hour service window."""
        if reply.suppress_send or not reply.text:
            return None

        now = datetime.now(UTC)
        if not service_window_open(contact.last_inbound_at, now):
            # Outside the window only an approved template may be sent, and a
            # conversational reply is not one. Fail closed and let a human pick
            # it up rather than attempting a send Meta will reject.
            logger.info("reply_skipped_outside_service_window", contact_id=contact.id)
            return None

        phone = self._cipher.decrypt(contact.phone_encrypted)
        if not phone:
            logger.error("contact_phone_undecryptable", contact_id=contact.id)
            return None

        send_text = getattr(self._whatsapp, "send_text", None)
        if send_text is None:
            return None

        result = await send_text(phone.lstrip("+"), reply.text)
        message_id = getattr(result, "meta_message_id", None)

        encrypted, preview = self._store_content(reply.text)
        await self._messages.add_message(
            business_id=self._business.business_id,
            conversation_id=conversation.id,
            contact_id=contact.id,
            direction=Direction.OUTBOUND,
            message_type=MessageType.TEXT,
            meta_message_id=message_id,
            content_encrypted=encrypted,
            content_preview=preview,
            intent=reply.intent.value,
            confidence=reply.confidence,
            delivery_status=DeliveryStatus.SENT,
            correlation_id=get_correlation_id(),
        )
        await self._contacts.touch_outbound(contact)
        return message_id

    # ---------------------------------------------------------------- inbound
    async def handle_inbound(self, event: InboundMessage) -> ProcessingOutcome:
        """Process one inbound customer message."""
        business_id = self._business.business_id
        contact = await self._resolve_contact(event)
        conversation = await self._conversations.get_or_create(
            business_id=business_id,
            contact_id=contact.id,
            expiry_hours=self._bot.conversation_expiry_hours,
            correlation_id=get_correlation_id(),
        )
        await self._conversations.touch_inbound(
            conversation, self._bot.conversation_expiry_hours
        )
        await self._contacts.touch_inbound(contact, event.timestamp)

        text = event.text or ""
        encrypted, preview = self._store_content(text)
        await self._messages.add_message(
            business_id=business_id,
            conversation_id=conversation.id,
            contact_id=contact.id,
            direction=Direction.INBOUND,
            message_type=event.message_type,
            meta_message_id=event.meta_message_id or None,
            content_encrypted=encrypted,
            content_preview=preview,
            media_reference=event.media,
            correlation_id=get_correlation_id(),
        )

        if self._bot.features.auto_mark_read and event.meta_message_id:
            mark_read = getattr(self._whatsapp, "mark_as_read", None)
            if mark_read is not None:
                await mark_read(event.meta_message_id)

        # --- 1. Opt-out is processed before anything else, always. ----------
        from app.domain.rules import detect_opt_out

        stop_word = detect_opt_out(text, self._bot)
        if stop_word:
            await self._consent.opt_out(
                contact,
                source="whatsapp_message",
                evidence=f"Customer sent stop word: {stop_word}",
                occurred_at=event.timestamp,
            )
            reply = BotReply(
                text=self._response.optout_confirmation(),
                intent=Intent.OPT_OUT,
                confidence=1.0,
                deterministic=True,
            )
            await self._send_reply(contact, conversation, reply)
            logger.info("opt_out_processed", contact_id=contact.id)
            return ProcessingOutcome(
                handled=True,
                reason="opt_out",
                intent=Intent.OPT_OUT,
                replied=True,
                reply_text=reply.text,
            )

        # --- 2. Pause switches. --------------------------------------------
        if await self._bot_state.is_paused(business_id):
            logger.info("bot_globally_paused_no_reply", contact_id=contact.id)
            return ProcessingOutcome(handled=True, reason="bot_paused_global")

        if contact.bot_paused:
            logger.info("bot_paused_for_contact", contact_id=contact.id)
            return ProcessingOutcome(handled=True, reason="bot_paused_contact")

        if conversation.state is ConversationState.HUMAN_HANDLING:
            return ProcessingOutcome(handled=True, reason="human_handling")

        # --- 3. Unsupported message types get a polite, fixed reply. -------
        if event.message_type is MessageType.UNSUPPORTED or (
            event.message_type.value not in self._bot.supported_message_types
            and not text
        ):
            reply = BotReply(
                text=self._response.unsupported_message_text(),
                intent=Intent.UNKNOWN,
                deterministic=True,
            )
            await self._send_reply(contact, conversation, reply)
            return ProcessingOutcome(
                handled=True, reason="unsupported_type", replied=True, reply_text=reply.text
            )

        if not text.strip():
            return ProcessingOutcome(handled=True, reason="empty_message")

        # --- 4. Retrieval + analysis. --------------------------------------
        retrieval = RetrievalResult(query=text, hits=[])
        if self._bot.features.knowledge_retrieval:
            retrieval = await self._knowledge.search(text, top_k=4)

        llm_failed = False
        try:
            analysis = await self._intent.analyse(
                text,
                retrieval=retrieval,
                history=await self._history(conversation),
                language=contact.locale,
            )
        except (LLMError, TimeoutError) as exc:
            logger.warning("llm_failed_using_fallback", error=type(exc).__name__)
            llm_failed = True
            analysis = IntentResult(intent=Intent.UNKNOWN, confidence=0.0)

        await self._conversations.record_analysis(
            conversation, intent=analysis.intent.value, confidence=analysis.confidence
        )

        # --- 5. Opt-in detected by the analyser. ---------------------------
        if analysis.intent is Intent.OPT_IN:
            await self._consent.opt_in(
                contact,
                source="whatsapp_message",
                evidence="Customer sent an explicit opt-in keyword.",
                occurred_at=event.timestamp,
            )

        # --- 6. Compose the reply. -----------------------------------------
        now = datetime.now(UTC)
        reply = self._response.compose(
            analysis, retrieval=retrieval, now=now, llm_failed=llm_failed
        )

        # --- 7. Escalate if required. --------------------------------------
        escalated = False
        if reply.escalate:
            await self._escalation.escalate(
                conversation=conversation,
                contact=contact,
                reason=reply.escalation_reason or EscalationReason.LOW_CONFIDENCE,
                confidence=reply.confidence,
                pause_bot=reply.pause_bot,
            )
            escalated = True

        # --- 8. Send. -------------------------------------------------------
        replied = False
        try:
            message_id = await self._send_reply(contact, conversation, reply)
            replied = message_id is not None
        except (WhatsAppAPIError, CircuitOpenError) as exc:
            # The inbound message is already persisted; the send failure is
            # recorded and surfaced rather than retried inline.
            logger.error(
                "reply_send_failed",
                contact_id=contact.id,
                error_code=getattr(exc, "code", "unknown"),
                meta_code=getattr(exc, "meta_code", None),
            )

        return ProcessingOutcome(
            handled=True,
            reason="processed",
            intent=reply.intent,
            replied=replied,
            escalated=escalated,
            reply_text=reply.text,
        )

    # ----------------------------------------------------------------- status
    async def handle_status(self, event: StatusUpdate) -> ProcessingOutcome:
        """Apply a delivery-status webhook to the stored outbound message."""
        business_id = self._business.business_id
        message = await self._messages.update_delivery_status(
            business_id=business_id,
            meta_message_id=event.meta_message_id,
            status=event.status,
            error_code=event.error_code,
            error_detail=event.error_title,
            moment=event.timestamp,
        )
        if message is None:
            logger.info(
                "status_for_unknown_message", status=event.status.value
            )
            return ProcessingOutcome(handled=True, reason="unknown_message")

        # Mirror the status onto the campaign recipient row, if any.
        if message.campaign_id:
            recipient = await self._campaign_recipients.get_by_meta_id(
                message.campaign_id, event.meta_message_id
            )
            if recipient is not None:
                from app.domain.enums import CampaignRecipientStatus

                mapping = {
                    DeliveryStatus.DELIVERED: CampaignRecipientStatus.DELIVERED,
                    DeliveryStatus.READ: CampaignRecipientStatus.READ,
                    DeliveryStatus.FAILED: CampaignRecipientStatus.FAILED,
                }
                mapped = mapping.get(event.status)
                if mapped is not None:
                    if mapped is CampaignRecipientStatus.FAILED:
                        await self._campaign_recipients.mark_failed(
                            recipient,
                            error_code=event.error_code,
                            error_detail=event.error_title,
                        )
                    else:
                        await self._campaign_recipients.set_status(recipient, mapped)

        # A permanent delivery failure means we should stop trying this number.
        if event.status is DeliveryStatus.FAILED and event.error_code in (131026, 131047):
            contact = await self._contacts.get_by_id(business_id, message.contact_id)
            if contact is not None and contact.consent_status is not ConsentStatus.OPTED_OUT:
                await self._consent.suppress(
                    contact,
                    reason="delivery_failure",
                    detail=f"Meta error {event.error_code}: {event.error_title}",
                )

        return ProcessingOutcome(handled=True, reason=f"status_{event.status.value}")
