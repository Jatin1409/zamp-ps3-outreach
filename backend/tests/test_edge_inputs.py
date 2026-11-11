"""Edge cases around inputs, run states, races and unusual names (sweep of 2026-11-11)."""
import copy

import httpx
import pytest

from app import jobs
from app.main import app

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


async def start(client, fx, prospect=P):
    r = await client.post("/api/runs", json={"prospect": prospect}, headers=H)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    jobs.FIXTURES[rid] = fx
    return rid


async def get(client, rid):
    return (await client.get(f"/api/runs/{rid}", headers=H)).json()


# ------------------------------------------------------------------ input validation
@pytest.mark.parametrize("prospect", [
    {"name": "   ", "company": "Acme"},                 # whitespace-only name
    {"name": "Jane Roe", "company": "   "},             # whitespace-only company
    {"name": "J", "company": "Acme"},                   # too short
    {"name": "Jane Roe", "company": "Acme", "linkedin_url": "linkedin.com/in/jane"},
    {"name": "Jane Roe", "company": "Acme", "notes": "x" * 6001},
    {"name": "Jane Roe"},                               # company missing
])
async def test_bad_inputs_rejected(client, prospect):
    r = await client.post("/api/runs", json={"prospect": prospect}, headers=H)
    assert r.status_code == 422


async def test_inputs_are_trimmed(client, base):
    rid = await start(client, base, {"name": "  Dana Whitfield ", "company": " Northwind Freight  ", "role": "  "})
    d = await get(client, rid)
    assert d["prospect"]["name"] == "Dana Whitfield" and d["prospect"]["company"] == "Northwind Freight"
    assert d["prospect"]["role"] is None


def test_quotes_and_control_characters_never_break_search_queries():
    from app.pipeline.stages import q_safe
    assert q_safe('Dana "The Boss" Whitfield') == "Dana The Boss Whitfield"
    assert q_safe("Acme\n\tLtd") == "Acme Ltd"


@pytest.mark.parametrize("entered,found,ok", [
    ("टाटा मोटर्स", "टाटा मोटर्स लिमिटेड", True),        # non-Latin company names must still match
    ("Reliance Industries", "Reliance Industries Limited", True),
    ("Infosys", "Infosys Ltd", True),
    ("Tata Motors", "Tata Steel", False),
])
def test_company_matching_unicode_and_indian_names(entered, found, ok):
    from app.pipeline.textmatch import company_matches
    assert company_matches(entered, found) is ok


async def test_unicode_name_runs_end_to_end(client, base):
    rid = await start(client, base, {"name": "Jörg Schuschnig", "company": "Rosenbauer", "role": "CFO"})
    await jobs.drain()
    d = await get(client, rid)
    assert d["prospect"]["name"] == "Jörg Schuschnig" and d["status"] in ("awaiting_review", "completed")


# ------------------------------------------------------------------ the rep's notes are context, even when search finds nothing
async def test_notes_rescue_a_prospect_search_cannot_find(client, base):
    fx = copy.deepcopy(base)
    fx["search"] = {"*": []}
    fx["llm"]["identity"] = [{"candidates": [], "companies": [], "conflicting_sources": False, "summary": "none"}]
    fx["llm"]["filter"] = [{"judgments": [{"sid": "s1", "about": "person", "signal_type": "post",
                                           "sensitive_category": "none", "contains_personal_details": False,
                                           "reason": "rep note"}]}]
    fx["llm"]["rank"] = [{"judgments": [{"sid": "s1", "specificity": 3, "relevance": 3,
                                         "key_fact": "we process 4,000 supplier invoices a month by hand",
                                         "seller_problem": "Manual matching", "why": "direct"}]}]
    fx["llm"]["draft"][0]["hook_signal_id"] = "s1"
    note = "From her post last week: we process 4,000 supplier invoices a month by hand and it is killing close."
    rid = await start(client, fx, {"name": "Asha Verma", "company": "Kestrel Components", "notes": note})
    await jobs.drain()
    d = await get(client, rid)
    assert d["identity"]["status"] == "not_found"          # not the hard "nothing found" stop
    assert d["status"] == "awaiting_review" and d["drafts"]
    assert any(s["scope"] == "rep" for s in d["signals"])
    assert d["final_route"] == "draft_with_warning"        # low identity confidence is still flagged


# ------------------------------------------------------------------ run-state races
async def test_state_races_are_rejected_cleanly(client, base):
    rid = await start(client, base)
    await jobs.drain()
    # review actions on the wrong state
    assert (await client.post(f"/api/runs/{rid}/decision", json={"choice": "stop"}, headers=H)).status_code == 409
    assert (await client.post(f"/api/runs/{rid}/retry", json={}, headers=H)).status_code == 409
    # an edit that empties the email is refused
    r = await client.post(f"/api/runs/{rid}/review", json={"action": "edit", "subject": "x", "body": "   "}, headers=H)
    assert r.status_code == 422
    # two regenerate clicks: the second is refused while the first is queued
    assert (await client.post(f"/api/runs/{rid}/review", json={"action": "regenerate"}, headers=H)).status_code == 200
    assert (await client.post(f"/api/runs/{rid}/review", json={"action": "regenerate"}, headers=H)).status_code == 409
    await jobs.drain()
    # approve twice: second is refused, outcome unchanged
    assert (await client.post(f"/api/runs/{rid}/review", json={"action": "approve"}, headers=H)).json()["outcome"] == "approved"
    assert (await client.post(f"/api/runs/{rid}/review", json={"action": "approve"}, headers=H)).status_code == 409


