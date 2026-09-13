## Purpose

规定真实业务 Agent 的正式接入方式和证据边界，使平台能够接收用户实际运行的 RAG、Tool 与 Custom Agent，而不依赖仓库内模拟服务或预制结果。

## Delivery Scope

MVP 首先交付并验收 SDK Task；独立 OTel/OpenInference、Remote Upload 和签名 Trigger 依次在后续里程碑开放。该阶段顺序不降低最终多入口要求，也不允许尚未验收的入口在产品中伪装为可用。

## ADDED Requirements

### Requirement: Multiple production integration modes
系统 SHALL 为真实 Agent 提供 SDK Task、OpenTelemetry/OpenInference 和 Remote Runtime Result Upload 三种正式接入方式，并 MAY 提供签名 HTTP Trigger 作为远程运行适配器。任何方式产生的数据 SHALL 汇入相同的 Project、Dataset、Experiment、Trace 和 Score 模型。

#### Scenario: Existing instrumented Agent needs no platform-specific run endpoint
- **WHEN** 用户的真实 Agent 已通过 OpenTelemetry 或 OpenInference 上报带有 Experiment 和 Case 关联属性的 Trace
- **THEN** 平台 SHALL 创建或关联对应 Experiment Item，不得要求用户再部署平台规定的 `/run` 接口

#### Scenario: Remote service uses a signed trigger
- **WHEN** 用户为 Dataset 配置远程实验触发 URL 并从界面启动 Experiment
- **THEN** 平台 SHALL 发送带一次性运行标识、Dataset Version、Case 引用、回调合同和可验证签名的请求，由用户服务异步执行并回传结果

### Requirement: Integration availability is truthful
系统 SHALL 只把已经实现且通过对应验收的接入模式标记为可用。尚未交付的模式 MUST NOT 显示可执行按钮、生成不可工作的命令或接受随后无法处理的 Experiment。

#### Scenario: MVP exposes only the verified SDK mode
- **WHEN** 部署版本只完成 SDK Task 的端到端验收而尚未完成独立 OTel 或 Remote Runtime 验收
- **THEN** 平台 SHALL 将 SDK Task 显示为可用，并将其他模式隐藏或明确标记为不可用且不得允许创建

### Requirement: Real execution evidence
正式 Experiment SHALL 记录 `execution_origin`、Agent Release、运行环境、Case 关联和 Trace 关联。声明使用 LLM 的执行 MUST 包含至少一个真实 LLM Span、模型标识、上游返回的 usage 状态以及真实时间信息；Tool Agent 的工具任务 MUST 包含由实际执行产生的 Tool Span 和参数/结果证据。

#### Scenario: Complete real Tool Agent evidence
- **WHEN** 一个真实 Tool Agent Case 成功完成
- **THEN** 用户 SHALL 能从 Experiment Item 进入 AGENT、LLM、TOOL 与 TOOL_RESULT 的父子轨迹，并看到模型、usage、工具参数、工具结果和时间

#### Scenario: Claimed LLM run lacks evidence
- **WHEN** 结果声明 `execution_origin=real_llm` 但缺少 LLM Span、模型标识或 usage 状态
- **THEN** 平台 SHALL 将该 Item 标记为 `INCOMPLETE`，保留原因且不得让其通过要求真实证据的 Gate

### Requirement: No runtime simulation or fallback
正式运行时 MUST NOT 提供 mock Provider、关键词/固定分支 Agent、预制输出、预制 Trace、故意回归开关或 Provider 失败后的规则回退。缺少真实 Agent、模型、凭据或网络时 SHALL 产生明确失败。

#### Scenario: Provider is unavailable
- **WHEN** 真实 Agent 的模型不可达、凭据无效、模型不存在或返回无效协议
- **THEN** 本次 Case SHALL 失败并记录安全的错误证据，不得切换到 mock、固定答案或本地规则 Agent

#### Scenario: Clean production installation
- **WHEN** 用户启动默认生产 Compose 配置
- **THEN** 运行服务中 SHALL 不包含仓库示例 Agent、Demo Seeder 或预制回归服务

### Requirement: Idempotent remote result ingestion
Remote Runtime SHALL 能按 Project、Experiment、Case、attempt 和外部运行标识幂等提交输出、Trace、usage、错误与可选 Score。重复的相同提交 SHALL 返回原结果，冲突重放 MUST 被拒绝且不得产生部分数据。

#### Scenario: CI retries the same result upload
- **WHEN** 远程 CI 因网络重试提交相同外部运行标识和相同内容
- **THEN** 平台 SHALL 返回同一 Experiment Item 且不重复计分

#### Scenario: Conflicting replay
- **WHEN** 相同外部运行标识被用于不同输出或 Trace
- **THEN** 平台 SHALL 拒绝冲突并保留原始不可变证据
