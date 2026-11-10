"""Stage 1 - who (and which company) is this? Person + company resolution, fictional-entity detection, and an
explicit checklist of what could and could not be verified. The model reads; code decides (routing.py)."""
from __future__ import annotations

from dataclasses import asdict

from rapidfuzz import fuzz

from ..models import Run
from ..providers.search import SearchQuery
from . import prompts as P
from .context import RunContext, update_run
from .injection import detect_injection
from .llm_schemas import IdentityExtraction
from .routing import CandidateView, CompanyView, decide_company, decide_identity, identity_gate
from .textmatch import company_matches, normalize, quote_in_text


def _verify_evidence(evidence, by_idx: dict) -> tuple[set, list]:
    from .stages import canonical_url

    urls, out = set(), []
    for ev in evidence:
        src = by_idx.get(str(ev.source))
        ok = bool(src) and quote_in_text(ev.quote, src["_full"])[0]
        if ok:
            urls.add(canonical_url(src["url"]))
        out.append({"quote": ev.quote, "url": src["url"] if src else None, "title": src["title"] if src else None,
                    "published": src.get("published") if src else None, "verified": ok})
    return urls, out


def build_checks(p: dict, person: dict, company: dict, chosen: dict | None, chosen_co: dict | None) -> list[dict]:
    """What we could and could not verify - shown to the rep on every run, whatever the route."""
    checks = []
    ps = person["status"]
    if ps == "fictional":
        checks.append({"key": "person", "label": f"{p['name']} is a real person", "status": "fictional",
                       "detail": "Sources describe this name as a fictional character."})
    elif ps == "not_found":
        checks.append({"key": "person", "label": f"{p['name']} found in public sources", "status": "not_verified",
                       "detail": "No public source links this name to this company."})
    elif ps == "ambiguous":
        checks.append({"key": "person", "label": f"Which {p['name']}", "status": "conflict",
                       "detail": f"{len(person.get('candidates', []))} different people match."})
    else:
        n = (chosen or {}).get("verified_sources", 0)
        checks.append({"key": "person", "label": f"{p['name']} found in public sources", "status": "verified",
                       "detail": f"{n} source(s) with quotes checked against the page text."})
    if ps in ("resolved", "role_conflict") and chosen:
        at = chosen.get("at_input_company")
        checks.append({"key": "employer", "label": f"Currently at {p['company']}",
                       "status": {"current": "verified", "former": "conflict", "never": "conflict"}.get(at,
                                                                                                  "not_verified"),
                       "detail": {"current": "Sources say so.", "former": f"Sources say they left; now at "
                                  f"{chosen.get('current_company') or 'unknown'}.",
                                  "never": f"Sources place them at {chosen.get('current_company') or 'another company'}."
                                  }.get(at, "Sources don't say whether this is current.")})
        if p.get("role"):
            rm = chosen.get("role_matches_input")
            checks.append({"key": "role", "label": f"Role: {p['role']}",
                           "status": {"yes": "verified", "no": "conflict"}.get(rm, "not_verified"),
                           "detail": {"yes": f"Sources say {chosen.get('current_role') or p['role']}.",
                                      "no": f"Sources say {chosen.get('current_role')}."}.get(
                               rm, "No source states the role.")})
    elif p.get("role") and ps == "not_found":
        checks.append({"key": "role", "label": f"Role: {p['role']}", "status": "not_verified",
                       "detail": "Can't be checked without finding the person."})
    cs = company["status"]
    if cs != "unchecked":
        checks.append({"key": "company", "label": f"Company: {p['company']}",
                       "status": {"found": "verified", "not_found": "not_verified", "ambiguous": "conflict",
                                  "fictional": "fictional"}[cs],
                       "detail": ((("Fictional: " if cs == "fictional" else "") + f"{chosen_co['name']} - "
                                   f"{chosen_co['what_it_does']}") if chosen_co and chosen_co.get("what_it_does")
                                  else " ".join(company.get("reasons", [])))})
    return checks


