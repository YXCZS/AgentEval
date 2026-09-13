## Purpose

为用户已经运行的 RAG、Tool 与 Custom Agent 提供项目隔离的 Trace 接入与标准化能力，使后续观测、评分和离线实验都基于真实执行证据。

## ADDED Requirements

### Requirement: Project-scoped external agent ingestion
系统 SHALL 将 Project 作为 Trace、Agent Connection、Dataset、Experiment、API Key 和访问控制的隔离容器。自托管单项目部署 SHALL 在首次启动时初始化默认 Project，并允许用户直接开始接入 Agent；系统 MUST NOT 要求用户先手工创建 Project 才能发送第一条 Trace。

#### Scenario: Default project accepts the first trace
- **WHEN** 新部署实例使用有效默认 Project 的接入凭据接收一条 Trace
- **THEN** 系统 SHALL 保存该 Trace 并在默认 Project 的 Trace 列表中展示它

#### Scenario: Cross-project access is rejected
- **WHEN** 一个 Project 的凭据请求读取或写入另一个 Project 的 Trace
- **THEN** 系统 SHALL 拒绝该请求且不得泄露目标 Project 的数据

### Requirement: Supported external trace inputs
系统 SHALL 接受规范化 HTTP JSON Trace，并 SHALL 提供 OpenInference 与 OTLP Trace 的接入路径或兼容适配器。系统 MUST 将已运行 Agent 的 Trace 作为一等数据源，而非要求平台托管模型调用或上传 Agent 源码。

#### Scenario: HTTP JSON trace is ingested
- **WHEN** 外部 Agent 通过认证后的 HTTP 接口提交包含 trace 和 span 的有效 JSON
- **THEN** 系统 SHALL 返回可查询的 Trace 标识并保存父子 Span 关系

#### Scenario: Unsupported trace payload is rejected safely
- **WHEN** 接入请求不满足声明的 JSON、OpenInference 或 OTLP 格式
- **THEN** 系统 SHALL 返回可定位字段的校验错误且不得创建部分 Trace

### Requirement: Safe idempotent trace ingestion
系统 SHALL 在同一 Project 与 source 范围内使用外部 trace/span 标识防止重试造成重复证据，并 SHALL 在持久化前执行配置化的请求大小、Span 数量和嵌套深度限制。相同有效负载的重放 MUST 返回既有 Trace；相同标识但内容冲突或超出限制的负载 MUST 被拒绝，且不得留下部分 Trace。

#### Scenario: Retried trace is not duplicated
- **WHEN** 外部 Agent 因网络重试再次提交同一条已经成功接收的 Trace
- **THEN** 系统 SHALL 返回原 Trace 标识且不得创建第二份 Trace 或 Span

#### Scenario: Unsafe trace is rejected before persistence
- **WHEN** 外部 Agent 提交超过 Project 配置限制的 Trace 请求
- **THEN** 系统 SHALL 返回明确的限制错误且数据库中不得出现该请求创建的部分 Trace 或 Span

### Requirement: Canonical execution evidence
系统 SHALL 将外部执行标准化为 Trace 与 Span，并保留 trace id、span id、parent span id、时间、状态、输入、输出、错误、属性、token、成本和未知扩展字段。系统 SHALL 支持 agent、llm、tool、tool_result、retrieval、guardrail 与 evaluator Span 类型。

#### Scenario: Tool trajectory is preserved
- **WHEN** 外部 Trace 包含 Agent、工具调用和工具结果的层级 Span
- **THEN** 系统 SHALL 在 Trace 详情中按原始父子关系展示各步骤及其输入输出

#### Scenario: Sensitive values are protected
- **WHEN** Trace 属性包含配置为敏感的字段或凭据模式
- **THEN** 系统 SHALL 在持久化和展示前脱敏该值，同时保留可审计的脱敏事实
