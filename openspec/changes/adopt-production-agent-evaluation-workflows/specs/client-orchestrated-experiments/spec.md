## Purpose

定义在用户自己的 Python 运行环境中执行真实 Agent 的实验协议，使平台获得主流 SDK Task 工作流，同时保持 Agent 代码、模型凭据和业务依赖由用户控制。

## Delivery Scope

本 capability 构成首个 MVP 的正式 Agent 执行入口。MVP 验收覆盖完整 SDK Task 闭环；后续里程碑增加其他语言和协议入口时，不得改变这里定义的用户进程执行、凭据边界和失败语义。

## ADDED Requirements

### Requirement: Python SDK task experiment
系统 SHALL 提供可安装的 Python SDK。用户 SHALL 能读取平台 Dataset 或使用明确上传的本地 Dataset，传入同步或异步 `task`，并运行一个真实 Experiment；`task` MAY 调用任意真实 Agent、RAG Pipeline、模型、工具、数据库或远程服务并返回可序列化输出。

#### Scenario: User evaluates an existing Agent function
- **WHEN** 用户将现有 Agent 调用封装为 SDK `task` 并选择平台上的 Dataset Version
- **THEN** SDK SHALL 对每个 Case 执行该函数，将输出、错误、Trace 关联和运行元数据写入同一 Experiment

#### Scenario: Task uses user-owned model key
- **WHEN** task 从用户进程的环境或 Secret Manager 读取模型 API Key
- **THEN** SDK SHALL 允许真实调用完成且不得把该 Key 上传到平台、Trace、日志或报告

### Requirement: Reproducible experiment definition
每次 SDK Experiment SHALL 固定 Dataset Version、Experiment 名称、Agent Release 身份、执行配置和证据要求。Dataset 或 Release 后续变化 MUST NOT 改变历史 Experiment 的输入和元数据。

#### Scenario: Dataset changes after a run
- **WHEN** 用户在 Experiment 完成后修改 Dataset
- **THEN** 历史 Experiment SHALL 继续显示其运行时绑定的 Dataset Version 和 Case 内容

### Requirement: Concurrency, isolation, retries and finalization
SDK SHALL 支持有界并发、每项超时、可配置重试、取消、进度和错误隔离。单个 Case 失败 MUST NOT 中止其他 Case；SDK SHALL 在 Trace 和结果上传完成后才将 Experiment 标记为终态。

#### Scenario: One Agent call times out
- **WHEN** 一个 Case 超时而其他 Case 正常完成
- **THEN** 超时 Item SHALL 保存错误，其余 Item SHALL 正常完成，Experiment SHALL 进入带错误的终态而不是伪装为全部成功

#### Scenario: Client exits before flush
- **WHEN** SDK 无法确认全部 Trace 和结果已上传
- **THEN** Experiment SHALL 保持 `INCOMPLETE` 或可恢复状态，不得自动标为通过

### Requirement: Automatic and manual instrumentation compatibility
SDK SHALL 创建 Experiment/Item 根 Span，并兼容真实 Agent 已有的 OpenTelemetry/OpenInference instrumentation。自动插桩和手工 Span SHALL 保持父子关系，未知标准属性 SHALL 被保留。

#### Scenario: LangChain or OpenAI is instrumented
- **WHEN** task 内部的 Agent 框架或模型客户端已生成 OpenTelemetry/OpenInference Span
- **THEN** 这些 Span SHALL 成为对应 Experiment Item 的子轨迹并保留模型、token、Tool 与 retrieval 语义

### Requirement: SDK authentication and compatibility
SDK SHALL 使用 Project-scoped API credential 连接平台，并提供明确的版本与服务兼容性错误。凭据不得出现在异常文本、调试输出或序列化配置中。

#### Scenario: Invalid project credential
- **WHEN** SDK 使用无效或无权访问目标 Project 的凭据
- **THEN** 平台 SHALL 拒绝运行创建或上传，SDK SHALL 返回可操作但不泄密的认证错误
