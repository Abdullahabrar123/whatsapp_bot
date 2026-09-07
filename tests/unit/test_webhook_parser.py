"""Unit tests for defensive parsing of Meta Cloud API webhook payloads."""

from app.domain.enums import DeliveryStatus, MessageType
from app.integrations.webhook_parser import parse_webhook


def test_parse_inbound_text_message():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "PHONE_123"},
                            "contacts": [
                                {"profile": {"name": "Alice"}, "wa_id": "447700900001"}
                            ],
                            "messages": [
                                {
                                    "from": "447700900001",
                                    "id": "wamid.inbound.1",
                                    "timestamp": "1718452800",
                                    "text": {"body": "Hi, what are your hours?"},
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    parsed = parse_webhook(payload)
    assert len(parsed.messages) == 1
    assert parsed.messages[0].text == "Hi, what are your hours?"
    assert parsed.messages[0].message_type == MessageType.TEXT
    assert parsed.messages[0].profile_name == "Alice"
    assert parsed.messages[0].wa_id == "447700900001"


def test_parse_delivery_status():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "PHONE_123"},
                            "statuses": [
                                {
                                    "id": "wamid.outbound.1",
                                    "status": "delivered",
                                    "timestamp": "1718452810",
                                    "recipient_id": "447700900001",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    parsed = parse_webhook(payload)
    assert len(parsed.statuses) == 1
    assert parsed.statuses[0].status == DeliveryStatus.DELIVERED
    assert parsed.statuses[0].meta_message_id == "wamid.outbound.1"
