"""Replays every fixture recorded from a real live run (fixtures/recorded/*.json) through the full pipeline and
checks it takes the same route. Proves routing is deterministic given the same evidence + model judgements."""
import json
from pathlib import Path

import httpx
import pytest

from app import jobs
from app.main import app

from .helpers import pin_today

REC = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "recorded").glob("*.json"))
H = {"X-Access-Code": "test-code"}


@pytest.mark.skipif(not REC, reason="no recorded fixtures yet")
@pytest.mark.parametrize("path", REC, ids=[p.stem for p in REC])
async def test_recorded_run_reproduces_route(db, monkeypatch, path):
    fx = json.loads(path.read_text(encoding="utf-8"))
    pin_today(monkeypatch, fx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/runs", json={"prospect": fx["prospect"], "mode": "live"}, headers=H)
        rid = r.json()["id"]
        jobs.FIXTURES[rid] = fx
        await jobs.drain()
        for d in fx.get("decisions", []):
            assert (await c.post(f"/api/runs/{rid}/decision", json=d, headers=H)).status_code == 200
            await jobs.drain()
        d = (await c.get(f"/api/runs/{rid}", headers=H)).json()
    exp = fx["expected"]
    assert d["error"] is None, d["error"]
    assert d["final_route"] == exp["final_route"]
    assert d["route_path"] == exp["route_path"]
    assert sorted(d["edge_cases"]) == sorted(exp["edge_cases"])
