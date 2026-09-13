# 生产运行路径迁移清单

本清单是 `adopt-production-agent-evaluation-workflows` 的实施基线。目标不是隐藏测试替身，而是保证任何会被用户启动、在 UI 中配置、由 Worker 调用或被 README 宣称为产品能力的路径都来自真实 Agent/模型执行。

## 必须从产品运行时删除

| 范围 | 当前文件 | 当前行为 | 迁移目标 |
| --- | --- | --- | --- |
| 规则 Agent 服务 | `examples/rag_agent/**`、`examples/order_agent/**`、`examples/custom_agent/**`、`examples/order-agent/**`、`examples/__init__.py` | 用固定 Python 分支生成 RAG、Tool 或结构化结果 | 删除，不再被 Python 包或镜像包含 |
| 故意回归 | `examples/order_agent/app.py` | `variant=regression` 强制选择错误工具 | 删除；比较只展示真实 Release 的实测差异 |
| Seeder 与预制结论 | `examples/seed_regression_demo.py` | 创建固定 Dataset、Trace、Score，并断言结果必须为 `BLOCK` | 删除；由 SDK/OTel/Remote Runtime 写入真实结果 |
| Compose 运行入口 | `infra/docker-compose.yml` | 默认启动三个规则 Agent，`demo` profile 启动 Seeder | 删除对应服务和 profile；Compose 只保留 Web、API、Worker、PostgreSQL、Redis |
| CI 演示 | `.github/workflows/ci.yml` | 运行 Seeder 并检查固定失败结果 | 删除；普通 CI 只验证平台，真实模型验收使用显式 live lane |
| 平台主动逐 Case 调 Agent | `apps/worker/agent_eval_worker/execution.py`、`apps/api/agent_eval_api/runner/http_agent.py`、`runner/__init__.py` | Worker 调用固定 `/run` 合同并重建 Agent Trace | 由 SDK/OTel/Remote Upload 代替；HTTP 仅保留 run-level 签名 Trigger |
| Agent 连接测试 | `apps/api/agent_eval_api/agents.py` | API 直接测试 `/run` 端点 | Release 与 endpoint 解耦；Trigger 使用独立连接测试和签名合同 |
| 产品 UI | `apps/web/app/connections-view.tsx`、`runs-view.tsx`、`page.tsx` | 先配置 `/run`，再让平台执行 Agent | 改为 SDK、OTel、Remote Runtime 三种入口和证据状态 |
| 产品文档 | `README.md`、`docs/architecture.md`、`docs/external-protocols.md`、`docs/operations.md`、`docs/reference-comparison.md`、`docs/usage.md` | 将规则 Agent 与固定 `BLOCK` 称为完整流程 | 改为真实 Agent 接入、真实 Provider Judge 和真实验收说明 |
| 构建打包 | `pyproject.toml`、`apps/api/Dockerfile`、`apps/worker/Dockerfile` | 将 `examples` 安装并复制进生产镜像 | 从生产 package discovery 和镜像中删除 `examples` |

## 需要迁移但保留的真实基础设施

| 范围 | 文件 | 保留理由 |
| --- | --- | --- |
| 确定性评测器 | `apps/api/agent_eval_api/evaluation/deterministic.py` 及调用路径 | 精确匹配、JSON Schema、Tool 顺序、延迟、token、成本是对真实输出执行的正式评测，不是模拟 Agent |
| Trace 接入 | `traces.py`、`trace_normalization.py`、`trace_privacy.py` | 已支持真实 HTTP JSON、OpenInference 和 OTLP；后续增加 Experiment/Item 关联 |
| Dataset、Score、Comparison、Gate | 对应 API、模型和 Worker | 这些处理持久化的真实结果；后续增加 evidence fail-closed 规则 |
| PostgreSQL、Redis/Celery | `infra/docker-compose.yml` 及 Worker | PostgreSQL 保存证据；Redis/Celery 执行真实 Judge 和异步评分，不再执行规则 Agent |
| 外部 Judge 协议 | `evaluation/judge.py`、`evaluator_connections.py` | 作为企业内部 Judge 扩展保留；默认 Judge 改为平台加密 Provider Connection |

## 只允许存在于测试范围

以下替身用于验证认证失败、超时、重试、幂等和前端状态，不得进入生产包、Compose 或产品文档：

- `tests/**` 中的 pytest fixture、`httpx.MockTransport` 和数据库 fixture；
- `apps/web/e2e/**` 中隔离的路由响应，后续改为新 SDK/OTel/Remote 合同；
- `tests/fixtures/**` 中明确标注的 OTLP/OpenInference、旧数据库和错误输入；
- Phoenix/Langfuse 等上游依赖自身的 mock 文件不属于本仓库产品代码，也不会被复制或打包。

`apps/api/agent_eval_api/evaluation/base.py`、`regression_gates.py` 中的 “deterministic” 或 “sample” 是评分语义，不是模拟 Agent；`package-lock.json` 中依赖名称产生的匹配属于第三方锁文件。上述项目均已分类，没有把未检查的匹配当作安全结论。

## 迁移前 PostgreSQL 证据

2026-09-10 在任何新 schema 修改前，对正在运行的 Compose PostgreSQL 执行了格式化完整备份和 schema-only 快照：

- 完整备份：`backups/agent-eval-pre-production-20260910-210008.dump`（69,772 bytes，SHA-256 `9503EA051A68825F350643AF72FF32333EB6722D9E2CA23C29C3B8F71FC2A4EB`）
- Schema 快照：`backups/agent-eval-pre-production-20260910-210008-schema.sql`（30,515 bytes，SHA-256 `234573FF3C79E1FDA8DB737194DE9414801DEEA9CDF2A7BBA69BB3A94009087D`）
- Alembic revision：`1a2b3c4d5e6f`
- 恢复前数据计数：Projects `1`、Datasets `10`、Traces `20`、Evaluation Runs `9`

完整备份已恢复到独立数据库 `agent_eval_restore_rehearsal_20260910`，核对上述表和 revision 后删除了该临时数据库。原 `agent_eval` 数据库未被替换或清空。`backups/` 已被 Git 忽略，备份内容不会上传。

## 审计命令

```powershell
rg -n -i "demo|mock|fixture|deterministic|seed|intentional|variant|/run|run_http_agent|预制|模拟|演示" apps examples docs README.md infra packages pyproject.toml .env.example .github
```

实施期间每次执行该命令都必须按照本清单分类。任务 8.4 完成后，正式运行目录中的模拟 Agent、Seed 和故意回归匹配必须为零。

