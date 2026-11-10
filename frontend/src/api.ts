export type StageStatus = "pending" | "running" | "succeeded" | "failed" | "skipped" | "waiting" | "cancelled" | "timed_out";

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

export interface Check { key: string; label: string; status: "verified" | "not_verified" | "conflict" | "fictional"; detail: string; }

export interface SellerData {
  company_name: string; product: string; ideal_customer: string; problems_solved: string[]; proof_points: string[];
  tone: string; sender_name: string; fictional_notice?: string | null;
}
export interface Profile { id: string; name: string; data: SellerData; is_default: boolean; created_at: string | null; updated_at: string; run_count: number | null; }

export interface RunSummary {
  id: string; created_at: string; finished_at: string | null; status: string; outcome: string | null; mode: string;
  prospect: Prospect; final_route: string | null; route_path: string[]; personalisation: string | null;
  edge_cases: string[]; warnings: string[]; cost_usd: number; tokens_in: number; tokens_out: number; active_ms: number;
  current_stage: string | null; scenario_key: string | null; identity_confidence: string | null; error: string | null;
  llm_calls: number; search_calls: number; seller_profile_id: string; seller_name: string | null;
  interrupted: { reason: "user" | "timeout"; stage: string | null; at: string; message: string; incomplete?: string[] } | null;
  within_budget: boolean;
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
const PROFILE_KEY = "od.profile";
export const getProfileId = () => { try { return localStorage.getItem(PROFILE_KEY) || ""; } catch { return ""; } };
export const setProfileId = (id: string) => { try { localStorage.setItem(PROFILE_KEY, id); } catch { /* private mode */ } };
export const getCode = () => { try { return localStorage.getItem(CODE_KEY) || ""; } catch { return ""; } };
export const setCode = (c: string) => { try { localStorage.setItem(CODE_KEY, c); } catch { /* private mode */ } };

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    const pid = getProfileId();
    res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Access-Code": getCode(),
      ...(pid ? { "X-Seller-Profile": pid } : {}), ...(opts.headers || {}) } });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
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
export const del = <T = any>(path: string) => api<T>(path, { method: "DELETE" });
