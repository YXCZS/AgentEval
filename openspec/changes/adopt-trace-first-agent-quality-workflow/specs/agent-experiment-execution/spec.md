## Purpose

为外部运行的 Agent 提供 Phoenix/Langfuse 风格的离线 Experiment：在固定测试集和评分规则下调用连接端点或关联外部运行结果，生成可复现、可比较的质量证据。

## ADDED Requirements

### Requirement: External agent connection and release identity
系统 SHALL 将被测对象登记为 RAG、Tool 或 Custom External Agent Connection，并保存其测试调用协议、认证引用、超时与版本或 release 标识。系统 MUST NOT 提供 Prompt Agent、Prompt Runner 或平台托管模型执行能力。

#### Scenario: Registered external agent is tested
- **WHEN** 用户创建一个具有可达测试端点和 release 标识的 External Agent Connection
- **THEN** 系统 SHALL 保存连接配置引用，并允许该 release 被选择为 Experiment candidate

#### Scenario: Managed prompt execution is unavailable
- **WHEN** 用户尝试创建平台调用大模型的 Prompt Agent 或提交模型 API key
- **THEN** 系统 SHALL 明确拒绝该产品路径，并指引用户接入已运行的 Agent 或 Trace

### Requirement: Immutable experiment definition
系统 SHALL 创建由 Dataset Version、External Agent Connection release、Evaluator Set、运行参数和可选 baseline 标识组成的 Experiment。创建后系统 MUST 冻结这些输入快照；只有使用同一 Dataset Version 和兼容指标的 Experiment 才可进行回归比较。

#### Scenario: Experiment freezes selected inputs
- **WHEN** 用户启动一个 Experiment
- **THEN** 系统 SHALL 记录当时的 Dataset Version、Agent release、Evaluator Version 和运行参数快照

#### Scenario: Incompatible experiments cannot be compared
- **WHEN** 用户选择具有不同 Dataset Version 的两个 Experiment 进行回归比较
- **THEN** 系统 SHALL 标记结果不可直接比较并说明不兼容原因

### Requirement: Isolated asynchronous experiment execution
系统 SHALL 对 Dataset Version 中每个 Case 独立执行或关联外部 Agent 结果，并记录运行状态、Trace、Score、延迟、token、成本和错误。单个 Case 的超时或协议错误 MUST NOT 中断其他 Case；未完成或评分错误的结果 MUST NOT 被当作通过。

#### Scenario: One case failure does not erase experiment
- **WHEN** Experiment 中一个 Agent Case 调用超时而其他 Case 成功
- **THEN** 系统 SHALL 将该 Case 标记为失败或未完成，继续处理其他 Case，并把 Experiment 标记为部分完成

#### Scenario: Experiment produces aggregate results
- **WHEN** 所有可执行 Case 已完成或达到终态
- **THEN** 系统 SHALL 展示每项评分的样本结果、聚合指标、缺失与错误计数，以及关联 Trace

