> **Status: Superseded.** 本变更不会实施。它保留确定性 Demo 并新增一个“真实 LLM Demo”的边界已不符合产品要求；正式实现由 `adopt-production-agent-evaluation-workflows` 取代，后者要求 SDK/OTel/Remote 真实业务接入并删除所有产品运行时模拟路径。

## Why

当前仓库的 RAG、Tool 和 Custom 示例虽然通过真实 HTTP、Redis/Celery、数据库和评测链路运行，但 Agent 内部都是确定性 Python 规则，不能证明平台真正评测过由大模型自主推理和选择工具的 Agent。项目用于面试和实际演示，必须增加一个禁止 mock 回退、能够核验真实模型调用证据的端到端案例，并重新定义“完成”的验收标准。

## What Changes

- 新增一个独立部署的真实 LLM Tool Agent：模型根据用户任务自主选择订单查询、取消或退款工具，程序执行工具后把结果返回模型继续推理，直至生成最终答案。
- 外部 Agent 使用 OpenAI-compatible 模型接口，支持用户自备远程模型或本地 Ollama；模型凭据只进入 Agent 容器，不进入 Agent Eval Workbench API、Worker、数据库、Trace 或报告。
- 新增真实 baseline/candidate Agent Release。两者使用相同模型和 Dataset，candidate 只改变可审计的 System Prompt 或 Tool Schema，从而验证真实 Agent 配置变化造成的回归。
- 新增由真实模型调用产生的 AGENT、LLM、TOOL 和 TOOL_RESULT Trace 证据，包括模型、provider request id、实际 token usage、工具参数、工具结果和时间信息。
- 新增至少覆盖订单查询、处理中订单取消、已发货订单拒绝取消、已交付订单退款和无效订单的版本化评测集，以及确定性业务断言。
- 新增 `real-agent` Docker Compose profile 和端到端命令；命令在没有模型、凭据无效、返回预制内容或缺少真实 LLM 证据时必须失败，不得回退到固定规则或 mock。
- 保留现有确定性 Agent 作为平台自身的快速、可重复回归测试，但在 UI 和文档中明确标为 fixture，不能再作为“真实 LLM Agent 已验证”的证据。
- 将真实运行生成的脱敏摘要、comparison Markdown/JSON 和可复核运行说明作为演示证据；真实 Key、完整敏感输入和本地运行 artifact 不进入 Git。

## Capabilities

### New Capabilities

- `real-llm-agent-validation`: 定义真实外部 LLM Tool Agent、可核验模型调用证据、真实版本回归场景和禁止 mock 回退的端到端验收。

### Modified Capabilities

None. Main OpenSpec capability specifications have not yet been archived; this change adds an independent validation capability without weakening the completed Trace-first contracts.

## Impact

- 新增外部真实 Agent 示例、模型客户端依赖、Tool loop、Trace 生成和版本化配置。
- 扩展 Docker Compose profile、环境变量模板、真实案例 Dataset、运行脚本和文档。
- 复用现有 Agent Connection、Agent Release、Dataset Version、Experiment、Evaluator、Trace ingestion、comparison、attribution 与 Gate API；如真实运行暴露合同缺口，只能通过兼容扩展修复，不能用预制 Trace 绕过。
- 本机当前没有模型 API 凭据，也没有安装 Ollama。实施可以完成代码和自动化前置检查，但只有在配置一个真实远程模型或下载本地 Ollama 模型并成功调用后，相关任务才能勾选完成。
