"""Sanitised Meta Cloud API webhook fixtures used by contract tests.

Every identifier here is fabricated. Phone numbers come from ranges officially
reserved for documentation and fiction:

* ``+44 7400 123456``  - UK mobile format, unassigned
* ``+1 302 555 0123``  - US 555-01xx fiction range
* ``+61 491 570 156``  - Australian official fictitious number

No value in this file corresponds to a real person, account or token.
"""

from __future__ import annotations

from typing import Any

WABA_ID = "102290129340398"
PHONE_NUMBER_ID = "106540352242922"
DISPLAY_PHONE_NUMBER = "15550001234"

CUSTOMER_WA_ID = "447400123456"
CUSTOMER_NAME = "Test Customer"

WAMID_INBOUND = "wamid.HBgLNDQ3NDAwMTIzNDU2FQIAEhggQTVGN0JDMEE5RDlBQzk1MTRE"
WAMID_OUTBOUND = "wamid.HBgLNDQ3NDAwMTIzNDU2FQIAERgSOEY4RTNBQjRDNzZBQkExRDcA"


def _envelope(value: dict[str, Any], *, field: str = "messages") -> dict[str, Any]:
    """Wrap a change value in the standard webhook envelope."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": WABA_ID, "changes": [{"value": value, "field": field}]}],
    }


def _message_value(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": DISPLAY_PHONE_NUMBER,
            "phone_number_id": PHONE_NUMBER_ID,
        },
        "contacts": [
            {"profile": {"name": CUSTOMER_NAME}, "wa_id": CUSTOMER_WA_ID},
        ],
        "messages": [message],
    }


def _status_value(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": DISPLAY_PHONE_NUMBER,
            "phone_number_id": PHONE_NUMBER_ID,
        },
        "statuses": [status],
    }


# ================================================================ inbound text
def text_message(body: str = "Hello, what are your opening hours?") -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND,
                "timestamp": "1780000000",
                "type": "text",
                "text": {"body": body},
            }
        )
    )


def text_message_with_context(body: str = "Yes please") -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "CTX",
                "timestamp": "1780000100",
                "type": "text",
                "context": {"from": DISPLAY_PHONE_NUMBER, "id": WAMID_OUTBOUND},
                "text": {"body": body},
            }
        )
    )


# ========================================================== inbound interactive
def button_reply(
    reply_id: str = "book_appointment", title: str = "Book an appointment"
) -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "BTN",
                "timestamp": "1780000200",
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": reply_id, "title": title},
                },
            }
        )
    )


def list_reply(
    reply_id: str = "service_hygiene",
    title: str = "Hygienist Appointment",
    description: str = "40 minute professional clean",
) -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "LST",
                "timestamp": "1780000300",
                "type": "interactive",
                "interactive": {
                    "type": "list_reply",
                    "list_reply": {
                        "id": reply_id,
                        "title": title,
                        "description": description,
                    },
                },
            }
        )
    )


def template_quick_reply(payload: str = "STOP", text: str = "Stop promotions") -> dict[str, Any]:
    """Quick-reply button attached to a template we previously sent."""
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "QR",
                "timestamp": "1780000350",
                "type": "button",
                "button": {"payload": payload, "text": text},
                "context": {"from": DISPLAY_PHONE_NUMBER, "id": WAMID_OUTBOUND},
            }
        )
    )


# ============================================================= inbound location
def location_message() -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "LOC",
                "timestamp": "1780000400",
                "type": "location",
                "location": {
                    "latitude": 53.4808,
                    "longitude": -2.2426,
                    "name": "Manchester Piccadilly",
                    "address": "Manchester, UK",
                },
            }
        )
    )


# ================================================================ inbound media
def image_message(caption: str | None = "Is this tooth chipped?") -> dict[str, Any]:
    image: dict[str, Any] = {
        "id": "1554723461234567",
        "mime_type": "image/jpeg",
        "sha256": "c4f3b2a19e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b",
    }
    if caption is not None:
        image["caption"] = caption
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "IMG",
                "timestamp": "1780000500",
                "type": "image",
                "image": image,
            }
        )
    )


def document_message() -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "DOC",
                "timestamp": "1780000600",
                "type": "document",
                "document": {
                    "id": "1554723461234568",
                    "mime_type": "application/pdf",
                    "sha256": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
                    "filename": "referral-letter.pdf",
                    "caption": "My referral letter",
                },
            }
        )
    )


# ========================================================== inbound unsupported
def unsupported_message() -> dict[str, Any]:
    """Meta reports message types the API cannot deliver with an errors array."""
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "UNS",
                "timestamp": "1780000700",
                "type": "unsupported",
                "errors": [
                    {
                        "code": 131051,
                        "title": "Message type is not currently supported.",
                        "message": "Message type is not currently supported.",
                        "error_data": {"details": "Message type is not currently supported."},
                    }
                ],
            }
        )
    )


def reaction_message(emoji: str = "\U0001f44d") -> dict[str, Any]:
    return _envelope(
        _message_value(
            {
                "from": CUSTOMER_WA_ID,
                "id": WAMID_INBOUND + "RCT",
                "timestamp": "1780000800",
                "type": "reaction",
                "reaction": {"message_id": WAMID_OUTBOUND, "emoji": emoji},
            }
        )
    )


# ================================================================== statuses
def status_update(
    status: str = "delivered", *, message_id: str = WAMID_OUTBOUND
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": message_id,
        "status": status,
        "timestamp": "1780000900",
        "recipient_id": CUSTOMER_WA_ID,
    }
    if status in ("delivered", "read", "sent"):
        payload["conversation"] = {
            "id": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6",
            "origin": {"type": "utility"},
        }
        payload["pricing"] = {
            "billable": True,
            "pricing_model": "PMP",
            "category": "utility",
        }
    return _envelope(_status_value(payload))


def failed_status(code: int = 131047, title: str = "Re-engagement message") -> dict[str, Any]:
    return _envelope(
        _status_value(
            {
                "id": WAMID_OUTBOUND,
                "status": "failed",
                "timestamp": "1780001000",
                "recipient_id": CUSTOMER_WA_ID,
                "errors": [
                    {
                        "code": code,
                        "title": title,
                        "message": title,
                        "error_data": {
                            "details": (
                                "Message failed to send because more than 24 hours have "
                                "passed since the customer last replied to this number."
                            )
                        },
                        "href": "https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/",
                    }
                ],
            }
        )
    )


# ============================================================== account errors
def system_error_event(code: int = 131064) -> dict[str, Any]:
    """Account-level error, e.g. messaging limit reached."""
    return _envelope(
        {
            "messaging_product": "whatsapp",
            "metadata": {
                "display_phone_number": DISPLAY_PHONE_NUMBER,
                "phone_number_id": PHONE_NUMBER_ID,
            },
            "errors": [
                {
                    "code": code,
                    "title": "Account has exceeded its messaging limit.",
                    "message": "Account has exceeded its messaging limit.",
                }
            ],
        }
    )


def template_status_update() -> dict[str, Any]:
    """A change field we deliberately do not process as a message."""
    return _envelope(
        {
            "event": "APPROVED",
            "message_template_id": 1234567890,
            "message_template_name": "appointment_reminder_v1",
            "message_template_language": "en",
        },
        field="message_template_status_update",
    )


# ================================================================== malformed
MALFORMED_PAYLOADS: dict[str, Any] = {
    "not_an_object": ["nope"],
    "wrong_object": {"object": "page", "entry": []},
    "no_entry": {"object": "whatsapp_business_account"},
    "entry_not_list": {"object": "whatsapp_business_account", "entry": {}},
    "changes_missing": {"object": "whatsapp_business_account", "entry": [{"id": WABA_ID}]},
    "value_not_dict": {
        "object": "whatsapp_business_account",
        "entry": [{"id": WABA_ID, "changes": [{"value": "x", "field": "messages"}]}],
    },
    "message_not_object": {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [{"value": {"messages": ["oops"]}, "field": "messages"}],
            }
        ],
    },
    "status_unknown_kind": {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "value": {"statuses": [{"id": "wamid.X", "status": "teleported"}]},
                        "field": "messages",
                    }
                ],
            }
        ],
    },
    "empty_object": {},
}


def batch_of_two_messages() -> dict[str, Any]:
    """Meta may batch several messages into one POST."""
    return _envelope(
        {
            "messaging_product": "whatsapp",
            "metadata": {
                "display_phone_number": DISPLAY_PHONE_NUMBER,
                "phone_number_id": PHONE_NUMBER_ID,
            },
            "contacts": [{"profile": {"name": CUSTOMER_NAME}, "wa_id": CUSTOMER_WA_ID}],
            "messages": [
                {
                    "from": CUSTOMER_WA_ID,
                    "id": WAMID_INBOUND + "A",
                    "timestamp": "1780001100",
                    "type": "text",
                    "text": {"body": "First message"},
                },
                {
                    "from": CUSTOMER_WA_ID,
                    "id": WAMID_INBOUND + "B",
                    "timestamp": "1780001101",
                    "type": "text",
                    "text": {"body": "Second message"},
                },
            ],
        }
    )
