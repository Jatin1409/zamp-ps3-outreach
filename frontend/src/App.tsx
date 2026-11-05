import { useEffect, useState } from "react";
import { NavLink, Route, Routes, Link } from "react-router-dom";
import { ArrowRight, Lock } from "lucide-react";
import { getCode, post, setCode, setUnauthorizedHandler } from "./api";
import NewRun from "./pages/NewRun";
import RunView from "./pages/RunView";
import Dashboard from "./pages/Dashboard";
import SellerProfile from "./pages/SellerProfile";

function Gate({ onOk }: { onOk: () => void }) {
  const [code, setC] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await post("/api/auth/check", { code: code.trim() }); setCode(code.trim()); onOk(); }
    catch (x: any) { setErr(x.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="gate">
      <form className="card stack" onSubmit={submit}>
        <div className="row"><div className="brand-mark"><Lock size={14} /></div><span className="eyebrow">Zamp · AI Solutions Analyst case study</span></div>
        <h2>Outreach Desk — PS-3 submission</h2>
        <p className="muted small">A working process for PS-3, <em>personalised outreach: from prospect to draft</em>.
          It runs real web searches and AI calls on limited free quotas, so it's protected. Enter the access code
          shared with the submission.</p>
        <input className="input" autoFocus value={code} onChange={(e) => setC(e.target.value)} placeholder="Access code" aria-label="Access code" />
        {err && <div className="callout bad small">{err}</div>}
        <button className="btn" disabled={!code.trim() || busy}>{busy ? "Checking…" : "Continue"} <ArrowRight size={16} className="arrow" /></button>
      </form>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false));
    const c = getCode();
    if (!c) { setAuthed(false); return; }
    post("/api/auth/check", { code: c }).then(() => setAuthed(true)).catch(() => setAuthed(false));
  }, []);
  if (authed === null) return <div className="gate"><div className="skeleton" style={{ width: 240, height: 16 }} /></div>;
  if (!authed) return <Gate onOk={() => setAuthed(true)} />;
  return (
    <>
      <nav className="nav" aria-label="Main">
        <div className="nav-inner">
          <Link to="/" className="brand"><div className="brand-mark"><ArrowRight size={15} /></div><span>Outreach Desk</span></Link>
          <div className="nav-links">
            <NavLink to="/" end>New run</NavLink>
            <NavLink to="/dashboard">Dashboard</NavLink>
            <NavLink to="/seller">Seller profile</NavLink>
          </div>
          <span className="chip nav-tag">Zamp ASA case study · PS-3</span>
        </div>
      </nav>
      <main className="shell">
        <Routes>
          <Route path="/" element={<NewRun />} />
          <Route path="/runs/:id" element={<RunView />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/seller" element={<SellerProfile />} />
          <Route path="*" element={<div className="empty"><h3>Page not found</h3><Link to="/">Go to New run</Link></div>} />
        </Routes>
        <footer className="site-footer">
          <div>
            <strong>Outreach Desk</strong> — a working submission for Zamp's AI Solutions Analyst case study,
            PS-3: personalised outreach, from prospect to draft. An independent build, not a Zamp product.
          </div>
          <div className="muted">
            Ledgerline, the seller, is fictional. Prospects are real public business figures; only public,
            professional information is used and LinkedIn is never scraped. Nothing is ever sent.{" "}
            <a href="https://github.com/Jatin1409/zamp-ps3-outreach/blob/main/docs/PROCESS_MAP.md" target="_blank" rel="noreferrer">Process map</a> ·{" "}
            <a href="https://github.com/Jatin1409/zamp-ps3-outreach/blob/main/docs/DECISIONS.md" target="_blank" rel="noreferrer">Design decisions</a> ·{" "}
            <a href="https://github.com/Jatin1409/zamp-ps3-outreach" target="_blank" rel="noreferrer">Source</a>
          </div>
        </footer>
      </main>
    </>
  );
}
