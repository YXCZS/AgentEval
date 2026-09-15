# 单机部署与运维

## 环境变量

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

`.env` 被 Git 忽略，仓库只跟踪 `.env.example`。生产环境必须替换 `API_KEY_SALT` 和 `WORKSPACE_SESSION_SECRET`，不能把真实 Key、Token 或密钥写入 tracked file。

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 连接地址 |
| `REDIS_URL` | Redis/Celery 地址 |
| `API_KEY_SALT` | Project API Key 哈希盐 |
| `WORKSPACE_SESSION_SECRET` | 浏览器会话秘密 |
| `AGENT_EVAL_WEB_PORT` | Web 暴露到宿主机的端口，默认 `3000` |
| `AGENT_EVAL_API_PORT` | API 暴露到宿主机的端口，默认 `8000` |
| `TRACE_MAX_FIELD_BYTES` | Trace 字段限制 |
| `TRACE_REDACTION_FIELD_NAMES` | 脱敏字段名 |
| `WORKER_MAX_CONCURRENCY` | 后台 Worker 并发上限 |

业务 Agent 的模型 Key 不配置在平台 Compose 中，而留在用户自己的 Agent 进程。V1.1 的托管 Judge Provider 另有加密配置要求，当前 MVP 不依赖它。

## 启动与检查

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --wait
docker compose --env-file .env -f infra/docker-compose.yml ps
$apiPort = if ($env:AGENT_EVAL_API_PORT) { $env:AGENT_EVAL_API_PORT } else { "8000" }
Invoke-WebRequest "http://127.0.0.1:$apiPort/health"
```

默认服务：Web、API、Worker、PostgreSQL、Redis。没有内置 Agent、Seeder 或预制结果。

如果默认端口已被其他进程占用，在未跟踪的 `.env` 中同时设置 `AGENT_EVAL_WEB_PORT`、`AGENT_EVAL_API_PORT`、`NEXT_PUBLIC_API_URL` 和 `AGENT_EVAL_BASE_URL`，再重新执行带 `--build` 的启动命令。健康检查地址中的端口应与 `AGENT_EVAL_API_PORT` 一致。

查看日志和停止服务：

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail=200 api worker
docker compose --env-file .env -f infra/docker-compose.yml down
```

MVP 的用户 Agent 不由平台 Worker 逐 Case 调用。Worker 启动时不会注册旧的 `agent_eval.execute_case`；SDK Experiment 必须从用户 Agent 进程启动。

## 数据库迁移

```powershell
python -m alembic heads
docker compose --env-file .env -f infra/docker-compose.yml exec -T api alembic current
```

破坏性升级前按 [数据库迁移演练](database-migration-rehearsal.md) 完成备份与恢复验证。

## 故障排查

### API 不健康

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail=200 api postgres
docker compose --env-file .env -f infra/docker-compose.yml exec -T api alembic current
```

确认 `DATABASE_URL` 使用 Compose 服务名 `postgres`，并检查迁移是否被其他进程占用。

### SDK 没有产生结果

确认用户进程已安装正确版本 SDK、Project API Key 正确，并实际执行了 `ExperimentRunner.run`。页面创建 Experiment 只会冻结定义，不会在平台内替用户运行 Agent。

### 证据是 INCOMPLETE

检查用户 Agent 是否上传了要求的根 Trace，以及对应的 LLM、usage、Tool 或 Retriever 子 Span。缺失证据是 fail-closed 的设计，不应通过修改 Gate 阈值绕过。

### 模型调用失败

检查用户 Agent 进程自己的模型 Key、Base URL、模型名称、网络和上游配额。平台不会读取或替用户保存业务 Agent 的模型 Key。

### 旧 HTTP Agent 迁移

旧 `/run` 适配器和 Celery task 仅为 V1.2 迁移保留，生产 Worker 默认不会加载它。新的正式实验入口是 SDK；不要把旧连接服务加入 Compose，也不要把它写入 MVP 文档或 CI。

## 安全检查

```powershell
$candidates = git ls-files --cached --others --exclude-standard | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
$candidates | Select-String -Pattern '(^|/)\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.db$|\.sqlite3?$|\.log$'
$candidates | ForEach-Object { rg -l -i 'sk-[A-Za-z0-9]{20,}|authorization:\s*bearer\s+[A-Za-z0-9._-]{20,}|api[_-]?key\s*=\s*[A-Za-z0-9._-]{20,}' -- $_ }
```

命令检查已跟踪文件和未被 `.gitignore` 排除的候选提交文件，不读取被忽略的 `.env`。不要把 `.env` 内容粘贴到日志或提交记录。提交前确认 `.env`、数据库、日志、`artifacts` 和构建缓存均未被跟踪，并使用 Gitleaks 等工具扫描 Git 历史。
