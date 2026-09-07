"""Repository tests for contact creation, tag handling and suppression."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ConsentStatus
from app.repositories.contacts import ContactRepository, SuppressionRepository


@pytest.mark.asyncio
async def test_get_or_create_persists_tags(db_session: AsyncSession):
    """Tags supplied at import time must reach the stored contact."""
    contacts = ContactRepository(db_session)

    contact, created = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-tags-1",
        phone_encrypted="enc",
        phone_masked="+44*****4321",
        first_name="Ada",
        tags=["vip", "newsletter"],
    )

    assert created is True
    assert contact.tags == {"values": ["vip", "newsletter"]}


@pytest.mark.asyncio
async def test_get_or_create_persists_all_import_columns(db_session: AsyncSession):
    """Every column the import CSV carries must reach the stored contact."""
    contacts = ContactRepository(db_session)

    contact, _ = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-cols-1",
        phone_encrypted="enc",
        phone_masked="+12*******01",
        external_id="CUST-001",
        first_name="Alice",
        last_name="Smith",
        locale="en",
        timezone_name="America/New_York",
        tags=["vip"],
    )

    assert contact.external_id == "CUST-001"
    assert contact.last_name == "Smith"
    assert contact.timezone_name == "America/New_York"
    assert contact.tags == {"values": ["vip"]}


@pytest.mark.asyncio
async def test_get_or_create_defaults_to_empty_tags(db_session: AsyncSession):
    contacts = ContactRepository(db_session)

    contact, _ = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-tags-2",
        phone_encrypted="enc",
        phone_masked="+44*****4322",
    )

    assert contact.tags == {"values": []}


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent(db_session: AsyncSession):
    """A second import of the same number returns the existing row, not a copy."""
    contacts = ContactRepository(db_session)

    first, created_first = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-tags-3",
        phone_encrypted="enc",
        phone_masked="+44*****4323",
        tags=["vip"],
    )
    second, created_second = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-tags-3",
        phone_encrypted="enc",
        phone_masked="+44*****4323",
        tags=["ignored-on-existing"],
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert second.tags == {"values": ["vip"]}


@pytest.mark.asyncio
async def test_import_never_implies_consent(db_session: AsyncSession):
    """Importing a contact must not grant permission to message them."""
    contacts = ContactRepository(db_session)

    contact, _ = await contacts.get_or_create(
        business_id="demo",
        phone_hash="hash-tags-4",
        phone_encrypted="enc",
        phone_masked="+44*****4324",
    )

    assert contact.consent_status is ConsentStatus.UNKNOWN


@pytest.mark.asyncio
async def test_suppression_create_marks_number_suppressed(db_session: AsyncSession):
    """SuppressionRepository.create (renamed from add) still suppresses the hash."""
    suppressions = SuppressionRepository(db_session)

    assert await suppressions.is_suppressed("demo", "hash-supp-1") is False

    await suppressions.create(
        business_id="demo",
        phone_hash="hash-supp-1",
        phone_masked="+44*****9999",
        reason="csv_import_opted_out",
    )

    assert await suppressions.is_suppressed("demo", "hash-supp-1") is True
