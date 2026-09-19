"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Check,
  CircleAlert,
  Clock3,
  Database,
  Link2,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Workflow,
  XCircle,
} from "lucide-react";
import { API_URL, PROJECT_ID, SESSION, fetchApi } from "./api-client";

type ExecutionStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
type TraceSummary = {
  trace_id: string;
  run_id: string | null;
  case_id: string | null;
  status: ExecutionStatus;
  source: string;
  span_count: number;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
};
type TraceSummaryPage = {
  items: TraceSummary[];
  total: number;
  offset: number;
  limit: number;
  next_offset: number | null;
};
type TraceSpan = {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  kind: string;
  name: string;
  status: ExecutionStatus;
  started_at: string;
  ended_at: string | null;
  input: unknown;
  output: unknown;
  error: Record<string, unknown> | null;
  usage: Record<string, unknown>;
  cost: unknown;
  attributes: Record<string, unknown>;
  extensions: Record<string, unknown>;
};
type Score = {
  id: string;
  experiment_item_id: string | null;
  repetition: number;
  attempt: number | null;
  metric_name: string;
  evaluator_version_id: string;
  trace_id: string | null;
  span_id: string | null;
  source: "automated" | "deterministic" | "llm_judge" | "human" | "adapter";
  status: "passed" | "failed" | "missing" | "error" | "not_run";
  value: number | null;
  label: string | null;
  passed: boolean | null;
  explanation: string | null;
  evidence: Array<Record<string, unknown>>;
  rubric?: string | null;
  judge_model?: string | null;
  provenance?: Record<string, unknown> | null;
};
type Trace = {
  trace_id: string;
  run_id: string | null;
  case_id: string | null;
  status: ExecutionStatus;
  spans: TraceSpan[];
  scores: Score[];
  source: string;
  extensions: Record<string, unknown>;
};
type Timeline = {
  trace_id: string;
  started_at: string | null;
  ended_at: string | null;
  spans: Array<{
    span_id: string;
    parent_span_id: string | null;
    kind: string;
    name: string;
    status: ExecutionStatus;
    started_at: string;
    ended_at: string | null;
    duration_ms: number | null;
    depth: number;
  }>;
};
type Dataset = { id: string; name: string; current_version_id: string | null };
type DatasetCaseSummary = { id: string; source_trace_id?: string; source_span_ids?: string[] };
type DatasetVersion = { id: string; dataset_id: string; version: number; cases: DatasetCaseSummary[] };
type TraceField = "input" | "output" | "attributes" | "extensions";
type FieldSelection = { span_id: string; field: TraceField; attribute_key?: string };

const statusLabels: Record<ExecutionStatus, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};
const sourceLabels: Record<Score["source"], string> = {
  automated: "自动评分",
  deterministic: "确定性规则",
  llm_judge: "LLM Judge",
  human: "人工标注",
  adapter: "评测适配器",
};
const scoreStatusLabels: Record<Score["status"], string> = {
  passed: "通过",
  failed: "失败",
  missing: "缺少证据",
  error: "评估错误",
  not_run: "未运行",
};

