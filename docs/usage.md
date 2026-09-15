# 零基础使用指南

先记住一句话：**平台不创造 Agent，平台收集真实 Agent 的执行证据，并判断新版本是否比旧版本差。**

## 1. Agent 从哪里来

Agent 是用户已经拥有的程序，例如会检索知识库的 RAG Agent、会调用订单工具的 Tool Agent，或返回业务 JSON 的 Custom Agent。它在用户自己的 Python 进程中运行，使用用户自己的模型 Key 和业务依赖。

本项目 MVP 不接收用户源码，不替用户保存业务模型 Key，也不在 Compose 中启动示例 Agent。平台提供 SDK，让用户把真实 Agent 函数作为 `task` 运行并上传结果。

## 2. 核心名词

| 名词 | 直白解释 |
| --- | --- |
| Project | 数据隔离边界 |
| Trace | Agent 一次真实运行的完整记录 |
| Span | Trace 中的一步，如 LLM、检索或工具调用 |
| Dataset Case | 一道测试题以及期望状态/工具等断言 |
| Dataset Version | 不会被后续编辑改变的试卷快照 |
| Agent Release | Agent 代码、镜像、模型配置或版本标签的身份 |
| Experiment | 用固定试卷、Release 和评测器执行的一次实验 |
| Score / Gate | 单项评分和发布判断 |

## 3. 完整流程

```text
创建 Dataset -> 选择 Dataset Version
登记 Agent Release -> 创建 Evaluator
创建 sdk_task Experiment
-> SDK 读取 Cases
-> 用户进程调用真实 Agent
-> 上传结果和真实 OTel Trace
-> 服务端验证证据并计算 Score
-> 第二个 Release 使用同一 Version
-> Comparison / Attribution / Gate
```

平台 API 是控制平面，负责版本、状态、权限和查询；用户进程是执行平面，负责调用真实模型和工具。两者通过 Project API Key 通信。

## 4. 启动平台

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --wait
docker compose --env-file .env -f infra/docker-compose.yml ps
```

访问 <http://127.0.0.1:3000>。API 文档使用 `.env` 中的 `AGENT_EVAL_API_PORT`，例如端口为 `18080` 时访问 <http://127.0.0.1:18080/docs>；未设置时使用 Compose 默认端口 `8000`。默认 Compose 只有 Web、API、Worker、PostgreSQL、Redis；它没有测试 Agent 和预制数据。

若 `3000` 或 `8000` 已被占用，请在未跟踪的 `.env` 中设置 `AGENT_EVAL_WEB_PORT`、`AGENT_EVAL_API_PORT`，并让 `NEXT_PUBLIC_API_URL`、`AGENT_EVAL_BASE_URL` 指向新的 API 宿主端口；随后使用上面的 `--build` 命令重新构建 Web。

## 5. Dataset

可在页面中创建 Dataset，也可以导入 CSV、JSON 或 JSONL。导入先预览，再提交；提交后形成不可变 Version。

一个 Case 可以包含：

```json
{
  "id": "cancel-42",
  "input": {"action": "cancel", "order_id": "ORD-42"},
  "expected_tools": [{"name": "cancel_order", "arguments": {"order_id": "ORD-42"}}],
  "expected_state": {"status": "cancelled"},
  "metadata": {"category": "orders"}
}
```

嵌套工具、消息和检索上下文推荐 JSONL；CSV 适合扁平字段。修改 Dataset 不会改变已经运行的 Experiment，因为 Experiment 会保存自己的 manifest 快照。

## 6. Agent Release

Release 只登记身份，例如：

- `git:4f21c9a`
- `image:sha256:...`
- `model-config:v3`

Release 不需要 Agent URL，也不需要把 OpenAI/Anthropic Key 填到平台。基线和候选版本分别登记为两个 Release。

## 7. SDK 执行真实 Agent

在用户 Agent 项目中安装 SDK：

```powershell
pip install -e .\sdk\python
python -c "import agent_eval; print(agent_eval.__version__)"
```

上面的相对路径以本仓库根目录为当前目录。`.env.example` 只列出变量名，不会被 SDK
自动加载；运行 SDK 前须由终端、进程管理器或用户 Agent 自己的配置加载器设置
`AGENT_EVAL_BASE_URL`、`AGENT_EVAL_PROJECT_ID` 和 `AGENT_EVAL_API_KEY`。真实 API Key
只填写在被 Git 忽略的 `.env`、Secret Manager 或当前进程环境中。

示例：

```python
from agent_eval import Client, ExperimentRunner, TaskResult
from my_agent import run_agent

