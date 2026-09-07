"""FastAPI application factory and middleware pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.webhooks import router as webhooks_router
from app.core.config_loader import AppConfig
from app.core.db import create_engine, create_session_factory
from app.core.exceptions import AppError, ConfigurationError
from app.core.logging import get_logger, set_correlation_id
from app.core.security import FieldCipher
from app.core.settings import Settings, get_settings
from app.domain.models import Base
from app.integrations.llm_client import build_llm_client
from app.integrations.whatsapp_client import WhatsAppClient
from app.services.knowledge_service import KnowledgeService
from app.workers.arq_runtime import TaskDispatcher

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle: initialize database, clients, knowledge base and queues."""
    settings = getattr(app.state, "settings", None) or get_settings()
    app.state.settings = settings
    logger.info("app_starting", app_env=settings.app_env, version="1.0.0")

    # 1. Load & validate business configuration
    config = AppConfig.load(
        settings.business_config_path,
        settings.bot_config_path,
        settings.templates_config_path,
    )
    app.state.config = config
    app.state.business_id = config.business.business_id

    # 2. Database setup
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # In development/test mode, auto-create tables if sqlite is used
    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # 3. Security cipher & external clients
    cipher = FieldCipher(settings.app_secret_key.get_secret_value())
    app.state.cipher = cipher
    whatsapp_client = WhatsAppClient(settings)
    llm_client = build_llm_client(settings)
    app.state.whatsapp_client = whatsapp_client
    app.state.llm_client = llm_client

    # 4. Knowledge base indexing
    knowledge_service = KnowledgeService(
        config.business,
        project_root=settings.business_config_path.parent.parent,
        llm_client=llm_client,
        embeddings_enabled=False,
    )
    knowledge_service.build_index()
    app.state.knowledge_service = knowledge_service

    # 5. Background task dispatcher
    dispatcher = TaskDispatcher(settings)
    await dispatcher.connect()
    app.state.dispatcher = dispatcher

    logger.info(
        "app_ready",
        business_id=config.business.business_id,
        business_name=config.business.name,
        llm_provider=settings.llm_provider,
    )

    yield

    logger.info("app_shutting_down")
    await dispatcher.close()
    await whatsapp_client.aclose()
    await llm_client.aclose()
    await engine.dispose()
    logger.info("app_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI instance."""
    resolved_settings = settings or get_settings()

    app = FastAPI(
        title="WhatsApp Business Platform Chatbot",
        description="Configuration-driven enterprise WhatsApp Business chatbot",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.app_env != "production" else None,
        redoc_url="/redoc" if resolved_settings.app_env != "production" else None,
    )
    app.state.settings = resolved_settings

    # Security & Logging Middlewares
    @app.middleware("http")
    async def correlation_id_and_security_headers_middleware(
        request: Request, call_next: object
    ) -> Response:
        import uuid

        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        set_correlation_id(correlation_id)

        # Content length check
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > resolved_settings.max_request_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Request payload exceeds maximum allowed size."},
            )

        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Correlation-Id"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    if resolved_settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    # Exception Handlers
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        _ = request
        status_code = getattr(exc, "status_code", 400)
        return JSONResponse(
            status_code=status_code,
            content={
                "error": exc.message,
                "code": getattr(exc, "code", "app_error"),
                "details": exc.details,
            },
        )

    @app.exception_handler(ConfigurationError)
    async def handle_config_error(request: Request, exc: ConfigurationError) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": exc.message, "code": "configuration_error", "details": exc.details},
        )

    # Register Routers
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(admin_router)

    return app


app = create_app()
