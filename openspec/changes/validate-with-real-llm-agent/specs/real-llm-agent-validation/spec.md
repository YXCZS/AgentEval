## Purpose

确保平台至少通过一个真实模型驱动的外部 Tool Agent 完成可复核的端到端评测，以真实模型请求、工具循环、Trace 和回归证据取代固定规则或预制响应带来的虚假完成感。

## ADDED Requirements

### Requirement: Real model-driven external Agent
系统 SHALL 提供一个独立于评测平台运行的外部 Tool Agent，该 Agent MUST 在每个评测 Case 中调用已配置的真实大模型，由模型决定是否以及如何调用工具，并在获得工具结果后继续调用模型生成最终结果。该 Agent MUST NOT 在模型不可用时回退到关键词、固定分支、mock server 或预制响应。

#### Scenario: Model autonomously completes a tool task
- **WHEN** 用户使用有效模型配置向真实 Agent 提交一条订单任务
- **THEN** Agent SHALL 至少完成一次真实模型请求，并按照模型返回的 tool call 执行工具或明确生成无需工具的最终答案

#### Scenario: Provider failure cannot look successful
- **WHEN** 模型 endpoint 不可达、凭据无效、模型不存在或上游返回无效 tool call
- **THEN** Agent SHALL 返回可识别的失败，Experiment SHALL 保存错误证据，并且真实案例验证 MUST fail 而不是使用确定性逻辑继续

### Requirement: Verifiable model and trajectory evidence
每个成功的真实 Agent Case SHALL 产生可审计的 AGENT、LLM、TOOL 和 TOOL_RESULT 执行证据。LLM 证据 MUST 包含实际模型标识、finish reason、非零或由 provider 明确返回的 token usage、上游 request id（若 provider 提供）、请求轮次和时间；Tool 证据 MUST 包含模型选择的工具名、解析后的参数、执行结果和父子关系。平台和报告 MUST 标识该运行是 `real_llm` 还是 `deterministic_fixture`。

#### Scenario: A reviewer can distinguish a real run
- **WHEN** 真实 Agent Experiment 完成
- **THEN** 用户 SHALL 能从 Trace 和运行摘要核实模型标识、至少一个 LLM Span、实际 usage 以及模型发起的 Tool Span，且运行不得带有 fixture 或 mock 身份

#### Scenario: Missing LLM evidence invalidates validation
- **WHEN** Agent 返回业务结果但没有真实 LLM Span、模型标识或 provider usage 证据
- **THEN** 验证流程 SHALL 把该 Case 和整次真实案例标记为失败，不得把业务断言通过当成真实 Agent 验证通过

### Requirement: Real baseline and candidate regression
系统 SHALL 使用同一个不可变 Dataset Version、同一个真实模型配置和兼容的 Evaluator Version 分别测试 baseline 与 candidate Agent Release。两个 Release 的差异 SHALL 是被记录的 Agent 配置变化，例如 System Prompt 或 Tool Schema；比较 SHALL 输出逐 Case 指标、至少一条可观察的轨迹差异、首错证据和 Gate 结论。

#### Scenario: Configuration regression is detected from real calls
- **WHEN** candidate 的已记录 Tool Schema 或 Agent 指令变化使至少一个关键订单 Case 相对 baseline 退化
- **THEN** 平台 SHALL 将其报告为新增失败，链接两次真实 Trace，并依据首个可观察差异生成归因和非 PASS Gate

#### Scenario: Nondeterministic result is not fabricated
- **WHEN** 真实模型在某次运行中没有产生预期回归，或证据不足以稳定对齐
- **THEN** 验证流程 SHALL 报告本次实测结果或 `INDETERMINATE`，不得修改、替换或预制模型输出以制造预期的 `BLOCK`

### Requirement: Representative real-use Dataset
真实案例 Dataset SHALL 至少覆盖订单查询、处理中订单取消、已发货订单拒绝取消、已交付订单退款和无效订单，并使用业务状态、工具名称、工具参数和工具顺序等确定性断言评测真实模型行为。数据 SHALL 使用明确标注的虚构订单，不得包含真实个人信息。

#### Scenario: Real Agent is tested across business paths
- **WHEN** 用户运行真实案例 Experiment
- **THEN** 每种规定业务路径 SHALL 至少执行一个独立 Case，并保留输入、期望、实际输出、工具轨迹、Score 和错误状态

### Requirement: Secret-isolated model configuration
模型 endpoint、模型名和凭据 SHALL 由外部 Agent 自己读取。模型凭据 MUST NOT 进入浏览器、平台 API 请求、Agent Connection 响应、数据库、Trace、日志、Artifact 或 Git 跟踪文件；平台 SHALL 只保存并调用外部 Agent endpoint。

#### Scenario: Platform runs a real Agent without owning its provider key
- **WHEN** 外部 Agent 使用环境变量中的远程 provider 凭据或本地无密钥模型完成 Experiment
- **THEN** 平台 SHALL 获得 output、Trace 和 Score，同时任何平台持久化或导出内容都不得包含 provider credential

### Requirement: Honest demo and completion status
仓库 SHALL 分别提供确定性 fixture Demo 与真实 LLM Demo，并在命令、UI 和文档中清楚标注。真实 Demo MUST 执行 provider preflight、真实 Agent 调用、Dataset 到 Experiment、baseline/candidate、comparison、attribution 和 Gate 全链路；只有命令实际成功并通过真实证据检查后，文档才可声明真实 LLM Agent 已验证。

#### Scenario: Clean machine has no provider configured
- **WHEN** 用户在没有真实模型配置和本地模型的环境运行真实 Demo
- **THEN** 命令 SHALL 在开始 Experiment 前以中文说明缺少的配置并非零退出，不得启动 mock 或把 fixture 结果写成成功

#### Scenario: Real demo completes with auditable artifacts
- **WHEN** provider preflight 与所有真实 Case 成功执行且比较证据完整
- **THEN** 命令 SHALL 生成脱敏的运行摘要及 Markdown/JSON comparison artifact，包含 model、Dataset Version、两个 Agent Release、真实证据检查和 Gate 状态
