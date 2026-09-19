"use client";

import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  Clipboard,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  FileJson,
  FileUp,
  ListPlus,
  LoaderCircle,
  Plus,
  RefreshCw,
  Table2,
  Trash2,
  Upload,
  Webhook,
  X,
} from "lucide-react";
import { API_URL, getProjectId, getSessionToken, fetchApi } from "./api-client";

type JsonValue = unknown;
type Mode = "manual" | "import";
type ImportFormat = "csv" | "json" | "jsonl";
type CanonicalField =
  | "id"
  | "input"
  | "expected_output"
  | "variables"
  | "criteria"
  | "metadata"
  | "output_schema"
  | "expected_tools"
  | "expected_state"
  | "retrieval_context"
  | "messages";
type ManualCase = Record<CanonicalField, string>;
type Dataset = { id: string; name: string; description: string | null; current_version_id: string | null; created_at: string; updated_at: string };
type DatasetCase = { id: string; input: JsonValue; expected_output?: JsonValue; expected_tools?: JsonValue[]; expected_state?: JsonValue; metadata?: Record<string, unknown>; retrieval_context?: JsonValue[] };
type DatasetVersion = { id: string; dataset_id: string; version: number; cases: DatasetCase[]; metadata: Record<string, unknown>; created_at: string };
type ImportIssue = { line: number; reason: string };
type Preview = { cases: DatasetCase[]; issues: ImportIssue[] };
type RemoteTrigger = { id: string; project_id: string; dataset_id: string; trigger_url: string; enabled: boolean; signature_header: string; secret_mask: string; secret_key_id: string; created_at: string; updated_at: string };
type RemoteTriggerCreated = RemoteTrigger & { signing_secret: string };
type RemoteTriggerDelivery = { id: string; experiment_id: string; delivery_id: string; status: "pending" | "delivering" | "accepted" | "rejected" | "failed"; attempt_count: number; last_http_status: number | null; last_error_type: string | null; last_error_message: string | null; accepted_at: string | null; created_at: string; updated_at: string };

