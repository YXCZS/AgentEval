## Purpose

确保可视化工作台、比较和发布门禁仅展示真实持久化数据及其证据完整性，让用户能够从接入到决策完整核查一次真实 Agent 评测。

## Delivery Scope

MVP 工作台覆盖 SDK、Dataset、Release、Experiment、Item、Trace、Score、Comparison 和 Gate 的最短真实链路。Provider、独立 OTel、Remote 和高级人工评审页面只有在对应后端能力验收后才进入导航。

## ADDED Requirements

### Requirement: Production onboarding without seeded success
空 Project SHALL 展示当前版本已验收的真实接入入口、凭据创建方式及可复制配置；当 SDK、OpenTelemetry/OpenInference 和 Remote Runtime 均完成时 SHALL 展示三种入口。任何版本都不得自动创建 Agent、Dataset、Trace、Score、Experiment 或成功报告，也不得把未验收入口显示为可执行。

#### Scenario: User opens a fresh project
- **WHEN** Project 中没有真实数据
- **THEN** 页面 SHALL 显示零状态和接入步骤，不得显示示例成功率、假 Trace 或预制实验

### Requirement: Evidence-aware experiment workflow
用户 SHALL 能从 Dataset 页面启动或登记真实 Experiment，选择 SDK/OTel/Remote 执行方式、Agent Release、Evaluator Set 和证据策略。结果页面 SHALL 区分运行中、成功、失败、取消和 `INCOMPLETE`，并提供 Case、Trace、Score 和错误之间的导航。

#### Scenario: SDK experiment appears in the UI
- **WHEN** 用户进程创建并完成 SDK Experiment
- **THEN** 前端 SHALL 实时显示真实进度、Case 结果、Trace 与评分，而不是要求在平台重新运行 Agent

#### Scenario: Result lacks required trace evidence
- **WHEN** 一个 Item 有输出但未满足 Experiment 的真实证据策略
- **THEN** 页面 SHALL 显示缺失项与 `INCOMPLETE` 状态，比较和 Gate SHALL 继承该状态

### Requirement: Real evaluation and comparison evidence
每个 Score、聚合指标、baseline/candidate 差值、首错归因和 Gate 结论 SHALL 能追溯到 Dataset Version、Case、Agent Release、Evaluator Version 和真实执行证据。平台 MUST NOT 预先假设 candidate 一定退化。

#### Scenario: Candidate does not regress
- **WHEN** 真实 candidate 的实测结果没有比 baseline 更差
- **THEN** 平台 SHALL 展示实际持平或改善结果，不得修改输出、选择性隐藏 Case 或制造 `BLOCK`

#### Scenario: Evidence is insufficient for attribution
- **WHEN** baseline 与 candidate Trace 无法可靠对齐
- **THEN** 首错归因 SHALL 返回 `INDETERMINATE` 并解释缺失证据，不得编造原因

### Requirement: Honest production acceptance
项目完成验收 SHALL 至少使用一个真实 Tool Agent 和一个真实 RAG Agent，通过真实 Provider 或用户现有业务服务完成 Dataset、Experiment、Trace、Evaluator、Comparison 和 Gate 链路。验收记录 MUST 包含运行时间、脱敏 provider/model、Release、Dataset Version、Experiment ID 和证据检查结果。

#### Scenario: No real provider is configured
- **WHEN** 自动测试通过但尚未完成真实 Provider/Agent 运行
- **THEN** 项目 SHALL 只报告“基础设施和合同测试通过”，不得报告“真实业务验收完成”

#### Scenario: Tool-only MVP acceptance succeeds
- **WHEN** 一个真实模型驱动的 Tool Agent 已完成 MVP 端到端链路，但真实 RAG 与平台 Provider Judge 尚未验收
- **THEN** 平台 MAY 报告“SDK Tool Agent MVP 验收完成”，但 MUST NOT 报告完整生产验收或 Tool 与 RAG 均已完成

#### Scenario: Full real acceptance succeeds
- **WHEN** Tool Agent 和 RAG Agent 均完成真实端到端运行且证据检查通过
- **THEN** 平台 SHALL 生成不含秘密的验收摘要，并允许用户从摘要中的 ID 打开对应数据

### Requirement: Secret-safe configuration templates
仓库 SHALL 只跟踪 `.env.example` 形式的变量名、占位值和获取说明。真实 `.env`、Provider Key、平台主密钥、运行 artifact 和用户业务数据 MUST 被 Git 忽略并接受自动密钥扫描。

#### Scenario: Repository is prepared for push
- **WHEN** 用户准备提交或推送代码
- **THEN** 密钥扫描 SHALL 检查被跟踪文件并在发现疑似真实凭据时失败
