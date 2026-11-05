import { useState } from "react";
import { ExternalLink, ShieldAlert, Trophy } from "lucide-react";
import { RunDetail, SignalRow } from "../api";
import { fmtPub, host } from "../format";

const COMPS: [string, string][] = [["specificity", "Specific"], ["relevance", "Relevant"], ["recency", "Recent"], ["credibility", "Credible"]];

function Src({ s }: { s: SignalRow }) {
  if (s.url.startsWith("rep://")) return <span className="small muted">Your note</span>;
  return <a className="src-link" href={s.url} target="_blank" rel="noreferrer">{host(s.url)} <ExternalLink size={11} /></a>;
}

export default function SignalsPanel({ run }: { run: RunDetail }) {
  const [showDropped, setShowDropped] = useState(false);
  const rank = run.stages.find((s) => s.key === "rank")?.output;
  const route = run.stages.find((s) => s.key === "route")?.output;
  const by = Object.fromEntries(run.signals.map((s) => [s.sid, s]));
  const ranking: any[] = rank?.ranking || [];
  const explain = rank?.explain || {};
  const hookId = route?.hook || explain.winner;
  const dropped = run.signals.filter((s) => s.status !== "kept");
  const quarantined = dropped.filter((s) => s.status === "quarantined");

  return (
    <div className="stack">
      <div className="card">
        <div className="row-between"><h3>Signal ranking</h3><span className="small muted mono">{rank?.formula || "score = 0.30·specific + 0.30·relevant + 0.25·recent + 0.15·credible"}</span></div>
        <p className="small muted" style={{ marginTop: 4 }}>The AI judges specificity and relevance (0–3). Recency and credibility are computed from the date and source. The final score is computed in code.</p>
        {ranking.length === 0 && <div className="empty small">No signals were ranked for this run.</div>}
        <div style={{ marginTop: 8 }}>
          {ranking.map((r, i) => {
            const s = by[r.sid];
            if (!s) return null;
            const win = r.sid === hookId;
            const sens = s.sensitive?.category && s.sensitive.category !== "none";
            return (
              <div key={r.sid} className={`rank-row ${win ? "winner" : ""}`}>
                <div className="rank-n">{win ? <Trophy size={16} /> : `#${i + 1}`}</div>
                <div style={{ minWidth: 0 }}>
                  <div className="row"><strong style={{ fontWeight: 500 }}>{s.title || s.url}</strong></div>
                  <div className="row small muted" style={{ marginTop: 2 }}>
                    <Src s={s} /><span>· {fmtPub(s.published_date)}</span>
                    <span className="chip">{s.about === "person" ? "person" : "company"}</span>
                    {s.signal_type && <span className="chip">{s.signal_type.replace("_", " ")}</span>}
                    {sens && <span className="pill bad"><ShieldAlert size={12} /> {s.sensitive.category.replace("_", " ")}</span>}
                  </div>
                  {s.judgments?.key_fact && <div className="quote">“{s.judgments.key_fact}”{!s.judgments.key_fact_verified && <span className="pill bad" style={{ marginLeft: 6 }}>not found in source</span>}</div>}
                  <div className="bars">
                    {COMPS.map(([k, l]) => (
                      <div key={k}><div className="bar-label"><span>{l}</span><span className="mono">{(r[k] ?? 0).toFixed(2)}</span></div>
                        <div className="bar"><span style={{ width: `${Math.round((r[k] ?? 0) * 100)}%` }} /></div></div>
                    ))}
                  </div>
                  <div className="small" style={{ marginTop: 8 }}>
                    {win ? <><strong>Why it won:</strong> {explain.why_winner}</> :
                      <><span className="muted">Why it lost:</span> {explain.losers?.[r.sid] || r.ineligible_reason || "Lower score"}</>}
                  </div>
                  {s.judgments?.why && <div className="small muted">{s.judgments.why}</div>}
                </div>
                <div className="score">{r.total?.toFixed(2)}</div>
              </div>
            );
          })}
        </div>
        {(rank?.unscored?.length ?? 0) > 0 && (
          <div className="callout neutral small" style={{ marginTop: 12 }}>
            <div><strong>Not scored ({rank.unscored.length}).</strong> The model didn't return a judgement for these, even
              after being asked again, so they were left out of the ranking rather than given a made-up score:
              {rank.unscored.map((u: any) => <div key={u.sid} className="muted">• {u.title || u.url}</div>)}</div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="row-between">
          <h3>Filtered out <span className="muted small">({dropped.length})</span></h3>
          <button className="btn ghost sm" onClick={() => setShowDropped((v) => !v)}>{showDropped ? "Hide" : "Show"}</button>
        </div>
        {quarantined.length > 0 && <div className="callout bad small" style={{ marginTop: 8 }}><ShieldAlert size={14} /><div>
          {quarantined.length} source{quarantined.length > 1 ? "s" : ""} contained instructions aimed at an AI (possible prompt injection) and {quarantined.length > 1 ? "were" : "was"} quarantined — never shown to the drafting model.
          {quarantined.map((q) => <div key={q.sid} className="quote">{(q.sensitive?.injection_snippets || [])[0]}</div>)}</div></div>}
        {showDropped && <div style={{ marginTop: 8 }}>
          {dropped.map((s) => (
            <div key={s.sid} className="claim">
              <div className="mono small muted">{s.sid}</div>
              <div><div className="small">{s.title || s.url}</div>
                <div className="row small muted"><Src s={s} /><span>· {s.drop_reason}</span></div></div>
            </div>
          ))}
        </div>}
      </div>
    </div>
  );
}

export function IdentityPanel({ run }: { run: RunDetail }) {
  const id = run.identity;
  if (!id) return <div className="card empty small">Identity not resolved yet.</div>;
  const chosen = (id.candidate_details || []).find((c: any) => c.id === id.chosen);
  const tone = id.confidence === "high" ? "ok" : id.confidence === "medium" ? "info" : "warn";
  return (
    <div className="card stack">
      <div className="row-between"><h3>Identity</h3><span className={`pill ${tone}`}>{id.confidence} confidence</span></div>
      <dl className="kv">
        <dt>Status</dt><dd>{id.status.replace("_", " ")}</dd>
        {chosen && <><dt>Person</dt><dd>{chosen.full_name}</dd><dt>Role</dt><dd>{chosen.current_role || "—"}</dd><dt>Company</dt><dd>{chosen.current_company || "—"}</dd></>}
      </dl>
      <div className="stack" style={{ gap: 4 }}>{(id.reasons || []).map((r: string, i: number) => <div key={i} className="small">• {r}</div>)}</div>
      {run.rep_decisions?.length > 0 && <div className="callout neutral small"><div><strong>Your decision:</strong> {run.rep_decisions.map((d: any) =>
        d.choice === "pick" ? `chose ${d.candidate?.full_name} (${d.candidate?.current_role || "role unknown"})` :
        d.choice === "use_new" ? `continue with new details: ${d.new_role || ""} at ${d.new_company}` :
        d.choice === "keep_original" ? "keep original details" : "stop").join("; ")}. Original input kept: {run.prospect.role ? `${run.prospect.role}, ` : ""}{run.prospect.company}.</div></div>}
      {chosen?.evidence?.length > 0 && <div className="stack" style={{ gap: 6 }}>
        <div className="eyebrow">Evidence</div>
        {chosen.evidence.map((e: any, i: number) => <div key={i}><div className="quote">“{e.quote}”</div>
          {e.url && <div style={{ paddingLeft: 12 }}><a className="src-link" href={e.url} target="_blank" rel="noreferrer">{host(e.url)} <ExternalLink size={11} /></a> <span className={`pill ${e.verified ? "ok" : "bad"}`}>{e.verified ? "verified" : "not found"}</span></div>}</div>)}
      </div>}
      <div className="small muted">Searched: {(id.searched || []).join(" · ")}</div>
      {run.prospect.linkedin_url && <div className="small muted">LinkedIn URL provided by you (stored, not fetched): {run.prospect.linkedin_url}</div>}
    </div>
  );
}
