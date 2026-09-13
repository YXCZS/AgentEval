# 托管 Prompt 能力迁移清单

本清单是 OpenSpec 迁移期间的历史审计记录。托管 Prompt Agent 已从当前产品路径移除；保留本文是为了说明迁移范围，不代表这些能力仍可用，也不是待执行的产品计划。

## 迁移目标

产品收敛为面向已运行 RAG、Tool 和 Custom Agent 的 Trace-first 可观测性、离线评测与回归诊断平台。平台不再托管 Prompt、调用用户的模型供应商，或保存模型供应商密钥。

## 已核验的遗留面

| 类别 | 已核验位置 | 当前作用 | 决定 |
| --- | --- | --- | --- |
| API 合同与类型 | `apps/api/agent_eval_api/contracts/models.py`、`contracts/__init__.py`、`packages/contracts/index.ts`、`packages/contracts/openapi.json` | 历史 `PromptConfig`/Prompt 类型曾存在 | 已删除或迁移；当前合同只接受 RAG、Tool、Custom Agent Release。 |
| Agent API | `apps/api/agent_eval_api/agents.py` | 历史上创建、测试和版本化 Prompt Agent | 当前只提供 endpoint-independent Release 注册；真实 Agent 在用户 SDK 进程中运行。 |
| 平台模型执行器 | `apps/api/agent_eval_api/runner/prompt.py`、`runner/__init__.py` | 历史平台托管 Prompt 执行器 | 已删除；当前平台不在生产路径执行 Prompt 或业务 Agent。 |
| Worker 执行分支 | `apps/worker/agent_eval_worker/execution.py` | 历史 Worker-owned Agent 执行器 | 不再由生产 Celery app 注册；MVP Agent 执行由 SDK 负责。 |
| 持久化与初始迁移 | `apps/api/agent_eval_api/db/models.py`、`migrations/versions/ffe07a933165_initial_evaluation_workbench_schema.py` | `agent_versions.prompt_config` 字段以及允许其被快照 | 在任务 1.2/1.3 先备份、演练和迁移旧记录；任务 8.1 再移除字段或将其隔离到历史迁移记录。 |
| 运行快照 | `apps/api/agent_eval_api/evaluation_runs.py` | 历史上快照 Prompt 配置 | 当前快照冻结 Dataset Version、Agent Release、Evaluator Version 和 SDK 执行参数。 |
| Prompt 专属评分 | `apps/api/agent_eval_api/evaluation/prompt_metrics.py`、`evaluation/scoring.py`、`evaluation/__init__.py`、`evaluation/deterministic.py` | Prompt 词法、语义等评分及 `prompt_` 指标 | 在任务 5.3 以 RAG/Tool/Custom 确定性评分替代；在任务 8.2 删除 Prompt 专属模块和注册。 |
| 可选第三方适配器 | `apps/api/agent_eval_api/evaluation/adapters.py`、`evaluation/future_adapters.py` | 包含 Promptfoo 名称或 `AgentType.PROMPT` 支持范围 | Promptfoo 不是平台 Prompt Runner。任务 8.1 仅移除 `prompt` Agent 类型声明；任务 5.4 之前不得把未实现的第三方适配器宣传为可用。 |
| 全局模型/嵌入式密钥设置 | `.env.example`、`apps/api/agent_eval_api/settings.py`、`evaluation/judge.py`、`evaluation/prompt_metrics.py`、`evaluation/scoring.py` | `LLM_API_KEY`、`LLM_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`，用于平台侧 Judge 或嵌入调用 | 在任务 5.4 改为用户自管外部 Judge HTTP Connection；任务 8.3 从环境模板和 Settings 中移除模型供应商密钥。基础设施密钥和 Project API key 保留。 |
| Web Agent 配置 | `apps/web/app/agents-view.tsx`、`apps/web/app/globals.css` | Prompt 类型、模板/模型/temperature 表单、渲染预览和连接测试 | 在任务 7.4 改为外部 Agent Connection/Release 页面；任务 8.2 删除 Prompt 表单、预览和样式。 |
| Web 工作台和运行页 | `apps/web/app/page.tsx`、`layout.tsx`、`runs-view.tsx`、`evaluators-view.tsx` | Prompt 文案、演示数据、`prompt` 类型筛选 | 在任务 7.1/7.2 重构为中文 Trace-first 工作台并由真实 API 数据驱动；任务 8.2 清除遗留文案和默认值。 |
| 前端浏览器测试 | `apps/web/e2e/button-workflow.spec.ts`、`apps/web/e2e/regression-flow.spec.ts` | Prompt Agent fixture 与 CSV `prompt` 字段 | 在任务 9.2 改为 RAG、Tool、Custom 外部 Agent fixture，并覆盖 Trace-first 主流程。 |
| 后端测试 | `tests/integration/test_agents.py`、`test_annotations.py`、`test_evaluation_runs.py`、`test_regression_flow.py`、`tests/unit/test_contracts.py`、`test_evaluator_adapters.py`、`test_future_adapters.py`、`test_llm_judge.py`、`test_prompt_metrics.py`、`test_prompt_runner.py`、`test_example_agents.py` | Prompt 合同、Runner、指标和端到端 fixture | 在任务 2-5 与 9.1 用外部 Agent、外部 Judge、Trace 和确定性评测的覆盖替代；删除仅验证 Prompt Runner 的测试。 |
| 示例和 Compose 服务 | `examples/prompt_agent/`、`infra/docker-compose.yml` | 示例 Prompt Agent 和 `prompt-agent` 容器 | 在任务 8.2 以 Tool/RAG/Custom 外部 Agent 示例替换并删除该服务。 |
| 产品文档和元数据 | `README.md`、`docs/architecture.md`、`docs/usage.md`、`pyproject.toml`、`apps/api/agent_eval_api/main.py` | 产品定位、流程图和说明仍将 Prompt 作为一等 Agent | 在任务 9.4/9.5 统一改为 Trace-first 定位、外部 Agent 接入和双质量闭环。 |

