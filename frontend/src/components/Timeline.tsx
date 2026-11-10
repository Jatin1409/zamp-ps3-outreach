import { useEffect, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Circle, Hand, Loader2, Minus, Square, Timer, X } from "lucide-react";
import { StageRow } from "../api";
import { fmtCost, fmtMs, fmtNum, parseTs } from "../format";

const WHAT: Record<string, string> = {
  identity: "Confirms the person, company and current role from public sources.",
  gather: "Searches for funding, hiring, press, interviews, launches and company news.",
  filter: "Removes duplicates, stale items, off-topic results, personal details and injected text.",
  rank: "Scores each signal on specificity, relevance, recency and credibility.",
  route: "Applies fixed rules to decide: draft, draft with a warning, ask you, or don't draft.",
  draft: "Writes a short email (≤120 words) with one ask.",
  verify: "Checks every factual claim against the stored source text.",
  generic: "Tests whether the email would still make sense for a different prospect.",
  review: "You approve, edit, reject or regenerate. Nothing is sent.",
};

function Icon({ s }: { s: string }) {
  if (s === "succeeded") return <Check size={15} strokeWidth={2.5} />;
  if (s === "running") return <Loader2 size={15} className="spin" />;
  if (s === "failed") return <X size={15} strokeWidth={2.5} />;
  if (s === "waiting") return <Hand size={14} />;
  if (s === "skipped") return <Minus size={14} />;
  if (s === "cancelled") return <Square size={10} fill="currentColor" />;
  if (s === "timed_out") return <Timer size={14} />;
  return <Circle size={8} fill="currentColor" />;
}

function Elapsed({ since }: { since?: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(t); }, []);
  if (!since) return null;
  return <span className="mono small muted">{fmtMs(Math.max(0, now - parseTs(since).getTime()))}</span>;
}

export default function Timeline({ stages, progress }: { stages: StageRow[]; progress: Record<string, string> }) {
  return (
    <ol className="timeline">
      {stages.map((s) => {
        const done = s.status === "succeeded";
        return (
          <li key={s.key} className={`tl-item ${done ? "done" : ""} ${s.status}`}>
            <div className={`tl-icon ${s.status}`} aria-label={s.status}><Icon s={s.status} /></div>
            <div>
              <div className="tl-head">
                <span className="tl-title">{s.label}{(s.attempt || 1) > 1 && <span className="chip" style={{ marginLeft: 6 }}>attempt {s.attempt}</span>}</span>
                {s.status === "running" ? <Elapsed since={s.started_at} /> :
                  s.duration_ms != null && s.status !== "waiting" ? <span className="mono small muted">{fmtMs(s.duration_ms)}</span> :
                  s.status === "waiting" ? <span className="pill warn">waiting for you</span> : null}
              </div>
              <div className="tl-body">
                {s.status === "running" && <div className="live pulse">{progress[s.key] || "Working…"}</div>}
                {s.status === "pending" && <div>{WHAT[s.key]}</div>}
                {s.status === "failed" && <div className="callout bad small" style={{ marginTop: 4 }}><AlertTriangle size={14} /><div><strong>This stage failed.</strong> {s.error}</div></div>}
                {(s.status === "cancelled" || s.status === "timed_out") && <div className={`callout ${s.status === "cancelled" ? "neutral" : "warn"} small`} style={{ marginTop: 4 }}>
                  {s.status === "cancelled" ? <Square size={12} fill="currentColor" /> : <Timer size={14} />}<div>{s.error}</div></div>}
                {s.status !== "pending" && s.status !== "running" && s.explanation && !["failed", "cancelled", "timed_out"].includes(s.status) && <div style={{ color: s.status === "skipped" ? "var(--ink-muted)" : "var(--ink)" }}>{s.explanation}</div>}
                {["succeeded", "failed", "cancelled", "timed_out"].includes(s.status) && (
                  <details className="tech">
                    <summary><ChevronRight size={12} /> Technical details</summary>
                    <div className="small muted" style={{ marginTop: 4 }}>
                      {WHAT[s.key]} Tokens {fmtNum(s.tokens_in)} in / {fmtNum(s.tokens_out)} out · {fmtCost(s.cost_usd)}
                    </div>
                    {s.input && Object.keys(s.input).length > 0 && <><div className="eyebrow" style={{ marginTop: 8 }}>Input</div><pre className="json">{JSON.stringify(s.input, null, 1)}</pre></>}
                    <div className="eyebrow" style={{ marginTop: 8 }}>Output</div>
                    <pre className="json">{JSON.stringify(s.output, null, 1)}</pre>
                    {s.attempts?.length > 0 && <div className="small muted" style={{ marginTop: 6 }}>Earlier attempts: {s.attempts.map((a) => `#${a.attempt} ${a.status} (${fmtMs(a.duration_ms)})`).join(", ")}</div>}
                  </details>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
