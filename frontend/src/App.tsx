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
        <div className="row"><div className="brand-mark"><Lock size={14} /></div><span className="eyebrow">Outreach Desk</span></div>
        <h2>Enter the access code</h2>
        <p className="muted small">This demo runs real web searches and AI calls that cost money, so it is protected.
          The code was shared with you alongside the link.</p>
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
      </main>
    </>
  );
}
