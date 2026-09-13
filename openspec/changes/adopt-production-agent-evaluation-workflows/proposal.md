## Why

当前平台的数据库、队列和 HTTP 链路是真实的，但正式用户流程仍以平台 Worker 调用仓库内确定性 `/run` 示例为中心，无法证明系统评测了真实模型驱动的业务 Agent。项目需要采用 Phoenix 和 Langfuse 已验证的真实接入模式，让 Agent 留在用户自己的运行环境，通过 SDK、OpenTelemetry/OpenInference 或受保护的远程触发接入，并把真实执行证据作为实验、评分和发布结论的前提。

## What Changes

- **BREAKING** 删除正式运行环境、Compose、前端和文档中的确定性示例 Agent、故意回归参数、Seed Demo、预制 Trace/Score/Comparison，以及任何模型失败后的 mock 或规则回退；确定性输入仅可作为 `tests/fixtures` 中的平台自动化测试数据。
- 将真实 Agent 接入扩展为三条主流入口：Python SDK task experiment、OpenTelemetry/OpenInference instrumented experiment、Remote Runtime result ingestion；现有 HTTP `/run` 降级为可选的签名 Remote Trigger Adapter。
- 提供 Python SDK：拉取版本化 Dataset，在用户进程中并发执行真实 `task`/Agent，隔离单 Case 错误，关联 Trace，上传结果、Score 和运行元数据。
- 扩展 OTLP/OpenInference 协议，使已运行的真实 Agent 能通过标准 Span 属性关联 Project、Dataset Version、Experiment 和 Case，而不要求部署特定 `/run` 服务。
- 支持远程运行环境按 `case_id/example_id` 上传已完成结果、Trace 和可选 Score，覆盖 Node/Java、CI、Notebook、沙箱和已有微服务。
- 新增平台管理的 LLM Provider Connection，用于真实 LLM-as-a-Judge；支持 OpenAI-compatible 起步，凭据加密保存、仅写入不回显、可测试与轮换。外部 HTTP Judge 保留为扩展适配器，不再是唯一 Judge 方式。
- 每个正式 Experiment 必须声明执行来源、Agent Release、Dataset Version 和证据要求；需要模型/Agent Trace 的运行在缺少真实 LLM Span、模型、usage 或关联标识时标记为 `INCOMPLETE`，不得产生通过结论。
- 前端改为真实接入向导、Dataset/Experiment 工作流和证据页面，不展示自动生成的成功数据；空项目展示可复制的 SDK、OTLP 和 Remote Runtime 配置。
- `.env.example` 只预留 Provider、平台加密主密钥和服务配置变量；真实 API Key 仅允许进入未跟踪的 `.env`、容器 secret 或加密连接存储。
- 最终验收必须使用用户提供的真实 Provider Key 或可访问的真实 OpenAI-compatible 服务，运行至少一个真实 Tool Agent 与一个真实 RAG Agent，生成可追溯 Trace、评测、版本比较和 Gate；没有真实调用时任务保持未完成。

## Delivery Strategy

- 采用可独立验收的增量交付，不再同时铺开全部入口和高级能力；每完成一个功能都先通过合同、数据库和用户流程验证，再进入下一项。
- MVP 只把 Python SDK Task 作为正式执行入口，但 SDK 的 `task` 保持 Agent 框架与模型供应商无关，能够包装真实 Tool、RAG 或 Custom Agent。MVP 必须打通 Dataset → Release → Experiment → Item → Trace → 客观 Score → baseline/candidate Comparison → Gate 的单机真实链路。
- MVP 使用真实 Agent 输出和确定性业务评估器，不把平台托管 LLM Judge 作为首个闭环的阻塞项；任何运行时 mock、预制答案、Seed 成功数据和模型失败后的规则回退仍然禁止，并须在 MVP 对外完成前从生产路径删除。
- V1.1 在 MVP 上增加真实 RAG 验收、加密 Provider Connection 和平台托管 LLM-as-a-Judge；V1.2 增加独立 OTel/OpenInference、语言无关 Remote Upload 和签名 Remote Trigger；V1.3 完成高级工作台、人工评审和全面交互质量。
- 最终项目范围和真实 Tool + RAG 验收标准保持不变；阶段划分只改变实现顺序，不允许用“后续版本”降低已经声明的安全、证据完整性或真实性要求。

## Capabilities

### New Capabilities

- `production-agent-integration`: 真实业务 Agent 通过 SDK、OpenTelemetry/OpenInference、Remote Result Upload 或可选签名 Trigger 接入，且禁止运行时 mock 回退。
- `client-orchestrated-experiments`: SDK 在用户运行时执行版本化 Dataset 和真实 Agent task，并将 Trace、结果、Score 与 Experiment 可靠关联。
- `managed-evaluation-providers`: 平台安全管理真实模型连接并执行 LLM-as-a-Judge，同时保留外部 Judge 扩展协议。
- `production-evidence-workbench`: 前端、报告和 Gate 只依据真实持久化数据与可验证执行证据，明确阻止不完整或伪造证据通过。

### Modified Capabilities

None. The repository has no archived main capability specifications for the active Trace-first change, so this change defines replacement production contracts as new capabilities.

## Impact

- 重构 `apps/api` 的 Experiment、Trace、Provider Connection 和结果接收合同，新增安全凭据存储与远程运行幂等协议。
- 重构 `apps/worker`：保留异步服务端评测和 Gate，移除“Worker 必须调用 Agent `/run`”的唯一执行假设。
- 新增可发布的 Python SDK 包及 SDK 合同测试；补充 OpenTelemetry/OpenInference Experiment 属性。
- 重构 `apps/web` 的接入、实验创建、证据状态和 Provider 设置页面。
- 删除 `examples/*_agent`、`seed_regression_demo.py`、Compose Demo 服务及对应正式文档；测试替身只保留在 `tests/`。
- 新增数据库迁移、凭据加密主密钥配置、真实 Provider 验收脚本和仓库密钥扫描。
