"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  Check,
  ChevronRight,
  CircleDashed,
  Clock3,
  Download,
  ExternalLink,
  FileJson,
  FileSearch,
  GitCompareArrows,
  LoaderCircle,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { API_URL, PROJECT_ID, SESSION, fetchApi } from "./api-client";

type RunStatus =
  "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
type ExecutionStatus =
  "queued" | "running" | "completed" | "failed" | "cancelled";
type ScoreStatus = "passed" | "failed" | "missing" | "error" | "not_run";
type Direction = "higher_is_better" | "lower_is_better";
type JsonValue =
  Record<string, unknown> | unknown[] | string | number | boolean | null;

type Metric = {
  metric_name: string;
  evaluator_version_id: string;
  valid_count: number;
  missing_count: number;
  error_count: number;
  passed_count: number;
  average: number | null;
  pass_rate: number | null;
  aggregation: string;
  threshold: number | null;
  direction: Direction;
};
type Score = {
  id: string;
  metric_name: string;
  evaluator_version_id: string;
  trace_id: string | null;
  status: ScoreStatus;
  value: number | null;
  label: string | null;
  passed: boolean | null;
  explanation: string | null;
  evidence: Array<Record<string, unknown>>;
  rubric: string | null;
  judge_model: string | null;
  provenance: Record<string, unknown> | null;
  threshold: number | null;
  direction: Direction;
  raw_response: JsonValue;
  raw_result: JsonValue;
};
type ReportCase = {
  case_id: string;
  metadata: Record<string, unknown>;
  execution_status: ExecutionStatus;
  error_type: string | null;
  error_message: string | null;
  output: JsonValue;
  trace_id: string | null;
  scores: Score[];
};
type Report = {
  run_id: string;
  status: RunStatus;
  total_cases: number;
  matched_cases: number;
  filters: Record<string, unknown>;
  metrics: Metric[];
  cases: ReportCase[];
  generated_at: string;
};
type ReportSummary = {
  run_id: string;
  status: RunStatus;
  agent_version_id: string;
  dataset_version_id: string;
  total_cases: number;
  completed_cases: number;
  failed_cases: number;
  metrics: Metric[];
  created_at: string;
  finished_at: string | null;
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
type ComparisonPoint = {
  run_id: string;
  average: number | null;
  pass_rate: number | null;
  valid_count: number;
  missing_count: number;
  error_count: number;
  passed_count: number;
  delta_average: number | null;
  delta_pass_rate: number | null;
};
type ComparisonRunCase = {
  run_id: string;
  execution_status: ExecutionStatus;
  output: JsonValue;
  trace_id: string | null;
  error_type: string | null;
  error_message: string | null;
  failed: boolean;
  scores: Score[];
};
type FirstError = {
  category:
    | "tool_selection"
    | "tool_arguments"
    | "tool_execution"
    | "retrieval"
    | "final_answer"
    | "format"
    | "timeout"
    | "cost_or_latency"
    | "indeterminate";
  reason: string;
  baseline_trace_id: string | null;
  candidate_trace_id: string | null;
  baseline_span_id: string | null;
  candidate_span_id: string | null;
  evidence: Array<Record<string, unknown>>;
};
type ComparisonCase = {
  case_id: string;
  metadata: Record<string, unknown>;
  critical: boolean;
  runs: ComparisonRunCase[];
  first_error: FirstError | null;
};
type Comparison = {
  dataset_version_id: string;
  baseline_run_id: string;
  runs: Array<{
    run_id: string;
    agent_version_id: string;
    agent_version: Record<string, unknown>;
    dataset_version_id: string;
    status: RunStatus;
    total_cases: number;
    completed_cases: number;
    failed_cases: number;
    created_at: string;
    finished_at: string | null;
  }>;
  metric_comparisons: Array<{
    metric_name: string;
    comparable: boolean;
    reason: string | null;
    points: ComparisonPoint[];
  }>;
  new_failures: Array<{
    case_id: string;
    run_id: string;
    baseline_run_id: string;
    failed_metrics: string[];
    critical: boolean;
    first_error: FirstError | null;
  }>;
  recovered_cases: Array<{
    case_id: string;
    run_id: string;
    baseline_run_id: string;
    failed_metrics: string[];
    critical: boolean;
    first_error: FirstError | null;
  }>;
  case_comparisons: ComparisonCase[];
  missing_evidence: Array<{
    run_id: string;
    metric_name: string;
    status: "missing" | "error";
    case_ids: string[];
  }>;
  critical_task_impact: Array<{
    candidate_run_id: string;
    critical_case_count: number;
    baseline_failed_count: number;
    candidate_failed_count: number;
    newly_regressed_case_ids: string[];
    recovered_case_ids: string[];
  }>;
};
type GateRule = {
  metric_name: string;
  evaluator_version_id?: string;
  aggregation: "average" | "pass_rate";
  minimum?: number;
  maximum?: number;
  require_all_passed: boolean;
};
type GateResult = {
  run_id: string;
  run_status: RunStatus;
  status:
    | "passed"
    | "failed"
    | "indeterminate"
    | "incomplete"
    | "PASS"
    | "WARNING"
    | "BLOCK"
    | "INCOMPLETE"
    | "INDETERMINATE";
  rules: Array<{
    rule: GateRule;
    status: "passed" | "failed" | "indeterminate" | "incomplete";
    actual_value: number | null;
    valid_count: number;
    missing_count: number;
    error_count: number;
    failed_case_ids: string[];
    reason: string | null;
  }>;
  policy_version?: string | null;
  generated_at: string;
};

function requestHeaders(): HeadersInit {
  return { "Content-Type": "application/json", "Authorization": `Bearer ${SESSION}` };
}
async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, {
    ...init,
    headers: { ...requestHeaders(), ...init?.headers },
  });
  const body = (await response.json().catch(() => null)) as
    T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body ? body.detail : null;
    if (Array.isArray(detail)) {
      throw new Error(
        detail
          .map((item) =>
            typeof item === "object" && item && "msg" in item
              ? String(item.msg)
              : String(item),
          )
          .join("；"),
      );
    }
    throw new Error(
      typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail !== null
          ? JSON.stringify(detail)
          : `请求失败（HTTP ${response.status}）`,
    );
  }
  return body as T;
}
function label(value: string): string {
  return (
    {
      queued: "排队中",
      running: "运行中",
      completed: "已完成",
      partial: "部分完成",
      failed: "失败",
      cancelled: "已取消",
      passed: "通过",
      missing: "缺失",
      error: "错误",
      not_run: "未运行",
      incomplete: "证据不完整",
      indeterminate: "无法确定",
      PASS: "通过",
      WARNING: "警告",
      BLOCK: "阻断发布",
      INCOMPLETE: "证据不完整",
      INDETERMINATE: "无法确定",
    }[value] ?? value.replaceAll("_", " ")
  );
}
function displayValue(value: JsonValue): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function formatMetric(metric: Metric): string {
  const value = metric.pass_rate ?? metric.average;
  return value === null
    ? "无评分"
    : metric.pass_rate !== null
      ? `通过率 ${Math.round(value * 100)}%`
      : value.toFixed(3);
}
function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function statusTone(
  status: string,
): "success" | "danger" | "warning" | "neutral" {
  if (status === "completed" || status === "passed" || status === "PASS") return "success";
  if (status === "failed" || status === "error" || status === "BLOCK") return "danger";
  if (
    status === "partial" ||
    status === "cancelled" ||
    status === "missing" ||
    status === "not_run" ||
    status === "incomplete" ||
    status === "indeterminate" ||
    status === "WARNING" ||
    status === "INCOMPLETE" ||
    status === "INDETERMINATE"
  )
    return "warning";
  return "neutral";
}

