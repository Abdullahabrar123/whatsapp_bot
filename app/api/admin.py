"""Protected Operator API.

Provides operational inspection and control over bot state, escalations,
campaigns, suppressions, delivery statistics, and knowledge re-indexing.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.core.db import session_scope
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.domain.phone import normalise_phone
from app.repositories.campaigns import CampaignRepository
from app.repositories.consent import BotStateRepository, ConsentRepository, EscalationRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import (
    ConversationRepository,
    MessageRepository,
)
from app.services.escalation_service import EscalationService
from app.services.knowledge_service import KnowledgeService

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Operator API"])


def _verify_admin_auth(
    x_admin_api_key: str | None = Header(None, alias="X-Admin-API-Key"),
    authorization: str | None = Header(None),
) -> None:
    """Validate operator authentication."""
    settings = get_settings()
    configured_key = settings.admin_api_key.get_secret_value()

    # In development/test with no key configured, allow access.
    if settings.app_env in ("development", "test") and not configured_key:
        return

    token = x_admin_api_key
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if not token or not configured_key or not hmac.compare_digest(token, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing operator API key.",
        )


# ==================================================================== Schemas
class PauseBotRequest(BaseModel):
    reason: str | None = "operator_manual_pause"
    actor: str = "operator"


class ContactActionRequest(BaseModel):
    reason: str | None = None
    actor: str = "operator"


class EscalationActionRequest(BaseModel):
    note: str | None = None
    actor: str = "operator"


class SuppressionRequest(BaseModel):
    phone: str
    reason: str = "operator_manual"
    detail: str | None = None


# ================================================================== Endpoints
@router.get("/status")
async def get_system_status(request: Request) -> dict[str, Any]:
    """Get system and business configuration status."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    config = getattr(request.app.state, "config", None)
    session_factory = getattr(request.app.state, "session_factory", None)
    settings = get_settings()

    is_paused = False
    open_escalations = 0
    total_contacts = 0
    total_suppressed = 0

    if session_factory is not None and config is not None:
        async with session_scope(session_factory) as session:
            bot_state = BotStateRepository(session)
            esc_repo = EscalationRepository(session)
            contact_repo = ContactRepository(session)
            supp_repo = SuppressionRepository(session)

            is_paused = await bot_state.is_paused(config.business.business_id)
            open_escalations = await esc_repo.count_open(config.business.business_id)
            total_contacts = await contact_repo.count(config.business.business_id)
            total_suppressed = await supp_repo.count(config.business.business_id)

    return {
        "business": {
            "id": config.business.business_id if config else "unknown",
            "name": config.business.name if config else "unknown",
            "industry": config.business.industry if config else "unknown",
            "default_language": config.business.default_language if config else "en",
        },
        "bot": {
            "globally_paused": is_paused,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "app_env": settings.app_env,
        },
        "metrics_summary": {
            "open_escalations": open_escalations,
            "total_contacts": total_contacts,
            "total_suppressed": total_suppressed,
        },
    }


@router.post("/bot/pause")
async def pause_bot_globally(request: Request, body: PauseBotRequest) -> dict[str, Any]:
    """Pause automated replies globally."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        repo = BotStateRepository(session)
        state = await repo.set_paused(
            business_id, paused=True, reason=body.reason, actor=body.actor
        )
        return {"business_id": business_id, "paused": state.paused, "reason": state.pause_reason}


@router.post("/bot/resume")
async def resume_bot_globally(request: Request, body: PauseBotRequest) -> dict[str, Any]:
    """Resume automated replies globally."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        repo = BotStateRepository(session)
        state = await repo.set_paused(business_id, paused=False, actor=body.actor)
        return {"business_id": business_id, "paused": state.paused}


@router.post("/contacts/{phone}/pause")
async def pause_contact(request: Request, phone: str, body: ContactActionRequest) -> dict[str, Any]:
    """Pause bot for a single contact."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    settings = get_settings()
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    norm = normalise_phone(phone, key=settings.app_secret_key.get_secret_value())
    async with session_scope(session_factory) as session:
        repo = ContactRepository(session)
        contact = await repo.get_by_hash(business_id, norm.hash)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        await repo.set_bot_paused(contact, paused=True, reason=body.reason or "operator_paused")
        return {"contact_id": contact.id, "phone_masked": contact.phone_masked, "bot_paused": True}


@router.post("/contacts/{phone}/resume")
async def resume_contact(request: Request, phone: str) -> dict[str, Any]:
    """Resume bot for a single contact."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    settings = get_settings()
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    norm = normalise_phone(phone, key=settings.app_secret_key.get_secret_value())
    async with session_scope(session_factory) as session:
        repo = ContactRepository(session)
        contact = await repo.get_by_hash(business_id, norm.hash)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        await repo.set_bot_paused(contact, paused=False)
        return {"contact_id": contact.id, "phone_masked": contact.phone_masked, "bot_paused": False}


