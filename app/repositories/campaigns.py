"""Campaign repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.enums import CampaignRecipientStatus, CampaignStatus
from app.domain.models import Campaign, CampaignRecipient, utcnow
from app.repositories.base import BaseRepository


class CampaignRepository(BaseRepository[Campaign]):
    async def create(
        self,
        *,
        business_id: str,
        name: str,
        template_name: str,
        template_language: str,
        correlation_id: str | None = None,
    ) -> Campaign:
        campaign = Campaign(
            business_id=business_id,
            name=name,
            template_name=template_name,
            template_language=template_language,
            status=CampaignStatus.DRAFT,
            correlation_id=correlation_id,
        )
        self.session.add(campaign)
        await self.session.flush()
        return campaign

    async def get_by_name(self, business_id: str, name: str) -> Campaign | None:
        stmt = select(Campaign).where(
            Campaign.business_id == business_id, Campaign.name == name
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, business_id: str, campaign_id: int) -> Campaign | None:
        stmt = select(Campaign).where(
            Campaign.business_id == business_id, Campaign.id == campaign_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_status(
        self, campaign: Campaign, status: CampaignStatus, *, reason: str | None = None
    ) -> None:
        campaign.status = status
        if status is CampaignStatus.PAUSED:
            campaign.pause_reason = reason
        elif status is CampaignStatus.RUNNING:
            campaign.pause_reason = None
            campaign.started_at = campaign.started_at or utcnow()
        elif status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED, CampaignStatus.FAILED):
            campaign.completed_at = utcnow()
        await self.session.flush()

    async def list_all(self, business_id: str, *, limit: int = 50) -> list[Campaign]:
        stmt = (
            select(Campaign)
            .where(Campaign.business_id == business_id)
            .order_by(Campaign.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def refresh_counters(self, campaign: Campaign) -> None:
        """Recompute campaign counters from recipient rows."""
        stmt = (
            select(CampaignRecipient.status, func.count())
            .where(CampaignRecipient.campaign_id == campaign.id)
            .group_by(CampaignRecipient.status)
        )
        counts = {
            str(status): int(count) for status, count in (await self.session.execute(stmt)).all()
        }
        campaign.total = sum(counts.values())
        campaign.eligible = counts.get(CampaignRecipientStatus.ELIGIBLE, 0) + counts.get(
            CampaignRecipientStatus.QUEUED, 0
        )
        campaign.rejected = counts.get(CampaignRecipientStatus.REJECTED, 0)
        campaign.sent = counts.get(CampaignRecipientStatus.SENT, 0)
        campaign.delivered = counts.get(CampaignRecipientStatus.DELIVERED, 0)
        campaign.read = counts.get(CampaignRecipientStatus.READ, 0)
        campaign.failed = counts.get(CampaignRecipientStatus.FAILED, 0)
        await self.session.flush()


class CampaignRecipientRepository(BaseRepository[CampaignRecipient]):
    async def create(
        self,
        *,
        campaign_id: int,
        contact_id: int,
        status: CampaignRecipientStatus,
        template_variables: dict[str, Any] | None = None,
        rejection_reason: str | None = None,
    ) -> CampaignRecipient:
        recipient = CampaignRecipient(
            campaign_id=campaign_id,
            contact_id=contact_id,
            status=status,
            template_variables=template_variables or {},
            rejection_reason=rejection_reason,
        )
        self.session.add(recipient)
        await self.session.flush()
        return recipient

    async def exists(self, campaign_id: int, contact_id: int) -> bool:
        stmt = select(CampaignRecipient.id).where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.contact_id == contact_id,
        )
        return (await self.session.execute(stmt)).first() is not None

    async def get(self, campaign_id: int, contact_id: int) -> CampaignRecipient | None:
        stmt = select(CampaignRecipient).where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.contact_id == contact_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_status(
        self, campaign_id: int, status: CampaignRecipientStatus, *, limit: int = 500
    ) -> list[CampaignRecipient]:
        stmt = (
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status == status,
            )
            .order_by(CampaignRecipient.id)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def mark_sent(
        self, recipient: CampaignRecipient, *, meta_message_id: str
    ) -> None:
        recipient.status = CampaignRecipientStatus.SENT
        recipient.meta_message_id = meta_message_id
        recipient.sent_at = utcnow()
        await self.session.flush()

    async def mark_failed(
        self,
        recipient: CampaignRecipient,
        *,
        error_code: int | None,
        error_detail: str | None,
    ) -> None:
        recipient.status = CampaignRecipientStatus.FAILED
        recipient.error_code = error_code
        recipient.error_detail = (error_detail or "")[:1000] or None
        await self.session.flush()

    async def set_status(
        self, recipient: CampaignRecipient, status: CampaignRecipientStatus
    ) -> None:
        recipient.status = status
        await self.session.flush()

    async def get_by_meta_id(
        self, campaign_id: int, meta_message_id: str
    ) -> CampaignRecipient | None:
        stmt = select(CampaignRecipient).where(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.meta_message_id == meta_message_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def counts_by_status(self, campaign_id: int) -> dict[str, int]:
        stmt = (
            select(CampaignRecipient.status, func.count())
            .where(CampaignRecipient.campaign_id == campaign_id)
            .group_by(CampaignRecipient.status)
        )
        return {
            str(status): int(count) for status, count in (await self.session.execute(stmt)).all()
        }