## 明确保留的语义

下列引用出现了 `prompt`，但不是托管 Prompt 产品功能，不能因为名称相同而删除：

| 位置 | 保留原因 | 后续处理 |
| --- | --- | --- |
| `apps/api/agent_eval_api/trace_normalization.py` 中的 `TraceSpanKind.PROMPT` | OpenInference/OTLP Trace 可能记录模板渲染步骤；这是外部 Agent 的观测证据。 | 保留 Span 语义，后续补充规范化和测试。 |
| `apps/web/app/datasets-view.tsx` 中 CSV 列名候选 `prompt` | 用户的测试集可能把输入列命名为 `prompt`。 | 保留为 CSV 输入字段映射别名，但 UI 不宣传 Prompt Agent。 |
| `PromptfooAdapter` 与未来能力目录中的 Promptfoo 名称 | 第三方工具名称，不代表本平台调用模型或托管 Prompt。 | 去除 `prompt` Agent 支持范围；未真正实现的适配器继续标注为未来能力。 |
| 已归档的 OpenSpec 历史文件 | 历史决策证据。 | 不重写归档；新文档只描述当前产品边界。 |

## 核验记录

2026-09-09 已执行并人工复核以下检索：

```powershell
rg -n "PromptRunner|PromptConfig|prompt" apps packages docs
rg -n --glob '!node_modules/**' --glob '!.git/**' --glob '!.local-run/**' --glob '!.firecrawl/**' --glob '!*.lock' `
  'prompt_config|agent_type.*prompt|AgentType\\.PROMPT|PromptRunner|run_prompt|prompt-agent|prompt_agent' `
  apps packages docs examples infra tests migrations scripts README.md pyproject.toml .env.example
```

复核结论：上述“已核验的遗留面”覆盖了检索到的产品代码、合同、迁移、前后端测试、示例和文档；OpenSpec 当前变更与历史归档中出现的文本属于计划/历史，不是待删除的运行时产品能力。
