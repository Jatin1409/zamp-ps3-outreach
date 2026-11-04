import { useEffect, useState } from "react";
import { Check, RotateCcw } from "lucide-react";
import { api, post, put } from "../api";

const FIELDS: [string, string, string, boolean][] = [
  ["company_name", "Company name", "", false],
  ["product", "Product", "What you sell, in one or two plain sentences.", false],
  ["ideal_customer", "Ideal customer", "Who buys it and when they need it.", false],
  ["problems_solved", "Problems solved", "One per line. Used to judge which signals are relevant.", true],
  ["proof_points", "Proof points", "One per line. The draft may quote these word-for-word — keep them true.", true],
  ["tone", "Tone", "How messages should sound.", false],
  ["sender_name", "Sign-off", "How the email is signed.", false],
];

export default function SellerProfile() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/seller-profile").then((r) => setData(r.data)).catch((e) => setErr(e.message)); }, []);

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const body = { ...data, problems_solved: toList(data.problems_solved), proof_points: toList(data.proof_points) };
      const r = await put("/api/seller-profile", body);
      setData(r.data); setSaved(true); setTimeout(() => setSaved(false), 1800);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const reset = async () => { const r = await post("/api/seller-profile/reset", {}); setData(r.data); };
  const toList = (v: any) => (Array.isArray(v) ? v : String(v || "").split("\n")).map((s: string) => s.trim()).filter(Boolean);

  if (!data) return err ? <div className="callout bad">{err}</div> : <div className="skeleton" style={{ height: 400 }} />;
  return (
    <div className="stack-lg" style={{ maxWidth: 820 }}>
      <div><div className="eyebrow">Who the drafts are written for</div><h1 style={{ marginTop: 6 }}>Seller profile</h1>
        <p className="muted" style={{ marginTop: 6 }}>Every run snapshots this profile, so editing it changes future runs only. Relevance scoring, the draft and the fact-check all use it.</p></div>
      {data.fictional_notice && <div className="callout info small">{data.fictional_notice}</div>}
      <div className="card stack">
        {FIELDS.map(([k, label, hint, list]) => (
          <label key={k} className="field">{label} {hint && <span className="hint">{hint}</span>}
            {list || k === "product" || k === "ideal_customer"
              ? <textarea className="textarea" value={Array.isArray(data[k]) ? data[k].join("\n") : data[k] || ""} onChange={(e) => setData({ ...data, [k]: list ? e.target.value.split("\n") : e.target.value })} />
              : <input className="input" value={data[k] || ""} onChange={(e) => setData({ ...data, [k]: e.target.value })} />}
          </label>
        ))}
        {err && <div className="callout bad small">{err}</div>}
        <div className="row-between">
          <button className="btn ghost sm" onClick={reset}><RotateCcw size={14} /> Reset to Ledgerline</button>
          <button className="btn" disabled={busy} onClick={save}><Check size={16} /> {saved ? "Saved" : busy ? "Saving…" : "Save profile"}</button>
        </div>
      </div>
    </div>
  );
}
