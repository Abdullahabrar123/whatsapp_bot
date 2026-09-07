"""Unit tests for Meta WhatsApp Cloud API client."""

import httpx
import pytest
import respx

from app.core.exceptions import WhatsAppAPIError
from app.core.settings import Settings
from app.domain.enums import DeliveryStatus
from app.integrations.whatsapp_client import WhatsAppClient


@pytest.mark.asyncio
@respx.mock
async def test_whatsapp_send_text_success():
    settings = Settings(
        app_env="test",
        meta_phone_number_id="123456",
        meta_access_token="test_token",
        meta_app_secret="test_secret",
    )

    mock_resp = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "447700900001", "wa_id": "447700900001"}],
        "messages": [{"id": "wamid.HBgLM...="}],
    }

    respx.post("https://graph.facebook.com/v26.0/123456/messages").respond(
        status_code=200, json=mock_resp
    )

    async with httpx.AsyncClient() as http_client:
        client = WhatsAppClient(settings, http_client=http_client)
        result = await client.send_text("447700900001", "Hello world!")
        assert result.meta_message_id == "wamid.HBgLM...="
        assert result.status == DeliveryStatus.SENT


@pytest.mark.asyncio
@respx.mock
async def test_whatsapp_permanent_error_no_retry():
    settings = Settings(
        app_env="test",
        meta_phone_number_id="123456",
        meta_access_token="test_token",
        meta_app_secret="test_secret",
        meta_max_retries=3,
    )

    error_resp = {
        "error": {
            "message": "(#132001) Template name does not exist",
            "type": "OAuthException",
            "code": 132001,
            "fbtrace_id": "AbCdEf123",
        }
    }

    route = respx.post("https://graph.facebook.com/v26.0/123456/messages").respond(
        status_code=400, json=error_resp
    )

    async with httpx.AsyncClient() as http_client:
        client = WhatsAppClient(settings, http_client=http_client)
        with pytest.raises(WhatsAppAPIError) as exc_info:
            await client.send_template("447700900001", "non_existent_tmpl", "en")

        assert exc_info.value.meta_code == 132001
        assert exc_info.value.retryable is False
        assert route.call_count == 1  # No retries on permanent error


@pytest.mark.asyncio
@respx.mock
async def test_whatsapp_retries_then_raises_last_error():
    """When every attempt fails, the final Meta error is raised, not swallowed."""
    settings = Settings(
        app_env="test",
        meta_phone_number_id="123456",
        meta_access_token="test_token",
        meta_app_secret="test_secret",
        meta_max_retries=3,
    )

    error_resp = {
        "error": {
            "message": "(#131056) Too many messages sent",
            "type": "OAuthException",
            "code": 131056,
        }
    }

    route = respx.post("https://graph.facebook.com/v26.0/123456/messages").respond(
        status_code=429, json=error_resp
    )

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    async with httpx.AsyncClient() as http_client:
        client = WhatsAppClient(settings, http_client=http_client, sleep=fake_sleep)
        with pytest.raises(WhatsAppAPIError) as exc_info:
            await client.send_text("447700900001", "hello")

    assert exc_info.value.meta_code == 131056
    assert exc_info.value.retryable is True
    assert route.call_count == 4  # initial attempt + meta_max_retries
    assert len(slept) == 3  # backoff between attempts, never after the last one


def test_circuit_breaker_reopens_and_half_opens():
    """The breaker trips at the threshold and half-opens once the window elapses."""
    from app.integrations.whatsapp_client import CircuitBreaker

    breaker = CircuitBreaker(fail_threshold=2, reset_seconds=30.0)
    assert breaker.is_open is False

    breaker.record_failure()
    assert breaker.is_open is False  # below threshold

    breaker.record_failure()
    assert breaker.is_open is True

    # Simulate the reset window elapsing: the breaker half-opens for a trial call.
    breaker._opened_at -= 31.0
    assert breaker.is_open is False

    breaker.record_success()
    assert breaker.is_open is False