const emptyCase = (): ManualCase => ({
  id: `case-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
  input: "",
  expected_output: "",
  variables: "{}",
  criteria: "[]",
  metadata: "{}",
  output_schema: "",
  expected_tools: "[]",
  expected_state: "",
  retrieval_context: "[]",
  messages: "[]",
});

const emptyMapping = (): Record<CanonicalField, string> => ({
  id: "",
  input: "",
  expected_output: "",
  variables: "",
  criteria: "",
  metadata: "",
  output_schema: "",
  expected_tools: "",
  expected_state: "",
  retrieval_context: "",
  messages: "",
});

function headers(json = true): HeadersInit {
  return { ...(json ? { "Content-Type": "application/json" } : {}), "Authorization": `Bearer ${getSessionToken()}` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, { ...init, headers: { ...headers(), ...init?.headers } });
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new Error(typeof detail === "string" ? detail : typeof detail === "object" ? JSON.stringify(detail) : `请求失败（HTTP ${response.status}）`);
  }
  return body as T;
}

function parseJsonField(value: string, fallback: JsonValue = undefined, strict = false): JsonValue {
  if (!value.trim()) return fallback;
  try { return JSON.parse(value); } catch { if (strict) throw new Error("扩展字段必须是合法 JSON"); return value; }
}

function textValue(value: JsonValue): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  return JSON.stringify(value);
}

function encodeBase64(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary);
}

function guessFormat(fileName: string): ImportFormat {
  const extension = fileName.toLowerCase().split(".").pop();
  return extension === "csv" ? "csv" : extension === "json" ? "json" : "jsonl";
}

function csvHeader(text: string): string[] {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
  return firstLine.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map((field) => field.trim().replace(/^"|"$/g, "")).filter(Boolean);
}

function sourceFields(text: string, format: ImportFormat): string[] {
  try {
    if (format === "csv") return csvHeader(text);
    const parsed = format === "json" ? JSON.parse(text)[0] : JSON.parse(text.split(/\r?\n/).find(Boolean) ?? "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? Object.keys(parsed) : [];
  } catch { return []; }
}

function inferMapping(fields: string[]): Record<CanonicalField, string> {
  const find = (aliases: string[]) => fields.find((field) => aliases.includes(field)) ?? "";
  return {
    ...emptyMapping(),
    id: find(["id", "case_id", "case_key"]),
    input: find(["input", "question", "prompt"]),
    expected_output: find(["expected_output", "expected", "answer"]),
    variables: find(["variables", "vars"]),
    criteria: find(["criteria", "assertions"]),
    metadata: find(["metadata", "meta"]),
    output_schema: find(["output_schema", "schema"]),
    expected_tools: find(["expected_tools", "tools", "tool_calls"]),
    expected_state: find(["expected_state", "state"]),
    retrieval_context: find(["retrieval_context", "context", "contexts"]),
    messages: find(["messages", "chat_messages"]),
  };
}

function caseFromManual(row: ManualCase, index: number): Record<string, JsonValue> {
  return {
    id: row.id.trim() || `case-${index + 1}`,
    input: parseJsonField(row.input, ""),
    expected_output: parseJsonField(row.expected_output, undefined),
    variables: parseJsonField(row.variables, {}, true),
    criteria: parseJsonField(row.criteria, [], true),
    output_schema: parseJsonField(row.output_schema, undefined, true),
    expected_tools: parseJsonField(row.expected_tools, [], true),
    expected_state: parseJsonField(row.expected_state, undefined, true),
    retrieval_context: parseJsonField(row.retrieval_context, [], true),
    messages: parseJsonField(row.messages, [], true),
    metadata: parseJsonField(row.metadata, {}, true),
  };
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function DatasetsView() {
  const [mode, setMode] = useState<Mode>("manual");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [cases, setCases] = useState<ManualCase[]>([emptyCase()]);
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<ImportFormat>("jsonl");
  const [fileText, setFileText] = useState("");
  const [fields, setFields] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<CanonicalField, string>>(emptyMapping);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [pendingDatasetId, setPendingDatasetId] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "danger" | "neutral"; text: string } | null>(null);
  const mappedFields = useMemo(() => Object.fromEntries(Object.entries(mapping).filter(([, value]) => value)), [mapping]);
  const selectedDataset = datasets.find((item) => item.id === selectedDatasetId) ?? null;
  const selectedVersion = versions.find((item) => item.id === selectedVersionId) ?? null;

  async function loadCatalog(preferredDatasetId?: string, preferredVersionId?: string) {
    setLoadingCatalog(true);
    try {
      const nextDatasets = await requestJson<Dataset[]>(`/projects/${getProjectId()}/datasets`);
      const nextDatasetId = nextDatasets.some((item) => item.id === preferredDatasetId) ? preferredDatasetId! : nextDatasets[0]?.id ?? "";
      const nextVersions = nextDatasetId ? await requestJson<DatasetVersion[]>(`/projects/${getProjectId()}/datasets/${nextDatasetId}/versions`) : [];
      const nextVersionId = nextVersions.some((item) => item.id === preferredVersionId) ? preferredVersionId! : nextVersions.find((item) => item.id === nextDatasets.find((dataset) => dataset.id === nextDatasetId)?.current_version_id)?.id ?? nextVersions.at(-1)?.id ?? "";
      setDatasets(nextDatasets); setSelectedDatasetId(nextDatasetId); setVersions(nextVersions); setSelectedVersionId(nextVersionId);
    } catch (error) {
      setNotice({ tone: "danger", text: `加载 Dataset 目录失败：${error instanceof Error ? error.message : "未知错误"}` });
    } finally { setLoadingCatalog(false); }
  }

  async function selectDataset(datasetId: string) {
    setSelectedDatasetId(datasetId); setSelectedVersionId("");
    try {
      const nextVersions = await requestJson<DatasetVersion[]>(`/projects/${getProjectId()}/datasets/${datasetId}/versions`);
      const dataset = datasets.find((item) => item.id === datasetId);
      setVersions(nextVersions); setSelectedVersionId(nextVersions.find((item) => item.id === dataset?.current_version_id)?.id ?? nextVersions.at(-1)?.id ?? "");
    } catch (error) { setNotice({ tone: "danger", text: `加载 Dataset 版本失败：${error instanceof Error ? error.message : "未知错误"}` }); }
  }

  useEffect(() => { void loadCatalog(); }, []);

  function updateCase(index: number, field: keyof ManualCase, value: string) { setCases((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row)); }

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (!selected) return;
    if (selected.size > 5 * 1024 * 1024) { event.target.value = ""; setFile(null); setFileText(""); setFields([]); setPreview(null); setNotice({ tone: "danger", text: "文件不能超过 5 MB" }); return; }
    const nextFormat = guessFormat(selected.name);
    try {
      const text = await selected.text();
      const nextFields = sourceFields(text, nextFormat);
      setFile(selected); setFormat(nextFormat); setFileText(text); setFields(nextFields); setMapping(inferMapping(nextFields)); setPreview(null); setNotice(null);
    } catch (error) { setNotice({ tone: "danger", text: `读取文件失败：${error instanceof Error ? error.message : "未知错误"}` }); }
  }

  function changeImportFormat(nextFormat: ImportFormat) { setFormat(nextFormat); setPreview(null); const nextFields = fileText ? sourceFields(fileText, nextFormat) : []; setFields(nextFields); setMapping(inferMapping(nextFields)); }

  async function resetImport() {
    const datasetId = pendingDatasetId;
    setFile(null); setFileText(""); setFields([]); setPreview(null); setPendingDatasetId(null); setMapping(emptyMapping());
     if (datasetId) { try { await requestJson<void>(`/projects/${getProjectId()}/datasets/${datasetId}/imports/cancel`, { method: "POST" }); } catch { setNotice({ tone: "danger", text: "清理预览 Dataset 失败，请检查 API" }); } }
  }

  async function previewImport() {
    if (!fileText || !file) { setNotice({ tone: "danger", text: "请先选择 CSV、JSON 或 JSONL 文件" }); return; }
    if (!name.trim()) { setNotice({ tone: "danger", text: "请先填写 Dataset 名称" }); return; }
    if (!mapping.id || !mapping.input) { setNotice({ tone: "danger", text: "请映射必填字段：用例 ID 和输入" }); return; }
    setBusy(true); setNotice(null); let createdId: string | null = null;
    try {
      const dataset = await requestJson<Dataset>(`/projects/${getProjectId()}/datasets`, { method: "POST", body: JSON.stringify({ name: name.trim(), description, cases: [] }) });
      createdId = dataset.id; setPendingDatasetId(dataset.id);
      const result = await requestJson<Preview>(`/projects/${getProjectId()}/datasets/${dataset.id}/imports/preview`, { method: "POST", body: JSON.stringify({ format, content_base64: encodeBase64(fileText), field_mapping: mappedFields }) });
      setPreview(result); setNotice({ tone: result.issues.length ? "neutral" : "success", text: `预览完成：${result.cases.length} 条有效用例，${result.issues.length} 条问题` });
    } catch (error) {
      if (createdId) { try { await requestJson<void>(`/projects/${getProjectId()}/datasets/${createdId}`, { method: "DELETE", headers: headers(false) }); } catch { /* 保留原始错误 */ } }
      setPendingDatasetId(null); setNotice({ tone: "danger", text: `导入预览失败：${error instanceof Error ? error.message : "未知错误"}` });
    } finally { setBusy(false); }
  }

  async function commitImport() {
    if (!pendingDatasetId || !preview) return;
    setBusy(true); setNotice(null);
    try {
      const result = await requestJson<{ dataset_version: DatasetVersion; issues: ImportIssue[] }>(`/projects/${getProjectId()}/datasets/${pendingDatasetId}/imports/commit`, { method: "POST", body: JSON.stringify({ format, content_base64: encodeBase64(fileText), field_mapping: mappedFields, allow_partial: preview.issues.length > 0 }) });
      setNotice({ tone: "success", text: `导入成功，已创建 Dataset Version ${result.dataset_version.version}` });
      setPreview(null); setFile(null); setFileText(""); setPendingDatasetId(null); await loadCatalog(pendingDatasetId, result.dataset_version.id);
    } catch (error) { setNotice({ tone: "danger", text: `导入提交失败：${error instanceof Error ? error.message : "未知错误"}` }); } finally { setBusy(false); }
  }

  async function createManual() {
    if (!name.trim()) { setNotice({ tone: "danger", text: "请填写 Dataset 名称" }); return; }
    if (cases.some((row) => !row.id.trim() || !row.input.trim())) { setNotice({ tone: "danger", text: "每条用例都必须填写 ID 和输入" }); return; }
    setBusy(true); setNotice(null);
    try {
      const created = await requestJson<Dataset>(`/projects/${getProjectId()}/datasets`, { method: "POST", body: JSON.stringify({ name: name.trim(), description, cases: cases.map(caseFromManual) }) });
      setNotice({ tone: "success", text: `Dataset 已创建，包含 ${cases.length} 条用例` }); setCases([emptyCase()]); setName(""); setDescription(""); await loadCatalog(created.id, created.current_version_id ?? undefined);
    } catch (error) { setNotice({ tone: "danger", text: `Dataset 创建失败：${error instanceof Error ? error.message : "未知错误"}` }); } finally { setBusy(false); }
  }

  return <section className="datasets-workbench">
    <div className="resource-heading dataset-heading"><div><p className="eyebrow"><Database size={14} /> 评测输入</p><h1>数据集</h1><p>数据集是可复现的评测用例集合。每次编辑或导入都会生成新版本，历史实验继续使用自己的版本快照。</p></div><button type="button" className="primary" onClick={() => { setMode("manual"); setName(""); setDescription(""); setCases([emptyCase()]); setNotice(null); }}><Plus size={17} /> 新建数据集</button></div>
    <section className="dataset-builder panel"><div className="builder-top"><div><span className="section-kicker">新建 Dataset</span><strong>{mode === "manual" ? "手动添加评测用例" : "导入评测用例"}</strong></div><span className="version-badge">提交后生成新版本</span></div><div className="builder-body"><div className="field-grid two dataset-meta"><label className="field-label">Dataset 名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 support-smoke" /></label><label className="field-label">描述<input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明这组用例验证什么" /></label></div><div className="mode-tabs" role="tablist"><button type="button" className={mode === "manual" ? "active" : ""} onClick={() => { setMode("manual"); setNotice(null); }}><Table2 size={16} /> 手动填写</button><button type="button" className={mode === "import" ? "active" : ""} onClick={() => { setMode("import"); setNotice(null); }}><FileUp size={16} /> 导入文件</button></div>{mode === "manual" ? <ManualEditor cases={cases} updateCase={updateCase} addCase={() => setCases((current) => [...current, emptyCase()])} removeCase={(index) => setCases((current) => current.length === 1 ? current : current.filter((_, rowIndex) => rowIndex !== index))} /> : <ImportEditor file={file} format={format} fields={fields} mapping={mapping} setFormat={changeImportFormat} setMapping={setMapping} onFile={handleFile} preview={preview} onPreview={previewImport} onCommit={commitImport} onReset={() => void resetImport()} busy={busy} />}{notice && <div className={`dataset-notice ${notice.tone}`} role="status"><span>{notice.tone === "success" ? <Check size={16} /> : notice.tone === "danger" ? <AlertTriangle size={16} /> : <RefreshCw size={16} />}</span>{notice.text}</div>}{mode === "manual" && <div className="builder-actions"><span className="muted-helper">提交后会创建不可变 Dataset Version。</span><button type="button" className="primary" onClick={() => void createManual()} disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <Database size={16} />} {busy ? "创建中…" : "创建 Dataset"}</button></div>}</div></section>
    <DatasetCatalog loading={loadingCatalog} datasets={datasets} versions={versions} selectedDataset={selectedDataset} selectedVersion={selectedVersion} selectedDatasetId={selectedDatasetId} selectedVersionId={selectedVersionId} onDataset={selectDataset} onVersion={setSelectedVersionId} onRefresh={() => void loadCatalog(selectedDatasetId, selectedVersionId)} />
  </section>;
}

function ManualEditor({ cases, updateCase, addCase, removeCase }: { cases: ManualCase[]; updateCase: (index: number, field: keyof ManualCase, value: string) => void; addCase: () => void; removeCase: (index: number) => void }) {
  return <div className="manual-editor"><div className="table-toolbar"><div><strong>{cases.length} 条用例</strong><span>输入和可选的预期输出</span></div><button type="button" className="outline-button compact" onClick={addCase}><ListPlus size={15} /> 添加一行</button></div><div className="case-table-wrap"><table className="case-table"><thead><tr><th>ID</th><th>输入</th><th>预期输出 <em>可选</em></th><th>高级字段 <em>JSON</em></th><th>操作</th></tr></thead><tbody>{cases.map((row, index) => <tr key={row.id}><td><input value={row.id} onChange={(event) => updateCase(index, "id", event.target.value)} /></td><td><textarea value={row.input} onChange={(event) => updateCase(index, "input", event.target.value)} placeholder="问题、消息或 JSON 输入" rows={2} /></td><td><textarea value={row.expected_output} onChange={(event) => updateCase(index, "expected_output", event.target.value)} placeholder="参考答案或对象" rows={2} /></td><td className="case-extensions"><details><summary><ChevronDown size={14} /> 编辑扩展字段</summary><div className="case-extension-grid"><label>变量<textarea value={row.variables} onChange={(event) => updateCase(index, "variables", event.target.value)} placeholder='{"order_id":"42"}' rows={2} /></label><label>标准<textarea value={row.criteria} onChange={(event) => updateCase(index, "criteria", event.target.value)} placeholder='["must cite source"]' rows={2} /></label><label>输出结构<textarea value={row.output_schema} onChange={(event) => updateCase(index, "output_schema", event.target.value)} placeholder='{"type":"object"}' rows={2} /></label><label>预期 Tool 轨迹<textarea value={row.expected_tools} onChange={(event) => updateCase(index, "expected_tools", event.target.value)} placeholder='[{"name":"search_order"}]' rows={2} /></label><label>预期状态<textarea value={row.expected_state} onChange={(event) => updateCase(index, "expected_state", event.target.value)} placeholder='{"status":"shipped"}' rows={2} /></label><label>检索上下文<textarea value={row.retrieval_context} onChange={(event) => updateCase(index, "retrieval_context", event.target.value)} placeholder='[{"content":"..."}]' rows={2} /></label><label>消息列表<textarea value={row.messages} onChange={(event) => updateCase(index, "messages", event.target.value)} placeholder='[{"role":"user","content":"..."}]' rows={2} /></label><label>元数据<textarea value={row.metadata} onChange={(event) => updateCase(index, "metadata", event.target.value)} placeholder='{"category":"orders"}' rows={2} /></label></div></details></td><td><button type="button" className="icon-button" title="移除用例" aria-label="移除用例" onClick={() => removeCase(index)}><Trash2 size={15} /></button></td></tr>)}</tbody></table></div></div>;
}

function ImportEditor({ file, format, fields, mapping, setFormat, setMapping, onFile, preview, onPreview, onCommit, onReset, busy }: { file: File | null; format: ImportFormat; fields: string[]; mapping: Record<CanonicalField, string>; setFormat: (value: ImportFormat) => void; setMapping: (value: Record<CanonicalField, string>) => void; onFile: (event: ChangeEvent<HTMLInputElement>) => void; preview: Preview | null; onPreview: () => void; onCommit: () => void; onReset: () => void; busy: boolean }) {
  const canonicalFields: Array<{ key: CanonicalField; label: string; required?: boolean }> = [{ key: "id", label: "用例 ID", required: true }, { key: "input", label: "输入", required: true }, { key: "expected_output", label: "预期输出" }, { key: "variables", label: "变量" }, { key: "criteria", label: "评测标准" }, { key: "output_schema", label: "输出结构" }, { key: "expected_tools", label: "预期 Tool 轨迹" }, { key: "expected_state", label: "预期状态" }, { key: "retrieval_context", label: "检索上下文" }, { key: "messages", label: "消息列表" }, { key: "metadata", label: "元数据" }];
  return <div className="import-editor"><div className="drop-zone"><input id="dataset-file" type="file" accept=".csv,.json,.jsonl,text/csv,application/json" onChange={onFile} /><label htmlFor="dataset-file"><Upload size={22} /><strong>{file ? file.name : "选择 Dataset 文件"}</strong><span>支持 CSV、JSON 数组和 JSONL，最大 5 MB</span></label>{file && <button type="button" className="icon-button" title="清除文件" aria-label="清除文件" onClick={onReset}><X size={16} /></button>}</div><div className="import-controls"><label className="field-label">文件格式<select value={format} onChange={(event) => setFormat(event.target.value as ImportFormat)}><option value="csv">CSV</option><option value="jsonl">JSONL</option><option value="json">JSON 数组</option></select></label><div className="format-hint"><FileJson size={16} /><span>先映射源字段，再预览和确认导入。</span></div></div>{fields.length > 0 && <div className="mapping-grid"><div className="mapping-heading"><div><span className="section-kicker">字段映射</span><strong>告诉平台每一列的含义</strong></div><span>检测到 {fields.length} 个源字段</span></div>{canonicalFields.map(({ key, label, required }) => <label className="mapping-row" key={key}><span>{label}{required && <b>*</b>}</span><select value={mapping[key]} onChange={(event) => setMapping({ ...mapping, [key]: event.target.value })}><option value="">不映射</option>{fields.map((field) => <option value={field} key={field}>{field}</option>)}</select></label>)}<button type="button" className="outline-button" onClick={onPreview} disabled={busy || !file}>{busy ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />} 预览导入</button></div>}{preview && <ImportPreview preview={preview} onCommit={onCommit} busy={busy} />}</div>;
}

function ImportPreview({ preview, onCommit, busy }: { preview: Preview; onCommit: () => void; busy: boolean }) {
  return <div className="import-preview"><div className="preview-summary"><div><span className="section-kicker">校验预览</span><strong>{preview.cases.length} 条有效用例</strong></div><span className={preview.issues.length ? "issue-count" : "valid-count"}>{preview.issues.length ? `${preview.issues.length} 个问题` : "无问题"}</span></div>{preview.cases.length > 0 && <div className="preview-cases">{preview.cases.slice(0, 5).map((item) => <div className="preview-case" key={item.id}><span className="valid-mark"><Check size={13} /></span><div><strong>{item.id}</strong><span>{textValue(item.input).slice(0, 110)}</span></div></div>)}{preview.cases.length > 5 && <small>仅显示前 5 条用例</small>}</div>}{preview.issues.length > 0 && <div className="issue-list"><strong><AlertTriangle size={14} /> 需要处理的行</strong>{preview.issues.slice(0, 8).map((issue) => <div key={`${issue.line}-${issue.reason}`}><b>第 {issue.line} 行</b><span>{issue.reason}</span></div>)}</div>}<div className="preview-actions"><span>{preview.issues.length ? "确认后仅导入有效行；无效行会被跳过。" : "确认后会创建一个新的 Dataset Version。"}</span><button type="button" className="primary" onClick={onCommit} disabled={busy || preview.cases.length === 0}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />} 确认导入</button></div></div>;
}

function DatasetCatalog({ loading, datasets, versions, selectedDataset, selectedVersion, selectedDatasetId, selectedVersionId, onDataset, onVersion, onRefresh }: { loading: boolean; datasets: Dataset[]; versions: DatasetVersion[]; selectedDataset: Dataset | null; selectedVersion: DatasetVersion | null; selectedDatasetId: string; selectedVersionId: string; onDataset: (id: string) => void; onVersion: (id: string) => void; onRefresh: () => void }) {
  const pageSize = 8;
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [selectedVersionId]);
  const caseCount = selectedVersion?.cases.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(caseCount / pageSize));
  const visibleCases = selectedVersion?.cases.slice(page * pageSize, (page + 1) * pageSize) ?? [];
  return <section className="dataset-catalog panel"><div className="panel-heading"><div><p className="eyebrow"><Database size={14} /> 已有评测资产</p><h2>数据集版本</h2></div><button type="button" className="outline-button compact" onClick={onRefresh} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} /> 刷新</button></div>{loading ? <div className="panel-placeholder"><LoaderCircle className="spin" size={22} /><p>正在加载数据集目录…</p></div> : datasets.length === 0 ? <div className="panel-placeholder"><Database size={23} /><p>还没有数据集。创建一组用例后，版本会显示在这里。</p></div> : <div className="dataset-catalog-body"><aside className="dataset-list">{datasets.map((dataset) => <button type="button" key={dataset.id} className={`dataset-list-item ${dataset.id === selectedDatasetId ? "selected" : ""}`} onClick={() => onDataset(dataset.id)}><span className="resource-symbol"><Database size={15} /></span><span><strong>{dataset.name}</strong><small>{dataset.description || "无描述"}</small></span></button>)}</aside><div className="dataset-version-detail">{selectedDataset && <><div className="dataset-detail-heading"><div><span className="section-kicker">{selectedDataset.name}</span><strong>版本历史</strong></div><select value={selectedVersionId} onChange={(event) => onVersion(event.target.value)}>{versions.map((version) => <option key={version.id} value={version.id}>版本 {version.version} · {version.cases.length} 条用例</option>)}</select></div>{selectedVersion ? <><div className="version-meta"><span>版本 {selectedVersion.version}</span><span>创建于 {formatDate(selectedVersion.created_at)}</span><span>该版本只读，实验会固定它</span></div><div className="version-case-list">{visibleCases.map((item) => <div className="version-case-row" key={item.id}><strong>{item.id}</strong><span>{textValue(item.input).slice(0, 180)}</span></div>)}{selectedVersion.cases.length === 0 && <span className="muted-helper">这个版本暂时没有用例。</span>}</div>{caseCount > pageSize && <div className="editor-actions"><span className="muted-helper">第 {page + 1} / {pageCount} 页，共 {caseCount} 条</span><button type="button" className="icon-button" title="上一页" aria-label="用例上一页" disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}><ChevronLeft size={16} /></button><button type="button" className="icon-button" title="下一页" aria-label="用例下一页" disabled={page + 1 >= pageCount} onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}><ChevronRight size={16} /></button></div>}</> : <div className="panel-placeholder compact-placeholder"><p>该数据集没有可用版本。</p></div>}<RemoteTriggerPanel dataset={selectedDataset} /></>}</div></div>}</section>;
}

function RemoteTriggerPanel({ dataset }: { dataset: Dataset }) {
  const [trigger, setTrigger] = useState<RemoteTrigger | null>(null);
  const [deliveries, setDeliveries] = useState<RemoteTriggerDelivery[]>([]);
  const [triggerUrl, setTriggerUrl] = useState("");
  const [oneTimeSecret, setOneTimeSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function loadTrigger() {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetchApi(`${API_URL}/projects/${getProjectId()}/datasets/${dataset.id}/remote-trigger`, { headers: headers() });
      if (response.status === 404) { setTrigger(null); setDeliveries([]); return; }
      const body = await response.json() as RemoteTrigger;
      if (!response.ok) throw new Error("加载 Remote Trigger 失败。");
      setTrigger(body); setTriggerUrl(body.trigger_url);
      const deliveryRows = await requestJson<RemoteTriggerDelivery[]>(`/projects/${getProjectId()}/datasets/${dataset.id}/remote-trigger/deliveries`);
      setDeliveries(deliveryRows);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加载 Remote Trigger 失败。"); } finally { setBusy(false); }
  }

  useEffect(() => { setOneTimeSecret(""); void loadTrigger(); }, [dataset.id]);

  async function createTrigger() {
    if (!triggerUrl.trim()) { setMessage("请填写远程运行器接收 URL。"); return; }
    setBusy(true); setMessage("");
    try {
      const created = await requestJson<RemoteTriggerCreated>(`/projects/${getProjectId()}/datasets/${dataset.id}/remote-trigger`, { method: "POST", body: JSON.stringify({ trigger_url: triggerUrl.trim(), enabled: true }) });
      setTrigger(created); setOneTimeSecret(created.signing_secret); setDeliveries([]); setMessage("Remote Trigger 已创建。请立即将一次性签名密钥保存到运行器的 Secret Manager。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建 Remote Trigger 失败。"); } finally { setBusy(false); }
  }

  async function setEnabled(enabled: boolean) {
    if (!trigger) return;
    setBusy(true); setMessage("");
    try {
      const updated = await requestJson<RemoteTrigger>(`/projects/${getProjectId()}/datasets/${dataset.id}/remote-trigger`, { method: "PATCH", body: JSON.stringify({ enabled }) });
      setTrigger(updated);
    } catch (error) { setMessage(error instanceof Error ? error.message : "更新 Remote Trigger 失败。"); } finally { setBusy(false); }
  }

  async function copy(text: string, successMessage: string) {
    try { await navigator.clipboard.writeText(text); setMessage(successMessage); } catch { setMessage("浏览器拒绝了剪贴板操作，请手动选择内容。"); }
  }

  const receiverConfig = [
    `AGENT_EVAL_TRIGGER_URL=${trigger?.trigger_url ?? "<由运行器提供的 HTTPS URL>"}`,
    `AGENT_EVAL_TRIGGER_SIGNATURE_HEADER=${trigger?.signature_header ?? "X-Agent-Eval-Trigger-Signature"}`,
    "AGENT_EVAL_TRIGGER_SECRET=<创建 Trigger 后保存的一次性密钥>",
  ].join("\n");
  return <section className="remote-trigger-panel"><div className="dataset-trigger-heading"><div><span className="section-kicker"><Webhook size={14} /> Remote Trigger</span><strong>远程运行器</strong><small>仅在创建 Remote Trigger Experiment 时向该运行器发送一次签名通知，不会逐条调用 Agent。</small></div><button type="button" className="outline-button compact" onClick={() => { setOneTimeSecret(""); void loadTrigger(); }} disabled={busy}><RefreshCw size={14} className={busy ? "spin" : ""} /> 刷新</button></div>{message && <p className="trigger-message" role="status">{message}</p>}{!trigger ? <div className="trigger-form"><label className="field-label">远程运行器 URL<input aria-label="远程运行器 URL" value={triggerUrl} onChange={(event) => setTriggerUrl(event.target.value)} placeholder="https://runner.example.com/agent-eval/trigger" /></label><div className="editor-actions"><span className="muted-helper">URL 不能包含认证信息、查询参数或片段。</span><button type="button" className="outline-button" onClick={() => void createTrigger()} disabled={busy}><Webhook size={15} /> 创建 Trigger</button></div></div> : <><div className="trigger-meta"><div><span>接收 URL</span><strong>{trigger.trigger_url}</strong></div><div><span>签名头</span><strong>{trigger.signature_header}</strong></div><div><span>密钥</span><strong>{trigger.secret_mask}</strong></div><label className="check-line"><input type="checkbox" checked={trigger.enabled} onChange={(event) => void setEnabled(event.target.checked)} disabled={busy} /> 已启用</label></div>{oneTimeSecret && <div className="secret-once-notice"><strong>一次性签名密钥</strong><span>仅在本次创建后显示，刷新或离开页面后无法再次读取。</span><pre>{oneTimeSecret}</pre><button type="button" className="outline-button compact" onClick={() => void copy(oneTimeSecret, "一次性签名密钥已复制。")}><Clipboard size={14} /> 复制密钥</button></div>}<div className="detail-block"><h3>运行器配置模板</h3><pre className="integration-snippet">{receiverConfig}</pre><button type="button" className="outline-button compact" onClick={() => void copy(receiverConfig, "不含密钥的运行器配置已复制。")}><Clipboard size={14} /> 复制配置</button></div><div className="trigger-deliveries"><h3>最近投递</h3>{deliveries.length ? deliveries.slice(0, 5).map((delivery) => <div key={delivery.id}><strong>{delivery.status}</strong><span>{delivery.experiment_id} · 尝试 {delivery.attempt_count}{delivery.last_http_status ? ` · HTTP ${delivery.last_http_status}` : ""}</span></div>) : <p className="muted-helper">还没有投递。webhook 返回 2xx 只表示运行器已收到通知，不代表 Experiment 已完成。</p>}</div></>}</section>;
}
