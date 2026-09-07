"""Integration tests for conversational message processing and compliance flows."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_loader import AppConfig
from app.core.security import FieldCipher
from app.domain.enums import ConsentStatus, Intent, MessageType
from app.integrations.llm_client import DeterministicClient
from app.integrations.webhook_parser import InboundMessage
from app.repositories.campaigns import CampaignRecipientRepository, CampaignRepository
from app.repositories.consent import BotStateRepository, ConsentRepository, EscalationRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.consent_service import ConsentService
from app.services.conversation_service import ConversationService
from app.services.escalation_service import EscalationService
from app.services.intent_service import IntentService
from app.services.knowledge_service import KnowledgeService
from app.services.response_service import ResponseService
from tests.conftest import FakeWhatsAppClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET = "test_key_minimum_32_characters_for_cipher_and_hashing_ok"


@pytest.fixture
def conversation_service(
    test_config: AppConfig,
    db_session: AsyncSession,
    cipher: FieldCipher,
    fake_whatsapp: FakeWhatsAppClient,
) -> ConversationService:
    contacts = ContactRepository(db_session)
    conversations = ConversationRepository(db_session)
    messages = MessageRepository(db_session)
    suppressions = SuppressionRepository(db_session)
    consents = ConsentRepository(db_session)
    escalations = EscalationRepository(db_session)
    bot_state = BotStateRepository(db_session)
    campaigns = CampaignRepository(db_session)
    campaign_recipients = CampaignRecipientRepository(db_session)

    consent_svc = ConsentService(
        business=test_config.business,
        bot=test_config.bot,
        contacts=contacts,
        consents=consents,
        suppressions=suppressions,
    )
    escalation_svc = EscalationService(
        business=test_config.business,
        escalations=escalations,
        conversations=conversations,
        messages=messages,
        contacts=contacts,
    )
    intent_svc = IntentService(test_config.business, test_config.bot, DeterministicClient())
    response_svc = ResponseService(test_config.business, test_config.bot)
    knowledge_svc = KnowledgeService(
        test_config.business,
        project_root=PROJECT_ROOT,
        llm_client=DeterministicClient(),
        embeddings_enabled=False,
    )
    knowledge_svc.build_index()

    return ConversationService(
        business=test_config.business,
        bot=test_config.bot,
        cipher=cipher,
        secret_key=SECRET,
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
        knowledge_service=knowledge_svc,
        whatsapp_client=fake_whatsapp,
    )


@pytest.mark.asyncio
async def test_greeting_and_outbound_reply(
    conversation_service: ConversationService,
    fake_whatsapp: FakeWhatsAppClient,
):
    event = InboundMessage(
        event_key="msg_001",
        meta_message_id="wamid.test.001",
        wa_id="447400123456",
        phone_number_id="123456",
        waba_id="987654",
        message_type=MessageType.TEXT,
        timestamp=datetime.now(UTC),
        text="Hello there",
        profile_name="Alice",
    )

    outcome = await conversation_service.handle_inbound(event)
    assert outcome.handled is True
    assert outcome.intent == Intent.GREETING
    assert outcome.replied is True
    assert len(fake_whatsapp.sent_texts) == 1
    assert "BrightSmile" in fake_whatsapp.sent_texts[0]["body"]


@pytest.mark.asyncio
async def test_opt_out_stop_suppresses_immediately(
    conversation_service: ConversationService,
    db_session: AsyncSession,
    fake_whatsapp: FakeWhatsAppClient,
):
    event = InboundMessage(
        event_key="msg_002",
        meta_message_id="wamid.test.002",
        wa_id="447400123457",
        phone_number_id="123456",
        waba_id="987654",
        message_type=MessageType.TEXT,
        timestamp=datetime.now(UTC),
        text="STOP",
        profile_name="Bob",
    )

    outcome = await conversation_service.handle_inbound(event)
    assert outcome.handled is True
    assert outcome.intent == Intent.OPT_OUT
    assert outcome.replied is True

    # Verify contact is suppressed in database
    contacts = ContactRepository(db_session)
    suppressions = SuppressionRepository(db_session)
    from app.domain.phone import normalise_phone

    norm = normalise_phone("447400123457", key=SECRET)
    contact = await contacts.get_by_hash("brightsmile_dental", norm.hash)
    assert contact is not None
    assert contact.consent_status == ConsentStatus.OPTED_OUT

    is_suppressed = await suppressions.is_suppressed("brightsmile_dental", norm.hash)
    assert is_suppressed is True