client = Client(
    base_url="http://127.0.0.1:8000",
    project_id="default-project",
    api_key="project-api-key",
)
dataset = client.get_dataset("dataset-id", version_id="dataset-version-id")
release = client.get_release("agent-release-id")

def task(case):
    answer, usage = run_agent(case.input)  # 用户自己的真实模型/工具调用
    return TaskResult(output=answer, usage=usage)

result = ExperimentRunner(client).run(
    dataset=dataset,
    task=task,
    release=release,
    evaluator_version_ids=["evaluator-version-id"],
    name="tool-agent candidate",
    evidence_policy="tool_trajectory_required",
)
```

SDK 对每个 Case：读取 manifest、创建 Item、建立根 Span、调用 `task(case)`、上传 Trace、提交输出，最后请求服务端计算评分。并发、超时、重试和取消都在 SDK 中控制。一个 Case 失败不会覆盖其他 Case 的结果。

如果 Agent 使用 OpenInference 或其他 OpenTelemetry instrumentor，SDK 会保留其 LLM、Retriever、Tool 和 Tool Result 子 Span。没有要求的 Trace 证据时，Score 是 `missing`，Gate 不会伪造通过。

## 8. baseline 与 candidate

先使用稳定 Release 运行一次，再用新 Release 使用**同一个 Dataset Version**运行。页面中的 Comparison 会按 Case 对齐两次实验，显示：

- 指标改善、持平或退化；
- 新增失败和恢复成功；
- Tool 轨迹的第一个可证据分歧；
- 无法判断时的 `INDETERMINATE`。

Gate 只读取持久化 Score、Comparison 和证据状态。`INCOMPLETE` 和 `INDETERMINATE` 不会默认为 `PASS`。

## 9. Trace 入口

当前支持 Canonical JSON、OpenInference-shaped JSON、OTLP HTTP JSON、Remote Upload 和签名 Remote Trigger：

- `POST /projects/{project_id}/traces`
- `POST /projects/{project_id}/traces/ingest`
- `POST /projects/{project_id}/traces/otlp`

SDK 适合 Python Agent；已运行的其他语言 Agent 可以使用独立 OTel 或 Remote Upload；已经部署的 Agent 服务可以使用 Dataset-scoped 签名 Remote Trigger。OTLP 当前承诺 HTTP JSON，不承诺 OTLP gRPC。

## 10. 技术为什么这样用

| 技术 | 作用 |
| --- | --- |
| Next.js/React | 页面化查看真实数据和诊断链路 |
| FastAPI/Pydantic | 统一 API 合同和输入校验 |
| PostgreSQL/SQLAlchemy | 长期保存版本、Trace、Score 和关系 |
| Alembic | 安全升级数据库结构 |
| OpenTelemetry/OpenInference | 跨框架记录 Agent 的真实步骤 |
| Redis/Celery | 执行 Provider Judge、聚合和后台任务；Remote Upload/Trigger 本身通过 API/签名 Webhook 接入 |
| Docker Compose | 一台服务器启动平台基础设施 |

MVP 的 Agent 执行不由 Worker 调 `/run`，而由用户 SDK 进程直接执行。这样平台不需要拥有业务 Agent 的 Key，也不需要替用户部署 Agent。

## 11. 常见问题

- Experiment 创建后没有结果：确认用户进程确实运行了 SDK，而不是只在页面创建定义。
- Item 是 `INCOMPLETE`：检查是否上传了要求的 Trace、LLM usage、Tool 或 Retriever 证据。
- Gate 不是 `PASS`：打开对应 Experiment 和 Trace，先解决缺失证据或真实失败。
- 业务模型调用失败：检查用户 Agent 自己的模型 Key、网络和模型配置；这些不由平台代管。
- 需要旧 HTTP `/run` 迁移：旧适配器只作为兼容路径保留，正式 MVP 不将它作为主流程；详见 [迁移说明](production-runtime-migration.md)。
