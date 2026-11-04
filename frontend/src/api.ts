export type StageStatus = "pending" | "running" | "succeeded" | "failed" | "skipped" | "waiting";

export interface Prospect { name: string; company: string; role?: string | null; linkedin_url?: string | null; notes?: string | null; }

export interface StageRow {
  key: string; label: string; attempt?: number; status: StageStatus; started_at?: string; finished_at?: string;
  duration_ms?: number | null; input?: any; output?: any; explanation?: string | null; error?: string | null;
  tokens_in?: number; tokens_out?: number; cost_usd?: number; attempts: StageRow[];
}

export interface SignalRow {
  sid: string; url: string; title: string; published_date: string | null; text_excerpt: string; provider: string;
  query: string | null; scope: string; signal_type: string | null; about: string | null; status: string;
  drop_reason: string | null; sensitive: any; judgments: any; scores: any;
}

export interface Claim {
  claim: string; kind: string; source_id: string; supporting_quote: string; entailed: boolean; supported: boolean;
  status: string; problem?: string | null; url?: string | null; title?: string | null; source_type?: string; published?: string | null;
  quote_match?: number;
}

export interface DraftRow {
  id: number; version: number; origin: string; subject: string; body: string; hook_signal_id: string | null;
  meta: any; lint: any; verification: { claims: Claim[]; unsupported: number; rounds: number; passed: boolean } | null;
  genericness: any; created_at: string;
}

export interface RunSummary {
  id: string; created_at: string; finished_at: string | null; status: string; outcome: string | null; mode: string;
  prospect: Prospect; final_route: string | null; route_path: string[]; personalisation: string | null;
  edge_cases: string[]; warnings: string[]; cost_usd: number; tokens_in: number; tokens_out: number; active_ms: number;
  current_stage: string | null; scenario_key: string | null; identity_confidence: string | null; error: string | null;
  llm_calls: number; search_calls: number;
}

export interface RunDetail extends RunSummary {
  seller_profile: any; identity: any; pause: any; rep_decisions: any[]; route_reason: string | null;
  replay_source_run_id: string | null; last_event_id: number; stages: StageRow[]; signals: SignalRow[];
  drafts: DraftRow[]; reviews: any[]; llm_call_log: any[];
}

export interface Scenario {
  key: string; title: string; description: string; expected_route: string; edge_case: string | null;
  prospect: Prospect; replay_available: boolean; demo_note?: string;
}

const CODE_KEY = "od.access";
export const getCode = () => { try { return localStorage.getItem(CODE_KEY) || ""; } catch { return ""; } };
export const setCode = (c: string) => { try { localStorage.setItem(CODE_KEY, c); } catch { /* private mode */ } };

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Access-Code": getCode(), ...(opts.headers || {}) } });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 401) { onUnauthorized(); throw new ApiError(401, "Access code required."); }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: any) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
    } catch { /* not json */ }
    throw new ApiError(res.status, msg);
  }
  return res.json();
}

export const post = <T = any>(path: string, body: any) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const put = <T = any>(path: string, body: any) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
