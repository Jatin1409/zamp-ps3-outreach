import { useCallback, useEffect, useRef, useState } from "react";
import { api, getCode, RunDetail } from "./api";

const LIVE = ["queued", "running"];

/** Loads a run and keeps it fresh: Server-Sent Events while it is active, with a polling fallback.
 *  Requests in flight are aborted (AbortController) when the page changes run or unmounts, so a slow response can
 *  never overwrite a newer one. */
export function useRun(id: string) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Record<string, string>>({});
  const [connected, setConnected] = useState(false);
  const timer = useRef<number | null>(null);
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    inflight.current?.abort();
    const ac = new AbortController();
    inflight.current = ac;
    try {
      const r = await api<RunDetail>(`/api/runs/${id}`, { signal: ac.signal });
      if (!ac.signal.aborted) { setRun(r); setError(null); }
    } catch (e: any) {
      if (e?.name !== "AbortError") setError(e.message);
    }
  }, [id]);

  const scheduleLoad = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 150);
  }, [load]);

  useEffect(() => {
    setRun(null); setProgress({}); load();
    return () => { inflight.current?.abort(); if (timer.current) window.clearTimeout(timer.current); };
  }, [load]);

  const active = run ? LIVE.includes(run.status) : false;
  useEffect(() => {
    if (!run || !active) return;
    const es = new EventSource(`/api/runs/${id}/events?after=${run.last_event_id}&code=${encodeURIComponent(getCode())}`);
    es.addEventListener("open", () => setConnected(true));
    es.addEventListener("error", () => setConnected(false));
    es.addEventListener("run", (m: MessageEvent) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "stage_progress") setProgress((p) => ({ ...p, [ev.data.stage]: ev.data.message }));
      else scheduleLoad();
    });
    return () => { es.close(); setConnected(false); };
    // re-subscribe only when the run id changes or the run becomes active again
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, run?.id, active]);

  // polling fallback if SSE is blocked by a proxy
  useEffect(() => {
    if (!active || connected) return;
    const t = window.setInterval(load, 2500);
    return () => window.clearInterval(t);
  }, [active, connected, load]);

  return { run, error, progress, reload: load, connected, setRun };
}
