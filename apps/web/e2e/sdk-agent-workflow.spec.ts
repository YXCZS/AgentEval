import { expect, test } from "./test-fixture";

const createdAt = "2026-01-01T00:00:00Z";
const release = {
  id: "release-1",
  project_id: "project-1",
  version: 1,
  label: "candidate",
  agent_type: "tool",
  release_identity: "git:abc123",
  source_revision: "abc123",
  metadata: {},
  enabled: true,
  created_at: createdAt,
};
const dataset = { id: "dataset-1", name: "订单回归", description: "真实 Tool 用例", current_version_id: "dataset-version-1", created_at: createdAt, updated_at: createdAt };
const datasetVersion = { id: "dataset-version-1", dataset_id: dataset.id, version: 1, cases: [{ id: "case-1", input: { order_id: "42" }, expected_tools: [{ name: "lookup_order" }], metadata: {} }], metadata: {}, created_at: createdAt };
const evaluator = { id: "evaluator-1", name: "tool_selection", version: "1.0.0", evaluator_type: "deterministic", supported_agent_types: ["tool"], enabled: true, requires: ["expected_tools"] };
const run = { id: "experiment-1", name: "订单 Agent candidate", status: "completed", total_cases: 1, completed_cases: 1, failed_cases: 0, agent_version_id: release.id, dataset_version_id: datasetVersion.id, evaluator_version_ids: [evaluator.id], execution_mode: "sdk_task", evidence_policy: "tool_trajectory_required", baseline_run_id: null, execution_options: { repetitions: 1, concurrency: 4, timeout_seconds: 30, max_retries: 2, retry_backoff_seconds: 0.2 }, configuration_snapshot: {}, created_at: createdAt };
const attempt = { id: "item-1", experiment_id: run.id, case_id: "case-internal-1", repetition: 1, attempt: 1, external_run_id: "sdk-1", status: "completed", output: { answer: "订单已发货" }, usage: { input_tokens: 12, output_tokens: 6 }, runtime_metadata: {}, error_type: null, error_message: null, trace_id: "trace-1", evidence_status: "complete", evidence_reasons: [], created_at: createdAt, started_at: createdAt, finished_at: createdAt };
const manifest = { experiment_id: run.id, dataset_version_id: datasetVersion.id, items: [{ case: datasetVersion.cases[0], attempts: [attempt] }], total: 1, offset: 0, limit: 200, next_offset: null };
const traceSummary = { trace_id: "trace-1", run_id: run.id, case_id: "case-1", status: "completed", source: "sdk", span_count: 3, started_at: createdAt, ended_at: createdAt, created_at: createdAt };
const trace = { ...traceSummary, extensions: {}, scores: [{ id: "score-1", experiment_item_id: "item-1", repetition: 1, attempt: 1, metric_name: "tool_selection", evaluator_version_id: evaluator.id, trace_id: "trace-1", span_id: "tool-1", source: "deterministic", status: "passed", value: 1, label: null, passed: true, explanation: "工具选择正确", evidence: [], rubric: null, judge_model: null }], spans: [
  { span_id: "agent-1", trace_id: "trace-1", parent_span_id: null, kind: "agent", name: "sdk task", status: "completed", started_at: createdAt, ended_at: createdAt, input: { order_id: "42" }, output: { answer: "订单已发货" }, error: null, usage: {}, cost: null, attributes: { "agent_eval.execution.origin": "sdk_task", "agent_eval.agent.release": "git:abc123" }, extensions: {} },
  { span_id: "llm-1", trace_id: "trace-1", parent_span_id: "agent-1", kind: "llm", name: "chat", status: "completed", started_at: createdAt, ended_at: createdAt, input: {}, output: {}, error: null, usage: { input_tokens: 12, output_tokens: 6 }, cost: null, attributes: { "gen_ai.request.model": "real-model" }, extensions: {} },
  { span_id: "tool-1", trace_id: "trace-1", parent_span_id: "agent-1", kind: "tool", name: "lookup_order", status: "completed", started_at: createdAt, ended_at: createdAt, input: { order_id: "42" }, output: { status: "shipped" }, error: null, usage: {}, cost: null, attributes: {}, extensions: {} },
] };
const timeline = { trace_id: "trace-1", started_at: createdAt, ended_at: createdAt, spans: trace.spans.map((span, index) => ({ span_id: span.span_id, parent_span_id: span.parent_span_id, kind: span.kind, name: span.name, status: span.status, started_at: createdAt, ended_at: createdAt, duration_ms: 10, depth: index ? 1 : 0 })) };
const experimentPage = (items: unknown[]) => ({ items, total: items.length, offset: 0, limit: 25, next_offset: null });
const tracePage = (items: unknown[]) => ({ items, total: items.length, offset: 0, limit: 25, next_offset: null });

