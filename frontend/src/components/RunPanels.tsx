import { AlertTriangle, BookOpen, CheckCircle2, CircleHelp, ExternalLink, Globe2, XCircle } from "lucide-react";
import { Check, RunDetail } from "../api";
import { host } from "../format";

const ICON: Record<Check["status"], JSX.Element> = {
  verified: <CheckCircle2 size={17} />,
  not_verified: <CircleHelp size={17} />,
  conflict: <AlertTriangle size={17} />,
  fictional: <BookOpen size={17} />,
};
const WORD: Record<Check["status"], string> = {
  verified: "Verified", not_verified: "Not verified", conflict: "Conflicting", fictional: "Fictional",
};

/** What the process could and could not verify - shown on every run, whatever the outcome. */
export function VerificationChecklist({ run }: { run: RunDetail }) {
  const checks: Check[] = run.identity?.checks || [];
  if (!checks.length) return null;
  const unverified = checks.filter((c) => c.status !== "verified").length;
  return (
    <div className="card checks">
      <div className="row-between">
        <h3>What we could verify</h3>
        <span className={`pill ${unverified ? "warn" : "ok"}`}>
          {unverified ? `${unverified} of ${checks.length} not verified` : "All checks verified"}</span>
      </div>
      <p className="small muted" style={{ marginTop: 2 }}>Only verified items are ever stated as fact in a draft. Anything else is left out.</p>
      <ul className="check-list">
        {checks.map((c) => (
          <li key={c.key} className={`check-item ${c.status}`}>
            <span className="check-icon" aria-hidden>{ICON[c.status]}</span>
            <div><div className="check-label">{c.label} <span className="check-word">{WORD[c.status]}</span></div>
              <div className="small muted">{c.detail}</div></div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Facts({ facts }: { facts: any[] }) {
  if (!facts?.length) return <p className="small muted">No verifiable sentence was found.</p>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      {facts.map((f, i) => (
        <div key={i}><div className="quote">“{f.quote}”</div>
          {f.url && <div style={{ paddingLeft: 12 }}><a className="src-link" href={f.url} target="_blank" rel="noreferrer">{host(f.url)} <ExternalLink size={11} /></a></div>}</div>
      ))}
    </div>
  );
}

/** A fictional character or company: never drafted; facts from fiction kept apart from the real world. */
export function FictionPanel({ run }: { run: RunDetail }) {
  const f = run.identity?.fiction;
  if (!f) return null;
  return (
    <div className="card stack">
      <div className="row"><span className="pill info"><BookOpen size={13} /> Fictional entity</span></div>
      <h2>This isn't a real prospect</h2>
      <p>Public sources describe {[f.person?.name, f.company?.name].filter(Boolean).join(" and ") || "this input"} as fictional.
        Nothing was drafted — outreach needs a real person at a real company.</p>
      <div className="split-2">
        <div className="card-sunken stack">
          <div className="row"><BookOpen size={15} /><strong>In fiction</strong><span className="chip">verified quotes</span></div>
          {f.person && <div className="stack" style={{ gap: 6 }}><div className="small"><strong>{f.person.name}</strong>
            {f.person.source ? ` — from ${f.person.source}` : ""}</div><Facts facts={f.person.facts} /></div>}
          {f.company && <div className="stack" style={{ gap: 6 }}><div className="small"><strong>{f.company.name}</strong>
            {f.company.source ? ` — from ${f.company.source}` : ""}</div><Facts facts={f.company.facts} /></div>}
        </div>
        <div className="card-sunken stack">
          <div className="row"><Globe2 size={15} /><strong>In the real world</strong></div>
          <p className="small">{f.real_world}</p>
          {f.real_companies?.map((c: any) => (
            <div key={c.name + c.what_it_does} className="stack" style={{ gap: 6 }}>
              <div className="small"><strong>{c.name}</strong>{c.what_it_does ? ` — ${c.what_it_does}` : ""} <span className="chip">real</span></div>
              <Facts facts={c.evidence} />
            </div>
          ))}
          <p className="small muted">If you meant a real person who shares this name, add their company and role (or paste a
            post in the notes) and run again.</p>
        </div>
      </div>
    </div>
  );
}

export function InterruptedPanel({ run, onRetry, onRerun, busy }: { run: RunDetail; onRetry: () => void; onRerun: () => void; busy: boolean }) {
  const i = run.interrupted;
  const timeout = run.status === "timed_out";
  return (
    <div className="card stack">
      <div className="row"><span className={`pill ${timeout ? "warn" : "neutral"}`}>
        {timeout ? <AlertTriangle size={13} /> : <XCircle size={13} />} {timeout ? "Time limit reached" : "Stopped"}</span></div>
      <h2>{timeout ? "This run hit the 90-second limit" : "You stopped this run"}</h2>
      <p>{i?.message}</p>
      <p className="small muted">Nothing after that point ran and no draft was produced. Everything found before then is kept below
        {run.signals.length ? ` (${run.signals.length} sources)` : ""}.
        {timeout ? " This usually means the AI provider was slow; trying again often works." : ""}</p>
      <div className="row">
        <button className="btn" disabled={busy} onClick={onRetry}>Resume from “{run.stages.find((x) => x.key === i?.stage)?.label || "the start"}”</button>
        <button className="btn secondary" disabled={busy} onClick={onRerun}>Start a fresh run</button>
      </div>
    </div>
  );
}
