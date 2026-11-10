import { useState } from "react";
import { ArrowRight, Building2, ExternalLink, Hand, StopCircle, Users, Shuffle } from "lucide-react";
import { post, RunDetail } from "../api";
import { fmtPub, host } from "../format";

function Evidence({ ev }: { ev: any[] }) {
  return (
    <div className="stack" style={{ gap: 8, marginTop: 8 }}>
      {ev.map((e, i) => (
        <div key={i}>
          <div className="quote">“{e.quote}”</div>
          <div className="row small" style={{ marginTop: 4, paddingLeft: 12 }}>
            {e.url ? <a className="src-link" href={e.url} target="_blank" rel="noreferrer">{host(e.url)} <ExternalLink size={11} /></a> : <span className="muted">no source</span>}
            <span className="muted">· {fmtPub(e.published)}</span>
            <span className={`pill ${e.verified ? "ok" : "bad"}`}>{e.verified ? "quote found in source" : "quote not found"}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DecisionPanel({ run, onDone }: { run: RunDetail; onDone: () => void }) {
  const pause = run.pause;
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const send = async (choice: string, candidate_id?: string) => {
    setBusy(true); setErr(null);
    try { await post(`/api/runs/${run.id}/decision`, { choice, candidate_id }); onDone(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  if (!pause) return null;

  if (pause.kind === "ambiguous_company") {
    return (
      <div className="card stack">
        <div className="row"><span className="pill warn"><Building2 size={13} /> Ambiguous company</span></div>
        <h2>Which “{run.prospect.company}” do you mean?</h2>
        <p className="muted">More than one organisation has this name. Researching the wrong one would put the wrong facts in the
          draft, so the run is paused until you choose.</p>
        <div className="stack">
          {pause.candidates.map((c: any) => (
            <label key={c.id} className={`cand ${sel === c.id ? "sel" : ""}`} style={{ cursor: "pointer" }}>
              <div className="row-between">
                <div className="row"><input type="radio" name="comp" checked={sel === c.id} onChange={() => setSel(c.id)} />
                  <strong>{c.name}</strong>{c.short_label && <span className="chip">{c.short_label}</span>}</div>
                <span className="chip">{c.verified_sources} verified source{c.verified_sources === 1 ? "" : "s"}</span>
              </div>
              {c.what_it_does && <div className="small" style={{ marginTop: 4 }}>{c.what_it_does}</div>}
              <Evidence ev={c.evidence} />
            </label>
          ))}
        </div>
        {err && <div className="callout bad small">{err}</div>}
        <div className="row-between">
          <button className="btn danger sm" disabled={busy} onClick={() => send("stop")}><StopCircle size={14} /> None of these — stop</button>
          <button className="btn" disabled={!sel || busy} onClick={() => send("pick_company", sel!)}>Research this company <ArrowRight size={16} className="arrow" /></button>
        </div>
      </div>
    );
  }

  if (pause.kind === "ambiguous") {
    return (
      <div className="card stack">
        <div className="row"><span className="pill warn"><Users size={13} /> Ambiguous identity</span></div>
        <h2>Which {run.prospect.name} do you mean?</h2>
        <p className="muted">More than one real person matches. Writing to the wrong one is worse than not writing, so the run is paused
          until you choose. Each candidate shows the evidence we found.</p>
        {pause.reasons?.map((r: string, i: number) => <div key={i} className="small muted">• {r}</div>)}
        <div className="stack">
          {pause.candidates.map((c: any) => (
            <label key={c.id} className={`cand ${sel === c.id ? "sel" : ""}`} style={{ cursor: "pointer" }}>
              <div className="row-between">
                <div className="row"><input type="radio" name="cand" checked={sel === c.id} onChange={() => setSel(c.id)} />
                  <strong>{c.full_name}</strong></div>
                <span className="chip">{c.verified_sources} verified source{c.verified_sources === 1 ? "" : "s"}</span>
              </div>
              <div className="small" style={{ marginTop: 4 }}>{c.current_role || "Role unknown"} · {c.current_company || "Company unknown"}</div>
              {c.distinguisher && <div className="small muted">{c.distinguisher}</div>}
              <Evidence ev={c.evidence} />
            </label>
          ))}
        </div>
        {err && <div className="callout bad small">{err}</div>}
        <div className="row-between">
          <button className="btn danger sm" disabled={busy} onClick={() => send("stop")}><StopCircle size={14} /> None of these — stop</button>
          <button className="btn" disabled={!sel || busy} onClick={() => send("pick", sel!)}>Continue with this person <ArrowRight size={16} className="arrow" /></button>
        </div>
      </div>
    );
  }

  const c = pause.candidates?.[0];
  return (
    <div className="card stack">
      <div className="row"><span className="pill warn"><Shuffle size={13} /> Outdated role</span></div>
      <h2>{run.prospect.name} may have moved on</h2>
      <p className="muted">What you entered doesn't match what recent sources say. We never overwrite your input silently — choose how to continue.</p>
      <div className="field-row">
        <div className="card-sunken">
          <div className="eyebrow">You entered</div>
          <div style={{ marginTop: 6 }}><strong>{pause.input.role || "No role given"}</strong></div>
          <div>{pause.input.company}</div>
        </div>
        <div className="card-sunken" style={{ background: "var(--warning-bg)" }}>
          <div className="eyebrow">Sources say</div>
          <div style={{ marginTop: 6 }}><strong>{c?.current_role || "Role unknown"}</strong></div>
          <div>{c?.current_company || "Company unknown"}</div>
        </div>
      </div>
      {pause.reasons?.map((r: string, i: number) => <div key={i} className="small">• {r}</div>)}
      {c && <><div className="eyebrow">Evidence</div><Evidence ev={c.evidence} /></>}
      {err && <div className="callout bad small">{err}</div>}
      <div className="stack" style={{ gap: 8 }}>
        <button className="btn" disabled={busy || !c?.current_company} onClick={() => send("use_new")}>
          Continue with the new details{c?.current_company ? ` (${c.current_company})` : ""} <ArrowRight size={16} className="arrow" /></button>
        <div className="row">
          <button className="btn secondary sm" disabled={busy} onClick={() => send("keep_original")}><Hand size={14} /> Keep my original details (draft will carry a warning)</button>
          <button className="btn danger sm" disabled={busy} onClick={() => send("stop")}><StopCircle size={14} /> Stop this run</button>
        </div>
      </div>
    </div>
  );
}
