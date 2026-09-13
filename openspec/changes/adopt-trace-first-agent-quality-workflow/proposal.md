## Why

当前工作台把平台托管的 Prompt Runner 与 HTTP Agent 放在同一条主流程中，容易让产品看起来像一个 Prompt 测试器，而不是 Agent 质量平台。Phoenix、Langfuse 和 TrajectIQ 的现行资料显示，更稳定的共同主线是：先从用户实际运行的 Agent Trace 获得可观测证据，再将线上样本沉淀为版本化 Dataset，运行可复现 Experiment，并在版本变化时执行回归诊断和发布门禁。

本次变更将产品收敛到这条 Trace-first 闭环，删除 Prompt Agent 的产品定位与后续实现计划，使项目的边界、优势和面试叙事可被清楚验证。

## What Changes

- **BREAKING** 移除 Prompt Agent、Prompt Runner、模型供应商配置、平台主动调用大模型和 Prompt Playground 作为产品能力；平台不再替用户执行 Agent 或保存模型密钥。
- 将产品定位改为：面向已运行 RAG、Tool 和 Custom Agent 的 Trace-first 可观测性、离线评测与回归诊断平台。
- 采用 Phoenix/Langfuse 的双闭环：线上 Trace 接入、查询、人工标注与在线 Score；离线 Dataset、Experiment、Evaluator 与结果比较。Trace 或 Observation 可经过字段映射转为 Dataset Case。
- 采用 Phoenix 的 Experiment 模型：一次 Experiment 固定 Dataset Version、被测 Agent Version/Release、Task Connection 和 Evaluator Set；同一 Dataset Version 上的结果才可比较。
- 采用 Langfuse 的数据集规则：Dataset 属于 Project，Case 以 input、expected output 和 metadata 为核心，支持 UI/API/CSV 导入、从 Observation 创建和版本化。
- 采用 TrajectIQ 的回归诊断：对 baseline 与 candidate 的同一批 Case 对齐，输出新增失败、恢复成功、指标差值、关键任务影响、首个分歧 Span 与失败类别。
- 将 YAML Regression Gate 和 GitHub Actions 作为正式发布路径：门禁读取版本化 Experiment 结果，输出 PASS、WARNING、BLOCK、INCOMPLETE 或 INDETERMINATE 及 Markdown/JSON 证据。
- 单机自托管时自动初始化一个默认 Project；Project 是 Trace、Dataset、Experiment 与 API Key 的隔离容器，不强迫用户把“创建 Project”当作第一项业务操作。

## Capabilities

### New Capabilities
- `trace-first-agent-ingestion`: 接收并标准化用户已运行 Agent 的 HTTP JSON、OpenInference 和 OTLP Trace，按 Project 保存 Trace/Span。
- `trace-driven-quality-loop`: 支持线上 Trace 评分、人工标注、Trace/Observation 到 Dataset 的字段映射，以及版本化 Dataset 与离线 Experiment。
- `agent-experiment-execution`: 以外部 Agent Connection 为 Task，在固定 Dataset Version 上异步执行可复现实验并写入 Trace、Score 和聚合结果。
- `trajectory-regression-diagnostics`: 对齐 baseline/candidate Experiment，执行首错归因、YAML Gate 和 CI 可消费的质量报告。
- `trace-first-workbench-ui`: 提供 Trace 优先的 Project 工作台、接入指引、Dataset、Experiment、诊断和门禁页面。

### Modified Capabilities
- None. No main OpenSpec capability specifications currently exist.

## Impact

- 后续实现将重构 `apps/api`、`apps/worker`、`apps/web`、`packages/contracts`、示例和测试；本变更只创建计划，不修改它们。
- `prompt` Agent 类型、`PromptConfig`、`PromptRunner`、模型密钥配置和 Prompt 示例将成为待删除的遗留实现；RAG、Tool、Custom 以及 HTTP/Trace 接入保留并重构。
- 新增或完善 OpenTelemetry/OpenInference、HTTP/JSON Trace adapter、Project 默认初始化、Experiment 快照、Trace-to-Dataset 映射、诊断结果和 CI artifact 合同。
- 不复制 Phoenix、Langfuse 或 TrajectIQ 的源码、商标、UI 和私有服务协议；只采用其公开文档中的工作流、开放标准和本仓库可合法引入的开源依赖。
