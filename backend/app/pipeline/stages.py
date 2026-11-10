"""The nine pipeline stages. Each one: reads state, does its work through RunContext, writes results."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import asdict
from urllib.parse import urlparse, urlunparse

from rapidfuzz import fuzz
from sqlalchemy import delete, select

from ..config import get_settings
from ..db import session
from ..models import Draft, Run, Signal
from ..providers.llm import LLMError
from ..providers.search import SearchQuery, SearchResult
from . import prompts as P
from .context import RunContext, StageFailed, StageHandle, add_warning, update_run
from .injection import detect_injection
from .lint import lint_draft
from .llm_schemas import ClaimBatch, DraftOut, FilterBatch, GenericnessOut, RankBatch
from .identity import stage_identity  # noqa: F401  (re-exported for the runner)
from .routing import DO_NOT_DRAFT, RouteDecision, SignalView, decide_route
from .scoring import STALE_DAYS, age_days, domain_of, explain_ranking, score_signal
from .textmatch import normalize, quote_in_text, redact_personal

EXCLUDED_DOMAINS = ["linkedin.com", "zoominfo.com", "rocketreach.co", "signalhire.com", "apollo.io", "lusha.com",
                    "contactout.com", "spokeo.com", "whitepages.com", "peoplefinders.com",
                    # registries / people pages that publish dates of birth and home addresses
                    "find-and-update.company-information.service.gov.uk", "opencorporates.com",
                    "beenverified.com", "radaris.com", "truepeoplesearch.com", "fastpeoplesearch.com"]
MAX_TEXT = 20000


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return urlunparse(("https", host, p.path.rstrip("/"), "", "", "")).lower()


def blocked(url: str) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in EXCLUDED_DOMAINS)


def focus_text(result: SearchResult, terms: list[str], max_chars: int = 2400) -> str:
    """The snippet the LLM sees: Tavily's summary + paragraphs of the page that mention our terms."""
    raw = result.raw_content or ""
    parts = [result.content or ""]
    if raw:
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n", raw) if len(p.strip()) > 30]
        lt = [t.lower() for t in terms if t]
        hits = [p for p in paras if any(t in p.lower() for t in lt)]
        parts.extend(hits[:8] if hits else paras[:4])
    out, seen = [], set()
    for p in parts:
        k = p[:80]
        if k not in seen:
            seen.add(k)
            out.append(p)
    return redact_personal("\n".join(out)[:max_chars])


def full_text(result: SearchResult) -> str:
    return redact_personal(((result.content or "") + "\n\n" + (result.raw_content or ""))[:MAX_TEXT])


def name_terms(name: str) -> list[str]:
    parts = [p for p in re.split(r"\s+", name.strip()) if len(p) > 1]
    return [name.strip()] + ([parts[-1]] if len(parts) > 1 else [])


# =========================================================================== 1. identity -> identity.py


# =========================================================================== 2. gather
def effective_prospect(run: Run) -> dict:
    p = dict(run.prospect)
    for d in run.rep_decisions or []:
        if d.get("kind") == "role_conflict" and d.get("choice") == "use_new":
            p["company"] = d.get("new_company") or p["company"]
            p["role"] = d.get("new_role") or p.get("role")
        if d.get("kind") == "ambiguous" and d.get("candidate"):
            c = d["candidate"]
            p["role"] = c.get("current_role") or p.get("role")
            # research the person the rep picked, where they are now (the original input stays on the run)
            if c.get("current_company") and c.get("at_input_company") in ("former", "never"):
                p["company"] = c["current_company"]
            p["distinguisher"] = c.get("distinguisher")
        if d.get("kind") == "ambiguous_company" and d.get("company"):
            co = d["company"]
            p["company_label"] = co.get("short_label") or co.get("what_it_does") or None
            p["company_what"] = co.get("what_it_does")
    return p


