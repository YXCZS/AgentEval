## Purpose

提供符合 Phoenix 与 Langfuse 实际工作方式的中文项目工作台，让用户从真实 Trace 或测试集进入评测闭环，而不是被引导去配置平台托管 Prompt。

## ADDED Requirements

### Requirement: Trace-first project home
系统 SHALL 在 Project 首页展示三类可执行入口：接入/查看线上 Trace、从 Trace 创建 Dataset、基于 Dataset 创建 Experiment。首页 MUST 显示 Trace、Dataset、Experiment、失败与门禁状态的真实 Project 数据，且不得以硬编码演示数据冒充实际结果。

#### Scenario: Empty project guides trace ingestion
- **WHEN** 默认 Project 尚无 Trace、Dataset 或 Experiment
- **THEN** 首页 SHALL 将“接入 Agent Trace”作为首要操作，并展示 HTTP/OTLP/OpenInference 接入方式

#### Scenario: Active project highlights quality work
- **WHEN** Project 已有 Trace 或 Experiment
- **THEN** 首页 SHALL 展示真实的最近失败、Dataset 覆盖、Experiment 状态和最近 Gate 结论

### Requirement: Product workflow navigation
系统 SHALL 以 Traces、Datasets、Experiments、Evaluators、Regression 与 Gate 为主导航。Trace 详情 SHALL 支持查看 Span、Score、Annotation 并发起“加入 Dataset”；Experiment 详情 SHALL 支持查看 Case 结果、关联 Trace、比较与诊断。

#### Scenario: Trace becomes regression coverage through UI
- **WHEN** 用户在 Trace 详情中选择 Observation 并点击加入 Dataset
- **THEN** 界面 SHALL 收集字段映射并在成功后提供创建或运行 Experiment 的入口

#### Scenario: Regression diagnosis is reachable from gate failure
- **WHEN** 一个 Gate 返回 BLOCK
- **THEN** 界面 SHALL 链接到失败 Case、baseline/candidate 结果和可用的首错诊断

