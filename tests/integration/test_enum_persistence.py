"""Enum columns must survive a database round-trip as enum members.

These columns were declared ``Mapped[SomeEnum]`` over a plain ``String``, so
SQLAlchemy read them back as ``str``. Every ``is`` / ``is not`` comparison
against an enum member then evaluated False regardless of the stored value --
which made the send-eligibility rules reject genuinely opted-in recipients.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ConsentStatus, DeliveryStatus, Direction, MessageType
from app.domain.models import Contact, Message
from app.repositories.contacts import ContactRepository
from app.repositories.conversations import ConversationRepository, MessageRepository


@pytest.mark.asyncio
async def test_contact_consent_status_round_trips_as_enum(db_session: AsyncSession):
    contacts = ContactRepository(db_session)
    contact, _ = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-enum-1",
        phone_encrypted="enc",
        phone_masked="+12*******01",
    )
    await contacts.set_consent_status(contact, ConsentStatus.OPTED_IN)
    await db_session.commit()
    db_session.expire_all()

    loaded = (
        await db_session.execute(select(Contact).where(Contact.phone_hash == "hash-enum-1"))
    ).scalar_one()

    assert isinstance(loaded.consent_status, ConsentStatus)
    # Identity comparison is what the eligibility rules actually use.
    assert loaded.consent_status is ConsentStatus.OPTED_IN
    assert loaded.consent_status is not ConsentStatus.OPTED_OUT


@pytest.mark.asyncio
async def test_message_enums_round_trip(db_session: AsyncSession):
    contacts = ContactRepository(db_session)
    conversations = ConversationRepository(db_session)
    messages = MessageRepository(db_session)

    contact, _ = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-enum-2",
        phone_encrypted="enc",
        phone_masked="+12*******02",
    )
    conversation = await conversations.get_or_create(
        business_id="demo", contact_id=contact.id, expiry_hours=24
    )
    message = await messages.add_message(
        business_id="demo",
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction=Direction.OUTBOUND,
        message_type=MessageType.TEXT,
        content_encrypted="enc",
        delivery_status=DeliveryStatus.DELIVERED,
    )
    message_id = message.id
    await db_session.commit()
    db_session.expire_all()

    loaded = (
        await db_session.execute(select(Message).where(Message.id == message_id))
    ).scalar_one()

    assert loaded.direction is Direction.OUTBOUND
    assert loaded.message_type is MessageType.TEXT
    assert loaded.delivery_status is DeliveryStatus.DELIVERED