async def stage_gather(ctx: RunContext, run: Run) -> list[dict]:
    p = effective_prospect(run)
    async with ctx.stage("gather", {"prospect": p}) as h:
        async with session() as s:
            await s.execute(delete(Signal).where(Signal.run_id == run.id))
            await s.commit()
        name, company = p["name"], p["company"]
        co = f'"{company}" {p["company_label"]}' if p.get("company_label") else f'"{company}"'
        queries = [
            ("person", SearchQuery(f'"{name}" {company} interview OR podcast OR keynote OR said', max_results=6)),
            ("person", SearchQuery(f'"{name}" {company}', max_results=6, time_range="year")),
            ("company", SearchQuery(f'{co} funding OR raises OR acquisition OR expansion', topic="news",
                                    max_results=6, time_range="year")),
            ("company", SearchQuery(f'{co} hiring OR launches OR partnership OR announces', topic="news",
                                    max_results=6, time_range="year")),
            ("company", SearchQuery(f'{co} finance OR CFO OR "accounts payable" OR invoices', max_results=5,
                                    time_range="year")),
        ]
        rows: list[Signal] = []
        n = 0
        results = await ctx.search_many(h, [q for _, q in queries])
        for (scope, q), rs in zip(queries, results):
            for r in rs:
                if blocked(r.url):
                    continue
                n += 1
                rows.append(Signal(run_id=run.id, sid=f"s{n}", url=r.url, title=r.title,
                                   published_date=r.published_date, text=full_text(r), provider=ctx.search.name,
                                   query=q.query, scope=scope,
                                   judgments={"focus": focus_text(r, name_terms(name) + [company])}))
        if p.get("notes"):
            n += 1
            rows.append(Signal(run_id=run.id, sid=f"s{n}", url=f"rep://notes/{run.id}", title="Rep-provided note",
                               published_date=None, text=p["notes"][:MAX_TEXT], provider="rep", query=None,
                               scope="rep", signal_type="post", judgments={"focus": p["notes"][:2400]}))
        async with session() as s:
            s.add_all(rows)
            await s.commit()
        by_scope = {sc: sum(1 for r in rows if r.scope == sc) for sc in ("person", "company", "rep")}
        h.output = {"count": len(rows), "by_scope": by_scope, "queries": [q.query for _, q in queries],
                    "mode": ctx.mode}
        if ctx.mode == "replay" and getattr(ctx.search, "misses", None):
            h.output["replay_misses"] = ctx.search.misses
        h.explanation = (f"Collected {len(rows)} items: {by_scope['person']} from person searches, "
                         f"{by_scope['company']} company news items"
                         + (", plus your note" if by_scope["rep"] else "") + "."
                         + (" (Replayed from saved sources.)" if ctx.mode == "replay" else ""))
        if not rows:
            h.explanation = "Search returned nothing for this person or company."
    return [{"sid": r.sid} for r in rows]


# =========================================================================== batched judgements
async def judge_in_batches(ctx: RunContext, h: StageHandle, *, purpose: str, system: str, make_user, items: list[dict],
                           schema, batch_size: int, reasks: int = 2) -> tuple[dict, list[str]]:
    """Ask for one judgement per item, in small batches; re-ask for any the model skipped.

    Seen live: a fallback model given 14 items in one call judged only 3. Smaller batches + targeted re-asks get
    near-complete coverage; anything still missing is returned so the caller can show it honestly.
    """
    got: dict = {}
    todo = list(items)
    for attempt in range(reasks + 1):
        if not todo:
            break
        if attempt:
            if not ctx.can_afford(8):
                await h.progress(f"{len(todo)} item(s) left unscored to stay within the time limit")
                break
            await h.progress(f"The model skipped {len(todo)} item(s); asking again for just those")
        batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
        # independent batches run concurrently (they were sequential: 2-3 x 5-10 s)
        results = await asyncio.gather(*(ctx.llm_call(h, purpose=purpose, system=system, user=make_user(b),
                                                       schema=schema, tier="fast") for b in batches))
        for batch, res in zip(batches, results):
            asked = {it["id"] for it in batch}
            for j in res.data.judgments:
                if j.sid in asked and j.sid not in got:
                    got[j.sid] = j
        todo = [it for it in todo if it["id"] not in got]
    return got, [it["id"] for it in todo]


# =========================================================================== 3. filter
async def load_signals(run_id: str, status: str | None = None) -> list[Signal]:
    async with session() as s:
        q = select(Signal).where(Signal.run_id == run_id)
        if status:
            q = q.where(Signal.status == status)
        return list((await s.execute(q.order_by(Signal.pk))).scalars())


