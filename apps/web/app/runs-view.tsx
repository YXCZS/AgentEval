"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Check,
  CircleDashed,
  Clipboard,
  Clock3,
  ExternalLink,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  Square,
  Target,
  XCircle,
} from "lucide-react";
import { API_URL, getProjectId, getSessionToken, fetchApi } from "./api-client";

type AgentType = "rag" | "tool" | "custom";
type RunStatus = "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
type ExecutionStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
type EvidenceStatus = "pending" | "complete" | "incomplete" | "not_required";
type AgentRelease = {
  id: string;
  version: number;
  label: string;
  agent_type: AgentType;
  release_identity: string;
  enabled: boolean;
};
type Dataset = { id: string; name: string; current_version_id: string | null };
type DatasetVersion = { id: string; version: number; cases: Array<{ id: string }> };
type Evaluator = {
  id: string;
  name: string;
  version: string;
  evaluator_type: string;
  supported_agent_types: AgentType[];
  enabled: boolean;
  requires: string[];
};
type ExecutionOptions = {
  repetitions: number;
  concurrency: number;
  timeout_seconds: number;
  max_retries: number;
  retry_backoff_seconds: number;
};
type ExecutionMode = "sdk_task" | "otel" | "remote_upload" | "remote_trigger";
type Run = {
  id: string;
  name: string;
  status: RunStatus;
  total_cases: number;
  completed_cases: number;
  failed_cases: number;
  agent_version_id: string;
  dataset_version_id: string;
  evaluator_version_ids: string[];
  execution_mode: ExecutionMode;
  evidence_policy: "trace_required" | "llm_required" | "tool_trajectory_required" | "rag_trajectory_required";
  baseline_run_id: string | null;
  execution_options: ExecutionOptions;
  configuration_snapshot: Record<string, unknown>;
  created_at: string;
};
type Attempt = {
  id: string;
  case_id: string;
  repetition: number;
  attempt: number;
  status: ExecutionStatus;
  output: unknown;
  usage: Record<string, unknown>;
  error_type: string | null;
  error_message: string | null;
  trace_id: string | null;
  evidence_status: EvidenceStatus;
  evidence_reasons: string[];
  started_at: string | null;
  finished_at: string | null;
};
type ManifestItem = {
  case: { id: string; input: unknown; expected_tools?: Array<Record<string, unknown>> };
  attempts: Attempt[];
};
type ManifestPage = {
  items: ManifestItem[];
  total: number;
  offset: number;
  limit: number;
  next_offset: number | null;
};
type ExperimentPage = {
  items: Run[];
  total: number;
  offset: number;
  limit: number;
  next_offset: number | null;
};
type DatasetOption = DatasetVersion & { datasetId: string; datasetName: string; current: boolean };
type Notice = { tone: "success" | "danger" | "neutral"; text: string };

function requestHeaders(): HeadersInit {
  return { "Content-Type": "application/json", "Authorization": `Bearer ${getSessionToken()}` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, {
    ...init,
    headers: { ...requestHeaders(), ...init?.headers },
    cache: "no-store",
  });
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
  }
  return body as T;
}

function displayStatus(status: RunStatus | ExecutionStatus): string {
  return {
    queued: "等待 SDK",
    running: "运行中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
  }[status];
}

function statusTone(status: RunStatus | ExecutionStatus | EvidenceStatus): "success" | "danger" | "warning" | "neutral" {
  if (status === "completed" || status === "complete" || status === "not_required") return "success";
  if (status === "failed") return "danger";
  if (status === "cancelled" || status === "partial" || status === "incomplete") return "warning";
  return "neutral";
}

function evidenceLabel(status: EvidenceStatus): string {
  return { pending: "等待证据", complete: "证据完整", incomplete: "证据不完整", not_required: "无需证据" }[status];
}

const executionModeOptions: Array<{ value: ExecutionMode; label: string; description: string }> = [
  { value: "sdk_task", label: "Python SDK", description: "Python 进程拉取用例并执行真实 Agent。" },
  { value: "otel", label: "OpenTelemetry", description: "已插桩的 Agent 上报关联实验属性的 Trace。" },
  { value: "remote_upload", label: "Remote Upload", description: "任意语言的运行器通过 REST 拉取用例并回传结果。" },
  { value: "remote_trigger", label: "Remote Trigger", description: "平台通知已配置的远程运行器，再由它异步回传结果。" },
];

