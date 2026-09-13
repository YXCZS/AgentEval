# Trace 接入范围

平台把已经运行中的 Agent 产生的观测数据统一保存为 Trace 和 Span。当前支持三种 JSON 入口：

- `POST /projects/{project_id}/traces`：提交平台标准的 Canonical Trace。
- `POST /projects/{project_id}/traces/ingest`：提交 OpenInference 风格 JSON，或带有 `trace` / `payload` 的通用请求。
- `POST /projects/{project_id}/traces/otlp`：提交 OTLP HTTP JSON 的 `ExportTraceServiceRequest`。

所有入口都需要项目凭证。Agent 使用 `X-Project-Key`，浏览器开发会话使用
`X-Workspace-Session`。OTLP HTTP JSON 的请求体形状是：

```json
{
  "resourceSpans": [
    {
      "resource": {"attributes": []},
      "scopeSpans": [
        {
          "scope": {"name": "my-agent.instrumentation"},
          "spans": []
        }
      ]
    }
  ]
}
```

平台会把 resource attributes 合并到每个 Span 的 `attributes`，并把 scope 和未知字段保留到
`extensions`。Span 的 `traceId`、`spanId`、`parentSpanId`、时间、status、OpenInference
语义类型、输入输出、token usage 和错误信息会映射到 Canonical Trace/Span。凭证字段会在持久化
前脱敏。

例如：

```bash
curl -X POST http://localhost:8000/projects/default-project/traces/otlp \
  -H 'Content-Type: application/json' \
  -H 'X-Project-Key: aek_default-project_...' \
  --data @tests/fixtures/otlp_http_trace.json
```

标准 OpenTelemetry Collector 可以把 OTLP HTTP JSON 转发到这个入口，但 Collector 的鉴权、批处理
和重试配置属于部署方责任。当前只承诺 OTLP HTTP JSON 互操作；OTLP gRPC 暂不支持，待 HTTP
互操作测试稳定后再单独引入，不能把 gRPC endpoint 当作已支持能力宣传。
