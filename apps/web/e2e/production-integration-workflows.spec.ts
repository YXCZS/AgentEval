import { expect, test } from "./test-fixture";

const createdAt = "2026-01-01T00:00:00Z";
const release = { id: "release-1", version: 1, label: "remote-candidate", agent_type: "tool", release_identity: "git:remote123", enabled: true };
const dataset = { id: "dataset-1", name: "远程评测集", description: "真实接入用例", current_version_id: "dataset-version-1", created_at: createdAt, updated_at: createdAt };
const version = { id: "dataset-version-1", dataset_id: dataset.id, version: 1, cases: [{ id: "case-1", input: { question: "状态？" }, metadata: {} }], metadata: {}, created_at: createdAt };
const evaluator = { id: "evaluator-1", name: "task_success", version: "1.0.0", evaluator_type: "deterministic", supported_agent_types: ["tool"], enabled: true, requires: [] };

function run(mode: "otel" | "remote_upload" | "remote_trigger") {
  return { id: `experiment-${mode}`, name: `${mode} experiment`, status: "queued", total_cases: 1, completed_cases: 0, failed_cases: 0, agent_version_id: release.id, dataset_version_id: version.id, evaluator_version_ids: [evaluator.id], execution_mode: mode, evidence_policy: "trace_required", baseline_run_id: null, execution_options: { repetitions: 1, concurrency: 1, timeout_seconds: 30, max_retries: 0, retry_backoff_seconds: 0.2 }, configuration_snapshot: {}, created_at: createdAt };
}
const experimentPage = (items: unknown[]) => ({ items, total: items.length, offset: 0, limit: 25, next_offset: null });

test("Experiment sends the selected OTel or Remote Upload execution mode and only renders secret-free configuration", async ({ page }) => {
  const submittedModes: string[] = [];
  const createdRuns: ReturnType<typeof run>[] = [];
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/agent-releases" && method === "GET") return json([release]);
    if (path === "/projects/project-1/datasets" && method === "GET") return json([dataset]);
    if (path === "/projects/project-1/datasets/dataset-1/versions" && method === "GET") return json([version]);
    if (path === "/projects/project-1/evaluators" && method === "GET") return json([evaluator]);
    if (path === "/projects/project-1/experiments" && method === "GET") return json(experimentPage(createdRuns));
    if (path === "/projects/project-1/experiments" && method === "POST") {
      const payload = request.postDataJSON() as { execution_mode: "otel" | "remote_upload" | "remote_trigger" };
      submittedModes.push(payload.execution_mode);
      const created = run(payload.execution_mode);
      createdRuns.unshift(created);
      return json(created, 201);
    }
    const experimentMatch = path.match(/^\/projects\/project-1\/experiments\/(experiment-(?:otel|remote_upload|remote_trigger))(?:\/manifest)?$/);
    if (experimentMatch && method === "GET") {
      const current = createdRuns.find((item) => item.id === experimentMatch[1]) ?? run("otel");
      return path.endsWith("/manifest") ? json({ experiment_id: current.id, dataset_version_id: version.id, items: [{ case: version.cases[0], attempts: [] }], total: 1, offset: 0, limit: 200, next_offset: null }) : json(current);
    }
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/?view=runs");
  await page.getByLabel("实验名称").fill("OTel 实验");
  await page.getByLabel("OpenTelemetry").check();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "创建 Experiment", exact: true }).click();
  await expect(page.getByRole("group").getByText("OpenTelemetry", { exact: true })).toBeVisible();
  expect(submittedModes).toEqual(["otel"]);

  await page.getByLabel("实验名称").fill("Remote Upload 实验");
  await page.getByLabel("Remote Upload").check();
  await page.getByRole("button", { name: "创建 Experiment", exact: true }).click();
  await expect(page.locator(".integration-snippet")).toContainText("X-Project-Key");
  await expect(page.locator(".integration-snippet")).toContainText("$AGENT_EVAL_PROJECT_KEY");
  expect(await page.locator(".integration-snippet").textContent()).not.toContain("aek_");
  expect(submittedModes).toEqual(["otel", "remote_upload"]);
});

test("Dataset Remote Trigger exposes a one-time signing secret but never returns it after refresh", async ({ page }) => {
  let triggerCreated = false;
  const trigger = { id: "trigger-1", project_id: "project-1", dataset_id: dataset.id, trigger_url: "https://runner.example.com/agent-eval/trigger", enabled: true, signature_header: "X-Agent-Eval-Trigger-Signature", secret_mask: "aet_***test", secret_key_id: "key-1", created_at: createdAt, updated_at: createdAt };
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects/project-1/datasets" && method === "GET") return json([dataset]);
    if (path === "/projects/project-1/datasets/dataset-1/versions" && method === "GET") return json([version]);
    if (path === "/projects/project-1/datasets/dataset-1/remote-trigger" && method === "GET") return triggerCreated ? json(trigger) : json({ detail: "remote trigger not found" }, 404);
    if (path === "/projects/project-1/datasets/dataset-1/remote-trigger" && method === "POST") { triggerCreated = true; return json({ ...trigger, signing_secret: "created-once-test-value" }, 201); }
    if (path === "/projects/project-1/datasets/dataset-1/remote-trigger/deliveries" && method === "GET") return json([]);
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/?view=datasets");
  await page.getByLabel("远程运行器 URL").fill(trigger.trigger_url);
  await page.getByRole("button", { name: "创建 Trigger", exact: true }).click();
  await expect(page.getByText("created-once-test-value", { exact: true })).toBeVisible();
  await expect(page.locator(".integration-snippet")).toContainText("AGENT_EVAL_TRIGGER_SECRET=<创建 Trigger 后保存的一次性密钥>");
  await page.getByRole("button", { name: "刷新", exact: true }).last().click();
  await expect(page.getByText("created-once-test-value", { exact: true })).toHaveCount(0);
  await expect(page.getByText(trigger.secret_mask, { exact: true })).toBeVisible();
});
