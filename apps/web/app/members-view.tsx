"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  Check,
  CircleAlert,
  CircleDashed,
  KeyRound,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserCog,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { API_URL, PROJECT_ID, SESSION, fetchApi, type AuthState } from "./api-client";

type Member = {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "member";
  active: boolean;
  project_id: string;
  created_at: string;
  last_login_at: string | null;
};
type MemberList = { items: Member[]; total: number };
type Notice = { tone: "success" | "danger" | "neutral"; text: string };

function headers(): HeadersInit {
  return { "Content-Type": "application/json", "Authorization": `Bearer ${SESSION}` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers(), ...init?.headers },
    cache: "no-store",
  });
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
  }
  return body as T;
}

function formatDate(value: string | null): string {
  if (!value) return "从未登录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function roleLabel(role: Member["role"]): string {
  return role === "admin" ? "管理员" : "成员";
}

export function MembersView({ auth }: { auth: AuthState }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [resetTarget, setResetTarget] = useState<Member | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Member | null>(null);

  const isAdmin = auth.role === "admin";

  async function load() {
    setLoading(true);
    setNotice(null);
    try {
      const result = await requestJson<MemberList>(`/auth/users`);
      setMembers(result.items);
      setTotal(result.total);
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "成员列表加载失败。" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function createMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || !password.trim() || !displayName.trim()) {
      setNotice({ tone: "danger", text: "请填写邮箱、姓名和密码。" });
      return;
    }
    if (password.length < 8) {
      setNotice({ tone: "danger", text: "密码至少需要 8 个字符。" });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const created = await requestJson<Member>(`/auth/users`, {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), display_name: displayName.trim(), password, role }),
      });
      setMembers((current) => [...current, created]);
      setTotal((current) => current + 1);
      setFormOpen(false);
      setEmail(""); setDisplayName(""); setPassword(""); setRole("member");
      setNotice({ tone: "success", text: `已创建账号 ${created.email}（独立空间 ${created.project_id}）。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "创建成员失败。" });
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(member: Member) {
    if (member.id === auth.userId) {
      setNotice({ tone: "danger", text: "不能停用当前登录的账号。" });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const updated = await requestJson<Member>(`/auth/users/${member.id}/active`, {
        method: "PATCH",
        body: JSON.stringify({ active: !member.active }),
      });
      setMembers((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice({ tone: "success", text: `账号 ${updated.email} 已${updated.active ? "启用" : "停用"}。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "更新账号状态失败。" });
    } finally {
      setBusy(false);
    }
  }

  async function resetMemberPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!resetTarget) return;
    if (resetPassword.length < 8) {
      setNotice({ tone: "danger", text: "新密码至少需要 8 个字符。" });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await requestJson<void>(`/auth/users/${resetTarget.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ password: resetPassword }),
      });
      setResetTarget(null);
      setResetPassword("");
      setNotice({ tone: "success", text: `已重置 ${resetTarget.email} 的密码。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "重置密码失败。" });
    } finally {
      setBusy(false);
    }
  }

  async function deleteMember(member: Member) {
    if (member.id === auth.userId) {
      setNotice({ tone: "danger", text: "不能删除当前登录的账号。" });
      setDeleteTarget(null);
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await requestJson<void>(`/auth/users/${member.id}`, { method: "DELETE" });
      setMembers((current) => current.filter((item) => item.id !== member.id));
      setTotal((current) => Math.max(0, current - 1));
      setDeleteTarget(null);
      setNotice({ tone: "success", text: `已删除账号 ${member.email} 及其数据空间。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "删除成员失败。" });
    } finally {
      setBusy(false);
    }
  }

  if (!isAdmin) {
    return (
      <section className="resource-view">
        <div className="resource-heading">
          <div><p className="eyebrow"><Users size={14} /> 团队空间</p><h1>成员管理</h1><p>只有管理员可以查看和管理团队账号。</p></div>
        </div>
        <div className="panel"><div className="panel-placeholder"><ShieldCheck size={24} /><p>当前账号是普通成员，没有成员管理权限。</p></div></div>
      </section>
    );
  }

  return (
    <section className="resource-view">
      <div className="resource-heading">
        <div><p className="eyebrow"><Users size={14} /> 团队空间</p><h1>成员管理</h1><p>每个成员有独立的评测空间和账号。账号由管理员分配，成员登录后只能看到自己的数据。</p></div>
        <div className="heading-actions">
          <button type="button" className="outline-button" onClick={() => void load()} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新</button>
          <button type="button" className="primary" onClick={() => { setFormOpen(true); setNotice(null); }}><Plus size={17} /> 邀请成员</button>
        </div>
      </div>

      {notice && <div className={`inline-notice ${notice.tone}`} role="status"><CircleAlert size={16} /> {notice.text}</div>}

      {formOpen && (
        <form className="panel connection-form" onSubmit={(event) => void createMember(event)}>
          <div className="panel-heading">
            <div><p className="eyebrow"><UserPlus size={14} /> 新账号</p><h2>邀请成员</h2></div>
            <button type="button" className="icon-button" aria-label="关闭邀请表单" title="关闭" onClick={() => setFormOpen(false)}><X size={17} /></button>
          </div>
          <div className="connection-form-body">
            <div className="field-grid two">
              <label className="field-label">邮箱<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="member@example.com" autoComplete="off" required /></label>
              <label className="field-label">姓名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="成员姓名" required /></label>
            </div>
            <div className="field-grid two">
              <label className="field-label">初始密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 个字符" autoComplete="new-password" required /></label>
              <label className="field-label">角色<select value={role} onChange={(event) => setRole(event.target.value as "admin" | "member")}><option value="member">成员</option><option value="admin">管理员</option></select></label>
            </div>
            <div className="editor-actions">
              <button type="button" className="outline-button" onClick={() => setFormOpen(false)}>取消</button>
              <button type="submit" className="primary" disabled={busy}><UserPlus size={16} /> 创建账号</button>
            </div>
          </div>
        </form>
      )}

      <section className="panel connection-detail">
        <div className="panel-heading">
          <div><p className="eyebrow"><Users size={14} /> 全部账号</p><h2>{total} 位成员</h2></div>
        </div>
        {loading ? (
          <div className="panel-placeholder"><CircleDashed className="spin" size={22} /><p>正在加载成员…</p></div>
        ) : members.length === 0 ? (
          <div className="panel-placeholder"><UserCog size={24} /><p>还没有成员。点击右上角「邀请成员」创建第一个账号。</p></div>
        ) : (
          <div className="connection-list">
            {members.map((member) => (
              <div className="connection-list-item" key={member.id}>
                <span className="connection-type"><Users size={15} /></span>
                <span className="connection-list-copy">
                  <strong>{member.display_name} <span className={`role-badge ${member.role}`}>{roleLabel(member.role)}</span></strong>
                  <small>{member.email} · 空间 {member.project_id}</small>
                  <small>创建于 {formatDate(member.created_at)} · 最近登录 {formatDate(member.last_login_at)}</small>
                </span>
                <i className={member.active ? "enabled" : "disabled"}>{member.active ? "已启用" : "已停用"}</i>
                <div className="provider-actions">
                  <button type="button" className="icon-button" title="重置密码" aria-label={`重置 ${member.email} 的密码`} onClick={() => { setResetTarget(member); setResetPassword(""); }} disabled={busy}><KeyRound size={15} /></button>
                  <button type="button" className="outline-button compact" onClick={() => void toggleActive(member)} disabled={busy || member.id === auth.userId} title={member.id === auth.userId ? "不能停用自己" : member.active ? "停用账号" : "启用账号"}>{member.active ? "停用" : "启用"}</button>
                  <button type="button" className="icon-button danger-button" title={member.id === auth.userId ? "不能删除自己" : "删除成员"} aria-label={`删除 ${member.email}`} onClick={() => setDeleteTarget(member)} disabled={busy || member.id === auth.userId}><Trash2 size={15} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {resetTarget && (
        <form className="panel connection-form provider-rotate" onSubmit={(event) => void resetMemberPassword(event)}>
          <div className="panel-heading">
            <div><p className="eyebrow"><KeyRound size={14} /> 重置密码</p><h2>{resetTarget.email}</h2></div>
            <button type="button" className="icon-button" aria-label="关闭重置密码" title="关闭" onClick={() => setResetTarget(null)}><X size={17} /></button>
          </div>
          <div className="connection-form-body">
            <label className="field-label">新密码<input type="password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} placeholder="至少 8 个字符" autoComplete="new-password" required /></label>
            <div className="editor-actions">
              <button type="button" className="outline-button" onClick={() => setResetTarget(null)}>取消</button>
              <button type="submit" className="primary" disabled={busy}><KeyRound size={16} /> 确认重置</button>
            </div>
          </div>
        </form>
      )}

      {deleteTarget && (
        <div className="confirm-backdrop" role="presentation">
          <div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-member-title">
            <div className="panel-heading">
              <div><p className="eyebrow"><Trash2 size={14} /> 删除账号</p><h2 id="delete-member-title">删除 {deleteTarget.email}？</h2></div>
              <button type="button" className="icon-button" aria-label="关闭删除确认" title="关闭" onClick={() => setDeleteTarget(null)} disabled={busy}><X size={17} /></button>
            </div>
            <div className="confirm-dialog-body">
              <p>删除后该账号及其独立数据空间（Project {deleteTarget.project_id}）会被永久删除，无法恢复。请确认。</p>
              <div className="editor-actions">
                <button type="button" className="outline-button" onClick={() => setDeleteTarget(null)} disabled={busy}>取消</button>
                <button type="button" className="danger-button-text" onClick={() => void deleteMember(deleteTarget)} disabled={busy}>{busy ? "删除中..." : "确认删除"}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
