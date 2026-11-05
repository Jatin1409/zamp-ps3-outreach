"""Google Gemini implementation of LLMProvider (free tier friendly).

Uses the official `google-genai` SDK, classic `models.generate_content` with a JSON schema response.
Every response is still validated against the same strict Pydantic schema as the Anthropic provider.
Free-tier quotas are small, so calls are spaced out (LLM_MIN_INTERVAL_S) and 429s are retried with backoff.
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


_exhausted_until: dict[str, float] = {}


def _exhausted(model: str) -> bool:
    return _exhausted_until.get(model, 0) > time.monotonic()


def _mark_exhausted(model: str, seconds: float):
    _exhausted_until[model] = time.monotonic() + min(seconds, 86400)


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
        """This tier's models first, then every other configured model as a last resort.

        Seen live (2026-11-05): Google returned 503 'high demand' for 3.8-flash and 3.5-flash-lite at the same time
        while 3.1-flash-lite kept answering - so no tier should be limited to its own short list.
        """
        s = self.s
        draft = [s.gemini_model] + [m.strip() for m in s.gemini_fallbacks.split(",") if m.strip()]
        fast = [s.gemini_model_fast] + [m.strip() for m in s.gemini_fast_fallbacks.split(",") if m.strip()]
        return list(dict.fromkeys((fast + draft) if tier == "fast" else (draft + fast)))

    async def complete(self, *, purpose, system, user, schema, tier="fast", max_tokens=8000) -> LLMResult:
        s, types, errors = self.s, self.types, self.errors
        models = [m for m in self.chain(tier) if not _exhausted(m)]
        if not models:
            raise LLMError("Gemini API error 429: daily free-tier quota used up for every configured model",
                           kind="rate_limited")
        mi = 0
        rounds = 0
        level = s.llm_effort_fast if tier == "fast" else s.llm_effort_draft
        json_schema = inline_schema(schema.model_json_schema())
        prompt = user
        t0 = time.monotonic()
        tin = tout = 0
        last = ""
        schema_tries = 0
        transient_tries = 0
        skipped: list[str] = []
        while True:
            model = models[mi]
            cfg = types.GenerateContentConfig(
                system_instruction=system, max_output_tokens=max(max_tokens, 4096),
                response_mime_type="application/json", response_json_schema=json_schema,
                thinking_config=types.ThinkingConfig(thinking_level=level))
            await self._space_out()
            try:
                resp = await self.client.aio.models.generate_content(model=model, contents=prompt, config=cfg)
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code in (401, 403):
                    raise LLMError("Gemini rejected the API key (check GEMINI_API_KEY)", kind="config") from e
                daily = code == 429 and _is_daily_quota(e)
                if daily or code == 404:
                    # this model can't serve today: drop it from this call's chain
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
                    # temporary (overload / per-minute limit): try the next model now; if the whole chain is
                    # busy, back off and go round again
                    skipped.append(f"{model} ({code})")
                    if mi + 1 < len(models):
                        mi += 1
                        continue
                    rounds += 1
                    if rounds > s.gemini_max_retries:
                        raise LLMError(f"Gemini API error {code}: every configured model was overloaded or "
                                       f"rate-limited across {rounds} attempts - try again in a minute",
                                       kind="rate_limited" if code == 429 else "provider_error") from e
                    delay = _retry_delay(e) if code == 429 else None
                    await asyncio.sleep(min(30.0, delay or 3 * 2 ** rounds) + random.random())
                    mi = 0
                    continue
                raise LLMError(f"Gemini API error {code}: {str(e)[:300]}", kind="provider_error") from e
            except (asyncio.TimeoutError, TimeoutError) as e:
                skipped.append(f"{model} (timeout)")
                if mi + 1 < len(models):
                    mi += 1
                    continue
                rounds += 1
                if rounds > s.gemini_max_retries:
                    raise LLMError("Gemini timed out on every configured model", kind="timeout") from e
                mi = 0
                continue
            except Exception as e:  # network errors from the http layer
                if transient_tries < s.gemini_max_retries and "connect" in type(e).__name__.lower():
                    transient_tries += 1
                    await asyncio.sleep(2 ** transient_tries)
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
