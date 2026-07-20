"""Async SQLAlchemy engine, session factory, and startup schema bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# Additive migrations for columns introduced after the initial release
# (create_all only creates missing tables, never alters existing ones).
_MIGRATIONS = (
    "ALTER TABLE vfs_index ADD COLUMN IF NOT EXISTS user_caption TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE vfs_index ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS default_chat_id VARCHAR(32) NOT NULL DEFAULT ''",
)


async def init_db() -> None:
    from sqlalchemy import text

    from app import models  # noqa: F401 - register mappings

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in _MIGRATIONS:
            await conn.execute(text(statement))


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