function executionModeLabel(mode: ExecutionMode): string {
  return executionModeOptions.find((option) => option.value === mode)?.label ?? mode;
}

function executionModeWaitingText(mode: ExecutionMode): string {
  return mode === "sdk_task" ? "等待 SDK 领取此 Case。" : "等待外部运行器领取并回传此 Case。";
}

function formatJson(value: unknown): string {
  if (value === null || value === undefined) return "未记录";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function latestAttempts(item: ManifestItem): Attempt[] {
  const byRepetition = new Map<number, Attempt>();
  item.attempts.forEach((attempt) => {
    const current = byRepetition.get(attempt.repetition);
    if (!current || current.attempt < attempt.attempt) byRepetition.set(attempt.repetition, attempt);
  });
  return [...byRepetition.values()].sort((a, b) => a.repetition - b.repetition);
}

function integrationSnippet(run: Run, dataset: DatasetOption): string {
  if (run.execution_mode === "sdk_task") {
    return [
      "from agent_eval import Client, ExperimentRunner",
      "from my_agent import run_agent",
      "",
      "client = Client()",
      `dataset = client.get_dataset(${JSON.stringify(dataset.datasetId)}, version_id=${JSON.stringify(run.dataset_version_id)})`,
      `release = client.get_release(${JSON.stringify(run.agent_version_id)})`,
      "ExperimentRunner(client).run(",
      "    dataset=dataset,",
      "    task=run_agent,",
      "    release=release,",
      `    evaluator_version_ids=${JSON.stringify(run.evaluator_version_ids)},`,
      `    name=${JSON.stringify(run.name)},`,
      `    evidence_policy=${JSON.stringify(run.evidence_policy)},`,
      `    max_concurrency=${run.execution_options.concurrency},`,
      `    repetitions=${run.execution_options.repetitions},`,
      `    timeout_seconds=${run.execution_options.timeout_seconds},`,
      `    max_retries=${run.execution_options.max_retries},`,
      `    retry_backoff_seconds=${run.execution_options.retry_backoff_seconds},`,
      `    resume_experiment_id=${JSON.stringify(run.id)},`,
      ")",
    ].join("\n");
  }

  if (run.execution_mode === "otel") {
    return [
      "# Add these attributes to the root OpenTelemetry/OpenInference task Span.",
      `agent_eval.project.id=${getProjectId()}`,
      `agent_eval.experiment.id=${run.id}`,
      "agent_eval.experiment.item.id=<item_id_from_manifest>",
      `agent_eval.dataset.id=${dataset.datasetId}`,
      `agent_eval.dataset.version.id=${run.dataset_version_id}`,
      "agent_eval.case.id=<case_id_from_manifest>",
      `agent_eval.agent.release=${run.agent_version_id}`,
      "agent_eval.execution.origin=otel",
      "agent_eval.repetition=1",
      "",
      "# Export the trace through your existing OTLP pipeline, then submit each Item result and finalize the Experiment.",
    ].join("\n");
  }

  const baseUrl = API_URL.replace(/\/$/, "");
  const manifestUrl = `${baseUrl}/projects/${getProjectId()}/experiments/${run.id}/manifest`;
  if (run.execution_mode === "remote_upload") {
    return [
      "# Read AGENT_EVAL_PROJECT_KEY from your runtime Secret Manager, not source code.",
      `curl -sS -H 'X-Project-Key: '"$AGENT_EVAL_PROJECT_KEY" \"${manifestUrl}\"`,
      "",
      "# For every Case: start Item -> run your real Agent -> complete/fail Item -> upload OTLP Trace -> finalize.",
      "# The Project Key is intentionally a placeholder and is never embedded in this configuration.",
    ].join("\n");
  }

  return [
    "# The configured Dataset Trigger has received this Experiment notification.",
    "# Your receiver must verify X-Agent-Eval-Trigger-Timestamp, Delivery-Id and Signature before running.",
    "# Keep the trigger secret in your runtime Secret Manager; it is not retrievable from this page.",
    `curl -sS -H 'X-Project-Key: '"$AGENT_EVAL_PROJECT_KEY" \"${manifestUrl}\"`,
    "",
    "# After verification, use the Remote Upload callbacks for each Item, OTLP Trace, and finalization.",
  ].join("\n");
}

export function RunsView({
  initialExperimentId = "",
  onSelectExperiment,
  onOpenTrace,
}: {
  initialExperimentId?: string;
  onSelectExperiment?: (experimentId: string) => void;
  onOpenTrace?: (traceId: string) => void;
}) {
  const [releases, setReleases] = useState<AgentRelease[]>([]);
  const [datasets, setDatasets] = useState<DatasetOption[]>([]);
  const [evaluators, setEvaluators] = useState<Evaluator[]>([]);
  const [experiments, setExperiments] = useState<Run[]>([]);
  const [experimentPage, setExperimentPage] = useState<ExperimentPage | null>(null);
  const [experimentOffset, setExperimentOffset] = useState(0);
  const [experimentQuery, setExperimentQuery] = useState("");
  const [experimentStatus, setExperimentStatus] = useState<"all" | RunStatus>("all");
  const [experimentMode, setExperimentMode] = useState<"all" | ExecutionMode>("all");
  const [selectedId, setSelectedId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [manifest, setManifest] = useState<ManifestItem[]>([]);
  const [manifestPage, setManifestPage] = useState<ManifestPage | null>(null);
  const [manifestOffset, setManifestOffset] = useState(0);
  const [name, setName] = useState("");
  const [releaseId, setReleaseId] = useState("");
  const [datasetVersionId, setDatasetVersionId] = useState("");
  const [evaluatorIds, setEvaluatorIds] = useState<string[]>([]);
  const [baselineRunId, setBaselineRunId] = useState("");
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("sdk_task");
  const [evidencePolicy, setEvidencePolicy] = useState<Run["evidence_policy"]>("tool_trajectory_required");
  const [options, setOptions] = useState<ExecutionOptions>({ repetitions: 1, concurrency: 4, timeout_seconds: 30, max_retries: 2, retry_backoff_seconds: 0.2 });
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const selectedRelease = releases.find((item) => item.id === releaseId) ?? null;
  const selectedDataset = datasets.find((item) => item.id === datasetVersionId) ?? null;
  const compatibleEvaluators = useMemo(
    () => evaluators.filter((item) => item.enabled && (!selectedRelease || item.supported_agent_types.includes(selectedRelease.agent_type))),
    [evaluators, selectedRelease],
  );
  const compatibleBaselines = useMemo(
    () => experiments.filter((item) => item.id !== run?.id && item.dataset_version_id === datasetVersionId && ["completed", "partial", "failed"].includes(item.status)),
    [datasetVersionId, experiments, run?.id],
  );
  const terminalCases = run
    ? Math.min(run.total_cases, run.completed_cases + run.failed_cases)
    : 0;
  const progress = run && run.total_cases > 0 ? Math.round((terminalCases / run.total_cases) * 100) : 0;

  async function loadManifest(experimentId: string, offset = manifestOffset): Promise<ManifestPage> {
    return requestJson<ManifestPage>(`/projects/${getProjectId()}/experiments/${experimentId}/manifest?offset=${offset}&limit=25`);
  }

  async function loadExperiment(experimentId: string, offset = manifestOffset) {
    const [detail, items] = await Promise.all([
      requestJson<Run>(`/projects/${getProjectId()}/experiments/${experimentId}`),
      loadManifest(experimentId, offset),
    ]);
    setSelectedId(experimentId);
    setRun(detail);
    setManifest(items.items);
    setManifestPage(items);
  }

  async function openExperiment(experimentId: string) {
    setManifestOffset(0);
    await loadExperiment(experimentId, 0);
    onSelectExperiment?.(experimentId);
  }

  async function loadCatalog(preferredId?: string, requestedOffset = experimentOffset) {
    setCatalogBusy(true);
    setNotice(null);
    try {
      const params = new URLSearchParams({ limit: "25", offset: String(requestedOffset) });
      if (experimentQuery.trim()) params.set("query", experimentQuery.trim());
      if (experimentStatus !== "all") params.set("status", experimentStatus);
      if (experimentMode !== "all") params.set("execution_mode", experimentMode);
      const [releaseRows, datasetRows, evaluatorRows, experimentPageRows] = await Promise.all([
        requestJson<AgentRelease[]>(`/projects/${getProjectId()}/agent-releases`),
        requestJson<Dataset[]>(`/projects/${getProjectId()}/datasets`),
        requestJson<Evaluator[]>(`/projects/${getProjectId()}/evaluators`),
        requestJson<ExperimentPage>(`/projects/${getProjectId()}/experiments?${params.toString()}`),
      ]);
      const experimentRows = experimentPageRows.items;
      const endpointIndependent = releaseRows.filter((item) => item.enabled);
      const versionGroups = await Promise.all(datasetRows.map(async (dataset) => {
        const versions = await requestJson<DatasetVersion[]>(`/projects/${getProjectId()}/datasets/${dataset.id}/versions`);
        return versions.map((version) => ({ ...version, datasetId: dataset.id, datasetName: dataset.name, current: version.id === dataset.current_version_id }));
      }));
      const datasetOptions = versionGroups.flat();
      setReleases(endpointIndependent);
      setDatasets(datasetOptions);
      setEvaluators(evaluatorRows);
      setExperiments(experimentRows);
      setExperimentPage(experimentPageRows);
      setReleaseId((current) => endpointIndependent.some((item) => item.id === current) ? current : endpointIndependent[0]?.id ?? "");
      setDatasetVersionId((current) => datasetOptions.some((item) => item.id === current) ? current : datasetOptions.find((item) => item.current)?.id ?? datasetOptions[0]?.id ?? "");
      const nextId = preferredId || (
        experimentRows.find((item) => item.id === selectedId)?.id
        ?? experimentRows[0]?.id
      );
      if (nextId) await loadExperiment(nextId);
      else { setSelectedId(""); setRun(null); setManifest([]); setManifestPage(null); }
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "实验目录加载失败。" });
    } finally {
      setCatalogBusy(false);
    }
  }

  useEffect(() => { void loadCatalog(initialExperimentId || undefined); }, [initialExperimentId, experimentQuery, experimentStatus, experimentMode, experimentOffset]);
  useEffect(() => {
    if (selectedId) void loadExperiment(selectedId, manifestOffset).catch((error) => setNotice({ tone: "danger", text: error instanceof Error ? error.message : "Case 页面加载失败。" }));
  }, [manifestOffset]);
  useEffect(() => {
    setEvaluatorIds((current) => current.filter((id) => compatibleEvaluators.some((item) => item.id === id)));
  }, [compatibleEvaluators]);
  useEffect(() => {
    if (!compatibleBaselines.some((item) => item.id === baselineRunId)) setBaselineRunId("");
  }, [baselineRunId, compatibleBaselines]);
  useEffect(() => {
    if (!selectedId || !run || !["queued", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => {
      void loadExperiment(selectedId).catch((error) => setNotice({ tone: "danger", text: error instanceof Error ? error.message : "实验进度刷新失败。" }));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [run?.status, selectedId]);

  function toggleEvaluator(id: string) {
    setEvaluatorIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function updateOption(key: keyof ExecutionOptions, value: string) {
    setOptions((current) => ({ ...current, [key]: Number(value) }));
  }

  async function createExperiment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!releaseId || !datasetVersionId || evaluatorIds.length === 0) {
      setNotice({ tone: "danger", text: "请选择 Release、Dataset Version 和至少一个 Evaluator。" });
      return;
    }
    setSubmitBusy(true);
    setNotice(null);
    try {
      const created = await requestJson<Run>(`/projects/${getProjectId()}/experiments`, {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          agent_version_id: releaseId,
          dataset_version_id: datasetVersionId,
          evaluator_version_ids: evaluatorIds,
          execution_mode: executionMode,
          evidence_policy: evidencePolicy,
          baseline_run_id: baselineRunId || null,
          execution_options: options,
        }),
      });
      setExperiments((current) => [created, ...current]);
      await loadExperiment(created.id);
      onSelectExperiment?.(created.id);
      setNotice({ tone: "success", text: `Experiment 已创建并冻结配置，正在等待 ${executionModeLabel(created.execution_mode)} 运行。` });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "创建 Experiment 失败。" });
    } finally {
      setSubmitBusy(false);
    }
  }

  async function cancelExperiment() {
    if (!run) return;
    setSubmitBusy(true);
    try {
      const cancelled = await requestJson<Run>(`/projects/${getProjectId()}/experiments/${run.id}/cancel`, { method: "POST" });
      setRun(cancelled);
      setExperiments((current) => current.map((item) => item.id === cancelled.id ? cancelled : item));
      setNotice({ tone: "neutral", text: "Experiment 已取消，晚到结果不会改变终态。" });
    } catch (error) {
      setNotice({ tone: "danger", text: error instanceof Error ? error.message : "取消 Experiment 失败。" });
    } finally {
      setSubmitBusy(false);
    }
  }

  const resumeSnippet = run && selectedDataset ? integrationSnippet(run, selectedDataset) : "";

  async function copyResumeSnippet() {
    if (!resumeSnippet) return;
    try {
      await navigator.clipboard.writeText(resumeSnippet);
      setNotice({ tone: "success", text: `${run ? executionModeLabel(run.execution_mode) : "接入"}配置已复制。` });
    } catch {
      setNotice({ tone: "danger", text: "浏览器拒绝了剪贴板操作，请手动选择代码。" });
    }
  }

  return (
    <section className="runs-workbench">
      <div className="resource-heading runs-heading">
        <div><p className="eyebrow"><Activity size={14} /> 真实 Agent 评测</p><h1>实验</h1><p>平台冻结评测定义并展示证据；真实 Agent 始终在你自己的运行环境中执行。</p></div>
        <button type="button" className="outline-button" onClick={() => void loadCatalog(selectedId)} disabled={catalogBusy}><RefreshCw className={catalogBusy ? "spin" : ""} size={16} /> 刷新</button>
      </div>
      {notice && <div className={`run-notice ${notice.tone}`} role="status"><AlertTriangle size={16} />{notice.text}</div>}
      <div className="runs-layout">
        <form className="run-config panel" onSubmit={(event) => void createExperiment(event)}>
          <div className="run-panel-header"><div><span className="section-kicker">创建 Experiment</span><strong>冻结可复现的评测输入</strong></div><span>{selectedDataset ? `${selectedDataset.cases.length} 条 Case` : "未选择数据集"}</span></div>
          <div className="run-config-body">
            <label className="field-label">实验名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 order-agent candidate" required /></label>
            <label className="field-label">Agent Release<select value={releaseId} onChange={(event) => setReleaseId(event.target.value)} disabled={catalogBusy}><option value="">选择 Release</option>{releases.map((item) => <option value={item.id} key={item.id}>{item.label} · {item.release_identity}</option>)}</select><small>这里只选择版本身份，不配置 Agent URL 或模型 Key。</small></label>
            {selectedRelease && <div className="selection-summary"><Target size={16} /><div><strong>{selectedRelease.label}</strong><span>{selectedRelease.agent_type} · {selectedRelease.release_identity}</span></div></div>}
            <label className="field-label">Dataset Version<select value={datasetVersionId} onChange={(event) => setDatasetVersionId(event.target.value)} disabled={catalogBusy}><option value="">选择 Dataset Version</option>{datasets.map((item) => <option value={item.id} key={item.id}>{item.datasetName} / v{item.version}（{item.cases.length} 条）{item.current ? " · 当前" : ""}</option>)}</select></label>
            <fieldset className="integration-mode-grid"><legend>接入方式</legend>{executionModeOptions.map((option) => <label className={`integration-mode-option ${executionMode === option.value ? "selected" : ""}`} key={option.value}><input type="radio" name="execution-mode" value={option.value} checked={executionMode === option.value} onChange={() => setExecutionMode(option.value)} /><span><strong>{option.label}</strong><small>{option.description}</small></span></label>)}</fieldset>
            <label className="field-label">证据策略<select value={evidencePolicy} onChange={(event) => setEvidencePolicy(event.target.value as Run["evidence_policy"])}><option value="trace_required">需要 Trace</option><option value="llm_required">需要 LLM Span 与 usage</option><option value="tool_trajectory_required">需要完整 Tool 轨迹</option><option value="rag_trajectory_required">需要完整 RAG 轨迹</option></select></label>
            <label className="field-label">Baseline Experiment（可选）<select value={baselineRunId} onChange={(event) => setBaselineRunId(event.target.value)} disabled={!compatibleBaselines.length}><option value="">不设置 Baseline</option>{compatibleBaselines.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.id.slice(0, 8)}</option>)}</select><small>只允许选择相同 Dataset Version 的历史实验。</small></label>
            <div className="evaluator-picker"><div className="picker-heading"><div><span className="section-kicker">Evaluator Set</span><strong>选择客观评分器</strong></div><span>已选 {evaluatorIds.length} 个</span></div>{compatibleEvaluators.length ? compatibleEvaluators.map((item) => <label className="evaluator-option" key={item.id}><input type="checkbox" checked={evaluatorIds.includes(item.id)} onChange={() => toggleEvaluator(item.id)} /><span className="checkbox-mark"><Check size={12} /></span><span className="evaluator-copy"><strong>{item.name}</strong><small>{item.evaluator_type} / v{item.version}{item.requires.length ? ` · 需要 ${item.requires.join(", ")}` : ""}</small></span></label>) : <div className="picker-empty">没有与当前 Agent 类型兼容的 Evaluator。</div>}</div>
            <div className="config-section"><div className="config-section-title"><Clock3 size={15} /><strong>运行参数</strong><span>参数会写入不可变快照</span></div><div className="field-grid four"><label className="field-label">重复次数<input type="number" min="1" max="100" value={options.repetitions} onChange={(event) => updateOption("repetitions", event.target.value)} /></label><label className="field-label">并发数<input type="number" min="1" max="100" value={options.concurrency} onChange={(event) => updateOption("concurrency", event.target.value)} /></label><label className="field-label">超时（秒）<input type="number" min="1" max="300" value={options.timeout_seconds} onChange={(event) => updateOption("timeout_seconds", event.target.value)} /></label><label className="field-label">最大重试<input type="number" min="0" max="5" value={options.max_retries} onChange={(event) => updateOption("max_retries", event.target.value)} /></label></div></div>
            <div className="run-config-actions"><span>{executionModeOptions.find((option) => option.value === executionMode)?.description}</span><button type="submit" className="primary" disabled={catalogBusy || submitBusy}>{submitBusy ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />} 创建 Experiment</button></div>
          </div>
        </form>

        <section className="run-progress panel">
          <div className="run-panel-header"><div><span className="section-kicker">Experiment 目录</span><strong>{experimentPage?.total ?? 0} 次实验</strong></div></div>
          <div className="toolbar"><div className="search-field"><Search size={16} /><input value={experimentQuery} onChange={(event) => { setExperimentOffset(0); setExperimentQuery(event.target.value); }} aria-label="搜索 Experiment" placeholder="搜索名称、ID、Release 或数据集版本" /></div><select className="filter-button" value={experimentStatus} aria-label="Experiment 状态筛选" onChange={(event) => { setExperimentOffset(0); setExperimentStatus(event.target.value as "all" | RunStatus); }}><option value="all">全部状态</option><option value="queued">等待</option><option value="running">运行中</option><option value="completed">已完成</option><option value="partial">部分完成</option><option value="failed">失败</option><option value="cancelled">已取消</option></select><select className="filter-button" value={experimentMode} aria-label="Experiment 接入方式筛选" onChange={(event) => { setExperimentOffset(0); setExperimentMode(event.target.value as "all" | ExecutionMode); }}><option value="all">全部接入方式</option>{executionModeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div>
          <div className="connection-list">{experiments.map((item) => <button type="button" key={item.id} className={`connection-list-item ${item.id === selectedId ? "selected" : ""}`} onClick={() => void openExperiment(item.id)}><span className="connection-type"><Activity size={15} /></span><span className="connection-list-copy"><strong>{item.name}</strong><small>{item.id.slice(0, 8)} · {item.completed_cases + item.failed_cases}/{item.total_cases}</small></span><i className={statusTone(item.status)}>{displayStatus(item.status)}</i></button>)}</div>
          <div className="editor-actions"><button className="outline-button compact" type="button" disabled={catalogBusy || experimentOffset === 0} onClick={() => setExperimentOffset((value) => Math.max(0, value - (experimentPage?.limit ?? 25)))}>上一页</button><span className="detail-muted">{experimentPage ? `${experimentPage.offset + 1}-${experimentPage.offset + experiments.length} / ${experimentPage.total}` : ""}</span><button className="outline-button compact" type="button" disabled={catalogBusy || experimentPage === null || experimentPage.next_offset === null} onClick={() => { if (experimentPage?.next_offset !== null && experimentPage?.next_offset !== undefined) setExperimentOffset(experimentPage.next_offset); }}>下一页</button></div>
          {!experiments.length && !catalogBusy && <div className="run-empty"><Activity size={22} /><p>还没有 Experiment。先在左侧冻结一次评测定义。</p></div>}
        </section>
      </div>

      {run && <section className="panel connection-detail">
        <div className="panel-heading"><div><p className="eyebrow">Experiment 详情</p><h2>{run.name}</h2><span className="connection-description">{run.id}</span></div><span className={`status status-${statusTone(run.status)}`}><CircleDashed size={14} /> {displayStatus(run.status)}</span></div>
        <div className="trace-detail-body">
          <div className="progress-number"><strong>{progress}%</strong><span>{terminalCases} / {run.total_cases} 个 Item 已终态</span></div><div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <div className="trace-meta-grid"><div><span>执行模式</span><strong>{executionModeLabel(run.execution_mode)}</strong></div><div><span>证据策略</span><strong>{run.evidence_policy}</strong></div><div><span>Dataset Version</span><strong>{run.dataset_version_id}</strong></div><div><span>Release</span><strong>{run.agent_version_id}</strong></div></div>
          {["queued", "running"].includes(run.status) && <div className="detail-block"><h3>在 Agent 运行环境中接入</h3><pre className="integration-snippet">{resumeSnippet}</pre><div className="editor-actions"><button type="button" className="outline-button" onClick={() => void copyResumeSnippet()}><Clipboard size={15} /> 复制接入配置</button><button type="button" className="outline-button" onClick={() => void cancelExperiment()} disabled={submitBusy}><Square size={14} /> 取消 Experiment</button></div></div>}
          <div className="trace-section"><div className="trace-section-heading"><h3>Case / Repetition / Attempt</h3><span>{manifestPage ? `${manifestPage.total} 个 Case` : "加载中"}</span></div>{manifest.map((item) => <article className="detail-block" key={item.case.id}><h3>{item.case.id}</h3><pre>{formatJson(item.case.input)}</pre>{latestAttempts(item).length === 0 ? <p className="detail-muted">{run.status === "cancelled" ? "Experiment 已取消，未执行此 Case。" : executionModeWaitingText(run.execution_mode)}</p> : latestAttempts(item).map((attempt) => <div className="release-row" key={attempt.id}><div className="release-version"><strong>R{attempt.repetition}</strong><span>Attempt {attempt.attempt}</span></div><div className="release-identity"><strong>{displayStatus(attempt.status)}</strong><small>{attempt.error_message ?? formatJson(attempt.output)}</small></div><div className="release-endpoint"><span className={`status status-${statusTone(attempt.evidence_status)}`}>{evidenceLabel(attempt.evidence_status)}</span><small>{attempt.evidence_reasons.length ? attempt.evidence_reasons.join("；") : `usage: ${formatJson(attempt.usage)}`}</small></div>{attempt.trace_id && <button type="button" className="outline-button compact" onClick={() => onOpenTrace?.(attempt.trace_id!)}><ExternalLink size={14} /> Trace</button>}</div>)}</article>)}<div className="editor-actions"><button className="outline-button compact" type="button" disabled={manifestOffset === 0} onClick={() => setManifestOffset((value) => Math.max(0, value - (manifestPage?.limit ?? 25)))}>上一页</button><span className="detail-muted">{manifestPage ? `${manifestPage.offset + 1}-${manifestPage.offset + manifest.length} / ${manifestPage.total}` : ""}</span><button className="outline-button compact" type="button" disabled={manifestPage === null || manifestPage.next_offset === null} onClick={() => { if (manifestPage?.next_offset !== null && manifestPage?.next_offset !== undefined) setManifestOffset(manifestPage.next_offset); }}>下一页</button></div></div>
        </div>
      </section>}
    </section>
  );
}

function StatusIcon({ status }: { status: ExecutionStatus }) {
  if (status === "completed") return <Check size={14} />;
  if (status === "failed") return <XCircle size={14} />;
  return <CircleDashed size={14} />;
}
