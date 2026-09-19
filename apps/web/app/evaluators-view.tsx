"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Check,
  CircleAlert,
  CircleCheck,
  CircleOff,
  FlaskConical,
  Plus,
  Save,
} from "lucide-react";
import { API_URL, getProjectId, getSessionToken, fetchApi } from "./api-client";

type EvaluatorType = "deterministic" | "llm_judge" | "adapter" | "human";
type AgentType = "rag" | "tool" | "custom";
type Direction = "higher_is_better" | "lower_is_better";
type Evaluator = {
  id: string;
  name: string;
  version: string;
  evaluator_type: EvaluatorType;
  requires: string[];
  supported_agent_types: AgentType[];
  score_min: number | null;
  score_max: number | null;
  direction: Direction;
  default_threshold: number | null;
  rubric: string | null;
  evaluator_connection_id: string | null;
  provider_connection_id: string | null;
  judge_model: string | null;
  prompt_template: string | null;
  output_schema: Record<string, unknown> | null;
  sampling_parameters: {
    temperature: number;
    top_p: number;
    max_tokens: number;
    seed: number | null;
  } | null;
  config: Record<string, unknown>;
  enabled: boolean;
};
type ProviderConnection = {
  id: string;
  name: string;
  model: string;
  enabled: boolean;
  status: "pending_validation" | "active" | "error" | "disabled";
};

const evaluatorTypeLabels: Record<EvaluatorType, string> = {
  deterministic: "确定性规则",
  llm_judge: "LLM Judge",
  adapter: "第三方适配器",
  human: "人工评审",
};
const agentTypeLabels: Record<AgentType, string> = {
  rag: "RAG",
  tool: "Tool",
  custom: "Custom",
};

function headers(): HeadersInit {
  return { "Content-Type": "application/json", "Authorization": `Bearer ${getSessionToken()}` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers(), ...init?.headers },
  });
  const body = (await response.json().catch(() => null)) as
    | T
    | { detail?: unknown }
    | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)).join("; "));
    }
    throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
  }
  return body as T;
}

function nextVersion(version: string): string {
  const match = version.match(/^(.*?)(\d+)$/);
  return match ? `${match[1]}${Number(match[2]) + 1}` : `${version}-next`;
}

function formatConfig(config: Record<string, unknown>): string {
  return JSON.stringify(config, null, 2);
}

function parseOptionalNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

