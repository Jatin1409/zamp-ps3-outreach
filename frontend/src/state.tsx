import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, getProfileId, Profile, setProfileId } from "./api";

/* ------------------------------------------------------------------ seller profiles */
interface ProfileState {
  profiles: Profile[] | null;
  current: Profile | null;
  select: (id: string) => void;
  refresh: () => Promise<void>;
  error: string | null;
}
const ProfileCtx = createContext<ProfileState>({ profiles: null, current: null, select: () => {}, refresh: async () => {}, error: null });

export function ProfileProvider({ children }: { children: ReactNode }) {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [currentId, setCurrentId] = useState<string>(getProfileId());
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api<{ current_id: string; items: Profile[] }>("/api/seller-profiles");
      setProfiles(r.items);
      // the stored choice may point at a profile that was deleted elsewhere: fall back to what the server resolved
      if (!r.items.some((p) => p.id === getProfileId())) { setProfileId(r.current_id); setCurrentId(r.current_id); }
      setError(null);
    } catch (e: any) { setError(e.message); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const select = useCallback((id: string) => { setProfileId(id); setCurrentId(id); }, []);
  const current = profiles?.find((p) => p.id === currentId) || profiles?.[0] || null;
  return <ProfileCtx.Provider value={{ profiles, current, select, refresh, error }}>{children}</ProfileCtx.Provider>;
}
export const useProfiles = () => useContext(ProfileCtx);

/* ------------------------------------------------------------------ toasts */
type Toast = { id: number; text: string; tone: "ok" | "bad" | "info" };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const n = useRef(0);
  const push = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = ++n.current;
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);
