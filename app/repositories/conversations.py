"""Conversation, message, webhook-idempotency and dead-letter repositories."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.domain.enums import (
    ConversationState,
    DeliveryStatus,
    Direction,
    MessageType,
)
from app.domain.models import (
    Conversation,
    DeadLetter,
    Message,
    WebhookEvent,
    utcnow,
)
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    async def get_active(self, business_id: str, contact_id: int) -> Conversation | None:
        """Return the current open conversation, if one exists and is unexpired."""
        stmt = (
            select(Conversation)
            .where(
                Conversation.business_id == business_id,
                Conversation.contact_id == contact_id,
                Conversation.state.notin_(
                    [ConversationState.CLOSED, ConversationState.EXPIRED]
                ),
            )
            .order_by(Conversation.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, business_id: str, conversation_id: int) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.business_id == business_id, Conversation.id == conversation_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        business_id: str,
        contact_id: int,
        expiry_hours: int,
        correlation_id: str | None = None,
    ) -> Conversation:
        now = utcnow()
        conversation = Conversation(
            business_id=business_id,
            contact_id=contact_id,
            state=ConversationState.ACTIVE,
            expires_at=now + timedelta(hours=expiry_hours),
            service_window_expires_at=now + timedelta(hours=24),
            correlation_id=correlation_id,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get_or_create(
        self,
        *,
        business_id: str,
        contact_id: int,
        expiry_hours: int,
        correlation_id: str | None = None,
    ) -> Conversation:
        existing = await self.get_active(business_id, contact_id)
        now = utcnow()
        if existing is not None:
            if existing.expires_at and existing.expires_at <= now:
                existing.state = ConversationState.EXPIRED
                existing.closed_at = now
                await self.session.flush()
            else:
                return existing
        return await self.create(
            business_id=business_id,
            contact_id=contact_id,
            expiry_hours=expiry_hours,
            correlation_id=correlation_id,
        )

    async def touch_inbound(self, conversation: Conversation, expiry_hours: int) -> None:
        """Extend both the app expiry and Meta's 24-hour service window."""
        now = utcnow()
        conversation.expires_at = now + timedelta(hours=expiry_hours)
        conversation.service_window_expires_at = now + timedelta(hours=24)
        await self.session.flush()

    async def set_state(
        self, conversation: Conversation, state: ConversationState
    ) -> None:
        conversation.state = state
        if state in (ConversationState.CLOSED, ConversationState.EXPIRED):
            conversation.closed_at = utcnow()
        await self.session.flush()

    async def record_analysis(
        self, conversation: Conversation, *, intent: str, confidence: float
    ) -> None:
        conversation.last_intent = intent
        conversation.last_confidence = confidence
        await self.session.flush()

    async def mark_escalated(
        self, conversation: Conversation, *, human_agent: str | None = None
    ) -> None:
        conversation.escalated = True
        conversation.state = ConversationState.ESCALATED
        conversation.human_agent = human_agent
        await self.session.flush()

    async def expire_stale(self, business_id: str, *, now: datetime | None = None) -> int:
        """Close conversations past their expiry. Returns the number closed."""
        moment = now or utcnow()
        stmt = (
            update(Conversation)
            .where(
                Conversation.business_id == business_id,
                Conversation.expires_at.is_not(None),
                Conversation.expires_at <= moment,
                Conversation.state.notin_(
                    [ConversationState.CLOSED, ConversationState.EXPIRED]
                ),
            )
            .values(state=ConversationState.EXPIRED, closed_at=moment)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def list_recent(
        self, business_id: str, *, limit: int = 50
    ) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.business_id == business_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class MessageRepository(BaseRepository[Message]):
    async def add_message(
        self,
        *,
        business_id: str,
        conversation_id: int,
        contact_id: int,
        direction: Direction,
        message_type: MessageType,
        meta_message_id: str | None = None,
        content_encrypted: str | None = None,
        content_preview: str | None = None,
        media_reference: dict[str, Any] | None = None,
        intent: str | None = None,
        confidence: float | None = None,
        delivery_status: DeliveryStatus | None = None,
        template_name: str | None = None,
        template_language: str | None = None,
        campaign_id: int | None = None,
        correlation_id: str | None = None,
    ) -> Message:
        message = Message(
            business_id=business_id,
            conversation_id=conversation_id,
            contact_id=contact_id,
            direction=direction,
            message_type=message_type,
            meta_message_id=meta_message_id,
            content_encrypted=content_encrypted,
            content_preview=content_preview,
            media_reference=media_reference,
            intent=intent,
            confidence=confidence,
            delivery_status=delivery_status,
            template_name=template_name,
            template_language=template_language,
            campaign_id=campaign_id,
            correlation_id=correlation_id,
            sent_at=utcnow() if direction is Direction.OUTBOUND else None,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def get_by_meta_id(self, business_id: str, meta_message_id: str) -> Message | None:
        stmt = select(Message).where(
            Message.business_id == business_id,
            Message.meta_message_id == meta_message_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_delivery_status(
        self,
        *,
        business_id: str,
        meta_message_id: str,
        status: DeliveryStatus,
        error_code: int | None = None,
        error_detail: str | None = None,
        moment: datetime | None = None,
    ) -> Message | None:
        """Apply a status webhook, ignoring out-of-order regressions."""
        message = await self.get_by_meta_id(business_id, meta_message_id)
        if message is None:
            return None

        # Meta can deliver 'sent' after 'delivered'; never move backwards.
        order = {
            DeliveryStatus.QUEUED: 0,
            DeliveryStatus.SENT: 1,
            DeliveryStatus.ACCEPTED: 1,
            DeliveryStatus.DELIVERED: 2,
            DeliveryStatus.READ: 3,
        }
        current_rank = order.get(message.delivery_status or DeliveryStatus.QUEUED, 0)
        new_rank = order.get(status, 0)
        terminal = status in (DeliveryStatus.FAILED, DeliveryStatus.DELETED)

        if terminal or new_rank >= current_rank:
            message.delivery_status = status
            when = moment or utcnow()
            if status is DeliveryStatus.DELIVERED:
                message.delivered_at = when
            elif status is DeliveryStatus.READ:
                message.read_at = when
            elif status is DeliveryStatus.FAILED:
                message.error_code = error_code
                message.error_detail = error_detail
        await self.session.flush()
        return message

    async def recent_history(
        self, conversation_id: int, *, limit: int = 10
    ) -> list[Message]:
        """Return the last ``limit`` messages, oldest first."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return list(reversed(rows))

    async def count_in_conversation(self, conversation_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def delivery_stats(
        self, business_id: str, *, since: datetime
    ) -> dict[str, int]:
        stmt = (
            select(Message.delivery_status, func.count())
            .where(
                Message.business_id == business_id,
                Message.direction == Direction.OUTBOUND,
                Message.created_at >= since,
            )
            .group_by(Message.delivery_status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(status): int(count) for status, count in rows if status}

    async def list_failed(
        self, business_id: str, *, limit: int = 50
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(
                Message.business_id == business_id,
                Message.delivery_status == DeliveryStatus.FAILED,
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete_older_than(self, business_id: str, cutoff: datetime) -> int:
        stmt = delete(Message).where(
            Message.business_id == business_id, Message.created_at < cutoff
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    """Idempotency ledger. Meta retries aggressively; this makes that safe."""

    async def claim(
        self,
        *,
        business_id: str,
        event_key: str,
        event_type: str,
        correlation_id: str | None = None,
    ) -> bool:
        """Atomically claim an event.

        Returns True if this caller should process the event, False if it was
        already claimed. The unique constraint is the source of truth, so two
        concurrent workers cannot both win.
        """
        existing = await self.get(business_id, event_key)
        if existing is not None:
            existing.attempts += 1
            await self.session.flush()
            return False

        event = WebhookEvent(
            business_id=business_id,
            event_key=event_key,
            event_type=event_type,
            processed=False,
            attempts=1,
            correlation_id=correlation_id,
        )
        self.session.add(event)
        try:
            await self.session.flush()
        except IntegrityError:
            # Lost a race with a concurrent worker: they own the event.
            await self.session.rollback()
            return False
        return True

    async def get(self, business_id: str, event_key: str) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(
            WebhookEvent.business_id == business_id,
            WebhookEvent.event_key == event_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def mark_processed(self, business_id: str, event_key: str) -> None:
        event = await self.get(business_id, event_key)
        if event is not None:
            event.processed = True
            event.processed_at = utcnow()
            await self.session.flush()

    async def mark_failed(self, business_id: str, event_key: str, error: str) -> int:
        """Record a failure and return the attempt count."""
        event = await self.get(business_id, event_key)
        if event is None:
            return 0
        event.last_error = error[:1000]
        await self.session.flush()
        return event.attempts

    async def purge_older_than(self, business_id: str, cutoff: datetime) -> int:
        stmt = delete(WebhookEvent).where(
            WebhookEvent.business_id == business_id, WebhookEvent.created_at < cutoff
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)


class DeadLetterRepository(BaseRepository[DeadLetter]):
    async def create(
        self,
        *,
        business_id: str,
        source: str,
        error: str,
        payload: dict[str, Any] | None = None,
        event_key: str | None = None,
        attempts: int = 0,
        correlation_id: str | None = None,
    ) -> DeadLetter:
        entry = DeadLetter(
            business_id=business_id,
            source=source,
            event_key=event_key,
            payload=payload or {},
            error=error[:2000],
            attempts=attempts,
            correlation_id=correlation_id,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_unresolved(
        self, business_id: str, *, limit: int = 50
    ) -> list[DeadLetter]:
        stmt = (
            select(DeadLetter)
            .where(DeadLetter.business_id == business_id, DeadLetter.resolved.is_(False))
            .order_by(DeadLetter.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_unresolved(self, business_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(DeadLetter)
            .where(DeadLetter.business_id == business_id, DeadLetter.resolved.is_(False))
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def resolve(self, business_id: str, entry_id: int) -> bool:
        stmt = select(DeadLetter).where(
            DeadLetter.business_id == business_id, DeadLetter.id == entry_id
        )
        entry = (await self.session.execute(stmt)).scalar_one_or_none()
        if entry is None:
            return False
        entry.resolved = True
        await self.session.flush()
        return True
