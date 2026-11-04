from sqlalchemy import select

from app.db import session
from app.models import Draft, Signal, Stage

from .helpers import get, load, pin_today, start_run


async def test_happy_path_end_to_end(db, monkeypatch):
    fx = load("handmade_happy")
    pin_today(monkeypatch, fx)
    rid = await start_run({"name": "Dana Whitfield", "company": "Northwind Freight", "role": "CFO"}, fx)
    run = await get(rid)
    assert run.error is None, run.error
    assert run.status == "awaiting_review"
    # The fixture's second evidence quote is attributed to the wrong source; code catches it -> medium.
    assert run.identity["status"] == "resolved" and run.identity["confidence"] == "medium"
    assert any("Only one source" in r for r in run.identity["reasons"])
    assert run.final_route == "draft"
    async with session() as s:
        stages = (await s.execute(select(Stage).where(Stage.run_id == rid))).scalars().all()
        sigs = (await s.execute(select(Signal).where(Signal.run_id == rid))).scalars().all()
        drafts = (await s.execute(select(Draft).where(Draft.run_id == rid))).scalars().all()
    by = {st.key: st for st in stages}
    for k in ("identity", "gather", "filter", "rank", "route", "draft", "verify", "generic"):
        assert by[k].status == "succeeded", (k, by[k].status, by[k].error)
        assert by[k].duration_ms is not None and by[k].explanation
    assert by["review"].status == "waiting"
    kept = [x for x in sigs if x.status == "kept"]
    assert {x.sid for x in kept} == {"s1", "s2"}
    assert any(x.drop_reason and x.drop_reason.startswith("Duplicate") for x in sigs)
    assert by["rank"].output["explain"]["winner"] == "s2"
    d = drafts[-1]
    assert d.verification["passed"] and d.verification["unsupported"] == 0
    assert d.genericness["generic"] is False
    assert run.tokens_in > 0
