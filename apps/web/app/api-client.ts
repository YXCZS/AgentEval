export const API_URL = process.env.NEXT_PUBLIC_API_URL?.trim() ?? "";

const AUTH_KEY = "agent-eval.auth";

// The current session is kept in a module-private store rather than exported
// bare `let` bindings. Readers go through getProjectId()/getSessionToken() and
// writers go through setSession(), so there is a single, explicit mutation
// point and no component mutates the value in place. This replaces the previous
// exported mutable `PROJECT_ID`/`SESSION` bindings that views imported and read
// directly (an implicit global with multi-user / hot-reload ordering hazards).
let _projectId = "";
let _sessionToken = "";

// Custom event fired when a protected request returns 401 (expired/invalid
// session). The shell listens for this and drops back to the login screen.
export const AUTH_EXPIRED_EVENT = "agent-eval:auth-expired";

export type AuthState = {
  token: string;
  projectId: string;
  userId: string;
  email: string;
  displayName: string;
  role: string;
};

export function getProjectId(): string {
  return _projectId;
}

export function getSessionToken(): string {
  return _sessionToken;
}

export function setSession(auth: AuthState | null): void {
  if (auth) {
    _projectId = auth.projectId;
    _sessionToken = auth.token;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(AUTH_KEY, JSON.stringify(auth));
    }
  } else {
    _projectId = "";
    _sessionToken = "";
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(AUTH_KEY);
    }
  }
}

// Decode the JWT payload (no signature verification — the API verifies it on
// every request; this is only a client-side expiry hint for early logout).
function decodeJwtExp(token: string): number | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = JSON.parse(
      atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")),
    ) as { exp?: unknown };
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

export function isTokenExpired(token: string): boolean {
  const exp = decodeJwtExp(token);
  if (exp === null) return false;
  // Treat as expired with a 30s buffer to avoid a race at the boundary.
  return exp * 1000 <= Date.now() + 30_000;
}

export function loadAuth(): AuthState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthState;
    if (
      typeof parsed.token === "string" &&
      typeof parsed.projectId === "string" &&
      !isTokenExpired(parsed.token)
    ) {
      return {
        ...parsed,
        role: typeof parsed.role === "string" ? parsed.role : "member",
        userId: typeof parsed.userId === "string" ? parsed.userId : "",
      };
    }
    // Stale or expired session: clear it so we don't show a "fake" logged-in UI.
    window.localStorage.removeItem(AUTH_KEY);
  } catch {
    return null;
  }
  return null;
}

export function clearAuth(): void {
  setSession(null);
}

export function notifyAuthExpired(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}

export async function fetchApi(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  if (typeof input === "string" && !API_URL) {
    throw new Error("未配置 NEXT_PUBLIC_API_URL，无法连接 Agent Eval API。请在 Web 环境变量中配置 API 地址。");
  }
  try {
    const response = await fetch(input, init);
    // A 401 on a protected endpoint means the session expired or was revoked.
    // The login endpoint legitimately returns 401 for bad credentials, so it is
    // excluded from the global sign-out path.
    if (
      response.status === 401 &&
      !(typeof input === "string" && input.includes("/auth/login"))
    ) {
      clearAuth();
      notifyAuthExpired();
    }
    return response;
  } catch {
    throw new Error("无法连接 API，请确认后端服务已启动，并检查 API 地址配置。");
  }
}
