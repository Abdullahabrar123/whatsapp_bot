"""Arq worker configuration and task dispatch abstraction.

Provides a unified interface for enqueuing jobs to Redis/arq or executing
them asynchronously in the background via asyncio tasks when Redis is disabled
or in test environments.
"""

from __future__ import annotations

import asyncio
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.workers.tasks import (
    build_service_container,
    cleanup_expired_conversations_task,
    execute_campaign_task,
    process_inbound_message_task,
    process_status_update_task,
    purge_retention_data_task,
)

logger = get_logger(__name__)


class TaskDispatcher:
    """Dispatches background tasks via Arq Redis pool or local asyncio tasks."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ArqRedis | None = None
        self._local_container: dict[str, Any] | None = None

    async def connect(self) -> None:
        """Attempt to connect to Redis for task queuing."""
        try:
            redis_settings = RedisSettings.from_dsn(self._settings.redis_url)
            self._pool = await create_pool(redis_settings)
            logger.info("arq_pool_connected", redis_url=self._settings.redis_url)
        except Exception as exc:  # noqa: BLE001 - fallback to in-memory dispatch
            logger.warning(
                "redis_unavailable_fallback_to_asyncio",
                error=type(exc).__name__,
                redis_url=self._settings.redis_url,
            )
            self._pool = None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        if self._local_container is not None:
            engine = self._local_container.get("engine")
            if engine is not None:
                await engine.dispose()
            whatsapp = self._local_container.get("whatsapp_client")
            if whatsapp is not None:
                await whatsapp.aclose()
            llm = self._local_container.get("llm_client")
            if llm is not None:
                await llm.aclose()

    async def _get_local_container(self) -> dict[str, Any]:
        if self._local_container is None:
            self._local_container = await build_service_container()
        return self._local_container

    async def enqueue_inbound_message(
        self, event_dict: dict[str, Any], correlation_id: str | None = None
    ) -> None:
        """Enqueue inbound message processing."""
        if self._pool is not None:
            await self._pool.enqueue_job(
                "process_inbound_message_task",
                event_dict=event_dict,
                correlation_id=correlation_id,
            )
        else:
            container = await self._get_local_container()
            asyncio.create_task(
                process_inbound_message_task(
                    {"container": container}, event_dict, correlation_id
                )
            )

    async def enqueue_status_update(
        self, event_dict: dict[str, Any], correlation_id: str | None = None
    ) -> None:
        """Enqueue status webhook processing."""
        if self._pool is not None:
            await self._pool.enqueue_job(
                "process_status_update_task",
                event_dict=event_dict,
                correlation_id=correlation_id,
            )
        else:
            container = await self._get_local_container()
            asyncio.create_task(
                process_status_update_task(
                    {"container": container}, event_dict, correlation_id
                )
            )

    async def enqueue_campaign(
        self, campaign_id: int, dry_run: bool = False, correlation_id: str | None = None
    ) -> None:
        """Enqueue campaign execution."""
        if self._pool is not None:
            await self._pool.enqueue_job(
                "execute_campaign_task",
                campaign_id=campaign_id,
                dry_run=dry_run,
                correlation_id=correlation_id,
            )
        else:
            container = await self._get_local_container()
            asyncio.create_task(
                execute_campaign_task(
                    {"container": container}, campaign_id, dry_run, correlation_id
                )
            )


# ========================================================= Arq Worker Settings
class WorkerSettings:
    """Worker settings class for the `arq` CLI entrypoint."""

    settings = get_settings()
    functions = [
        process_inbound_message_task,
        process_status_update_task,
        execute_campaign_task,
        cleanup_expired_conversations_task,
        purge_retention_data_task,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    poll_delay = 0.5

    @classmethod
    async def on_startup(cls, ctx: dict[str, Any]) -> None:
        logger.info("arq_worker_startup")
        ctx["container"] = await build_service_container(ctx)

    @classmethod
    async def on_shutdown(cls, ctx: dict[str, Any]) -> None:
        logger.info("arq_worker_shutdown")
        container = ctx.get("container")
        if container:
            engine = container.get("engine")
            if engine:
                await engine.dispose()
            whatsapp = container.get("whatsapp_client")
            if whatsapp:
                await whatsapp.aclose()
            llm = container.get("llm_client")
            if llm:
                await llm.aclose()