export function EvaluatorsView() {
  const [evaluators, setEvaluators] = useState<Evaluator[]>([]);
  const [providers, setProviders] = useState<ProviderConnection[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [evaluatorType, setEvaluatorType] = useState<EvaluatorType>("deterministic");
  const [supportedTypes, setSupportedTypes] = useState<AgentType[]>(["rag"]);
  const [requires, setRequires] = useState("");
  const [scoreMin, setScoreMin] = useState("0");
  const [scoreMax, setScoreMax] = useState("1");
  const [threshold, setThreshold] = useState("1");
  const [direction, setDirection] = useState<Direction>("higher_is_better");
  const [rubric, setRubric] = useState("");
  const [configText, setConfigText] = useState("{}");
  const [providerConnectionId, setProviderConnectionId] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [promptTemplate, setPromptTemplate] = useState("请根据 rubric 对输入、预期结果、实际输出和 Trace 进行评分。只返回符合 JSON Schema 的结果。");
  const [outputSchemaText, setOutputSchemaText] = useState('{"type":"object","properties":{"score":{"type":"number"},"explanation":{"type":"string"}},"required":["score","explanation"],"additionalProperties":false}');
  const [temperature, setTemperature] = useState("0");
  const [topP, setTopP] = useState("1");
  const [maxTokens, setMaxTokens] = useState("1000");
  const selected = evaluators.find((item) => item.id === selectedId) ?? null;

  const grouped = useMemo(() => {
    return evaluators.reduce<Record<string, Evaluator[]>>((groups, evaluator) => {
      (groups[evaluator.name] ??= []).push(evaluator);
      return groups;
    }, {});
  }, [evaluators]);

  async function loadEvaluators(selectId?: string) {
    setLoading(true);
    try {
      const [loaded, providerRows] = await Promise.all([
        requestJson<Evaluator[]>(`/projects/${getProjectId()}/evaluators`),
        requestJson<ProviderConnection[]>(`/projects/${getProjectId()}/provider-connections`),
      ]);
      setEvaluators(loaded);
      setProviders(providerRows.filter((provider) => provider.enabled && provider.status === "active"));
      const next = loaded.find((item) => item.id === selectId) ?? loaded[0];
      setSelectedId(next?.id ?? "");
      setNotice(null);
    } catch (error) {
      setNotice(`加载评估器失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadEvaluators();
  }, []);

  function resetForm(item?: Evaluator) {
    setFormOpen(true);
    setName(item?.name ?? "");
    setVersion(item ? nextVersion(item.version) : "1.0.0");
    setEvaluatorType(item?.evaluator_type ?? "deterministic");
    setSupportedTypes(item?.supported_agent_types ?? ["rag"]);
    setRequires(item?.requires.join(", ") ?? "");
    setScoreMin(item?.score_min === null || item?.score_min === undefined ? "" : String(item.score_min));
    setScoreMax(item?.score_max === null || item?.score_max === undefined ? "" : String(item.score_max));
    setThreshold(item?.default_threshold === null || item?.default_threshold === undefined ? "" : String(item.default_threshold));
    setDirection(item?.direction ?? "higher_is_better");
    setRubric(item?.rubric ?? "");
    setConfigText(item ? formatConfig(item.config) : "{}");
    setProviderConnectionId(item?.provider_connection_id ?? "");
    setJudgeModel(item?.judge_model ?? "");
    setPromptTemplate(item?.prompt_template ?? "请根据 rubric 对输入、预期结果、实际输出和 Trace 进行评分。只返回符合 JSON Schema 的结果。");
    setOutputSchemaText(item?.output_schema ? formatConfig(item.output_schema) : '{"type":"object","properties":{"score":{"type":"number"},"explanation":{"type":"string"}},"required":["score","explanation"],"additionalProperties":false}');
    setTemperature(String(item?.sampling_parameters?.temperature ?? 0));
    setTopP(String(item?.sampling_parameters?.top_p ?? 1));
    setMaxTokens(String(item?.sampling_parameters?.max_tokens ?? 1000));
    setNotice(null);
  }

  function toggleAgentType(type: AgentType) {
    setSupportedTypes((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type]);
  }

  async function createEvaluator() {
    if (!name.trim()) {
      setNotice("请填写评估器名称。");
      return;
    }
    if (supportedTypes.length === 0) {
      setNotice("至少选择一种支持的 Agent 类型。");
      return;
    }
    let config: Record<string, unknown>;
    let outputSchema: Record<string, unknown> | null = null;
    try {
      const parsed: unknown = JSON.parse(configText);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("配置必须是 JSON 对象");
      config = parsed as Record<string, unknown>;
    } catch (error) {
      setNotice(`配置 JSON 无效：${error instanceof Error ? error.message : "无法解析"}`);
      return;
    }
    if (evaluatorType === "llm_judge") {
      if (!providerConnectionId || !judgeModel.trim() || !rubric.trim() || !promptTemplate.trim()) {
        setNotice("LLM Judge 需要 Provider、模型、评分标准和提示词模板。");
        return;
      }
      try {
        outputSchema = parseJsonObject(outputSchemaText);
      } catch (error) {
        setNotice(`Judge 输出 Schema 无效：${error instanceof Error ? error.message : "无法解析"}`);
        return;
      }
    }
    setBusy(true);
    setNotice(null);
    try {
      const created = await requestJson<Evaluator>(`/projects/${getProjectId()}/evaluators`, {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          version: version.trim(),
          evaluator_type: evaluatorType,
          requires: requires.split(",").map((item) => item.trim()).filter(Boolean),
          supported_agent_types: supportedTypes,
          score_min: parseOptionalNumber(scoreMin),
          score_max: parseOptionalNumber(scoreMax),
          direction,
          default_threshold: parseOptionalNumber(threshold),
          rubric: rubric.trim() || null,
          ...(evaluatorType === "llm_judge" ? {
            provider_connection_id: providerConnectionId,
            judge_model: judgeModel.trim(),
            prompt_template: promptTemplate.trim(),
            output_schema: outputSchema,
            sampling_parameters: {
              temperature: Number(temperature),
              top_p: Number(topP),
              max_tokens: Number(maxTokens),
            },
          } : {}),
          config,
        }),
      });
      await loadEvaluators(created.id);
      setFormOpen(false);
      setNotice(`评估器 ${created.name} ${created.version} 已创建。`);
    } catch (error) {
      setNotice(`创建失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(item: Evaluator) {
    setBusy(true);
    setNotice(null);
    try {
      const updated = await requestJson<Evaluator>(`/projects/${getProjectId()}/evaluators/${item.id}/enabled?enabled=${!item.enabled}`, { method: "PATCH" });
      setEvaluators((current) => current.map((candidate) => candidate.id === updated.id ? updated : candidate));
      setNotice(`${updated.name} ${updated.version} 已${updated.enabled ? "启用" : "停用"}。`);
    } catch (error) {
      setNotice(`状态更新失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="resource-view evaluator-workbench">
      <div className="resource-heading">
        <div>
          <p className="eyebrow"><FlaskConical size={14} /> 评分定义</p>
          <h1>评估器</h1>
          <p>每个评估器都是一个可复用的评分规则。版本创建后不可修改，只能创建新版本，保证历史报告可复现。</p>
        </div>
        <button className="primary" onClick={() => resetForm()}><Plus size={17} /> 新建评估器</button>
      </div>
      {notice && <div className="inline-notice" role="status"><CircleAlert size={16} /> {notice}</div>}
      <div className="evaluator-layout">
        <aside className="panel evaluator-catalog">
          <div className="panel-heading"><div><p className="eyebrow">已注册规则</p><h2>{evaluators.length} 个版本</h2></div><FlaskConical size={16} className="muted-icon" /></div>
          {loading ? <p className="panel-placeholder">正在加载评估器...</p> : Object.keys(grouped).length === 0 ? <div className="panel-placeholder">还没有评估器，请先创建一个。</div> : <div className="evaluator-list">{Object.entries(grouped).map(([groupName, versions]) => <div key={groupName} className="evaluator-group"><strong>{groupName}</strong>{versions.map((item) => <button key={item.id} className={`evaluator-list-item ${item.id === selectedId ? "selected" : ""}`} onClick={() => { setSelectedId(item.id); setFormOpen(false); setNotice(null); }}><span><b>{item.version}</b><small>{evaluatorTypeLabels[item.evaluator_type]}</small></span><i className={item.enabled ? "enabled" : "disabled"}>{item.enabled ? "启用" : "停用"}</i></button>)}</div>)}</div>}
        </aside>
        <div className="panel evaluator-detail">
          {formOpen ? <>
            <div className="panel-heading"><div><p className="eyebrow">新版本</p><h2>{name || "新建评估器"}</h2></div><Save size={16} className="muted-icon" /></div>
            <div className="evaluator-form">
              <div className="field-grid two"><label className="field-label">名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 task_success" /></label><label className="field-label">版本<input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="例如 1.0.0" /></label></div>
              <div className="field-grid two"><label className="field-label">评估器类型<select value={evaluatorType} onChange={(event) => setEvaluatorType(event.target.value as EvaluatorType)}>{Object.entries(evaluatorTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field-label">方向<select value={direction} onChange={(event) => setDirection(event.target.value as Direction)}><option value="higher_is_better">分数越高越好</option><option value="lower_is_better">分数越低越好</option></select></label></div>
              <fieldset className="check-field"><legend>支持的 Agent 类型</legend><div className="check-grid">{(Object.keys(agentTypeLabels) as AgentType[]).map((type) => <label key={type}><input type="checkbox" checked={supportedTypes.includes(type)} onChange={() => toggleAgentType(type)} /><span>{supportedTypes.includes(type) ? <Check size={13} /> : null}</span>{agentTypeLabels[type]}</label>)}</div></fieldset>
              <label className="field-label">依赖字段<input value={requires} onChange={(event) => setRequires(event.target.value)} placeholder="用逗号分隔，例如 expected_output, expected_state" /><small>运行前会检查测试用例是否提供这些字段。</small></label>
              <div className="field-grid three"><label className="field-label">最低分<input type="number" value={scoreMin} onChange={(event) => setScoreMin(event.target.value)} /></label><label className="field-label">最高分<input type="number" value={scoreMax} onChange={(event) => setScoreMax(event.target.value)} /></label><label className="field-label">默认阈值<input type="number" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label></div>
              <label className="field-label">评分标准{evaluatorType === "llm_judge" ? "（必填）" : "（可选）"}<textarea value={rubric} onChange={(event) => setRubric(event.target.value)} rows={3} placeholder="描述 LLM Judge 或人工评审的评分规则" /></label>
              {evaluatorType === "llm_judge" && <div className="judge-binding-form"><div className="config-section-title"><FlaskConical size={15} /><strong>平台托管 Judge</strong><span>只选择已启用且已验证的 Provider</span></div>{providers.length === 0 ? <div className="inline-notice danger" role="status"><CircleAlert size={15} />没有可用 Provider。请先到“Release 与接入”完成 Provider 测试和加密保存。</div> : <div className="field-grid two"><label className="field-label">Provider 连接<select aria-label="Judge Provider 连接" value={providerConnectionId} onChange={(event) => setProviderConnectionId(event.target.value)} required><option value="">请选择已验证的 Provider</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name} · {provider.model}</option>)}</select></label><label className="field-label">Judge 模型<input aria-label="Judge 模型" value={judgeModel} onChange={(event) => setJudgeModel(event.target.value)} placeholder="例如 deepseek-chat" required /></label></div>}<label className="field-label">Judge 提示词模板<textarea aria-label="Judge 提示词模板" value={promptTemplate} onChange={(event) => setPromptTemplate(event.target.value)} rows={3} required /></label><label className="field-label">Judge 输出 JSON Schema<textarea aria-label="Judge 输出 JSON Schema" value={outputSchemaText} onChange={(event) => setOutputSchemaText(event.target.value)} rows={5} required /></label><div className="field-grid three"><label className="field-label">Temperature<input aria-label="Judge Temperature" type="number" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} required /></label><label className="field-label">Top P<input aria-label="Judge Top P" type="number" min="0.01" max="1" step="0.01" value={topP} onChange={(event) => setTopP(event.target.value)} required /></label><label className="field-label">最大输出 Token<input aria-label="Judge 最大输出 Token" type="number" min="1" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} required /></label></div></div>}
              <div className="field-grid two"><label className="field-label">配置 JSON<textarea value={configText} onChange={(event) => setConfigText(event.target.value)} rows={3} /></label></div>
              <div className="editor-actions"><button className="outline-button" onClick={() => setFormOpen(false)}>取消</button><button className="primary" onClick={() => void createEvaluator()} disabled={busy}><Save size={16} /> 创建版本</button></div>
            </div>
          </> : selected ? <>
            <div className="panel-heading"><div><p className="eyebrow">评估器详情</p><h2>{selected.name} <span className="version-badge">{selected.version}</span></h2></div><button className="outline-button compact" onClick={() => resetForm(selected)}><Plus size={15} /> 新建版本</button></div>
            <div className="evaluator-detail-body"><div className="detail-stat-grid"><div><span>类型</span><strong>{evaluatorTypeLabels[selected.evaluator_type]}</strong></div><div><span>评分方向</span><strong>{selected.direction === "higher_is_better" ? "越高越好" : "越低越好"}</strong></div><div><span>默认阈值</span><strong>{selected.default_threshold ?? "未设置"}</strong></div><div><span>状态</span><strong className={selected.enabled ? "text-success" : "text-muted"}>{selected.enabled ? "已启用" : "已停用"}</strong></div></div><div className="detail-block"><h3>支持的 Agent</h3><div className="tag-list">{selected.supported_agent_types.map((type) => <span key={type}>{agentTypeLabels[type]}</span>)}</div></div><div className="detail-block"><h3>运行依赖</h3><p className="detail-muted">{selected.requires.length ? selected.requires.join("、") : "无需额外字段"}</p></div>{selected.provider_connection_id && <div className="detail-block"><h3>绑定的 Provider Judge</h3><p className="detail-muted">{providers.find((provider) => provider.id === selected.provider_connection_id)?.name ?? selected.provider_connection_id} · {selected.judge_model}</p></div>}{selected.rubric && <div className="detail-block"><h3>评审配置</h3><p className="rubric-text">{selected.rubric}</p></div>}<div className="detail-block"><h3>原始配置</h3><pre>{formatConfig(selected.config)}</pre></div><button className="outline-button" onClick={() => void toggleEnabled(selected)} disabled={busy}>{selected.enabled ? <><CircleOff size={16} /> 停用此版本</> : <><CircleCheck size={16} /> 启用此版本</>}</button></div>
          </> : <div className="panel-placeholder"><FlaskConical size={24} /><p>选择一个评估器查看详情，或创建第一个评估器。</p></div>}
        </div>
      </div>
    </section>
  );
}
