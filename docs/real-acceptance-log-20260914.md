# 真实 Agent 验收记录

本记录只保存脱敏后的验收结果。真实 API Key、Project Key、完整模型输入输出和本地数据库均不进入 Git。

## 环境

- 日期：2026-09-14
- Chat Provider：DeepSeek OpenAI-compatible API
- Chat model：配置为 `deepseek-chat`，上游响应模型为 `deepseek-flash`
- Embedding Provider：阿里云百炼 OpenAI-compatible Embedding API
- Embedding model：`text-embedding-v4`
- Platform API：本地 FastAPI，独立 SQLite 验收库
- Queue：本地 Redis

## Provider 预检

以下检查实际调用了上游 Provider，并校验随机 challenge、模型标识和 usage：

```powershell
python tests/live/provider_preflight.py
python -c "from dotenv import load_dotenv; load_dotenv(dotenv_path='.env'); from tests.live.provider_preflight import LiveEmbeddingConfig, run_embedding_preflight; print(run_embedding_preflight(LiveEmbeddingConfig.from_environment()))"
```

结果：

- Chat challenge：通过
- Chat usage：`input_tokens=42`、`output_tokens=21`、`total_tokens=63`
- Embedding challenge：通过
- Embedding vector dimensions：`1024`
- Embedding usage：`input_tokens=28`

## Tool Agent

Tool lane 使用真实模型决定工具，平台只执行无副作用的虚构订单查询工具。Dataset 已从 3 条扩展到 5 条，新增处理中订单取消资格和未知订单查询场景。

执行入口：

```powershell
python tests/live/run_tool_acceptance.py
```

本次真实执行结果：

- Dataset：5 条 Case，生成新的不可变 Dataset Version
- baseline Experiment：`completed`
- candidate Experiment：`completed`
- 每条 Item：均有关联 Trace，证据状态为 `complete`
- `live_tool_selection`：baseline `5/5`，candidate `5/5`
- `live_tool_arguments`：baseline `5/5`，candidate `5/5`
- `live_business_state`：baseline `5/5`，candidate `5/5`
- Comparison：没有新增失败，没有缺失证据
- Gate：`passed`

这次 candidate 没有发生退化，平台保留了真实观测结果，没有人为制造 `BLOCK`。这证明了真实模型执行、Trace、评分、比较和 Gate 的正常路径，但不证明每次配置变化都会产生回归。

## RAG Agent

RAG lane 实际调用了 Embedding 和 Chat Provider，并保存 Retriever、文档 ID、LLM 和 usage 证据。

执行入口：

```powershell
python tests/live/run_rag_acceptance.py
```

本次真实执行结果：

- Dataset：2 条受控政策问答 Case
- Experiment：`completed`
- 每条 Item：均有关联独立 Trace，证据状态为 `complete`
- 每条 Trace：包含 Retriever 和 LLM Span
- Retriever：保存真实检索文档 ID
- LLM：保存真实响应模型和 usage

## 托管 LLM Judge

托管 Judge 的代码和单元/集成测试覆盖 Provider 连接、AES-GCM 凭据解密、Celery 任务、重试和评分替换。

在 2026-09-14 的第二次验收中，API、Worker、SQLite schema、Redis DB 和加密配置全部使用同一组隔离环境变量，真实队列闭环已通过：

- RAG Dataset Version：`39396185-73db-4609-bdef-65de7fb39f63`
- baseline Experiment：`72ecda04-8319-413c-8a5b-f5be04c55cb4`，状态 `completed`
- candidate Experiment：`9dfcefdd-6c23-4452-acfc-777dc84f6687`，状态 `completed`
- Judge baseline/candidate：各 2 条，全部 `passed`
- Judge provider：DeepSeek OpenAI-compatible，Key 只在未跟踪运行环境中使用，平台只返回掩码
- Comparison：新增失败 `0`，缺失证据 `0`，Judge 平均分 baseline/candidate 均为 `1.0`
- Gate：`passed`

因此，托管 Judge 的真实 Celery/Redis/数据库一致配置链路已经通过；Docker Compose 环境的等价复验仍受 Docker Desktop 无法启动影响。

