"""GeminiLLM behaviour with a fake SDK client (no network)."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from google.genai import errors

from app.config import get_settings
from app.pipeline.llm_schemas import FilterBatch, IdentityExtraction
from app.providers import gemini as G
from app.providers.llm import LLMError


def resp(payload, reason="STOP", pin=100, pout=20, think=5):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return NS(text=text, usage_metadata=NS(prompt_token_count=pin, candidates_token_count=pout,
                                           thoughts_token_count=think),
              candidates=[NS(finish_reason=NS(name=reason))], prompt_feedback=None)


class FakeModels:
    def __init__(self, items):
        self.items, self.calls = list(items), []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def make(monkeypatch, items):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("LLM_MIN_INTERVAL_S", "0")
    get_settings.cache_clear()
    G.reset_model_state()
    llm = G.GeminiLLM()
    fm = FakeModels(items)
    llm.client = NS(aio=NS(models=fm))
    monkeypatch.setattr(G.asyncio, "sleep", _nosleep)
    return llm, fm


async def _nosleep(*_a, **_k):
    return None


OK = {"judgments": [{"sid": "s1", "about": "company", "signal_type": "funding", "sensitive_category": "none",
                     "contains_personal_details": False, "reason": "r"}]}


def test_inline_schema_has_no_refs():
    s = G.inline_schema(IdentityExtraction.model_json_schema())
    txt = json.dumps(s)
    assert "$ref" not in txt and "$defs" not in txt and '"title"' not in txt
    ev = s["properties"]["candidates"]["items"]["properties"]["evidence"]["items"]
    assert ev["additionalProperties"] is False and set(ev["required"]) == {"source", "quote"}


async def test_success_validates_and_counts_tokens(monkeypatch):
    llm, fm = make(monkeypatch, [resp(OK)])
    r = await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch, tier="fast")
    assert isinstance(r.data, FilterBatch) and r.input_tokens == 100 and r.output_tokens == 25
    cfg = fm.calls[0]["config"]
    assert fm.calls[0]["model"] == "gemini-3.5-flash-lite"
    assert cfg.response_mime_type == "application/json" and cfg.system_instruction == "s"
    assert cfg.thinking_config.thinking_level.lower().endswith("low")


async def test_draft_tier_uses_main_model(monkeypatch):
    llm, fm = make(monkeypatch, [resp(OK)])
    await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert fm.calls[0]["model"] == "gemini-3.6-flash"


async def test_429_is_retried_then_succeeds(monkeypatch):
    e = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
                                           "details": [{"retryDelay": "3s"}]}})
    llm, fm = make(monkeypatch, [e, e, resp(OK)])
    r = await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch)
    assert len(fm.calls) == 3 and r.data.judgments[0].sid == "s1"


async def test_429_exhausted_raises_rate_limited(monkeypatch):
    e = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    llm, fm = make(monkeypatch, [e] * 40)
    with pytest.raises(LLMError) as ex:
        await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch)
    assert ex.value.kind == "rate_limited"
    # classification only uses the Flash-Lite models (never the slow, 20/day Flash models)
    assert {c["model"] for c in fm.calls} == {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}


async def test_whole_chain_overloaded_backs_off_and_recovers(monkeypatch):
    # Seen live: 3.8-flash and 3.5-flash-lite both 503; only 3.1-flash-lite answering - and sometimes none.
    G._exhausted_until.clear()
    e503 = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    llm, fm = make(monkeypatch, [e503] * 4 + [resp(OK)])
    r = await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert [c["model"] for c in fm.calls[:4]] == ["gemini-3.6-flash", "gemini-3.5-flash-lite",
                                                  "gemini-3.1-flash-lite", "gemini-3.8-flash"]
    assert fm.calls[4]["model"] == "gemini-3.6-flash"   # second round after backing off
    assert len(r.fallback_from) == 4


async def test_draft_tier_falls_back_to_flash_lite_before_the_slow_flash_model(monkeypatch):
    e503 = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    llm, fm = make(monkeypatch, [e503, resp(OK)])
    r = await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert r.model == "gemini-3.5-flash-lite"


async def test_bad_key_is_config_error(monkeypatch):
    e = errors.ClientError(403, {"error": {"code": 403, "message": "bad key", "status": "PERMISSION_DENIED"}})
    llm, _ = make(monkeypatch, [e])
    with pytest.raises(LLMError) as ex:
        await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch)
    assert ex.value.kind == "config"


async def test_schema_violation_repaired_once(monkeypatch):
    llm, fm = make(monkeypatch, [resp({"judgments": [{"sid": "s1"}]}), resp(OK)])
    r = await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch)
    assert r.attempts == 2 and "failed validation" in fm.calls[1]["contents"]
    assert r.input_tokens == 200  # both attempts counted


async def test_schema_violation_twice_fails(monkeypatch):
    llm, _ = make(monkeypatch, [resp("not json"), resp("{}")])
    with pytest.raises(LLMError) as ex:
        await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch)
    assert ex.value.kind == "schema"


async def test_safety_block_is_refusal(monkeypatch):
    llm, _ = make(monkeypatch, [resp("", reason="SAFETY")])
    with pytest.raises(LLMError) as ex:
        await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch)
    assert ex.value.kind == "refusal"


def test_missing_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(LLMError):
        G.GeminiLLM()


def quota_err(daily=True, delay="3600s"):
    qid = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
                                              "details": [{"violations": [{"quotaId": qid, "quotaValue": "20"}]},
                                                          {"retryDelay": delay}]}})


async def test_daily_quota_falls_back_to_next_model_without_waiting(monkeypatch):
    G._exhausted_until.clear()
    llm, fm = make(monkeypatch, [quota_err(), resp(OK)])
    r = await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert [c["model"] for c in fm.calls] == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    assert r.model == "gemini-3.5-flash-lite" and "daily quota" in r.fallback_from[0]
    # remembered: the next call skips the exhausted model entirely
    fm.items.append(resp(OK))
    await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert fm.calls[-1]["model"] == "gemini-3.5-flash-lite"
    G._exhausted_until.clear()


async def test_overloaded_model_falls_back(monkeypatch):
    G._exhausted_until.clear()
    e503 = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    llm, fm = make(monkeypatch, [e503, resp(OK)])
    r = await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch, tier="fast")
    assert r.model == "gemini-3.1-flash-lite"


async def test_all_models_exhausted_fails_clearly(monkeypatch):
    G._exhausted_until.clear()
    llm, _ = make(monkeypatch, [quota_err()] * 5)
    with pytest.raises(LLMError) as ex:
        await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert ex.value.kind == "rate_limited" and "every configured model" in str(ex.value)
    G._exhausted_until.clear()


async def test_circuit_breaker_skips_recently_overloaded_model_on_next_call(monkeypatch):
    e503 = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    llm, fm = make(monkeypatch, [e503, resp(OK), resp(OK)])
    await llm.complete(purpose="draft", system="s", user="u", schema=FilterBatch, tier="draft")
    assert [c["model"] for c in fm.calls] == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    # the next call goes straight to a healthy model - no time wasted rediscovering the overloaded one
    await llm.complete(purpose="verify", system="s", user="u", schema=FilterBatch, tier="draft")
    assert fm.calls[-1]["model"] == "gemini-3.5-flash-lite"
    assert "gemini-3.6-flash" in G.model_health()["cooling"]


_real_sleep = asyncio.sleep  # make() swaps asyncio.sleep for a no-op to keep backoff tests fast


class SlowModels(FakeModels):
    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if model == "gemini-3.5-flash-lite":
            await _real_sleep(5)            # hangs
        return resp(OK)


async def test_hanging_model_is_abandoned_for_the_next_one(monkeypatch):
    llm, _ = make(monkeypatch, [])
    monkeypatch.setattr(get_settings(), "llm_attempt_timeout_fast_s", 0.3)
    llm.client = NS(aio=NS(models=SlowModels([])))
    t0 = asyncio.get_running_loop().time()
    r = await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch, tier="fast")
    assert r.model == "gemini-3.1-flash-lite" and asyncio.get_running_loop().time() - t0 < 2


async def test_call_respects_the_overall_deadline(monkeypatch):
    llm, _ = make(monkeypatch, [])
    llm.client = NS(aio=NS(models=SlowModels([])))
    monkeypatch.setattr(get_settings(), "gemini_fast_fallbacks", "")
    monkeypatch.setattr(get_settings(), "gemini_model", "gemini-3.5-flash-lite")
    monkeypatch.setattr(get_settings(), "gemini_fallbacks", "")
    with pytest.raises(LLMError) as ex:
        await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch, tier="fast", timeout=1.0)
    assert ex.value.kind == "timeout"


class StallFirst(FakeModels):
    """The first model stalls for 3 s; any other model answers at once."""
    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if model == "gemini-3.5-flash-lite":
            await _real_sleep(3)
        return resp(OK)


async def test_hedged_request_takes_the_faster_model(monkeypatch):
    llm, _ = make(monkeypatch, [])
    monkeypatch.setattr(get_settings(), "llm_hedge_after_fast_s", 0.2)
    fm = StallFirst([])
    llm.client = NS(aio=NS(models=fm))
    t0 = asyncio.get_running_loop().time()
    r = await llm.complete(purpose="filter", system="s", user="u", schema=FilterBatch, tier="fast")
    took = asyncio.get_running_loop().time() - t0
    assert r.model == "gemini-3.1-flash-lite" and took < 1.0       # did not wait for the stalled model
    assert [c["model"] for c in fm.calls] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    assert "parallel request" in r.fallback_from[0]
