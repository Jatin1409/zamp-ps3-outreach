"""DEV ONLY: populate a local database with runs produced from offline fixtures, to look at the UI without API keys.

Usage:  DATABASE_URL=sqlite+aiosqlite:///dev_ui.db python -m scripts.dev_offline_seed
These runs use fixture providers (model name 'fixture', $0 cost) and are clearly not live runs.
"""
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import jobs  # noqa: E402
from app.db import create_all, init_engine, session  # noqa: E402
from app.models import Run  # noqa: E402
from app.pipeline import stages  # noqa: E402
from app.pipeline.runner import init_stages  # noqa: E402
from app.seed import LEDGERLINE, ensure_seed  # noqa: E402
from tests import test_routes_offline as T  # noqa: E402
from tests.helpers import load  # noqa: E402


async def one(prospect, fx, mode="live"):
    async with session() as s:
        r = Run(prospect=prospect, seller_profile=LEDGERLINE, mode=mode, status="queued", scenario_key="offline-fixture")
        s.add(r)
        await s.commit()
        rid = r.id
    await init_stages(rid)
    jobs.FIXTURES[rid] = fx
    await jobs.enqueue(rid, "start")
    await jobs.drain()
    return rid


async def main():
    init_engine()
    await create_all()
    await ensure_seed()
    base = load("handmade_happy")
    stages.today = lambda: dt.date.fromisoformat(base["today"])
    P = T.P
    await one(P, base)
    await one(P, T.ambiguous(base))
    await one(P, T.moved(base))
    await one(P, T.layoff(base))
    import copy
    thin = copy.deepcopy(base)
    thin["llm"]["filter"][0]["judgments"][1]["about"] = "other_person"
    await one(P, thin)
    none = copy.deepcopy(base)
    none["search"] = {"*": []}
    none["llm"]["identity"] = [{"candidates": [], "conflicting_sources": False, "summary": "none"}]
    await one({"name": "Tomas Lindqvist", "company": "Brightwater Fasteners"}, none)
    fail = copy.deepcopy(base)
    fail["fail_search"] = True
    await one(P, fail)
    print("seeded")


if __name__ == "__main__":
    asyncio.run(main())
