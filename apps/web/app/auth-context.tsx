"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  AUTH_EXPIRED_EVENT,
  clearAuth,
  getProjectId,
  getSessionToken,
  loadAuth,
  setSession,
  type AuthState,
} from "./api-client";

type AuthContextValue = {
  /** Current authenticated user, or null when logged out. */
  auth: AuthState | null;
  /** True once the initial auth state has been restored from storage. */
  ready: boolean;
  /** Current user's project id (empty string when logged out). */
  projectId: string;
  /** Current session token (empty string when logged out). */
  sessionToken: string;
  login: (auth: AuthState) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [ready, setReady] = useState(false);

  // Restore any persisted session on first mount, then reflect it into the
  // shared session store used by request helpers.
  useEffect(() => {
    const existing = loadAuth();
    if (existing) {
      setSession(existing);
      setAuth(existing);
    }
    setReady(true);
  }, []);

  // When any protected request returns 401 (expired/revoked session), drop back
  // to the login screen instead of leaving a stale "logged-in" UI.
  useEffect(() => {
    function handleAuthExpired() {
      clearAuth();
      setAuth(null);
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, []);

  const login = useCallback((next: AuthState) => {
    setSession(next);
    setAuth(next);
  }, []);

  const logout = useCallback(() => {
    clearAuth();
    setAuth(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      auth,
      ready,
      projectId: getProjectId(),
      sessionToken: getSessionToken(),
      login,
      logout,
    }),
    [auth, ready, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return value;
}
