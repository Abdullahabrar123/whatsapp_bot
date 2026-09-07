"""Contract tests verifying parsing against official Meta WhatsApp Cloud API fixtures."""

from app.domain.enums import DeliveryStatus, MessageType
from app.integrations.webhook_parser import parse_webhook
from tests.fixtures.meta_payloads import (
    button_reply,
    failed_status,
    location_message,
    status_update,
    text_message,
)


def test_contract_inbound_text_payload():
    parsed = parse_webhook(text_message())
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]
    assert msg.message_type == MessageType.TEXT
    assert msg.text is not None
    assert msg.wa_id == "447400123456"


def test_contract_inbound_button_payload():
    parsed = parse_webhook(button_reply())
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]
    assert msg.message_type in (
        MessageType.BUTTON,
        MessageType.INTERACTIVE_BUTTON_REPLY,
    )
    assert msg.interactive_title is not None or msg.text is not None


def test_contract_inbound_location_payload():
    parsed = parse_webhook(location_message())
    assert len(parsed.messages) == 1
    msg = parsed.messages[0]
    assert msg.message_type == MessageType.LOCATION
    assert msg.location is not None
    assert "latitude" in msg.location


def test_contract_status_delivered_payload():
    parsed = parse_webhook(status_update("delivered"))
    assert len(parsed.statuses) == 1
    st = parsed.statuses[0]
    assert st.status == DeliveryStatus.DELIVERED


def test_contract_status_read_payload():
    parsed = parse_webhook(status_update("read"))
    assert len(parsed.statuses) == 1
    st = parsed.statuses[0]
    assert st.status == DeliveryStatus.READ


def test_contract_status_failed_payload():
    parsed = parse_webhook(failed_status())
    assert len(parsed.statuses) == 1
    st = parsed.statuses[0]
    assert st.status == DeliveryStatus.FAILED
    assert st.error_code is not None
