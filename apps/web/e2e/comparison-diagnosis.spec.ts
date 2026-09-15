import { expect, test } from "./test-fixture";

const baselineRun = {
  run_id: "run-baseline",
  status: "completed",
  agent_version_id: "release-baseline",
  dataset_version_id: "dataset-v1",
  total_cases: 1,
  completed_cases: 1,
  failed_cases: 0,
  created_at: "2026-01-01T00:00:00Z",
  finished_at: "2026-01-01T00:01:00Z",
  metrics: [{ metric_name: "task_success", evaluator_version_id: "eval-v1", valid_count: 1, missing_count: 0, error_count: 0, passed_count: 1, average: 1, pass_rate: 1, aggregation: "pass_rate", threshold: 0.9, direction: "higher_is_better" }],
};
const candidateRun = { ...baselineRun, run_id: "run-candidate", agent_version_id: "release-candidate", failed_cases: 1 };
const comparison = {
  dataset_version_id: "dataset-v1",
  baseline_run_id: baselineRun.run_id,
  runs: [
    { ...baselineRun, agent_version: {}, failed_cases: 0 },
    { ...candidateRun, agent_version: {}, failed_cases: 1 },
  ],
  metric_comparisons: [{ metric_name: "task_success", comparable: true, reason: null, points: [
    { run_id: baselineRun.run_id, average: 1, pass_rate: 1, valid_count: 1, missing_count: 0, error_count: 0, passed_count: 1, delta_average: null, delta_pass_rate: null },
    { run_id: candidateRun.run_id, average: 0, pass_rate: 0, valid_count: 1, missing_count: 0, error_count: 0, passed_count: 0, delta_average: -1, delta_pass_rate: -1 },
  ] }],
  case_comparisons: [{
    case_id: "case-order-42",
    metadata: { critical: true },
    critical: true,
    runs: [
      { run_id: baselineRun.run_id, execution_status: "completed", output: { answer: "已发货" }, trace_id: "trace-base", error_type: null, error_message: null, failed: false, scores: [{ id: "score-base", metric_name: "task_success", evaluator_version_id: "eval-v1", trace_id: "trace-base", status: "passed", value: 1, label: null, passed: true, explanation: null, evidence: [], rubric: null, judge_model: null, provenance: null, threshold: 0.9, direction: "higher_is_better", raw_response: null, raw_result: null }] },
      { run_id: candidateRun.run_id, execution_status: "failed", output: { answer: "无法处理" }, trace_id: "trace-candidate", error_type: "tool_error", error_message: "invalid argument", failed: true, scores: [{ id: "score-candidate", metric_name: "task_success", evaluator_version_id: "eval-v1", trace_id: "trace-candidate", status: "failed", value: 0, label: null, passed: false, explanation: "工具参数不正确", evidence: [], rubric: null, judge_model: null, provenance: null, threshold: 0.9, direction: "higher_is_better", raw_response: null, raw_result: null }] },
    ],
    first_error: { category: "tool_arguments", reason: "候选版本第一次偏离发生在工具参数校验。", baseline_trace_id: "trace-base", candidate_trace_id: "trace-candidate", baseline_span_id: "span-base", candidate_span_id: "span-candidate", evidence: [{ field: "order_id", baseline: "42", candidate: "" }] },
  }],
  new_failures: [{ case_id: "case-order-42", run_id: candidateRun.run_id, baseline_run_id: baselineRun.run_id, failed_metrics: ["task_success"], critical: true, first_error: null }],
  recovered_cases: [],
  missing_evidence: [],
  critical_task_impact: [{ candidate_run_id: candidateRun.run_id, critical_case_count: 1, baseline_failed_count: 0, candidate_failed_count: 1, newly_regressed_case_ids: ["case-order-42"], recovered_case_ids: [] }],
  generated_at: "2026-01-01T00:01:00Z",
};

test("BLOCK 门禁可以打开失败 Case 的首错诊断", async ({ page }) => {
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (method === "GET" && path === "/projects/project-1/reports") return json([baselineRun, candidateRun]);
    if (method === "GET" && path === "/projects/project-1/reports/run-baseline") return json({ ...baselineRun, matched_cases: 1, filters: {}, generated_at: baselineRun.created_at, cases: [] });
    if (method === "POST" && path === "/projects/project-1/comparisons") return json(comparison);
    if (method === "POST" && path === "/projects/project-1/runs/run-baseline/regression-gate") return json({ run_id: baselineRun.run_id, run_status: "completed", status: "BLOCK", policy_version: "local-v1", generated_at: baselineRun.created_at, rules: [{ rule: { metric_name: "task_success", aggregation: "pass_rate", operator: "gte", threshold: 0.9, severity: "block", require_all_passed: false }, status: "BLOCK", actual_value: 0, valid_count: 1, missing_count: 0, error_count: 0, failed_case_ids: ["case-order-42"], reason: "task_success 低于发布阈值" }] });
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "回归分析", exact: true }).click();
  await page.getByRole("tab", { name: "比较与门禁" }).click();
  await page.getByRole("checkbox").nth(0).check();
  await page.getByRole("checkbox").nth(1).check();
  await page.getByRole("button", { name: "比较运行", exact: true }).click();
  await expect(page.getByText("新增失败", { exact: true }).first()).toBeVisible();
  await page.getByText("使用 YAML 门禁策略", { exact: true }).click();
  await page.getByPlaceholder("粘贴当前项目实际使用的 YAML 门禁策略").fill("version: test-v1\nrules:\n  - metric: task_success\n    aggregation: pass_rate\n    operator: gte\n    threshold: 0.9\n    severity: block");
  await page.getByRole("button", { name: "评估门禁", exact: true }).click();
  await expect(page.getByText("此结果阻断发布", { exact: true })).toBeVisible();
  await expect(page.getByText("首错诊断：工具参数", { exact: true })).toBeVisible();
  await expect(page.getByText("候选版本第一次偏离发生在工具参数校验。", { exact: true })).toBeVisible();
});

