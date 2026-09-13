import { expect, test } from "./test-fixture";

const evaluator = {
  id: "evaluator-1",
  name: "task_success",
  version: "1.0.0",
  evaluator_type: "deterministic",
  requires: ["expected_state"],
  supported_agent_types: ["tool"],
  score_min: 0,
  score_max: 1,
  direction: "higher_is_better",
  default_threshold: 1,
  rubric: null,
  judge_model: null,
  config: { comparison: "subset" },
  enabled: true,
};

const traceSummary = {
  trace_id: "trace-1",
  run_id: "run-1",
  case_id: "case-1",
  status: "completed",
  source: "platform",
  span_count: 2,
  started_at: "2026-01-01T00:00:00Z",
  ended_at: "2026-01-01T00:00:01Z",
  created_at: "2026-01-01T00:00:01Z",
};

const trace = {
  trace_id: "trace-1",
  run_id: "run-1",
  case_id: "case-1",
  status: "completed",
  source: "platform",
  extensions: { request_kind: "evaluation" },
  scores: [],
  spans: [
    {
      span_id: "span-agent",
      trace_id: "trace-1",
      parent_span_id: null,
      kind: "agent",
      name: "Agent root",
      status: "completed",
      started_at: "2026-01-01T00:00:00Z",
      ended_at: "2026-01-01T00:00:01Z",
      input: { question: "hello" },
      output: { answer: "ok" },
      error: null,
      attributes: { agent_version: "v1" },
      extensions: {},
    },
    {
      span_id: "span-tool",
      trace_id: "trace-1",
      parent_span_id: "span-agent",
      kind: "tool",
      name: "lookup_order",
      status: "completed",
      started_at: "2026-01-01T00:00:00Z",
      ended_at: "2026-01-01T00:00:00.500Z",
      input: { order_id: "A-1" },
      output: { state: "shipped" },
      error: null,
      attributes: {},
      extensions: {},
    },
  ],
};

const timeline = {
  trace_id: "trace-1",
  started_at: traceSummary.started_at,
  ended_at: traceSummary.ended_at,
  spans: [
    { span_id: "span-agent", parent_span_id: null, kind: "agent", name: "Agent root", status: "completed", started_at: traceSummary.started_at, ended_at: traceSummary.ended_at, duration_ms: 1000, depth: 0 },
    { span_id: "span-tool", parent_span_id: "span-agent", kind: "tool", name: "lookup_order", status: "completed", started_at: traceSummary.started_at, ended_at: "2026-01-01T00:00:00.500Z", duration_ms: 500, depth: 1 },
  ],
};
const tracePage = (items: unknown[]) => ({ items, total: items.length, offset: 0, limit: 25, next_offset: null });

test("evaluator and trace pages use their API workflows", async ({ page }) => {
  let currentEvaluator = { ...evaluator };
  let created = false;
  let createPayload: Record<string, unknown> | null = null;
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (method === "GET" && path === "/projects/project-1/evaluators") return json(created ? [currentEvaluator, evaluator] : [currentEvaluator]);
    if (method === "GET" && path === "/projects/project-1/provider-connections") return json([]);
    if (method === "POST" && path === "/projects/project-1/evaluators") {
      createPayload = request.postDataJSON() as Record<string, unknown>;
      created = true;
      currentEvaluator = { ...currentEvaluator, id: "evaluator-2", name: "answer_quality", version: "1.0.0" };
      return json(currentEvaluator, 201);
    }
    if (method === "PATCH" && path === "/projects/project-1/evaluators/evaluator-2/enabled") {
      currentEvaluator = { ...currentEvaluator, enabled: url.searchParams.get("enabled") === "true" };
      return json(currentEvaluator);
    }
    if (method === "GET" && path === "/projects/project-1/traces") return json(tracePage([traceSummary]));
    if (method === "GET" && path === "/projects/project-1/traces/trace-1/timeline") return json(timeline);
    if (method === "GET" && path === "/projects/project-1/traces/trace-1") return json(trace);
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "评估器", exact: true }).click();
  await expect(page.getByRole("heading", { name: "task_success 1.0.0" })).toBeVisible();
  await page.getByRole("button", { name: "新建评估器", exact: true }).click();
  await page.getByLabel("名称").fill("answer_quality");
  await page.getByLabel("版本").fill("1.0.0");
  await page.getByRole("button", { name: "创建版本", exact: true }).click();
  await expect(page.getByText("评估器 answer_quality 1.0.0 已创建。", { exact: true })).toBeVisible();
  expect(createPayload).not.toHaveProperty("judge_model");
  await page.getByRole("button", { name: "停用此版本", exact: true }).click();
  await expect(page.getByText("answer_quality 1.0.0 已停用。", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Trace", exact: true }).click();
  await expect(page.getByRole("heading", { name: "trace-1" })).toBeVisible();
  await page.getByLabel("搜索 Trace").fill("trace-1");
  await page.getByRole("button", { name: /Agent root/ }).click();
  await expect(page.getByText("Observation 数据", { exact: true })).toBeVisible();
  await expect(page.locator(".span-json-grid .detail-block").first().locator("pre")).toContainText('"hello"');
  await page.getByRole("button", { name: "刷新 Trace", exact: true }).click();
  await expect(page.getByText("执行时间线", { exact: true })).toBeVisible();
});
