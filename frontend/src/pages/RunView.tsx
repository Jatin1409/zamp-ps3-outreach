import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Ban, Database, Loader2, RotateCcw, Square, Timer, Wifi, WifiOff } from "lucide-react";
import { post, RunDetail } from "../api";
import { ACTIVE, parseTs, EDGE_LABEL, fmtCost, fmtDate, fmtMs, fmtNum, OUTCOME_LABEL, ROUTE_LABEL, routeTone, STATUS_LABEL, statusTone, STOPPABLE } from "../format";
import { useRun } from "../useRun";
import { useToast } from "../state";
import Timeline from "../components/Timeline";
import DecisionPanel from "../components/DecisionPanel";
import DraftPanel from "../components/DraftPanel";
import SignalsPanel, { IdentityPanel } from "../components/SignalsPanel";
import { FictionPanel, InterruptedPanel, VerificationChecklist } from "../components/RunPanels";

const BUDGET_S = 90;

/** Active seconds used so far: finished stage time + the running stage's elapsed time. */
function useElapsed(run: RunDetail) {
  const [now, setNow] = useState(Date.now());
  const running = run.stages.find((s) => s.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [running?.key, running?.started_at]);
  const live = running?.started_at ? Math.max(0, now - parseTs(running.started_at).getTime()) : 0;
  return (run.active_ms || 0) + live;
}

