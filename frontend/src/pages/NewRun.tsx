import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, Database, FlaskConical, Globe, Info, Pencil } from "lucide-react";
import { api, post, Prospect, Scenario } from "../api";
import { EDGE_LABEL, ROUTE_LABEL, routeTone } from "../format";

const EMPTY: Prospect = { name: "", company: "", role: "", linkedin_url: "", notes: "" };
const STEPS = ["Confirm identity", "Gather signals", "Filter", "Rank", "Route", "Draft", "Verify", "Genericness", "You review"];

export default function NewRun() {
  const nav = useNavigate();
  const loc = useLocation() as any;
  const [p, setP] = useState<Prospect>({ ...EMPTY, ...(loc.state?.prospect || {}) });
  const [mode, setMode] = useState<"live" | "replay">("live");
  const [replayOk, setReplayOk] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [seller, setSeller] = useState<any>(null);

  useEffect(() => {
    api<Scenario[]>("/api/scenarios").then(setScenarios).catch(() => setScenarios([]));
    api("/api/seller-profile").then((r) => setSeller(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!p.name.trim() || !p.company.trim()) { setReplayOk(false); return; }
    const t = setTimeout(() => {
      api(`/api/replay-availability?name=${encodeURIComponent(p.name)}&company=${encodeURIComponent(p.company)}`)
        .then((r) => { setReplayOk(r.available); if (!r.available) setMode("live"); }).catch(() => setReplayOk(false));
    }, 350);
    return () => clearTimeout(t);
  }, [p.name, p.company]);

  const start = async (prospect: Prospect, m: "live" | "replay", scenario_key?: string) => {
    setErr(null); setBusy(scenario_key || "form");
    try {
      const clean = Object.fromEntries(Object.entries(prospect).map(([k, v]) => [k, typeof v === "string" && !v.trim() ? null : v]));
      const r = await post<{ id: string }>("/api/runs", { prospect: clean, mode: m, scenario_key });
      nav(`/runs/${r.id}`);
    } catch (e: any) { setErr(e.message); setBusy(null); }
  };

  const set = (k: keyof Prospect) => (e: any) => setP({ ...p, [k]: e.target.value });
  const valid = p.name.trim().length >= 2 && p.company.trim().length >= 1;

  return (
    <div className="stack-lg">
      <section className="hero">
        <div className="hero-art" aria-hidden>
          <div className="b" style={{ left: 40, top: 70, width: 140, height: 150, background: "var(--glow-lavender)" }} />
          <div className="b" style={{ left: 200, top: 40, width: 110, height: 120, background: "var(--glow-warm)" }} />
          <div className="b" style={{ left: 180, top: 180, width: 140, height: 110, background: "var(--glow-mist)" }} />
        </div>
        <div className="eyebrow">Zamp · AI Solutions Analyst case study · PS-3 personalised outreach</div>
        <h1 style={{ marginTop: 8 }}>From prospect to a draft worth reviewing — grounded in something real.</h1>
        <p className="lead">A rep names a B2B prospect. The process confirms who they are, researches public sources,
          picks the most relevant hook, writes a short email, and fact-checks every claim against its source. A human
          approves before anything goes anywhere — it cannot send email.</p>
        <div className="steps">{STEPS.map((s, i) => <span key={s} className="step-chip"><span className="mono">{String(i + 1).padStart(2, "0")}</span>{s}</span>)}</div>
        <div className="review-tip"><strong>Reviewing this submission?</strong> You play the sales rep. Start with a
          sample scenario — each is a real, live run chosen to trigger one route: the happy path, an outdated role,
          an ambiguous identity, thin signal, or sensitive news. Or enter any prospect of your own.</div>
      </section>

      <div className="grid-2">
        <form className="card stack" onSubmit={(e) => { e.preventDefault(); if (valid) start(p, mode); }}>
          <div className="row-between"><h2>New run</h2><span className="eyebrow">Required: name + company</span></div>
          <div className="field-row">
            <label className="field">Prospect name *<input className="input" value={p.name} onChange={set("name")} placeholder="e.g. Jane Roe" required minLength={2} /></label>
            <label className="field">Company *<input className="input" value={p.company} onChange={set("company")} placeholder="e.g. Acme Logistics" required /></label>
          </div>
          <div className="field-row">
            <label className="field">Role <span className="hint">Helps confirm identity and spot role changes</span><input className="input" value={p.role || ""} onChange={set("role")} placeholder="e.g. VP Finance" /></label>
            <label className="field">LinkedIn URL <span className="hint">Stored as a reference only — never fetched</span><input className="input" value={p.linkedin_url || ""} onChange={set("linkedin_url")} placeholder="https://www.linkedin.com/in/…" /></label>
          </div>
          <label className="field">Notes or a pasted post <span className="hint">Treated as a source (and checked for hidden instructions)</span>
            <textarea className="textarea" value={p.notes || ""} onChange={set("notes")} placeholder="Paste a LinkedIn post, something they said on a call, or context from your CRM." /></label>
          <div className="row-between">
            <div className="stack" style={{ gap: 6 }}>
              <span className="eyebrow">Search mode</span>
              <div className="seg" role="group" aria-label="Search mode">
                <button type="button" className={mode === "live" ? "on" : ""} onClick={() => setMode("live")}><Globe size={13} style={{ verticalAlign: -2 }} /> Live search</button>
                <button type="button" className={mode === "replay" ? "on" : ""} disabled={!replayOk} onClick={() => setMode("replay")}
                  title={replayOk ? "Reuse sources saved from an earlier live run" : "No saved sources for this prospect yet"}><Database size={13} style={{ verticalAlign: -2 }} /> Replay saved sources</button>
              </div>
            </div>
            <button className="btn" disabled={!valid || !!busy}>{busy === "form" ? "Starting…" : "Research & draft"} <ArrowRight size={16} className="arrow" /></button>
          </div>
          {mode === "replay" && <div className="mode-banner"><Database size={16} /> Replay mode: reuses search results saved from an earlier live run. AI steps still run live. The run will be labelled “Replay”.</div>}
          {err && <div className="callout bad"><Info size={16} /><div>{err}</div></div>}
        </form>

        <div className="stack">
          <div className="card stack">
            <div className="row-between"><h3>Sample scenarios</h3><span className="chip"><FlaskConical size={12} style={{ marginRight: 4 }} />real people · live pipeline</span></div>
            <p className="muted small">One click starts a real run on a public business figure. Each one was chosen to trigger a specific route.</p>
            {scenarios === null && [0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 64 }} />)}
            {scenarios?.length === 0 && <div className="callout neutral small">No sample scenarios configured.</div>}
            {scenarios?.map((s) => (
              <div key={s.key} className="scenario">
                <div className="row-between" style={{ width: "100%" }}>
                  <strong>{s.title}</strong>
                  <span className={`pill ${routeTone(s.expected_route)}`}>Expected: {ROUTE_LABEL[s.expected_route] || s.expected_route}</span>
                </div>
                <span className="small muted">{s.prospect.name} · {s.prospect.role ? `${s.prospect.role}, ` : ""}{s.prospect.company}</span>
                <span className="small">{s.description}</span>
                <div className="row-between" style={{ width: "100%" }}>
                  <div className="row">{s.edge_case && <span className="chip">{EDGE_LABEL[s.edge_case] || s.edge_case}</span>}</div>
                  <div className="row">
                    {s.replay_available && <button type="button" className="btn ghost sm" disabled={!!busy} onClick={() => start(s.prospect, "replay", s.key)}
                      title="Reuse saved sources if live search is down"><Database size={13} /> Replay</button>}
                    <button type="button" className="btn sm" disabled={!!busy} onClick={() => start(s.prospect, "live", s.key)}>
                      {busy === s.key ? "Starting…" : "Run live"} <ArrowRight size={14} className="arrow" /></button>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="card stack">
            <div className="row-between"><h3>Drafts are written for</h3><Link to="/seller" className="btn secondary sm"><Pencil size={13} /> Edit</Link></div>
            {!seller ? <div className="skeleton" style={{ height: 60 }} /> : (
              <>
                <p className="small"><strong>{seller.company_name}</strong> — {seller.product}</p>
                <p className="small muted">As the PS-3 brief asks, the seller is a clearly fictional AP-automation company,
                  so no real company's claims are put into drafts. Edit the profile to try a different seller — every
                  proof point can be quoted word-for-word, so keep them true.</p>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
