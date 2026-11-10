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


# Columns added after the first deployment. create_all() never alters existing tables, so they are added here,
# idempotently, on every boot. Old rows get NULL, which every reader treats as the previous behaviour.
_ADDED_COLUMNS = [
    ("runs", "seller_profile_id", "VARCHAR(32)"),
    ("runs", "cancel_requested", "BOOLEAN DEFAULT FALSE"),
    ("runs", "interrupted", "JSON"),
    ("search_records", "qkey", "VARCHAR(700)"),
    ("seller_profiles", "created_at", "TIMESTAMP WITH TIME ZONE"),
]
_ADDED_INDEXES = [
    ("ix_runs_seller_profile_id", "runs", "seller_profile_id"),
    ("ix_search_records_qkey", "search_records", "qkey"),
]


async def migrate():
    from sqlalchemy import text

    async with engine().begin() as conn:
        dialect = conn.dialect.name
        for table, col, typ in _ADDED_COLUMNS:
            if dialect == "postgresql":
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}"))
            else:
                cols = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
                if col not in cols:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} "
                                            f"{typ.replace(' WITH TIME ZONE', '')}"))
        for name, table, col in _ADDED_INDEXES:
            await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col})"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session() as s:
        yield s
