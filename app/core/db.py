"""Async database engine, session factory and health probe."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine, adapting pool options to the driver."""
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        ensure_sqlite_parent(settings.database_url)
    else:
        # Pool sizing applies to server-backed drivers only; aiosqlite rejects it.
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_recycle"] = 1800
    return create_async_engine(settings.database_url, **kwargs)


def ensure_sqlite_parent(database_url: str) -> None:
    """Create the directory holding a file-backed SQLite database, if missing."""
    _, _, location = database_url.partition(":///")
    if not location or location.startswith(":memory:"):
        return
    parent = Path(location).expanduser().parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_database(engine: AsyncEngine) -> bool:
    """Readiness probe for the database."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - probe must never raise
        return False
    return True
