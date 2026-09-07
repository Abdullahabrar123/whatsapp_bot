"""Pytest fixtures and configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config_loader import AppConfig
from app.core.security import FieldCipher
from app.core.settings import Settings
from app.domain.models import Base
from app.integrations.llm_client import DeterministicClient
from app.integrations.whatsapp_client import SendResult
from app.main import create_app
from app.services.knowledge_service import KnowledgeService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


class FakeWhatsAppClient:
    """Mock WhatsApp client for tests."""

    def __init__(self) -> None:
        self.sent_texts: list[dict[str, Any]] = []
        self.sent_templates: list[dict[str, Any]] = []
        self.marked_read: list[str] = []

    async def send_text(
        self, to: str, body: str, *, preview_url: bool = False
    ) -> SendResult:
        self.sent_texts.append({"to": to, "body": body, "preview_url": preview_url})
        return SendResult(
            meta_message_id=f"wamid.test.{len(self.sent_texts)}",
            recipient_wa_id=to,
        )

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        *,
        body_variables: list[str] | None = None,
    ) -> SendResult:
        self.sent_templates.append(
            {
                "to": to,
                "template_name": template_name,
                "language": language,
                "body_variables": body_variables,
            }
        )
        return SendResult(
            meta_message_id=f"wamid.template.{len(self.sent_templates)}",
            recipient_wa_id=to,
        )

    async def send_interactive_buttons(
        self, to: str, body: str, buttons: list[dict[str, str]], *, header: str | None = None
    ) -> SendResult:
        return SendResult(meta_message_id="wamid.buttons.1", recipient_wa_id=to)

    async def send_interactive_list(
        self,
        to: str,
        body: str,
        button_text: str,
        sections: list[dict[str, Any]],
        *,
        header: str | None = None,
    ) -> SendResult:
        return SendResult(meta_message_id="wamid.list.1", recipient_wa_id=to)

    async def mark_as_read(self, meta_message_id: str) -> bool:
        self.marked_read.append(meta_message_id)
        return True

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key=TEST_SECRET_KEY,
        database_url="sqlite+aiosqlite:///:memory:",
        admin_api_key="test_admin_key_for_testing_purposes_only",
        meta_app_secret="test_meta_app_secret",
        meta_webhook_verify_token="test_meta_verify_token",
        meta_wABA_id="test_waba_id",
        meta_phone_number_id="test_phone_number_id",
        meta_access_token="test_meta_access_token",
        llm_provider="deterministic",
    )


@pytest.fixture(scope="session")
def test_config() -> AppConfig:
    return AppConfig.load(
        PROJECT_ROOT / "config" / "business.yaml",
        PROJECT_ROOT / "config" / "bot.yaml",
        PROJECT_ROOT / "config" / "templates.yaml",
    )


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest_asyncio.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def fake_whatsapp() -> FakeWhatsAppClient:
    return FakeWhatsAppClient()


@pytest.fixture
def cipher() -> FieldCipher:
    return FieldCipher(TEST_SECRET_KEY)


@pytest.fixture
def deterministic_llm() -> DeterministicClient:
    return DeterministicClient()


@pytest_asyncio.fixture
async def api_client(
    test_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    async_engine: AsyncEngine,
    fake_whatsapp: FakeWhatsAppClient,
    test_config: AppConfig,
) -> AsyncIterator[AsyncClient]:
    app = create_app(test_settings)
    app.state.engine = async_engine
    app.state.session_factory = session_factory
    app.state.config = test_config
    app.state.business_id = test_config.business.business_id
    app.state.whatsapp_client = fake_whatsapp
    app.state.llm_client = DeterministicClient()

    ks = KnowledgeService(
        test_config.business,
        project_root=PROJECT_ROOT,
        llm_client=app.state.llm_client,
        embeddings_enabled=False,
    )
    ks.build_index()
    app.state.knowledge_service = ks

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
