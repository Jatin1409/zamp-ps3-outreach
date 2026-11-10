"""Sequences the stages for a job: start | resume (after a rep decision) | regenerate | retry.

Every job runs under the run's time budget (RUN_BUDGET_S, default 90 s of active time; time spent paused for the
rep doesn't count). A user Stop or the budget timer interrupts the job through RunContext.interrupt(), which cancels
the job's asyncio task - in-flight HTTP calls to the search and model providers are aborted with it - and
`_finalize_interrupted` then leaves the run in a consistent state: the running stage is marked cancelled/timed out,
stages that never ran are marked skipped, and everything produced so far is kept.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from sqlalchemy import delete, select

from ..config import get_settings
from ..db import session, utcnow
from ..models import Draft, Run, SearchRecord, Stage
from ..providers.llm import LLMError, LLMProvider, make_llm
from ..providers.search import ReplaySearch, SearchError, SearchProvider, SearchQuery, make_search
from . import stages as S
from .context import INTERRUPT_STATUS, STAGES, RunContext, StageFailed, emit, prospect_key, update_run

log = logging.getLogger("pipeline")
TERMINAL = ("completed", "failed", "cancelled", "timed_out")
STAGE_LABEL = dict(STAGES)
RUNNING: dict[str, RunContext] = {}   # run_id -> context of the job executing it in this process


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
    for k in ("draft", "generic", "review"):
        await ctx.mark_stage(k, "skipped", "Skipped: " + reason)
    await update_run(run_id, status="completed", outcome=outcome, current_stage=None, finished_at=utcnow())
    await emit(run_id, "run_status", {"status": "completed", "outcome": outcome})


async def pause_for_rep(ctx: RunContext, run_id: str, identity: dict, kind: str | None = None):
    kind = kind or identity["status"]
    run = await load_run(run_id)
    if kind == "ambiguous_company":
        comp = identity.get("company") or {}
        cands = [c for c in comp.get("details", []) if c["id"] in comp.get("candidates", [])]
        reasons = comp.get("reasons", [])
        edge = "ambiguous_company"
    else:
        cands = [c for c in identity["candidate_details"] if c["id"] in identity.get("candidates", [])]
        reasons = identity["reasons"]
        edge = "ambiguous_identity" if kind == "ambiguous" else "outdated_role"
    payload = {"kind": kind, "reasons": reasons, "candidates": cands,
               "conflict_kind": identity.get("conflict_kind"), "input": run.prospect}
    await ctx.mark_stage("gather", "waiting", "Waiting for your decision on identity before researching.")
    await update_run(run_id, status="awaiting_rep", pause=payload, route_path=list(run.route_path or []) + ["ask_rep"],
                     edge_cases=list(dict.fromkeys(list(run.edge_cases or []) + [edge])))
    await emit(run_id, "run_status", {"status": "awaiting_rep", "kind": kind})


async def stop_after_identity(ctx: RunContext, run_id: str, identity: dict):
    """Fictional entity, or neither person nor company exists publicly: say so plainly, invent nothing."""
    st = identity["status"]
    for k in ("gather", "filter", "rank"):
        await ctx.mark_stage(k, "skipped", "Skipped: " + ("this is a fictional entity." if st == "fictional"
                                                          else "nothing to research."))
    run = await load_run(run_id)
    p = run.prospect
    if st == "fictional":
        f = identity.get("fiction") or {}
        names = " and ".join(x["name"] for x in (f.get("person"), f.get("company")) if x)
        reason = (f"{names or p['name']} is fictional - not a real prospect. Nothing is drafted; the facts found are "
                  "shown as fiction, separately from the real world.")
        await S.stage_route_fixed(ctx, run, "R0", reason, [f.get("real_world", "")], edge="fictional_entity")
        await finish_not_drafted(ctx, run_id, "fictional entity.")
    else:
        reason = (f"Neither {p['name']} nor {p['company']} could be found in public sources. Recommend not sending "
                  "until you can confirm who and where they are.")
        missing = [f"No public source mentions a person called {p['name']} at {p['company']}.",
                   f"No public source describes a company called {p['company']}.",
                   "Check the spelling, add the company website or a LinkedIn post in the notes, and run again."]
        await S.stage_route_fixed(ctx, run, "R0", reason, missing, edge="company_not_found")
        await finish_not_drafted(ctx, run_id, "nothing found to research.")


def budget_for(run: Run, kind: str) -> float:
    """start: full budget; resume: what's left after the identity stage; regenerate/retry: a fresh budget
    (each is a separate execution the rep asked for)."""
    full = get_settings().run_budget_s
    if kind == "resume":
        return max(5.0, full - (run.active_ms or 0) / 1000)
    return full


async def run_job(run_id: str, kind: str, payload: dict, fixture: dict | None = None):
    run = await load_run(run_id)
    if run.cancel_requested or run.status in ("cancelled",):
        return  # stopped while queued
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
    ctx = RunContext(run_id, llm, search, run.mode, budget_s=budget_for(run, kind))
    ctx.prospect_key = prospect_key(run.prospect["name"], run.prospect["company"])
    ctx.task = asyncio.current_task()
    async with session() as s:
        versions = (await s.execute(select(Draft.version).where(Draft.run_id == run_id))).scalars().all()
    draft_version_at_start = max(versions) if versions else 0
    RUNNING[run_id] = ctx
    timer = asyncio.get_running_loop().call_later(max(0.05, ctx.remaining()), ctx.interrupt, "timeout")
    t0 = time.monotonic()
    await update_run(run_id, status="running", error=None, interrupted=None)
    await emit(run_id, "run_status", {"status": "running", "budget_s": round(ctx.remaining(), 1)})
    try:
        if kind == "start":
            identity = await S.stage_identity(ctx, run)
            st = identity["status"]
            if st in ("ambiguous", "role_conflict", "ambiguous_company"):
                await pause_for_rep(ctx, run_id, identity)
                return
            if st in ("fictional", "nothing_found"):
                await stop_after_identity(ctx, run_id, identity)
                return
            if st == "not_found":
                if (identity.get("company") or {}).get("status") == "not_found":
                    await S.add_warning(run_id, "Neither the person nor the company was found in public sources - "
                                                "the draft is based only on your notes.")
                else:
                    await S.add_warning(run_id, "Person not found in public sources - nothing about them is stated "
                                                "as fact; company-level research only.")
            await _research_and_draft(ctx, run_id)
        elif kind == "resume":
            run = await load_run(run_id)
            ident = run.identity or {}
            decided = {d.get("kind") for d in run.rep_decisions or []}
            ps = ident.get("person_status")
            stopped = any(d.get("choice") == "stop" for d in run.rep_decisions or [])
            if not stopped and ps in ("ambiguous", "role_conflict") and ps not in decided:
                # the company was ambiguous first; now ask about the person
                await pause_for_rep(ctx, run_id, ident, kind=ps)
                return
            await _research_and_draft(ctx, run_id)
        elif kind == "regenerate":
            run = await load_run(run_id)
            route = (await _stage_output(run_id, "route")) or {}
            await S.draft_and_check(ctx, run, route, origin="regenerate",
                                         instruction=payload.get("instruction"))
            await _await_review(ctx, run_id)
        elif kind == "retry":
            await _retry(ctx, run_id)
    except asyncio.CancelledError:
        if ctx.cancel_reason is None:
            raise  # server shutdown, not a stop/timeout: boot-time recovery marks the run interrupted
        task = asyncio.current_task()
        if task is not None and hasattr(task, "uncancel"):
            task.uncancel()
        await _finalize_interrupted(ctx, run_id, kind, draft_version_at_start)
    except StageFailed as e:
        await fail_run(run_id, str(e))
    finally:
        timer.cancel()
        RUNNING.pop(run_id, None)
        elapsed = time.monotonic() - t0
        log.info("run %s job=%s finished in %.1f s (budget %.0f s, interrupted=%s, search cache hits=%d)",
                 run_id, kind, elapsed, get_settings().run_budget_s, ctx.cancel_reason, ctx.search_cache_hits)


async def _finalize_interrupted(ctx: RunContext, run_id: str, kind: str, draft_version_at_start: int):
    reason = ctx.cancel_reason or "user"
    status_word = INTERRUPT_STATUS[reason]
    stage_label = STAGE_LABEL.get(ctx.current_stage or "", "the run")
    budget = get_settings().run_budget_s
    stopped_msg = (f"Stopped by you during “{stage_label}”." if reason == "user"
                   else f"Reached the {budget:.0f}-second time limit during “{stage_label}”.")
    not_run = "Not run: stopped by you." if reason == "user" else f"Not run: {budget:.0f}-second time limit reached."

    async with session() as s:
        rows = (await s.execute(select(Stage).where(Stage.run_id == run_id))).scalars().all()
        for r in rows:
            if r.status == "running":       # interrupted between DB writes: never leave it 'running'
                r.status, r.finished_at, r.error = status_word, utcnow(), stopped_msg
            elif r.status in ("pending", "waiting"):
                r.status, r.explanation, r.finished_at = "skipped", not_run, utcnow()
        drafts = (await s.execute(select(Draft).where(Draft.run_id == run_id).order_by(Draft.version))).scalars().all()
        new = [d for d in drafts if d.version > draft_version_at_start]
        old = [d for d in drafts if d.version <= draft_version_at_start]
        # a half-finished new draft (never checked) is not kept if an earlier, complete one exists
        if old and any(d.genericness is None for d in new):
            await s.execute(delete(Draft).where(Draft.run_id == run_id, Draft.version > draft_version_at_start))
            new = []
        await s.commit()
    latest = (new or old or [None])[-1]
    run = await load_run(run_id)
    warnings = list(run.warnings or [])
    info = {"reason": reason, "stage": ctx.current_stage, "at": utcnow().isoformat(), "message": stopped_msg}

    if latest is not None:
        # results so far are usable: send the rep to review, saying exactly what is incomplete
        gaps = []
        if latest.genericness is None:
            gaps.append("the genericness check did not run")
        if kind == "regenerate" and not new:
            note = f"{stopped_msg} The previous draft was kept."
        else:
            note = stopped_msg + (" The draft is incomplete: " + "; ".join(gaps) + "." if gaps else "")
        info["incomplete"] = gaps
        warnings.append(note)
        async with session() as s:
            st = (await s.execute(select(Stage).where(Stage.run_id == run_id, Stage.key == "review")
                                  .order_by(Stage.attempt.desc()))).scalars().first()
            if st:
                st.status, st.finished_at = "waiting", None
                st.explanation = ("Draft ready for review, but incomplete - read the warning before approving."
                                  if gaps else "Draft ready. Waiting for the rep. Nothing is sent automatically.")
            await s.commit()
        await update_run(run_id, status="awaiting_review", current_stage="review", warnings=warnings,
                         interrupted=info, cancel_requested=False)
        await emit(run_id, "run_status", {"status": "awaiting_review", "interrupted": reason})
        return
    await update_run(run_id, status=status_word, outcome=status_word, current_stage=None, finished_at=utcnow(),
                     interrupted=info, warnings=warnings + [stopped_msg + " Everything found so far is kept below."])
    await emit(run_id, "run_status", {"status": status_word})


async def finalize_without_job(run_id: str, reason: str = "user"):
    """Stop a run that has no job executing right now (queued, or paused for the rep)."""
    ctx = RunContext(run_id, None, None, "live")  # type: ignore[arg-type]
    ctx.cancel_reason = reason
    run = await load_run(run_id)
    ctx.current_stage = run.current_stage
    await _finalize_interrupted(ctx, run_id, "start", draft_version_at_start=0)


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
    await S.draft_and_check(ctx, run, asdict(dec))
    await _await_review(ctx, run_id)


async def _await_review(ctx: RunContext, run_id: str):
    await ctx.mark_stage("review", "waiting", "Draft ready. Waiting for the rep to approve, edit, reject or "
                         "regenerate. Nothing is sent automatically.")
    await update_run(run_id, status="awaiting_review", current_stage="review")
    await emit(run_id, "run_status", {"status": "awaiting_review"})


async def _retry(ctx: RunContext, run_id: str):
    """Re-run from the first failed / interrupted stage."""
    async with session() as s:
        rows = (await s.execute(select(Stage).where(Stage.run_id == run_id))).scalars().all()
    failed = sorted((r for r in rows if r.status in ("failed", "cancelled", "timed_out")), key=lambda r: r.seq)
    first = failed[0].key if failed else "identity"
    run = await load_run(run_id)
    if first == "identity":
        identity = await S.stage_identity(ctx, run)
        st = identity["status"]
        if st in ("ambiguous", "role_conflict", "ambiguous_company") and not run.rep_decisions:
            await pause_for_rep(ctx, run_id, identity)
            return
        if st in ("fictional", "nothing_found"):
            await stop_after_identity(ctx, run_id, identity)
            return
        await _research_and_draft(ctx, run_id)
    elif first in ("gather", "filter", "rank", "route"):
        await _research_and_draft(ctx, run_id)
    else:
        route = (await _stage_output(run_id, "route")) or {}
        await S.draft_and_check(ctx, run, route)
        await _await_review(ctx, run_id)