async def save_signals(sigs: list[Signal]):
    async with session() as s:
        for x in sigs:
            await s.merge(x)
        await s.commit()


async def stage_filter(ctx: RunContext, run: Run):
    p = effective_prospect(run)
    async with ctx.stage("filter") as h:
        sigs = await load_signals(run.id)
        seen_urls, seen_titles = {}, []
        t = today()
        for x in sigs:
            cu = canonical_url(x.url)
            dup = seen_urls.get(cu)
            if dup is None and x.title and x.scope != "rep":
                for sid, title in seen_titles:
                    if fuzz.ratio(normalize(title), normalize(x.title)) >= 92:
                        dup = sid
                        break
            if dup:
                x.status, x.drop_reason = "dropped", f"Duplicate of {dup}"
                continue
            seen_urls[cu] = x.sid
            seen_titles.append((x.sid, x.title or ""))
            age = age_days(x.published_date, t)
            if age is not None and age > STALE_DAYS:
                x.status, x.drop_reason = "dropped", f"Stale ({age} days old)"
                continue
            hits = detect_injection(x.text)
            if hits:
                x.status = "quarantined"
                x.drop_reason = "Possible prompt injection - text contains instructions aimed at an AI"
                x.sensitive = {"injection_snippets": hits[:3]}
        live = [x for x in sigs if x.status == "kept"]
        await h.progress(f"Classifying {len(live)} items (who is it about? sensitive? personal?)")
        if live:
            items = [{"id": x.sid, "url": x.url, "title": x.title, "published": x.published_date,
                      "text": (x.judgments or {}).get("focus", x.text)} for x in live[:30]]
            js, _ = await judge_in_batches(ctx, h, purpose="filter", system=P.FILTER_SYSTEM,
                                           make_user=lambda b: P.filter_user(p, b), items=items,
                                           schema=FilterBatch, batch_size=15)
            for x in live[30:]:
                x.status, x.drop_reason = "dropped", "Over the per-run review limit (30 items)"
            for x in live[:30]:
                j = js.get(x.sid)
                if j is None:
                    x.status, x.drop_reason = "dropped", "Classifier returned no judgement"
                    continue
                x.about = "person" if x.scope == "rep" and j.about in ("person", "unclear") else j.about
                x.signal_type = j.signal_type
                x.sensitive = {"category": j.sensitive_category}
                x.judgments = {**(x.judgments or {}), "filter_reason": j.reason}
                if j.contains_personal_details:
                    x.status, x.drop_reason = "dropped", "Contains personal (non-professional) details"
                elif x.about not in ("person", "company"):
                    x.status, x.drop_reason = "dropped", f"Not about this person/company ({j.about}): {j.reason}"
        await save_signals(sigs)
        kept = [x for x in sigs if x.status == "kept"]
        dropped = [x for x in sigs if x.status == "dropped"]
        quarantined = [x for x in sigs if x.status == "quarantined"]
        sensitive = [x for x in kept if (x.sensitive or {}).get("category", "none") != "none"]
        h.output = {"kept": len(kept), "dropped": len(dropped), "quarantined": len(quarantined),
                    "sensitive": [x.sid for x in sensitive],
                    "drop_reasons": {x.sid: x.drop_reason for x in dropped + quarantined}}
        h.explanation = (f"Kept {len(kept)} of {len(sigs)} items. Removed {len(dropped)} (duplicates, stale, "
                         f"off-topic or personal)" + (f", quarantined {len(quarantined)} that contained "
                                                      "instructions aimed at an AI" if quarantined else "")
                         + (f". {len(sensitive)} flagged as sensitive." if sensitive else "."))
    if quarantined:
        await add_warning(run.id, f"{len(quarantined)} source(s) quarantined for possible prompt injection.")


# =========================================================================== 4. rank
def guess_company_domain(company: str, sigs: list[Signal]) -> str | None:
    slug = re.sub(r"[^a-z0-9]", "", company.lower())
    if len(slug) < 3:
        return None
    for x in sigs:
        host = domain_of(x.url)
        if slug in host.replace("-", "").replace(".", ""):
            return host
    return None


