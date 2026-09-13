## Purpose

基于同一版本化测试集对 Agent 版本进行回归分析，定位首次行为偏差，并将可解释的质量阈值接入持续集成发布决策。

## ADDED Requirements

### Requirement: Experiment regression comparison
系统 SHALL 对同一 Dataset Version 上的 baseline 与 candidate Experiment 对齐 Case，展示聚合指标差值、新增失败、恢复成功、类别切片和关键任务影响。系统 MUST 区分质量回归、运行错误、缺失评分与不可比较状态。

#### Scenario: Candidate regression is reported
- **WHEN** candidate 在 baseline 成功的关键 Case 上失败
- **THEN** 系统 SHALL 将该 Case 标记为新增回归，并在比较结果中说明受影响指标和关键任务状态

#### Scenario: Candidate recovery is reported
- **WHEN** candidate 在 baseline 失败的 Case 上成功
- **THEN** 系统 SHALL 将该 Case 标记为恢复成功而不是回归

### Requirement: First-error attribution
对于具有可对齐轨迹的新增失败，系统 SHALL 找到 baseline 与 candidate 的第一个可观察分歧步骤，并归类为工具选择、工具参数、工具执行、检索、最终回答、格式、超时、成本或延迟问题。无法可靠判断时系统 MUST 标记为不确定而不得伪造归因。

#### Scenario: Wrong tool is localized
- **WHEN** candidate 的首个工具调用名称与 baseline 或 Case 期望轨迹不一致
- **THEN** 系统 SHALL 输出该工具 Span 作为首错步骤并归类为工具选择错误

#### Scenario: No reliable attribution is available
- **WHEN** Trace 缺失必要 Span 或轨迹无法对齐
- **THEN** 系统 SHALL 将归因标记为不确定并保留原始 Trace 证据链接

### Requirement: Declarative release gate and CI result
系统 SHALL 支持由 YAML 定义的指标阈值、比较方向、严重级别和关键任务策略，并为 CI 返回 PASS、WARNING、BLOCK、INCOMPLETE 或 INDETERMINATE。机器可读结果 MUST 包含实际值、阈值、失败 Case、缺失或错误数及首错诊断引用。

#### Scenario: Critical regression blocks release
- **WHEN** candidate 造成 YAML 规则定义的关键任务回归或阻断阈值失败
- **THEN** 系统 SHALL 返回 BLOCK 并生成可供 GitHub Actions 发布的 Markdown 与 JSON 报告

#### Scenario: Incomplete data cannot pass gate
- **WHEN** Gate 所需的 Case 或评分处于未完成、缺失或错误状态
- **THEN** 系统 SHALL 返回 INCOMPLETE 或 INDETERMINATE，且不得返回 PASS

