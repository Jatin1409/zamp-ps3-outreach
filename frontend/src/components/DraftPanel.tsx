import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, CheckCircle2, Copy, ExternalLink, Pencil, RefreshCw, ShieldAlert, X, XCircle } from "lucide-react";
import { Claim, post, RunDetail } from "../api";
import { fmtPub, host } from "../format";

const KIND: Record<string, string> = {
  prospect_fact: "About the prospect", company_fact: "About the company", seller_fact: "About the seller",
  opinion_or_framing: "Framing (no source needed)", ask: "The ask",
};

export function ClaimList({ claims }: { claims: Claim[] }) {
  const facts = claims.filter((c) => ["prospect_fact", "company_fact", "seller_fact"].includes(c.kind));
  const other = claims.filter((c) => !facts.includes(c));
  return (
    <div>
      {facts.map((c, i) => (
        <div key={i} className="claim">
          <div style={{ color: c.supported ? "var(--positive)" : "var(--danger)" }}>{c.supported ? <CheckCircle2 size={18} /> : <XCircle size={18} />}</div>
          <div>
            <div>{c.claim}</div>
            <div className="row small muted" style={{ marginTop: 2 }}>
              <span className="chip">{KIND[c.kind]}</span>
              {c.supported ? <span style={{ color: "var(--positive)" }}>Verified against source</span> :
                <span style={{ color: "var(--danger)" }}>Unsupported: {c.problem}</span>}
            </div>
            {c.supporting_quote && <div className="quote">“{c.supporting_quote}”</div>}
            <div className="row small" style={{ marginTop: 4 }}>
              {c.url && !c.url.startsWith("rep://") ? <a className="src-link" href={c.url} target="_blank" rel="noreferrer">{c.title || host(c.url)} <ExternalLink size={11} /></a>
                : c.source_type === "seller_profile" ? <span className="muted">Source: {c.title}</span>
                : c.url?.startsWith("rep://") ? <span className="muted">Source: your note</span>
                : <span className="muted">No source</span>}
              {c.published && <span className="muted">· {fmtPub(c.published)}</span>}
            </div>
          </div>
        </div>
      ))}
      {other.length > 0 && <div className="small muted" style={{ marginTop: 8 }}>Not fact-checked (framing / ask): {other.map((c) => `“${c.claim}”`).join(" · ")}</div>}
    </div>
  );
}

