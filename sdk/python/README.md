# agent-eval-sdk

在用户自己的 Python 进程中运行真实 Agent 评测任务，并把 Experiment Item、结果与
OpenTelemetry Trace 上传到 Agent Eval Workbench。

```powershell
pip install -e .\sdk\python
```

创建客户端前，在当前进程中设置 `AGENT_EVAL_BASE_URL`、`AGENT_EVAL_PROJECT_ID` 和
`AGENT_EVAL_API_KEY`。SDK 不加载仓库的 `.env.example`，也不读取或上传业务模型密钥。

完整流程与示例见仓库根目录的 `README.md` 和 `docs/usage.md`。
