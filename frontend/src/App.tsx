import { useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ArrowRight, Check, ChevronDown, Lock, Menu, Settings2, X } from "lucide-react";
import { getCode, post, setCode, setUnauthorizedHandler } from "./api";
import NewRun from "./pages/NewRun";
import RunView from "./pages/RunView";
import Dashboard from "./pages/Dashboard";
import SellerProfiles from "./pages/SellerProfiles";
import About from "./pages/About";
import { ProfileProvider, ToastProvider, useProfiles, useToast } from "./state";

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

function ProfileSwitcher() {
  const { profiles, current, select } = useProfiles();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, []);
  if (!profiles) return <div className="skeleton" style={{ width: 150, height: 34, borderRadius: 999 }} />;
  return (
    <div className="switcher" ref={ref}>
      <button className="switcher-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        title="Seller profile used for new runs">
        <span className="switcher-dot">{(current?.name || "?").slice(0, 1).toUpperCase()}</span>
        <span className="switcher-label"><span className="switcher-cap">Selling as</span>{current?.name}</span>
        <ChevronDown size={14} className={open ? "rot" : ""} />
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-cap">Seller profiles</div>
          {profiles.map((p) => (
            <button key={p.id} role="menuitemradio" aria-checked={p.id === current?.id} className="menu-item"
              onClick={() => { select(p.id); setOpen(false); if (p.id !== current?.id) toast(`Now selling as ${p.name}`); }}>
              <span className="switcher-dot sm">{p.name.slice(0, 1).toUpperCase()}</span>
              <span className="menu-text"><strong>{p.name}</strong><span>{p.run_count ?? 0} run{p.run_count === 1 ? "" : "s"}</span></span>
              {p.id === current?.id && <Check size={15} />}
            </button>
          ))}
          <div className="menu-sep" />
          <Link to="/profiles" className="menu-item" role="menuitem"><Settings2 size={15} /> Manage profiles</Link>
        </div>
      )}
    </div>
  );
}

function Shell() {
  const [menu, setMenu] = useState(false);
  const loc = useLocation();
  useEffect(() => { setMenu(false); window.scrollTo(0, 0); }, [loc.pathname]);
  const links = [["/", "New run"], ["/dashboard", "Dashboard"], ["/profiles", "Seller profiles"], ["/about", "About"]];
  return (
    <>
      <nav className="nav" aria-label="Main">
        <div className="nav-inner">
          <Link to="/" className="brand"><div className="brand-mark"><ArrowRight size={15} /></div><span>Outreach Desk</span></Link>
          <div className={`nav-links ${menu ? "open" : ""}`}>
            {links.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}
          </div>
          <div className="nav-right">
            <ProfileSwitcher />
            <button className="icon-btn nav-burger" aria-label="Menu" aria-expanded={menu} onClick={() => setMenu((v) => !v)}>
              {menu ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </div>
      </nav>
      <main className="shell" key={loc.pathname.split("/")[1] || "home"}>
        <Routes>
          <Route path="/" element={<NewRun />} />
          <Route path="/runs/:id" element={<RunView />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/profiles" element={<SellerProfiles />} />
          <Route path="/seller" element={<Navigate to="/profiles" replace />} />
          <Route path="/about" element={<About />} />
          <Route path="*" element={<div className="empty"><h3>Page not found</h3><Link to="/">Go to New run</Link></div>} />
        </Routes>
        <footer className="site-footer">
          <div>
            <strong>Outreach Desk</strong> — a working submission for Zamp's AI Solutions Analyst case study,
            PS-3: personalised outreach, from prospect to draft. Built by <Link to="/about">Jatin Jain</Link>.
            An independent build, not a Zamp product.
          </div>
          <div className="muted">
            The sample seller, Ledgerline, is fictional. Prospects are real public business figures; only public,
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
  return <ToastProvider><ProfileProvider><Shell /></ProfileProvider></ToastProvider>;
}
