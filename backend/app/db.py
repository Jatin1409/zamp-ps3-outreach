from __future__ import annotations

import datetime as dt
from typing import AsyncIterator

from sqlalchemy import JSON, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None):
    global _engine, _sessionmaker
    settings = get_settings()
    url = url or settings.async_database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs.update(pool_size=10, max_overflow=5, pool_pre_ping=True)
    _engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine.sync_engine, "connect")
        def _pragma(dbapi_conn, _):  # pragma: no cover - sqlite only
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def engine():
    if _engine is None:
        init_engine()
    return _engine


def session() -> AsyncSession:
    if _sessionmaker is None:
        init_engine()
    return _sessionmaker()  # type: ignore[misc]


async def create_all():
    from . import models  # noqa: F401  (register tables)
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session() as s:
        yield s
