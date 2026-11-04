from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from .db import session, utcnow
from .jobs import enqueue
from .models import Draft, Event, LLMCall, Review, Run, SearchRecord, SellerProfile, Signal, Stage
from .pipeline.context import STAGES, emit, prospect_key
from .pipeline.lint import lint_draft
from .pipeline.runner import init_stages
from .pipeline.textmatch import levenshtein
from .scenarios import SCENARIOS
from .security import check_run_budget, client_id, require_access
from .seed import LEDGERLINE, get_seller_profile

router = APIRouter(prefix="/api")
auth = [Depends(require_access)]


# --------------------------------------------------------------------------- schemas
class ProspectIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    company: str = Field(min_length=1, max_length=160)
    role: str | None = Field(default=None, max_length=160)
    linkedin_url: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=6000)

    @field_validator("name", "company", "role", "linkedin_url", "notes")
    @classmethod
    def strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("linkedin_url")
    @classmethod
    def li(cls, v):
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("LinkedIn URL must start with http(s)://")
        return v or None


class SellerIn(BaseModel):
    company_name: str = Field(max_length=120)
    product: str = Field(max_length=2000)
    ideal_customer: str = Field(max_length=2000)
    problems_solved: list[str] = Field(max_length=12)
    proof_points: list[str] = Field(max_length=12)
    tone: str = Field(max_length=500)
    sender_name: str = Field(max_length=120)
    fictional_notice: str | None = None


class RunIn(BaseModel):
    prospect: ProspectIn
    mode: Literal["live", "replay"] = "live"
    scenario_key: str | None = None


class DecisionIn(BaseModel):
    choice: Literal["pick", "use_new", "keep_original", "stop"]
    candidate_id: str | None = None


class ReviewIn(BaseModel):
    action: Literal["approve", "edit", "reject", "regenerate"]
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=5000)
    note: str | None = Field(default=None, max_length=1000)
    instruction: str | None = Field(default=None, max_length=500)
    acknowledged_sensitive: bool = False


class RetryIn(BaseModel):
    mode: Literal["live", "replay"] | None = None


# --------------------------------------------------------------------------- open endpoints
@router.get("/health")
async def health():
    async with session() as s:
        await s.execute(select(1))
    return {"ok": True, "time": utcnow().isoformat()}


class CodeIn(BaseModel):
    code: str


@router.post("/auth/check")
async def auth_check(body: CodeIn):
    from .security import _ok

    if not _ok(body.code):
        raise HTTPException(401, "That access code is not valid.")
    return {"ok": True}


# --------------------------------------------------------------------------- seller profile
@router.get("/seller-profile", dependencies=auth)
async def get_profile():
    p = await get_seller_profile()
    return {"id": p.id, "data": p.data, "updated_at": p.updated_at}


@router.put("/seller-profile", dependencies=auth)
async def put_profile(body: SellerIn):
    p = await get_seller_profile()
    async with session() as s:
        row = await s.get(SellerProfile, p.id)
        row.data = body.model_dump()
        row.name = body.company_name
        await s.commit()
        return {"id": row.id, "data": row.data}


@router.post("/seller-profile/reset", dependencies=auth)
async def reset_profile():
    p = await get_seller_profile()
    async with session() as s:
        row = await s.get(SellerProfile, p.id)
        row.data, row.name = LEDGERLINE, "Ledgerline"
        await s.commit()
    return {"data": LEDGERLINE}


@router.get("/scenarios", dependencies=auth)
async def scenarios():
    out = []
    for sc in SCENARIOS:
        recs = await _has_saved(sc["prospect"]["name"], sc["prospect"]["company"])
        out.append({**sc, "replay_available": recs})
    return out


async def _has_saved(name: str, company: str) -> bool:
    async with session() as s:
        n = (await s.execute(select(func.count()).select_from(SearchRecord).where(
            SearchRecord.prospect_key == prospect_key(name, company), SearchRecord.provider != "replay"))).scalar()
    return bool(n)


@router.get("/replay-availability", dependencies=auth)
async def replay_availability(name: str, company: str):
    return {"available": await _has_saved(name, company)}


