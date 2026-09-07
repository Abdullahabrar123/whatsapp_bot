"""Seed demo contacts, consent records, and conversations for local testing."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config_loader import AppConfig
from app.core.db import create_engine, create_session_factory, session_scope
from app.core.security import FieldCipher
from app.core.settings import get_settings
from app.domain.enums import (
    ConsentPurpose,
    ConsentStatus,
    DeliveryStatus,
    Direction,
    MessageType,
)
from app.domain.models import Base
from app.domain.phone import normalise_phone
from app.repositories.consent import ConsentRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import ConversationRepository, MessageRepository


async def seed_demo_data() -> None:
    print("Seeding demo data into database...")
    settings = get_settings()
    config = AppConfig.load(
        settings.business_config_path,
        settings.bot_config_path,
        settings.templates_config_path,
    )
    business_id = config.business.business_id

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = FieldCipher(settings.app_secret_key.get_secret_value())

    # Create tables if not existing
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)

    async with session_scope(session_factory) as session:
        contacts = ContactRepository(session)
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        consents = ConsentRepository(session)
        suppressions = SuppressionRepository(session)

        # 1. Opted-in Contact
        phone1 = normalise_phone("+12025550101", key=settings.app_secret_key.get_secret_value())
        c1, _ = await contacts.get_or_create(
            business_id=business_id,
            phone_hash=phone1.hash,
            phone_encrypted=cipher.encrypt(phone1.e164) or "",
            phone_masked=phone1.masked,
            first_name="Alice",
        )
        await contacts.set_consent_status(c1, ConsentStatus.OPTED_IN)
        await consents.record(
            business_id=business_id,
            contact_id=c1.id,
            status=ConsentStatus.OPTED_IN,
            purpose=ConsentPurpose.MARKETING,
            source="demo_seed",
            occurred_at=now - timedelta(days=2),
        )

        # 2. Suppressed Contact
        phone2 = normalise_phone("+12025550103", key=settings.app_secret_key.get_secret_value())
        c2, _ = await contacts.get_or_create(
            business_id=business_id,
            phone_hash=phone2.hash,
            phone_encrypted=cipher.encrypt(phone2.e164) or "",
            phone_masked=phone2.masked,
            first_name="Charlie",
        )
        await contacts.set_consent_status(c2, ConsentStatus.OPTED_OUT)
        await suppressions.create(
            business_id=business_id,
            phone_hash=phone2.hash,
            phone_masked=phone2.masked,
            reason="customer_opt_out",
        )

        # 3. Create active demo conversation for c1
        conv = await conversations.get_or_create(
            business_id=business_id,
            contact_id=c1.id,
            expiry_hours=24,
        )
        await messages.add_message(
            business_id=business_id,
            conversation_id=conv.id,
            contact_id=c1.id,
            direction=Direction.INBOUND,
            message_type=MessageType.TEXT,
            content_encrypted=cipher.encrypt("Hi, what are your opening hours?"),
            content_preview="Hi, what are your opening hours?",
        )
        await messages.add_message(
            business_id=business_id,
            conversation_id=conv.id,
            contact_id=c1.id,
            direction=Direction.OUTBOUND,
            message_type=MessageType.TEXT,
            content_encrypted=cipher.encrypt(
                "BrightSmile Dental Care is open Monday to Friday 08:30 - 17:30."
            ),
            content_preview="BrightSmile Dental Care is open Monday to Friday...",
            delivery_status=DeliveryStatus.READ,
        )

    await engine.dispose()
    print("Demo data seeded successfully!")


if __name__ == "__main__":
    asyncio.run(seed_demo_data())
