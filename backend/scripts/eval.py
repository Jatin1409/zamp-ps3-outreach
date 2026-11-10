"""End-to-end evaluation against a running deployment (real search + real LLM).

    python -m scripts.eval --base https://<app>.onrender.com --code <ACCESS_CODE> [--record] [--only key1,key2]

For each sample scenario: starts a live run, waits (answering identity pauses the way the scenario specifies),
then reports route accuracy, edge-case accuracy, unsupported-claim count, genericness failures, duration and
cost. Writes docs/EVAL_RESULTS.md. With --record, also saves each run as fixtures/recorded/<key>.json so
integration tests can replay it offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenarios import SCENARIOS  # noqa: E402


class Retrying(httpx.Client):
    """The free Render instance occasionally resets a connection; retry any request a few times."""
    def request(self, *a, **kw):
        for i in range(5):
            try:
                return super().request(*a, **kw)
            except httpx.TransportError:
                if i == 4:
                    raise
                time.sleep(2 + 2 * i)


def wait(c: httpx.Client, rid: str, until: tuple[str, ...], timeout: float = 400) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = c.get(f"/api/runs/{rid}").json()
        except httpx.TransportError:   # slow poll on a small free instance: just try again
            time.sleep(3)
            continue
        if d["status"] in until:
            return d
        time.sleep(2)
    raise TimeoutError(f"run {rid} did not reach {until}")


def run_scenario(c: httpx.Client, sc: dict, mode: str) -> dict:
    t_start = time.time()
    r = c.post("/api/runs", json={"prospect": sc["prospect"], "mode": mode, "scenario_key": sc["key"]})
    r.raise_for_status()
    rid = r.json()["id"]
    d = wait(c, rid, ("awaiting_rep", "awaiting_review", "completed", "failed", "timed_out", "cancelled"))
    paused_as = d["pause"]["kind"] if d["status"] == "awaiting_rep" else None
    if d["status"] == "awaiting_rep":
        dec = dict(sc.get("eval_decision") or {"choice": "stop"})
        if dec.get("choice") == "pick" and dec.get("candidate") == "best":
            cands = d["pause"]["candidates"]
            dec = {"choice": "pick", "candidate_id": max(cands, key=lambda x: x["verified_sources"])["id"]}
        c.post(f"/api/runs/{rid}/decision", json=dec).raise_for_status()
        d = wait(c, rid, ("awaiting_review", "completed", "failed", "timed_out", "cancelled"))
    last = d["drafts"][-1] if d["drafts"] else None
    claims = (last or {}).get("verification", {}) or {}
    facts = [x for x in claims.get("claims", []) if x["kind"].endswith("fact")]
    return {
        "key": sc["key"], "title": sc["title"], "run_id": rid, "status": d["status"], "error": d["error"],
        "expected_route": sc["expected_route"], "route": d["final_route"], "route_path": d["route_path"],
        "expected_edge": sc.get("edge_case"), "edge_cases": d["edge_cases"], "paused_as": paused_as,
        "route_ok": d["final_route"] == sc["expected_route"],
        "edge_ok": (sc.get("edge_case") in d["edge_cases"]) if sc.get("edge_case") else not d["edge_cases"]
        or set(d["edge_cases"]) <= set(sc.get("allowed_extra_edges", [])),
        "factual_claims": len(facts), "unsupported": claims.get("unsupported"),
        "generic": (last or {}).get("genericness", {}).get("generic") if last else None,
        "regenerated_for_genericness": any(x["origin"] == "genericness_retry" for x in d["drafts"]),
        "rewrites": sum(1 for x in d["drafts"] if x["origin"] == "rewrite"),
        "words": (last or {}).get("lint", {}).get("word_count"),
        "duration_s": round(d["active_ms"] / 1000, 1), "cost_usd": d["cost_usd"],
        "interrupted": (d.get("interrupted") or {}).get("reason"),
        "wall_s": round(time.time() - t_start, 1),
        "tokens": d["tokens_in"] + d["tokens_out"], "subject": (last or {}).get("subject"),
        "body": (last or {}).get("body"), "mode": d["mode"],
        "hook": next((s["title"] for s in d["signals"] if last and s["sid"] == last["hook_signal_id"]), None),
    }


def write_report(rows: list[dict], base: str, path: Path):
    n = len(rows)
    ok = sum(r["route_ok"] for r in rows)
    eok = sum(r["edge_ok"] for r in rows)
    drafted = [r for r in rows if r["generic"] is not None]
    unsup = sum(r["unsupported"] or 0 for r in drafted)
    facts = sum(r["factual_claims"] for r in drafted)
    gen = sum(1 for r in drafted if r["generic"])
    regen = sum(1 for r in drafted if r["regenerated_for_genericness"])
    fails = [r for r in rows if r["status"] == "failed"]
    L = [f"# Evaluation results", "",
         f"Run on {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} against `{base}` "
         f"(live web search + live LLM, mode: {rows[0]['mode'] if rows else '-'}). Generated by `backend/scripts/eval.py`.",
         "", "## Summary", "",
         "| Metric | Result |", "|---|---|",
         f"| Route accuracy (final route = designed route) | **{ok}/{n}** |",
         f"| Edge-case detection (intended edge case flagged) | **{eok}/{n}** |",
         f"| Final drafts still flagged generic | **{gen}**/{len(drafted)} (regenerated once for genericness: {regen}) |",
         f"| Failed runs | {len(fails)} |",
         f"| Median duration (active) | {sorted(r['duration_s'] for r in rows)[n // 2] if rows else '-'} s |",
         f"| Average duration (active) | {sum(r['duration_s'] for r in rows) / max(n, 1):.1f} s |",
         f"| Slowest run (active) | {max((r['duration_s'] for r in rows), default=0):.1f} s |",
         f"| Finished within the 90 s budget | **{sum(1 for r in rows if r.get('duration_s', 0) <= 90 and not r.get('interrupted'))}/{n}** "
         f"(hit the limit: {sum(1 for r in rows if r.get('interrupted') == 'timeout')}) |",
         f"| Total cost | ${sum(r['cost_usd'] for r in rows):.3f} (avg ${sum(r['cost_usd'] for r in rows) / max(n, 1):.3f}/run) |",
         "", "## Per scenario", "",
         "| Scenario | Expected route | Actual route path | Edge case | Generic | Time | Cost | Run |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "✅" if r["route_ok"] and r["edge_ok"] else "❌"
        L.append(f"| {mark} {r['title']} | {r['expected_route']} | {' → '.join(r['route_path']) or r['status']} | "
                 f"{', '.join(r['edge_cases']) or '-'} | "
                 f"{'yes' if r['generic'] else 'no' if r['generic'] is not None else '-'} | {r['duration_s']} s | "
                 f"${r['cost_usd']:.3f} | `{r['run_id']}` |")
    L += ["", "## Drafts produced", ""]
    for r in rows:
        L.append(f"### {r['title']}")
        if r["error"]:
            L.append(f"Run failed: {r['error']}")
        if r["body"]:
            L += [f"Hook: {r['hook']}", "", "```", f"Subject: {r['subject']}", "", r["body"], "```"]
        elif not r["error"]:
            L.append(f"No draft (route `{r['route']}`) — by design.")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--mode", default="live", choices=["live", "replay"])
    ap.add_argument("--only", default="")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "EVAL_RESULTS.md"))
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    rows = []
    with Retrying(base_url=a.base.rstrip("/"), headers={"X-Access-Code": a.code}, timeout=120) as c:
        for sc in SCENARIOS:
            if only and sc["key"] not in only:
                continue
            print(f"-> {sc['key']} ...", flush=True)
            try:
                row = run_scenario(c, sc, a.mode)
            except Exception as e:  # noqa: BLE001
                row = {"key": sc["key"], "title": sc["title"], "run_id": "-", "status": "failed", "error": str(e),
                       "expected_route": sc["expected_route"], "route": None, "route_path": [], "edge_cases": [],
                       "route_ok": False, "edge_ok": False, "factual_claims": 0, "unsupported": None,
                       "generic": None, "regenerated_for_genericness": False, "duration_s": 0, "cost_usd": 0,
                       "body": None, "mode": a.mode, "expected_edge": sc.get("edge_case")}
            print(f"   route {row['route_path']} (expected {row['expected_route']}) edge {row['edge_cases']} "
                  f"unsupported {row['unsupported']} generic {row['generic']} {row['duration_s']}s ${row['cost_usd']:.3f}")
            rows.append(row)
            if a.record and row.get("run_id", "-") != "-" and row["status"] != "failed":
                fx = c.get(f"/api/runs/{row['run_id']}/fixture").json()
                out = ROOT / "backend" / "fixtures" / "recorded" / f"{sc['key']}.json"
                out.write_text(json.dumps(fx, indent=1, ensure_ascii=False), encoding="utf-8")
    write_report(rows, a.base, Path(a.out))
    (Path(a.out).with_suffix(".json")).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
