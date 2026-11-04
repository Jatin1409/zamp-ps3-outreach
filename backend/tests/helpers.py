import datetime as dt
import json
from pathlib import Path

from app import jobs
from app.db import session
from app.models import Run
from app.pipeline.runner import init_stages
from app.seed import LEDGERLINE

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def pin_today(monkeypatch, fixture: dict):
    d = dt.date.fromisoformat(fixture.get("today", "2026-11-01"))
    monkeypatch.setattr("app.pipeline.stages.today", lambda: d)


async def start_run(prospect: dict, fixture: dict, mode: str = "live") -> str:
    async with session() as s:
        r = Run(prospect=prospect, seller_profile=fixture.get("seller_profile", LEDGERLINE), mode=mode,
                status="queued")
        s.add(r)
        await s.commit()
        rid = r.id
    await init_stages(rid)
    jobs.FIXTURES[rid] = fixture
    await jobs.enqueue(rid, "start")
    await jobs.drain()
    return rid


async def get(rid: str) -> Run:
    async with session() as s:
        return await s.get(Run, rid)
