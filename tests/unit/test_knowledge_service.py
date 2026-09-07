"""Unit tests for knowledge indexing, retrieval, and response composition."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config_loader import AppConfig
from app.domain.enums import Intent
from app.domain.schemas import IntentResult, RetrievalResult
from app.integrations.llm_client import DeterministicClient
from app.services.knowledge_service import KnowledgeService
from app.services.response_service import ResponseService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_knowledge_indexing_and_search(test_config: AppConfig):
    ks = KnowledgeService(
        test_config.business,
        project_root=PROJECT_ROOT,
        llm_client=DeterministicClient(),
        embeddings_enabled=False,
    )
    index = ks.build_index()
    assert len(index.chunks) > 0

    results = await ks.search("How much is a check-up?", top_k=3)
    assert len(results.hits) > 0
    assert any(
        "checkup" in hit.chunk.chunk_id.lower() or "check-up" in hit.chunk.text.lower()
        for hit in results.hits
    )


def test_response_composition_greeting(test_config: AppConfig):
    rs = ResponseService(test_config.business, test_config.bot)
    intent_res = IntentResult(intent=Intent.GREETING, confidence=0.9)
    retrieval = RetrievalResult(query="Hi", hits=[])
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

    reply = rs.compose(intent_res, retrieval=retrieval, now=now)
    assert "BrightSmile" in reply.text
    assert reply.intent == Intent.GREETING
    assert reply.escalate is False
