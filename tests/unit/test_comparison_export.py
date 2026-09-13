from datetime import UTC, datetime

from agent_eval_api.comparison_export import (
    export_comparison_json,
    export_comparison_markdown,
)
from agent_eval_api.contracts import (
    CaseComparison,
    CaseComparisonChange,
    CaseComparisonRun,
    ComparisonMetricPoint,
    ComparisonRun,
    EvaluationComparison,
    ExecutionStatus,
    FirstErrorAttribution,
    MetricComparison,
    RegressionGateResult,
    RegressionGateRule,
    RegressionGateRuleResult,
    RegressionGateStatus,
    RunStatus,
)


def _comparison() -> EvaluationComparison:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    baseline_id = "baseline-run"
    candidate_id = "candidate-run"
    diagnosis = FirstErrorAttribution(
        category="tool_arguments",
        reason="the first shared tool receives different arguments",
        baseline_trace_id="trace-baseline",
        candidate_trace_id="trace-candidate",
        baseline_span_id="span-baseline",
        candidate_span_id="span-candidate",
    )
    return EvaluationComparison(
        dataset_version_id="dataset-v1",
        baseline_run_id=baseline_id,
        runs=[
            ComparisonRun(
                run_id=baseline_id,
                agent_version_id="agent-v1",
                dataset_version_id="dataset-v1",
                status=RunStatus.COMPLETED,
                total_cases=1,
                completed_cases=1,
                failed_cases=0,
                created_at=created_at,
            ),
            ComparisonRun(
                run_id=candidate_id,
                agent_version_id="agent-v2",
                dataset_version_id="dataset-v1",
                status=RunStatus.COMPLETED,
                total_cases=1,
                completed_cases=1,
                failed_cases=1,
                created_at=created_at,
            ),
        ],
        metric_comparisons=[
            MetricComparison(
                metric_name="task_success",
                evaluator_version_ids=["evaluator-v1"],
                comparable=True,
                points=[
                    ComparisonMetricPoint(
                        run_id=baseline_id,
                        average=1.0,
                        pass_rate=1.0,
                        valid_count=1,
                        passed_count=1,
                    ),
                    ComparisonMetricPoint(
                        run_id=candidate_id,
                        average=0.0,
                        pass_rate=0.0,
                        valid_count=1,
                        passed_count=0,
                        delta_average=-1.0,
                        delta_pass_rate=-1.0,
                    ),
                ],
            )
        ],
        case_comparisons=[
            CaseComparison(
                case_id="order|42",
                runs=[
                    CaseComparisonRun(
                        run_id=baseline_id,
                        execution_status=ExecutionStatus.COMPLETED,
                        trace_id="trace-baseline",
                        failed=False,
                    ),
                    CaseComparisonRun(
                        run_id=candidate_id,
                        execution_status=ExecutionStatus.COMPLETED,
                        trace_id="trace-candidate",
                        failed=True,
                    ),
                ],
                first_error=diagnosis,
            )
        ],
        new_failures=[
            CaseComparisonChange(
                case_id="order|42",
                run_id=candidate_id,
                baseline_run_id=baseline_id,
                failed_metrics=["task_success"],
                first_error=diagnosis,
            )
        ],
        generated_at=created_at,
    )


def _gate() -> RegressionGateResult:
    rule = RegressionGateRule(
        metric_name="task_success",
        operator="gte",
        threshold=0.9,
        severity="block",
    )
    return RegressionGateResult(
        run_id="candidate-run",
        run_status=RunStatus.COMPLETED,
        status=RegressionGateStatus.BLOCK,
        rules=[
            RegressionGateRuleResult(
                rule=rule,
                status=RegressionGateStatus.BLOCK,
                actual_value=0.0,
                reason="metric value is outside the configured threshold",
            )
        ],
        policy_version="release-v1",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_json_artifact_contains_comparison_and_gate() -> None:
    document = export_comparison_json(_comparison(), _gate())

    assert '"artifact_type": "agent-eval.comparison"' in document
    assert '"candidate_run_id": "candidate-run"' in document
    assert '"status": "BLOCK"' in document
    assert '"candidate_span_id": "span-candidate"' in document


def test_markdown_artifact_links_cases_runs_and_traces() -> None:
    document = export_comparison_markdown(_comparison(), _gate())

    assert "**Status: `BLOCK`**" in document
    assert "[order\\|42](#case-order-42)" in document
    assert "[candidate-run](#run-candidate-run)" in document
    assert "[trace-candidate](#trace-trace-candidate)" in document
    assert "`tool_arguments`" in document
    assert "`span-candidate`" in document
