"""Unit tests for human escalation service and CRM adapter."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_loader import AppConfig
from app.core.security import FieldCipher
from app.domain.enums import EscalationReason
from app.repositories.consent import EscalationRepository
from app.repositories.contacts import ContactRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.escalation_service import EscalationPayload, EscalationService

SECRET = "test_key_minimum_32_characters_for_cipher_and_hashing_ok"


class MockCRMAdapter:
    def __init__(self) -> None:
        self.tickets: list[EscalationPayload] = []

    @property
    def name(self) -> str:
        return "mock_crm"

    async def create_ticket(self, payload: EscalationPayload) -> str:
        self.tickets.append(payload)
        return f"TICKET-{len(self.tickets)}"


@pytest.mark.asyncio
async def test_escalation_creation_and_crm_push(
    test_config: AppConfig,
    db_session: AsyncSession,
    cipher: FieldCipher,
):
    contacts = ContactRepository(db_session)
    conversations = ConversationRepository(db_session)
    messages = MessageRepository(db_session)
    escalations = EscalationRepository(db_session)
    crm = MockCRMAdapter()

    svc = EscalationService(
        business=test_config.business,
        escalations=escalations,
        conversations=conversations,
        messages=messages,
        contacts=contacts,
        crm=crm,
        decrypt=cipher.decrypt,
    )

    from app.domain.phone import normalise_phone

    phone = normalise_phone("+447400123456", key=SECRET)
    contact, _ = await contacts.get_or_create(
        business_id=test_config.business.business_id,
        phone_hash=phone.hash,
        phone_encrypted=cipher.encrypt(phone.e164) or "",
        phone_masked=phone.masked,
        first_name="Alice",
    )
    conv = await conversations.create(
        business_id=test_config.business.business_id,
        contact_id=contact.id,
        expiry_hours=24,
    )

    esc = await svc.escalate(
        conversation=conv,
        contact=contact,
        reason=EscalationReason.CUSTOMER_REQUEST,
        confidence=0.95,
        pause_bot=True,
    )

    assert esc.id is not None
    assert esc.reason == "customer_request"
    assert esc.crm_reference == "TICKET-1"
    assert len(crm.tickets) == 1
    assert contact.bot_paused is True

    # Resolve escalation
    resolved = await svc.resolve(
        business_id=test_config.business.business_id,
        escalation_id=esc.id,
        note="Resolved by operator",
        resume_bot=True,
    )
    assert resolved is not None
    assert resolved.status.value == "resolved"
    assert contact.bot_paused is False
