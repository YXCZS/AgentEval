"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Package,
  ClipboardCheck,
  Check,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Database,
  FileChartColumn,
  FlaskConical,
  Gauge,
  LogOut,
  Play,
  Search,
  ShieldCheck,
  Users,
  Workflow,
  XCircle,
} from "lucide-react";
import { ConnectionsView } from "./connections-view";
import { AnnotationsView } from "./annotations-view";
import { DatasetsView } from "./datasets-view";
import { EvaluatorsView } from "./evaluators-view";
import { ReportsView } from "./reports-view";
import { RunsView } from "./runs-view";
import { TracesView } from "./traces-view";
import { LoginView } from "./login-view";
import { MembersView } from "./members-view";
import { API_URL, getProjectId, getSessionToken, fetchApi } from "./api-client";
import { AuthProvider, useAuth } from "./auth-context";

type ViewKey =
  | "overview"
  | "datasets"
  | "evaluators"
  | "annotations"
  | "runs"
  | "traces"
  | "regression"
  | "gates"
  | "connections"
  | "members";

type IconComponent = typeof Gauge;
type TraceSummary = {
  trace_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  span_count: number;
  created_at: string;
};
type TraceSummaryPage = { items: TraceSummary[]; total: number };
type Dataset = { id: string; name: string; current_version_id: string | null };
type Run = {
  id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
  total_cases: number;
  completed_cases: number;
  failed_cases: number;
  created_at: string;
};
type ExperimentPage = { items: Run[]; total: number };
type ReportSummary = {
  run_id: string;
  status: Run["status"];
  metrics: Array<{ metric_name: string; pass_rate: number | null }>;
  created_at: string;
};
type OverviewData = {
  traces: TraceSummary[];
  traceTotal: number;
  datasets: Dataset[];
  runs: Run[];
  runTotal: number;
  reports: ReportSummary[];
};

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeTracePage(value: unknown): TraceSummaryPage {
  const page = value && typeof value === "object" ? value as { items?: unknown; total?: unknown } : {};
  return { items: asArray<TraceSummary>(page.items), total: asNumber(page.total) };
}

function normalizeRunPage(value: unknown): ExperimentPage {
  const page = value && typeof value === "object" ? value as { items?: unknown; total?: unknown } : {};
  return { items: asArray<Run>(page.items), total: asNumber(page.total) };
}

const navItems: Array<{ key: ViewKey; label: string; icon: IconComponent }> = [
  { key: "overview", label: "总览", icon: Gauge },
  { key: "traces", label: "Trace", icon: Workflow },
  { key: "datasets", label: "数据集", icon: Database },
  { key: "runs", label: "实验", icon: Activity },
  { key: "evaluators", label: "评估器", icon: FlaskConical },
  { key: "annotations", label: "人工评审", icon: ClipboardCheck },
  { key: "regression", label: "回归分析", icon: FileChartColumn },
  { key: "gates", label: "发布门禁", icon: ShieldCheck },
  { key: "connections", label: "Release 与接入", icon: Package },
];

const adminOnlyNavItems: Array<{ key: ViewKey; label: string; icon: IconComponent }> = [
  { key: "members", label: "成员管理", icon: Users },
];

