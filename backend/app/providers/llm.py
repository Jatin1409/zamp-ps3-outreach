"""LLM provider interface + Anthropic implementation + fixture implementation (tests / replay of recordings).

The pipeline only ever calls `LLMProvider.complete(...)` with a Pydantic model as the output schema, so a
provider can be swapped without touching pipeline code.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import get_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Raised when the provider fails after retries. `kind` is shown to the user."""

    def __init__(self, message: str, kind: str = "provider_error"):
        super().__init__(message)
        self.kind = kind


@dataclass
class LLMResult:
    data: BaseModel
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    attempts: int = 1
    raw: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    async def complete(self, *, purpose: str, system: str, user: str, schema: type[T],
                       tier: str = "fast", max_tokens: int = 8000) -> LLMResult: ...


# ---------------------------------------------------------------------------
class AnthropicLLM:
    name = "anthropic"

    def __init__(self):
        import anthropic

        s = get_settings()
        if not s.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set", kind="config")
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, timeout=s.llm_timeout_s,
                                               max_retries=s.provider_retries)
        self.settings = s

    async def complete(self, *, purpose, system, user, schema, tier="fast", max_tokens=8000) -> LLMResult:
        s = self.settings
        model = s.llm_model_fast if tier == "fast" else s.llm_model
        effort = s.llm_effort_fast if tier == "fast" else s.llm_effort_draft
        a = self._anthropic
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        t0 = time.monotonic()
        tokens_in = tokens_out = 0
        last_err: str = ""
        for attempt in range(1, 3):  # one schema-repair retry on top of SDK transport retries
            try:
                resp = await self.client.messages.parse(
                    model=model, max_tokens=max_tokens, system=system, messages=messages,
                    output_format=schema, output_config={"effort": effort},
                )
            except a.APITimeoutError as e:
                raise LLMError(f"LLM timed out after retries ({e})", kind="timeout") from e
            except a.RateLimitError as e:
                raise LLMError("LLM rate-limited after retries", kind="rate_limited") from e
            except a.AuthenticationError as e:
                raise LLMError("LLM authentication failed - check ANTHROPIC_API_KEY", kind="config") from e
            except a.APIStatusError as e:
                raise LLMError(f"LLM API error {e.status_code}: {e.message}", kind="provider_error") from e
            except a.APIConnectionError as e:
                raise LLMError(f"Could not reach the LLM API ({e})", kind="network") from e
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_err = str(e)[:800]
                messages = [{"role": "user", "content": user + "\n\nYour previous answer failed schema "
                             f"validation: {last_err}\nReturn JSON that matches the schema exactly."}]
                continue
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            if resp.stop_reason == "refusal":
                raise LLMError("The model declined this request", kind="refusal")
            if resp.stop_reason == "max_tokens":
                last_err = "output truncated (max_tokens)"
                max_tokens = min(max_tokens * 2, 32000)
                continue
            parsed = resp.parsed_output
            if parsed is None:
                last_err = "no parsed output"
                continue
            return LLMResult(data=parsed, model=model, input_tokens=tokens_in, output_tokens=tokens_out,
                             latency_ms=int((time.monotonic() - t0) * 1000), attempts=attempt,
                             raw=parsed.model_dump(mode="json"))
        raise LLMError(f"LLM output failed schema validation twice: {last_err}", kind="schema")


# ---------------------------------------------------------------------------
class FixtureLLM:
    """Plays back recorded responses, keyed by `purpose`, in call order.

    A fixture file is {"llm": {"<purpose>": [<response json>, ...]}, "search": {...}}.
    Responses are validated against the same schema as live ones, so a stale fixture fails loudly.
    """

    name = "fixture"

    def __init__(self, fixture: dict):
        self.responses: dict[str, list[dict]] = {k: list(v) for k, v in fixture.get("llm", {}).items()}
        self.calls: list[str] = []
        self.fail_purposes: set[str] = set(fixture.get("fail_llm", []))

    async def complete(self, *, purpose, system, user, schema, tier="fast", max_tokens=8000) -> LLMResult:
        await asyncio.sleep(0)
        self.calls.append(purpose)
        if purpose in self.fail_purposes:
            raise LLMError(f"Simulated provider failure for {purpose}", kind="provider_error")
        queue = self.responses.get(purpose)
        if not queue:
            raise LLMError(f"No recorded LLM response for purpose '{purpose}'", kind="fixture_missing")
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        data = schema.model_validate(payload)
        return LLMResult(data=data, model="fixture", input_tokens=len(user) // 4,
                         output_tokens=len(json.dumps(payload)) // 4, latency_ms=1, raw=payload)


def load_fixture(name: str) -> dict:
    path = Path(get_settings().fixtures_dir) / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def make_llm(fixture: dict | None = None) -> LLMProvider:
    provider = get_settings().llm_provider
    if fixture is not None or provider == "fixture":
        return FixtureLLM(fixture or {})
    if provider == "gemini":
        from .gemini import GeminiLLM

        return GeminiLLM()
    return AnthropicLLM()