function BudgetMeter({ run }: { run: RunDetail }) {
  const ms = useElapsed(run);
  const s = ms / 1000;
  const pct = Math.min(100, (s / BUDGET_S) * 100);
  const tone = s > BUDGET_S * 0.85 ? "warn" : "";
  return (
    <div className="budget" title={`Each run has a ${BUDGET_S}-second limit on active processing time (time spent waiting for you doesn't count).`}>
      <div className="budget-row"><span className="eyebrow"><Timer size={11} /> Time used</span><span className="mono small">{s.toFixed(0)} / {BUDGET_S} s</span></div>
      <div className={`budget-bar ${tone}`}><span style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function Header({ run, connected, onStop, stopping, onRerun, rerunning }: {
  run: RunDetail; connected: boolean; onStop: () => void; stopping: boolean; onRerun: () => void; rerunning: boolean;
}) {
  const p = run.prospect;
  const live = ACTIVE.includes(run.status);
  const stoppable = STOPPABLE.includes(run.status);
  return (
    <div className="card stack run-head">
      <div className="row-between">
        <Link to="/dashboard" className="btn ghost sm"><ArrowLeft size={14} /> All runs</Link>
        <div className="row">
          {run.mode === "replay" ? <span className="pill info"><Database size={12} /> Replay · saved sources</span> : <span className="pill outline"><span className="dot" style={{ color: "var(--positive)" }} /> Live search</span>}
          {live && <span className="pill outline small" title={connected ? "Receiving live updates" : "Reconnecting — polling instead"}>{connected ? <Wifi size={12} /> : <WifiOff size={12} />} {connected ? "live" : "polling"}</span>}
        </div>
      </div>
      <div className="row-between" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Run {run.id} · {fmtDate(run.created_at)}{run.seller_name ? ` · selling as ${run.seller_name}` : ""}</div>
          <h1 style={{ marginTop: 6 }}>{p.name}</h1>
          <div className="muted">{p.role ? `${p.role} · ` : ""}{p.company}</div>
        </div>
        <div className="row">
          <span className={`pill ${statusTone(run.status)}`}>{live && <Loader2 size={12} className="spin" />}{STATUS_LABEL[run.status] || run.status}</span>
          {run.final_route && <span className={`pill ${routeTone(run.final_route)}`}>Route: {run.route_path.map((r) => ROUTE_LABEL[r] || r).join(" → ")}</span>}
          {run.outcome && OUTCOME_LABEL[run.outcome] !== STATUS_LABEL[run.status] && <span className="pill outline">{OUTCOME_LABEL[run.outcome] || run.outcome}</span>}
          {stoppable && <button className="btn stop" onClick={onStop} disabled={stopping} aria-label="Stop this run">
            {stopping ? <Loader2 size={15} className="spin" /> : <Square size={13} fill="currentColor" />} {stopping ? "Stopping…" : "Stop run"}</button>}
          {!stoppable && <button className="btn secondary sm" onClick={onRerun} disabled={rerunning}><RotateCcw size={14} /> Run again</button>}
        </div>
      </div>
      <div className="meta-strip">
        <div><span className="eyebrow">Duration</span><span className="v">{fmtMs(run.active_ms)}</span></div>
        <div><span className="eyebrow">Est. cost</span><span className="v">{fmtCost(run.cost_usd)}</span></div>
        <div><span className="eyebrow">Tokens</span><span className="v">{fmtNum(run.tokens_in + run.tokens_out)}</span></div>
        <div><span className="eyebrow">AI calls</span><span className="v">{run.llm_calls}</span></div>
        <div><span className="eyebrow">Searches</span><span className="v">{run.search_calls}</span></div>
        {run.identity_confidence && <div><span className="eyebrow">Identity</span><span className="v">{run.identity_confidence}</span></div>}
        {run.personalisation && <div><span className="eyebrow">Personalisation</span><span className="v">{run.personalisation}</span></div>}
        {(live || run.status === "awaiting_rep") && <BudgetMeter run={run} />}
      </div>
      {run.edge_cases.length > 0 && <div className="row">{run.edge_cases.map((e) => <span key={e} className="pill warn">{EDGE_LABEL[e] || e}</span>)}</div>}
      {run.mode === "replay" && <div className="mode-banner"><Database size={16} /> Replay mode: search results reused from saved sources{run.replay_source_run_id ? <> of run <Link to={`/runs/${run.replay_source_run_id}`}>{run.replay_source_run_id}</Link></> : ""}. The AI steps ran live.</div>}
    </div>
  );
}

function FailedPanel({ run, onDone }: { run: RunDetail; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const failed = run.stages.find((s) => s.status === "failed");
  const searchFail = (failed?.error || run.error || "").toLowerCase().includes("search");
  const retry = async (mode?: string) => {
    setBusy(true); setErr(null);
    try { await post(`/api/runs/${run.id}/retry`, { mode }); onDone(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="card stack">
      <div className="row"><span className="pill bad"><AlertTriangle size={13} /> Run failed</span></div>
      <h2>{failed ? `“${failed.label}” failed` : "This run failed"}</h2>
      <p>{failed?.error || run.error}</p>
      <p className="small muted">Nothing after the failed stage ran{run.drafts.length ? ", so the draft below is unfinished and cannot be approved" : ", and no draft was produced"}. {searchFail ? "If live search is down, you can retry using sources saved from an earlier live run of this prospect." : ""}</p>
      {err && <div className="callout bad small">{err}</div>}
      <div className="row">
        <button className="btn" disabled={busy} onClick={() => retry()}><RotateCcw size={15} /> Retry{run.mode === "replay" ? "" : " live"}</button>
        {run.mode !== "replay" && <button className="btn secondary" disabled={busy} onClick={() => retry("replay")}><Database size={15} /> Retry in replay mode</button>}
      </div>
    </div>
  );
}

function NotDrafted({ run }: { run: RunDetail }) {
  const nav = useNavigate();
  const route = run.stages.find((s) => s.key === "route")?.output || {};
  const stopped = run.outcome === "stopped_by_rep";
  const nothing = run.identity?.status === "nothing_found";
  return (
    <div className="card stack">
      <div className="row"><span className={`pill ${stopped ? "neutral" : "bad"}`}><Ban size={13} /> {stopped ? "Stopped by you" : "Do not send"}</span></div>
      <h2>{stopped ? "You stopped this run" : nothing ? "Couldn't find this person or company" : "Recommend not sending anything yet"}</h2>
      <p>{stopped ? "No research or drafting happened after your decision." : route.reason}</p>
      {!stopped && route.missing?.length > 0 && <div className="card-sunken stack" style={{ gap: 6 }}>
        <div className="eyebrow">{nothing ? "What we checked" : "What's missing"}</div>
        {route.missing.map((m: string, i: number) => <div key={i} className="small">• {m}</div>)}
      </div>}
      {!stopped && <p className="small muted">Rather than invent a reason to reach out, the process stops here. If you know something about this person — a post, a talk, a conversation — add it as a note and run again.</p>}
      <div><button className="btn secondary" onClick={() => nav("/", { state: { prospect: run.prospect } })}>Add notes and run again</button></div>
    </div>
  );
}

export default function RunView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { run, error, progress, reload, connected } = useRun(id);
  const [tab, setTab] = useState("draft");
  const [stopping, setStopping] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (run && !run.drafts.length && tab === "draft") setTab("signals");
    if (run && run.drafts.length && tab === "signals" && run.status === "awaiting_review") setTab("draft");
    // eslint-disable-next-line
  }, [run?.drafts.length, run?.status]);
  useEffect(() => { if (run && !STOPPABLE.includes(run.status)) setStopping(false); }, [run?.status]);

  if (error && !run) return <div className="card empty"><h3>Couldn't load this run</h3><p>{error}</p><Link to="/dashboard">Back to dashboard</Link></div>;
  if (!run) return <div className="stack-lg"><div className="skeleton" style={{ height: 160 }} /><div className="grid-run"><div className="skeleton" style={{ height: 420 }} /><div className="skeleton" style={{ height: 420 }} /></div></div>;

  const stop = async () => {
    setStopping(true);
    try {
      const r = await post<{ status: string }>(`/api/runs/${run.id}/cancel`, {});
      toast(r.status === "cancelled" ? "Run stopped" : "Stopping the run…", "info");
      reload();
    } catch (e: any) { toast(e.message, "bad"); setStopping(false); }
  };
  const rerun = async () => {
    setBusy(true);
    try { const r = await post<{ id: string }>(`/api/runs/${run.id}/rerun`, {}); toast("Started a fresh run"); nav(`/runs/${r.id}`); }
    catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  const retry = async () => {
    setBusy(true);
    try { await post(`/api/runs/${run.id}/retry`, {}); reload(); } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };

  const working = ACTIVE.includes(run.status);
  const interrupted = run.status === "cancelled" || run.status === "timed_out";
  const hasResults = run.signals.length > 0 || run.drafts.length > 0;
  return (
    <div className="page stack-lg">
      <Header run={run} connected={connected} onStop={stop} stopping={stopping} onRerun={rerun} rerunning={busy} />
      {run.warnings.length > 0 && run.status !== "failed" && (
        <div className="callout warn"><AlertTriangle size={16} /><div><strong>Warnings</strong>{run.warnings.map((w, i) => <div key={i} className="small">• {w}</div>)}</div></div>
      )}
      <div className="grid-run">
        <div className="card sticky-col">
          <div className="row-between" style={{ marginBottom: 12 }}><h3>Pipeline</h3>{run.route_reason && <span className="chip" title={run.route_reason}>{run.route_reason.split(":")[0]}</span>}</div>
          <Timeline stages={run.stages} progress={progress} />
        </div>
        <div className="stack">
          {run.status === "awaiting_rep" && <DecisionPanel run={run} onDone={reload} />}
          {run.status === "failed" && <FailedPanel run={run} onDone={reload} />}
          {interrupted && <InterruptedPanel run={run} onRetry={retry} onRerun={rerun} busy={busy} />}
          {run.identity?.status === "fictional" && run.status === "completed" && <FictionPanel run={run} />}
          {run.final_route === "do_not_draft" && run.status === "completed" && run.identity?.status !== "fictional" && <NotDrafted run={run} />}
          {working && !hasResults && <div className="card stack"><div className="row"><Loader2 size={16} className="spin" /><strong>Working on it</strong></div>
            <p className="muted small">Each stage appears on the left as it runs — most runs finish in under a minute, and every run is
              capped at {BUDGET_S} seconds. You can leave this page; the run continues in the background, and <strong>Stop run</strong> ends it at any point.</p></div>}
          {working && run.drafts.length > 0 && <div className="callout info small"><Loader2 size={14} className="spin" /><div>Generating a new version — the draft below will update.</div></div>}
          <VerificationChecklist run={run} />
          {hasResults && <>
            <div className="tabs" role="tablist">
              {run.drafts.length > 0 && <button className={tab === "draft" ? "on" : ""} onClick={() => setTab("draft")}>Draft</button>}
              <button className={tab === "signals" ? "on" : ""} onClick={() => setTab("signals")}>Signals & ranking</button>
              <button className={tab === "identity" ? "on" : ""} onClick={() => setTab("identity")}>Identity</button>
              {(run.drafts.length > 1 || run.reviews.length > 0) && <button className={tab === "history" ? "on" : ""} onClick={() => setTab("history")}>History</button>}
            </div>
            <div className="tab-body" key={tab}>
              {tab === "draft" && run.drafts.length > 0 && <DraftPanel run={run} onDone={reload} />}
              {tab === "signals" && <SignalsPanel run={run} />}
              {tab === "identity" && <IdentityPanel run={run} />}
              {tab === "history" && <History run={run} />}
            </div>
          </>}
          {!hasResults && !working && !["awaiting_rep", "failed"].includes(run.status) && run.final_route !== "do_not_draft" && !interrupted && <IdentityPanel run={run} />}
          {(run.status === "awaiting_rep" || (run.final_route === "do_not_draft" && !hasResults)) && <IdentityPanel run={run} />}
        </div>
      </div>
    </div>
  );
}

function History({ run }: { run: RunDetail }) {
  return (
    <div className="stack">
      <div className="card">
        <h3>Draft versions</h3>
        {run.drafts.map((d) => (
          <div key={d.id} className="claim">
            <div className="mono small">v{d.version}</div>
            <div><div className="row small muted"><span className="chip">{d.origin.replace("_", " ")}</span><span>{fmtDate(d.created_at)}</span>
              {d.verification && <span>{d.verification.unsupported} unsupported</span>}{d.genericness && <span>{d.genericness.generic ? "generic" : "specific"}</span>}</div>
              <div className="small" style={{ marginTop: 4 }}><strong>{d.subject}</strong></div>
              <div className="small muted" style={{ whiteSpace: "pre-wrap" }}>{d.body}</div></div>
          </div>
        ))}
      </div>
      {run.reviews.length > 0 && <div className="card">
        <h3>Review log</h3>
        {run.reviews.map((r) => (
          <div key={r.id} className="claim"><div className="mono small">{r.action}</div>
            <div className="small"><div className="muted">{fmtDate(r.created_at)}{r.edit_distance != null ? ` · ${r.edit_distance} characters changed` : ""}{r.acknowledged_sensitive ? " · sensitive context acknowledged" : ""}</div>
              {r.note && <div>Note: {r.note}</div>}
              {r.final_text && r.final_text !== r.original_text && <details className="tech"><summary>Show final text</summary><pre className="json">{r.final_text}</pre></details>}</div></div>
        ))}
      </div>}
    </div>
  );
}
