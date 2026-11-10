import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ACCESS_CODE", "test-code")
os.environ["WORKER_ENABLED"] = "false"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from app import db as dbm
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    get_settings.cache_clear()
    dbm.init_engine()
    await dbm.create_all()
    await dbm.migrate()
    from app.seed import ensure_seed
    await ensure_seed()
    yield dbm
    await dbm.engine().dispose()