@router.get("/escalations")
async def list_escalations(
    request: Request, limit: int = Query(50, ge=1, le=200)
) -> list[dict[str, Any]]:
    """List open escalation requests."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        repo = EscalationRepository(session)
        rows = await repo.list_open(business_id, limit=limit)
        return [
            {
                "id": esc.id,
                "conversation_id": esc.conversation_id,
                "contact_id": esc.contact_id,
                "reason": esc.reason,
                "status": esc.status.value,
                "summary": esc.summary,
                "detected_intent": esc.detected_intent,
                "confidence": esc.confidence,
                "assigned_to": esc.assigned_to,
                "created_at": esc.created_at.isoformat(),
            }
            for esc in rows
        ]


@router.post("/escalations/{escalation_id}/acknowledge")
async def acknowledge_escalation(
    request: Request, escalation_id: int, body: EscalationActionRequest
) -> dict[str, Any]:
    """Acknowledge an escalation."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        esc_repo = EscalationRepository(session)
        conv_repo = ConversationRepository(session)
        msg_repo = MessageRepository(session)
        contact_repo = ContactRepository(session)

        svc = EscalationService(
            business=config.business,
            escalations=esc_repo,
            conversations=conv_repo,
            messages=msg_repo,
            contacts=contact_repo,
        )
        esc = await svc.acknowledge(
            business_id=business_id, escalation_id=escalation_id, actor=body.actor
        )
        if esc is None:
            raise HTTPException(status_code=404, detail="Escalation not found")
        return {"id": esc.id, "status": esc.status.value, "assigned_to": esc.assigned_to}


@router.post("/escalations/{escalation_id}/resolve")
async def resolve_escalation(
    request: Request, escalation_id: int, body: EscalationActionRequest
) -> dict[str, Any]:
    """Resolve an escalation and return conversation to bot."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        esc_repo = EscalationRepository(session)
        conv_repo = ConversationRepository(session)
        msg_repo = MessageRepository(session)
        contact_repo = ContactRepository(session)

        svc = EscalationService(
            business=config.business,
            escalations=esc_repo,
            conversations=conv_repo,
            messages=msg_repo,
            contacts=contact_repo,
        )
        esc = await svc.resolve(
            business_id=business_id,
            escalation_id=escalation_id,
            note=body.note,
            actor=body.actor,
            resume_bot=True,
        )
        if esc is None:
            raise HTTPException(status_code=404, detail="Escalation not found")
        return {"id": esc.id, "status": esc.status.value, "resolved": True}


@router.get("/stats/delivery")
async def get_delivery_stats(
    request: Request, hours: int = Query(24, ge=1, le=720)
) -> dict[str, int]:
    """Get delivery statistics for outbound messages in the last N hours."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with session_scope(session_factory) as session:
        repo = MessageRepository(session)
        return await repo.delivery_stats(business_id, since=since)


@router.get("/suppressions")
async def list_suppressions(
    request: Request, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    """List permanent do-not-message records."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        repo = SuppressionRepository(session)
        rows = await repo.list_all(business_id, limit=limit)
        return [
            {
                "id": r.id,
                "phone_masked": r.phone_masked,
                "reason": r.reason,
                "detail": r.detail,
                "suppressed_at": r.suppressed_at.isoformat(),
            }
            for r in rows
        ]


@router.post("/suppressions")
async def add_suppression(request: Request, body: SuppressionRequest) -> dict[str, Any]:
    """Manually suppress a phone number."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    settings = get_settings()
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    norm = normalise_phone(body.phone, key=settings.app_secret_key.get_secret_value())
    async with session_scope(session_factory) as session:
        repo = SuppressionRepository(session)
        entry = await repo.create(
            business_id=business_id,
            phone_hash=norm.hash,
            phone_masked=norm.masked,
            reason=body.reason,
            detail=body.detail,
        )
        return {
            "id": entry.id,
            "phone_masked": entry.phone_masked,
            "reason": entry.reason,
            "suppressed": True,
        }


@router.get("/consent/{phone}")
async def get_consent_history(request: Request, phone: str) -> list[dict[str, Any]]:
    """Get consent audit history for a contact."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    settings = get_settings()
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    norm = normalise_phone(phone, key=settings.app_secret_key.get_secret_value())
    async with session_scope(session_factory) as session:
        contact_repo = ContactRepository(session)
        consent_repo = ConsentRepository(session)

        contact = await contact_repo.get_by_hash(business_id, norm.hash)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")

        records = await consent_repo.history(contact.id)
        return [
            {
                "id": rec.id,
                "status": rec.status.value,
                "purpose": rec.purpose.value,
                "source": rec.source,
                "occurred_at": rec.occurred_at.isoformat(),
                "evidence": rec.evidence,
            }
            for rec in records
        ]


@router.get("/campaigns")
async def list_campaigns(
    request: Request, limit: int = Query(50, ge=1, le=200)
) -> list[dict[str, Any]]:
    """List recent campaigns."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    session_factory = request.app.state.session_factory
    business_id = request.app.state.business_id

    async with session_scope(session_factory) as session:
        repo = CampaignRepository(session)
        campaigns = await repo.list_all(business_id, limit=limit)
        return [
            {
                "id": c.id,
                "name": c.name,
                "template_name": c.template_name,
                "template_language": c.template_language,
                "status": c.status.value,
                "total": c.total,
                "eligible": c.eligible,
                "rejected": c.rejected,
                "sent": c.sent,
                "delivered": c.delivered,
                "read": c.read,
                "failed": c.failed,
                "created_at": c.created_at.isoformat(),
            }
            for c in campaigns
        ]


@router.post("/knowledge/reindex")
async def reindex_knowledge(request: Request) -> dict[str, Any]:
    """Rebuild knowledge base lexical/embedding index."""
    _verify_admin_auth(
        request.headers.get("X-Admin-API-Key"), request.headers.get("Authorization")
    )
    knowledge_svc: KnowledgeService | None = getattr(
        request.app.state, "knowledge_service", None
    )
    if knowledge_svc is None:
        raise HTTPException(status_code=500, detail="Knowledge service not initialized")

    chunk_count = knowledge_svc.build_index()
    return {"status": "reindexed", "indexed_chunks": chunk_count}
