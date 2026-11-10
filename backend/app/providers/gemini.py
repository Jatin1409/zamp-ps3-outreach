"""Google Gemini implementation of LLMProvider (free tier friendly).

Uses the official `google-genai` SDK, classic `models.generate_content` with a JSON schema response.
Every response is still validated against the same strict Pydantic schema as the Anthropic provider.

Reliability / latency design (each point came from measuring real runs):
- Each tier has an ordered model chain; any configured model is a last resort for any tier.
- **Circuit breaker:** a model that returns 503 or times out is skipped by *every* call for MODEL_COOLDOWN_S.
  Before this, each call rediscovered the same overloaded model with backoff sleeps - 50-100 s per call.
- A model whose free daily quota is used up is skipped until its quota resets.
- Each attempt has its own timeout, and the whole call respects the deadline handed down from the run's 90 s
  budget; no sleep or attempt is allowed to run past it.
"""
from __future__ import annotations

import asyncio
import copy
import json
import random
import re
import time

from pydantic import ValidationError

from ..config import get_settings
from .llm import LLMError, LLMResult

_lock = asyncio.Lock()
_last_call = 0.0


def inline_schema(schema: dict) -> dict:
    """Resolve $defs/$ref and drop keys that only add noise ('title', 'default')."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(copy.deepcopy(defs[name]))
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title", "default")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


# model -> monotonic time until which it should not be tried
_exhausted_until: dict[str, float] = {}   # daily quota used up
_cooling_until: dict[str, float] = {}     # overloaded (503) / timed out recently


def _exhausted(model: str) -> bool:
    return _exhausted_until.get(model, 0) > time.monotonic()


def _cooling(model: str) -> bool:
    return _cooling_until.get(model, 0) > time.monotonic()


def _mark_exhausted(model: str, seconds: float):
    _exhausted_until[model] = time.monotonic() + min(seconds, 86400)


def _mark_cooling(model: str, seconds: float):
    _cooling_until[model] = time.monotonic() + seconds


def reset_model_state():
    _exhausted_until.clear()
    _cooling_until.clear()


def model_health() -> dict:
    now = time.monotonic()
    return {"exhausted": {m: round(t - now) for m, t in _exhausted_until.items() if t > now},
            "cooling": {m: round(t - now) for m, t in _cooling_until.items() if t > now}}


def _is_daily_quota(err) -> bool:
    try:
        for d in (err.details or {}).get("error", {}).get("details", []):
            for v in d.get("violations", []) or []:
                if "PerDay" in str(v.get("quotaId", "")):
                    return True
    except (AttributeError, TypeError):
        pass
    return "PerDay" in str(err)


def _retry_delay(err) -> float | None:
    """Gemini 429s carry a RetryInfo detail like '27s'."""
    try:
        for d in (err.details or {}).get("error", {}).get("details", []):
            if "retryDelay" in d:
                return float(str(d["retryDelay"]).rstrip("s"))
    except (AttributeError, TypeError, ValueError):
        pass
    m = re.search(r"retry in ([\d.]+)s", str(err))
    return float(m.group(1)) if m else None


class GeminiLLM:
    name = "gemini"

    def __init__(self):
        from google import genai
        from google.genai import errors, types

        s = get_settings()
        if not s.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set", kind="config")
        self.s, self.types, self.errors = s, types, errors
        self.client = genai.Client(api_key=s.gemini_api_key,
                                   http_options=types.HttpOptions(timeout=int(s.llm_timeout_s * 1000)))

    async def _space_out(self):
        global _last_call
        async with _lock:
            wait = self.s.llm_min_interval_s - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_call = time.monotonic()

    def chain(self, tier: str) -> list[str]:
        """This tier's models first, then every other configured model as a last resort."""
        s = self.s
        draft = [s.gemini_model] + [m.strip() for m in s.gemini_fallbacks.split(",") if m.strip()]
        fast = [s.gemini_model_fast] + [m.strip() for m in s.gemini_fast_fallbacks.split(",") if m.strip()]
        return list(dict.fromkeys((fast + draft) if tier == "fast" else (draft + fast)))

    def usable_chain(self, tier: str) -> list[str]:
        """Skip exhausted models; put cooling (recently overloaded) models last instead of dropping them."""
        live = [m for m in self.chain(tier) if not _exhausted(m)]
        return [m for m in live if not _cooling(m)] + sorted(
            (m for m in live if _cooling(m)), key=lambda m: _cooling_until.get(m, 0))

    async def complete(self, *, purpose, system, user, schema, tier="fast", max_tokens=8000,
                       timeout=None) -> LLMResult:
        s, types, errors = self.s, self.types, self.errors
        models = self.usable_chain(tier)
        if not models:
            raise LLMError("Gemini API error 429: daily free-tier quota used up for every configured model",
                           kind="rate_limited")
        t0 = time.monotonic()
        deadline = t0 + (timeout if timeout else s.llm_timeout_s)
        per_attempt = s.llm_attempt_timeout_fast_s if tier == "fast" else s.llm_attempt_timeout_draft_s
        level = s.llm_effort_draft if tier == "draft" else s.llm_effort_fast
        json_schema = inline_schema(schema.model_json_schema())
        prompt = user
        tin = tout = 0
        last = ""
        schema_tries = 0
        mi = 0
        rounds = 0
        skipped: list[str] = []

        def left() -> float:
            return deadline - time.monotonic()

        while True:
            if left() < 1.5:
                raise LLMError(f"No model answered within the time available ({timeout or s.llm_timeout_s:.0f} s)"
                               + (f"; tried {', '.join(skipped)}" if skipped else ""), kind="timeout")
            model = models[mi]
            cfg = types.GenerateContentConfig(
                system_instruction=system, max_output_tokens=max(max_tokens, 4096),
                response_mime_type="application/json", response_json_schema=json_schema,
                thinking_config=types.ThinkingConfig(thinking_level=level))
            await self._space_out()
            try:
                resp = await asyncio.wait_for(
                    self.client.aio.models.generate_content(model=model, contents=prompt, config=cfg),
                    timeout=max(1.0, min(per_attempt, left())))
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code in (401, 403):
                    raise LLMError("Gemini rejected the API key (check GEMINI_API_KEY)", kind="config") from e
                daily = code == 429 and _is_daily_quota(e)
                if daily or code == 404:
                    if daily:
                        _mark_exhausted(model, _retry_delay(e) or 3600)
                    skipped.append(f"{model} ({'daily quota used up' if daily else 'not found'})")
                    models.pop(mi)
                    if not models:
                        msg = ("daily free-tier quota used up for every configured model" if daily
                               else f"model '{model}' not found")
                        raise LLMError(f"Gemini API error {code}: {msg}",
                                       kind="rate_limited" if daily else "config") from e
                    mi = mi % len(models)
                    continue
                if code in (429, 500, 502, 503, 504):
                    if code != 429:
                        _mark_cooling(model, s.model_cooldown_s)
                    skipped.append(f"{model} ({code})")
                    if mi + 1 < len(models):
                        mi += 1
                        continue
                    rounds += 1
                    if rounds > s.gemini_max_retries:
                        raise LLMError(f"Gemini API error {code}: every configured model was overloaded or "
                                       f"rate-limited across {rounds} attempts - try again in a minute",
                                       kind="rate_limited" if code == 429 else "provider_error") from e
                    delay = (_retry_delay(e) if code == 429 else None) or 2 * rounds
                    await asyncio.sleep(max(0.0, min(delay + random.random(), left() - 2)))
                    mi = 0
                    continue
                raise LLMError(f"Gemini API error {code}: {str(e)[:300]}", kind="provider_error") from e
            except (asyncio.TimeoutError, TimeoutError) as e:
                _mark_cooling(model, s.model_cooldown_s)
                skipped.append(f"{model} (no answer in {min(per_attempt, left()):.0f} s)")
                if mi + 1 < len(models):
                    mi += 1
                    continue
                rounds += 1
                if rounds > s.gemini_max_retries:
                    raise LLMError("Gemini timed out on every configured model", kind="timeout") from e
                mi = 0
                continue
            except Exception as e:  # network errors from the http layer
                if "connect" in type(e).__name__.lower() and mi + 1 < len(models):
                    skipped.append(f"{model} (network)")
                    mi += 1
                    continue
                raise LLMError(f"Could not reach Gemini ({type(e).__name__}: {e})", kind="network") from e

            u = resp.usage_metadata
            if u is not None:
                tin += u.prompt_token_count or 0
                tout += (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
            cand = (resp.candidates or [None])[0]
            reason = getattr(getattr(cand, "finish_reason", None), "name", None)
            if resp.prompt_feedback and getattr(resp.prompt_feedback, "block_reason", None):
                raise LLMError(f"Gemini blocked the prompt ({resp.prompt_feedback.block_reason})", kind="refusal")
            if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
                raise LLMError(f"Gemini declined to answer ({reason})", kind="refusal")
            text = resp.text or ""
            try:
                if reason == "MAX_TOKENS":
                    raise ValueError("output truncated (max tokens)")
                data = schema.model_validate_json(text)
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                schema_tries += 1
                last = str(e)[:800]
                if schema_tries >= 2:
                    raise LLMError(f"Gemini output failed schema validation twice: {last}", kind="schema") from e
                if reason == "MAX_TOKENS":
                    max_tokens = min(max_tokens * 2, 32000)
                prompt = (user + "\n\nYour previous answer failed validation: " + last
                          + "\nReturn JSON that matches the schema exactly.")
                continue
            res = LLMResult(data=data, model=model, input_tokens=tin, output_tokens=tout,
                            latency_ms=int((time.monotonic() - t0) * 1000), attempts=schema_tries + 1,
                            raw=data.model_dump(mode="json"))
            res.fallback_from = skipped  # type: ignore[attr-defined]
            return res
