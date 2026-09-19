"use client";

import { useState } from "react";
import { LockKeyhole, LogIn } from "lucide-react";
import { API_URL, fetchApi, loadAuth, setSession, type AuthState } from "./api-client";

type LoginResult = {
  access_token: string;
  token_type: string;
  user: { id: string; email: string; display_name: string; role: string; project_id: string };
};

export function LoginView({ onAuthed }: { onAuthed: (auth: AuthState) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const response = await fetchApi(`${API_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const body = (await response.json().catch(() => null)) as LoginResult | { detail?: unknown } | null;
      if (!response.ok || !body || !("access_token" in body)) {
        const detail = body && "detail" in body ? (body.detail as string) : null;
        setError(typeof detail === "string" ? detail : `登录失败（HTTP ${response.status}）`);
        return;
      }
      const auth: AuthState = {
        token: body.access_token,
        projectId: body.user.project_id,
        userId: body.user.id,
        email: body.user.email,
        displayName: body.user.display_name,
        role: body.user.role,
      };
      setSession(auth);
      onAuthed(auth);
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card panel" onSubmit={submit}>
        <div className="login-brand">
          <div className="brand-mark">AE</div>
          <div>
            <strong>Agent Eval</strong>
            <span>质量评测工作台</span>
          </div>
        </div>
        <p className="login-lede">登录后进入你的独立评测空间。账号由管理员分配。</p>
        <label className="field-label">
          <span>邮箱</span>
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
            required
          />
        </label>
        <label className="field-label">
          <span>密码</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="••••••••"
            required
          />
        </label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="primary login-submit" type="submit" disabled={busy}>
          {busy ? <LogIn size={16} /> : <LockKeyhole size={16} />}
          {busy ? "登录中..." : "登录"}
        </button>
      </form>
    </div>
  );
}
