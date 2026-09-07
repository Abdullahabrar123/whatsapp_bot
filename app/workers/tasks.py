"""Background tasks for async webhook processing and maintenance.

Executed either asynchronously via arq / Redis or synchronously in-memory
when background queues are bypassed (e.g. in tests or local development).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config_loader import AppConfig
from app.core.db import create_engine, create_session_factory, session_scope
from app.core.logging import get_logger, set_correlation_id
from app.core.security import FieldCipher
from app.integrations.llm_client import build_llm_client
from app.integrations.webhook_parser import InboundMessage, StatusUpdate
from app.integrations.whatsapp_client import WhatsAppClient
from app.repositories.campaigns import CampaignRecipientRepository, CampaignRepository
from app.repositories.consent import BotStateRepository, ConsentRepository, EscalationRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import (
    ConversationRepository,
    DeadLetterRepository,
    MessageRepository,
    WebhookEventRepository,
)
from app.services.campaign_service import CampaignService
from app.services.consent_service import ConsentService
from app.services.conversation_service import ConversationService
from app.services.escalation_service import EscalationService
from app.services.intent_service import IntentService
from app.services.knowledge_service import KnowledgeService
from app.services.response_service import ResponseService

logger = get_logger(__name__)


async def build_service_container(
    ctx: dict[str, Any] | None = None,  # noqa: ARG001 - arq passes its context; kept for call symmetry
) -> dict[str, Any]:
    """Helper to build db session, repositories, and services for worker tasks."""
    from app.core.settings import get_settings

    settings = get_settings()
    config = AppConfig.load(
        settings.business_config_path,
        settings.bot_config_path,
        settings.templates_config_path,
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = FieldCipher(settings.app_secret_key.get_secret_value())
    whatsapp_client = WhatsAppClient(settings)
    llm_client = build_llm_client(settings)

    knowledge_svc = KnowledgeService(
        config.business,
        project_root=settings.business_config_path.parent.parent,
        llm_client=llm_client,
        embeddings_enabled=False,
    )
    knowledge_svc.build_index()

    return {
        "settings": settings,
        "config": config,
        "engine": engine,
        "session_factory": session_factory,
        "cipher": cipher,
        "whatsapp_client": whatsapp_client,
        "llm_client": llm_client,
        "knowledge_service": knowledge_svc,
    }


async def process_inbound_message_task(
    ctx: dict[str, Any],
    event_dict: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Process an inbound message asynchronously."""
    set_correlation_id(correlation_id)
    container = ctx.get("container") or await build_service_container(ctx)
    settings = container["settings"]
    config = container["config"]
    cipher = container["cipher"]
    session_factory = container["session_factory"]

    # Reconstruct InboundMessage
    from app.domain.enums import MessageType

    msg_type_str = event_dict.get("message_type")
    msg_type = (
        MessageType(msg_type_str)
        if msg_type_str in [m.value for m in MessageType]
        else MessageType.TEXT
    )

    ts = event_dict.get("timestamp")
    if isinstance(ts, str):
        timestamp = datetime.fromisoformat(ts)
    elif isinstance(ts, int | float):
        timestamp = datetime.fromtimestamp(ts, tz=UTC)
    else:
        timestamp = datetime.now(UTC)

    event = InboundMessage(
        event_key=event_dict["event_key"],
        meta_message_id=event_dict.get("meta_message_id", ""),
        wa_id=event_dict["wa_id"],
        phone_number_id=event_dict.get("phone_number_id", ""),
        waba_id=event_dict.get("waba_id", ""),
        message_type=msg_type,
        timestamp=timestamp,
        text=event_dict.get("text"),
        profile_name=event_dict.get("profile_name"),
        interactive_id=event_dict.get("interactive_id"),
        interactive_title=event_dict.get("interactive_title"),
        media=event_dict.get("media"),
        location=event_dict.get("location"),
        context_message_id=event_dict.get("context_message_id"),
    )

    async with session_scope(session_factory) as session:
        contacts = ContactRepository(session)
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        suppressions = SuppressionRepository(session)
        consents = ConsentRepository(session)
        escalations = EscalationRepository(session)
        bot_state = BotStateRepository(session)
        campaigns = CampaignRepository(session)
        campaign_recipients = CampaignRecipientRepository(session)
        webhook_events = WebhookEventRepository(session)
        dead_letters = DeadLetterRepository(session)

        consent_svc = ConsentService(
            business=config.business,
            bot=config.bot,
            contacts=contacts,
            consents=consents,
            suppressions=suppressions,
        )
        escalation_svc = EscalationService(
            business=config.business,
            escalations=escalations,
            conversations=conversations,
            messages=messages,
            contacts=contacts,
            decrypt=cipher.decrypt,
        )
        intent_svc = IntentService(config.business, config.bot, container["llm_client"])
        response_svc = ResponseService(config.business, config.bot)

        conv_svc = ConversationService(
            business=config.business,
            bot=config.bot,
            cipher=cipher,
            secret_key=settings.app_secret_key.get_secret_value(),
            contacts=contacts,
            conversations=conversations,
            messages=messages,
            suppressions=suppressions,
            bot_state=bot_state,
            campaigns=campaigns,
            campaign_recipients=campaign_recipients,
            consent_service=consent_svc,
            escalation_service=escalation_svc,
            intent_service=intent_svc,
            response_service=response_svc,
            knowledge_service=container["knowledge_service"],
            whatsapp_client=container["whatsapp_client"],
        )

        try:
            outcome = await conv_svc.handle_inbound(event)
            await webhook_events.mark_processed(config.business.business_id, event.event_key)
            return {
                "handled": outcome.handled,
                "reason": outcome.reason,
                "replied": outcome.replied,
                "reply_text": outcome.reply_text,
            }
        except Exception as exc:
            attempts = await webhook_events.mark_failed(
                config.business.business_id, event.event_key, str(exc)
            )
            if attempts >= 3:
                await dead_letters.create(
                    business_id=config.business.business_id,
                    source="inbound_webhook",
                    error=str(exc),
                    event_key=event.event_key,
                    payload=event_dict,
                    attempts=attempts,
                    correlation_id=correlation_id,
                )
            logger.error(
                "inbound_processing_task_failed", event_key=event.event_key, error=str(exc)
            )
            raise


