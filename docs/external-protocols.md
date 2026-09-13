# 外部 Agent 与 Judge 协议

平台评测用户已经运行的 Agent，不执行上传源码，也不保存业务模型供应商 API Key。MVP 的正式入口是用户进程中的 Python SDK；平台不会按 Case 调用用户的 `/run` 端点。

## Python SDK（MVP）

用户先在平台创建 Dataset Version、Agent Release 和 Experiment，然后在自己的 Python 环境安装 `agent-eval-sdk`。SDK 拉取不可变 Case manifest，在用户进程内调用真实 Agent，提交 Item 结果并关联 OTel Trace。Agent 可以使用用户自己的模型 Key、工具、数据库或 RAG 服务，这些秘密不会上传平台。

平台要求真实 Agent 运行声明与证据相匹配：需要模型时必须有 LLM Span、模型标识和 usage 状态；Tool/RAG 评测还必须有对应的工具或检索 Span。缺失证据会进入 `INCOMPLETE`，不会被当作通过。

## Remote Upload（V1.2）

已运行的 Node.js、Java、CI、Notebook 或微服务可以只使用 HTTP/JSON 接入，无须安装 Python SDK，也不需要实现平台规定的 `/run` 端点。运行时使用 Project 凭据访问 API；真实凭据只能来自部署环境或 Secret Manager，不能写入代码、Trace 或本文档。

流程固定为：

```text
POST Experiment (execution_mode=remote_upload)
-> GET immutable manifest
-> POST Item start
-> 运行用户自己的真实 Agent
-> POST Item complete/fail
-> POST OTLP Trace
-> 可选 POST Score
-> POST finalize
```

创建 Experiment 后，远程运行时读取冻结的 Case manifest：

```text
POST /projects/{project_id}/experiments
GET  /projects/{project_id}/experiments/{experiment_id}/manifest
POST /projects/{project_id}/experiments/{experiment_id}/items/start
POST /projects/{project_id}/experiments/{experiment_id}/items/{item_id}/complete
POST /projects/{project_id}/traces/otlp
POST /projects/{project_id}/traces/{trace_id}/scores       # 可选
POST /projects/{project_id}/experiments/{experiment_id}/finalize
```

远程运行器的所有请求使用 `Content-Type: application/json` 与 Project 鉴权头 `X-Project-Key: $AGENT_EVAL_PROJECT_KEY`。Project Key 只应从运行环境或 Secret Manager 读取，不能写入源代码、Trace 或本文档。`manifest` 是 Experiment 创建时固化的 Dataset Version 快照，运行时不得改用当前 Dataset 别名。`items/start` 的 `external_run_id` 是调用方生成的幂等键；同一个键和同一内容重试会返回原 Item，不同内容会被拒绝。

远程运行时按 Case 提交结果，并可以先提交结果、后传 Trace：

```json
{
  "input": {"question": "订单 42 在哪里？"},
  "variables": {"locale": "zh-CN"},
  "messages": [],
  "metadata": {"run_id": "run-123", "case_id": "case-42"},
  "trace_id": "trace-run-123-case-42"
}
```

结果提交必须包含 `output` 或安全错误，并使用 `trace_id` 关联本次真实 Trace：

```json
{
  "output": {"answer": "订单已发货。"},
  "usage": {"input_tokens": 120, "output_tokens": 35, "cost": 0.002},
  "tool_calls": [
    {"name": "lookup_order", "arguments": {"order_id": "42"}, "order": 0}
  ],
  "trace_id": "trace-run-123-case-42",
  "runtime_metadata": {"runtime": "nodejs", "transport": "fetch"}
}
```

OTLP 根 Span 必须带有以下 Experiment 属性，且 `agent_eval.execution.origin` 必须为 `remote_upload`：

```text
agent_eval.project.id
agent_eval.experiment.id
agent_eval.experiment.item.id
agent_eval.dataset.id
agent_eval.dataset.version.id
agent_eval.case.id
agent_eval.agent.release
agent_eval.execution.origin
agent_eval.repetition
```

这些值由平台校验 Project、Dataset Version、Agent Release、Case 与 Item 的对应关系；不一致的 Trace 不会入库。结果先到时，Experiment 会显示“等待 Trace”，Trace 成功写入后自动解除。重复 OTLP 投递按 Trace ID 和内容幂等，不会复制 Span 或 Score。

调用方也可以为已入库 Trace 上传可选 Score。Score 必须引用 Project 内的 `evaluator_version_id`，并包含 `source`、`status`、`value`/`label`、`passed` 等规范字段；它与平台自动评分使用同一份 Trace、Case 与 Experiment 关联。相同 Case、指标和来源的冲突 Score 会被拒绝。

未知厂商信息放入 Trace 的 `extensions`。适配器将故障归一化为 `timeout`、`authentication_error`、`protocol_error`、`connection_error`、`rate_limit`、`service_error` 或 `response_too_large`。认证错误和畸形响应不重试；任何重试都不会把未完成 Case 变成通过。

平台不会从 Worker 访问 Agent URL，也不会把 `/run` 作为 MVP 的正式合同。

## Remote Trigger（V1.2）

Remote Trigger 用于已经部署在用户环境中的 Agent 服务。先在 Dataset 上保存 Trigger URL，平台仅在创建 `execution_mode=remote_trigger` 的 Experiment 后发送一次 run-level webhook；平台不会按 Case 调用用户的 `/run`，也不会把业务模型 Key、Project Key 或签名 secret 放进正文。

