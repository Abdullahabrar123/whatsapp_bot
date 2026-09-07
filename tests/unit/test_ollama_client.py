"""Unit tests for Ollama client request construction and response parsing."""

import json

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.domain.enums import Intent
from app.integrations.llm_client import OllamaClient, extract_json_object, strip_thinking


def test_strip_thinking():
    text = (
        "<think>Let me reason about the user's greeting...</think>"
        '```json\n{"intent": "greeting", "confidence": 0.9}\n```'
    )
    cleaned = strip_thinking(text)
    assert "<think>" not in cleaned
    assert "greeting" in cleaned


def test_extract_json_object():
    text = (
        "Here is your response:\n```json\n"
        '{"intent": "greeting", "confidence": 0.95, "proposed_response": "Hi!"}\n```'
    )
    data = extract_json_object(text)
    assert data is not None
    assert data["intent"] == "greeting"
    assert data["confidence"] == 0.95


@pytest.mark.asyncio
@respx.mock
async def test_ollama_client_success():
    settings = Settings(
        app_env="test",
        ollama_base_url="http://localhost:11434",
        llm_model="qwen3:4b",
    )

    mock_response = {
        "message": {
            "content": json.dumps(
                {
                    "intent": "pricing_inquiry",
                    "confidence": 0.88,
                    "proposed_response": "A check-up costs £65.",
                    "requires_human": False,
                    "safety_flags": [],
                    "sources": ["svc:checkup"],
                }
            )
        }
    }

    respx.post("http://localhost:11434/api/chat").respond(status_code=200, json=mock_response)

    async with httpx.AsyncClient(base_url="http://localhost:11434") as http_client:
        client = OllamaClient(settings, http_client=http_client)
        result = await client.analyse(system_prompt="sys", user_prompt="usr")
        assert result.intent == Intent.PRICING_INQUIRY
        assert result.confidence == 0.88
        assert "£65" in result.proposed_response
