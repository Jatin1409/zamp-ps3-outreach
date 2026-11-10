import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { del, post, Profile, put, SellerData } from "../api";
import { useProfiles, useToast } from "../state";
import { fmtDate } from "../format";

const FIELDS: [keyof SellerData, string, string, "input" | "text" | "list"][] = [
  ["company_name", "Company name", "Shown in the switcher; must be unique.", "input"],
  ["product", "Product", "What you sell, in one or two plain sentences.", "text"],
  ["ideal_customer", "Ideal customer", "Who buys it and when they need it.", "text"],
  ["problems_solved", "Problems solved", "One per line. Relevance scoring uses these — at least one is required.", "list"],
  ["proof_points", "Proof points", "One per line. Drafts may quote these word-for-word, so keep them true.", "list"],
  ["tone", "Tone", "How messages should sound.", "input"],
  ["sender_name", "Sign-off", "How the email is signed.", "input"],
];

const BLANK: SellerData = { company_name: "", product: "", ideal_customer: "", problems_solved: [], proof_points: [],
  tone: "Plain, direct and peer-to-peer. Short sentences. No hype.", sender_name: "" };

type Draft = Record<keyof SellerData, string>;
const toDraft = (d: SellerData): Draft => Object.fromEntries(FIELDS.map(([k]) => [k, Array.isArray(d[k]) ? (d[k] as string[]).join("\n") : (d[k] as string) || ""])) as Draft;
const fromDraft = (d: Draft, base: SellerData): SellerData => ({ ...base, ...Object.fromEntries(FIELDS.map(([k, , , kind]) =>
  [k, kind === "list" ? d[k].split("\n").map((x) => x.trim()).filter(Boolean) : d[k].trim()])) } as SellerData);

