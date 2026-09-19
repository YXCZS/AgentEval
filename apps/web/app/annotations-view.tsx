"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, CircleAlert, ClipboardCheck, Link2, LoaderCircle, Plus, RefreshCw, Save } from "lucide-react";
import { API_URL, getProjectId, getSessionToken, fetchApi } from "./api-client";

type Evaluator = { id: string; name: string; version: string; evaluator_type: "deterministic" | "llm_judge" | "adapter" | "human"; rubric: string | null; enabled: boolean };
type Experiment = { id: string; name: string; status: string; created_at: string };
type ExperimentPage = { items: Experiment[]; total: number; next_offset: number | null };
type Manifest = { items: Array<{ case: { id: string; input: unknown }; attempts: unknown[] }> };
type Queue = { id: string; name: string; description: string | null; evaluator_version_id: string; created_at: string };
type QueueItem = { id: string; queue_id: string; run_id: string; case_id: string; trace_id: string | null; status: "pending" | "in_review" | "completed" | "skipped"; created_at: string; completed_at: string | null };
type Score = { id: string; value: number | null; label: string | null; passed: boolean | null; explanation: string | null; evidence: Array<Record<string, unknown>> };
type Audit = { id: string; action: "created" | "updated"; reviewer: string; previous_value: Record<string, unknown> | null; new_value: Record<string, unknown>; created_at: string };

function headers(): HeadersInit {
  return { "Content-Type": "application/json", "Authorization": `Bearer ${getSessionToken()}` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, { ...init, headers: { ...headers(), ...init?.headers } });
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
  }
  return body as T;
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "未记录";
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

const statusLabels: Record<QueueItem["status"], string> = { pending: "待评审", in_review: "评审中", completed: "已完成", skipped: "已跳过" };