function comparisonDeltaLabel(delta: number): string {
  if (delta > 0) return "改善";
  if (delta < 0) return "退化";
  return "持平";
}

function diagnosisLabel(category: FirstError["category"]): string {
  return (
    {
      tool_selection: "工具选择",
      tool_arguments: "工具参数",
      tool_execution: "工具执行",
      retrieval: "检索",
      final_answer: "最终回答",
      format: "输出格式",
      timeout: "超时",
      cost_or_latency: "成本或延迟",
      indeterminate: "无法确定",
    }[category] ?? category
  );
}

function StatusMark({ status }: { status: string }) {
  const tone = statusTone(status);
  const Icon =
    tone === "success"
      ? Check
      : tone === "danger"
        ? XCircle
        : tone === "warning"
          ? AlertTriangle
          : CircleDashed;
  return (
    <span className={`report-status ${tone}`}>
      <Icon size={13} />
      {label(status)}
    </span>
  );
}

type ReportMode = "report" | "compare" | "gate";

export function ReportsView({ initialMode = "report" }: { initialMode?: ReportMode }) {
  const [mode, setMode] = useState<ReportMode>(initialMode);
  const [summaries, setSummaries] = useState<ReportSummary[]>([]);
  const [runId, setRunId] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [metric, setMetric] = useState("");
  const [executionStatus, setExecutionStatus] = useState("");
  const [search, setSearch] = useState("");
  const [selectedCase, setSelectedCase] = useState<ReportCase | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [busy, setBusy] = useState(true);
  const [detailBusy, setDetailBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [comparisonCaseId, setComparisonCaseId] = useState<string | null>(null);
  const [comparisonFilter, setComparisonFilter] = useState<"all" | "regressed" | "recovered">("all");
  const [compareBusy, setCompareBusy] = useState(false);
  const [gateMetric, setGateMetric] = useState("");
  const [gateMinimum, setGateMinimum] = useState("0.9");
  const [gateHard, setGateHard] = useState(false);
  const [usePolicy, setUsePolicy] = useState(false);
  const [gateResult, setGateResult] = useState<GateResult | null>(null);
  const [gatePolicy, setGatePolicy] = useState("");
  const [pendingCaseId, setPendingCaseId] = useState<string | null>(null);
  const timelineRequestRef = useRef<AbortController | null>(null);

  async function loadSummaries() {
    setBusy(true);
    setNotice(null);
    try {
      const next = await requestJson<ReportSummary[]>(
        `/projects/${PROJECT_ID}/reports`,
      );
      setSummaries(next);
      setRunId((current) => next.some((item) => item.run_id === current) ? current : next[0]?.run_id || "");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法加载报告。");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadSummaries();
  }, []);
  useEffect(() => {
    if (!runId) {
      setReport(null);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (metric) params.set("metric", metric);
    if (executionStatus) params.set("execution_status", executionStatus);
    setBusy(true);
    setNotice(null);
    setSelectedCase(null);
    setTimeline(null);
    let timer: number | undefined;
    let stopped = false;
    let firstLoad = true;
    const refresh = async () => {
      try {
        const next = await requestJson<Report>(
          `/projects/${PROJECT_ID}/reports/${runId}?${params.toString()}`,
          { signal: controller.signal },
        );
        if (stopped) return;
        setReport(next);
        if (next.status === "queued" || next.status === "running") {
          timer = window.setTimeout(() => void refresh(), 2500);
        }
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setNotice(error instanceof Error ? error.message : "无法加载此报告。");
          timer = window.setTimeout(() => void refresh(), 4000);
        }
      } finally {
        if (firstLoad && !controller.signal.aborted) {
          firstLoad = false;
          setBusy(false);
        }
      }
    };
    void refresh();
    return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer); controller.abort(); };
  }, [runId, metric, executionStatus]);

  useEffect(() => {
    if (!pendingCaseId || !report) return;
    const item = report.cases.find((candidate) => candidate.case_id === pendingCaseId);
    if (!item) return;
    setPendingCaseId(null);
    void selectCase(item);
  }, [pendingCaseId, report]);

  const metricNames = useMemo(
    () =>
      Array.from(
        new Set(
          (
            summaries.find((item) => item.run_id === runId)?.metrics ??
            report?.metrics ??
            []
          ).map((item) => item.metric_name),
        ),
      ).sort(),
    [summaries, runId, report],
  );
  const visibleCases = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized || !report) return report?.cases ?? [];
    return report.cases.filter(
      (item) =>
        item.case_id.toLowerCase().includes(normalized) ||
        JSON.stringify(item.metadata).toLowerCase().includes(normalized),
    );
  }, [report, search]);
  const failures =
    report?.cases.filter(
      (item) =>
        item.execution_status === "failed" ||
        item.scores.some(
          (score) => score.status === "failed" || score.status === "error",
        ),
    ).length ?? 0;

  function toggleComparison(runId: string) {
    setComparisonIds((current) =>
      current.includes(runId)
        ? current.filter((item) => item !== runId)
        : [...current, runId].slice(-2),
    );
    setComparison(null);
    setComparisonCaseId(null);
  }
  async function createComparison() {
    if (comparisonIds.length !== 2) {
      setNotice("请选择恰好两个运行；第一个将作为基线。");
      return;
    }
    setCompareBusy(true);
    setNotice(null);
    try {
      setComparison(
        await requestJson<Comparison>(`/projects/${PROJECT_ID}/comparisons`, {
          method: "POST",
          body: JSON.stringify({ run_ids: comparisonIds }),
        }),
      );
      setComparisonCaseId(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法比较这些运行。");
    } finally {
      setCompareBusy(false);
    }
  }
  function openReportCase(nextRunId: string, caseId: string) {
    setMode("report");
    setRunId(nextRunId);
    setPendingCaseId(caseId);
    setMetric("");
    setExecutionStatus("");
  }
  async function evaluateGate() {
    if (!runId || (!usePolicy && !gateMetric)) {
      setNotice(usePolicy ? "评估门禁前，请选择报告运行。" : "评估门禁前，请选择报告运行和指标。");
      return;
    }
    if (usePolicy && !gatePolicy.trim()) {
      setNotice("请输入当前项目实际使用的 YAML 门禁策略。");
      return;
    }
    const minimum = Number(gateMinimum);
    if (!usePolicy && !gateHard && (!Number.isFinite(minimum) || gateMinimum.trim() === "")) {
      setNotice("请输入数值型最低阈值，或要求所有用例通过。");
      return;
    }
    setCompareBusy(true);
    setNotice(null);
    const [metricName, evaluatorVersionId] = gateMetric.split("::");
    const rule: GateRule = {
      metric_name: metricName,
      evaluator_version_id: evaluatorVersionId,
      aggregation: "pass_rate",
      require_all_passed: gateHard,
    };
    if (!gateHard && Number.isFinite(minimum) && gateMinimum.trim() !== "")
      rule.minimum = minimum;
    try {
      const nextGateResult = await requestJson<GateResult>(
        `/projects/${PROJECT_ID}/runs/${runId}/regression-gate`,
        {
          method: "POST",
          body: JSON.stringify(
            usePolicy ? { policy_yaml: gatePolicy } : { rules: [rule] },
          ),
        },
      );
      setGateResult(nextGateResult);
      if (nextGateResult.status === "BLOCK" && comparison) {
        const failedCaseId = nextGateResult.rules
          .flatMap((item) => item.failed_case_ids)
          .find((caseId) => comparison.case_comparisons.some((item) => item.case_id === caseId));
        if (failedCaseId) setComparisonCaseId(failedCaseId);
      }
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "无法评估此回归门禁。",
      );
    } finally {
      setCompareBusy(false);
    }
  }
  async function downloadReport(format: "json" | "csv") {
    if (!runId) return;
    const params = new URLSearchParams({ format });
    if (metric) params.set("metric", metric);
    if (executionStatus) params.set("execution_status", executionStatus);
    try {
      const response = await fetchApi(
        `${API_URL}/projects/${PROJECT_ID}/reports/${runId}/export?${params.toString()}`,
        { headers: requestHeaders() },
      );
      if (!response.ok) throw new Error(`导出失败（${response.status}）`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `evaluation-report-${runId}.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法导出此报告。");
    }
  }

  async function downloadComparison(format: "json" | "markdown") {
    if (comparisonIds.length !== 2) {
      setNotice("请先选择基线和候选两个 Experiment，再导出比较 artifact。");
      return;
    }
    const gate = gateResult
      ? usePolicy
        ? { policy_yaml: gatePolicy }
        : gateMetric
          ? {
              rules: [
                {
                  metric_name: gateMetric.split("::")[0],
                  evaluator_version_id: gateMetric.split("::")[1],
                  aggregation: "pass_rate",
                  require_all_passed: gateHard,
                  ...(gateHard ? {} : { minimum: Number(gateMinimum) }),
                },
              ],
            }
          : undefined
      : undefined;
    try {
      const response = await fetchApi(
        `${API_URL}/projects/${PROJECT_ID}/comparisons/artifact?format=${format}`,
        {
          method: "POST",
          headers: requestHeaders(),
          body: JSON.stringify({ run_ids: comparisonIds, ...(gate ? { gate } : {}) }),
        },
      );
      if (!response.ok) throw new Error(`artifact 导出失败（HTTP ${response.status}）`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `agent-eval-comparison.${format === "markdown" ? "md" : "json"}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法导出比较 artifact。");
    }
  }

  async function selectCase(item: ReportCase) {
    timelineRequestRef.current?.abort();
    setSelectedCase(item);
    setTimeline(null);
    if (!item.trace_id) { setDetailBusy(false); return; }
    setDetailBusy(true);
    const controller = new AbortController();
    timelineRequestRef.current = controller;
    try {
      const nextTimeline = await requestJson<Timeline>(
        `/projects/${PROJECT_ID}/traces/${item.trace_id}/timeline`,
        { signal: controller.signal },
      );
      if (!controller.signal.aborted && timelineRequestRef.current === controller) setTimeline(nextTimeline);
    } catch (error) {
      if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : "Unable to load this Trace timeline.");
    } finally {
      if (!controller.signal.aborted && timelineRequestRef.current === controller) setDetailBusy(false);
    }
  }

  return (
    <section className="reports-workbench">
      <div className="resource-heading reports-heading">
        <div>
          <p className="eyebrow">
            <BarChart3 size={14} /> 质量信号
          </p>
          <h1>评测报告</h1>
          <p>检查指标状态、定位失败，并将每项评分追溯到生成它的执行过程。</p>
        </div>
        <button
          className="outline-button"
          onClick={() => void loadSummaries()}
          disabled={busy}
        >
          <LoaderCircle className={busy ? "spin" : ""} size={16} /> 刷新报告
        </button>
      </div>
      {notice && (
        <div className="report-notice">
          <AlertTriangle size={16} />
          {notice}
        </div>
      )}
      <div className="report-tabs" role="tablist">
        <button
          className={mode === "report" ? "active" : ""}
          onClick={() => setMode("report")}
          role="tab"
          aria-selected={mode === "report"}
        >
          <FileSearch size={15} /> 报告详情
        </button>
        <button
          className={mode === "compare" ? "active" : ""}
          onClick={() => setMode("compare")}
          role="tab"
          aria-selected={mode === "compare"}
        >
          <GitCompareArrows size={15} /> 比较与门禁
        </button>
        <button
          className={mode === "gate" ? "active" : ""}
          onClick={() => setMode("gate")}
          role="tab"
          aria-selected={mode === "gate"}
        >
          <ShieldCheck size={15} /> 发布门禁
        </button>
      </div>
      {mode === "compare" || mode === "gate" ? (
        <ComparisonGatePanel
          summaries={summaries}
          selectedRunId={runId}
          comparisonIds={comparisonIds}
          comparison={comparison}
          gateMetric={gateMetric}
          minimum={gateMinimum}
          hard={gateHard}
          gateResult={gateResult}
          comparisonCaseId={comparisonCaseId}
          comparisonFilter={comparisonFilter}
          usePolicy={usePolicy}
          gatePolicy={gatePolicy}
          busy={compareBusy}
          onToggle={toggleComparison}
          onCompare={() => void createComparison()}
          onMetric={setGateMetric}
          onMinimum={setGateMinimum}
          onHard={setGateHard}
          onSelectCase={setComparisonCaseId}
          onFilter={setComparisonFilter}
          onUsePolicy={setUsePolicy}
          onPolicy={setGatePolicy}
          onOpenCase={openReportCase}
          onDownload={downloadComparison}
          onGate={() => void evaluateGate()}
        />
      ) : (
        <>
          <div className="report-controls panel">
      <label className="field-label">
              评测运行
              <select
                value={runId}
                onChange={(event) => {
                  setRunId(event.target.value);
                  setMetric("");
                  setExecutionStatus("");
                setGateResult(null);
                setGateMetric("");
                }}
                disabled={busy && summaries.length === 0}
              >
                <option value="">选择运行</option>
                {summaries.map((item) => (
                  <option key={item.run_id} value={item.run_id}>
                    {item.run_id.slice(0, 12)} / {item.total_cases} 个用例 /{" "}
                    {label(item.status)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field-label">
              指标
              <select
                value={metric}
                onChange={(event) => setMetric(event.target.value)}
                disabled={!runId}
              >
                <option value="">所有指标</option>
                {metricNames.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="field-label">
              执行状态
              <select
                value={executionStatus}
                onChange={(event) => setExecutionStatus(event.target.value)}
                disabled={!runId}
              >
                <option value="">所有状态</option>
                {["completed", "failed", "queued", "running", "cancelled"].map(
                  (item) => (
                    <option key={item} value={item}>
                      {label(item)}
                    </option>
                  ),
                )}
              </select>
            </label>
            <label className="report-search">
              <Search size={15} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="查找用例或元数据"
                aria-label="查找报告用例"
              />
            </label>
          </div>
          {runId && (
            <div className="report-downloads">
              <span>包含配置快照、指标定义和已应用的筛选条件。</span>
              <button
                className="outline-button compact"
                onClick={() => void downloadReport("csv")}
              >
                <Download size={14} /> CSV
              </button>
              <button
                className="outline-button compact"
                onClick={() => void downloadReport("json")}
              >
                <Download size={14} /> JSON
              </button>
            </div>
          )}
          {!busy && !report && !notice && (
            <div className="report-empty panel">
              <FileSearch size={24} />
              <strong>暂无评测报告</strong>
              <span>开始一次评测运行以生成评分和 Trace 证据。</span>
            </div>
          )}
          {report && (
            <>
              <div className="report-summary">
                <div>
                  <span>运行</span>
                  <strong>{report.run_id}</strong>
                  <StatusMark status={report.status} />
                </div>
                <div>
                  <span>显示的用例</span>
                  <strong>
                    {report.matched_cases} / {report.total_cases}
                  </strong>
                </div>
                <div>
                  <span>失败数</span>
                  <strong className={failures ? "danger-text" : ""}>
                    {failures}
                  </strong>
                </div>
                <div>
                  <span>生成时间</span>
                  <strong>{formatDate(report.generated_at)}</strong>
                </div>
              </div>
              <div className="metric-grid report-metrics">
                {report.metrics.length ? (
                  report.metrics.map((item) => (
                    <article
                      className="metric-tile accent-teal"
                      key={`${item.metric_name}-${item.evaluator_version_id}`}
                    >
                      <span className="metric-label">{item.metric_name}</span>
                      <strong>{formatMetric(item)}</strong>
                      <span className="metric-change muted">
                        {item.passed_count}/{item.valid_count} 个通过
                        {item.threshold !== null
                          ? ` / 阈值 ${item.threshold}`
                          : ""}
                      </span>
                    </article>
                  ))
                ) : (
                  <div className="report-metric-empty">
                    没有评分记录符合这些筛选条件。
                  </div>
                )}
              </div>
              <div className="reports-layout">
                <section className="report-cases panel">
                  <div className="report-panel-header">
                    <div>
                      <span className="section-kicker">用例</span>
                      <strong>{visibleCases.length} 个匹配用例</strong>
                    </div>
                    <span>输出和评分状态</span>
                  </div>
                  <div className="case-list">
                    {visibleCases.map((item) => (
                      <button
                        className={
                          selectedCase?.case_id === item.case_id
                            ? "report-case selected"
                            : "report-case"
                        }
                        onClick={() => void selectCase(item)}
                        key={item.case_id}
                      >
                        <span className="case-status">
                          <StatusMark status={item.execution_status} />
                        </span>
                        <span className="case-copy">
                          <strong>{item.case_id}</strong>
                          <small>
                            {item.error_type ??
                              (Object.entries(item.metadata)
                                .slice(0, 2)
                                .map(
                                  ([key, value]) => `${key}: ${String(value)}`,
                                )
                                .join(" / ") ||
                                "无元数据")}
                          </small>
                        </span>
                        <span className="case-score-summary">
                          {item.scores.slice(0, 2).map((score) => (
                            <i
                              className={statusTone(score.status)}
                              title={`${score.metric_name}: ${label(score.status)}`}
                              key={score.id}
                            >
                              {score.value ??
                                (score.passed === true
                                  ? "1"
                                  : score.passed === false
                                    ? "0"
                                    : "-")}
                            </i>
                          ))}
                        </span>
                        <ChevronRight size={16} />
                      </button>
                    ))}
                    {visibleCases.length === 0 && (
                      <div className="case-list-empty">
                        没有用例符合当前报告筛选条件。
                      </div>
                    )}
                  </div>
                </section>
                <CaseDetail
                  item={selectedCase}
                  timeline={timeline}
                  busy={detailBusy}
                />
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

function ComparisonGatePanel({
  summaries,
  selectedRunId,
  comparisonIds,
  comparison,
  gateMetric,
  minimum,
  hard,
  gateResult,
  comparisonCaseId,
  comparisonFilter,
  usePolicy,
  gatePolicy,
  busy,
  onToggle,
  onCompare,
  onMetric,
  onMinimum,
  onHard,
  onSelectCase,
  onFilter,
  onUsePolicy,
  onPolicy,
  onOpenCase,
  onDownload,
  onGate,
}: {
  summaries: ReportSummary[];
  selectedRunId: string;
  comparisonIds: string[];
  comparison: Comparison | null;
  gateMetric: string;
  minimum: string;
  hard: boolean;
  gateResult: GateResult | null;
  comparisonCaseId: string | null;
  comparisonFilter: "all" | "regressed" | "recovered";
  usePolicy: boolean;
  gatePolicy: string;
  busy: boolean;
  onToggle: (runId: string) => void;
  onCompare: () => void;
  onMetric: (value: string) => void;
  onMinimum: (value: string) => void;
  onHard: (value: boolean) => void;
  onSelectCase: (caseId: string | null) => void;
  onFilter: (value: "all" | "regressed" | "recovered") => void;
  onUsePolicy: (value: boolean) => void;
  onPolicy: (value: string) => void;
  onOpenCase: (runId: string, caseId: string) => void;
  onDownload: (format: "json" | "markdown") => void;
  onGate: () => void;
}) {
  const current = summaries.find((item) => item.run_id === selectedRunId);
  const metrics = current?.metrics ?? [];
  const comparisonRuns = comparison?.runs ?? [];
  const missingEvidence = comparison?.missing_evidence ?? [];
  const criticalTaskImpact = comparison?.critical_task_impact ?? [];
  const changedCaseIds = new Set([
    ...(comparison?.new_failures.map((item) => item.case_id) ?? []),
    ...(comparison?.recovered_cases.map((item) => item.case_id) ?? []),
  ]);
  const visibleComparisonCases = (comparison?.case_comparisons ?? []).filter((item) => {
    if (comparisonFilter === "all") return true;
    if (comparisonFilter === "regressed") return comparison?.new_failures.some((change) => change.case_id === item.case_id);
    return comparison?.recovered_cases.some((change) => change.case_id === item.case_id);
  });
  const [comparisonSearch, setComparisonSearch] = useState("");
  const [comparisonPage, setComparisonPage] = useState(0);
  const comparisonPageSize = 20;
  const searchedComparisonCases = visibleComparisonCases.filter((item) => {
    const query = comparisonSearch.trim().toLowerCase();
    return !query || item.case_id.toLowerCase().includes(query) || item.first_error?.category.toLowerCase().includes(query);
  });
  const pagedComparisonCases = searchedComparisonCases.slice(
    comparisonPage * comparisonPageSize,
    (comparisonPage + 1) * comparisonPageSize,
  );
  const comparisonPageCount = Math.max(1, Math.ceil(searchedComparisonCases.length / comparisonPageSize));
  const selectedComparisonCase = comparison?.case_comparisons.find((item) => item.case_id === comparisonCaseId) ?? null;
  const candidateRunId = comparisonRuns[1]?.run_id ?? selectedRunId;
  return (
    <div className="compare-workbench">
      <section className="compare-config panel">
        <div className="report-panel-header">
          <div>
            <span className="section-kicker">版本比较</span>
            <strong>选择基线和候选版本</strong>
          </div>
          <span>已选择 {comparisonIds.length}/2</span>
        </div>
        <div className="compare-config-body">
          <div className="comparison-runs">
            {summaries.map((item) => (
              <label key={item.run_id}>
                <input
                  type="checkbox"
                  checked={comparisonIds.includes(item.run_id)}
                  onChange={() => onToggle(item.run_id)}
                />
                <span>
                  <strong>{item.run_id}</strong>
                  <small>
                    {item.dataset_version_id.slice(0, 10)} / {item.total_cases}{" "}
                    个用例 / {label(item.status)}
                  </small>
                </span>
              </label>
            ))}
          </div>
          <button
            className="primary"
            onClick={onCompare}
            disabled={busy || comparisonIds.length !== 2}
          >
            <GitCompareArrows size={16} /> {busy ? "正在比较……" : "比较运行"}
          </button>
        </div>
      </section>
      {comparison && (
        <section className="comparison-result panel">
          <div className="report-panel-header">
            <div>
              <span className="section-kicker">比较结果</span>
              <strong>基线 vs 候选</strong>
            </div>
            <span>数据集版本 {comparison.dataset_version_id.slice(0, 12)}</span>
          </div>
          <div className="comparison-run-summary">
            {comparisonRuns.map((run, index) => (
              <div key={run.run_id}>
                <span>{index === 0 ? "基线版本" : "候选版本"}</span>
                <strong>{run.run_id}</strong>
                <small>
                  Agent {run.agent_version_id.slice(0, 12)} / {run.completed_cases}/
                  {run.total_cases} 个用例 / {label(run.status)}
                </small>
              </div>
            ))}
          </div>
          <div className="comparison-metrics">
            {comparison.metric_comparisons.map((item) => (
              <div key={item.metric_name}>
                <strong>{item.metric_name}</strong>
                {!item.comparable ? (
                  <span className="comparison-reason">{item.reason}</span>
                ) : (
                  item.points.map((point) => (
                    <span className="comparison-point" key={point.run_id}>
                      {point.run_id === comparison.baseline_run_id ? "基线" : "候选"}：
                      {point.pass_rate === null
                        ? "无评分"
                        : `${Math.round(point.pass_rate * 100)}%`}
                      {point.delta_pass_rate !== null && (
                        <b
                          className={
                            point.delta_pass_rate < 0
                              ? "danger-text"
                              : "positive"
                          }
                        >
                          {comparisonDeltaLabel(point.delta_pass_rate)} {" "}
                          {point.delta_pass_rate >= 0 ? "+" : ""}
                          {(point.delta_pass_rate * 100).toFixed(1)} 个百分点
                        </b>
                      )}
                    </span>
                  ))
                )}
              </div>
            ))}
          </div>
          <div className="change-columns">
            <div>
              <span className="section-kicker">新增失败</span>
              {comparison.new_failures.length ? (
                comparison.new_failures.map((item) => (
                  <button
                    className="change-case-button"
                    key={`${item.case_id}-${item.run_id}`}
                    onClick={() => onSelectCase(item.case_id)}
                    type="button"
                  >
                    <b>{item.case_id}</b>
                    {item.failed_metrics.join(", ") || "执行"}
                    <ChevronRight size={13} />
                  </button>
                ))
              ) : (
                <p>没有新出现的失败用例。</p>
              )}
            </div>
            <div>
              <span className="section-kicker">恢复用例</span>
              {comparison.recovered_cases.length ? (
                comparison.recovered_cases.map((item) => (
                  <button
                    className="change-case-button"
                    key={`${item.case_id}-${item.run_id}`}
                    onClick={() => onSelectCase(item.case_id)}
                    type="button"
                  >
                    <b>{item.case_id}</b>
                    {item.failed_metrics.join(", ") || "执行"}
                    <ChevronRight size={13} />
                  </button>
                ))
              ) : (
                <p>没有恢复的用例。</p>
              )}
            </div>
          </div>
          <div className="comparison-case-toolbar">
            <span className="section-kicker">用例诊断</span>
            <div className="search-field"><Search size={16} /><input value={comparisonSearch} onChange={(event) => { setComparisonSearch(event.target.value); setComparisonPage(0); }} aria-label="搜索比较用例" placeholder="搜索 Case ID 或首错类型" /></div>
            <div className="case-filter-buttons" role="group" aria-label="比较用例筛选">
              {(
                [
                  ["all", "全部"],
                  ["regressed", "新增失败"],
                  ["recovered", "恢复用例"],
                ] as const
              ).map(([value, text]) => (
                <button
                  className={comparisonFilter === value ? "active" : ""}
                  key={value}
                  onClick={() => { onFilter(value); setComparisonPage(0); }}
                  type="button"
                >
                  {text}
                </button>
              ))}
            </div>
          </div>
          <div className="comparison-case-list">
            {pagedComparisonCases.map((item) => {
              const candidate = item.runs.find((run) => run.run_id === candidateRunId) ?? item.runs[1];
              const changed = changedCaseIds.has(item.case_id);
              return (
                <button
                  className={`comparison-case-row ${comparisonCaseId === item.case_id ? "selected" : ""}`}
                  key={item.case_id}
                  onClick={() => onSelectCase(item.case_id)}
                  type="button"
                >
                  <span className={`case-change-mark ${changed && candidate?.failed ? "danger" : changed ? "success" : "neutral"}`}>
                    {changed ? (candidate?.failed ? "回归" : "恢复") : "稳定"}
                  </span>
                  <span className="comparison-case-copy">
                    <strong>{item.case_id}</strong>
                    <small>{item.critical ? "关键任务" : "普通任务"} / {candidate ? label(candidate.execution_status) : "无执行记录"}</small>
                  </span>
                  <span className="comparison-case-diagnosis">
                    {item.first_error ? diagnosisLabel(item.first_error.category) : "无首错诊断"}
                  </span>
                  <ChevronRight size={15} />
                </button>
              );
            })}
            {searchedComparisonCases.length === 0 && (
              <p className="case-list-empty">没有符合当前筛选条件的比较用例。</p>
            )}
          </div>
          {searchedComparisonCases.length > 0 && <div className="editor-actions"><button className="outline-button compact" type="button" disabled={comparisonPage === 0} onClick={() => setComparisonPage((value) => Math.max(0, value - 1))}>上一页</button><span className="detail-muted">{comparisonPage + 1} / {comparisonPageCount} 页，{searchedComparisonCases.length} 个 Case</span><button className="outline-button compact" type="button" disabled={comparisonPage + 1 >= comparisonPageCount} onClick={() => setComparisonPage((value) => Math.min(comparisonPageCount - 1, value + 1))}>下一页</button></div>}
          {selectedComparisonCase && (
            <ComparisonCaseDetail
              item={selectedComparisonCase}
              baselineRunId={comparison.baseline_run_id}
              candidateRunId={candidateRunId}
              onOpenCase={onOpenCase}
            />
          )}
          {(missingEvidence.length > 0 || criticalTaskImpact.length > 0) && (
            <div className="comparison-evidence-summary">
              {missingEvidence.length > 0 && (
                <div>
                  <span className="section-kicker">缺失证据</span>
                  {missingEvidence.map((item) => item.case_ids.length ? (
                    <button className="change-case-button" type="button" key={`${item.run_id}-${item.metric_name}`} onClick={() => onSelectCase(item.case_ids[0])}>
                      <b>{item.metric_name}</b>{item.run_id.slice(0, 12)} / {label(item.status)} / {item.case_ids.length} 个用例<ChevronRight size={13} />
                    </button>
                  ) : <p key={`${item.run_id}-${item.metric_name}`}>{item.run_id.slice(0, 12)} / {item.metric_name} / {label(item.status)} / 0 个用例</p>)}
                </div>
              )}
              {criticalTaskImpact.map((item) => (
                <div key={item.candidate_run_id}>
                  <span className="section-kicker">关键任务影响</span>
                  <p>
                    候选失败 {item.candidate_failed_count}/{item.critical_case_count}，新增回归 {item.newly_regressed_case_ids.length} 个，恢复 {item.recovered_case_ids.length} 个
                  </p>
                </div>
              ))}
            </div>
          )}
          <div className="artifact-actions">
            <span>导出本次比较，供 CI 或评审留档</span>
            <button className="outline-button" onClick={() => onDownload("markdown")} type="button">
              <Download size={14} /> Markdown
            </button>
            <button className="outline-button" onClick={() => onDownload("json")} type="button">
              <FileJson size={14} /> JSON
            </button>
          </div>
        </section>
      )}
      <section className="gate-config panel">
        <div className="report-panel-header">
          <div>
            <span className="section-kicker">回归门禁</span>
            <strong>机器可读的质量检查</strong>
          </div>
          <ShieldCheck size={17} />
        </div>
        <div className="gate-config-body">
          <label className="field-label">
            指标
            <select
              value={gateMetric}
              onChange={(event) => onMetric(event.target.value)}
            >
              <option value="">选择指标</option>
              {metrics.map((item) => (
                <option
                  key={`${item.metric_name}-${item.evaluator_version_id}`}
                  value={`${item.metric_name}::${item.evaluator_version_id}`}
                >
                  {item.metric_name} / {item.evaluator_version_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            最低通过率
            <input
              type="number"
              min="0"
              max="1"
              step="0.01"
              value={minimum}
              onChange={(event) => onMinimum(event.target.value)}
              disabled={hard}
            />
          </label>
          <label className="gate-hard">
            <input
              type="checkbox"
              checked={hard}
              onChange={(event) => onHard(event.target.checked)}
            />
            <span className="checkbox-mark">
              <Check size={12} />
            </span>{" "}
            要求所有用例通过
          </label>
          <label className="gate-hard">
            <input
              type="checkbox"
              checked={usePolicy}
              onChange={(event) => onUsePolicy(event.target.checked)}
            />
            <span className="checkbox-mark">
              <Check size={12} />
            </span>{" "}
            使用 YAML 门禁策略
          </label>
          {usePolicy && (
            <label className="field-label gate-policy-editor">
              策略 YAML
              <textarea
                value={gatePolicy}
                onChange={(event) => onPolicy(event.target.value)}
                placeholder="粘贴当前项目实际使用的 YAML 门禁策略"
                rows={8}
                spellCheck={false}
              />
              <small>策略会随本次评估解析；无效 YAML、未知指标或缺失证据不会通过门禁。</small>
            </label>
          )}
          <button
            className="primary"
            onClick={onGate}
            disabled={busy || !selectedRunId}
          >
            <ShieldCheck size={16} /> {busy ? "正在检查……" : "评估门禁"}
          </button>
          {gateResult && (
            <div className={`gate-result ${statusTone(gateResult.status)}`}>
              <div className="gate-result-heading">
                <StatusMark status={gateResult.status} />
                {gateResult.status === "BLOCK" && <strong>此结果阻断发布</strong>}
                {gateResult.policy_version && <small>策略 {gateResult.policy_version}</small>}
              </div>
              {gateResult.rules.map((item) => (
                <div className="gate-rule-result" key={`${item.rule.metric_name}-${item.rule.evaluator_version_id ?? "any"}`}>
                  <div>
                    <strong>{item.rule.metric_name}</strong>
                    <StatusMark status={item.status} />
                  </div>
                  <p>
                    实际值：{item.actual_value === null
                      ? "无结果"
                      : item.rule.aggregation === "pass_rate"
                        ? `${Math.round(item.actual_value * 100)}%`
                        : item.actual_value.toFixed(3)}
                    <span>有效 {item.valid_count} / 缺失 {item.missing_count} / 错误 {item.error_count}</span>
                  </p>
                  {item.reason && <p>{item.reason}</p>}
                  {item.failed_case_ids.length > 0 && (
                    <div className="gate-failed-cases">
                      <span>失败用例：</span>
                      {item.failed_case_ids.map((caseId) => (
                        <button
                          className="gate-failed-case"
                          key={caseId}
                          onClick={() => onSelectCase(caseId)}
                          type="button"
                        >
                          {caseId} <ExternalLink size={11} />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function ComparisonCaseDetail({
  item,
  baselineRunId,
  candidateRunId,
  onOpenCase,
}: {
  item: ComparisonCase;
  baselineRunId: string;
  candidateRunId: string;
  onOpenCase: (runId: string, caseId: string) => void;
}) {
  const orderedRuns = [
    item.runs.find((run) => run.run_id === baselineRunId),
    item.runs.find((run) => run.run_id === candidateRunId),
  ].filter((run): run is ComparisonRunCase => Boolean(run));

  return (
    <div className="comparison-case-detail">
      <div className="comparison-case-detail-heading">
        <div>
          <span className="section-kicker">选中用例</span>
          <strong>{item.case_id}</strong>
        </div>
        {item.critical && <span className="critical-badge">关键任务</span>}
      </div>
      {item.first_error && (
        <div className="diagnosis-panel">
          <div className="diagnosis-heading">
            <ShieldAlert size={15} />
            <strong>首错诊断：{diagnosisLabel(item.first_error.category)}</strong>
          </div>
          <p>{item.first_error.reason}</p>
          <div className="diagnosis-identifiers">
            <span>Baseline Span：{item.first_error.baseline_span_id ?? "无"}</span>
            <span>Candidate Span：{item.first_error.candidate_span_id ?? "无"}</span>
          </div>
          {item.first_error.evidence.length > 0 && (
            <details>
              <summary>查看诊断证据</summary>
              <pre>{displayValue(item.first_error.evidence)}</pre>
            </details>
          )}
        </div>
      )}
      {!item.first_error && <p className="detail-muted">当前没有可对齐的首错诊断证据。</p>}
      <div className="comparison-run-details">
        {orderedRuns.map((run, index) => (
          <article key={run.run_id}>
            <div className="comparison-run-detail-heading">
              <div>
                <span className="section-kicker">{index === 0 ? "Baseline" : "Candidate"}</span>
                <strong>{run.run_id}</strong>
              </div>
              <StatusMark status={run.failed ? "failed" : run.execution_status} />
            </div>
            {(run.error_type || run.error_message) && (
              <div className="sample-error">
                <ShieldAlert size={14} />
                <span>{run.error_type ?? "执行错误"}：{run.error_message ?? "此运行失败。"}</span>
              </div>
            )}
            <DetailBlock title="实际输出">
              <pre>{displayValue(run.output)}</pre>
            </DetailBlock>
            <div className="comparison-score-list">
              <span className="section-kicker">评分</span>
              {run.scores.length > 0 ? run.scores.map((score) => (
                <div key={score.id}>
                  <span>{score.metric_name}</span>
                  <StatusMark status={score.status} />
                  <b>{score.value ?? score.label ?? "无值"}</b>
                </div>
              )) : <p className="detail-muted">没有评分证据。</p>}
            </div>
            <div className="trace-link-row">
              <span>Trace：{run.trace_id ?? "无 Trace"}</span>
              <button
                className="trace-link"
                onClick={() => onOpenCase(run.run_id, item.case_id)}
                type="button"
              >
                {run.trace_id ? "查看 Trace / 报告" : "查看报告"} <ExternalLink size={12} />
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function CaseDetail({
  item,
  timeline,
  busy,
}: {
  item: ReportCase | null;
  timeline: Timeline | null;
  busy: boolean;
}) {
  if (!item)
    return (
      <section className="report-detail panel">
        <div className="report-panel-header">
          <div>
            <span className="section-kicker">用例详情</span>
            <strong>选择用例</strong>
          </div>
        </div>
        <div className="detail-empty">
          <Sparkles size={22} />
          <span>选择一个用例，以查看其输出、评分证据和 Trace 时间线。</span>
        </div>
      </section>
    );
  return (
    <section className="report-detail panel">
      <div className="report-panel-header">
        <div>
          <span className="section-kicker">用例详情</span>
          <strong>{item.case_id}</strong>
        </div>
        <StatusMark status={item.execution_status} />
      </div>
      <div className="detail-scroll">
        {(item.error_type || item.error_message) && (
          <div className="sample-error">
            <ShieldAlert size={15} />
            <div>
              <strong>{item.error_type ?? "执行错误"}</strong>
              <span>{item.error_message ?? "此用例未能成功完成。"}</span>
            </div>
          </div>
        )}
        <DetailBlock title="实际输出">
          <pre>{displayValue(item.output)}</pre>
        </DetailBlock>
        <div className="score-evidence">
          <span className="section-kicker">评分和证据</span>
          {item.scores.length ? (
            item.scores.map((score) => (
              <article className="score-record" key={score.id}>
                <div>
                  <strong>{score.metric_name}</strong>
                  <StatusMark status={score.status} />
                </div>
                <b>{score.value ?? score.label ?? "无值"}</b>
                {score.explanation && <p>{score.explanation}</p>}
                {score.evidence.length > 0 && (
                  <pre>{displayValue(score.evidence)}</pre>
                )}
                {score.rubric && <small>评分标准：{score.rubric}</small>}
                {score.provenance && (
                  <small>Judge 版本：{displayValue(score.provenance)}</small>
                )}
                {score.raw_response !== null && (
                  <details>
                    <summary>原始 Judge 响应</summary>
                    <pre>{displayValue(score.raw_response)}</pre>
                  </details>
                )}
              </article>
            ))
          ) : (
            <p className="detail-muted">此用例没有写入评分记录。</p>
          )}
        </div>
        <div className="trace-evidence">
          <span className="section-kicker">Trace 时间线</span>
          {busy && (
            <div className="timeline-loading">
              <LoaderCircle className="spin" size={15} /> 正在加载执行证据
            </div>
          )}
          {!busy && !item.trace_id && (
            <p className="detail-muted">此用例没有存储的 Trace 记录。</p>
          )}
          {!busy && timeline && (
            <div className="timeline-list">
              {timeline.spans.map((span) => (
                <div
                  className="timeline-row"
                  style={{ paddingLeft: `${span.depth * 13}px` }}
                  key={span.span_id}
                >
                  <i className={statusTone(span.status)} />
                  <div>
                    <strong>{span.name}</strong>
                    <span>
                      {span.kind} /{" "}
                      {span.duration_ms === null
                        ? "进行中"
                        : `${Math.round(span.duration_ms)} ms`}
                    </span>
                  </div>
                  <StatusMark status={span.status} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function DetailBlock({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="detail-block">
      <span className="section-kicker">{title}</span>
      {children}
    </div>
  );
}
