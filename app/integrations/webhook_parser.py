"""Parsing of Meta WhatsApp Cloud API webhook payloads.

Meta guarantees an envelope shape but not that every optional field is present,
and it retries aggressively. This parser is therefore deliberately defensive:

* An unrecognised or malformed *item* never aborts the whole batch.
* Every parsed item carries a stable ``event_key`` used for idempotency.
* Unsupported message types are surfaced as first-class events so the bot can
  reply politely instead of silently dropping the customer's message.

Reference envelope::

    {"object": "whatsapp_business_account",
     "entry": [{"id": "<WABA_ID>",
                "changes": [{"field": "messages",
                             "value": {"messaging_product": "whatsapp",
                                       "metadata": {...},
                                       "contacts": [...],
                                       "messages": [...] | "statuses": [...]}}]}]}
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import DeliveryStatus, MessageType

MAX_TEXT_CHARS = 4096


# ===================================================================== results
@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A normalised inbound customer message."""

    event_key: str
    meta_message_id: str
    wa_id: str
    phone_number_id: str
    waba_id: str
    message_type: MessageType
    timestamp: datetime
    text: str | None = None
    profile_name: str | None = None
    interactive_id: str | None = None
    interactive_title: str | None = None
    media: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    context_message_id: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    raw_type: str = ""


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """A delivery-status notification for a message we sent."""

    event_key: str
    meta_message_id: str
    recipient_wa_id: str
    phone_number_id: str
    waba_id: str
    status: DeliveryStatus
    timestamp: datetime
    conversation_id: str | None = None
    conversation_origin: str | None = None
    pricing_category: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def error_code(self) -> int | None:
        if not self.errors:
            return None
        code = self.errors[0].get("code")
        return int(code) if isinstance(code, int | str) and str(code).isdigit() else None

    @property
    def error_title(self) -> str | None:
        return self.errors[0].get("title") if self.errors else None


@dataclass(frozen=True, slots=True)
class SystemError:
    """An account-level error reported outside any specific message."""

    event_key: str
    waba_id: str
    code: int | None
    title: str
    detail: str | None = None


@dataclass(slots=True)
class ParsedWebhook:
    """Everything successfully extracted from one webhook POST."""

    messages: list[InboundMessage] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)
    system_errors: list[SystemError] = field(default_factory=list)
    #: Items that could not be parsed. Recorded, never raised, never retried.
    malformed: list[str] = field(default_factory=list)
    #: Change fields we do not handle (e.g. account_update, template_status).
    ignored_fields: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.messages or self.statuses or self.system_errors)

    @property
    def total_events(self) -> int:
        return len(self.messages) + len(self.statuses) + len(self.system_errors)


# ==================================================================== helpers
_TYPE_MAP: dict[str, MessageType] = {
    "text": MessageType.TEXT,
    "image": MessageType.IMAGE,
    "document": MessageType.DOCUMENT,
    "audio": MessageType.AUDIO,
    "video": MessageType.VIDEO,
    "sticker": MessageType.STICKER,
    "location": MessageType.LOCATION,
    "contacts": MessageType.CONTACTS,
    "button": MessageType.BUTTON,
    "reaction": MessageType.REACTION,
    "order": MessageType.ORDER,
    "system": MessageType.SYSTEM,
    "unsupported": MessageType.UNSUPPORTED,
}

_STATUS_MAP: dict[str, DeliveryStatus] = {
    "sent": DeliveryStatus.SENT,
    "accepted": DeliveryStatus.ACCEPTED,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "failed": DeliveryStatus.FAILED,
    "deleted": DeliveryStatus.DELETED,
    "warning": DeliveryStatus.SENT,
}

_MEDIA_TYPES = {"image", "document", "audio", "video", "sticker"}


