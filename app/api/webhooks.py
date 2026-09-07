"""Meta WhatsApp Cloud API Webhook endpoint.

Handles:
* GET /webhooks/meta -> Webhook verification handshake with verify token.
* POST /webhooks/meta -> HMAC-SHA256 signature verification, idempotency claim,
  parsing, and async task dispatch.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from app.core.db import session_scope
from app.core.logging import get_correlation_id, get_logger
from app.core.security import verify_meta_signature
from app.core.settings import get_settings
from app.integrations.webhook_parser import parse_webhook
from app.repositories.conversations import WebhookEventRepository
from app.workers.arq_runtime import TaskDispatcher

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.get("/meta")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
) -> Response:
    """Meta webhook verification endpoint."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    expected_token = settings.meta_webhook_verify_token.get_secret_value()

    if hub_mode != "subscribe" or not expected_token or not hmac.compare_digest(
        hub_verify_token, expected_token
    ):
        logger.warning(
            "meta_webhook_verification_failed",
            hub_mode=hub_mode,
            has_expected_token=bool(expected_token),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification token mismatch or invalid mode.",
        )

    logger.info("meta_webhook_verified")
    return Response(content=hub_challenge, media_type="text/plain")


@router.post("/meta")
async def handle_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """Inbound webhook event receiver."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    app_secret = settings.meta_app_secret.get_secret_value()

    # Read raw body for constant-time HMAC verification
    raw_body = await request.body()

    # In development/test mode without an app secret configured, allow unverified payloads.
    signature_required = settings.app_env not in ("development", "test") or bool(app_secret)
    if signature_required and (
        not x_hub_signature_256
        or not verify_meta_signature(raw_body, x_hub_signature_256, app_secret)
    ):
        logger.warning("meta_webhook_invalid_signature")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Hub-Signature-256 signature.",
        )

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("meta_webhook_invalid_json", error=str(exc))
        return {"status": "ignored", "reason": "invalid_json"}

    parsed = parse_webhook(body)
    if parsed.is_empty():
        return {"status": "ok", "processed": 0}

    dispatcher: TaskDispatcher | None = getattr(request.app.state, "dispatcher", None)
    session_factory = getattr(request.app.state, "session_factory", None)
    business_id = getattr(request.app.state, "business_id", "default")
    correlation_id = get_correlation_id()

    claimed_count = 0

    # Claim and enqueue messages
    for msg in parsed.messages:
        claimed = True
        if session_factory is not None:
            async with session_scope(session_factory) as session:
                repo = WebhookEventRepository(session)
                claimed = await repo.claim(
                    business_id=business_id,
                    event_key=msg.event_key,
                    event_type="inbound_message",
                    correlation_id=correlation_id,
                )

        if not claimed:
            logger.info("inbound_message_duplicate_ignored", event_key=msg.event_key)
            continue

        claimed_count += 1
        msg_dict = {
            "event_key": msg.event_key,
            "meta_message_id": msg.meta_message_id,
            "wa_id": msg.wa_id,
            "phone_number_id": msg.phone_number_id,
            "waba_id": msg.waba_id,
            "message_type": msg.message_type.value,
            "timestamp": msg.timestamp.isoformat(),
            "text": msg.text,
            "profile_name": msg.profile_name,
            "interactive_id": msg.interactive_id,
            "interactive_title": msg.interactive_title,
            "media": msg.media,
            "location": msg.location,
            "context_message_id": msg.context_message_id,
        }
        if dispatcher is not None:
            await dispatcher.enqueue_inbound_message(msg_dict, correlation_id)

    # Claim and enqueue delivery statuses
    for st in parsed.statuses:
        claimed = True
        if session_factory is not None:
            async with session_scope(session_factory) as session:
                repo = WebhookEventRepository(session)
                claimed = await repo.claim(
                    business_id=business_id,
                    event_key=st.event_key,
                    event_type="status_update",
                    correlation_id=correlation_id,
                )

        if not claimed:
            logger.info("status_update_duplicate_ignored", event_key=st.event_key)
            continue

        claimed_count += 1
        status_dict = {
            "event_key": st.event_key,
            "meta_message_id": st.meta_message_id,
            "recipient_wa_id": st.recipient_wa_id,
            "phone_number_id": st.phone_number_id,
            "waba_id": st.waba_id,
            "status": st.status.value,
            "timestamp": st.timestamp.isoformat(),
            "conversation_id": st.conversation_id,
            "conversation_origin": st.conversation_origin,
            "pricing_category": st.pricing_category,
            "errors": st.errors,
        }
        if dispatcher is not None:
            await dispatcher.enqueue_status_update(status_dict, correlation_id)

    return {
        "status": "ok",
        "total_events": parsed.total_events,
        "claimed_events": claimed_count,
        "malformed_count": len(parsed.malformed),
    }
