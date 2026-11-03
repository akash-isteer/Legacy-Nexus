import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore } from "./api";
import { navigate } from "./router";
import type { DocRole, User } from "./types";

/** The perspective a person signs in with. Document Q&A answers are written for this role. */
export const SIGNIN_ROLES: { id: DocRole; label: string; hint: string }[] = [
  { id: "business", label: "Business Person", hint: "Requirements, rules, processes and outcomes" },
  { id: "developer", label: "Developer", hint: "Architecture, logic, data and constraints" },
  { id: "tester", label: "Tester", hint: "Test scenarios, edge cases and risks" },
  { id: "end_user", label: "End User", hint: "Plain steps and expected behaviour" },
];
const ROLE_KEY = "legacynexus.signinRole";

function storedRole(): DocRole {
  try {
    const v = localStorage.getItem(ROLE_KEY);
    if (SIGNIN_ROLES.some((r) => r.id === v)) return v as DocRole;
  } catch { /* storage unavailable */ }
  return "business";
}

type AuthState = {
  user: User | null;
  loading: boolean;
  /** Role chosen on the sign-in page. */
  signinRole: DocRole;
  login: (email: string, password: string, role?: DocRole) => Promise<void>;
  register: (fullName: string, email: string, password: string, role?: DocRole) => Promise<void>;
  refresh: () => Promise<void>;
  logout: () => void;
  updatePreferences: (prefs: Record<string, unknown>) => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [signinRole, setSigninRole] = useState<DocRole>(storedRole);

  const rememberRole = useCallback((role?: DocRole) => {
    if (!role) return;
    setSigninRole(role);
    try { localStorage.setItem(ROLE_KEY, role); } catch { /* still applies for this session */ }
  }, []);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
    navigate("/login");
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      const publicPaths = ["/login", "/signup", "/forgot-password", "/reset-password"];
      if (!publicPaths.some((p) => window.location.pathname.startsWith(p)) && window.location.pathname !== "/") navigate("/login");
    });
    if (!tokenStore.get()) {
      setLoading(false);
      return;
    }
    api
      .get<User>("/auth/me")
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string, role?: DocRole) => {
    const res = await api.post<{ access_token: string; user: User }>("/auth/login", { email, password });
    tokenStore.set(res.access_token);
    rememberRole(role);
    setUser(res.user);
  }, [rememberRole]);

  const register = useCallback(async (fullName: string, email: string, password: string, role?: DocRole) => {
    const res = await api.post<{ access_token: string; user: User }>("/auth/register", { full_name: fullName, email, password });
    tokenStore.set(res.access_token);
    rememberRole(role);
    setUser(res.user);
  }, [rememberRole]);

  const refresh = useCallback(async () => {
    setUser(await api.get<User>("/auth/me"));
  }, []);

  const updatePreferences = useCallback(async (prefs: Record<string, unknown>) => {
    const updated = await api.patch<User>("/auth/me/preferences", { preferences: prefs });
    setUser(updated);
  }, []);

  return <AuthContext.Provider value={{ user, loading, signinRole, login, register, refresh, logout, updatePreferences }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
