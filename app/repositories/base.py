"""Shared repository plumbing.

Repositories own all SQL. Services never build queries, so every database access
goes through parameterised SQLAlchemy constructs -- there is no string-formatted
SQL anywhere in the codebase, which removes the injection surface entirely.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Common constructor and flush helper."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def flush(self) -> None:
        await self.session.flush()

    def add(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        return instance
