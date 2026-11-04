"""Sequences the stages for a job: start | resume (after a rep decision) | regenerate (from review)."""
from __future__ import annotations

import logging
from dataclasses import asdict

from sqlalchemy import select

from ..db import session, utcnow
from ..models import Run, SearchRecord, Stage
from ..providers.llm import LLMError, LLMProvider, make_llm
from ..providers.search import ReplaySearch, SearchError, SearchProvider, SearchQuery, make_search
from . import stages as S
from .context import STAGES, RunContext, StageFailed, emit, prospect_key, update_run

log = logging.getLogger("pipeline")
TERMINAL = ("completed", "failed")


async def init_stages(run_id: str):
    async with session() as s:
        for i, (k, _) in enumerate(STAGES):
            s.add(Stage(run_id=run_id, key=k, seq=i, status="pending"))
        await s.commit()


async def load_run(run_id: str) -> Run:
    async with session() as s:
        run = await s.get(Run, run_id)
        if run is None:
            raise KeyError(run_id)
        return run


async def replay_records(pkey: str) -> tuple[dict[str, list[dict]], str | None]:
    """Latest live results per query for this prospect."""
    async with session() as s:
        rows = (await s.execute(select(SearchRecord).where(SearchRecord.prospect_key == pkey,
                                                           SearchRecord.provider != "replay")
                                .order_by(SearchRecord.id))).scalars().all()
    recs: dict[str, list[dict]] = {}
    src = None
    for r in rows:
        recs[r.params.get("key") or SearchQuery(r.query).key()] = r.results
        src = r.run_id
    return recs, src


async def build_providers(run: Run, fixture: dict | None) -> tuple[LLMProvider, SearchProvider]:
    llm = make_llm(fixture)
    if run.mode == "replay":
        recs, src = await replay_records(prospect_key(run.prospect["name"], run.prospect["company"]))
        if not recs:
            raise StageFailed("No saved sources exist for this prospect yet - run it once in live mode first.",
                              "replay_empty")
        if src and not run.replay_source_run_id:
            await update_run(run.id, replay_source_run_id=src)
        return llm, ReplaySearch(recs, src)
    return llm, make_search(fixture)


async def fail_run(run_id: str, msg: str):
    await update_run(run_id, status="failed", outcome="failed", error=msg, finished_at=utcnow())
    await emit(run_id, "run_status", {"status": "failed", "error": msg})


async def finish_not_drafted(ctx: RunContext, run_id: str, reason: str, outcome: str = "not_drafted"):
    for k in ("draft", "verify", "generic", "review"):
        await ctx.mark_stage(k, "skipped", "Skipped: " + reason)
    await update_run(run_id, status="completed", outcome=outcome, current_stage=None, finished_at=utcnow())
    await emit(run_id, "run_status", {"status": "completed", "outcome": outcome})


async def pause_for_rep(ctx: RunContext, run_id: str, identity: dict):
    kind = identity["status"]
    edge = "ambiguous_identity" if kind == "ambiguous" else "outdated_role"
    run = await load_run(run_id)
    payload = {"kind": kind, "reasons": identity["reasons"], "candidates": [
        c for c in identity["candidate_details"] if c["id"] in identity.get("candidates", [])],
        "conflict_kind": identity.get("conflict_kind"), "input": run.prospect}
    await ctx.mark_stage("gather", "waiting", "Waiting for your decision on identity before researching.")
    await update_run(run_id, status="awaiting_rep", pause=payload, route_path=list(run.route_path or []) + ["ask_rep"],
                     edge_cases=list(dict.fromkeys(list(run.edge_cases or []) + [edge])))
    await emit(run_id, "run_status", {"status": "awaiting_rep", "kind": kind})