export default function DraftPanel({ run, onDone }: { run: RunDetail; onDone: () => void }) {
  const d = run.drafts[run.drafts.length - 1];
  const lastEdit = [...run.reviews].reverse().find((r) => r.action === "edit" && r.draft_id === d?.id);
  const parse = (t: string) => { const [h, ...rest] = t.split("\n\n"); return { subject: h.replace(/^Subject:\s*/, ""), body: rest.join("\n\n") }; };
  const initial = lastEdit ? parse(lastEdit.final_text) : { subject: d?.subject || "", body: d?.body || "" };
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(initial.subject);
  const [body, setBody] = useState(initial.body);
  const [ack, setAck] = useState(false);
  const [ackIncomplete, setAckIncomplete] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [showRegen, setShowRegen] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => { setSubject(initial.subject); setBody(initial.body); setEditing(false); /* new draft */ // eslint-disable-next-line
  }, [d?.id]);

  const route = run.stages.find((s) => s.key === "route")?.output || {};
  const needsAck = route.sensitive_mode === "acknowledge";
  const sensSig = run.signals.find((s) => s.sid === route.sensitive_signal);
  const hook = run.signals.find((s) => s.sid === d?.hook_signal_id);
  const words = useMemo(() => (body.match(/\b[\w'’-]+\b/g) || []).length, [body]);
  const open = run.status === "awaiting_review";
  const changed = subject !== d?.subject || body !== d?.body;

  if (!d) return null;
  const act = async (action: string, extra: any = {}) => {
    setBusy(action); setErr(null);
    try {
      await post(`/api/runs/${run.id}/review`, { action, subject, body, acknowledged_sensitive: ack, acknowledged_incomplete: ackIncomplete, ...extra });
      if (action === "edit") setEditing(false);
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };
  const ver = d.verification;
  const incomplete = !!(run.interrupted?.incomplete?.length);
  const gen = d.genericness;
  const finalReview = [...run.reviews].reverse().find((r) => r.action === "approve" || r.action === "reject");

  return (
    <div className="stack">
      {run.personalisation === "company" && <div className="callout warn"><AlertTriangle size={16} /><div><strong>Lower personalisation.</strong> Nothing usable was found about {run.prospect.name} personally, so this draft uses a company-level signal.</div></div>}
      {needsAck && <div className="callout bad"><ShieldAlert size={16} /><div><strong>Sensitive situation.</strong> Recent news about {run.prospect.company} is sensitive ({sensSig?.sensitive?.category?.replace("_", " ")}{sensSig ? <>: <a href={sensSig.url} target="_blank" rel="noreferrer">{sensSig.title}</a></> : null}). It was not used as a hook and the tone was adapted. Read carefully before approving.</div></div>}
      {route.sensitive_mode === "avoided" && <div className="callout info"><ShieldAlert size={16} /><div>A sensitive item ({sensSig?.sensitive?.category?.replace("_", " ")}) was deliberately <strong>not</strong> used as the hook; the draft uses the best safe alternative.</div></div>}
      {incomplete && <div className="callout bad"><AlertTriangle size={16} /><div><strong>Incomplete draft.</strong> {run.interrupted?.message}{" "}
        The genericness check did not run. Regenerate it, or read it
        carefully before approving.</div></div>}
      {ver && ver.unsupported > 0 && <div className="callout bad"><XCircle size={16} /><div><strong>{ver.unsupported} claim{ver.unsupported > 1 ? "s" : ""} could not be verified</strong> after {ver.rounds} rewrite{ver.rounds === 1 ? "" : "s"}. Remove or fix {ver.unsupported > 1 ? "them" : "it"} before using this draft.</div></div>}
      {gen?.generic && <div className="callout warn"><AlertTriangle size={16} /><div><strong>Reads as generic.</strong> {gen.reasons.join(" ")}</div></div>}

      <div className="card stack">
        <div className="row-between">
          <div className="row"><h2>Draft</h2><span className="chip">v{d.version} · {d.origin.replace("_", " ")}</span></div>
          <div className="row">
            <span className="chip">{words} words</span>
            {ver && <span className={`pill ${ver.unsupported ? "bad" : "ok"}`}>{ver.unsupported ? "claims unverified" : "all claims verified"}</span>}
            {gen && <span className={`pill ${gen.generic ? "warn" : "ok"}`}>{gen.generic ? "generic" : "specific"}</span>}
          </div>
        </div>
        {editing ? (
          <div className="stack">
            <label className="field">Subject<input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
            <label className="field">Body <span className="hint">{words}/120 words · your edits are saved with the original kept for comparison.</span>
              <textarea className="textarea" style={{ minHeight: 240 }} value={body} onChange={(e) => setBody(e.target.value)} /></label>
          </div>
        ) : (
          <div className="email">
            <div className="email-head"><span className="eyebrow">Subject</span><strong>{subject}</strong></div>
            <div className="email-body">{body}</div>
          </div>
        )}
        {incomplete && open && (
          <label className="check callout neutral"><input type="checkbox" checked={ackIncomplete} onChange={(e) => setAckIncomplete(e.target.checked)} />
            <span>I understand this draft is incomplete and I've reviewed it myself.</span></label>
        )}
        {needsAck && open && (
          <label className="check callout neutral"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            <span>I've read the sensitive context and confirm the tone of this message is appropriate.</span></label>
        )}
        {err && <div className="callout bad small">{err}</div>}
        {open ? (
          <div className="row-between">
            <div className="row">
              {editing ? <>
                <button className="btn secondary sm" disabled={!!busy || !changed} onClick={() => act("edit")}><Check size={14} /> Save edits</button>
                <button className="btn ghost sm" onClick={() => { setSubject(initial.subject); setBody(initial.body); setEditing(false); }}>Cancel</button>
              </> : <button className="btn secondary sm" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</button>}
              <button className="btn secondary sm" disabled={!!busy} onClick={() => setShowRegen((v) => !v)}><RefreshCw size={14} /> Regenerate</button>
              <button className="btn danger sm" disabled={!!busy} onClick={() => act("reject")}><X size={14} /> Reject</button>
            </div>
            <button className="btn" disabled={!!busy || (needsAck && !ack) || (incomplete && !ackIncomplete)} onClick={() => act("approve")}>
              <Check size={16} /> {changed ? "Approve with edits" : "Approve"}</button>
          </div>
        ) : (
          <div className="row-between">
            <span className={`pill ${run.outcome === "rejected" ? "neutral" : "ok"}`}>
              {run.outcome === "approved" ? "Approved — ready to copy" : run.outcome === "approved_with_edits" ? "Approved with edits — ready to copy" : run.outcome === "rejected" ? "Rejected" : run.status}</span>
            {(run.outcome === "approved" || run.outcome === "approved_with_edits") &&
              <button className="btn sm" onClick={copy}><Copy size={14} /> {copied ? "Copied" : "Copy to clipboard"}</button>}
          </div>
        )}
        {showRegen && open && (
          <div className="card-sunken stack">
            <label className="field">Optional instruction for the new version <span className="hint">Style only — it can't add facts</span>
              <input className="input" value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="e.g. shorter, more formal, lead with the hiring news" /></label>
            <div><button className="btn sm" disabled={!!busy} onClick={() => act("regenerate", { instruction: instruction || null })}><RefreshCw size={14} /> Regenerate from the same evidence</button></div>
          </div>
        )}
        <p className="small muted">Approving only marks the draft as ready to copy. This tool never sends email.</p>
        {finalReview?.note && <p className="small">Note: {finalReview.note}</p>}
      </div>

      {hook && (
        <div className="card stack">
          <div className="eyebrow">Chosen hook</div>
          <h3>{hook.title}</h3>
          <div className="row small"><a className="src-link" href={hook.url.startsWith("rep://") ? undefined : hook.url} target="_blank" rel="noreferrer">{hook.url.startsWith("rep://") ? "Your note" : host(hook.url)} {!hook.url.startsWith("rep://") && <ExternalLink size={11} />}</a>
            <span className="muted">· {fmtPub(hook.published_date)} · {hook.about === "person" ? "about the person" : "about the company"}</span></div>
          {hook.judgments?.key_fact && <div className="quote">“{hook.judgments.key_fact}”</div>}
          {hook.judgments?.seller_problem && <div className="small"><strong>Connects to:</strong> {hook.judgments.seller_problem}</div>}
          {run.stages.find((s) => s.key === "rank")?.output?.explain?.why_winner && <div className="small muted">{run.stages.find((s) => s.key === "rank")!.output.explain.why_winner}</div>}
        </div>
      )}

      {ver && (
        <div className="card">
          <div className="row-between" style={{ marginBottom: 4 }}><h3>Fact check</h3>
            <span className="small muted">{ver.claims.filter((c) => c.supported && c.kind.endsWith("fact")).length} of {ver.claims.filter((c) => c.kind.endsWith("fact")).length} factual claims verified{ver.rounds ? ` · ${ver.rounds} rewrite${ver.rounds > 1 ? "s" : ""}` : ""}</span></div>
          <ClaimList claims={ver.claims} />
          {d.meta?.removed_claims?.length > 0 && <div className="small muted" style={{ marginTop: 8 }}>Removed during rewrite: {d.meta.removed_claims.map((c: string) => `“${c}”`).join(", ")}</div>}
        </div>
      )}
    </div>
  );
}
