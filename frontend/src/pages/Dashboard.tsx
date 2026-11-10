import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Database, Search } from "lucide-react";
import { api, RunSummary } from "../api";
import { useProfiles } from "../state";
import { EDGE_LABEL, fmtCost, fmtDate, fmtMs, fmtPct, OUTCOME_LABEL, outcomeTone, ROUTE_LABEL, routeTone, STATUS_LABEL, statusTone } from "../format";

export default function Dashboard() {
  const nav = useNavigate();
  const [m, setM] = useState<any>(null);
  const [rows, setRows] = useState<RunSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { profiles, current } = useProfiles();
  const [scope, setScope] = useState<string>("current");   // "current" | "all" | a profile id
  const [f, setF] = useState({ q: "", status: "", route: "", outcome: "", edge: "", mode: "" });
  const profileParam = scope === "current" ? current?.id || "" : scope;

  useEffect(() => {
    if (!profileParam) return;
    const ac = new AbortController();
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)), profile: profileParam }).toString();
      Promise.all([api(`/api/runs?limit=200&${qs}`, { signal: ac.signal }), api(`/api/metrics?profile=${profileParam}`, { signal: ac.signal })])
        .then(([r, mm]) => { setRows(r.items); setM(mm); setErr(null); }).catch((e) => { if (e?.name !== "AbortError") setErr(e.message); });
    }, 200);
    return () => { clearTimeout(t); ac.abort(); };
  }, [f, profileParam]);
  // refresh while something is running
  useEffect(() => {
    if (!rows?.some((r) => ["queued", "running"].includes(r.status))) return;
    const t = setInterval(() => setF((x) => ({ ...x })), 4000);
    return () => clearInterval(t);
  }, [rows]);

  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const edgeMax = m ? Math.max(1, ...Object.values(m.edge_cases as Record<string, number>)) : 1;
  const routeMax = m ? Math.max(1, ...Object.values(m.routes as Record<string, number>)) : 1;
  const filtered = Object.values(f).some(Boolean);

  return (
    <div className="stack-lg">
      <div className="row-between">
        <div><div className="eyebrow">{scope === "all" ? "Across all seller profiles" : `Runs for ${scope === "current" ? current?.name || "…" : profiles?.find((p) => p.id === scope)?.name}`}</div>
          <h1 style={{ marginTop: 6 }}>Dashboard</h1></div>
        <div className="row">
          <select className="select compact" value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Seller profile">
            <option value="current">Current profile{current ? ` (${current.name})` : ""}</option>
            <option value="all">All profiles</option>
            {profiles?.filter((p) => p.id !== current?.id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <Link to="/" className="btn">New run <ArrowRight size={16} className="arrow" /></Link>
        </div>
      </div>
      {err && <div className="callout bad">{err}</div>}

      <div className="tiles">
        {[
          ["Runs", m?.runs ?? "—", m ? `${m.statuses?.awaiting_review || 0} awaiting review` : ""],
          ["Approval rate", fmtPct(m?.approval_rate), m ? `${m.approved} of ${m.reviewed} reviewed` : ""],
          ["Edit rate", fmtPct(m?.edit_rate), m ? `${m.edited} approved after edits` : ""],
          ["Avg duration", fmtMs(m?.avg_duration_ms), m?.p90_duration_ms ? `p90 ${fmtMs(m.p90_duration_ms)} · active time` : "active processing time"],
          [`Within ${m?.budget_s ?? 90} s`, fmtPct(m?.within_budget_rate), m ? `${m.timed_out} timed out · ${m.cancelled} stopped` : ""],
          ["Failed", m?.failed ?? "—", m ? `${m.statuses?.awaiting_rep || 0} waiting on rep · ${fmtCost(m.total_cost_usd)} spent` : ""],
        ].map(([l, v, s]) => (
          <div key={l as string} className="tile"><div className="eyebrow">{l}</div><div className="metric">{m ? v : <span className="skeleton" style={{ display: "inline-block", width: 60, height: 24 }} />}</div><div className="small muted">{s}</div></div>
        ))}
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Edge-case routes taken</h3>
          <p className="small muted" style={{ marginBottom: 8 }}>How often each deliberate non-happy-path route was triggered.</p>
          {m && Object.keys(EDGE_LABEL).map((k) => (
            <div key={k} className="hbar"><span>{EDGE_LABEL[k]}</span><div className="track"><span style={{ width: `${((m.edge_cases[k] || 0) / edgeMax) * 100}%` }} /></div><span className="mono small">{m.edge_cases[k] || 0}</span></div>
          ))}
        </div>
        <div className="card">
          <h3>Final routes</h3>
          <p className="small muted" style={{ marginBottom: 8 }}>The routing rule's final decision per run.</p>
          {m && ["draft", "draft_with_warning", "do_not_draft"].map((k) => (
            <div key={k} className="hbar"><span>{ROUTE_LABEL[k]}</span><div className="track"><span style={{ width: `${((m.routes[k] || 0) / routeMax) * 100}%` }} /></div><span className="mono small">{m.routes[k] || 0}</span></div>
          ))}
        </div>
      </div>

      <div className="card stack">
        <div className="filters">
          <label style={{ position: "relative" }}><Search size={15} style={{ position: "absolute", left: 10, top: 12, color: "var(--ink-muted)" }} />
            <input className="input" style={{ paddingLeft: 32 }} placeholder="Search prospect or company" value={f.q} onChange={set("q")} aria-label="Search" /></label>
          <select className="select" value={f.status} onChange={set("status")} aria-label="Status"><option value="">Any status</option>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select className="select" value={f.route} onChange={set("route")} aria-label="Route"><option value="">Any route</option>{Object.entries(ROUTE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select className="select" value={f.outcome} onChange={set("outcome")} aria-label="Outcome"><option value="">Any outcome</option><option value="open">Open</option>{Object.entries(OUTCOME_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select className="select" value={f.edge} onChange={set("edge")} aria-label="Edge case"><option value="">Any edge case</option>{Object.entries(EDGE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select className="select" value={f.mode} onChange={set("mode")} aria-label="Mode"><option value="">Live + replay</option><option value="live">Live only</option><option value="replay">Replay only</option></select>
        </div>

        {rows === null ? <div className="stack">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 44 }} />)}</div> :
          rows.length === 0 ? (
            <div className="empty"><h3>{filtered ? "No runs match these filters" : "No runs yet"}</h3>
              <p>{filtered ? "Try clearing a filter." : "Start one from the New run page — or click a sample scenario."}</p>
              {!filtered && <Link to="/" className="btn" style={{ marginTop: 12 }}>Start a run</Link>}</div>
          ) : <>
            <div className="runs-table-wrap" style={{ overflowX: "auto" }}>
              <table className="runs">
                <thead><tr><th>Prospect</th><th>Status</th><th>Route</th><th>Outcome</th><th>Edge cases</th><th>Duration</th><th>Cost</th><th>Started</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => nav(`/runs/${r.id}`)}>
                      <td><Link to={`/runs/${r.id}`} style={{ textDecoration: "none" }} onClick={(e) => e.stopPropagation()}><strong style={{ fontWeight: 500 }}>{r.prospect.name}</strong></Link>
                        <div className="small muted">{r.prospect.company}{r.mode === "replay" && <> · <Database size={11} /> replay</>}{scope === "all" && r.seller_name ? <> · {r.seller_name}</> : null}</div></td>
                      <td><span className={`pill ${statusTone(r.status)}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                      <td>{r.route_path.length ? <span className={`pill ${routeTone(r.final_route)}`}>{r.route_path.map((x) => ROUTE_LABEL[x] || x).join(" → ")}</span> : <span className="muted">—</span>}</td>
                      <td>{r.outcome ? <span className={`pill ${outcomeTone(r.outcome)}`}>{OUTCOME_LABEL[r.outcome]}</span> : <span className="muted small">open</span>}</td>
                      <td><div className="row">{r.edge_cases.map((e) => <span key={e} className="chip">{EDGE_LABEL[e] || e}</span>)}</div></td>
                      <td className="mono small">{fmtMs(r.active_ms)}</td>
                      <td className="mono small">{fmtCost(r.cost_usd)}</td>
                      <td className="small muted">{fmtDate(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="run-cards">
              {rows.map((r) => (
                <Link key={r.id} to={`/runs/${r.id}`} className="card tight stack" style={{ textDecoration: "none", gap: 6 }}>
                  <div className="row-between"><strong style={{ fontWeight: 500 }}>{r.prospect.name}</strong><span className={`pill ${statusTone(r.status)}`}>{STATUS_LABEL[r.status]}</span></div>
                  <div className="small muted">{r.prospect.company} · {fmtDate(r.created_at)}</div>
                  <div className="row">{r.final_route && <span className={`pill ${routeTone(r.final_route)}`}>{ROUTE_LABEL[r.final_route]}</span>}
                    {r.outcome && <span className={`pill ${outcomeTone(r.outcome)}`}>{OUTCOME_LABEL[r.outcome]}</span>}
                    {r.edge_cases.map((e) => <span key={e} className="chip">{EDGE_LABEL[e]}</span>)}</div>
                  <div className="small mono muted">{fmtMs(r.active_ms)} · {fmtCost(r.cost_usd)}</div>
                </Link>
              ))}
            </div>
          </>}
      </div>
    </div>
  );
}
