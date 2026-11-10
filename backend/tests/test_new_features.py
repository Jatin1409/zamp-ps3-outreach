"""Seller profiles, stop/cancel, 90-second budget, performance (parallelism + cache), entity edge cases."""
import asyncio
import copy
import time

import httpx
import pytest

from app import jobs
from app.config import get_settings
from app.main import app
from app.providers.search import FixtureSearch

from .helpers import load, pin_today

H = {"X-Access-Code": "test-code"}
P = {"name": "Dana Whitfield", "company": "Northwind Freight", "role": "CFO"}


@pytest.fixture
def base(monkeypatch):
    fx = load("handmade_happy")
    pin_today(monkeypatch, fx)
    return fx


@pytest.fixture
async def client(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def create(client, fx, prospect=P, headers=None):
    r = await client.post("/api/runs", json={"prospect": prospect}, headers={**H, **(headers or {})})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    jobs.FIXTURES[rid] = fx
    return rid


async def get(client, rid):
    return (await client.get(f"/api/runs/{rid}", headers=H)).json()


async def wait_for(client, rid, pred, timeout=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        d = await get(client, rid)
        if pred(d):
            return d
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not reached; status={d['status']} stage={d['current_stage']}")


def stage(d, key):
    return next(s for s in d["stages"] if s["key"] == key)


def seller(name="Acme Payables"):
    return {"company_name": name, "product": "Acme automates supplier invoice matching for finance teams.",
            "ideal_customer": "Mid-size finance teams", "problems_solved": ["Manual invoice matching"],
            "proof_points": ["Acme customers close the books two days faster"], "tone": "Plain",
            "sender_name": "Sam at Acme"}


# =============================================================== seller profiles
async def test_profiles_crud_and_validation(client):
    lst = (await client.get("/api/seller-profiles", headers=H)).json()
    assert lst["current_id"] == "default" and len(lst["items"]) == 1
    r = await client.post("/api/seller-profiles", json={"data": seller()}, headers=H)
    assert r.status_code == 200
    pid = r.json()["id"]
    # duplicate name rejected (case-insensitive)
    r = await client.post("/api/seller-profiles", json={"data": seller("acme payables")}, headers=H)
    assert r.status_code == 409
    # validation: needs at least one problem solved
    bad = seller("Other")
    bad["problems_solved"] = ["  "]
    assert (await client.post("/api/seller-profiles", json={"data": bad}, headers=H)).status_code == 422
    # duplicate an existing profile
    r = await client.post("/api/seller-profiles", json={"copy_from": pid}, headers=H)
    assert r.json()["name"] == "Acme Payables (copy)"
    # edit
    upd = seller("Acme Payables")
    upd["tone"] = "Warm"
    r = await client.put(f"/api/seller-profiles/{pid}", json=upd, headers=H)
    assert r.json()["data"]["tone"] == "Warm"
    # switching = sending the header; unknown id falls back to the default profile
    cur = (await client.get("/api/seller-profiles", headers={**H, "X-Seller-Profile": pid})).json()
    assert cur["current_id"] == pid
    cur = (await client.get("/api/seller-profiles", headers={**H, "X-Seller-Profile": "nope"})).json()
    assert cur["current_id"] == "default"


async def test_profile_data_isolation_and_delete_keeps_history(client, base):
    pid = (await client.post("/api/seller-profiles", json={"data": seller()}, headers=H)).json()["id"]
    r1 = await create(client, base)                                       # default profile
    r2 = await create(client, base, headers={"X-Seller-Profile": pid})   # Acme
    await jobs.drain()
    d2 = await get(client, r2)
    assert d2["seller_profile_id"] == pid and d2["seller_profile"]["company_name"] == "Acme Payables"
    assert (await get(client, r1))["seller_profile"]["company_name"] == "Ledgerline"
    only_acme = (await client.get(f"/api/runs?profile={pid}", headers=H)).json()
    only_default = (await client.get("/api/runs?profile=default", headers=H)).json()
    assert [x["id"] for x in only_acme["items"]] == [r2] and [x["id"] for x in only_default["items"]] == [r1]
    assert (await client.get(f"/api/metrics?profile={pid}", headers=H)).json()["runs"] == 1
    assert (await client.get("/api/metrics?profile=all", headers=H)).json()["runs"] == 2
    # delete keeps the run (with its snapshot); the last profile can't be deleted
    r = await client.delete(f"/api/seller-profiles/{pid}", headers=H)
    assert r.json()["runs_kept"] == 1
    assert (await get(client, r2))["seller_profile"]["company_name"] == "Acme Payables"
    assert (await client.delete("/api/seller-profiles/default", headers=H)).status_code == 409


async def test_legacy_runs_without_profile_belong_to_default(client, base):
    from app.db import session
    from app.models import Run

    rid = await create(client, base)
    await jobs.drain()
    async with session() as s:
        row = await s.get(Run, rid)
        row.seller_profile_id = None   # as created before multi-profile support
        await s.commit()
    assert (await client.get("/api/runs?profile=default", headers=H)).json()["total"] == 1


# =============================================================== stop / cancel
async def test_cancel_during_a_stage_stops_and_keeps_results(client, base):
    fx = copy.deepcopy(base)
    fx["llm_delay_s"] = {"draft": 3}
    rid = await create(client, fx)
    worker = asyncio.create_task(jobs.drain())
    await wait_for(client, rid, lambda d: d["current_stage"] == "draft" and d["status"] == "running")
    t0 = time.monotonic()
    r = await client.post(f"/api/runs/{rid}/cancel", headers=H)
    assert r.json()["status"] in ("cancelling", "cancelled")
    await client.post(f"/api/runs/{rid}/cancel", headers=H)  # repeated cancel is harmless
    await worker
    d = await get(client, rid)
    assert time.monotonic() - t0 < 2.5            # did not wait for the 3 s model call
    assert d["status"] == "cancelled" and d["outcome"] == "cancelled"
    assert stage(d, "draft")["status"] == "cancelled"
    assert all(stage(d, k)["status"] == "skipped" for k in ("verify", "generic", "review"))
    assert stage(d, "rank")["status"] == "succeeded" and d["signals"]   # earlier results preserved
    assert d["interrupted"]["reason"] == "user" and d["interrupted"]["stage"] == "draft"
    assert not any(s["status"] == "running" for s in d["stages"])
    # nothing new started after the stop
    assert all(c["purpose"] != "verify" for c in d["llm_call_log"])
    # a fresh run can be started afterwards
    r = await client.post(f"/api/runs/{rid}/rerun", headers=H)
    assert r.status_code == 200 and r.json()["id"] != rid


async def test_cancel_during_identity(client, base):
    fx = copy.deepcopy(base)
    fx["llm_delay_s"] = {"identity": 3}
    rid = await create(client, fx)
    worker = asyncio.create_task(jobs.drain())
    await wait_for(client, rid, lambda d: d["current_stage"] == "identity")
    await asyncio.sleep(0.2)
    await client.post(f"/api/runs/{rid}/cancel", headers=H)
    await worker
    d = await get(client, rid)
    assert d["status"] == "cancelled" and stage(d, "identity")["status"] == "cancelled"
    assert stage(d, "gather")["status"] == "skipped"


async def test_cancel_queued_and_paused_runs(client, base):
    rid = await create(client, base)                       # queued, worker not started
    r = await client.post(f"/api/runs/{rid}/cancel", headers=H)
    assert r.json()["status"] == "cancelled"
    await jobs.drain()                                     # the queued job must not run
    d = await get(client, rid)
    assert d["status"] == "cancelled" and d["llm_calls"] == 0
    # paused for the rep
    fx = copy.deepcopy(base)
    c1 = fx["llm"]["identity"][0]["candidates"][0]
    c1.update(at_input_company="former", current_company="Globex")
    rid2 = await create(client, fx)
    await jobs.drain()
    assert (await get(client, rid2))["status"] == "awaiting_rep"
    await client.post(f"/api/runs/{rid2}/cancel", headers=H)
    d2 = await get(client, rid2)
    assert d2["status"] == "cancelled" and stage(d2, "gather")["status"] == "skipped"


async def test_cancel_finished_or_reviewing_runs(client, base):
    rid = await create(client, base)
    await jobs.drain()
    assert (await client.post(f"/api/runs/{rid}/cancel", headers=H)).status_code == 409  # awaiting review
    await client.post(f"/api/runs/{rid}/review", json={"action": "reject"}, headers=H)
    r = await client.post(f"/api/runs/{rid}/cancel", headers=H)
    assert r.json()["already_finished"] is True


async def test_cancel_during_regenerate_keeps_previous_draft(client, base):
    fx = copy.deepcopy(base)
    rid = await create(client, fx)
    await jobs.drain()
    fx["llm_delay_s"] = {"draft": 3}
    await client.post(f"/api/runs/{rid}/review", json={"action": "regenerate"}, headers=H)
    worker = asyncio.create_task(jobs.drain())
    await wait_for(client, rid, lambda d: d["status"] == "running" and d["current_stage"] == "draft")
    await client.post(f"/api/runs/{rid}/cancel", headers=H)
    await worker
    d = await get(client, rid)
    assert d["status"] == "awaiting_review" and len(d["drafts"]) == 1   # previous draft kept
    assert d["drafts"][0]["verification"]["passed"]


# =============================================================== 90-second budget
async def test_time_limit_before_a_draft_exists(client, base, monkeypatch):
    monkeypatch.setattr(get_settings(), "run_budget_s", 1.5)
    fx = copy.deepcopy(base)
    fx["llm_delay_s"] = {"rank": 5}
    rid = await create(client, fx)
    t0 = time.monotonic()
    await jobs.drain()
    d = await get(client, rid)
    assert time.monotonic() - t0 < 4
    assert d["status"] == "timed_out" and d["outcome"] == "timed_out"
    assert stage(d, "rank")["status"] == "timed_out" and stage(d, "route")["status"] == "skipped"
    assert d["signals"] and d["interrupted"]["reason"] == "timeout"   # partial results kept
    r = await client.post(f"/api/runs/{rid}/retry", json={}, headers=H)      # can be retried
    assert r.status_code == 200


async def test_time_limit_during_verification_returns_flagged_draft(client, base, monkeypatch):
    monkeypatch.setattr(get_settings(), "run_budget_s", 1.5)
    fx = copy.deepcopy(base)
    fx["llm_delay_s"] = {"verify": 5}
    rid = await create(client, fx)
    await jobs.drain()
    d = await get(client, rid)
    assert d["status"] == "awaiting_review"
    assert stage(d, "verify")["status"] == "timed_out"
    assert d["drafts"] and d["drafts"][-1]["verification"] is None
    assert "NOT fact-checked" in " ".join(d["warnings"])
    # can't approve an unchecked draft without acknowledging it
    r = await client.post(f"/api/runs/{rid}/review", json={"action": "approve"}, headers=H)
    assert r.status_code == 400
    r = await client.post(f"/api/runs/{rid}/review", json={"action": "approve", "acknowledged_incomplete": True},
                          headers=H)
    assert r.json()["outcome"] == "approved"


async def test_optional_regeneration_skipped_when_it_cannot_finish_in_time(client, base, monkeypatch):
    monkeypatch.setattr(get_settings(), "min_s_for_regenerate", 10_000)
    fx = copy.deepcopy(base)
    g = copy.deepcopy(fx["llm"]["generic"][0])
    g["decoys"][0]["still_makes_sense"] = True
    fx["llm"]["generic"] = [g]
    rid = await create(client, fx)
    await jobs.drain()
    d = await get(client, rid)
    assert d["status"] == "awaiting_review" and len(d["drafts"]) == 1
    assert any("90-second limit" in w for w in d["warnings"])


# =============================================================== performance
async def test_searches_run_in_parallel(client, base):
    fx = copy.deepcopy(base)
    fx["search_delay_s"] = 0.6      # 4 identity + 5 gather searches: ~5.4 s if sequential
    rid = await create(client, fx)
    await jobs.drain()
    d = await get(client, rid)
    assert stage(d, "identity")["duration_ms"] < 1500
    assert stage(d, "gather")["duration_ms"] < 1500


async def test_scoring_batches_run_in_parallel(client, base):
    fx = copy.deepcopy(base)
    fx["llm_delay_s"] = {"filter": 0.5}
    # make 20 signals so the filter needs 2 batches of 15
    words = ["apex", "birch", "cobalt", "delta", "ember", "fjord", "garnet", "harbor", "iris", "jade", "kelp",
             "lumen", "maple", "nectar", "onyx", "pine", "quartz", "raven", "sable", "tundra"]
    fx["search"]["*"] = [dict(fx["search"]["*"][1], url=f"https://www.cfodive.com/news/a{i}",
                              title=f"{w} {w} {w} report") for i, w in enumerate(words)]
    fx["llm"]["filter"] = [{"judgments": [
        {"sid": f"s{i}", "about": "company", "signal_type": "company_news", "sensitive_category": "none",
         "contains_personal_details": False, "reason": "r"} for i in range(1, 21)]}]
    rid = await create(client, fx)
    await jobs.drain()
    d = await get(client, rid)
    calls = [c for c in d["llm_call_log"] if c["purpose"] == "filter"]
    # two batches (15 + 5), no re-asks; concurrent, so ~one call's latency rather than two
    assert len(calls) == 2 and stage(d, "filter")["duration_ms"] < 900


class CountingTavily(FixtureSearch):
    name = "tavily"
    calls = 0

    async def search(self, q):
        CountingTavily.calls += 1
        return await super().search(q)


async def test_identical_searches_are_cached_across_runs(client, base, monkeypatch):
    from app.pipeline import runner

    monkeypatch.setattr(runner, "make_search", lambda fx: CountingTavily(fx or {}))
    CountingTavily.calls = 0
    await create(client, base)
    await jobs.drain()
    first = CountingTavily.calls
    rid2 = await create(client, base)
    await jobs.drain()
    d2 = await get(client, rid2)
    assert first > 0 and CountingTavily.calls == first        # second run made no new search calls
    assert d2["search_calls"] == 0 and d2["status"] == "awaiting_review"
    assert stage(d2, "gather")["output"].get("search_cache_hits")


# =============================================================== entity edge cases
def fictional_fx(base):
    fx = copy.deepcopy(base)
    fx["search"] = {"*": [{
        "title": "Tony Stark - Wikipedia", "url": "https://en.wikipedia.org/wiki/Tony_Stark",
        "content": "Tony Stark is a fictional character appearing in American comic books published by Marvel "
                   "Comics. Stark Industries is a fictional company in the Marvel Universe.",
        "raw_content": None, "published_date": None}]}
    fx["llm"]["identity"] = [{
        "candidates": [{"full_name": "Tony Stark", "current_company": "Stark Industries", "current_role": "CEO",
                        "at_input_company": "current", "role_matches_input": "yes", "distinguisher": "Marvel",
                        "is_fictional": True, "fiction_source": "Marvel Comics",
                        "evidence": [{"source": 0, "quote": "Tony Stark is a fictional character appearing in "
                                                            "American comic books published by Marvel Comics."}]}],
        "companies": [{"name": "Stark Industries", "what_it_does": "fictional conglomerate",
                       "short_label": "Marvel", "is_fictional": True, "fiction_source": "Marvel Comics",
                       "evidence": [{"source": 0, "quote": "Stark Industries is a fictional company in the "
                                                           "Marvel Universe."}]}],
        "conflicting_sources": False, "summary": "fictional"}]
    return fx


async def test_fictional_character_is_identified_not_drafted(client, base):
    rid = await create(client, fictional_fx(base),
                       prospect={"name": "Tony Stark", "company": "Stark Industries", "role": "CEO"})
    await jobs.drain()
    d = await get(client, rid)
    assert d["identity"]["status"] == "fictional" and d["outcome"] == "not_drafted"
    assert "fictional_entity" in d["edge_cases"] and stage(d, "route")["output"]["rule"] == "R0"
    f = d["identity"]["fiction"]
    assert f["person"]["source"] == "Marvel Comics" and f["person"]["facts"][0]["verified"]
    assert f["company"]["name"] == "Stark Industries" and "No real person" in f["real_world"]
    assert stage(d, "gather")["status"] == "skipped" and d["drafts"] == [] and d["llm_calls"] == 1


async def test_fictional_claim_without_verifiable_quote_is_not_trusted(client, base):
    fx = fictional_fx(base)
    for c in fx["llm"]["identity"][0]["candidates"] + fx["llm"]["identity"][0]["companies"]:
        c["evidence"] = [{"source": 0, "quote": "an invented sentence that is not on the page at all"}]
    rid = await create(client, fx, prospect={"name": "Tony Stark", "company": "Stark Industries"})
    await jobs.drain()
    d = await get(client, rid)
    assert d["identity"]["status"] != "fictional"   # the model's say-so alone decides nothing


def two_companies_fx(base, person_found=False):
    fx = copy.deepcopy(base)
    fx["search"]["*"] = fx["search"]["*"] + [
        {"title": "Mercury bank", "url": "https://mercury.com/about", "content": "Mercury is a fintech company "
         "offering banking for startups.", "raw_content": None, "published_date": None},
        {"title": "Mercury Insurance", "url": "https://www.mercuryinsurance.com/about", "content": "Mercury "
         "Insurance is an auto insurance company based in California.", "raw_content": None, "published_date": None}]
    ident = copy.deepcopy(fx["llm"]["identity"][0])
    if not person_found:
        ident["candidates"] = []
    ident["companies"] = [
        {"name": "Mercury", "what_it_does": "banking for startups", "short_label": "fintech bank",
         "is_fictional": False, "fiction_source": "",
         "evidence": [{"source": 3, "quote": "Mercury is a fintech company offering banking for startups."}]},
        {"name": "Mercury Insurance", "what_it_does": "auto insurance", "short_label": "insurer, California",
         "is_fictional": False, "fiction_source": "",
         "evidence": [{"source": 4, "quote": "Mercury Insurance is an auto insurance company based in California."}]}]
    fx["llm"]["identity"] = [ident]
    return fx


async def test_ambiguous_company_pauses_then_research_uses_the_chosen_one(client, base):
    rid = await create(client, two_companies_fx(base), prospect={"name": "Dana Whitfield", "company": "Mercury"})
    await jobs.drain()
    d = await get(client, rid)
    assert d["status"] == "awaiting_rep" and d["pause"]["kind"] == "ambiguous_company"
    assert {c["name"] for c in d["pause"]["candidates"]} == {"Mercury", "Mercury Insurance"}
    assert "ambiguous_company" in d["edge_cases"]
    await client.post(f"/api/runs/{rid}/decision", json={"choice": "pick_company", "candidate_id": "k1"},
                      headers=H)
    await jobs.drain()
    d = await get(client, rid)
    assert d["status"] == "awaiting_review"
    assert any("fintech bank" in q for q in stage(d, "gather")["output"]["queries"])
    assert d["identity"]["company"]["status"] == "found"


async def test_company_resolved_by_the_person_needs_no_company_question(client, base):
    rid = await create(client, two_companies_fx(base, person_found=True),
                       prospect={"name": "Dana Whitfield", "company": "Mercury", "role": "CFO"})
    await jobs.drain()
    d = await get(client, rid)
    assert d["pause"] is None or d["pause"]["kind"] != "ambiguous_company"


async def test_verification_checklist_reports_what_could_not_be_verified(client, base):
    fx = copy.deepcopy(base)
    fx["llm"]["identity"][0]["candidates"] = []          # person not found, company has news
    fx["llm"]["identity"][0]["companies"] = [
        {"name": "Northwind Freight", "what_it_does": "freight brokerage", "short_label": "Chicago freight",
         "is_fictional": False, "fiction_source": "",
         "evidence": [{"source": 0, "quote": "Northwind Freight, the Chicago-based freight brokerage, raised a $40 "
                                             "million Series C"}]}]
    fx["llm"]["filter"][0]["judgments"][1]["about"] = "other_person"
    rid = await create(client, fx)
    await jobs.drain()
    d = await get(client, rid)
    checks = {c["key"]: c["status"] for c in d["identity"]["checks"]}
    assert checks["person"] == "not_verified" and checks["role"] == "not_verified"
    assert checks["company"] == "verified" and checks["company_news"] == "verified"
    assert checks["person_signal"] == "not_verified"
    assert d["personalisation"] == "company"
    # no claim about the person survives verification
    claims = d["drafts"][-1]["verification"]["claims"]
    assert not [c for c in claims if c["kind"] == "prospect_fact" and c["supported"]
                and c.get("source_id") == "s2"]


async def test_fictional_person_with_real_namesake_company_is_described_accurately(client, base):
    fx = fictional_fx(base)
    fx["search"]["*"].append({"title": "Stark Industries Houston", "url": "https://starkindustries.example.com/about",
                              "content": "Stark Industries is a compressor manufacturer based in Houston, Texas.",
                              "raw_content": None, "published_date": None})
    fx["llm"]["identity"][0]["companies"].append(
        {"name": "Stark Industries", "what_it_does": "compressor manufacturer", "short_label": "Houston",
         "is_fictional": False, "fiction_source": "",
         "evidence": [{"source": 1, "quote": "Stark Industries is a compressor manufacturer based in Houston, Texas."}]})
    rid = await create(client, fx, prospect={"name": "Tony Stark", "company": "Stark Industries"})
    await jobs.drain()
    d = await get(client, rid)
    f = d["identity"]["fiction"]
    assert d["identity"]["status"] == "fictional"
    assert "do exist" in f["real_world"] and "compressor manufacturer" in f["real_world"]
    assert f["real_companies"][0]["evidence"][0]["verified"]
