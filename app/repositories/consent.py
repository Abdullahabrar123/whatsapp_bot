"""Consent, escalation and bot-state repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.domain.enums import (
    ConsentPurpose,
    ConsentStatus,
    EscalationStatus,
)
from app.domain.models import BotState, ConsentRecord, Escalation, utcnow
from app.repositories.base import BaseRepository


class ConsentRepository(BaseRepository[ConsentRecord]):
    """Append-only consent evidence.

    Records are never updated or deleted: the history is the audit trail that
    demonstrates a lawful basis for each business-initiated message.
    """

    async def record(
        self,
        *,
        business_id: str,
        contact_id: int,
        status: ConsentStatus,
        purpose: ConsentPurpose,
        source: str,
        occurred_at: datetime,
        evidence: str | None = None,
        correlation_id: str | None = None,
    ) -> ConsentRecord:
        record = ConsentRecord(
            business_id=business_id,
            contact_id=contact_id,
            status=status,
            purpose=purpose,
            source=source,
            occurred_at=occurred_at,
            evidence=evidence,
            correlation_id=correlation_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def latest_for_purpose(
        self, contact_id: int, purpose: ConsentPurpose
    ) -> ConsentRecord | None:
        stmt = (
            select(ConsentRecord)
            .where(
                ConsentRecord.contact_id == contact_id,
                ConsentRecord.purpose == purpose,
            )
            .order_by(ConsentRecord.occurred_at.desc(), ConsentRecord.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def history(self, contact_id: int, *, limit: int = 50) -> list[ConsentRecord]:
        stmt = (
            select(ConsentRecord)
            .where(ConsentRecord.contact_id == contact_id)
            .order_by(ConsentRecord.occurred_at.desc(), ConsentRecord.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, business_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ConsentRecord)
            .where(ConsentRecord.business_id == business_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())


class EscalationRepository(BaseRepository[Escalation]):
    async def create(
        self,
        *,
        business_id: str,
        conversation_id: int,
        contact_id: int,
        reason: str,
        summary: str,
        detected_intent: str | None = None,
        confidence: float | None = None,
        correlation_id: str | None = None,
    ) -> Escalation:
        escalation = Escalation(
            business_id=business_id,
            conversation_id=conversation_id,
            contact_id=contact_id,
            reason=reason,
            summary=summary,
            detected_intent=detected_intent,
            confidence=confidence,
            status=EscalationStatus.OPEN,
            correlation_id=correlation_id,
        )
        self.session.add(escalation)
        await self.session.flush()
        return escalation

    async def get_open_for_conversation(self, conversation_id: int) -> Escalation | None:
        stmt = (
            select(Escalation)
            .where(
                Escalation.conversation_id == conversation_id,
                Escalation.status != EscalationStatus.RESOLVED,
            )
            .order_by(Escalation.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, business_id: str, escalation_id: int) -> Escalation | None:
        stmt = select(Escalation).where(
            Escalation.business_id == business_id, Escalation.id == escalation_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_open(self, business_id: str, *, limit: int = 50) -> list[Escalation]:
        stmt = (
            select(Escalation)
            .where(
                Escalation.business_id == business_id,
                Escalation.status != EscalationStatus.RESOLVED,
            )
            .order_by(Escalation.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def resolve(
        self, escalation: Escalation, *, note: str | None = None, actor: str | None = None
    ) -> None:
        escalation.status = EscalationStatus.RESOLVED
        escalation.resolution_note = note
        escalation.assigned_to = actor or escalation.assigned_to
        escalation.resolved_at = utcnow()
        await self.session.flush()

    async def acknowledge(self, escalation: Escalation, *, actor: str) -> None:
        escalation.status = EscalationStatus.ACKNOWLEDGED
        escalation.assigned_to = actor
        await self.session.flush()

    async def count_open(self, business_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(Escalation)
            .where(
                Escalation.business_id == business_id,
                Escalation.status != EscalationStatus.RESOLVED,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())


class BotStateRepository(BaseRepository[BotState]):
    """Global pause switch, one row per business."""

    async def get(self, business_id: str) -> BotState | None:
        stmt = select(BotState).where(BotState.business_id == business_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create(self, business_id: str) -> BotState:
        existing = await self.get(business_id)
        if existing is not None:
            return existing
        state = BotState(business_id=business_id, paused=False)
        self.session.add(state)
        await self.session.flush()
        return state

    async def set_paused(
        self,
        business_id: str,
        *,
        paused: bool,
        reason: str | None = None,
        actor: str | None = None,
    ) -> BotState:
        state = await self.get_or_create(business_id)
        state.paused = paused
        state.pause_reason = reason if paused else None
        state.paused_by = actor if paused else None
        state.paused_at = utcnow() if paused else None
        await self.session.flush()
        return state

    async def is_paused(self, business_id: str) -> bool:
        state = await self.get(business_id)
        return bool(state and state.paused)
