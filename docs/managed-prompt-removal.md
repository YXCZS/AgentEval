# 托管 Prompt 能力移除说明

本次变更是一次破坏性迁移。平台从同时支持“托管 Prompt 执行”和“外部 Agent 测试”，收敛为只评测用户自行运行的 RAG、Tool 与 Custom Agent。

## 已移除的能力

- `prompt` Agent 类型、`PromptConfig` 与旧 `/projects/{project_id}/agents` API。
- 平台内置的 PromptRunner 以及直接调用模型供应商的执行路径。
- `LLM_API_KEY`、`LLM_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL` 配置。
- `agent_versions.prompt_config` 数据库列。

真实 Agent 使用 `/projects/{project_id}/agent-releases` 注册 endpoint-independent Release 身份，并由用户进程中的 Python SDK 执行。平台也支持独立 OTel、Remote Upload 和签名 Remote Trigger；这些入口同样不要求平台执行用户源码。平台托管的 Provider Judge 使用单独加密的评测 Provider 凭据，平台不接收业务 Agent 的模型供应商 API Key。

## 升级前

1. 按 `docs/database-migration-rehearsal.md` 备份数据库并演练恢复。
2. 将仍需保留的旧 Prompt 配置迁移到用户自己的 Agent 服务中。
3. 在自己的运行环境安装 `agent-eval-sdk`，把现有 Agent 调用封装为 SDK `task`。
4. 创建带 Git SHA、镜像标签或语义版本号的 endpoint-independent Agent Release。

## 数据库迁移

执行 `alembic upgrade head` 会运行 `1a2b3c4d5e6f_remove_managed_prompt_config.py` 并删除 `agent_versions.prompt_config`。该列删除后，旧 Prompt 配置不能从线上数据库直接恢复；生产升级应保留升级前备份。

迁移提供的 `downgrade()` 只恢复一个空的兼容列，不会重建已经删除的数据或重新启用 PromptRunner。因此，正式回滚应恢复升级前的完整数据库备份和上一版本应用，而不是依赖部分降级。

## 保留的 Prompt 相关语义

Trace 中的 `prompt` Span、外部 Judge 的 `prompt_template_version`、CSV 输入列的 `prompt` 别名以及第三方工具 Promptfoo 的名称不代表平台托管 Prompt，仍然保留。
