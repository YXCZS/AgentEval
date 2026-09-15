# 数据库备份与迁移演练

本流程适用于任何会删除字段、迁移已有记录或改变数据语义的数据库变更。它先保护旧数据，再验证升级。不要在没有成功备份和演练的情况下执行破坏性迁移。

## 本地演练

仓库的 `tests/fixtures/legacy_project_1.json` 模拟了旧版单项目实例的 `project-1` 数据：它包含 API key 的哈希记录、旧 Prompt Agent 版本、版本化 Dataset Case、Evaluator 和运行快照。fixture 不包含真实密钥。

在项目根目录执行：

```powershell
python scripts/rehearse_migration.py
```

脚本执行以下步骤：

1. 将临时 SQLite 数据库升级到 `ffe07a933165` 基线 schema。
2. 写入代表已有实例的 `project-1` fixture。
3. 使用 SQLite backup API 创建备份文件。
4. 从备份恢复到全新的数据库文件。
5. 将恢复的数据库升级到当前 Alembic `head`。
6. 校验旧记录已迁移至 `default-project`，API key 哈希、Agent Release 身份、Dataset Case 和历史运行快照仍然存在；已废弃的 `agent_versions.prompt_config` 列被删除，但历史 Experiment 快照中的模型证据不会丢失。

当将来新增破坏性 Alembic migration 时，演练命令不变；它会从基线 fixture 恢复后再升级到新的 `head`。若需要保留演练产生的文件排查失败原因，可指定目录：

```powershell
python scripts/rehearse_migration.py --workdir .\migration-rehearsal
```

## Compose PostgreSQL 备份

在升级实际 Compose 数据库前，从项目根目录运行：

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
$backup = ".\backups\agent-eval-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres pg_dump -U agent_eval -Fc agent_eval > $backup
Get-FileHash $backup -Algorithm SHA256
```

备份文件含运行数据，不能提交 Git，也不能分享给不受信任的人。确认文件大小非零、SHA-256 已记录，并把备份复制到当前机器以外的位置后，才可以升级。

## PostgreSQL 恢复演练

恢复会替换目标数据库内的对象，只能在隔离的本地副本或明确批准的恢复环境中执行。先将备份复制进 PostgreSQL 容器，再执行恢复：

```powershell
$backup = Resolve-Path .\backups\agent-eval-YYYYMMDD-HHMMSS.dump
docker compose --env-file .env -f infra/docker-compose.yml cp $backup postgres:/tmp/agent-eval-restore.dump
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres pg_restore --clean --if-exists --no-owner -U agent_eval -d agent_eval /tmp/agent-eval-restore.dump
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres psql -U agent_eval -d agent_eval -c "SELECT id, name FROM projects;"
```

然后启动 API，并验证 `default-project` 的受认证 Trace 仍可写入和读取。已有 `project-1` 的开发会话令牌在本地迁移后可作为兼容令牌使用一次；将环境变量更新为 `dev:default-project:<workspace-session-secret>` 后即可使用新的令牌格式。
