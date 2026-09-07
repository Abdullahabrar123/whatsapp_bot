"""Contact and suppression repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.domain.enums import ConsentStatus
from app.domain.models import Contact, SuppressionEntry, utcnow
from app.repositories.base import BaseRepository


class ContactRepository(BaseRepository[Contact]):
    """Contacts are always addressed by ``phone_hash``, never by raw number."""

    async def get_by_hash(self, business_id: str, phone_hash: str) -> Contact | None:
        stmt = select(Contact).where(
            Contact.business_id == business_id, Contact.phone_hash == phone_hash
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, business_id: str, contact_id: int) -> Contact | None:
        stmt = select(Contact).where(
            Contact.business_id == business_id, Contact.id == contact_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        business_id: str,
        phone_hash: str,
        phone_encrypted: str,
        phone_masked: str,
        external_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        locale: str = "en",
        timezone_name: str | None = None,
        tags: list[str] | None = None,
        consent_status: ConsentStatus = ConsentStatus.UNKNOWN,
    ) -> Contact:
        """Create a contact.

        ``consent_status`` defaults to UNKNOWN: importing a contact never
        implies permission to message them.
        """
        contact = Contact(
            business_id=business_id,
            phone_hash=phone_hash,
            phone_encrypted=phone_encrypted,
            phone_masked=phone_masked,
            external_id=external_id,
            first_name=first_name,
            last_name=last_name,
            locale=locale,
            timezone_name=timezone_name,
            tags={"values": tags or []},
            consent_status=consent_status,
        )
        self.session.add(contact)
        await self.session.flush()
        return contact

    async def get_or_create(
        self,
        *,
        business_id: str,
        phone_hash: str,
        phone_encrypted: str,
        phone_masked: str,
        external_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        locale: str = "en",
        timezone_name: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[Contact, bool]:
        """Return an existing contact or create one. Second value is ``created``."""
        existing = await self.get_by_hash(business_id, phone_hash)
        if existing is not None:
            return existing, False
        contact = await self.create(
            business_id=business_id,
            phone_hash=phone_hash,
            phone_encrypted=phone_encrypted,
            phone_masked=phone_masked,
            external_id=external_id,
            first_name=first_name,
            last_name=last_name,
            locale=locale,
            timezone_name=timezone_name,
            tags=tags,
        )
        return contact, True

    async def set_consent_status(self, contact: Contact, status: ConsentStatus) -> None:
        contact.consent_status = status
        await self.session.flush()

    async def set_bot_paused(
        self, contact: Contact, *, paused: bool, reason: str | None = None
    ) -> None:
        contact.bot_paused = paused
        contact.pause_reason = reason if paused else None
        await self.session.flush()

    async def touch_inbound(self, contact: Contact, moment: datetime | None = None) -> None:
        contact.last_inbound_at = moment or utcnow()
        await self.session.flush()

    async def touch_outbound(self, contact: Contact, moment: datetime | None = None) -> None:
        contact.last_outbound_at = moment or utcnow()
        await self.session.flush()

    async def list_by_consent(
        self, business_id: str, status: ConsentStatus, *, limit: int = 100
    ) -> list[Contact]:
        stmt = (
            select(Contact)
            .where(Contact.business_id == business_id, Contact.consent_status == status)
            .order_by(Contact.id)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, business_id: str) -> int:
        stmt = select(func.count()).select_from(Contact).where(Contact.business_id == business_id)
        return int((await self.session.execute(stmt)).scalar_one())


class SuppressionRepository(BaseRepository[SuppressionEntry]):
    """The permanent do-not-message list.

    There is deliberately no delete method: removing someone from suppression
    requires an explicit, audited opt-in event, handled by ConsentService.
    """

    async def is_suppressed(self, business_id: str, phone_hash: str) -> bool:
        stmt = select(SuppressionEntry.id).where(
            SuppressionEntry.business_id == business_id,
            SuppressionEntry.phone_hash == phone_hash,
        )
        return (await self.session.execute(stmt)).first() is not None

    async def create(
        self,
        *,
        business_id: str,
        phone_hash: str,
        phone_masked: str,
        reason: str,
        detail: str | None = None,
    ) -> SuppressionEntry:
        """Idempotently suppress a recipient."""
        stmt = select(SuppressionEntry).where(
            SuppressionEntry.business_id == business_id,
            SuppressionEntry.phone_hash == phone_hash,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        entry = SuppressionEntry(
            business_id=business_id,
            phone_hash=phone_hash,
            phone_masked=phone_masked,
            reason=reason,
            detail=detail,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def remove(self, business_id: str, phone_hash: str) -> bool:
        """Remove a suppression entry. Only ConsentService may call this."""
        stmt = select(SuppressionEntry).where(
            SuppressionEntry.business_id == business_id,
            SuppressionEntry.phone_hash == phone_hash,
        )
        entry = (await self.session.execute(stmt)).scalar_one_or_none()
        if entry is None:
            return False
        await self.session.delete(entry)
        await self.session.flush()
        return True

    async def list_all(self, business_id: str, *, limit: int = 200) -> list[SuppressionEntry]:
        stmt = (
            select(SuppressionEntry)
            .where(SuppressionEntry.business_id == business_id)
            .order_by(SuppressionEntry.suppressed_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, business_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(SuppressionEntry)
            .where(SuppressionEntry.business_id == business_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())
