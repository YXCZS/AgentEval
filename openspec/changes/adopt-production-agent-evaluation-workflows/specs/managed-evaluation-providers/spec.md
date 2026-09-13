## Purpose

让平台可以安全连接真实模型执行 LLM-as-a-Judge，并像 Phoenix、Langfuse 一样向自托管用户提供可配置 Provider，同时保留用户自有评测服务的扩展能力。

## Delivery Scope

本 capability 属于 V1.1，而不是首个 MVP 的阻塞项。MVP 使用真实 Agent 输出与可复现的客观评估器；在本 capability 完成真实连接、加密和失败验收前，界面不得把平台托管 Judge 显示为可用功能。

## ADDED Requirements

### Requirement: Project-scoped real provider connections
系统 SHALL 允许 Project 管理员创建、测试、禁用、轮换和删除真实 LLM Provider Connection。首个正式协议 SHALL 支持 OpenAI-compatible Chat Completions，并 SHALL 保存 provider、base URL、model、默认参数和加密凭据。

#### Scenario: User configures an OpenAI-compatible provider
- **WHEN** 用户提交合法 base URL、model 和 API Key 并点击测试
- **THEN** 服务端 SHALL 发起一个最小真实模型请求，显示成功的 provider/model 信息后保存连接

#### Scenario: Provider validation fails
- **WHEN** API Key、URL 或模型无效
- **THEN** 平台 SHALL 返回安全且可操作的错误，不得保存为可用连接或使用 mock 验证代替

### Requirement: Secret confidentiality and encryption
Provider 凭据 MUST 在浏览器提交后仅由服务端处理，使用平台主密钥认证加密后持久化，并在所有读取 API、日志、Trace、队列消息和导出中保持不可回显。生产环境缺少安全主密钥时 Provider 功能 MUST 拒绝启动或拒绝保存凭据。

#### Scenario: Connection is read after creation
- **WHEN** 用户或前端读取 Provider Connection
- **THEN** 响应 SHALL 仅显示掩码和非敏感元数据，不得包含可恢复的 API Key

#### Scenario: Encryption key is absent in production
- **WHEN** 生产模式未配置凭据加密主密钥
- **THEN** 平台 SHALL 明确拒绝创建或使用 Provider Connection，不得使用硬编码开发密钥

### Requirement: Real LLM-as-a-Judge execution
用户 SHALL 能创建版本化 Judge Evaluator，绑定 Provider Connection、模型、模板、rubric、输出 Schema、阈值和采样策略。Judge SHALL 对真实 Trace 或 Experiment Item 发起真实模型请求，并保存模型、usage、原始结构化结果、归一化 Score、解释和失败证据。

#### Scenario: Judge evaluates an Agent output
- **WHEN** 一个绑定真实 Provider 的 Judge Evaluator 处理完成的 Experiment Item
- **THEN** 平台 SHALL 发起真实模型调用并保存可追溯到 Evaluator Version、Provider/Model 和被评对象的 Score

#### Scenario: Judge call fails
- **WHEN** Judge 请求超时、限流或返回不符合 Schema 的结果
- **THEN** 该 Score SHALL 标记失败或不完整，Gate MUST NOT 将缺失 Judge 证据当成通过

### Requirement: External evaluator remains extensible
系统 SHALL 保留签名外部 Evaluator 协议，供企业网关、内部模型或自定义评测器使用，但产品 SHALL 将其标为扩展方式而非配置 LLM Judge 的唯一方法。

#### Scenario: User chooses an internal judge service
- **WHEN** 用户配置外部 Evaluator endpoint
- **THEN** 平台 SHALL 通过受保护协议发送评测输入并保存与平台 Provider Judge 一致的 provenance 和状态