async def stage_rank(ctx: RunContext, run: Run) -> list[dict]:
    p = effective_prospect(run)
    async with ctx.stage("rank") as h:
        sigs = await load_signals(run.id, "kept")
        cand = sorted(sigs, key=lambda x: (x.about != "person", x.scope != "rep"))[:14]
        if not cand:
            h.output = {"ranking": [], "explain": {"winner": None, "why_winner": "Nothing to rank."}}
            h.explanation = "Nothing survived filtering, so there is nothing to rank."
            return []
        await h.progress(f"Scoring {len(cand)} signals for specificity and relevance to {run.seller_profile.get('company_name', 'the seller')}")
        items = [{"id": x.sid, "url": x.url, "title": x.title, "published": x.published_date,
                  "type": x.signal_type, "text": (x.judgments or {}).get("focus", x.text)} for x in cand]
        js, unscored = await judge_in_batches(ctx, h, purpose="rank", system=P.RANK_SYSTEM,
                                              make_user=lambda b: P.rank_user(p, run.seller_profile, b),
                                              items=items, schema=RankBatch, batch_size=7)
        domain = guess_company_domain(p["company"], sigs)
        t = today()
        scored = []
        for x in cand:
            j = js.get(x.sid)
            if j is None:
                continue
            verified, ratio = quote_in_text(j.key_fact, x.text)
            sens = (x.sensitive or {}).get("category", "none") != "none"
            sc = score_signal(sid=x.sid, specificity=j.specificity, relevance=j.relevance,
                              published=x.published_date, url=x.url, scope=x.scope, today=t,
                              company_domain=domain, key_fact_verified=verified, sensitive=sens)
            x.scores = {**sc.components(), "total": sc.total, "eligible": sc.eligible,
                        "ineligible_reason": sc.ineligible_reason, "notes": sc.notes,
                        "age_days": age_days(x.published_date, t)}
            x.judgments = {**(x.judgments or {}), "specificity_0_3": j.specificity, "relevance_0_3": j.relevance,
                           "key_fact": j.key_fact, "key_fact_verified": verified, "key_fact_match": ratio,
                           "seller_problem": j.seller_problem, "why": j.why}
            scored.append(sc)
        missing = [x for x in cand if x.sid not in js]
        if cand and not scored:
            raise StageFailed(f"The model returned no scores for any of the {len(cand)} signals, even after "
                              "re-asking. This is a model problem, not a lack of signal - retry the run.",
                              "llm_incomplete")
        for x in missing:
            # not given a fake 0 score: left out of the ranking (and of routing) and listed separately
            x.scores = None
            x.judgments = {**(x.judgments or {}), "unscored": True}
        await save_signals(sigs)
        expl = explain_ranking(scored)
        ranking = sorted(({"sid": x.sid, "title": x.title, "url": x.url, "about": x.about, **(x.scores or {})}
                          for x in cand if x.scores), key=lambda r: r["total"], reverse=True)
        h.output = {"ranking": ranking, "explain": expl, "company_domain": domain,
                    "unscored": [{"sid": x.sid, "title": x.title, "url": x.url} for x in missing],
                    "formula": "0.30*specificity + 0.30*relevance + 0.25*recency + 0.15*credibility"}
        win = next((r for r in ranking if r["sid"] == expl.get("winner")), None)
        h.explanation = ((f"Ranked {len(ranking)} signals. Top hook: “{win['title'][:90]}”. "
                          + expl["why_winner"]) if win else "No signal qualified as a safe, verifiable hook.") + (
            f" {len(missing)} item(s) could not be scored by the model even after re-asking and were left out."
            if missing else "")
    return ranking