test("比较与门禁忠实展示改善、持平、退化和证据不足状态", async ({ page }) => {
  const candidatePoint = (passRate: number, delta: number) => ({
    run_id: candidateRun.run_id,
    average: passRate,
    pass_rate: passRate,
    valid_count: 1,
    missing_count: 0,
    error_count: 0,
    passed_count: passRate === 1 ? 1 : 0,
    delta_average: delta,
    delta_pass_rate: delta,
  });
  const baselinePoint = (passRate: number) => ({
    run_id: baselineRun.run_id,
    average: passRate,
    pass_rate: passRate,
    valid_count: 1,
    missing_count: 0,
    error_count: 0,
    passed_count: passRate === 1 ? 1 : 0,
    delta_average: null,
    delta_pass_rate: null,
  });
  const evidenceComparison = {
    ...comparison,
    metric_comparisons: [
      { metric_name: "quality_improved", comparable: true, reason: null, points: [baselinePoint(0.5), candidatePoint(1, 0.5)] },
      { metric_name: "quality_tied", comparable: true, reason: null, points: [baselinePoint(1), candidatePoint(1, 0)] },
      { metric_name: "quality_regressed", comparable: true, reason: null, points: [baselinePoint(1), candidatePoint(0, -1)] },
    ],
    case_comparisons: [{
      ...comparison.case_comparisons[0],
      first_error: {
        category: "indeterminate",
        reason: "缺少可对齐的候选工具 Span，无法可靠定位首错。",
        baseline_trace_id: "trace-base",
        candidate_trace_id: null,
        baseline_span_id: "span-base",
        candidate_span_id: null,
        evidence: [{ field: "candidate_trace", candidate: null }],
      },
    }],
    missing_evidence: [{
      run_id: candidateRun.run_id,
      metric_name: "quality_regressed",
      status: "missing",
      case_ids: ["case-order-42"],
    }],
  };
  let gateRequestCount = 0;

  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/projects/default-project/", "/projects/project-1/");
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (method === "GET" && path === "/projects/project-1/reports") return json([baselineRun, candidateRun]);
    if (method === "GET" && path === "/projects/project-1/reports/run-baseline") return json({ ...baselineRun, matched_cases: 1, filters: {}, generated_at: baselineRun.created_at, cases: [] });
    if (method === "POST" && path === "/projects/project-1/comparisons") return json(evidenceComparison);
    if (method === "POST" && path === "/projects/project-1/runs/run-baseline/regression-gate") {
      gateRequestCount += 1;
      const indeterminate = gateRequestCount > 1;
      return json({
        run_id: baselineRun.run_id,
        run_status: "completed",
        status: indeterminate ? "INDETERMINATE" : "INCOMPLETE",
        policy_version: "evidence-v1",
        generated_at: baselineRun.created_at,
        rules: [{
          rule: { metric_name: "quality_regressed", aggregation: "pass_rate", operator: "gte", threshold: 0.9, severity: "block", require_all_passed: false },
          status: indeterminate ? "indeterminate" : "incomplete",
          actual_value: null,
          valid_count: 0,
          missing_count: indeterminate ? 0 : 1,
          error_count: indeterminate ? 1 : 0,
          failed_case_ids: [],
          reason: indeterminate ? "评分执行错误，无法确定发布结论。" : "候选运行缺少必需 Trace 证据。",
        }],
      });
    }
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "回归分析", exact: true }).click();
  await page.getByRole("tab", { name: "比较与门禁" }).click();
  await page.getByRole("checkbox").nth(0).check();
  await page.getByRole("checkbox").nth(1).check();
  await page.getByRole("button", { name: "比较运行", exact: true }).click();

  await expect(page.getByText("改善 +50.0 个百分点", { exact: true })).toBeVisible();
  await expect(page.getByText("持平 +0.0 个百分点", { exact: true })).toBeVisible();
  await expect(page.getByText("退化 -100.0 个百分点", { exact: true })).toBeVisible();
  await expect(page.getByText("缺失证据", { exact: true })).toBeVisible();
  await page.getByText("case-order-42", { exact: true }).last().click();
  await expect(page.getByText("首错诊断：无法确定", { exact: true })).toBeVisible();
  await expect(page.getByText("缺少可对齐的候选工具 Span，无法可靠定位首错。", { exact: true })).toBeVisible();

  await page.getByText("使用 YAML 门禁策略", { exact: true }).click();
  await page.getByPlaceholder("粘贴当前项目实际使用的 YAML 门禁策略").fill("version: evidence-v1\nrules:\n  - metric: quality_regressed\n    aggregation: pass_rate\n    operator: gte\n    threshold: 0.9\n    severity: block");
  await page.getByRole("button", { name: "评估门禁", exact: true }).click();
  await expect(page.getByText("证据不完整", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("候选运行缺少必需 Trace 证据。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "评估门禁", exact: true }).click();
  await expect(page.getByText("无法确定", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("评分执行错误，无法确定发布结论。", { exact: true })).toBeVisible();
});