# --------------------------------------------------------------------------- runs
@router.post("/runs", dependencies=auth)
async def create_run(body: RunIn, request: Request):
    cid = client_id(request)
    await check_run_budget(cid)
    if body.mode == "replay" and not await _has_saved(body.prospect.name, body.prospect.company):
        raise HTTPException(400, "No saved sources for this prospect yet. Run it once in live mode first.")
    seller = (await get_seller_profile()).data
    async with session() as s:
        run = Run(prospect=body.prospect.model_dump(), seller_profile=seller, mode=body.mode,
                  scenario_key=body.scenario_key, client_id=cid, status="queued")
        s.add(run)
        await s.commit()
        rid = run.id
    await init_stages(rid)
    await emit(rid, "run_status", {"status": "queued"})
    await enqueue(rid, "start")
    return {"id": rid}


def _run_summary(r: Run) -> dict:
    return {
        "id": r.id, "created_at": r.created_at, "finished_at": r.finished_at, "status": r.status,
        "outcome": r.outcome, "mode": r.mode, "prospect": r.prospect, "final_route": r.final_route,
        "route_path": r.route_path, "personalisation": r.personalisation, "edge_cases": r.edge_cases,
        "warnings": r.warnings, "cost_usd": r.cost_usd, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
        "active_ms": r.active_ms, "current_stage": r.current_stage, "scenario_key": r.scenario_key,
        "identity_confidence": (r.identity or {}).get("confidence"), "error": r.error,
        "llm_calls": r.llm_calls, "search_calls": r.search_calls,
    }


@router.get("/runs", dependencies=auth)
async def list_runs(q: str | None = None, status: str | None = None, route: str | None = None,
                    outcome: str | None = None, edge: str | None = None, mode: str | None = None,
                    limit: int = 50, offset: int = 0):
    async with session() as s:
        rows = (await s.execute(select(Run).order_by(Run.created_at.desc()).limit(1000))).scalars().all()
    out = []
    for r in rows:
        if q and q.lower() not in json.dumps(r.prospect).lower():
            continue
        if status and r.status != status:
            continue
        if route and r.final_route != route and route not in (r.route_path or []):
            continue
        if outcome and (r.outcome or "open") != outcome:
            continue
        if edge and edge not in (r.edge_cases or []):
            continue
        if mode and r.mode != mode:
            continue
        out.append(_run_summary(r))
    return {"total": len(out), "items": out[offset:offset + min(limit, 200)]}