function requestHeaders(): HeadersInit {
  return { "Content-Type": "application/json", "Authorization": `Bearer ${SESSION}` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, {
    ...init,
    headers: { ...requestHeaders(), ...init?.headers },
  });
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    const message = Array.isArray(detail)
      ? detail.map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item))).join("；")
      : typeof detail === "string"
        ? detail
        : `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return body as T;
}

function formatDate(value: string | null): string {
  return value
    ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "未记录";
}

function formatJson(value: unknown): string {
  if (value === null || value === undefined) return "未记录";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function tone(status: string): "success" | "danger" | "warning" | "neutral" {
  return status === "completed" || status === "passed" ? "success" : status === "failed" || status === "error" ? "danger" : status === "cancelled" || status === "missing" || status === "not_run" ? "warning" : "neutral";
}

function StatusMark({ status }: { status: ExecutionStatus }) {
  const currentTone = tone(status);
  const Icon = currentTone === "success" ? Check : currentTone === "danger" ? XCircle : currentTone === "warning" ? AlertTriangle : Clock3;
  return <span className={`status status-${currentTone}`}><Icon size={14} />{statusLabels[status]}</span>;
}

function containsPrivacyMarker(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  if (Array.isArray(value)) return value.some(containsPrivacyMarker);
  const record = value as Record<string, unknown>;
  return record.__agent_eval_redacted === true || "__agent_eval_content_ref" in record || Object.values(record).some(containsPrivacyMarker);
}

function privacySummary(trace: Trace): { redacted: number; truncated: number } {
  const value = trace.extensions["agent_eval.privacy"];
  if (!value || typeof value !== "object") return { redacted: 0, truncated: 0 };
  const record = value as Record<string, unknown>;
  return {
    redacted: typeof record.redacted_fields === "number" ? record.redacted_fields : 0,
    truncated: typeof record.truncated_fields === "number" ? record.truncated_fields : 0,
  };
}

function traceAttribute(trace: Trace, key: string): unknown {
  const root = trace.spans.find((span) => span.parent_span_id === null) ?? trace.spans[0];
  return root?.attributes[key];
}

function traceModel(trace: Trace): string {
  const llm = trace.spans.find((span) => span.kind === "llm");
  const value = llm?.attributes["llm.model_name"]
    ?? llm?.attributes["gen_ai.request.model"]
    ?? llm?.attributes["gen_ai.response.model"];
  return typeof value === "string" ? value : "未记录";
}

function traceUsage(trace: Trace): Record<string, unknown> {
  return trace.spans.reduce<Record<string, unknown>>((result, span) => {
    Object.entries(span.usage ?? {}).forEach(([key, value]) => {
      if (typeof value === "number") result[key] = Number(result[key] ?? 0) + value;
      else if (!(key in result)) result[key] = value;
    });
    return result;
  }, {});
}

function defaultSpanId(spans: TraceSpan[], field: "input" | "output"): string {
  return spans.find((span) => span.kind === "agent" && span[field] !== null && span[field] !== undefined)?.span_id
    ?? spans.find((span) => span[field] !== null && span[field] !== undefined)?.span_id
    ?? spans[0]?.span_id
    ?? "";
}

function FieldPicker({
  label,
  spans,
  value,
  onChange,
  required = false,
}: {
  label: string;
  spans: TraceSpan[];
  value: FieldSelection | null;
  onChange: (value: FieldSelection | null) => void;
  required?: boolean;
}) {
  const selectedSpan = spans.find((span) => span.span_id === value?.span_id);
  const selectedValue = selectedSpan && value ? selectedSpan[value.field] : null;
  const needsKey = value?.field === "attributes" || value?.field === "extensions";
  const keys = selectedValue && typeof selectedValue === "object" && !Array.isArray(selectedValue) ? Object.keys(selectedValue) : [];
  function updateField(field: TraceField) {
    if (!value) return;
    onChange({ span_id: value.span_id, field, attribute_key: field === "attributes" || field === "extensions" ? value.attribute_key : undefined });
  }
  function updateSpan(spanId: string) {
    if (!spanId) { onChange(null); return; }
    onChange({ span_id: spanId, field: value?.field ?? "input", attribute_key: value?.field === "attributes" || value?.field === "extensions" ? value.attribute_key : undefined });
  }
  return <div className="trace-field-picker">
    <span className="field-label-text">{label}{required && <b>*</b>}</span>
    <div className="field-picker-row">
      <select value={value?.span_id ?? ""} onChange={(event) => updateSpan(event.target.value)}>
        <option value="">选择 Observation</option>
        {spans.map((span) => <option key={span.span_id} value={span.span_id}>{span.name} / {span.kind} / {span.span_id}</option>)}
      </select>
      <select value={value?.field ?? "input"} disabled={!value} onChange={(event) => updateField(event.target.value as TraceField)}>
        <option value="input">输入 input</option>
        <option value="output">输出 output</option>
        <option value="attributes">Attributes</option>
        <option value="extensions">Extensions</option>
      </select>
      {needsKey && <select value={value?.attribute_key ?? ""} disabled={!value || keys.length === 0} onChange={(event) => onChange(value ? { ...value, attribute_key: event.target.value || undefined } : null)}>
        <option value="">整个对象</option>
        {keys.map((key) => <option key={key} value={key}>{key}</option>)}
      </select>}
    </div>
  </div>;
}

function ScoreRecord({ score, spans }: { score: Score; spans: TraceSpan[] }) {
  const linkedSpan = spans.find((span) => span.span_id === score.span_id);
  const scoreValue = score.value !== null && score.value !== undefined ? String(score.value) : score.label ?? scoreStatusLabels[score.status];
  return <article className={`score-record ${score.source === "human" ? "human-score" : ""}`}>
    <div><strong>{score.metric_name}</strong><span className={`score-chip ${tone(score.status)}`}>{scoreStatusLabels[score.status]}</span></div>
    <b>{scoreValue}</b>
    <small>{sourceLabels[score.source]} · v{score.evaluator_version_id}{linkedSpan ? ` · Observation: ${linkedSpan.name}` : " · Trace 级评分"}</small>
    {score.experiment_item_id && <small>Item {score.experiment_item_id} · 重复 {score.repetition}{score.attempt ? ` · 尝试 ${score.attempt}` : ""}</small>}
    {score.explanation && <p>{score.explanation}</p>}
    {score.judge_model && <small>Judge 模型：{score.judge_model}</small>}
    {score.provenance && <details className="score-provenance"><summary>查看评测来源证据</summary><pre>{formatJson(score.provenance)}</pre></details>}
    {score.evidence.length > 0 && <pre>{formatJson(score.evidence)}</pre>}
  </article>;
}

export function TracesView({
  initialTraceId = "",
  onSelectTrace,
}: {
  initialTraceId?: string;
  onSelectTrace?: (traceId: string) => void;
}) {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [tracePage, setTracePage] = useState<TraceSummaryPage | null>(null);
  const [pageOffset, setPageOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<Trace | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | ExecutionStatus>("all");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedSpanId, setSelectedSpanId] = useState("");
  const [mappingOpen, setMappingOpen] = useState(false);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetVersions, setDatasetVersions] = useState<DatasetVersion[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [datasetId, setDatasetId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [caseId, setCaseId] = useState("");
  const [inputSelection, setInputSelection] = useState<FieldSelection | null>(null);
  const [outputSelection, setOutputSelection] = useState<FieldSelection | null>(null);
  const [stateSelection, setStateSelection] = useState<FieldSelection | null>(null);
  const [includeOutput, setIncludeOutput] = useState(true);
  const [includeState, setIncludeState] = useState(false);
  const [includeTools, setIncludeTools] = useState(true);
  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([]);
  const [creatingCase, setCreatingCase] = useState(false);
  const [createdCase, setCreatedCase] = useState<{ id: string; version: number; sourceTraceId?: string; sourceSpanIds: string[] } | null>(null);
  const selectedSpan = detail?.spans.find((span) => span.span_id === selectedSpanId) ?? null;
  const toolSpans = detail?.spans.filter((span) => span.kind === "tool") ?? [];
  const privacy = detail ? privacySummary(detail) : { redacted: 0, truncated: 0 };

  async function loadTrace(traceId: string) {
    setSelectedId(traceId);
    setDetailLoading(true);
    setNotice(null);
    setMappingOpen(false);
    setCreatedCase(null);
    try {
      const [trace, traceTimeline] = await Promise.all([
        requestJson<Trace>(`/projects/${PROJECT_ID}/traces/${encodeURIComponent(traceId)}`),
        requestJson<Timeline>(`/projects/${PROJECT_ID}/traces/${encodeURIComponent(traceId)}/timeline`),
      ]);
      setDetail(trace);
      setTimeline(traceTimeline);
      const nextSpanId = trace.spans[0]?.span_id ?? "";
      setSelectedSpanId(nextSpanId);
      setInputSelection({ span_id: defaultSpanId(trace.spans, "input"), field: "input" });
      const outputSpanId = defaultSpanId(trace.spans, "output");
      const outputSpan = trace.spans.find((span) => span.span_id === outputSpanId);
      setOutputSelection(outputSpan?.output !== null && outputSpan?.output !== undefined ? { span_id: outputSpanId, field: "output" } : null);
      setStateSelection(null);
      setIncludeOutput(Boolean(outputSpan?.output !== null && outputSpan?.output !== undefined));
      const nextToolIds = trace.spans.filter((span) => span.kind === "tool").map((span) => span.span_id);
      setSelectedToolIds(nextToolIds);
      setIncludeTools(nextToolIds.length > 0);
      setCaseId(`trace-${trace.trace_id}-${nextSpanId}`.slice(0, 128));
    } catch (error) {
      setDetail(null);
      setTimeline(null);
      setNotice(`加载 Trace 详情失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setDetailLoading(false);
    }
  }

  async function openTrace(traceId: string) {
    await loadTrace(traceId);
    onSelectTrace?.(traceId);
  }

  async function loadTraces(preferredId?: string, requestedOffset = pageOffset) {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "25", offset: String(requestedOffset) });
      if (query.trim()) params.set("query", query.trim());
      if (statusFilter !== "all") params.set("status", statusFilter);
      const page = await requestJson<TraceSummaryPage>(`/projects/${PROJECT_ID}/traces?${params.toString()}`);
      setTraces(page.items);
      setTracePage(page);
      const nextId = preferredId && page.items.some((item) => item.trace_id === preferredId)
        ? preferredId
        : page.items[0]?.trace_id;
      if (nextId) await loadTrace(nextId);
      else { setSelectedId(""); setDetail(null); setTimeline(null); }
      setNotice(null);
    } catch (error) {
      setNotice(`加载 Trace 失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadDatasetVersions(nextDatasetId: string) {
    setDatasetId(nextDatasetId);
    setVersionId("");
    if (!nextDatasetId) { setDatasetVersions([]); return; }
    try {
      const versions = await requestJson<DatasetVersion[]>(`/projects/${PROJECT_ID}/datasets/${nextDatasetId}/versions`);
      setDatasetVersions(versions);
      const current = datasets.find((dataset) => dataset.id === nextDatasetId)?.current_version_id;
      setVersionId(current && versions.some((version) => version.id === current) ? current : versions[versions.length - 1]?.id ?? "");
    } catch (error) {
      setNotice(`加载数据集版本失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function openMapping() {
    setMappingOpen(true);
    if (datasets.length > 0) return;
    setDatasetsLoading(true);
    try {
      const loaded = await requestJson<Dataset[]>(`/projects/${PROJECT_ID}/datasets`);
      setDatasets(loaded);
      const first = loaded[0];
      if (first) await loadDatasetVersions(first.id);
    } catch (error) {
      setNotice(`加载数据集失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setDatasetsLoading(false);
    }
  }

  async function createCaseFromTrace() {
    if (!detail || !inputSelection || !datasetId || !versionId || !caseId.trim()) {
      setNotice("请先选择数据集版本、填写 Case ID，并设置输入字段。");
      return;
    }
    setCreatingCase(true);
    setNotice(null);
    try {
      const result = await requestJson<DatasetVersion>(`/projects/${PROJECT_ID}/datasets/${datasetId}/versions/${versionId}/cases/from-trace`, {
        method: "POST",
        body: JSON.stringify({
          id: caseId.trim(),
          trace_id: detail.trace_id,
          input: inputSelection,
          expected_output: includeOutput ? outputSelection : null,
          expected_state: includeState ? stateSelection : null,
          tool_span_ids: includeTools ? selectedToolIds : [],
          metadata: { created_from: "trace_observation", source_observation: selectedSpanId },
        }),
      });
      const created = result.cases.find((item) => item.id === caseId.trim());
      setCreatedCase({ id: caseId.trim(), version: result.version, sourceTraceId: created?.source_trace_id, sourceSpanIds: created?.source_span_ids ?? [] });
      setNotice(`Dataset Case 已创建到版本 ${result.version}。`);
    } catch (error) {
      setNotice(`创建 Dataset Case 失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setCreatingCase(false);
    }
  }

  useEffect(() => { void loadTraces(initialTraceId || undefined); }, [initialTraceId, query, statusFilter, pageOffset]);

  return <section className="resource-view traces-workbench">
    <div className="resource-heading">
      <div><p className="eyebrow"><Workflow size={14} /> 执行证据</p><h1>Trace 追踪</h1><p>查看 Python SDK 运行真实 Agent 后写入的 Trace、父子 Span、模型 usage 和质量评分。</p></div>
      <button className="outline-button" onClick={() => void loadTraces(selectedId)} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新 Trace</button>
    </div>
    {notice && <div className="inline-notice" role="status"><CircleAlert size={16} /> {notice}</div>}
    <div className="toolbar"><div className="search-field"><Search size={16} /><input value={query} onChange={(event) => { setPageOffset(0); setQuery(event.target.value); }} aria-label="搜索 Trace" placeholder="搜索 Trace、Run 或 Case..." /></div><select className="filter-button" aria-label="Trace 状态筛选" value={statusFilter} onChange={(event) => { setPageOffset(0); setStatusFilter(event.target.value as "all" | ExecutionStatus); }}><option value="all">全部状态</option><option value="completed">已完成</option><option value="failed">失败</option><option value="running">运行中</option><option value="queued">排队中</option><option value="cancelled">已取消</option></select></div>
    <div className="traces-layout">
      <section className="panel trace-list-panel"><div className="panel-heading"><div><p className="eyebrow">最近执行</p><h2>{tracePage?.total ?? 0} 条 Trace</h2></div><Activity size={16} className="muted-icon" /></div>{loading ? <p className="panel-placeholder">正在加载 Trace...</p> : traces.length === 0 ? <div className="panel-placeholder"><Workflow size={24} /><p>{tracePage?.total ? "没有符合筛选条件的 Trace。" : "还没有 Trace。请先在实验页创建 Experiment，并在 Agent 进程中运行 SDK。"}</p></div> : <div className="trace-list">{traces.map((trace) => <button key={trace.trace_id} className={`trace-list-item ${trace.trace_id === selectedId ? "selected" : ""}`} onClick={() => void openTrace(trace.trace_id)}><span className={`trace-dot ${tone(trace.status)}`} /><span className="trace-list-copy"><strong>{trace.trace_id}</strong><small>{trace.run_id ?? "无 Experiment"} · {trace.case_id ?? "无 Case"}</small></span><span className="trace-list-meta"><b>{trace.span_count} spans</b><small>{formatDate(trace.created_at)}</small></span><StatusMark status={trace.status} /></button>)}</div>}<div className="editor-actions"><button className="outline-button compact" type="button" disabled={loading || pageOffset === 0} onClick={() => setPageOffset((value) => Math.max(0, value - (tracePage?.limit ?? 25)))}>上一页</button><span className="detail-muted">{tracePage ? `${tracePage.offset + 1}-${tracePage.offset + traces.length} / ${tracePage.total}` : ""}</span><button className="outline-button compact" type="button" disabled={loading || tracePage?.next_offset === null || tracePage === null} onClick={() => { if (tracePage?.next_offset !== null && tracePage?.next_offset !== undefined) setPageOffset(tracePage.next_offset); }}>下一页</button></div></section>
      <section className="panel trace-detail-panel">{detailLoading ? <div className="panel-placeholder"><RefreshCw size={22} className="spin" /><p>正在加载 Trace 详情...</p></div> : detail && timeline ? <>
        <div className="panel-heading"><div><p className="eyebrow">Trace 详情</p><h2>{detail.trace_id}</h2></div><StatusMark status={detail.status} /></div>
        <div className="trace-detail-body">
          {(privacy.redacted > 0 || privacy.truncated > 0) && <div className="privacy-notice"><ShieldAlert size={17} /><div><strong>此 Trace 包含隐私保护标记</strong><span>{privacy.redacted > 0 ? `${privacy.redacted} 个字段已脱敏` : ""}{privacy.redacted > 0 && privacy.truncated > 0 ? "；" : ""}{privacy.truncated > 0 ? `${privacy.truncated} 个字段已截断为内容引用` : ""}。页面不会展示原始敏感值。</span></div></div>}
          <div className="trace-meta-grid"><div><span>执行来源</span><strong>{String(traceAttribute(detail, "agent_eval.execution.origin") ?? detail.source)}</strong></div><div><span>Release</span><strong>{String(traceAttribute(detail, "agent_eval.agent.release") ?? "未记录")}</strong></div><div><span>模型</span><strong>{traceModel(detail)}</strong></div><div><span>Usage</span><strong>{formatJson(traceUsage(detail))}</strong></div><div><span>Experiment</span><strong>{detail.run_id ?? "无"}</strong></div><div><span>Case</span><strong>{detail.case_id ?? "无"}</strong></div><div><span>Span 数量</span><strong>{detail.spans.length}</strong></div></div>
          <div className="trace-section"><div className="trace-section-heading"><h3>执行时间线</h3><span>{formatDate(timeline.started_at)} - {formatDate(timeline.ended_at)}</span></div><div className="timeline-list">{timeline.spans.map((span) => <button key={span.span_id} className={`timeline-row timeline-button ${span.span_id === selectedSpanId ? "selected" : ""}`} style={{ paddingLeft: `${12 + span.depth * 18}px` }} onClick={() => { setSelectedSpanId(span.span_id); setMappingOpen(false); }}><i className={tone(span.status)} /><div><strong>{span.name}</strong><span>{span.kind} · {span.duration_ms === null ? "未结束" : `${Math.round(span.duration_ms)} ms`} · {span.span_id}</span></div><StatusMark status={span.status} /></button>)}</div></div>
          {selectedSpan && <div className="trace-section span-detail"><div className="trace-section-heading"><h3>Observation 数据</h3><span>{selectedSpan.kind} · {selectedSpan.span_id}</span></div><div className="span-json-grid"><div className="detail-block"><h3>输入 {containsPrivacyMarker(selectedSpan.input) && <em className="redaction-badge">已保护</em>}</h3><pre>{formatJson(selectedSpan.input)}</pre></div><div className="detail-block"><h3>输出 {containsPrivacyMarker(selectedSpan.output) && <em className="redaction-badge">已保护</em>}</h3><pre>{formatJson(selectedSpan.output)}</pre></div>{selectedSpan.error && <div className="detail-block"><h3>错误</h3><pre>{formatJson(selectedSpan.error)}</pre></div>}<div className="detail-block"><h3>Attributes</h3><pre>{formatJson(selectedSpan.attributes)}</pre></div><div className="detail-block"><h3>Extensions</h3><pre>{formatJson(selectedSpan.extensions)}</pre></div></div><button className="primary add-case-button" onClick={() => void openMapping()}><Plus size={16} /> 将此 Observation 加入 Dataset</button></div>}
          {mappingOpen && <div className="trace-section mapping-panel"><div className="trace-section-heading"><h3>从 Observation 创建 Dataset Case</h3><span>平台会创建新的 Dataset Version，不修改历史版本</span></div>{datasetsLoading ? <div className="timeline-loading"><LoaderCircle size={15} className="spin" /> 正在加载数据集...</div> : datasets.length === 0 ? <div className="mapping-empty"><Database size={18} /><p>还没有 Dataset。请先在“数据集”页面创建一个空数据集，再回到这里映射 Trace。</p></div> : <div className="mapping-form"><div className="field-grid two"><label className="field-label">目标数据集<select value={datasetId} onChange={(event) => void loadDatasetVersions(event.target.value)}><option value="">选择数据集</option>{datasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}</select></label><label className="field-label">目标版本<select value={versionId} onChange={(event) => setVersionId(event.target.value)} disabled={!datasetId}><option value="">选择版本</option>{datasetVersions.map((version) => <option key={version.id} value={version.id}>v{version.version}（{version.cases.length} 个 Case）</option>)}</select></label></div><label className="field-label">Case ID<input value={caseId} onChange={(event) => setCaseId(event.target.value)} placeholder="例如：failed-order-001" /></label><FieldPicker label="评测输入" spans={detail.spans} value={inputSelection} onChange={setInputSelection} required /><label className="check-line"><input type="checkbox" checked={includeOutput} onChange={(event) => setIncludeOutput(event.target.checked)} /><span>保存预期输出</span></label>{includeOutput && <FieldPicker label="预期输出" spans={detail.spans} value={outputSelection} onChange={setOutputSelection} />}<label className="check-line"><input type="checkbox" checked={includeState} onChange={(event) => setIncludeState(event.target.checked)} /><span>保存预期状态</span></label>{includeState && <FieldPicker label="预期状态" spans={detail.spans} value={stateSelection} onChange={setStateSelection} />}<div className="tool-selection"><label className="check-line"><input type="checkbox" checked={includeTools} onChange={(event) => setIncludeTools(event.target.checked)} /><span>保存 Tool 调用轨迹</span></label>{includeTools && toolSpans.length > 0 && <div className="tool-options">{toolSpans.map((span) => <label className="check-line" key={span.span_id}><input type="checkbox" checked={selectedToolIds.includes(span.span_id)} onChange={() => setSelectedToolIds((current) => current.includes(span.span_id) ? current.filter((id) => id !== span.span_id) : [...current, span.span_id])} /><span>{span.name} / {span.span_id}</span></label>)}</div>}</div><div className="mapping-actions"><button className="outline-button" onClick={() => setMappingOpen(false)}>取消</button><button className="primary" onClick={() => void createCaseFromTrace()} disabled={creatingCase || !datasetId || !versionId || !inputSelection}>{creatingCase ? <LoaderCircle size={16} className="spin" /> : <Link2 size={16} />} {creatingCase ? "正在创建..." : "创建 Dataset Case"}</button></div></div>}</div>}
          {createdCase && <div className="trace-section created-case"><div className="trace-section-heading"><h3><Check size={15} /> 已建立 Trace 证据关联</h3><span>Dataset Case 已可用于后续 Experiment</span></div><p><strong>{createdCase.id}</strong> 已创建到 Dataset Version v{createdCase.version}。</p><small>source_trace_id: {createdCase.sourceTraceId ?? detail.trace_id}</small><small>source_span_ids: {createdCase.sourceSpanIds.length ? createdCase.sourceSpanIds.join(", ") : selectedSpanId}</small></div>}
          <div className="trace-section score-evidence"><div className="trace-section-heading"><h3>Scores / Annotations</h3><span>{detail.scores.length} 条评分证据</span></div>{detail.scores.length ? detail.scores.map((score) => <ScoreRecord key={score.id} score={score} spans={detail.spans} />) : <p className="detail-muted">此 Trace 还没有评分。完成在线规则评测、外部 LLM Judge 或人工标注后，评分会关联到这里。</p>}</div>
          <div className="trace-section"><div className="trace-section-heading"><h3>Trace 扩展字段</h3></div><pre className="trace-json">{formatJson(detail.extensions)}</pre></div>
        </div>
      </> : <div className="panel-placeholder"><Workflow size={24} /><p>从左侧选择一条 Trace 查看执行详情。</p></div>}</section>
    </div>
  </section>;
}
