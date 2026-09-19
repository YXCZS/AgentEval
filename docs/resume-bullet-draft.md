# 简历项目条目改写建议

> 基于「回归门禁拦截演练 + 评测集扩充」两项成果，为你简历中 Agent Eval 项目条目提供可直接使用的改写。

---

## 一、项目标题（可选两种）

1. **Agent Eval Workbench —— LLM Agent 评测与发布门禁平台**（强调"门禁"，差异化）
2. **Agent Eval Workbench —— 自托管 Agent 评测与回归拦截平台**（强调"拦截"结果）

---

## 二、项目简介（2~3 句）

> 面向 LLM Agent 的自托管评测平台，覆盖「评测集构建 → 分层评测 → 首错归因 → 发布门禁 → 线上观测 → Badcase 回流」全闭环。后端 FastAPI + PostgreSQL + Redis + Celery + OpenTelemetry，前端 Next.js，配套 Python SDK。多用户私有 project 隔离，支持真实 LLM/嵌入模型接入，全程无 mock。

---

## 三、核心 bullet（4 条，推荐全部采用）

**Bullet 1 —— 分层评测与 Trace（技术深度）**

> 针对 LLM Agent 评测缺乏可观测证据的问题，基于 OpenTelemetry 实现 Trace 采集与归一化，落库 LLM/Tool/Retrieval 三类 span（含 input/output/usage/cost/起止时间）；构建确定性优先、开放式走外置 LLM Judge、兜底人工标注的三层评分体系，覆盖工具选择、参数正确性、轨迹顺序、检索召回、业务状态五类指标。

**Bullet 2 —— 发布门禁与回归拦截（结果，最硬的一条）**

> 设计 YAML 声明式发布门禁（severity=block/warning + critical_tasks 圈定关键用例 + 五态决策），打通「注册 baseline → 候选跑测 → 对比 → 门禁」链路。用真实 DeepSeek 模型做回归拦截演练：故意注入工具选择回归的候选版本被门禁稳定 `BLOCK`（三项指标 1.0→0.8），修复版本复跑恢复 `PASS`，证明门禁可拦截真实回归且不永久锁死。

**Bullet 3 —— 首错归因与 Badcase 回流（差异化，同行简历没有）**

> 实现首错归因引擎（对比 baseline/candidate Trace 定位到具体 case 与首个偏离 span），并支持 Badcase 一键回流评测集（保留 span 级溯源）；人工标注队列 + 评分审计保证不可变历史。形成「评测 → 门禁 → 观测 → 回流」的持续改进闭环。

**Bullet 4 —— 工程化与规模（可选，体现工程量）**

> 全栈自研：后端 FastAPI + SQLAlchemy 2.0 + Alembic 迁移 + Celery 异步评测 + Redis 队列，前端 Next.js 16 + React 19，配套 Python SDK（含 wheel 一致性 CI 护栏）；版本化评测集 31 条（覆盖订单状态/取消/退款窗口边界/未知订单等多类业务场景），全量 322 单测 + 前端 e2e 通过，生产密钥强校验 + 鉴权 header 脱敏。

---

## 四、写法要点（对比同行简历）

| 维度 | 你的旧写法 | 建议新写法 |
| --- | --- | --- |
| 动词 | 实现 / 建立 / 完成（过程） | 拦截 / 定位 / 打通 / 证明（结果） |
| 数字 | 5 条 + 2 条（暴露短板） | 31 条（覆盖维度）+ 1.0→0.8→1.0（拦截证据） |
| 结果 | "两轮 Gate 均通过"（弱） | "故意回归被 BLOCK、修复后 PASS"（强） |
| 差异化 | 未提人工标注/回流/线上接入 | 主动强调这三样（同行简历都没有） |

---

## 五、面试话术钩子（加分项）

- **为什么门禁用 YAML 而不是硬编码？** → 版本化、可审计、CI 可消费（产出 `BLOCK`/`PASS` 机器可读状态）。
- **怎么保证评测可信？** → 真实 LLM + challenge 验证防缓存 + usage 校验防假响应 + 每条 case 独立持久化 Trace。
- **怎么处理 LLM Judge 偏差？** → 诚实说明：人工标注数据已具备，但"用人工样本自动校准裁判 + 偏差检测"是下一步（体现方法论认知完整）。
- **评测集怎么维护？** → 不可变版本 + manifest 哈希去重 + Badcase 回流（带 span 溯源）。