# =========================================================================== 5. route
async def stage_route(ctx: RunContext, run: Run) -> RouteDecision:
    async with ctx.stage("route") as h:
        sigs = [x for x in await load_signals(run.id, "kept") if x.scores]
        views = [SignalView(x.sid, x.scores.get("total", 0), x.scores.get("eligible", False), x.about or "company",
                            (x.sensitive or {}).get("category", "none"), x.scores.get("age_days"),
                            (x.judgments or {}).get("specificity_0_3", 0)) for x in sigs]
        ident = run.identity or {}
        stopped = any(d.get("choice") == "stop" for d in run.rep_decisions or [])
        continued = any(d.get("kind") == "role_conflict" and d.get("choice") == "keep_original"
                        for d in run.rep_decisions or [])
        dec = decide_route(views, ident.get("confidence", "low"), stopped, continued)
        h.output = asdict(dec)
        h.explanation = f"Rule {dec.rule}: {dec.reason}"
    edge = list(run.edge_cases or [])
    if dec.rule in ("R2", "R5") and "thin_signal" not in edge:
        edge.append("thin_signal")
    if dec.sensitive_mode != "none" and "sensitive_signal" not in edge:
        edge.append("sensitive_signal")
    # complete the "what could / couldn't be verified" checklist with what the research found
    usable = [x for x in sigs if x.scores.get("eligible")]
    person_n = sum(1 for x in usable if x.about == "person")
    company_n = sum(1 for x in usable if x.about == "company")
    checks = [c for c in ident.get("checks", []) if c.get("key") not in ("person_signal", "company_news")]
    checks += [
        {"key": "person_signal", "label": "Something recent and specific about the person",
         "status": "verified" if person_n else "not_verified",
         "detail": f"{person_n} usable item(s)." if person_n else "Nothing usable: no interviews, posts or talks found."},
        {"key": "company_news", "label": "Recent company news",
         "status": "verified" if company_n else "not_verified",
         "detail": f"{company_n} usable item(s)." if company_n else "No usable, credible company news found."},
    ]
    await update_run(run.id, route_path=list(run.route_path or []) + [dec.route], final_route=dec.route,
                     route_reason=f"{dec.rule}: {dec.reason}", personalisation=dec.personalisation,
                     edge_cases=edge, identity={**ident, "checks": checks} if ident else ident)
    for w in dec.warnings:
        await add_warning(run.id, w)
    return dec


async def stage_route_fixed(ctx: RunContext, run: Run, rule: str, reason: str, missing: list[str], edge: str):
    """Route decided at the identity gate (fictional / nothing found): recorded like any other route."""
    async with ctx.stage("route") as h:
        dec = RouteDecision(DO_NOT_DRAFT, rule, reason, None, "none", missing=[m for m in missing if m])
        h.output = asdict(dec)
        h.explanation = f"Rule {rule}: {reason}"
    await update_run(run.id, route_path=list(run.route_path or []) + [DO_NOT_DRAFT], final_route=DO_NOT_DRAFT,
                     route_reason=f"{rule}: {reason}", personalisation="none",
                     edge_cases=list(dict.fromkeys(list(run.edge_cases or []) + [edge])))
    return dec


# =========================================================================== 6. draft
def signal_payload(x: Signal, n: int = 2500) -> dict:
    return {"id": x.sid, "url": x.url, "title": x.title, "published": x.published_date, "type": x.signal_type,
            "text": ((x.judgments or {}).get("key_fact", "") + "\n" + (x.judgments or {}).get("focus", x.text))[:n]}


async def _hook_and_support(run: Run, route: dict) -> tuple[Signal, list[Signal]]:
    sigs = await load_signals(run.id, "kept")
    by = {x.sid: x for x in sigs}
    hook = by.get(route["hook"])
    if hook is None:
        raise StageFailed("Chosen hook signal is missing", "internal")
    support = sorted((x for x in sigs if x.sid != hook.sid and x.scores and x.scores.get("eligible")),
                     key=lambda x: x.scores["total"], reverse=True)[:2]
    return hook, support


async def _next_version(run_id: str) -> int:
    async with session() as s:
        rows = (await s.execute(select(Draft.version).where(Draft.run_id == run_id))).scalars().all()
    return (max(rows) if rows else 0) + 1


