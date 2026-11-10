/** Server timestamps are UTC; some databases return them without a zone suffix. Never read them as local time. */
export const parseTs = (s: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) || !s.includes("T") ? s : s + "Z");

export const fmtMs = (ms?: number | null) => {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
};
export const fmtCost = (c?: number | null) => (c == null ? "—" : c === 0 ? "$0.00" : c < 0.01 ? `$${c.toFixed(4)}` : `$${c.toFixed(3)}`);
export const fmtNum = (n?: number | null) => (n == null ? "—" : n.toLocaleString());
export const fmtPct = (n?: number | null) => (n == null ? "—" : `${Math.round(n * 100)}%`);
export const fmtDate = (s?: string | null) => {
  if (!s) return "—";
  const d = parseTs(s);
  return isNaN(+d) ? s : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
export const fmtPub = (s?: string | null) => {
  if (!s) return "date unknown";
  const d = new Date(s);
  return isNaN(+d) ? s : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
};
export const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

export const ROUTE_LABEL: Record<string, string> = {
  draft: "Draft", draft_with_warning: "Draft with warning", ask_rep: "Ask rep", do_not_draft: "Do not draft",
};
export const OUTCOME_LABEL: Record<string, string> = {
  approved: "Approved", approved_with_edits: "Approved with edits", rejected: "Rejected", not_drafted: "Not drafted",
  stopped_by_rep: "Stopped by rep", failed: "Failed", cancelled: "Stopped", timed_out: "Timed out",
};
export const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", running: "Running", awaiting_rep: "Needs your decision", awaiting_review: "Ready for review",
  completed: "Completed", failed: "Failed", cancelled: "Stopped", timed_out: "Timed out",
};
export const EDGE_LABEL: Record<string, string> = {
  ambiguous_identity: "Ambiguous identity", outdated_role: "Outdated role", thin_signal: "Thin signal",
  sensitive_signal: "Sensitive signal", ambiguous_company: "Ambiguous company", fictional_entity: "Fictional entity",
  company_not_found: "Nothing found",
};

export const statusTone = (s: string) =>
  ({ queued: "neutral", running: "info", awaiting_rep: "warn", awaiting_review: "info", completed: "ok", failed: "bad",
     cancelled: "neutral", timed_out: "warn" } as any)[s] || "neutral";
export const outcomeTone = (s?: string | null) =>
  ({ approved: "ok", approved_with_edits: "ok", rejected: "neutral", not_drafted: "warn", stopped_by_rep: "neutral", failed: "bad",
     cancelled: "neutral", timed_out: "warn" } as any)[s || ""] || "outline";
export const routeTone = (s?: string | null) =>
  ({ draft: "ok", draft_with_warning: "warn", ask_rep: "info", do_not_draft: "bad" } as any)[s || ""] || "neutral";

export const ACTIVE = ["queued", "running"];
export const STOPPABLE = ["queued", "running", "awaiting_rep"];
