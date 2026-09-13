import { expect, test } from "./test-fixture";

const createdAt = "2026-01-01T00:00:00Z";
const experiment = {
  id: "experiment-1",
  name: "真实订单 Agent 回归",
  status: "completed",
  created_at: createdAt,
};
const humanEvaluator = {
  id: "human-evaluator-1",
  name: "human_quality",
  version: "1.0.0",
  evaluator_type: "human",
  rubric: "核对答案是否满足用户要求",
  enabled: true,
};
const experimentPage = (items: unknown[]) => ({ items, total: items.length, offset: 0, limit: 200, next_offset: null });

test("人工评审队列保存评分、审计历史，并展示 Judge 来源证据", async ({ page }) => {
  const queues: Array<Record<string, unknown>> = [];
  const items: Array<Record<string, unknown>> = [];
  let score: Record<string, unknown> | null = null;
  const audit: Array<Record<string, unknown>> = [];

  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (method === "GET" && path === "/projects/project-1/annotation-queues") return json(queues);
    if (method === "POST" && path === "/projects/project-1/annotation-queues") {
      const payload = request.postDataJSON() as { name: string; description: string | null; evaluator_version_id: string };
      const queue = { id: "queue-1", ...payload, created_at: createdAt };
      queues.push(queue);
      return json(queue, 201);
    }
    if (method === "GET" && path === "/projects/project-1/evaluators") return json([humanEvaluator]);
    if (method === "GET" && path === "/projects/project-1/experiments") return json(experimentPage([experiment]));
    if (method === "GET" && path === "/projects/project-1/experiments/experiment-1/manifest") {
      return json({ experiment_id: experiment.id, dataset_version_id: "dataset-version-1", total: 1, offset: 0, limit: 200, next_offset: null, items: [{ case: { id: "case-order-1", input: { order_id: "A-100" } }, attempts: [] }] });
    }
    if (method === "GET" && path === "/projects/project-1/annotation-queues/queue-1/items") return json(items);
    if (method === "POST" && path === "/projects/project-1/annotation-queues/queue-1/items") {
      const queueItem = { id: "queue-item-1", queue_id: "queue-1", run_id: "experiment-1", case_id: "case-order-1", trace_id: "trace-1", status: "pending", created_at: createdAt, completed_at: null };
      items.splice(0, items.length, queueItem);
      return json(queueItem, 201);
    }
    if (method === "GET" && path === "/projects/project-1/annotation-queues/queue-1/items/queue-item-1/score") {
      return score ? json(score) : json({ detail: "human score not found" }, 404);
    }
    if (method === "PUT" && path === "/projects/project-1/annotation-queues/queue-1/items/queue-item-1/score") {
      const payload = request.postDataJSON() as { value: number; label: string | null; passed: boolean; explanation: string | null; evidence: Array<Record<string, unknown>> };
      score = { id: "score-1", ...payload };
      audit.push({ id: "audit-1", action: "created", reviewer: "browser:workspace", previous_value: null, new_value: { value: payload.value, passed: payload.passed, evidence: payload.evidence }, created_at: createdAt });
      items[0] = { ...items[0], status: "completed", completed_at: createdAt };
      return json(score);
    }
    if (method === "GET" && path === "/projects/project-1/annotation-queues/queue-1/scores/score-1/audit") return json(audit);

    if (method === "GET" && path === "/projects/project-1/traces") return json({ items: [{ trace_id: "trace-1", run_id: "experiment-1", case_id: "case-order-1", status: "completed", source: "remote_upload", span_count: 1, started_at: createdAt, ended_at: createdAt, created_at: createdAt }], total: 1, offset: 0, limit: 25, next_offset: null });
    if (method === "GET" && path === "/projects/project-1/traces/trace-1/timeline") return json({ trace_id: "trace-1", started_at: createdAt, ended_at: createdAt, spans: [{ span_id: "span-1", parent_span_id: null, kind: "agent", name: "订单助手", status: "completed", started_at: createdAt, ended_at: createdAt, duration_ms: 1, depth: 0 }] });
    if (method === "GET" && path === "/projects/project-1/traces/trace-1") return json({ trace_id: "trace-1", run_id: "experiment-1", case_id: "case-order-1", status: "completed", source: "remote_upload", extensions: {}, spans: [{ span_id: "span-1", trace_id: "trace-1", parent_span_id: null, kind: "agent", name: "订单助手", status: "completed", started_at: createdAt, ended_at: createdAt, input: { order_id: "A-100" }, output: { state: "shipped" }, error: null, usage: {}, cost: null, attributes: {}, extensions: {} }], scores: [{ id: "judge-score-1", experiment_item_id: "item-1", repetition: 1, attempt: 1, metric_name: "answer_quality", evaluator_version_id: "judge-v1", trace_id: "trace-1", span_id: null, source: "llm_judge", status: "passed", value: 0.9, label: null, passed: true, explanation: "回答完整", evidence: [], provenance: { provider_connection_id: "provider-1", model: "deepseek-chat", response_id: "judge-response-1" } }] });
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/?view=annotations");
  await page.locator(".annotation-create input").first().fill("高风险订单复核");
  await page.locator(".annotation-create select").selectOption(humanEvaluator.id);
  await page.locator(".annotation-create button.primary").click();
  await expect(page.locator(".annotation-queue-item")).toContainText("高风险订单复核");

  await page.locator(".annotation-add-case select").first().selectOption(experiment.id);
  await page.locator(".annotation-add-case button").click();
  await expect(page.locator(".annotation-item")).toContainText("case-order-1");

  await page.locator(".annotation-editor input[type=number]").fill("0.8");
  await page.locator(".annotation-editor textarea").first().fill("订单状态与工具轨迹一致");
  await page.locator(".annotation-editor textarea").nth(1).fill('[{"trace_id":"trace-1"}]');
  await page.locator(".annotation-editor button.primary").click();
  await expect(page.locator(".annotation-audit")).toContainText("browser:workspace");
  await expect(page.locator(".annotation-audit")).toContainText("0.8");

  await page.goto("/?view=traces");
  await expect(page.locator(".score-provenance")).toBeVisible();
  await page.locator(".score-provenance summary").click();
  await expect(page.locator(".score-provenance pre")).toContainText("provider_connection_id");
});
