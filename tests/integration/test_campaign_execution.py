"""Integration tests for outbound campaign creation, staging, compliance check and dispatch."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_loader import AppConfig
from app.core.security import FieldCipher
from app.domain.enums import CampaignStatus, ConsentPurpose, ConsentStatus
from app.repositories.campaigns import CampaignRecipientRepository, CampaignRepository
from app.repositories.consent import ConsentRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.campaign_service import CampaignService
from app.services.consent_service import ConsentService
from tests.conftest import FakeWhatsAppClient

SECRET = "test_key_minimum_32_characters_for_cipher_and_hashing_ok"


@pytest.mark.asyncio
async def test_campaign_staging_and_dry_run(
    test_config: AppConfig,
    db_session: AsyncSession,
    cipher: FieldCipher,
    fake_whatsapp: FakeWhatsAppClient,
):
    campaigns = CampaignRepository(db_session)
    recipients = CampaignRecipientRepository(db_session)
    contacts = ContactRepository(db_session)
    conversations = ConversationRepository(db_session)
    messages = MessageRepository(db_session)
    suppressions = SuppressionRepository(db_session)
    consents = ConsentRepository(db_session)

    consent_svc = ConsentService(
        business=test_config.business,
        bot=test_config.bot,
        contacts=contacts,
        consents=consents,
        suppressions=suppressions,
    )

    campaign_svc = CampaignService(
        business=test_config.business,
        bot=test_config.bot,
        templates=test_config.templates,
        cipher=cipher,
        secret_key=SECRET,
        campaigns=campaigns,
        campaign_recipients=recipients,
        contacts=contacts,
        conversations=conversations,
        messages=messages,
        consent_service=consent_svc,
        whatsapp_client=fake_whatsapp,
    )

    campaign = await campaign_svc.create_campaign(
        name="test_campaign_01", template_name="appointment_reminder_v1"
    )

    # 1. Pre-seed an opted-in contact
    from app.domain.phone import normalise_phone

    phone = normalise_phone("+447400123456", key=SECRET)
    c1, _ = await contacts.get_or_create(
        business_id=test_config.business.business_id,
        phone_hash=phone.hash,
        phone_encrypted=cipher.encrypt(phone.e164) or "",
        phone_masked=phone.masked,
        first_name="Alice",
    )
    await contacts.set_consent_status(c1, ConsentStatus.OPTED_IN)
    await consents.record(
        business_id=test_config.business.business_id,
        contact_id=c1.id,
        status=ConsentStatus.OPTED_IN,
        purpose=ConsentPurpose.MARKETING,
        source="web",
        occurred_at=datetime.now(UTC),
    )

    recipient_rows = [
        {"phone_e164": "+447400123456", "first_name": "Alice"},
        {"phone_e164": "+447400123499", "first_name": "UnknownConsentPerson"},
    ]

    daytime = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    report = await campaign_svc.stage_recipients(campaign, recipient_rows, now=daytime)
    assert report.eligible == 1
    assert report.rejected == 1

    summary = await campaign_svc.execute_campaign(campaign, dry_run=True)
    assert summary.status == CampaignStatus.DRAFT
    assert len(fake_whatsapp.sent_templates) == 0

    # Live execution
    live_summary = await campaign_svc.execute_campaign(campaign, dry_run=False)
    assert live_summary.status == CampaignStatus.COMPLETED
    assert live_summary.sent == 1
    assert len(fake_whatsapp.sent_templates) == 1