export default function SellerProfiles() {
  const { profiles, current, select, refresh } = useProfiles();
  const toast = useToast();
  const [openId, setOpenId] = useState<string | null>(null);   // profile shown in the editor; "new" = creating
  const [form, setForm] = useState<Draft>(toDraft(BLANK));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);

  const open = useMemo(() => profiles?.find((p) => p.id === openId) || null, [profiles, openId]);
  useEffect(() => { if (!openId && current) setOpenId(current.id); }, [current, openId]);
  useEffect(() => {
    setErr(null); setConfirmDel(false);
    if (openId === "new") setForm(toDraft(BLANK));
    else if (open) setForm(toDraft(open.data));
  }, [openId, open?.updated_at]); // eslint-disable-line

  const dirty = openId === "new" || (open && JSON.stringify(fromDraft(form, open.data)) !== JSON.stringify(open.data));
  const run = async (fn: () => Promise<void>) => { setBusy(true); setErr(null); try { await fn(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };

  const save = () => run(async () => {
    if (openId === "new") {
      const p = await post<Profile>("/api/seller-profiles", { data: fromDraft(form, BLANK) });
      await refresh(); setOpenId(p.id); select(p.id); toast(`Created “${p.name}” — new runs now use it`);
    } else if (open) {
      await put(`/api/seller-profiles/${open.id}`, fromDraft(form, open.data));
      await refresh(); toast("Profile saved");
    }
  });
  const duplicate = (p: Profile) => run(async () => {
    const c = await post<Profile>("/api/seller-profiles", { copy_from: p.id });
    await refresh(); setOpenId(c.id); toast(`Duplicated as “${c.name}”`);
  });
  const remove = (p: Profile) => run(async () => {
    const r = await del<{ runs_kept: number }>(`/api/seller-profiles/${p.id}`);
    const next = profiles!.find((x) => x.id !== p.id)!;
    if (current?.id === p.id) select(next.id);
    await refresh(); setOpenId(next.id);
    toast(`Deleted “${p.name}”${r.runs_kept ? ` — its ${r.runs_kept} run(s) keep their own copy of the profile` : ""}`, "info");
  });
  const reset = () => run(async () => { await post(`/api/seller-profiles/default/reset`, {}); await refresh(); toast("Reset to the Ledgerline example"); });

  if (!profiles) return <div className="page"><div className="skeleton" style={{ height: 420 }} /></div>;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">Who the drafts are written for</div>
          <h1>Seller profiles</h1>
          <p className="muted lead-sm">Each profile is an independent seller. New runs use the profile selected in the top bar, and every run
            keeps its own copy — so editing or deleting a profile never changes past runs.</p>
        </div>
        <button className="btn" onClick={() => setOpenId("new")} disabled={openId === "new"}><Plus size={16} /> New profile</button>
      </div>

      <div className="profiles-grid">
        <div className="stack" role="list">
          {openId === "new" && (
            <div className="profile-card sel" role="listitem"><div className="profile-avatar">+</div>
              <div><strong>New profile</strong><div className="small muted">Not saved yet</div></div></div>
          )}
          {profiles.map((p) => (
            <button key={p.id} role="listitem" className={`profile-card ${openId === p.id ? "sel" : ""}`} onClick={() => setOpenId(p.id)}>
              <div className="profile-avatar">{p.name.slice(0, 1).toUpperCase()}</div>
              <div className="profile-meta">
                <div className="row" style={{ gap: 6 }}><strong>{p.name}</strong>{p.id === current?.id && <span className="pill ok">Current</span>}</div>
                <div className="small muted clamp-2">{p.data.product}</div>
                <div className="tiny muted">{p.run_count ?? 0} run{p.run_count === 1 ? "" : "s"} · updated {fmtDate(p.updated_at)}</div>
              </div>
            </button>
          ))}
        </div>

        <div className="card stack" aria-live="polite">
          <div className="row-between">
            <h2>{openId === "new" ? "New seller profile" : open?.name}</h2>
            {open && openId !== "new" && (
              <div className="row">
                {open.id !== current?.id
                  ? <button className="btn sm" onClick={() => { select(open.id); toast(`Now selling as ${open.name}`); }}><Check size={14} /> Use this profile</button>
                  : <span className="pill ok"><Check size={12} /> Used for new runs</span>}
                <button className="btn secondary sm" onClick={() => duplicate(open)} disabled={busy}><Copy size={14} /> Duplicate</button>
              </div>
            )}
          </div>
          {open?.data.fictional_notice && openId !== "new" && <div className="callout info small">{open.data.fictional_notice}</div>}
          {FIELDS.map(([k, label, hint, kind]) => (
            <label key={k} className="field">{label} <span className="hint">{hint}</span>
              {kind === "input"
                ? <input className="input" value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} maxLength={k === "tone" ? 500 : 120} />
                : <textarea className="textarea" rows={kind === "list" ? 4 : 3} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />}
            </label>
          ))}
          {err && <div className="callout bad small">{err}</div>}
          <div className="row-between">
            <div className="row">
              {openId === "new" && <button className="btn ghost sm" onClick={() => setOpenId(current?.id || profiles[0].id)}><X size={14} /> Cancel</button>}
              {open?.is_default && openId !== "new" && <button className="btn ghost sm" onClick={reset} disabled={busy}><RotateCcw size={14} /> Reset to Ledgerline example</button>}
              {open && openId !== "new" && profiles.length > 1 && !confirmDel &&
                <button className="btn ghost sm danger-text" onClick={() => setConfirmDel(true)}><Trash2 size={14} /> Delete</button>}
            </div>
            <button className="btn" onClick={save} disabled={busy || !dirty}><Check size={16} /> {busy ? "Saving…" : openId === "new" ? "Create profile" : "Save changes"}</button>
          </div>
          {confirmDel && open && (
            <div className="callout bad">
              <Trash2 size={16} />
              <div className="stack" style={{ gap: 8 }}>
                <div><strong>Delete “{open.name}”?</strong> {open.run_count ? `Its ${open.run_count} past run(s) stay in the dashboard with their own copy of this profile.` : "It has no runs."} This can't be undone.</div>
                <div className="row">
                  <button className="btn danger sm" onClick={() => remove(open)} disabled={busy}>Delete profile</button>
                  <button className="btn ghost sm" onClick={() => setConfirmDel(false)}>Keep it</button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