async def stage_identity(ctx: RunContext, run: Run) -> dict:
    from .stages import blocked, canonical_url, focus_text, full_text, name_terms, q_safe

    p = run.prospect
    async with ctx.stage("identity", {"prospect": p}) as h:
        nm, co, rl = q_safe(p["name"]), q_safe(p["company"]), q_safe(p.get("role"))
        queries = [SearchQuery(f'"{nm}" "{co}"', max_results=6)]
        if rl:
            queries.append(SearchQuery(f'"{nm}" {rl} {co}', max_results=5))
        queries.append(SearchQuery(f'"{nm}" {rl} joins OR appointed OR former'.replace("  ", " "), max_results=5))
        company_q = SearchQuery(f'"{co}" company OR headquarters OR founded OR "about us"', max_results=5)
        queries.append(company_q)
        results = await ctx.search_many(h, queries)
        sources: list[dict] = []
        seen: set[str] = set()
        terms = name_terms(p["name"]) + [p["company"]]
        for q, rs in zip(queries, results):
            for r in rs:
                cu = canonical_url(r.url)
                if cu in seen or blocked(r.url):
                    continue
                seen.add(cu)
                sources.append({"id": str(len(sources)), "url": r.url, "title": r.title,
                                "published": r.published_date, "text": focus_text(r, terms), "_full": full_text(r)})
        # In replay mode an old recording may predate the company search: then the company is "not checked",
        # rather than wrongly "not found".
        company_searched = not (ctx.mode == "replay" and company_q.query in getattr(ctx.search, "misses", []))
        h.output["sources"] = [{k: v for k, v in s.items() if k != "_full"} for s in sources]
        clean = [s for s in sources if not detect_injection(s["_full"])]
        quarantined = [s["url"] for s in sources if s not in clean]
        if quarantined:
            h.output["quarantined"] = quarantined
        if not clean:
            ext = IdentityExtraction(candidates=[], companies=[], conflicting_sources=False,
                                     summary="No sources found.")
        else:
            await h.progress(f"Reading {len(clean)} sources to identify the person and the company")
            res = await ctx.llm_call(h, purpose="identity", system=P.IDENTITY_SYSTEM,
                                     user=P.identity_user(p, clean), schema=IdentityExtraction, tier="fast",
                                     max_wait=ctx.research_wait())
            ext = res.data
        by_idx = {s["id"]: s for s in clean}

        # ---- people
        views, cand_out = [], []
        for i, c in enumerate(ext.candidates):
            urls, ev_out = _verify_evidence(c.evidence, by_idx)
            name_ok = fuzz.token_set_ratio(normalize(c.full_name), normalize(p["name"])) >= 85
            cid = f"c{i + 1}"
            at_input, note = c.at_input_company, None
            company_ok = not c.current_company or company_matches(p["company"], c.current_company)
            if at_input == "current" and not company_ok and not c.is_fictional:
                # model output contradicts itself ("current at X" but current_company = Y): trust neither
                at_input = "unclear"
                note = (f"Sources were read as both 'currently at {p['company']}' and 'currently at "
                        f"{c.current_company}' - treated as unclear.")
            views.append(CandidateView(cid, c.full_name, c.current_company, c.current_role, at_input,
                                       c.role_matches_input, len(urls), name_ok, c.is_fictional))
            cand_out.append({"id": cid, **c.model_dump(exclude={"evidence"}), "at_input_company": at_input,
                             "evidence": ev_out, "verified_sources": len(urls), "name_matches": name_ok,
                             "consistency_note": note})
        dec = decide_identity(views, bool(p.get("role")), ext.conflicting_sources)
        dec.reasons.extend(c["consistency_note"] for c in cand_out
                           if c["consistency_note"] and c["id"] == dec.chosen)

        # ---- companies
        comp_views, comp_out = None, []
        if ext.companies is None and company_searched and not _company_mentioned(p["company"], clean):
            # the model skipped the company list; code can still tell when no source even names the company
            ext.companies = []
            h.output["company_decided_by"] = "code: no retrieved source mentions the company name"
        if ext.companies is not None and company_searched:
            comp_views = []
            for i, co in enumerate(ext.companies):
                urls, ev_out = _verify_evidence(co.evidence, by_idx)
                kid = f"k{i + 1}"
                ok = company_matches(p["company"], co.name)
                comp_views.append(CompanyView(kid, co.name, co.short_label, len(urls), ok, co.is_fictional))
                comp_out.append({"id": kid, **co.model_dump(exclude={"evidence"}), "evidence": ev_out,
                                 "verified_sources": len(urls), "name_matches": ok})
        cdec = decide_company(comp_views, dec.status)
        gate = identity_gate(dec, cdec)
        if gate == "nothing_found" and (p.get("notes") or "").strip():
            gate = "not_found"
            dec.reasons.append("Public sources found neither the person nor the company, but you added notes - "
                               "drafting from your notes only, with low confidence.")

        chosen = next((c for c in cand_out if c["id"] == dec.chosen), None)
        chosen_co = next((c for c in comp_out if c["id"] == cdec.chosen), None)
        person = asdict(dec)
        company = {**asdict(cdec), "details": comp_out}
        fiction = None
        if gate == "fictional":
            fc = next((c for c in cand_out if c.get("is_fictional") and c["verified_sources"]), None)
            fk = next((c for c in comp_out if c.get("is_fictional") and c["verified_sources"]), None)
            real_people = [c for c in cand_out if not c.get("is_fictional") and c["verified_sources"]
                           and c["name_matches"]]
            real_cos = [c for c in comp_out if not c.get("is_fictional") and c["verified_sources"]
                        and c["name_matches"]]
            # Say exactly what does exist in the real world - e.g. real companies that share a fictional name.
            parts = []
            if real_cos:
                parts.append(f"Real organisations called {p['company']} do exist ("
                             + "; ".join(f"{c['name']}: {c.get('what_it_does') or c.get('short_label') or 'no description'}"
                                         for c in real_cos[:3]) + f"), but no real {p['name']} was found at them.")
            if real_people:
                parts.append("Real people with this name were found elsewhere: "
                             + "; ".join(f"{c['full_name']} ({c.get('current_role') or 'role unknown'}, "
                                         f"{c.get('current_company') or 'company unknown'})" for c in real_people[:3])
                             + ".")
            if not parts:
                parts.append("No real person or company matching this input was found in public sources.")
            fiction = {
                "person": ({"name": fc["full_name"], "source": fc.get("fiction_source"),
                            "facts": [e for e in fc["evidence"] if e["verified"]]} if fc else None),
                "company": ({"name": fk["name"], "source": fk.get("fiction_source"),
                             "facts": [e for e in fk["evidence"] if e["verified"]]} if fk else None),
                "real_world": " ".join(parts),
                "real_companies": [{"name": c["name"], "what_it_does": c.get("what_it_does"),
                                    "evidence": [e for e in c["evidence"] if e["verified"]]} for c in real_cos[:3]],
            }
        identity = {**person, "status": gate, "person_status": dec.status, "company": company,
                    "candidate_details": cand_out, "summary": ext.summary, "fiction": fiction,
                    "checks": build_checks(p, person, company, chosen, chosen_co),
                    "searched": [q.query for q in queries]}
        if gate == "ambiguous_company":
            identity["confidence"] = "low"
            identity["reasons"] = dec.reasons + cdec.reasons
        h.output.update({"decision": person, "company": company, "gate": gate, "candidates": cand_out})
        h.explanation = {
            "resolved": f"Confirmed {chosen['full_name'] if chosen else p['name']} at {p['company']}"
                        f" ({dec.confidence} confidence). " + " ".join(dec.reasons[-2:]),
            "ambiguous": "Found more than one plausible person. Pausing so you can choose the right one.",
            "ambiguous_company": "More than one organisation has this name. Pausing so you can choose which one.",
            "role_conflict": "Sources suggest this person's company or role has changed. Pausing for your decision.",
            "not_found": "Could not find this person in public sources. Continuing with company-level research only; "
                         "nothing about the person will be stated as fact.",
            "nothing_found": "Neither the person nor the company could be found in public sources. Stopping here "
                             "rather than inventing anything.",
            "fictional": "This is a fictional character or company, not a real prospect. Nothing will be drafted.",
        }[gate]
    await update_run(run.id, identity=identity)
    return identity


def _company_mentioned(company: str, sources: list[dict]) -> bool:
    """True if any source text contains every distinctive word of the company name (legal suffixes ignored)."""
    from .textmatch import company_tokens, normalize

    toks = company_tokens(company)
    if not toks:
        return True
    return any(all(t in normalize(s.get("_full") or s.get("text") or "") for t in toks) for s in sources)