export function AnnotationsView({ onOpenTrace }: { onOpenTrace: (traceId: string) => void }) {
  const [queues, setQueues] = useState<Queue[]>([]);
  const [evaluators, setEvaluators] = useState<Evaluator[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [selectedQueueId, setSelectedQueueId] = useState("");
  const [selectedItemId, setSelectedItemId] = useState("");
  const [selectedExperimentId, setSelectedExperimentId] = useState("");
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [score, setScore] = useState<Score | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [queueName, setQueueName] = useState("");
  const [queueDescription, setQueueDescription] = useState("");
  const [queueEvaluatorId, setQueueEvaluatorId] = useState("");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [passed, setPassed] = useState(true);
  const [explanation, setExplanation] = useState("");
  const [evidenceText, setEvidenceText] = useState("[]");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const humanEvaluators = useMemo(() => evaluators.filter((item) => item.evaluator_type === "human" && item.enabled), [evaluators]);
  const selectedQueue = queues.find((item) => item.id === selectedQueueId) ?? null;
  const selectedItem = items.find((item) => item.id === selectedItemId) ?? null;

  async function loadWorkspace(selectQueueId?: string) {
    setLoading(true);
    try {
      const [queueRows, evaluatorRows, experimentPage] = await Promise.all([
        requestJson<Queue[]>(`/projects/${getProjectId()}/annotation-queues`),
        requestJson<Evaluator[]>(`/projects/${getProjectId()}/evaluators`),
        requestJson<ExperimentPage>(`/projects/${getProjectId()}/experiments?limit=200`),
      ]);
      const experimentRows = experimentPage.items;
      setQueues(queueRows);
      setEvaluators(evaluatorRows);
      setExperiments(experimentRows);
      setSelectedQueueId(selectQueueId && queueRows.some((item) => item.id === selectQueueId) ? selectQueueId : queueRows[0]?.id ?? "");
      setQueueEvaluatorId((current) => current || (evaluatorRows.find((item) => item.evaluator_type === "human" && item.enabled)?.id ?? ""));
    } catch (error) {
      setNotice(`加载人工评审数据失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadItems(queueId: string) {
    if (!queueId) { setItems([]); return; }
    try {
      const rows = await requestJson<QueueItem[]>(`/projects/${getProjectId()}/annotation-queues/${queueId}/items`);
      setItems(rows);
      setSelectedItemId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id ?? "");
    } catch (error) {
      setItems([]);
      setNotice(`加载队列条目失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function loadManifest(experimentId: string) {
    setSelectedExperimentId(experimentId);
    setSelectedCaseId("");
    setManifest(null);
    if (!experimentId) return;
    try {
      const loaded = await requestJson<Manifest>(`/projects/${getProjectId()}/experiments/${experimentId}/manifest?limit=200`);
      setManifest(loaded);
      setSelectedCaseId(loaded.items[0]?.case.id ?? "");
    } catch (error) {
      setNotice(`加载 Experiment Case 失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function loadScore(queueId: string, item: QueueItem | null | undefined) {
    setScore(null);
    setAudit([]);
    setValue(""); setLabel(""); setPassed(true); setExplanation(""); setEvidenceText("[]");
    if (!queueId || !item) return;
    try {
      const current = await requestJson<Score>(`/projects/${getProjectId()}/annotation-queues/${queueId}/items/${item.id}/score`);
      setScore(current);
      setValue(current.value === null ? "" : String(current.value));
      setLabel(current.label ?? "");
      setPassed(current.passed ?? false);
      setExplanation(current.explanation ?? "");
      setEvidenceText(formatJson(current.evidence));
      const audits = await requestJson<Audit[]>(`/projects/${getProjectId()}/annotation-queues/${queueId}/scores/${current.id}/audit`);
      setAudit(audits);
    } catch (error) {
      if (error instanceof Error && error.message === "human score not found") return;
      setNotice(`加载评审记录失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  useEffect(() => { void loadWorkspace(); }, []);
  useEffect(() => { void loadItems(selectedQueueId); }, [selectedQueueId]);
  useEffect(() => { void loadScore(selectedQueueId, selectedItem); }, [selectedQueueId, selectedItemId]);

  async function createQueue() {
    if (!queueName.trim() || !queueEvaluatorId) { setNotice("请填写队列名称并选择人工评估器。"); return; }
    setBusy(true);
    try {
      const created = await requestJson<Queue>(`/projects/${getProjectId()}/annotation-queues`, { method: "POST", body: JSON.stringify({ name: queueName.trim(), description: queueDescription.trim() || null, evaluator_version_id: queueEvaluatorId }) });
      setQueueName(""); setQueueDescription(""); setNotice(`人工评审队列“${created.name}”已创建。`);
      await loadWorkspace(created.id);
    } catch (error) {
      setNotice(`创建队列失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally { setBusy(false); }
  }

  async function addItem() {
    if (!selectedQueue || !selectedExperimentId || !selectedCaseId) { setNotice("请选择 Experiment 和其中的 Case。 "); return; }
    setBusy(true);
    try {
      const created = await requestJson<QueueItem>(`/projects/${getProjectId()}/annotation-queues/${selectedQueue.id}/items`, { method: "POST", body: JSON.stringify({ run_id: selectedExperimentId, case_id: selectedCaseId }) });
      setNotice(`Case ${created.case_id} 已加入人工评审队列。`);
      await loadItems(selectedQueue.id);
      setSelectedItemId(created.id);
    } catch (error) {
      setNotice(`加入队列失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally { setBusy(false); }
  }

  async function submitScore() {
    if (!selectedQueue || !selectedItem) return;
    const numericValue = value.trim() ? Number(value) : null;
    if (numericValue !== null && !Number.isFinite(numericValue)) { setNotice("分数必须是有效数字。 "); return; }
    if (numericValue === null && !label.trim()) { setNotice("请填写数值分数或标签。 "); return; }
    let evidence: Array<Record<string, unknown>>;
    try {
      const parsed: unknown = JSON.parse(evidenceText);
      if (!Array.isArray(parsed) || parsed.some((item) => !item || typeof item !== "object" || Array.isArray(item))) throw new Error("证据必须是 JSON 对象数组");
      evidence = parsed as Array<Record<string, unknown>>;
    } catch (error) { setNotice(`证据格式无效：${error instanceof Error ? error.message : "无法解析"}`); return; }
    setBusy(true);
    try {
      const saved = await requestJson<Score>(`/projects/${getProjectId()}/annotation-queues/${selectedQueue.id}/items/${selectedItem.id}/score`, { method: "PUT", body: JSON.stringify({ value: numericValue, label: label.trim() || null, passed, explanation: explanation.trim() || null, evidence }) });
      setNotice("人工评分已保存，并写入不可变审计记录。 ");
      setScore(saved);
      const audits = await requestJson<Audit[]>(`/projects/${getProjectId()}/annotation-queues/${selectedQueue.id}/scores/${saved.id}/audit`);
      setAudit(audits);
      await loadItems(selectedQueue.id);
    } catch (error) {
      setNotice(`保存评分失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally { setBusy(false); }
  }

  return <section className="resource-view annotation-workbench">
    <div className="resource-heading"><div><p className="eyebrow"><ClipboardCheck size={14} /> 人工质量信号</p><h1>人工评审</h1><p>将真实 Experiment Case 加入队列，由浏览器会话提交人工评分。每次修改都会保存审核人、前后值和时间。</p></div><button className="outline-button" onClick={() => void loadWorkspace(selectedQueueId)} disabled={loading || busy}><RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新</button></div>
    {notice && <div className="inline-notice" role="status"><CircleAlert size={16} /> {notice}</div>}
    <section className="annotation-create panel"><div className="panel-heading"><div><p className="eyebrow">新建队列</p><h2>选择人工评估器</h2></div><Plus size={16} className="muted-icon" /></div><div className="annotation-form"><label className="field-label">队列名称<input aria-label="队列名称" value={queueName} onChange={(event) => setQueueName(event.target.value)} placeholder="例如：高风险回答复核" /></label><label className="field-label">说明<input aria-label="队列说明" value={queueDescription} onChange={(event) => setQueueDescription(event.target.value)} placeholder="可选" /></label><label className="field-label">人工评估器<select aria-label="人工评估器" value={queueEvaluatorId} onChange={(event) => setQueueEvaluatorId(event.target.value)}><option value="">请选择</option>{humanEvaluators.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.version}</option>)}</select></label><button className="primary" onClick={() => void createQueue()} disabled={busy || humanEvaluators.length === 0}><Plus size={16} /> 创建队列</button></div>{humanEvaluators.length === 0 && <p className="detail-muted annotation-hint">请先在“评估器”创建并启用一个“人工评审”类型的评估器。</p>}</section>
    <div className="annotation-layout">
      <aside className="panel annotation-queues"><div className="panel-heading"><div><p className="eyebrow">评审队列</p><h2>{queues.length} 个队列</h2></div><ClipboardCheck size={16} className="muted-icon" /></div>{loading ? <p className="panel-placeholder">正在加载队列...</p> : queues.length === 0 ? <p className="panel-placeholder">还没有人工评审队列。</p> : <div className="annotation-queue-list">{queues.map((queue) => <button key={queue.id} className={queue.id === selectedQueueId ? "annotation-queue-item selected" : "annotation-queue-item"} onClick={() => { setSelectedQueueId(queue.id); setSelectedItemId(""); setNotice(null); }}><strong>{queue.name}</strong><span>{queue.description || "未填写说明"}</span></button>)}</div>}</aside>
      <div className="panel annotation-detail">{selectedQueue ? <><div className="panel-heading"><div><p className="eyebrow">{selectedQueue.id}</p><h2>{selectedQueue.name}</h2><span className="detail-muted">创建于 {formatDate(selectedQueue.created_at)}</span></div><ClipboardCheck size={16} className="muted-icon" /></div><div className="annotation-detail-body"><section className="annotation-add-case"><div><strong>加入真实 Experiment Case</strong><span>平台不会执行 Agent，只将已有 Experiment 的 Case 放入人工评审队列。</span></div><div className="annotation-add-controls"><label className="field-label">Experiment<select aria-label="评审 Experiment" value={selectedExperimentId} onChange={(event) => void loadManifest(event.target.value)}><option value="">请选择已创建的 Experiment</option>{experiments.map((experiment) => <option key={experiment.id} value={experiment.id}>{experiment.name} · {experiment.id}</option>)}</select></label><label className="field-label">Case<select aria-label="评审 Case" value={selectedCaseId} onChange={(event) => setSelectedCaseId(event.target.value)} disabled={!manifest}><option value="">请选择 Case</option>{manifest?.items.map((item) => <option key={item.case.id} value={item.case.id}>{item.case.id}</option>)}</select></label><button className="outline-button" onClick={() => void addItem()} disabled={busy || !selectedExperimentId || !selectedCaseId}><Plus size={15} /> 加入队列</button></div></section><div className="annotation-review-layout"><section><div className="annotation-section-heading"><h3>待评审条目</h3><span>{items.length} 条</span></div>{items.length === 0 ? <p className="detail-muted">从上方选择 Experiment 和 Case，加入后可在这里评分。</p> : <div className="annotation-item-list">{items.map((item) => <button key={item.id} className={item.id === selectedItemId ? "annotation-item selected" : "annotation-item"} onClick={() => setSelectedItemId(item.id)}><span><strong>{item.case_id}</strong><small>{item.run_id}</small></span><b className={`annotation-status ${item.status}`}>{statusLabels[item.status]}</b></button>)}</div>}</section><section className="annotation-editor">{selectedItem ? <><div className="annotation-section-heading"><h3>提交人工评分</h3>{selectedItem.trace_id && <button className="text-button" onClick={() => onOpenTrace(selectedItem.trace_id!)}><Link2 size={14} /> 查看 Trace</button>}</div><p className="detail-muted">Case: <code>{selectedItem.case_id}</code> · Experiment: <code>{selectedItem.run_id}</code></p><div className="field-grid two"><label className="field-label">数值分数<input aria-label="人工分数" type="number" value={value} onChange={(event) => setValue(event.target.value)} placeholder="可选，例如 0.8" /></label><label className="field-label">标签<input aria-label="人工标签" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="可选，例如 needs_review" /></label></div><label className="check-line"><input aria-label="人工评分通过" type="checkbox" checked={passed} onChange={(event) => setPassed(event.target.checked)} /><span>{passed ? <Check size={13} /> : null}</span> 通过此 Case</label><label className="field-label">审核说明<textarea aria-label="人工评分说明" value={explanation} onChange={(event) => setExplanation(event.target.value)} rows={3} placeholder="记录判断依据" /></label><label className="field-label">证据 JSON<textarea aria-label="人工评分证据" value={evidenceText} onChange={(event) => setEvidenceText(event.target.value)} rows={4} /></label><button className="primary" onClick={() => void submitScore()} disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />} 保存人工评分</button>{score && <section className="annotation-audit"><div className="annotation-section-heading"><h3>审计历史</h3><span>{audit.length} 次记录</span></div>{audit.length ? audit.map((entry) => <article key={entry.id}><div><strong>{entry.action === "created" ? "首次评分" : "更新评分"}</strong><span>{entry.reviewer} · {formatDate(entry.created_at)}</span></div><details><summary>查看前后值</summary>{entry.previous_value && <pre>前：{formatJson(entry.previous_value)}</pre>}<pre>后：{formatJson(entry.new_value)}</pre></details></article>) : <p className="detail-muted">正在读取审计记录...</p>}</section>}</> : <p className="panel-placeholder">选择一个待评审条目。</p>}</section></div></div></> : <p className="panel-placeholder">先创建或选择一个人工评审队列。</p>}</div>
    </div>
  </section>;
}
