"""Unit tests for background worker tasks."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config_loader import AppConfig
from app.core.security import FieldCipher
from app.core.settings import Settings
from app.integrations.llm_client import DeterministicClient
from app.services.knowledge_service import KnowledgeService
from app.workers.tasks import (
    cleanup_expired_conversations_task,
    process_inbound_message_task,
    process_status_update_task,
    purge_retention_data_task,
)
from tests.conftest import FakeWhatsAppClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_process_inbound_message_task(
    test_settings: Settings,
    test_config: AppConfig,
    session_factory,
    cipher: FieldCipher,
    fake_whatsapp: FakeWhatsAppClient,
):
    ks = KnowledgeService(
        test_config.business,
        project_root=PROJECT_ROOT,
        llm_client=DeterministicClient(),
        embeddings_enabled=False,
    )
    ks.build_index()

    mock_container = {
        "settings": test_settings,
        "config": test_config,
        "cipher": cipher,
        "session_factory": session_factory,
        "whatsapp_client": fake_whatsapp,
        "llm_client": DeterministicClient(),
        "knowledge_service": ks,
    }

    event_dict = {
        "event_key": "task_msg_01",
        "meta_message_id": "wamid.task.01",
        "wa_id": "447400123456",
        "phone_number_id": "123456",
        "waba_id": "987654",
        "message_type": "text",
        "timestamp": datetime.now(UTC).isoformat(),
        "text": "Hello there",
        "profile_name": "Alice",
    }

    res = await process_inbound_message_task({"container": mock_container}, event_dict)
    assert res["handled"] is True
    assert res["replied"] is True


@pytest.mark.asyncio
async def test_process_status_update_task(
    test_settings: Settings,
    test_config: AppConfig,
    session_factory,
    cipher: FieldCipher,
    fake_whatsapp: FakeWhatsAppClient,
):
    ks = KnowledgeService(
        test_config.business,
        project_root=PROJECT_ROOT,
        llm_client=DeterministicClient(),
        embeddings_enabled=False,
    )
    mock_container = {
        "settings": test_settings,
        "config": test_config,
        "cipher": cipher,
        "session_factory": session_factory,
        "whatsapp_client": fake_whatsapp,
        "llm_client": DeterministicClient(),
        "knowledge_service": ks,
    }

    status_dict = {
        "event_key": "task_st_01",
        "meta_message_id": "wamid.task.unknown",
        "status": "delivered",
        "timestamp": datetime.now(UTC).isoformat(),
    }

    res = await process_status_update_task({"container": mock_container}, status_dict)
    assert res["handled"] is True


@pytest.mark.asyncio
async def test_cleanup_and_purge_tasks(
    test_settings: Settings,
    test_config: AppConfig,
    session_factory,
):
    mock_container = {
        "settings": test_settings,
        "config": test_config,
        "session_factory": session_factory,
    }

    expired = await cleanup_expired_conversations_task(
        {"container": mock_container}, test_config.business.business_id
    )
    assert expired >= 0

    purged = await purge_retention_data_task(
        {"container": mock_container}, test_config.business.business_id, retention_days=30
    )
    assert "purged_webhooks" in purged