async def run_job(run_id: str, kind: str, payload: dict, fixture: dict | None = None):
    run = await load_run(run_id)
    try:
        try:
            llm, search = await build_providers(run, fixture)
        except (LLMError, SearchError) as e:
            raise StageFailed(f"Provider not available ({e.kind}): {e}", e.kind) from e
    except StageFailed as e:
        ctx = RunContext(run_id, None, None, run.mode)  # type: ignore[arg-type]
        first = "identity" if kind in ("start", "retry") else ("gather" if kind == "resume" else "draft")
        try:
            async with ctx.stage(first) as h:  # record the failure on a stage, visibly
                h.explanation = str(e)
                raise e
        except StageFailed:
            pass
        await fail_run(run_id, str(e))
        return
    ctx = RunContext(run_id, llm, search, run.mode)
    ctx.prospect_key = prospect_key(run.prospect["name"], run.prospect["company"])
    await update_run(run_id, status="running", error=None)
    await emit(run_id, "run_status", {"status": "running"})
    try:
        if kind == "start":
            identity = await S.stage_identity(ctx, run)
            if identity["status"] in ("ambiguous", "role_conflict"):
                await pause_for_rep(ctx, run_id, identity)
                return
            if identity["status"] == "not_found":
                await S.add_warning(run_id, "Person not found in public sources - identity unconfirmed.")
            await _research_and_draft(ctx, run_id)
        elif kind == "resume":
            await _research_and_draft(ctx, run_id)
        elif kind == "regenerate":
            run = await load_run(run_id)
            route = (await _stage_output(run_id, "route")) or {}
            await S.draft_verify_generic(ctx, run, route, origin="regenerate",
                                         instruction=payload.get("instruction"))
            await _await_review(ctx, run_id)
        elif kind == "retry":
            await _retry(ctx, run_id)
    except StageFailed as e:
        await fail_run(run_id, str(e))


async def _stage_output(run_id: str, key: str) -> dict | None:
    async with session() as s:
        row = (await s.execute(select(Stage).where(Stage.run_id == run_id, Stage.key == key,
                                                   Stage.status == "succeeded")
                               .order_by(Stage.attempt.desc()))).scalars().first()
        return row.output if row else None


async def _research_and_draft(ctx: RunContext, run_id: str):
    run = await load_run(run_id)
    if any(d.get("choice") == "stop" for d in run.rep_decisions or []):
        for k in ("gather", "filter", "rank"):
            await ctx.mark_stage(k, "skipped", "Skipped: the rep stopped this run.")
        await S.stage_route(ctx, run)
        await finish_not_drafted(ctx, run_id, "the rep stopped this run.", outcome="stopped_by_rep")
        return
    await S.stage_gather(ctx, run)
    await S.stage_filter(ctx, run)
    await S.stage_rank(ctx, run)
    dec = await S.stage_route(ctx, run)
    if dec.route == "do_not_draft":
        await finish_not_drafted(ctx, run_id, "nothing usable to write about. " + " ".join(dec.missing))
        return
    run = await load_run(run_id)
    await S.draft_verify_generic(ctx, run, asdict(dec))
    await _await_review(ctx, run_id)


async def _await_review(ctx: RunContext, run_id: str):
    await ctx.mark_stage("review", "waiting", "Draft ready. Waiting for the rep to approve, edit, reject or "
                         "regenerate. Nothing is sent automatically.")
    await update_run(run_id, status="awaiting_review", current_stage="review")
    await emit(run_id, "run_status", {"status": "awaiting_review"})


async def _retry(ctx: RunContext, run_id: str):
    """Re-run from the first failed stage."""
    async with session() as s:
        rows = (await s.execute(select(Stage).where(Stage.run_id == run_id))).scalars().all()
    failed = sorted((r for r in rows if r.status == "failed"), key=lambda r: r.seq)
    first = failed[0].key if failed else "identity"
    run = await load_run(run_id)
    if first == "identity":
        identity = await S.stage_identity(ctx, run)
        if identity["status"] in ("ambiguous", "role_conflict") and not run.rep_decisions:
            await pause_for_rep(ctx, run_id, identity)
            return
        await _research_and_draft(ctx, run_id)
    elif first in ("gather", "filter", "rank", "route"):
        await _research_and_draft(ctx, run_id)
    else:
        route = (await _stage_output(run_id, "route")) or {}
        await S.draft_verify_generic(ctx, run, route)
        await _await_review(ctx, run_id)