test("SDK Tool Agent MVP primary controls follow one persisted evidence chain", async ({ page }) => {
  let releases: typeof release[] = [];
  let datasets: typeof dataset[] = [];
  let experiments: typeof run[] = [];
  let keys: Array<Record<string, unknown>> = [];
  let datasetCreatePayload: Record<string, unknown> | null = null;
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/agent-releases" && method === "GET") return json(releases);
    if (path === "/projects/project-1/agent-releases" && method === "POST") { releases = [release]; return json(release, 201); }
    if (path === "/projects/project-1/api-keys" && method === "GET") return json(keys);
    if (path === "/projects/project-1/api-keys" && method === "POST") { const key = { id: "key-1", name: "本地 SDK", key_prefix: "aek_project-1_a", active: true, created_at: createdAt, last_used_at: null, key: "aek_project-1_once-only" }; keys = [key]; return json(key, 201); }
    if (path === "/projects/project-1/api-keys/key-1" && method === "DELETE") { keys = keys.map((key) => ({ ...key, active: false })); return route.fulfill({ status: 204 }); }
    if (path === "/projects/project-1/datasets" && method === "GET") return json(datasets);
    if (path === "/projects/project-1/datasets" && method === "POST") { datasetCreatePayload = request.postDataJSON() as Record<string, unknown>; datasets = [dataset]; return json(dataset, 201); }
    if (path === "/projects/project-1/datasets/dataset-1/versions" && method === "GET") return json([datasetVersion]);
    if (path === "/projects/project-1/evaluators" && method === "GET") return json([evaluator]);
    if (path === "/projects/project-1/experiments" && method === "GET") return json(experimentPage(experiments));
    if (path === "/projects/project-1/experiments" && method === "POST") { experiments = [run]; return json({ ...run, status: "queued", completed_cases: 0 }, 201); }
    if (path === "/projects/project-1/experiments/experiment-1" && method === "GET") return json(run);
    if (path === "/projects/project-1/experiments/experiment-1/manifest" && method === "GET") return json(manifest);
    if (path === "/projects/project-1/traces" && method === "GET") return json(tracePage([traceSummary]));
    if (path === "/projects/project-1/traces/trace-1" && method === "GET") return json(trace);
    if (path === "/projects/project-1/traces/trace-1/timeline" && method === "GET") return json(timeline);
    if (path === "/projects/project-1/runs" && method === "GET") return json(experimentPage(experiments));
    if (path === "/projects/project-1/reports" && method === "GET") return json([]);
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Release 与接入", exact: true }).click();
  await page.getByRole("button", { name: "创建 Project Key" }).click();
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByText("aek_project-1_once-only", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "登记 Release", exact: true }).first().click();
  await page.getByLabel("Release 名称").fill("candidate");
  await page.getByLabel("唯一版本标识").fill("git:abc123");
  await page.getByRole("button", { name: "保存 Release", exact: true }).click();
  await expect(page.getByText("git:abc123", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "数据集", exact: true }).click();
  await page.getByLabel("Dataset 名称").fill(dataset.name);
  await page.locator("textarea").first().fill('{"order_id":"42"}');
  await page.locator(".case-table tbody tr").first().locator("textarea").nth(1).fill("订单已发货");
  await page.getByRole("button", { name: "创建 Dataset", exact: true }).click();
  await expect(page.getByText(/Dataset 已创建/)).toBeVisible();
  const submittedDataset = datasetCreatePayload as unknown as {
    cases: Array<Record<string, unknown>>;
  };
  expect(submittedDataset.cases[0].expected_output).toBe("订单已发货");

  await page.getByRole("button", { name: "实验", exact: true }).click();
  await page.getByLabel("实验名称").fill(run.name);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "创建 Experiment", exact: true }).click();
  await expect(page.getByText("证据完整", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Trace", exact: true }).last().click();
  await expect(page).toHaveURL(/view=traces&trace_id=trace-1/);
  await expect(page.getByRole("heading", { name: "trace-1" })).toBeVisible();
  await expect(page.getByText("sdk_task", { exact: true })).toBeVisible();
  await expect(page.getByText("git:abc123", { exact: true })).toBeVisible();
  await expect(page.getByText("real-model", { exact: true })).toBeVisible();
  await expect(page.getByText(/Item item-1 · 重复 1 · 尝试 1/)).toBeVisible();
});

