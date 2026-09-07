"""Integration tests for Meta Webhooks API endpoints."""

import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient

from app.core.settings import Settings


@pytest.mark.asyncio
async def test_webhook_verification_success(api_client: AsyncClient, test_settings: Settings):
    token = test_settings.meta_webhook_verify_token.get_secret_value()
    response = await api_client.get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": token,
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 200
    assert response.text == "1158201444"


@pytest.mark.asyncio
async def test_webhook_verification_failure_invalid_token(api_client: AsyncClient):
    response = await api_client.get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_post_message_end_to_end(
    api_client: AsyncClient, test_settings: Settings
):
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
                                    "id": "wamid.inbound.test.1",
                                    "timestamp": "1718452800",
                                    "text": {"body": "Hello there"},
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    secret = test_settings.meta_app_secret.get_secret_value().encode("utf-8")
    sig = "sha256=" + hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()

    response = await api_client.post(
        "/webhooks/meta",
        content=body_bytes,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["claimed_events"] == 1

    # Send duplicate
    dup_response = await api_client.post(
        "/webhooks/meta",
        content=body_bytes,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert dup_response.status_code == 200
    dup_data = dup_response.json()
    assert dup_data["claimed_events"] == 0  # Deduplicated!
