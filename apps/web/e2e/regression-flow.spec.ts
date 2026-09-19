import { expect, test } from "./test-fixture";

const agentVersion = { id: "agent-v1", project_id: "project-1", version: 1, label: "baseline", agent_type: "tool", release_identity: "baseline-sha", source_revision: "baseline-sha", metadata: {}, enabled: true, created_at: "2026-01-01T00:00:00Z" };
const dataset = { id: "dataset-1", name: "Order regression", current_version_id: "dataset-v2" };
const datasetVersion = { id: "dataset-v2", version: 2, cases: [{ id: "cancel-42" }] };
const evaluator = { id: "evaluator-v1", name: "task_success", version: "1.0.0", evaluator_type: "deterministic", supported_agent_types: ["tool"], enabled: true, requires: ["expected_state"] };
const run = { id: "run-1", name: "导入回归", status: "completed", total_cases: 1, completed_cases: 1, failed_cases: 0, agent_version_id: agentVersion.id, dataset_version_id: datasetVersion.id, evaluator_version_ids: [evaluator.id], execution_mode: "sdk_task", evidence_policy: "tool_trajectory_required", baseline_run_id: null, execution_options: { repetitions: 1, concurrency: 4, timeout_seconds: 30, max_retries: 2, retry_backoff_seconds: 0.2 }, configuration_snapshot: {}, created_at: "2026-01-01T00:00:00Z" };
const reportSummary = { run_id: run.id, agent_version_id: agentVersion.id, dataset_version_id: datasetVersion.id, status: "completed", total_cases: 1, completed_cases: 1, failed_cases: 0, created_at: run.created_at, finished_at: run.created_at, metrics: [{ metric_name: "task_success", evaluator_version_id: evaluator.id, valid_count: 1, missing_count: 0, error_count: 0, passed_count: 1, pass_rate: 1, average: 1, aggregation: "pass_rate", threshold: 1, direction: "higher_is_better" }] };
const experimentPage = (items: unknown[]) => ({ items, total: items.length, offset: 0, limit: 25, next_offset: null });

test("imports a dataset, starts an evaluation, and evaluates its regression gate", async ({ page }) => {
  let datasetCreated = false;
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (method === "POST" && path === "/projects/project-1/datasets") {
      datasetCreated = true;
      return json({ ...dataset, current_version_id: "dataset-v1" }, 201);
    }
    if (method === "POST" && path.endsWith("/imports/preview")) return json({ cases: [{ id: "cancel-42", input: "Cancel order 42", expected_output: null }], issues: [] });
    if (method === "POST" && path.endsWith("/imports/commit")) return json({ dataset_version: datasetVersion, issues: [] }, 201);
    if (method === "GET" && path === "/projects/project-1/agent-releases") return json([agentVersion]);
    if (method === "GET" && path === "/projects/project-1/datasets") return json(datasetCreated ? [dataset] : []);
    if (method === "GET" && path === "/projects/project-1/datasets/dataset-1/versions") return json([datasetVersion]);
    if (method === "GET" && path === "/projects/project-1/evaluators") return json([evaluator]);
    if (method === "GET" && path === "/projects/project-1/experiments") return json(experimentPage([]));
    if (method === "POST" && path === "/projects/project-1/experiments") return json(run, 201);
    if (method === "GET" && path === "/projects/project-1/experiments/run-1") return json(run);
    if (method === "GET" && path === "/projects/project-1/experiments/run-1/manifest") return json({ experiment_id: run.id, dataset_version_id: datasetVersion.id, items: [], total: 0, offset: 0, limit: 200, next_offset: null });
    if (method === "GET" && path === "/projects/project-1/reports") return json([reportSummary]);
    if (method === "GET" && path === "/projects/project-1/reports/run-1") return json({ ...reportSummary, generated_at: "2026-01-01T00:00:00Z", matched_cases: 1, cases: [] });
    if (method === "POST" && path === "/projects/project-1/runs/run-1/regression-gate") return json({ run_id: run.id, run_status: "completed", status: "passed", generated_at: "2026-01-01T00:00:00Z", rules: [{ rule: { metric_name: "task_success", aggregation: "pass_rate", minimum: 0.9, require_all_passed: false }, status: "passed", actual_value: 1, valid_count: 1, missing_count: 0, error_count: 0, failed_case_ids: [] }] });
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "数据集", exact: true }).click();
  await page.getByRole("button", { name: "导入文件" }).click();
  await page.getByLabel("Dataset 名称").fill("Order regression");
  await page.locator("#dataset-file").setInputFiles({ name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from("case_key,prompt\ncancel-42,Cancel order 42\n") });
  await page.getByRole("button", { name: "预览导入" }).click();
  await expect(page.getByText("1 条有效用例", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认导入" }).click();
  await expect(page.getByText("导入成功，已创建 Dataset Version 2", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "实验" }).click();
  await page.getByLabel("实验名称").fill(run.name);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "创建 Experiment" }).click();
  await expect(page.getByRole("heading", { name: run.name })).toBeVisible();

  await page.getByRole("button", { name: "发布门禁" }).click();
  await page.getByLabel("指标").selectOption(`task_success::${evaluator.id}`);
  await page.getByRole("button", { name: "评估门禁" }).click();
  await expect(page.getByText("100%")).toBeVisible();
});