async def stage_draft(ctx: RunContext, run: Run, route: dict, origin: str = "generated",
                      feedback: list[str] | None = None, instruction: str | None = None) -> Draft:
    p = effective_prospect(run)
    async with ctx.stage("draft", {"hook": route["hook"], "origin": origin, "instruction": instruction}) as h:
        hook, support = await _hook_and_support(run, route)
        system = P.DRAFT_SYSTEM.replace("{sender_sign_off}", run.seller_profile.get("sender_name", "Alex"))
        if route.get("sensitive_mode") == "acknowledge":
            sens = next((x for x in await load_signals(run.id) if x.sid == route.get("sensitive_signal")), None)
            system += P.SENSITIVE_ADDENDUM.format(category=(sens.sensitive or {}).get("category", "sensitive")
                                                  if sens else "sensitive")
        fb = list(feedback or [])
        out: DraftOut | None = None
        lint = {}
        for attempt in range(2):
            await h.progress("Writing draft" if attempt == 0 else "Fixing style issues: " + "; ".join(fb))
            res = await ctx.llm_call(h, purpose="draft", system=system,
                                     user=P.draft_user(p, run.seller_profile, signal_payload(hook),
                                                       [signal_payload(x, 1200) for x in support],
                                                       route.get("personalisation", "person"), fb, instruction),
                                     schema=DraftOut, tier="draft")
            out = res.data
            lint = lint_draft(out.subject, out.body)
            if lint["ok"]:
                break
            if not ctx.can_afford(get_settings().min_s_for_rewrite):
                break  # keep the draft; the style issues are reported as a warning instead
            fb = lint["issues"]
        version = await _next_version(run.id)
        d = Draft(run_id=run.id, version=version, origin=origin, subject=out.subject.strip(), body=out.body.strip(),
                  hook_signal_id=hook.sid, lint=lint,
                  meta={"problem": out.problem_addressed, "ask": out.ask, "instruction": instruction,
                        "support": [x.sid for x in support]})
        async with session() as s:
            s.add(d)
            await s.commit()
            await s.refresh(d)
        h.output = {"draft_id": d.id, "version": version, "subject": d.subject, "body": d.body,
                    "word_count": lint.get("word_count"), "lint": lint, "hook": hook.sid}
        h.explanation = (f"Wrote a {lint.get('word_count')}-word draft hooked on “{hook.title[:80]}”, "
                         f"linking it to: {out.problem_addressed}."
                         + ("" if lint["ok"] else " Style issues remain: " + "; ".join(lint["issues"])))
    if not lint.get("ok"):
        await add_warning(run.id, "Style issues: " + "; ".join(lint["issues"]))
    return d


# =========================================================================== 7. verify
FACT_KINDS = ("prospect_fact", "company_fact", "seller_fact")


def seller_source_text(seller: dict, source_id: str) -> str | None:
    field = source_id.split(".", 1)[1] if "." in source_id else ""
    if field not in seller:
        return " ".join(str(v) for v in seller.values()) if source_id == "seller" else None
    v = seller[field]
    return "\n".join(map(str, v)) if isinstance(v, list) else str(v)


async def check_claims(ctx: RunContext, h: StageHandle, run: Run, draft: dict) -> list[dict]:
    kept = {x.sid: x for x in await load_signals(run.id, "kept")}
    sources = [{"id": x.sid, "url": x.url, "title": x.title, "published": x.published_date,
                "text": (x.judgments or {}).get("key_fact", "") + "\n" + x.text[:6000]} for x in kept.values()
               if x.scores]
    res = await ctx.llm_call(h, purpose="verify", system=P.VERIFY_SYSTEM,
                             user=P.verify_user(draft, sources, run.seller_profile), schema=ClaimBatch, tier="verify")
    out = []
    for c in res.data.claims:
        item = c.model_dump()
        if c.kind not in FACT_KINDS:
            item.update(status="not_factual", supported=True)
            out.append(item)
            continue
        sid = c.source_id.strip()
        if sid.startswith("seller"):
            text = seller_source_text(run.seller_profile, sid)
            src_meta = {"source_type": "seller_profile", "url": None, "title": f"Seller profile: {sid}"}
        else:
            x = kept.get(sid)
            text = x.text if x else None
            src_meta = {"source_type": "signal", "url": x.url if x else None, "title": x.title if x else None,
                        "published": x.published_date if x else None}
        found, ratio = quote_in_text(c.supporting_quote, text) if text else (False, 0.0)
        supported = bool(text) and found and c.entailed
        problem = None
        if not text:
            problem = "cites no stored source" if not sid else f"cites '{sid}', which is not a kept source"
        elif not found:
            problem = "supporting quote not found in the stored source text"
        elif not c.entailed:
            problem = "source does not fully support the claim as written"
        item.update(src_meta, quote_match=round(ratio, 1), supported=supported,
                    status="supported" if supported else "unsupported", problem=problem)
        out.append(item)
    return out