发送的 JSON 只包含冻结的 Experiment、Dataset Version 和回调端点：

```json
{
  "protocol": "agent_eval_remote_trigger_v1",
  "experiment": {
    "id": "experiment-123",
    "name": "release regression",
    "execution_mode": "remote_trigger",
    "agent_release": "git-sha-abc",
    "evidence_policy": "tool_trajectory_required"
  },
  "dataset": {"id": "dataset-1", "version_id": "version-3", "version": 3},
  "callback": {
    "api_base_url": "https://eval.example.com",
    "manifest_url": "https://eval.example.com/projects/project-1/experiments/experiment-123/manifest",
    "item_start_url": "https://eval.example.com/projects/project-1/experiments/experiment-123/items/start",
    "item_complete_url_template": ".../items/{item_id}/complete",
    "item_fail_url_template": ".../items/{item_id}/fail",
    "trace_otlp_url": "https://eval.example.com/projects/project-1/traces/otlp",
    "finalize_url": "https://eval.example.com/projects/project-1/experiments/experiment-123/finalize"
  }
}
```

接收方使用它自己安全保存的、创建 Trigger 时只显示一次的 secret 验证原始 UTF-8 请求字节：

```text
signature_input = <timestamp> + "." + <delivery_id> + "." + <exact_request_body>
X-Agent-Eval-Trigger-Timestamp: <unix_timestamp>
X-Agent-Eval-Trigger-Delivery-Id: <uuid>
X-Agent-Eval-Trigger-Signature: v1=<hex_hmac_sha256>
```

接收方必须在启动 Agent 前验证 HMAC-SHA256、拒绝超过五分钟的消息，并保存已处理的 `delivery_id` 至少五分钟以拒绝重放。验签通过后，运行器读取 manifest，使用自身环境中的 Project Key 调用 Remote Upload 回调接口。Webhook 已接受仅代表“运行器已收到通知”，不代表 Experiment 已完成。

平台为每个 run-level webhook 持久化独立的 Delivery 记录。通过下面的接口查询安全的投递状态，不会返回签名、请求正文、响应正文或 secret：

```text
GET /projects/{project_id}/datasets/{dataset_id}/remote-trigger/deliveries
```

状态 `accepted` 仅表示远程运行器返回了 2xx；Experiment 仍保持 `queued`，直到运行器上传全部 Item、Trace 并调用 `finalize`。超时、连接错误、`408`、`425`、`429` 与 `5xx` 最多尝试三次，间隔为 0.2 秒和 0.4 秒；其他 `4xx` 被标记为 `rejected`，不会重试。

## 外部 LLM Judge 扩展端点

普通 LLM Judge 默认绑定平台管理的 Provider Connection，用户只需填写模型 API Key，不需要另行部署 Judge 服务。企业网关、内部模型或自定义评测器可以选择用户自管 Judge HTTP 服务；Evaluator Version 会引用一个 Evaluator Connection，平台发送 Case、Agent 结果、工具、Trace 关联和精确 rubric：

```json
{
  "run_id": "run-123",
  "case_id": "case-42",
  "trace_id": "trace-run-123-case-42",
  "metric_name": "answer_quality",
  "evaluator_version": "answer-quality@1.0.0",
  "rubric": "根据参考答案判断正确性。",
  "input": {"question": "订单 42 在哪里？"},
  "expected_output": "订单已发货。",
  "actual_output": "订单 42 已发货。",
  "criteria": [],
  "tool_calls": [],
  "trace": null,
  "metadata": {"source": "offline-experiment"}
}
```

Judge 返回标准化 Score 和来源信息：

```json
{
  "score": 0.9,
  "passed": true,
  "label": "good",
  "explanation": "回答与参考事实一致。",
  "evidence": [{"statement": "订单状态均为已发货。"}],
  "trace_id": "trace-run-123-case-42",
  "provenance": {
    "source": "external_judge",
    "evaluator_version": "answer-quality@1.0.0",
    "model": "judge-model",
    "model_release": "judge-2026-01",
    "rubric_version": "rubric-3",
    "prompt_template_version": "template-2",
    "metadata": {}
  },
  "extensions": {}
}
```

`explanation`、`provenance` 与 `provenance.evaluator_version` 必须存在。响应的 `source` 必须是 `external_judge`，Evaluator Version 必须与请求完全相同；平台会补充不可伪造的 Evaluator Connection ID 和 `signed_http_json_v1` 协议标识。超时、认证失败或畸形响应保存为不完整证据，依赖它的 Gate 不会返回 `PASS`。

Evaluator Connection 只保存 endpoint、timeout 与 `auth_ref`。部署方在未跟踪的 `.env` 中通过 `AGENT_EVAL_EXTERNAL_EVALUATOR_SECRETS` JSON 对象把 `auth_ref` 映射到真实签名密钥；API 和 Worker 可以读取该配置，Web、PostgreSQL 和 Redis 不接收它。缺少引用或密钥时，平台会在网络请求发出前拒绝执行。

每次请求按实际发送的 UTF-8 JSON 字节计算 HMAC-SHA256：

```text
signature_input = <unix_timestamp> + "." + <exact_request_body>
X-Agent-Eval-Timestamp: <unix_timestamp>
X-Agent-Eval-Signature: v1=<hex_hmac_sha256>
```

外部服务应校验签名、限制时间戳偏差并拒绝重放。签名密钥、请求头和认证信息不会进入 Score、Trace、日志或导出数据。
