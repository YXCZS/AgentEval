"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  CircleAlert,
  CircleDashed,
  Clipboard,
  GitBranch,
  KeyRound,
  Package,
  Plus,
  RefreshCw,
  RotateCw,
  Save,
  Terminal,
  Trash2,
  X,
} from "lucide-react";

type AgentType = "rag" | "tool" | "custom";
type Release = {
  id: string;
  project_id: string;
  version: number;
  label: string;
  agent_type: AgentType;
  release_identity: string;
  source_revision: string | null;
  metadata: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
};
type ProjectKey = {
  id: string;
  name: string;
  key_prefix: string;
  active: boolean;
  created_at: string;
  last_used_at: string | null;
};
type CreatedProjectKey = ProjectKey & { key: string };
type ProviderConnection = {
  id: string;
  name: string;
  provider: "openai_compatible";
  base_url: string;
  model: string;
  default_parameters: Record<string, unknown>;
  credential_mask: string;
  credential_key_id: string;
  status: "pending_validation" | "active" | "error" | "disabled";
  enabled: boolean;
  tested_at: string | null;
};
type ProviderTest = {
  provider: string;
  configured_model: string;
  response_model: string;
  upstream_request_id: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};
type Notice = { tone: "success" | "danger" | "neutral"; text: string };

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const PROJECT_ID = process.env.NEXT_PUBLIC_PROJECT_ID ?? "default-project";
const SESSION = process.env.NEXT_PUBLIC_WORKSPACE_SESSION ?? "";
const typeLabels: Record<AgentType, string> = {
  rag: "RAG Agent",
  tool: "Tool Agent",
  custom: "自定义 Agent",
};

function headers(): HeadersInit {
  return { "Content-Type": "application/json", "X-Workspace-Session": SESSION };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers(), ...init?.headers },
    cache: "no-store",
  });
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)).join("；"));
    }
    throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
  }
  return body as T;
}