async def stage_verify(ctx: RunContext, run: Run, route: dict, draft: Draft) -> tuple[Draft, dict]:
    p = effective_prospect(run)
    async with ctx.stage("verify", {"draft_id": draft.id}) as h:
        rounds = []
        current = draft
        skipped_for_time = False
        for rnd in range(3):  # initial check + up to 2 rewrites
            await h.progress("Checking each claim against stored sources" if rnd == 0
                             else f"Re-verifying rewrite {rnd}")
            claims = await check_claims(ctx, h, run, {"subject": current.subject, "body": current.body})
            bad = [c for c in claims if not c["supported"]]
            rounds.append({"draft_id": current.id, "version": current.version, "claims": len(claims),
                           "unsupported": len(bad)})
            ver = {"claims": claims, "unsupported": len(bad), "rounds": rnd, "passed": not bad}
            async with session() as s:
                row = await s.get(Draft, current.id)
                row.verification = ver
                await s.commit()
            current.verification = ver
            if not bad or rnd == 2:
                break
            if not ctx.can_afford(get_settings().min_s_for_rewrite):
                skipped_for_time = True
                await h.progress("Not enough time left for a rewrite; flagging the unsupported claims instead")
                break
            await h.progress(f"{len(bad)} unsupported claim(s); rewriting (round {rnd + 1} of 2)")
            hook, support = await _hook_and_support(run, route)
            system = P.REWRITE_SYSTEM.replace("{sender_sign_off}", run.seller_profile.get("sender_name", "Alex"))
            res = await ctx.llm_call(h, purpose="rewrite", system=system,
                                     user=P.rewrite_user(p, run.seller_profile, signal_payload(hook),
                                                         [signal_payload(x, 1200) for x in support],
                                                         {"subject": current.subject, "body": current.body}, bad,
                                                         route.get("personalisation", "person")),
                                     schema=DraftOut, tier="draft")
            nd = Draft(run_id=run.id, version=await _next_version(run.id), origin="rewrite",
                       subject=res.data.subject.strip(), body=res.data.body.strip(), hook_signal_id=hook.sid,
                       lint=lint_draft(res.data.subject, res.data.body),
                       meta={**(current.meta or {}), "rewrite_of": current.version,
                             "removed_claims": [c["claim"] for c in bad]})
            async with session() as s:
                s.add(nd)
                await s.commit()
                await s.refresh(nd)
            current = nd
        ver = current.verification
        n_fact = sum(1 for c in ver["claims"] if c["kind"] in FACT_KINDS)
        h.output = {"final_draft_id": current.id, "rounds": rounds, "unsupported": ver["unsupported"],
                    "factual_claims": n_fact}
        if ver["unsupported"]:
            h.explanation = (f"{ver['unsupported']} claim(s) still could not be traced to a source after "
                             f"{len(rounds) - 1} rewrite(s)"
                             + (" (no time left for another rewrite within the 90-second limit)"
                                if skipped_for_time else "") + ". Sent to review with a warning.")
        else:
            h.explanation = (f"All {n_fact} factual claims trace to stored sources"
                             + (f" after {len(rounds) - 1} rewrite(s)." if len(rounds) > 1 else "."))
    if ver["unsupported"]:
        await add_warning(run.id, f"{ver['unsupported']} unsupported claim(s) remain - check before approving.")
    return current, ver


# =========================================================================== 8. genericness
def mask(text: str, prospect: dict) -> str:
    out = text
    terms = [prospect["company"]] + name_terms(prospect["name"]) + prospect["name"].split()
    for t in sorted({t for t in terms if t and len(t) > 1}, key=len, reverse=True):
        repl = "[COMPANY]" if t == prospect["company"] else "[NAME]"
        out = re.sub(re.escape(t), repl, out, flags=re.IGNORECASE)
    return out


def swap_test_input(run: Run, draft: Draft) -> str:
    return mask(f"Subject: {draft.subject}\n\n{draft.body}", effective_prospect(run))