def _parse_timestamp(value: Any) -> datetime:
    """Meta sends Unix seconds as a string; fall back to now() if absent."""
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _clip(value: Any, limit: int = MAX_TEXT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


def payload_fingerprint(payload: Any) -> str:
    """Stable hash of a payload, used as an idempotency key of last resort."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ===================================================================== parsing
def _parse_message(
    item: dict[str, Any],
    *,
    phone_number_id: str,
    waba_id: str,
    profiles: dict[str, str],
) -> InboundMessage:
    raw_type = str(item.get("type") or "unknown")
    message_id = str(item.get("id") or "")
    wa_id = str(item.get("from") or "")
    timestamp = _parse_timestamp(item.get("timestamp"))
    context = _as_dict(item.get("context"))
    errors = _as_list(item.get("errors"))

    message_type = _TYPE_MAP.get(raw_type, MessageType.UNSUPPORTED)
    text: str | None = None
    interactive_id: str | None = None
    interactive_title: str | None = None
    media: dict[str, Any] | None = None
    location: dict[str, Any] | None = None

    if raw_type == "text":
        text = _clip(_as_dict(item.get("text")).get("body"))

    elif raw_type == "interactive":
        interactive = _as_dict(item.get("interactive"))
        kind = str(interactive.get("type") or "")
        if kind == "button_reply":
            reply = _as_dict(interactive.get("button_reply"))
            message_type = MessageType.INTERACTIVE_BUTTON_REPLY
            interactive_id = _clip(reply.get("id"), 256)
            interactive_title = _clip(reply.get("title"), 256)
            text = interactive_title
        elif kind == "list_reply":
            reply = _as_dict(interactive.get("list_reply"))
            message_type = MessageType.INTERACTIVE_LIST_REPLY
            interactive_id = _clip(reply.get("id"), 256)
            interactive_title = _clip(reply.get("title"), 256)
            description = _clip(reply.get("description"), 1024)
            text = (
                f"{interactive_title}: {description}" if description else interactive_title
            )
        else:
            message_type = MessageType.UNSUPPORTED

    elif raw_type == "button":
        # Quick-reply button attached to a template we sent.
        button = _as_dict(item.get("button"))
        interactive_id = _clip(button.get("payload"), 256)
        interactive_title = _clip(button.get("text"), 256)
        text = interactive_title

    elif raw_type == "location":
        loc = _as_dict(item.get("location"))
        location = {
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "name": _clip(loc.get("name"), 256),
            "address": _clip(loc.get("address"), 512),
        }
        text = location["name"] or "shared a location"

    elif raw_type in _MEDIA_TYPES:
        payload = _as_dict(item.get(raw_type))
        # Only metadata is captured. Media bytes are never fetched here; any
        # download is a separate, explicitly guarded operation.
        media = {
            "media_id": _clip(payload.get("id"), 256),
            "mime_type": _clip(payload.get("mime_type"), 128),
            "sha256": _clip(payload.get("sha256"), 128),
            "filename": _clip(payload.get("filename"), 256),
            "caption": _clip(payload.get("caption"), 1024),
        }
        text = media["caption"]

    elif raw_type == "reaction":
        reaction = _as_dict(item.get("reaction"))
        text = _clip(reaction.get("emoji"), 16)

    elif raw_type == "order":
        order = _as_dict(item.get("order"))
        media = {"catalog_id": _clip(order.get("catalog_id"), 128)}
        text = _clip(order.get("text"), 1024)

    elif raw_type == "system":
        system = _as_dict(item.get("system"))
        text = _clip(system.get("body"), 1024)

    event_key = message_id or f"msg:{payload_fingerprint(item)}"

    return InboundMessage(
        event_key=event_key,
        meta_message_id=message_id,
        wa_id=wa_id,
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        message_type=message_type,
        timestamp=timestamp,
        text=text,
        profile_name=profiles.get(wa_id),
        interactive_id=interactive_id,
        interactive_title=interactive_title,
        media=media,
        location=location,
        context_message_id=_clip(context.get("id"), 256),
        errors=[_as_dict(err) for err in errors],
        raw_type=raw_type,
    )


def _parse_status(
    item: dict[str, Any], *, phone_number_id: str, waba_id: str
) -> StatusUpdate | None:
    message_id = str(item.get("id") or "")
    raw_status = str(item.get("status") or "").lower()
    status = _STATUS_MAP.get(raw_status)
    if status is None or not message_id:
        return None

    conversation = _as_dict(item.get("conversation"))
    pricing = _as_dict(item.get("pricing"))
    origin = _as_dict(conversation.get("origin"))

    return StatusUpdate(
        # Distinct statuses for one message must not deduplicate each other.
        event_key=f"{message_id}:{raw_status}",
        meta_message_id=message_id,
        recipient_wa_id=str(item.get("recipient_id") or ""),
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        status=status,
        timestamp=_parse_timestamp(item.get("timestamp")),
        conversation_id=_clip(conversation.get("id"), 128),
        conversation_origin=_clip(origin.get("type"), 64),
        pricing_category=_clip(pricing.get("category"), 64),
        errors=[_as_dict(err) for err in _as_list(item.get("errors"))],
    )


def parse_webhook(payload: Any) -> ParsedWebhook:
    """Parse a webhook POST body into normalised events.

    Never raises on malformed input: unparseable fragments are collected in
    ``malformed`` so the endpoint can still return 200 and Meta stops retrying
    an event that will never succeed.
    """
    result = ParsedWebhook()

    if not isinstance(payload, dict):
        result.malformed.append("payload is not a JSON object")
        return result

    if payload.get("object") != "whatsapp_business_account":
        result.malformed.append(f"unexpected object field: {payload.get('object')!r}")
        return result

    for entry in _as_list(payload.get("entry")):
        entry_dict = _as_dict(entry)
        waba_id = str(entry_dict.get("id") or "")

        for change in _as_list(entry_dict.get("changes")):
            change_dict = _as_dict(change)
            field_name = str(change_dict.get("field") or "")

            if field_name != "messages":
                # e.g. account_update, message_template_status_update, flows.
                if field_name:
                    result.ignored_fields.append(field_name)
                continue

            value = _as_dict(change_dict.get("value"))
            metadata = _as_dict(value.get("metadata"))
            phone_number_id = str(metadata.get("phone_number_id") or "")

            profiles: dict[str, str] = {}
            for contact in _as_list(value.get("contacts")):
                contact_dict = _as_dict(contact)
                contact_wa_id = str(contact_dict.get("wa_id") or "")
                name = _as_dict(contact_dict.get("profile")).get("name")
                if contact_wa_id and isinstance(name, str):
                    profiles[contact_wa_id] = name[:128]

            for item in _as_list(value.get("messages")):
                item_dict = _as_dict(item)
                if not item_dict:
                    result.malformed.append("message item is not an object")
                    continue
                try:
                    result.messages.append(
                        _parse_message(
                            item_dict,
                            phone_number_id=phone_number_id,
                            waba_id=waba_id,
                            profiles=profiles,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one bad item must not fail the batch
                    result.malformed.append(f"message parse failure: {type(exc).__name__}")

            for item in _as_list(value.get("statuses")):
                item_dict = _as_dict(item)
                if not item_dict:
                    result.malformed.append("status item is not an object")
                    continue
                try:
                    parsed = _parse_status(
                        item_dict, phone_number_id=phone_number_id, waba_id=waba_id
                    )
                except Exception as exc:  # noqa: BLE001
                    result.malformed.append(f"status parse failure: {type(exc).__name__}")
                    continue
                if parsed is None:
                    result.malformed.append("status item missing id or unknown status")
                else:
                    result.statuses.append(parsed)

            for item in _as_list(value.get("errors")):
                error = _as_dict(item)
                code = error.get("code")
                result.system_errors.append(
                    SystemError(
                        event_key=f"err:{payload_fingerprint(error)}",
                        waba_id=waba_id,
                        code=int(str(code)) if str(code).isdigit() else None,
                        title=str(error.get("title") or "unknown error")[:256],
                        detail=_clip(
                            error.get("message")
                            or _as_dict(error.get("error_data")).get("details"),
                            1024,
                        ),
                    )
                )

    return result