const viewLabels: Record<ViewKey, string> = Object.fromEntries(
  [...navItems, ...adminOnlyNavItems].map((item) => [item.key, item.label]),
) as Record<ViewKey, string>;

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${getSessionToken()}`,
    },
    cache: "no-store",
  });
  const body = (await response.json().catch(() => null)) as
    | { detail?: unknown }
    | T
    | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `API 请求失败（HTTP ${response.status}）`,
    );
  }
  return body as T;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusLabel(status: Run["status"] | TraceSummary["status"]): string {
  return {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
  }[status];
}

function statusTone(status: Run["status"] | TraceSummary["status"]): "success" | "warning" | "danger" | "live" | "neutral" {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "partial" || status === "cancelled") return "warning";
  if (status === "running" || status === "queued") return "live";
  return "neutral";
}

function StatusMark({ status }: { status: Run["status"] | TraceSummary["status"] }) {
  const Icon = status === "completed" ? CircleCheck : status === "failed" ? XCircle : CircleDashed;
  return (
    <span className={`status status-${statusTone(status)}`}>
      <Icon size={14} />
      {statusLabel(status)}
    </span>
  );
}

function Overview({ onNavigate }: { onNavigate: (view: ViewKey) => void }) {
  const [data, setData] = useState<OverviewData | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadOverview() {
    setBusy(true);
    setError(null);
    try {
      const [tracePage, datasets, runPage, reports] = await Promise.all([
        requestJson<TraceSummaryPage>(`/projects/${getProjectId()}/traces?limit=25`),
        requestJson<Dataset[]>(`/projects/${getProjectId()}/datasets`),
        requestJson<ExperimentPage>(`/projects/${getProjectId()}/runs?limit=25`),
        requestJson<ReportSummary[]>(`/projects/${getProjectId()}/reports`),
      ]);
      const normalizedTracePage = normalizeTracePage(tracePage);
      const normalizedRunPage = normalizeRunPage(runPage);
      setData({
        traces: normalizedTracePage.items,
        traceTotal: normalizedTracePage.total,
        datasets: asArray<Dataset>(datasets),
        runs: normalizedRunPage.items,
        runTotal: normalizedRunPage.total,
        reports: asArray<ReportSummary>(reports),
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "总览数据加载失败");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadOverview();
  }, []);

  const stats = useMemo(() => {
    if (!data) return { traces: 0, datasets: 0, runs: 0, failures: 0 };
    const traceFailures = data.traces.filter((item) => item.status === "failed").length;
    const runFailures = data.runs.reduce((sum, item) => sum + item.failed_cases, 0);
    return {
      traces: data.traceTotal,
      datasets: data.datasets.length,
      runs: data.runTotal,
      failures: traceFailures + runFailures,
    };
  }, [data]);

  const recentRuns = data?.runs.slice(0, 4) ?? [];
  const recentReports = data?.reports.slice(0, 3) ?? [];
  const isEmpty = !busy && !error && stats.traces + stats.datasets + stats.runs === 0;

  return (
    <section className="overview-workbench">
      <section className="welcome-band">
        <div>
          <p className="eyebrow"><Workflow size={14} /> 当前项目 · {getProjectId()}</p>
          <h1>评测总览</h1>
          <p className="lede">查看真实 Agent 上报的 Trace、Experiment、评分和发布结论。</p>
        </div>
      </section>

      {busy && <div className="overview-state panel"><CircleDashed className="spin" size={22} /><strong>正在读取项目数据...</strong><span>总览只展示 API 返回的真实数据。</span></div>}
      {error && <div className="overview-state panel error"><CircleAlert size={22} /><strong>总览加载失败</strong><span>{error}</span><button className="outline-button" onClick={() => void loadOverview()}>重新加载</button></div>}

      {!busy && !error && (
        <>
          <section className="metric-grid" aria-label="项目实时统计">
            <MetricTile label="Trace 数量" value={stats.traces} accent="" onClick={() => onNavigate("traces")} />
            <MetricTile label="数据集数量" value={stats.datasets} accent="accent-teal" onClick={() => onNavigate("datasets")} />
            <MetricTile label="Experiment 数量" value={stats.runs} accent="accent-gold" onClick={() => onNavigate("runs")} />
            <MetricTile label="当前页失败证据" value={stats.failures} accent="accent-coral" onClick={() => onNavigate("traces")} />
          </section>

          {isEmpty ? (
            <section className="overview-empty panel">
              <div className="overview-empty-icon"><Workflow size={25} /></div>
              <div>
                <p className="eyebrow">项目还没有执行证据</p>
                <h2>从真实 SDK Experiment 开始</h2>
                <p>先创建 Project Key、登记 Agent Release 和版本化数据集，再由本地 Python SDK 运行你的真实 Agent。模型 Key 始终留在 Agent 进程中，平台不会读取。</p>
                <div className="quick-actions"><button className="primary" onClick={() => onNavigate("connections")}><Package size={16} />配置 Release 与 Key</button><button onClick={() => onNavigate("datasets")}><Database size={16} />创建数据集</button></div>
              </div>
            </section>
          ) : (
            <section className="content-grid">
              <article className="panel runs-panel">
                <div className="panel-heading"><div><p className="eyebrow">最近质量工作</p><h2>最近的 Experiment</h2></div><button className="text-button" onClick={() => onNavigate("runs")}>查看全部 <ArrowUpRight size={15} /></button></div>
                <div className="run-list">
                  {recentRuns.length === 0 && <EmptyLine text="还没有 Experiment，先创建数据集并登记 Agent Release。" />}
                  {recentRuns.map((run) => {
                    const terminalCases = Math.min(run.total_cases, run.completed_cases + run.failed_cases);
                    const successRate = terminalCases > 0
                      ? `${Math.round((run.completed_cases / terminalCases) * 100)}%`
                      : "-";
                    return <div className="run-row" key={run.id}><div className="run-icon"><Play size={16} /></div><div className="run-main"><strong>{run.id}</strong><span>{terminalCases} / {run.total_cases} 个用例已终态 · {formatDate(run.created_at)}</span></div><div className="run-score"><strong>{successRate}</strong><span>已执行成功率</span></div><StatusMark status={run.status} /></div>;
                  })}
                </div>
              </article>
              <article className="panel signal-panel">
                <div className="panel-heading"><div><p className="eyebrow">评测报告</p><h2>最近的质量信号</h2></div><button className="icon-button" title="打开回归分析" aria-label="打开回归分析" onClick={() => onNavigate("regression")}><ArrowUpRight size={17} /></button></div>
                <div className="signal-list">
                  {recentReports.length === 0 && <EmptyLine text="完成一次 Experiment 后，这里会显示评测指标。" />}
                  {recentReports.map((report) => { const score = report.metrics.find((metric) => metric.pass_rate !== null)?.pass_rate; return <div className="signal-row" key={report.run_id}><div><strong>{report.run_id}</strong><span>{formatDate(report.created_at)}</span></div><b>{score === null || score === undefined ? "-" : `${Math.round(score * 100)}%`}</b><div className="bar"><i style={{ width: `${score === null || score === undefined ? 0 : score * 100}%` }} /></div></div>; })}
                </div>
                <div className="signal-foot"><ShieldCheck size={16} /> Gate 状态：尚未在总览中执行</div>
              </article>
            </section>
          )}
        </>
      )}
    </section>
  );
}

function MetricTile({ label, value, accent, onClick }: { label: string; value: number; accent: string; onClick: () => void }) {
  return <button className={`metric-tile ${accent}`} onClick={onClick}><span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-change muted">查看详情 <ArrowUpRight size={13} /></span></button>;
}

function EmptyLine({ text }: { text: string }) {
  return <div className="overview-line-empty"><Check size={15} />{text}</div>;
}

function ApiStatus() {
  const [status, setStatus] = useState<"checking" | "connected" | "unavailable">("checking");

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const response = await fetch(`${API_URL}/health`, { cache: "no-store" });
        const body = await response.json().catch(() => null) as { status?: unknown } | null;
        if (active) setStatus(response.ok && body?.status === "ok" ? "connected" : "unavailable");
      } catch {
        if (active) setStatus("unavailable");
      }
    };
    void check();
    const timer = window.setInterval(() => void check(), 30_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const text = status === "checking" ? "API 检查中" : status === "connected" ? "API 已连接" : "API 不可用";
  return <div className={`system-status ${status}`} role="status"><span className="status-dot" />{text}<span>v0.1.0</span></div>;
}

export default function Home() {
  const [activeView, setActiveView] = useState<ViewKey>("overview");
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const { auth, ready: authReady, login: handleAuthed, logout: handleLogout } = useAuth();

  function logout() {
    handleLogout();
    setActiveView("overview");
  }

  function readLocation() {
    const params = new URLSearchParams(window.location.search);
    const requestedView = params.get("view");
    const allNavItems = [...navItems, ...adminOnlyNavItems];
    const view = allNavItems.some((item) => item.key === requestedView)
      ? requestedView as ViewKey
      : "overview";
    setActiveView(view);
    setSelectedRunId(view === "runs" ? params.get("run_id") ?? "" : "");
    setSelectedTraceId(view === "traces" ? params.get("trace_id") ?? "" : "");
  }

  function navigate(view: ViewKey, resource?: { runId?: string; traceId?: string }) {
    const params = new URLSearchParams();
    if (view !== "overview") params.set("view", view);
    if (view === "runs" && resource?.runId) params.set("run_id", resource.runId);
    if (view === "traces" && resource?.traceId) params.set("trace_id", resource.traceId);
    const queryString = params.toString();
    window.history.pushState(null, "", queryString ? `/?${queryString}` : "/");
    readLocation();
  }

  useEffect(() => {
    readLocation();
    window.addEventListener("popstate", readLocation);
    return () => window.removeEventListener("popstate", readLocation);
  }, []);

  function openTrace(traceId: string) {
    navigate("traces", { traceId });
  }

  function submitSearch() {
    const match = navItems.find((item) => item.label.toLowerCase().includes(query.trim().toLowerCase()));
    if (!query.trim()) { setNotice("请输入要搜索的导航名称。"); return; }
    if (!match) { setNotice(`没有找到“${query.trim()}”对应的页面。`); return; }
    navigate(match.key); setSearchOpen(false); setQuery(""); setNotice("");
  }

  const activeLabel = viewLabels[activeView];

  if (!authReady) {
    return <div className="app-shell"><div className="login-screen"><div className="panel login-loading">正在加载…</div></div></div>;
  }

  if (!auth) {
    return <LoginView onAuthed={handleAuthed} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">AE</div><div><strong>Agent Eval</strong><span>质量评测工作台</span></div></div>
        <div className="workspace-current" aria-label="当前项目"><span>我的空间</span><strong>{getProjectId()}</strong></div>
        <nav aria-label="主导航">{[...navItems, ...(auth.role === "admin" ? adminOnlyNavItems : [])].map(({ key, label, icon: Icon }) => <button className={activeView === key ? "nav-item active" : "nav-item"} key={key} aria-label={label} onClick={() => navigate(key)}><Icon size={18} /><span>{label}</span>{key === "runs" && <i className="nav-count">{activeView === "runs" ? "·" : ""}</i>}</button>)}</nav>
        <div className="sidebar-bottom"><ApiStatus /><div className="profile" aria-label="当前用户"><div className="avatar">{auth.displayName.slice(0, 2).toUpperCase()}</div><div><strong>{auth.displayName}</strong><span>{auth.email}</span></div><button className="icon-button" title="退出登录" aria-label="退出登录" onClick={logout}><LogOut size={17} /></button></div></div>
      </aside>
      <main className="main-content">
        <header className="topbar"><div className="breadcrumb"><span>质量评测工作台</span><b>/</b><strong>{activeLabel}</strong></div><div className="topbar-actions">{searchOpen && <label className="topbar-search"><Search size={15} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") submitSearch(); if (event.key === "Escape") { setSearchOpen(false); setQuery(""); } }} placeholder="搜索导航" aria-label="搜索导航" /></label>}<button className="icon-button" title="搜索导航" aria-label="搜索导航" onClick={() => setSearchOpen((open) => !open)}><Search size={18} /></button><button className="outline-button" onClick={() => navigate("traces")}><Activity size={16} />实时 Trace</button><button className="primary" onClick={() => navigate("runs")}><Play size={16} />新建评测</button></div>{notice && <div className="global-search-notice" role="status">{notice}</div>}</header>
        <div className="page-content">{activeView === "overview" ? <Overview onNavigate={(view) => navigate(view)} /> : activeView === "datasets" ? <DatasetsView /> : activeView === "runs" ? <RunsView initialExperimentId={selectedRunId} onSelectExperiment={(runId) => navigate("runs", { runId })} onOpenTrace={openTrace} /> : activeView === "evaluators" ? <EvaluatorsView /> : activeView === "annotations" ? <AnnotationsView onOpenTrace={openTrace} /> : activeView === "regression" ? <ReportsView initialMode="compare" /> : activeView === "gates" ? <ReportsView initialMode="gate" /> : activeView === "members" ? <MembersView auth={auth} /> : activeView === "connections" ? <ConnectionsView /> : <TracesView initialTraceId={selectedTraceId} onSelectTrace={(traceId) => navigate("traces", { traceId })} />}</div>
      </main>
    </div>
  );
}