async def test_unknown_ids(client):
    for path in ("/api/runs/nope", "/api/seller-profiles/nope"):
        assert (await client.get(path, headers=H)).status_code == 404
    for path in ("/api/runs/nope/cancel", "/api/runs/nope/rerun", "/api/runs/nope/retry"):
        assert (await client.post(path, json={}, headers=H)).status_code == 404


async def test_double_decision_is_rejected(client, base):
    fx = copy.deepcopy(base)
    c = fx["llm"]["identity"][0]["candidates"][0]
    c.update(at_input_company="former", current_company="Globex")
    rid = await start(client, fx)
    await jobs.drain()
    assert (await client.post(f"/api/runs/{rid}/decision", json={"choice": "use_new"}, headers=H)).status_code == 200
    assert (await client.post(f"/api/runs/{rid}/decision", json={"choice": "stop"}, headers=H)).status_code == 409


async def test_profile_deleted_while_selected_falls_back(client, base):
    data = {"company_name": "Temp Co", "product": "Temporary product for a test.", "ideal_customer": "Anyone",
            "problems_solved": ["x"], "sender_name": "T"}
    pid = (await client.post("/api/seller-profiles", json={"data": data}, headers=H)).json()["id"]
    await client.delete(f"/api/seller-profiles/{pid}", headers=H)
    r = await client.post("/api/runs", json={"prospect": P}, headers={**H, "X-Seller-Profile": pid})
    assert r.status_code == 200
    d = (await client.get(f"/api/runs/{r.json()['id']}", headers=H)).json()
    assert d["seller_profile_id"] == "default"


async def test_profile_edit_after_run_does_not_change_the_run(client, base):
    rid = await start(client, base)
    await jobs.drain()
    prof = (await client.get("/api/seller-profiles/default", headers=H)).json()["data"]
    prof["company_name"] = "Ledgerline Renamed"
    assert (await client.put("/api/seller-profiles/default", json=prof, headers=H)).status_code == 200
    d = await get(client, rid)
    assert d["seller_profile"]["company_name"] == "Ledgerline"


async def test_one_failed_search_does_not_sink_the_stage(client, base, monkeypatch):
    from app.providers.search import FixtureSearch, SearchError

    orig = FixtureSearch.search

    async def flaky(self, q):
        if "interview" in q.query:          # one of the five gather searches always fails
            raise SearchError("upstream 503", kind="unavailable")
        return await orig(self, q)

    monkeypatch.setattr(FixtureSearch, "search", flaky)
    rid = await start(client, base)
    await jobs.drain()
    d = await get(client, rid)
    gather = next(s for s in d["stages"] if s["key"] == "gather")
    assert d["status"] == "awaiting_review" and gather["status"] == "succeeded"
    assert len(gather["output"]["search_failures"]) == 1


async def test_all_searches_failing_fails_the_stage_visibly(client, base):
    fx = copy.deepcopy(base)
    fx["fail_search"] = True
    rid = await start(client, fx)
    await jobs.drain()
    d = await get(client, rid)
    assert d["status"] == "failed" and "none of the searches answered" in d["error"]


@pytest.mark.parametrize("title,url,shop", [
    ("Buy Peyush Bansal Shark Tank Yellow Eyeglasses", "https://www.lenskart.com/lenskart-air-la-e15417.html", True),
    ("Lenskart Air frames - Add to Cart", "https://x.com/a", True),
    ("Widget", "https://www.amazon.in/dp/B0ABC123", True),
    ("Lenskart launches Spanish brand Meller in India", "https://m.economictimes.com/tech/lenskart-launches", False),
    ("How buyers in India think about eyewear", "https://news.example.com/story", False),
])
def test_shop_pages_are_not_signals(title, url, shop):
    from app.pipeline.stages import is_shop_page
    assert is_shop_page(title, url) is shop


def test_namesake_in_other_role_plus_other_namesakes_is_ambiguous():
    from app.pipeline.routing import CandidateView, decide_identity
    cands = [CandidateView("c1", "Michael F. Brown", "Deloitte", "Senior Manager", "current", "no", 2, True, False),
             CandidateView("c2", "Michael W. Brown", "", "Former CFO", "former", "no", 1, True, False)]
    assert decide_identity(cands, True, False).status == "ambiguous"
    assert decide_identity(cands[:1], True, False).status == "role_conflict"


def test_company_never_mentioned_is_detected_by_code():
    from app.pipeline.identity import _company_mentioned
    src = [{"_full": "Brightwater Capital announced a fund."}]
    assert not _company_mentioned("Brightwater Fasteners GmbH", src)
    assert _company_mentioned("Brightwater Capital Ltd", src)


def test_many_unrelated_namesakes_mean_not_found_not_ambiguous():
    from app.pipeline.routing import CandidateView, decide_identity
    cands = [CandidateView(f"c{i}", "Tomas Lindqvist", co, "Director", "never", "no", 1, True, False)
             for i, co in enumerate(["Qtagg", "Sealvalue", "Ams Pictures"])]
    assert decide_identity(cands, True, False).status == "not_found"
    # a single namesake elsewhere is still offered as "has this person moved?"
    assert decide_identity(cands[:1], True, False).status == "role_conflict"
