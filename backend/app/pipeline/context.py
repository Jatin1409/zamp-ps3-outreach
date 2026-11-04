"""RunContext: the only way stages touch the outside world. It persists and streams everything."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select, update

from ..config import get_settings, llm_cost
from ..db import session, utcnow
from ..models import Event, LLMCall, Run, SearchRecord, Stage
from ..providers.llm import LLMError, LLMProvider, LLMResult
from ..providers.search import SearchError, SearchProvider, SearchQuery, SearchResult
from .textmatch import normalize

STAGES: list[tuple[str, str]] = [
    ("identity", "Resolve identity"),
    ("gather", "Gather signals"),
    ("filter", "Filter"),
    ("rank", "Rank signals"),
    ("route", "Choose route"),
    ("draft", "Write draft"),
    ("verify", "Verify claims"),
    ("generic", "Genericness check"),
    ("review", "Human review"),
]
STAGE_SEQ = {k: i for i, (k, _) in enumerate(STAGES)}


def prospect_key(name: str, company: str) -> str:
    return f"{normalize(name)}|{normalize(company)}"


class StageFailed(Exception):
    def __init__(self, message: str, kind: str = "error", details: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.details = details or {}


async def emit(run_id: str, type_: str, data: dict | None = None):
    async with session() as s:
        s.add(Event(run_id=run_id, type=type_, data=data or {}))
        await s.commit()


async def update_run(run_id: str, **fields):
    async with session() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(updated_at=utcnow(), **fields))
        await s.commit()


async def add_warning(run_id: str, text: str):
    async with session() as s:
        run = await s.get(Run, run_id)
        if run and text not in (run.warnings or []):
            run.warnings = list(run.warnings or []) + [text]
            await s.commit()


class StageHandle:
    def __init__(self, ctx: "RunContext", row_id: int, key: str):
        self.ctx, self.row_id, self.key = ctx, row_id, key
        self.output: dict[str, Any] = {}
        self.meta: dict[str, Any] = {}  # provider metadata, merged into output when the stage finishes
        self.explanation: str = ""
        self.status = "succeeded"
        self.tokens_in = self.tokens_out = 0
        self.cost = 0.0

    async def progress(self, message: str):
        await emit(self.ctx.run_id, "stage_progress", {"stage": self.key, "message": message})


class RunContext:
    def __init__(self, run_id: str, llm: LLMProvider, search: SearchProvider, mode: str):
        self.run_id, self.llm, self.search, self.mode = run_id, llm, search, mode
        self.prospect_key: str = ""

    # ------------------------------------------------------------------ stages
    @asynccontextmanager
    async def stage(self, key: str, input_: dict | None = None):
        async with session() as s:
            last = (await s.execute(select(func.max(Stage.attempt)).where(
                Stage.run_id == self.run_id, Stage.key == key, Stage.status != "pending"))).scalar()
            pending = (await s.execute(select(Stage).where(
                Stage.run_id == self.run_id, Stage.key == key, Stage.status == "pending"))).scalars().first()
            row = pending or Stage(run_id=self.run_id, key=key, seq=STAGE_SEQ[key])
            row.attempt = (last or 0) + 1
            row.status, row.started_at, row.input = "running", utcnow(), input_ or {}
            row.output, row.error, row.finished_at, row.duration_ms = None, None, None, None
            row.tokens_in = row.tokens_out = 0
            row.cost_usd = 0.0
            s.add(row)
            await s.commit()
            row_id, attempt = row.id, row.attempt
        await update_run(self.run_id, current_stage=key)
        await emit(self.run_id, "stage_started", {"stage": key, "attempt": attempt})
        h = StageHandle(self, row_id, key)
        t0 = time.monotonic()
        try:
            yield h
        except StageFailed as e:
            await self._finish(h, t0, "failed", error=f"{e}", extra={"error_kind": e.kind, **e.details})
            raise
        except Exception as e:  # unexpected bug: still never looks like success
            await self._finish(h, t0, "failed", error=f"Unexpected error: {type(e).__name__}: {e}",
                               extra={"error_kind": "internal"})
            raise StageFailed(f"Unexpected error in {key}: {e}", "internal") from e
        else:
            await self._finish(h, t0, h.status)

    async def _finish(self, h: StageHandle, t0: float, status: str, error: str | None = None,
                      extra: dict | None = None):
        ms = int((time.monotonic() - t0) * 1000)
        out = dict(h.output)
        out.update(h.meta)
        if extra:
            out.update(extra)
        async with session() as s:
            row = await s.get(Stage, h.row_id)
            row.status, row.finished_at, row.duration_ms = status, utcnow(), ms
            row.output, row.explanation, row.error = out, h.explanation or row.explanation, error
            row.tokens_in, row.tokens_out, row.cost_usd = h.tokens_in, h.tokens_out, round(h.cost, 6)
            run = await s.get(Run, self.run_id)
            if status != "waiting":
                run.active_ms = (run.active_ms or 0) + ms
            await s.commit()
        ev = "stage_failed" if status == "failed" else "stage_completed"
        await emit(self.run_id, ev, {"stage": h.key, "status": status, "duration_ms": ms,
                                     "explanation": h.explanation, "error": error})

    async def mark_stage(self, key: str, status: str, explanation: str, output: dict | None = None):
        """Set a stage that does no work (skipped / waiting) without timing it."""
        async with session() as s:
            row = (await s.execute(select(Stage).where(Stage.run_id == self.run_id, Stage.key == key)
                                   .order_by(Stage.attempt.desc()))).scalars().first()
            if row is None or row.status not in ("pending", "waiting"):
                row = Stage(run_id=self.run_id, key=key, seq=STAGE_SEQ[key],
                            attempt=(row.attempt + 1) if row else 1)
                s.add(row)
            row.status, row.explanation = status, explanation
            if status == "waiting":
                row.started_at = row.started_at or utcnow()
            else:
                row.finished_at = utcnow()
            if output is not None:
                row.output = output
            await s.commit()
        await emit(self.run_id, "stage_completed", {"stage": key, "status": status, "explanation": explanation})

    # ------------------------------------------------------------------ providers
    async def llm_call(self, h: StageHandle, *, purpose: str, system: str, user: str, schema, tier="fast",
                       max_tokens: int = 8000) -> LLMResult:
        t0 = time.monotonic()
        try:
            res = await self.llm.complete(purpose=purpose, system=system, user=user, schema=schema, tier=tier,
                                          max_tokens=max_tokens)
        except LLMError as e:
            async with session() as s:
                s.add(LLMCall(run_id=self.run_id, stage=h.key, purpose=purpose, model="?", ok=False,
                              error=f"{e.kind}: {e}", latency_ms=int((time.monotonic() - t0) * 1000)))
                await s.commit()
            raise StageFailed(f"Language model call failed ({e.kind}): {e}", kind=f"llm_{e.kind}") from e
        cost = llm_cost(res.model, res.input_tokens, res.output_tokens)
        h.meta.setdefault("models_used", [])
        if res.model not in h.meta["models_used"]:
            h.meta["models_used"].append(res.model)
        skipped = getattr(res, "fallback_from", None)
        if skipped:
            h.meta.setdefault("model_fallbacks", []).append({"purpose": purpose, "skipped": skipped,
                                                                "used": res.model})
            await h.progress(f"Primary model unavailable ({'; '.join(skipped)}) - used {res.model}")
        h.tokens_in += res.input_tokens
        h.tokens_out += res.output_tokens
        h.cost += cost
        async with session() as s:
            s.add(LLMCall(run_id=self.run_id, stage=h.key, purpose=purpose, model=res.model,
                          input_tokens=res.input_tokens, output_tokens=res.output_tokens, cost_usd=cost,
                          latency_ms=res.latency_ms, ok=True, response=res.raw))
            run = await s.get(Run, self.run_id)
            run.tokens_in += res.input_tokens
            run.tokens_out += res.output_tokens
            run.llm_calls += 1
            run.cost_usd = round(run.cost_usd + cost, 6)
            await s.commit()
        return res

    async def search_call(self, h: StageHandle, q: SearchQuery) -> list[SearchResult]:
        try:
            results = await self.search.search(q)
        except SearchError as e:
            raise StageFailed(f"Web search failed ({e.kind}): {e}", kind=f"search_{e.kind}") from e
        live = self.mode == "live"
        async with session() as s:
            s.add(SearchRecord(run_id=self.run_id, prospect_key=self.prospect_key, query=q.query,
                               params={"key": q.key(), "topic": q.topic, "time_range": q.time_range,
                                       "mode": self.mode},
                               results=[r.to_dict() for r in results], provider=self.search.name))
            run = await s.get(Run, self.run_id)
            run.search_calls += 1
            per_call = get_settings().search_cost_per_call_usd
            if live and self.search.name == "tavily" and per_call:
                run.cost_usd = round(run.cost_usd + per_call, 6)
                h.cost += per_call
            await s.commit()
        return results