function formatDate(value: string | null): string {
  if (!value) return "尚未使用";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("默认参数必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

export function ConnectionsView() {
  const [releases, setReleases] = useState<Release[]>([]);
  const [keys, setKeys] = useState<ProjectKey[]>([]);
  const [providers, setProviders] = useState<ProviderConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [releaseFormOpen, setReleaseFormOpen] = useState(false);
  const [keyFormOpen, setKeyFormOpen] = useState(false);
  const [providerFormOpen, setProviderFormOpen] = useState(false);
  const [rotateTarget, setRotateTarget] = useState<ProviderConnection | null>(null);
  const [createdKey, setCreatedKey] = useState("");
  const [keyName, setKeyName] = useState("本地 SDK");
  const [label, setLabel] = useState("");
  const [agentType, setAgentType] = useState<AgentType>("tool");
  const [releaseIdentity, setReleaseIdentity] = useState("");
  const [sourceRevision, setSourceRevision] = useState("");
  const [providerName, setProviderName] = useState("");
  const [providerBaseUrl, setProviderBaseUrl] = useState("https://api.deepseek.com/v1");
  const [providerModel, setProviderModel] = useState("deepseek-chat");
  const [providerParameters, setProviderParameters] = useState('{"temperature":0}');
  const [providerTest, setProviderTest] = useState<ProviderTest | null>(null);
  const providerKeyRef = useRef<HTMLInputElement>(null);
  const rotateKeyRef = useRef<HTMLInputElement>(null);

  const sdkEnvironment = useMemo(
    () => [
      `AGENT_EVAL_API_URL=${API_URL}`,
      `AGENT_EVAL_PROJECT_ID=${PROJECT_ID}`,
      "AGENT_EVAL_API_KEY=<仅粘贴刚创建的 Project Key>",
    ].join("\n"),
    [],
  );

  async function load() {
    setLoading(true);
    try {
      const [releaseRows, keyRows, providerRows] = await Promise.all([
        requestJson<Release[]>(`/projects/${PROJECT_ID}/agent-releases`),
        requestJson<ProjectKey[]>(`/projects/${PROJECT_ID}/api-keys`),
        requestJson<ProviderConnection[]>(`/projects/${PROJECT_ID}/provider-connections`),
      ]);
      setReleases(releaseRows);
      setKeys(keyRows);
      setProviders(providerRows);
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "接入信息加载失败。" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  function invalidateProviderTest() {
    setProviderTest(null);
  }

  async function copyText(value: string, labelText: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice({ tone: "success", text: `${labelText}已复制。` });
    } catch {
      setNotice({ tone: "danger", text: "浏览器拒绝了剪贴板操作，请手动选择文本。" });
    }
  }

  async function createRelease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await requestJson<Release>(`/projects/${PROJECT_ID}/agent-releases`, {
        method: "POST",
        body: JSON.stringify({ label: label.trim(), agent_type: agentType, release_identity: releaseIdentity.trim(), source_revision: sourceRevision.trim() || null, metadata: {} }),
      });
      setReleases((current) => [created, ...current]);
      setReleaseFormOpen(false);
      setLabel(""); setReleaseIdentity(""); setSourceRevision("");
      setNotice({ tone: "success", text: `Release“${created.label}”已登记。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "创建 Release 失败。" });
    } finally { setBusy(false); }
  }

  async function createKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setCreatedKey("");
    try {
      const created = await requestJson<CreatedProjectKey>(`/projects/${PROJECT_ID}/api-keys`, { method: "POST", body: JSON.stringify({ name: keyName.trim() }) });
      setKeys((current) => [created, ...current]);
      setCreatedKey(created.key); setKeyFormOpen(false);
      setNotice({ tone: "success", text: "Project Key 已创建，明文只显示这一次。" });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "创建 Project Key 失败。" });
    } finally { setBusy(false); }
  }

  async function revokeKey(key: ProjectKey) {
    setBusy(true);
    try {
      await requestJson<void>(`/projects/${PROJECT_ID}/api-keys/${key.id}`, { method: "DELETE" });
      setKeys((current) => current.map((item) => item.id === key.id ? { ...item, active: false } : item));
      setNotice({ tone: "success", text: `Project Key“${key.name}”已撤销。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "撤销 Project Key 失败。" });
    } finally { setBusy(false); }
  }

  function providerPayload(apiKey: string) {
    return {
      provider: "openai_compatible",
      base_url: providerBaseUrl.trim(),
      model: providerModel.trim(),
      api_key: apiKey,
      default_parameters: parseJsonObject(providerParameters),
    };
  }

  async function testProvider() {
    const apiKey = providerKeyRef.current?.value.trim() ?? "";
    if (!apiKey) { setNotice({ tone: "danger", text: "请填写 Provider API Key 后再测试。" }); return; }
    setBusy(true); setNotice(null);
    try {
      const result = await requestJson<ProviderTest>(`/projects/${PROJECT_ID}/provider-connections/test`, { method: "POST", body: JSON.stringify(providerPayload(apiKey)) });
      setProviderTest(result);
      setNotice({ tone: "success", text: "真实 Provider 测试成功。请重新输入 API Key 后保存连接。" });
    } catch (error) {
      setProviderTest(null);
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "Provider 测试失败。" });
    } finally {
      if (providerKeyRef.current) providerKeyRef.current.value = "";
      setBusy(false);
    }
  }

  async function createProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const apiKey = providerKeyRef.current?.value.trim() ?? "";
    if (!providerTest) { setNotice({ tone: "danger", text: "请先完成真实 Provider 测试。" }); return; }
    if (!apiKey) { setNotice({ tone: "danger", text: "请重新输入 API Key 后保存连接。" }); return; }
    setBusy(true); setNotice(null);
    try {
      const created = await requestJson<ProviderConnection>(`/projects/${PROJECT_ID}/provider-connections`, {
        method: "POST",
        body: JSON.stringify({ name: providerName.trim(), ...providerPayload(apiKey) }),
      });
      setProviders((current) => [created, ...current]);
      setProviderFormOpen(false); setProviderTest(null); setProviderName("");
      setNotice({ tone: "success", text: `Provider“${created.name}”已加密保存。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "保存 Provider 失败。" });
    } finally {
      if (providerKeyRef.current) providerKeyRef.current.value = "";
      setBusy(false);
    }
  }

  async function toggleProvider(provider: ProviderConnection) {
    setBusy(true);
    try {
      const updated = await requestJson<ProviderConnection>(`/projects/${PROJECT_ID}/provider-connections/${provider.id}/enabled?enabled=${!provider.enabled}`, { method: "PATCH" });
      setProviders((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice({ tone: "success", text: `Provider“${updated.name}”已${updated.enabled ? "启用" : "停用"}。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "更新 Provider 状态失败。" });
    } finally { setBusy(false); }
  }

  async function rotateProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!rotateTarget) return;
    const apiKey = rotateKeyRef.current?.value.trim() ?? "";
    if (!apiKey) { setNotice({ tone: "danger", text: "请填写新的 Provider API Key。" }); return; }
    setBusy(true);
    try {
      const updated = await requestJson<ProviderConnection>(`/projects/${PROJECT_ID}/provider-connections/${rotateTarget.id}/rotate`, { method: "POST", body: JSON.stringify({ api_key: apiKey }) });
      setProviders((current) => current.map((item) => item.id === updated.id ? updated : item));
      setRotateTarget(null);
      setNotice({ tone: "success", text: `Provider“${updated.name}”的凭据已轮换并重新验证。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "轮换 Provider 凭据失败。" });
    } finally {
      if (rotateKeyRef.current) rotateKeyRef.current.value = "";
      setBusy(false);
    }
  }

  async function deleteProvider(provider: ProviderConnection) {
    if (!window.confirm(`删除 Provider“${provider.name}”？已绑定的 Judge 版本不能删除其 Provider。`)) return;
    setBusy(true);
    try {
      await requestJson<void>(`/projects/${PROJECT_ID}/provider-connections/${provider.id}`, { method: "DELETE" });
      setProviders((current) => current.filter((item) => item.id !== provider.id));
      setNotice({ tone: "success", text: `Provider“${provider.name}”已删除。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "删除 Provider 失败。" });
    } finally { setBusy(false); }
  }

  return <section className="resource-view connections-workbench">
    <div className="resource-heading"><div><p className="eyebrow"><Package size={14} /> SDK 执行身份</p><h1>Release 与接入</h1><p>Agent 在你的 SDK 进程中运行。平台只保存版本身份、评测证据，以及专供 LLM Judge 使用的加密 Provider 连接。</p></div><div className="heading-actions"><button type="button" className="outline-button" onClick={() => void load()} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新</button><button type="button" className="primary" onClick={() => setReleaseFormOpen(true)}><Plus size={17} /> 登记 Release</button></div></div>
    {notice && <div className={`inline-notice ${notice.tone}`} role="status"><CircleAlert size={16} /> {notice.text}</div>}

    {releaseFormOpen && <form className="panel connection-form" onSubmit={(event) => void createRelease(event)}><div className="panel-heading"><div><p className="eyebrow">不可变版本</p><h2>登记 Agent Release</h2></div><button type="button" className="icon-button" aria-label="关闭 Release 表单" title="关闭" onClick={() => setReleaseFormOpen(false)}><X size={17} /></button></div><div className="connection-form-body"><div className="field-grid two"><label className="field-label">Release 名称<input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="例如 candidate-2026-09" required /></label><label className="field-label">Agent 类型<select value={agentType} onChange={(event) => setAgentType(event.target.value as AgentType)}>{Object.entries(typeLabels).map(([value, text]) => <option value={value} key={value}>{text}</option>)}</select></label></div><div className="field-grid two"><label className="field-label">唯一版本标识<input value={releaseIdentity} onChange={(event) => setReleaseIdentity(event.target.value)} placeholder="例如 git:8f14e45" required /><small>使用 Git SHA、镜像 digest 或配置哈希。</small></label><label className="field-label">源码 revision（可选）<input value={sourceRevision} onChange={(event) => setSourceRevision(event.target.value)} placeholder="例如 8f14e45" /></label></div><div className="editor-actions"><button type="button" className="outline-button" onClick={() => setReleaseFormOpen(false)}>取消</button><button type="submit" className="primary" disabled={busy}><Save size={16} /> 保存 Release</button></div></div></form>}

    <div className="connections-layout"><section className="panel connection-detail"><div className="panel-heading"><div><p className="eyebrow"><GitBranch size={14} /> 版本目录</p><h2>{releases.length} 个 Release</h2></div></div>{loading ? <div className="panel-placeholder"><CircleDashed className="spin" size={22} /><p>正在加载 Release...</p></div> : releases.length === 0 ? <div className="panel-placeholder"><Package size={24} /><p>还没有 Release。先登记当前 Agent 的真实版本身份。</p><button type="button" className="primary" onClick={() => setReleaseFormOpen(true)}><Plus size={16} /> 登记 Release</button></div> : <div className="release-list">{releases.map((release) => <div className="release-row" key={release.id}><div className="release-version"><strong>v{release.version}</strong><span>{release.label}</span></div><div className="release-identity"><GitBranch size={13} /><code>{release.release_identity}</code><small>{typeLabels[release.agent_type]} · {formatDate(release.created_at)}</small></div><div className="release-endpoint"><span>SDK Task</span><small>{release.source_revision ? `revision ${release.source_revision}` : "未填写源码 revision"}</small></div><span className={`status ${release.enabled ? "status-success" : "status-warning"}`}>{release.enabled && <Check size={13} />}{release.enabled ? "可用" : "停用"}</span></div>)}</div>}</section>
      <aside className="panel connection-catalog"><div className="panel-heading"><div><p className="eyebrow"><KeyRound size={14} /> Project 凭据</p><h2>SDK API Key</h2></div><button type="button" className="icon-button" title="创建 Project Key" aria-label="创建 Project Key" onClick={() => setKeyFormOpen(true)}><Plus size={17} /></button></div><p className="connection-description">这是 SDK 访问本项目的 Key，不是模型 API Key。服务端只保存哈希。</p>{keyFormOpen && <form className="release-form" onSubmit={(event) => void createKey(event)}><label className="field-label">凭据名称<input value={keyName} onChange={(event) => setKeyName(event.target.value)} required /></label><div className="editor-actions"><button type="button" className="outline-button" onClick={() => setKeyFormOpen(false)}>取消</button><button type="submit" className="primary" disabled={busy}><KeyRound size={15} /> 创建</button></div></form>}{createdKey && <div className="created-case"><strong>请立即保存，关闭后无法再次查看</strong><code className="trace-json">{createdKey}</code><button type="button" className="outline-button compact" onClick={() => void copyText(createdKey, "Project Key")}><Clipboard size={14} /> 复制 Key</button></div>}<div className="connection-list">{keys.map((key) => <div className="connection-list-item" key={key.id}><span className="connection-type"><KeyRound size={15} /></span><span className="connection-list-copy"><strong>{key.name}</strong><small>{key.key_prefix}... · {key.last_used_at ? `最近使用 ${formatDate(key.last_used_at)}` : "尚未使用"}</small></span><i className={key.active ? "enabled" : "disabled"}>{key.active ? "有效" : "已撤销"}</i>{key.active && <button type="button" className="icon-button" title="撤销 Project Key" aria-label={`撤销 ${key.name}`} onClick={() => void revokeKey(key)} disabled={busy}><Trash2 size={15} /></button>}</div>)}</div></aside></div>

    <section className="panel connection-detail provider-section"><div className="panel-heading"><div><p className="eyebrow"><KeyRound size={14} /> LLM Judge 凭据</p><h2>Provider 连接</h2></div><button type="button" className="primary" onClick={() => { setProviderFormOpen(true); setProviderTest(null); }}><Plus size={16} /> 添加 Provider</button></div><p className="connection-description">只用于平台执行 LLM Judge。API Key 仅在提交时进入请求，测试、保存和轮换完成后都会从浏览器输入框清除，读取接口只返回掩码。</p>
      {providerFormOpen && <form className="connection-form-body provider-form" onSubmit={(event) => void createProvider(event)}><div className="field-grid two"><label className="field-label">连接名称<input aria-label="Provider 连接名称" value={providerName} onChange={(event) => { setProviderName(event.target.value); invalidateProviderTest(); }} placeholder="例如 DeepSeek Judge" required /></label><label className="field-label">协议<select aria-label="Provider 协议" disabled><option>OpenAI-compatible Chat Completions</option></select></label></div><div className="field-grid two"><label className="field-label">Base URL<input aria-label="Provider Base URL" value={providerBaseUrl} onChange={(event) => { setProviderBaseUrl(event.target.value); invalidateProviderTest(); }} required /></label><label className="field-label">模型<input aria-label="Provider 模型" value={providerModel} onChange={(event) => { setProviderModel(event.target.value); invalidateProviderTest(); }} required /></label></div><label className="field-label">默认参数 JSON<textarea aria-label="Provider 默认参数" value={providerParameters} onChange={(event) => { setProviderParameters(event.target.value); invalidateProviderTest(); }} rows={3} /></label><label className="field-label">API Key<input ref={providerKeyRef} aria-label="Provider API Key" type="password" autoComplete="off" placeholder="仅用于本次提交，不会保存到页面状态" required /><small>测试会发送一次最小真实请求并立即清空此输入。保存时需要重新输入，服务端会再次验证并 AES-GCM 加密。</small></label>{providerTest && <div className="connection-result success" role="status"><strong>测试成功</strong><span>{providerTest.response_model}</span><small>请求 ID：{providerTest.upstream_request_id} · Token：{providerTest.total_tokens}</small></div>}<div className="editor-actions"><button type="button" className="outline-button" onClick={() => { setProviderFormOpen(false); setProviderTest(null); if (providerKeyRef.current) providerKeyRef.current.value = ""; }}>取消</button><button type="button" className="outline-button" onClick={() => void testProvider()} disabled={busy}><RefreshCw size={16} /> 测试连接</button><button type="submit" className="primary" disabled={busy || !providerTest}><Save size={16} /> 加密保存</button></div></form>}
      <div className="provider-list">{providers.length === 0 ? <div className="panel-placeholder"><KeyRound size={22} /><p>没有 Provider 连接。添加并验证真实模型连接后，才能创建平台托管的 LLM Judge。</p></div> : providers.map((provider) => <div className="provider-row" key={provider.id}><div className="provider-summary"><strong>{provider.name}</strong><small>{provider.model} · {provider.base_url}</small><small>Key：{provider.credential_mask} · 加密密钥：{provider.credential_key_id} · 最近验证：{formatDate(provider.tested_at)}</small></div><span className={`status ${provider.enabled ? "status-success" : "status-warning"}`}>{provider.enabled ? "已启用" : "已停用"}</span><div className="provider-actions"><button type="button" className="icon-button" title="轮换 Provider Key" aria-label={`轮换 ${provider.name} 的 Key`} onClick={() => setRotateTarget(provider)} disabled={busy}><RotateCw size={16} /></button><button type="button" className="outline-button compact" onClick={() => void toggleProvider(provider)} disabled={busy}>{provider.enabled ? "停用" : "启用"}</button><button type="button" className="icon-button danger-button" title="删除 Provider" aria-label={`删除 ${provider.name}`} onClick={() => void deleteProvider(provider)} disabled={busy}><Trash2 size={16} /></button></div></div>)}</div></section>

    {rotateTarget && <form className="panel connection-form provider-rotate" onSubmit={(event) => void rotateProvider(event)}><div className="panel-heading"><div><p className="eyebrow">凭据轮换</p><h2>{rotateTarget.name}</h2></div><button type="button" className="icon-button" aria-label="关闭凭据轮换" title="关闭" onClick={() => setRotateTarget(null)}><X size={17} /></button></div><div className="connection-form-body"><label className="field-label">新的 Provider API Key<input ref={rotateKeyRef} aria-label="新的 Provider API Key" type="password" autoComplete="off" placeholder="提交后会被清空" required /></label><div className="editor-actions"><button type="button" className="outline-button" onClick={() => setRotateTarget(null)}>取消</button><button type="submit" className="primary" disabled={busy}><RotateCw size={16} /> 验证并轮换</button></div></div></form>}

    <section className="panel connection-detail"><div className="panel-heading"><div><p className="eyebrow"><Terminal size={14} /> 本地配置</p><h2>安装并连接 SDK</h2></div></div><div className="connection-form-body"><div className="detail-block"><h3>安装</h3><pre>pip install agent-eval-sdk</pre><button type="button" className="outline-button compact" onClick={() => void copyText("pip install agent-eval-sdk", "安装命令")}><Clipboard size={14} /> 复制</button></div><div className="detail-block"><h3>环境变量</h3><pre>{sdkEnvironment}</pre><button type="button" className="outline-button compact" onClick={() => void copyText(sdkEnvironment, "环境变量模板")}><Clipboard size={14} /> 复制</button></div></div></section>
  </section>;
}