async def process_status_update_task(
    ctx: dict[str, Any],
    event_dict: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Process a delivery status update webhook."""
    set_correlation_id(correlation_id)
    container = ctx.get("container") or await build_service_container(ctx)
    settings = container["settings"]
    config = container["config"]
    cipher = container["cipher"]
    session_factory = container["session_factory"]

    from app.domain.enums import DeliveryStatus

    status_str = event_dict.get("status")
    status = (
        DeliveryStatus(status_str)
        if status_str in [s.value for s in DeliveryStatus]
        else DeliveryStatus.SENT
    )

    ts = event_dict.get("timestamp")
    if isinstance(ts, str):
        timestamp = datetime.fromisoformat(ts)
    elif isinstance(ts, int | float):
        timestamp = datetime.fromtimestamp(ts, tz=UTC)
    else:
        timestamp = datetime.now(UTC)

    event = StatusUpdate(
        event_key=event_dict["event_key"],
        meta_message_id=event_dict["meta_message_id"],
        recipient_wa_id=event_dict.get("recipient_wa_id", ""),
        phone_number_id=event_dict.get("phone_number_id", ""),
        waba_id=event_dict.get("waba_id", ""),
        status=status,
        timestamp=timestamp,
        conversation_id=event_dict.get("conversation_id"),
        conversation_origin=event_dict.get("conversation_origin"),
        pricing_category=event_dict.get("pricing_category"),
        errors=event_dict.get("errors", []),
    )

    async with session_scope(session_factory) as session:
        contacts = ContactRepository(session)
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        suppressions = SuppressionRepository(session)
        consents = ConsentRepository(session)
        escalations = EscalationRepository(session)
        bot_state = BotStateRepository(session)
        campaigns = CampaignRepository(session)
        campaign_recipients = CampaignRecipientRepository(session)
        webhook_events = WebhookEventRepository(session)

        consent_svc = ConsentService(
            business=config.business,
            bot=config.bot,
            contacts=contacts,
            consents=consents,
            suppressions=suppressions,
        )
        escalation_svc = EscalationService(
            business=config.business,
            escalations=escalations,
            conversations=conversations,
            messages=messages,
            contacts=contacts,
            decrypt=cipher.decrypt,
        )
        intent_svc = IntentService(config.business, config.bot, container["llm_client"])
        response_svc = ResponseService(config.business, config.bot)

        conv_svc = ConversationService(
            business=config.business,
            bot=config.bot,
            cipher=cipher,
            secret_key=settings.app_secret_key.get_secret_value(),
            contacts=contacts,
            conversations=conversations,
            messages=messages,
            suppressions=suppressions,
            bot_state=bot_state,
            campaigns=campaigns,
            campaign_recipients=campaign_recipients,
            consent_service=consent_svc,
            escalation_service=escalation_svc,
            intent_service=intent_svc,
            response_service=response_svc,
            knowledge_service=container["knowledge_service"],
            whatsapp_client=container["whatsapp_client"],
        )

        outcome = await conv_svc.handle_status(event)
        await webhook_events.mark_processed(config.business.business_id, event.event_key)
        return {"handled": outcome.handled, "reason": outcome.reason}


async def execute_campaign_task(
    ctx: dict[str, Any],
    campaign_id: int,
    dry_run: bool = False,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Execute or dry-run a campaign in the background."""
    set_correlation_id(correlation_id)
    container = ctx.get("container") or await build_service_container(ctx)
    settings = container["settings"]
    config = container["config"]
    cipher = container["cipher"]
    session_factory = container["session_factory"]

    async with session_scope(session_factory) as session:
        campaigns = CampaignRepository(session)
        recipients = CampaignRecipientRepository(session)
        contacts = ContactRepository(session)
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        suppressions = SuppressionRepository(session)
        consents = ConsentRepository(session)

        campaign = await campaigns.get_by_id(config.business.business_id, campaign_id)
        if campaign is None:
            return {"status": "error", "message": f"Campaign {campaign_id} not found"}

        consent_svc = ConsentService(
            business=config.business,
            bot=config.bot,
            contacts=contacts,
            consents=consents,
            suppressions=suppressions,
        )

        campaign_svc = CampaignService(
            business=config.business,
            bot=config.bot,
            templates=config.templates,
            cipher=cipher,
            secret_key=settings.app_secret_key.get_secret_value(),
            campaigns=campaigns,
            campaign_recipients=recipients,
            contacts=contacts,
            conversations=conversations,
            messages=messages,
            consent_service=consent_svc,
            whatsapp_client=container["whatsapp_client"],
        )

        summary = await campaign_svc.execute_campaign(campaign, dry_run=dry_run)
        return {
            "campaign_id": summary.campaign_id,
            "campaign_name": summary.campaign_name,
            "status": summary.status.value,
            "total": summary.total,
            "sent": summary.sent,
            "failed": summary.failed,
            "auto_paused": summary.auto_paused,
            "pause_reason": summary.pause_reason,
        }


async def cleanup_expired_conversations_task(
    ctx: dict[str, Any],
    business_id: str,
) -> int:
    """Periodic task: expire stale service conversations."""
    container = ctx.get("container") or await build_service_container(ctx)
    session_factory = container["session_factory"]
    async with session_scope(session_factory) as session:
        repo = ConversationRepository(session)
        count = await repo.expire_stale(business_id)
        logger.info("expired_stale_conversations", count=count, business_id=business_id)
        return count


async def purge_retention_data_task(
    ctx: dict[str, Any],
    business_id: str,
    retention_days: int = 90,
) -> dict[str, int]:
    """Periodic task: purge old webhook ledger and messages beyond retention period."""
    container = ctx.get("container") or await build_service_container(ctx)
    session_factory = container["session_factory"]
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with session_scope(session_factory) as session:
        webhook_repo = WebhookEventRepository(session)
        message_repo = MessageRepository(session)

        purged_webhooks = await webhook_repo.purge_older_than(business_id, cutoff)
        purged_messages = await message_repo.delete_older_than(business_id, cutoff)

        logger.info(
            "retention_data_purged",
            business_id=business_id,
            purged_webhooks=purged_webhooks,
            purged_messages=purged_messages,
        )
        return {"purged_webhooks": purged_webhooks, "purged_messages": purged_messages}
