// Auth provider component. Exports only AuthProvider so react-refresh is happy;
// the context lives in authContext.ts and the hook in useAuth.ts.
// Boot flow: on mount, attempt bootSession() (refresh from stored token).

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { bootSession, login as apiLogin, logout as apiLogout } from "./api";
import { AuthContext, type AuthStatus } from "./authContext";
import type { LoginRequest } from "./types";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("booting");

  useEffect(() => {
    let cancelled = false;
    void bootSession().then((ok) => {
      if (!cancelled) setStatus(ok ? "authed" : "anon");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (payload: LoginRequest) => {
    await apiLogin(payload);
    setStatus("authed");
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setStatus("anon");
  }, []);

  const onExpired = useCallback(() => {
    setStatus("anon");
  }, []);

  return (
    <AuthContext.Provider value={{ status, login, logout, onExpired }}>
      {children}
    </AuthContext.Provider>
  );
}