def start_swap_test(ctx: RunContext, run: Run, draft: Draft) -> asyncio.Task:
    """Started as soon as a draft exists, so it runs while claims are being verified."""
    t = asyncio.create_task(ctx.llm_raw(purpose="generic", system=P.GENERIC_SYSTEM,
                                        user=P.generic_user(swap_test_input(run, draft)),
                                        schema=GenericnessOut, tier="fast"))
    t.add_done_callback(lambda x: x.cancelled() or x.exception())  # result may be discarded; never warn
    return t


async def stage_generic(ctx: RunContext, run: Run, draft: Draft, pre: asyncio.Task | None = None) -> dict:
    async with ctx.stage("generic", {"draft_id": draft.id}) as h:
        claims = (draft.verification or {}).get("claims", [])
        specific_facts = [c for c in claims if c["kind"] in ("prospect_fact", "company_fact") and c["supported"]
                          and c.get("source_type") == "signal"]
        masked = swap_test_input(run, draft)
        res = None
        if pre is not None:
            try:
                res, _ = await pre
                await ctx.account(h, "generic", res)
                h.meta["ran_in_parallel_with_verify"] = True
            except LLMError:
                res = None  # fall back to a normal call below
        if res is None:
            await h.progress("Swap test: would this email still work for two unrelated prospects?")
            res = await ctx.llm_call(h, purpose="generic", system=P.GENERIC_SYSTEM, user=P.generic_user(masked),
                                     schema=GenericnessOut, tier="fast")
        fits = [d.model_dump() for d in res.data.decoys]
        llm_generic = any(d["still_makes_sense"] for d in fits)
        generic = llm_generic or not specific_facts
        reasons = []
        if not specific_facts:
            reasons.append("No verified fact specific to this prospect or company.")
        for d in fits:
            if d["still_makes_sense"]:
                reasons.append(f"Would still make sense for: {d['decoy']}.")
        g = {"generic": generic, "reasons": reasons, "decoys": fits, "specific_elements":
             res.data.specific_elements, "verified_specific_facts": len(specific_facts), "masked": masked}
        async with session() as s:
            row = await s.get(Draft, draft.id)
            row.genericness = g
            await s.commit()
        draft.genericness = g
        h.output = g
        h.explanation = ("Generic: " + " ".join(reasons)) if generic else (
            f"Specific: relies on {len(specific_facts)} verified prospect fact(s) and does not fit either decoy.")
    return g


# =========================================================================== orchestration helpers
async def _verify_then_generic(ctx: RunContext, run: Run, route: dict, d: Draft) -> tuple[Draft, dict]:
    swap = start_swap_test(ctx, run, d)  # runs concurrently with claim verification
    try:
        final, _ = await stage_verify(ctx, run, route, d)
    except BaseException:
        swap.cancel()
        raise
    if final.id != d.id:  # the draft was rewritten: the early swap test is about the old text
        swap.cancel()
        swap = None
    g = await stage_generic(ctx, run, final, pre=swap)
    return final, g


async def draft_verify_generic(ctx: RunContext, run: Run, route: dict, origin: str = "generated",
                               instruction: str | None = None) -> Draft:
    d = await stage_draft(ctx, run, route, origin=origin, instruction=instruction)
    d, g = await _verify_then_generic(ctx, run, route, d)
    if g["generic"]:
        if not ctx.can_afford(get_settings().min_s_for_regenerate):
            await add_warning(run.id, "Draft reads as generic; not regenerated because it could not finish within "
                                      "the 90-second limit. Use Regenerate if you want another version.")
            return d
        fb = ["The previous draft was too generic: " + " ".join(g["reasons"]),
              "Anchor the first sentence on the specific fact in the HOOK source and connect it to one problem."]
        d = await stage_draft(ctx, run, route, origin="genericness_retry", feedback=fb, instruction=instruction)
        d, g = await _verify_then_generic(ctx, run, route, d)
        if g["generic"]:
            await add_warning(run.id, "Draft still reads as generic after one regeneration.")
    return d


async def is_do_not_draft(dec: RouteDecision) -> bool:
    return dec.route == DO_NOT_DRAFT
