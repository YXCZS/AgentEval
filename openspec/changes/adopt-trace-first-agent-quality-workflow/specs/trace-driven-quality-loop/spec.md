## Purpose

将线上观测产生的真实 Agent 样本转化为可重复运行的评测资产，并同时支持线上评分与离线质量实验，形成从生产失败到回归保护的闭环。

## ADDED Requirements

### Requirement: Online trace evaluation and annotation
系统 SHALL 支持对已接入 Trace 或其 Observation/Span 写入确定性、外部 LLM Judge 或人工 Score。外部 LLM Judge SHALL 通过用户自管的 Evaluator Connection 调用；系统 MUST NOT 保存模型供应商 API key 或提供模型供应商配置页面。每个 Score MUST 关联目标 Trace/Span、评分名称、类型、数值或标签、解释、证据来源和评分器版本。

#### Scenario: Online evaluator records trace score
- **WHEN** 用户或自动规则对一条已完成 Trace 执行评分
- **THEN** 系统 SHALL 保存可追溯 Score 并在该 Trace 详情展示评分结果和证据

#### Scenario: Human annotation is auditable
- **WHEN** 评审者通过人工标注修改或新增质量结论
- **THEN** 系统 SHALL 保存评审者、时间、结论和说明，并保留原有自动评分

#### Scenario: External judge failure is preserved as incomplete evidence
- **WHEN** 用户自管的 LLM Judge 接口超时、返回无效结果或不可达
- **THEN** 系统 SHALL 保存可审计的评分失败证据，并将依赖该评分的结论标记为不完整而不得视为通过

### Requirement: Dataset creation from multiple sources
系统 SHALL 支持在 Project 中手动创建 Dataset Case、通过 API 创建、从 CSV 导入，以及从一条或多条 Trace Observation 转换 Case。Case SHALL 以 input、可选 expected_output、metadata 为核心，并允许 RAG 的 reference context 与 Tool Agent 的 expected tool trajectory 扩展字段。

#### Scenario: Observation becomes a dataset case
- **WHEN** 用户从生产 Trace 选择一个 Observation 并配置输入、期望输出和元数据的字段映射
- **THEN** 系统 SHALL 创建带 source trace 引用的 Dataset Case 供后续实验使用

#### Scenario: CSV dataset import validates rows
- **WHEN** 用户上传 CSV 并映射源列到标准 Case 字段
- **THEN** 系统 SHALL 在提交前展示有效行、无效行和字段错误

### Requirement: Versioned dataset reproducibility
系统 SHALL 为 Dataset Case 的新增、修改、删除或归档创建可识别的 Dataset Version。Experiment MUST 绑定一个不可变 Dataset Version，历史 Experiment 的样本集合不得因后续编辑而改变。

#### Scenario: Dataset edit creates a new version
- **WHEN** 用户更新某 Dataset 的一个 Case
- **THEN** 系统 SHALL 生成新 Dataset Version，且旧版本仍可被历史 Experiment 查询

#### Scenario: Archived case is excluded from new experiment
- **WHEN** 用户归档一个 Dataset Case 后创建新 Experiment
- **THEN** 系统 SHALL 不将该 Case 加入新 Experiment，同时保留旧 Experiment 的历史结果