@router.get("/metrics", dependencies=auth)
async def metrics():
    async with session() as s:
        runs = (await s.execute(select(Run))).scalars().all()
        reviews = (await s.execute(select(Review))).scalars().all()
    n = len(runs)
    reviewed = [r for r in runs if r.outcome in ("approved", "approved_with_edits", "rejected")]
    approved = [r for r in runs if r.outcome in ("approved", "approved_with_edits")]
    edited = [r for r in runs if r.outcome == "approved_with_edits"]
    finished = [r for r in runs if r.status in ("completed", "awaiting_review", "failed")]
    edge: dict[str, int] = {}
    routes: dict[str, int] = {}
    for r in runs:
        for e in r.edge_cases or []:
            edge[e] = edge.get(e, 0) + 1
        if r.final_route:
            routes[r.final_route] = routes.get(r.final_route, 0) + 1
    statuses: dict[str, int] = {}
    for r in runs:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    durs = sorted(r.active_ms for r in finished if r.active_ms)
    return {
        "runs": n, "statuses": statuses,
        "approval_rate": round(len(approved) / len(reviewed), 3) if reviewed else None,
        "edit_rate": round(len(edited) / len(approved), 3) if approved else None,
        "reviewed": len(reviewed), "approved": len(approved), "edited": len(edited),
        "rejected": sum(1 for r in runs if r.outcome == "rejected"),
        "failed": sum(1 for r in runs if r.status == "failed"),
        "edge_cases": edge, "routes": routes,
        "median_duration_ms": durs[len(durs) // 2] if durs else None,
        "total_cost_usd": round(sum(r.cost_usd for r in runs), 4),
        "avg_cost_usd": round(sum(r.cost_usd for r in runs) / n, 4) if n else None,
        "regenerations": sum(1 for rv in reviews if rv.action == "regenerate"),
    }


@router.get("/runs/{run_id}", dependencies=auth)
async def get_run(run_id: str):
    async with session() as s:
        r = await s.get(Run, run_id)
        if r is None:
            raise HTTPException(404, "Run not found")
        stages = (await s.execute(select(Stage).where(Stage.run_id == run_id)
                                  .order_by(Stage.seq, Stage.attempt))).scalars().all()
        sigs = (await s.execute(select(Signal).where(Signal.run_id == run_id).order_by(Signal.pk))).scalars().all()
        drafts = (await s.execute(select(Draft).where(Draft.run_id == run_id).order_by(Draft.version))).scalars().all()
        reviews = (await s.execute(select(Review).where(Review.run_id == run_id).order_by(Review.id))).scalars().all()
        calls = (await s.execute(select(LLMCall).where(LLMCall.run_id == run_id).order_by(LLMCall.id))).scalars().all()
        last_event = (await s.execute(select(func.max(Event.id)).where(Event.run_id == run_id))).scalar() or 0
    latest: dict[str, Stage] = {}
    history: dict[str, list] = {}
    for st in stages:
        history.setdefault(st.key, []).append(st)
        latest[st.key] = st

    def st_json(st: Stage) -> dict:
        return {"key": st.key, "attempt": st.attempt, "status": st.status, "started_at": st.started_at,
                "finished_at": st.finished_at, "duration_ms": st.duration_ms, "input": st.input,
                "output": st.output, "explanation": st.explanation, "error": st.error,
                "tokens_in": st.tokens_in, "tokens_out": st.tokens_out, "cost_usd": st.cost_usd}

    timeline = []
    for key, label in STAGES:
        st = latest.get(key)
        timeline.append({"label": label, **(st_json(st) if st else {"key": key, "status": "pending"}),
                         "attempts": [st_json(x) for x in history.get(key, [])[:-1] if x.status != "pending"]})
    return {
        **_run_summary(r), "seller_profile": r.seller_profile, "identity": r.identity, "pause": r.pause,
        "rep_decisions": r.rep_decisions, "route_reason": r.route_reason,
        "replay_source_run_id": r.replay_source_run_id, "last_event_id": last_event,
        "stages": timeline,
        "signals": [{"sid": x.sid, "url": x.url, "title": x.title, "published_date": x.published_date,
                     "text_excerpt": x.text[:1200], "provider": x.provider, "query": x.query, "scope": x.scope,
                     "signal_type": x.signal_type, "about": x.about, "status": x.status,
                     "drop_reason": x.drop_reason, "sensitive": x.sensitive, "judgments":
                         {k: v for k, v in (x.judgments or {}).items() if k != "focus"},
                     "scores": x.scores} for x in sigs],
        "drafts": [{"id": d.id, "version": d.version, "origin": d.origin, "subject": d.subject, "body": d.body,
                    "hook_signal_id": d.hook_signal_id, "meta": d.meta, "lint": d.lint,
                    "verification": d.verification, "genericness": d.genericness, "created_at": d.created_at}
                   for d in drafts],
        "reviews": [{"id": v.id, "draft_id": v.draft_id, "action": v.action, "original_text": v.original_text,
                     "final_text": v.final_text, "note": v.note, "acknowledged_sensitive": v.acknowledged_sensitive,
                     "edit_distance": v.edit_distance, "created_at": v.created_at} for v in reviews],
        "llm_call_log": [{"stage": c.stage, "purpose": c.purpose, "model": c.model, "input_tokens": c.input_tokens,
                          "output_tokens": c.output_tokens, "cost_usd": c.cost_usd, "latency_ms": c.latency_ms,
                          "ok": c.ok, "error": c.error} for c in calls],
    }


@router.get("/runs/{run_id}/events", dependencies=auth)
async def run_events(run_id: str, request: Request, after: int = 0):
    async def gen():
        last = after
        idle = 0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            async with session() as s:
                evs = (await s.execute(select(Event).where(Event.run_id == run_id, Event.id > last)
                                       .order_by(Event.id).limit(100))).scalars().all()
            for e in evs:
                last = e.id
                payload = json.dumps({"id": e.id, "type": e.type, "data": e.data, "ts": e.ts.isoformat()})
                yield f"id: {e.id}\nevent: run\ndata: {payload}\n\n"
            if evs:
                idle = 0
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/decision", dependencies=auth)
async def decide(run_id: str, body: DecisionIn):
    async with session() as s:
        r = await s.get(Run, run_id)
        if r is None:
            raise HTTPException(404, "Run not found")
        if r.status != "awaiting_rep" or not r.pause:
            raise HTTPException(409, "This run is not waiting for a decision.")
        kind = r.pause["kind"]
        cands = {c["id"]: c for c in r.pause.get("candidates", [])}
        d: dict = {"kind": kind, "choice": body.choice, "at": utcnow().isoformat()}
        if body.choice == "pick":
            if kind != "ambiguous" or body.candidate_id not in cands:
                raise HTTPException(400, "Pick one of the listed candidates.")
            d["candidate"] = cands[body.candidate_id]
        if body.choice in ("use_new", "keep_original"):
            if kind != "role_conflict":
                raise HTTPException(400, "That choice only applies to a role conflict.")
            c = next(iter(cands.values()), {})
            d.update(new_company=c.get("current_company"), new_role=c.get("current_role"),
                     original=r.prospect)
        r.rep_decisions = list(r.rep_decisions or []) + [d]
        if kind == "ambiguous" and body.choice == "pick":
            ident = dict(r.identity or {})
            ident.update(status="resolved", confidence="medium", chosen=body.candidate_id,
                         reasons=list(ident.get("reasons", [])) + ["Identity chosen by the rep."])
            r.identity = ident
        if kind == "role_conflict" and body.choice == "use_new":
            ident = dict(r.identity or {})
            ident.update(status="resolved", confidence="medium",
                         reasons=list(ident.get("reasons", [])) + ["Rep confirmed the new company/role."])
            r.identity = ident
        r.pause, r.status = None, "queued"
        await s.commit()
    await emit(run_id, "rep_decision", d)
    await enqueue(run_id, "resume")
    return {"ok": True}


@router.post("/runs/{run_id}/review", dependencies=auth)
async def review(run_id: str, body: ReviewIn):
    async with session() as s:
        r = await s.get(Run, run_id)
        if r is None:
            raise HTTPException(404, "Run not found")
        if r.status != "awaiting_review" and not (r.status == "completed" and body.action == "regenerate"
                                                  and r.outcome in ("rejected",)):
            raise HTTPException(409, "This run is not awaiting review.")
        d = (await s.execute(select(Draft).where(Draft.run_id == run_id).order_by(Draft.version.desc()))
             ).scalars().first()
        if d is None:
            raise HTTPException(409, "No draft to review.")
        prior_edit = (await s.execute(select(Review).where(Review.run_id == run_id, Review.action == "edit")
                                      .order_by(Review.id.desc()))).scalars().first()
        original = f"Subject: {d.subject}\n\n{d.body}"
        current = prior_edit.final_text if prior_edit and prior_edit.draft_id == d.id else original
        rv = Review(run_id=run_id, draft_id=d.id, action=body.action, original_text=original, note=body.note,
                    acknowledged_sensitive=body.acknowledged_sensitive)
        route_out = (await s.execute(select(Stage).where(Stage.run_id == run_id, Stage.key == "route")
                                     .order_by(Stage.attempt.desc()))).scalars().first()
        needs_ack = bool(route_out and (route_out.output or {}).get("sensitive_mode") == "acknowledge")
        if body.action in ("edit", "approve") and body.body is not None:
            subj = body.subject if body.subject is not None else d.subject
            current = f"Subject: {subj.strip()}\n\n{body.body.strip()}"
        if body.action == "edit":
            rv.final_text = current
            rv.edit_distance = levenshtein(original, current)
            rv.note = (body.note or "") + (" | lint: " + "; ".join(lint_draft(*_split(current))["issues"])
                                           if not lint_draft(*_split(current))["ok"] else "")
        elif body.action == "approve":
            if needs_ack and not body.acknowledged_sensitive:
                raise HTTPException(400, "This draft touches a sensitive situation. Tick the acknowledgement "
                                         "before approving.")
            rv.final_text = current
            rv.edit_distance = levenshtein(original, current)
            r.outcome = "approved_with_edits" if rv.edit_distance else "approved"
            r.status, r.finished_at = "completed", utcnow()
        elif body.action == "reject":
            r.outcome, r.status, r.finished_at = "rejected", "completed", utcnow()
        elif body.action == "regenerate":
            r.status, r.outcome = "queued", None
        s.add(rv)
        await s.commit()
        out = {"ok": True, "outcome": r.outcome, "status": r.status}
    await emit(run_id, "review_action", {"action": body.action})
    if body.action == "regenerate":
        await enqueue(run_id, "regenerate", {"instruction": body.instruction})
    elif body.action in ("approve", "reject"):
        async with session() as s:
            st = (await s.execute(select(Stage).where(Stage.run_id == run_id, Stage.key == "review")
                                  .order_by(Stage.attempt.desc()))).scalars().first()
            if st:
                st.status, st.finished_at = "succeeded", utcnow()
                st.explanation = {"approve": "Approved by the rep - marked ready to copy. Not sent.",
                                  "reject": "Rejected by the rep."}[body.action]
                st.output = {"action": body.action, "outcome": out["outcome"]}
                await s.commit()
        await emit(run_id, "run_status", {"status": "completed", "outcome": out["outcome"]})
    return out


def _split(text: str) -> tuple[str, str]:
    if text.startswith("Subject:"):
        head, _, body = text.partition("\n\n")
        return head[len("Subject:"):].strip(), body
    return "", text


@router.post("/runs/{run_id}/retry", dependencies=auth)
async def retry(run_id: str, body: RetryIn, request: Request):
    await check_run_budget(client_id(request))
    async with session() as s:
        r = await s.get(Run, run_id)
        if r is None:
            raise HTTPException(404, "Run not found")
        if r.status != "failed":
            raise HTTPException(409, "Only failed runs can be retried.")
        if body.mode == "replay" and not await _has_saved(r.prospect["name"], r.prospect["company"]):
            raise HTTPException(400, "No saved sources for this prospect - replay is not possible.")
        if body.mode:
            r.mode = body.mode
        r.status, r.outcome, r.error, r.finished_at = "queued", None, None, None
        await s.commit()
    await emit(run_id, "run_status", {"status": "queued", "retry": True})
    await enqueue(run_id, "retry")
    return {"ok": True}


@router.get("/runs/{run_id}/fixture", dependencies=auth)
async def export_fixture(run_id: str):
    """Export a real run as a replayable test fixture: its saved search results + every LLM response in order."""
    async with session() as s:
        r = await s.get(Run, run_id)
        if r is None:
            raise HTTPException(404, "Run not found")
        recs = (await s.execute(select(SearchRecord).where(SearchRecord.run_id == run_id)
                                .order_by(SearchRecord.id))).scalars().all()
        calls = (await s.execute(select(LLMCall).where(LLMCall.run_id == run_id, LLMCall.ok.is_(True))
                                 .order_by(LLMCall.id))).scalars().all()
        drafts = (await s.execute(select(Draft).where(Draft.run_id == run_id).order_by(Draft.version))).scalars().all()
    llm: dict[str, list] = {}
    for c in calls:
        llm.setdefault(c.purpose, []).append(c.response)
    search: dict[str, list] = {}
    for x in recs:
        search[x.params.get("key") or x.query.lower()] = x.results
    last = drafts[-1] if drafts else None
    return {
        "_note": f"Recorded from live run {run_id} ({r.mode}) on {r.created_at.date().isoformat()}.",
        "recorded_from": run_id, "today": r.created_at.date().isoformat(), "prospect": r.prospect,
        "seller_profile": r.seller_profile, "decisions": [{k: v for k, v in d.items() if k in ("choice",)}
                                                          | ({"candidate_id": d["candidate"]["id"]}
                                                             if d.get("candidate") else {})
                                                          for d in r.rep_decisions or []],
        "expected": {"final_route": r.final_route, "route_path": r.route_path, "edge_cases": r.edge_cases,
                     "personalisation": r.personalisation, "identity_status": (r.identity or {}).get("status"),
                     "unsupported": (last.verification or {}).get("unsupported") if last else None,
                     "generic": (last.genericness or {}).get("generic") if last else None},
        "search": search, "llm": llm,
    }