## 当前未完成项

1. Docker Desktop 当前无法启动，干净 Docker Compose、重启恢复和 PostgreSQL 全链路验收尚未完成。
2. 真实浏览器已覆盖桌面和移动端导航、跨域预检、Dataset 创建、Version/Case 读取；Experiment、Trace、Score、Comparison、Gate 的真实浏览器逐按钮验收仍可继续扩展。
3. Tool 与 RAG candidate 本次真实运行均未退化，平台正确报告持平，不能据此宣称已证明“回归阻断”场景。

## 安全检查

- Git 跟踪文件中没有 `.env`、数据库、证书或日志文件。
- 本记录不包含 Project Key、模型 API Key、Authorization Header 或完整 Provider 响应。
- 真实验收产生的 `artifacts/` 和本地 SQLite 数据库位于 Git 忽略目录。
- Git 历史扫描只命中测试用的 `sk-*`、`Bearer ...` 字符串；`.env.example` 中的 salt/session 已明确为 `change-me-before-use` 占位值。

## 自动化验证

- Python/SDK/Worker/API：`325 passed, 4 warnings`
- Web TypeScript：`npm run typecheck` 通过
- Web production build：`npm run build` 通过
- Mock API 浏览器 E2E：`30 passed`（桌面和移动端各覆盖）
- 真实 API 浏览器 E2E：`4 passed`（桌面和移动端覆盖导航、跨域预检、Dataset 创建及 Version/Case 读取）
- OpenSpec strict validation：通过
- Docker Compose：静态配置通过，但 Docker Desktop 在本机无法启动，因此未宣称 Compose 运行验收通过。
## Compose 全链路验收（2026-09-15）

本节更新并覆盖前文在 Docker Desktop 不可用期间记录的“Compose 尚未验收”状态。

Docker Desktop 恢复后，使用未跟踪 `.env` 中的真实 Provider 配置和一次性 Project Key
完成了生产 Compose 验收。一次性 Project Key 仅存在于当前进程，验收后已撤销；仓库没有
保存任何真实 Key。

Compose 服务：

- API、Worker、Web、PostgreSQL、Redis 均健康
- `docker compose ... up -d --wait` 通过
- `docker compose ... config --quiet` 通过

真实链路：

- SDK Tool Agent：通过真实 DeepSeek Chat，5 条 Case，baseline/candidate 均完成
- OTel Experiment：完成，真实 Trace 含 8 个 Span
- Remote Upload Experiment：完成，真实 Trace 含 8 个 Span
- Remote Trigger Experiment：签名触发、远程回调、结果上传和完成状态均通过
- Provider Judge：真实 DeepSeek Judge 通过 Celery/Redis 执行，2 条 baseline 和 2 条 candidate Score 均通过
- RAG：真实阿里云百炼 `text-embedding-v4` 完成检索，真实 DeepSeek Chat 生成回答
- Comparison：Tool 和 RAG 均使用同一 Dataset Version，实际结果为持平，无新增失败和缺失证据
- Gate：Tool 和 RAG 均为 `passed`
- Failure isolation：2 条 Case 中 1 条完成、1 条失败，Experiment 为 `partial`，未影响已完成 Case

重启恢复：

```text
重启 API/Worker -> 等待 Compose 健康 -> 读取四个协议 Experiment
-> manifest attempts 保留 -> Trace 链接保留 -> Trace API 可读取
```

重启后验证结果：

- OTel、Remote Upload、Remote Trigger 实验均保持 `completed`
- 失败隔离实验保持 `partial`，完成数为 1，失败数为 1
- 4 个实验均能读取持久化 Item/attempt；成功实验各有 1 条 Trace，失败隔离实验保留已完成 Case 的 Trace
- 读取到的真实 Trace 均可通过 API 访问，单条协议 Trace 含 8 个 Span

本次 Compose 验收摘要中的资源 ID 已在命令输出中生成，但本文件不保存
Project Key、模型 API Key、Authorization Header、完整模型响应或用户业务数据。