test("acceptance resource IDs open persisted Experiment and Trace details", async ({ page }) => {
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/agent-releases") return json([release]);
    if (path === "/projects/project-1/datasets") return json([dataset]);
    if (path === "/projects/project-1/datasets/dataset-1/versions") return json([datasetVersion]);
    if (path === "/projects/project-1/evaluators") return json([evaluator]);
    if (path === "/projects/project-1/experiments") return json(experimentPage([]));
    if (path === "/projects/project-1/experiments/experiment-1") return json(run);
    if (path === "/projects/project-1/experiments/experiment-1/manifest") return json(manifest);
    if (path === "/projects/project-1/traces") return json(tracePage([traceSummary]));
    if (path === "/projects/project-1/traces/trace-1") return json(trace);
    if (path === "/projects/project-1/traces/trace-1/timeline") return json(timeline);
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: path }) });
  });

  await page.goto("/?view=runs&run_id=experiment-1");
  await expect(page.getByRole("heading", { name: run.name })).toBeVisible();
  await expect(page.getByText(run.id, { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/view=runs&run_id=experiment-1/);

  await page.goto("/?view=traces&trace_id=trace-1");
  await expect(page.getByRole("heading", { name: "trace-1" })).toBeVisible();
  await expect(page.getByText("real-model", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/view=traces&trace_id=trace-1/);
});

test("cancelled SDK Experiment shows every unclaimed Case as terminal", async ({ page }) => {
  const queuedRun = { ...run, status: "queued", completed_cases: 0, failed_cases: 0 };
  const emptyManifest = {
    ...manifest,
    items: [{ case: datasetVersion.cases[0], attempts: [] }],
  };

  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/agent-releases" && method === "GET") return json([release]);
    if (path === "/projects/project-1/datasets" && method === "GET") return json([dataset]);
    if (path === "/projects/project-1/datasets/dataset-1/versions" && method === "GET") return json([datasetVersion]);
    if (path === "/projects/project-1/evaluators" && method === "GET") return json([evaluator]);
    if (path === "/projects/project-1/experiments" && method === "GET") return json(experimentPage([queuedRun]));
    if (path === "/projects/project-1/experiments/experiment-1" && method === "GET") return json(queuedRun);
    if (path === "/projects/project-1/experiments/experiment-1/manifest" && method === "GET") return json(emptyManifest);
    if (path === "/projects/project-1/experiments/experiment-1/cancel" && method === "POST") return json({ ...queuedRun, status: "cancelled" });
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "实验", exact: true }).click();
  await page.getByRole("button", { name: "取消 Experiment", exact: true }).click();
  await expect(page.getByText("Experiment 已取消，未执行此 Case。", { exact: true })).toBeVisible();
  await expect(page.getByText("1 / 1 个 Item 已终态", { exact: true })).toBeVisible();
  await expect(page.getByText("100%", { exact: true })).toBeVisible();
});

test("running SDK Experiment refreshes persisted Item progress", async ({ page }) => {
  const runningRun = { ...run, status: "running", completed_cases: 0, failed_cases: 0 };
  const queuedManifest = {
    ...manifest,
    items: [{ case: datasetVersion.cases[0], attempts: [] }],
  };
  let detailReads = 0;

  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/agent-releases") return json([release]);
    if (path === "/projects/project-1/datasets") return json([dataset]);
    if (path === "/projects/project-1/datasets/dataset-1/versions") return json([datasetVersion]);
    if (path === "/projects/project-1/evaluators") return json([evaluator]);
    if (path === "/projects/project-1/experiments") return json(experimentPage([runningRun]));
    if (path === "/projects/project-1/experiments/experiment-1") {
      detailReads += 1;
      return json(detailReads > 1 ? run : runningRun);
    }
    if (path === "/projects/project-1/experiments/experiment-1/manifest") {
      return json(detailReads > 1 ? manifest : queuedManifest);
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: path }) });
  });

  await page.goto("/?view=runs&run_id=experiment-1");
  await expect(page.locator(".connection-detail .panel-heading .status")).toHaveText("运行中");
  await expect(page.getByText("等待 SDK 领取此 Case。", { exact: true })).toBeVisible();
  await expect(page.getByText("证据完整", { exact: true })).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("1 / 1 个 Item 已终态", { exact: true })).toBeVisible();
  expect(detailReads).toBeGreaterThan(1);
});

test("incomplete Item displays its persisted evidence reasons", async ({ page }) => {
  const incompleteAttempt = {
    ...attempt,
    evidence_status: "incomplete",
    evidence_reasons: ["missing LLM usage", "missing Tool result span"],
  };
  const incompleteRun = { ...run, status: "partial" };
  const incompleteManifest = {
    ...manifest,
    items: [{ case: datasetVersion.cases[0], attempts: [incompleteAttempt] }],
  };

  await page.route("**/projects/default-project/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/agent-releases") return json([release]);
    if (path === "/projects/project-1/datasets") return json([dataset]);
    if (path === "/projects/project-1/datasets/dataset-1/versions") return json([datasetVersion]);
    if (path === "/projects/project-1/evaluators") return json([evaluator]);
    if (path === "/projects/project-1/experiments") return json(experimentPage([incompleteRun]));
    if (path === "/projects/project-1/experiments/experiment-1") return json(incompleteRun);
    if (path === "/projects/project-1/experiments/experiment-1/manifest") return json(incompleteManifest);
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: path }) });
  });

  await page.goto("/?view=runs&run_id=experiment-1");
  await expect(page.getByText("证据不完整", { exact: true })).toBeVisible();
  await expect(page.getByText("missing LLM usage；missing Tool result span", { exact: true })).toBeVisible();
});
