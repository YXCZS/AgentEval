from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from agent_eval import CancellationToken, ExperimentRunner, TaskResult
from agent_eval.models import (
    AgentRelease,
    Dataset,
    DatasetCase,
    DatasetSelection,
    DatasetVersion,
    Experiment,
    ExperimentItem,
    ManifestItem,
    ManifestPage,
)

NOW = datetime.now(UTC)


def selection(case_count: int = 3) -> DatasetSelection:
    cases = [DatasetCase(id=f"case-{index}", input={"index": index}) for index in range(case_count)]
    dataset = Dataset(
        id="dataset-1",
        project_id="project-1",
        name="checks",
        current_version_id="version-1",
        created_at=NOW,
        updated_at=NOW,
    )
    version = DatasetVersion(
        id="version-1",
        dataset_id=dataset.id,
        version=1,
        cases=cases,
        created_at=NOW,
    )
    return DatasetSelection(dataset=dataset, version=version)


def release() -> AgentRelease:
    return AgentRelease(
        id="release-1",
        project_id="project-1",
        version=1,
        label="candidate",
        agent_type="tool",
        release_identity="git:abc",
        source_revision="abc",
        enabled=True,
        created_at=NOW,
    )


class FakeClient:
    project_id = "project-1"

    def __init__(self, dataset: DatasetSelection) -> None:
        self.dataset = dataset
        self.items: list[ExperimentItem] = []
        self.traces: list[dict[str, Any]] = []
        self.finalize_calls = 0

    def create_experiment(self, **kwargs: Any) -> Experiment:
        options = kwargs["execution_options"]
        return Experiment(
            id="experiment-1",
            name=kwargs["name"],
            agent_version_id=kwargs["release_id"],
            dataset_version_id=kwargs["dataset_version_id"],
            evaluator_version_ids=kwargs["evaluator_version_ids"],
            execution_mode="sdk_task",
            evidence_policy=kwargs["evidence_policy"],
            status="queued",
            total_cases=len(self.dataset.version.cases) * options["repetitions"],
            completed_cases=0,
            failed_cases=0,
            baseline_run_id=kwargs["baseline_experiment_id"],
            execution_options=options,
            configuration_snapshot={
                "dataset_version": {
                    "id": self.dataset.version.id,
                    "dataset_id": self.dataset.dataset.id,
                }
            },
            created_at=NOW,
            started_at=None,
            finished_at=None,
        )

    def iter_manifest(self, _experiment_id: str) -> list[ManifestItem]:
        return [
            ManifestItem(
                case=case,
                attempts=[item for item in self.items if item.case_id == case.id],
            )
            for case in self.dataset.version.cases
        ]

    def get_manifest_page(self, experiment_id: str, *, limit: int) -> ManifestPage:
        return ManifestPage(
            experiment_id=experiment_id,
            dataset_version_id=self.dataset.version.id,
            items=self.iter_manifest(experiment_id)[:limit],
            total=len(self.dataset.version.cases),
            offset=0,
            limit=limit,
            next_offset=None,
        )

    def get_experiment(self, _experiment_id: str) -> Experiment:
        return self.create_experiment(
            name="resumed",
            release_id="release-1",
            dataset_version_id="version-1",
            evaluator_version_ids=["eval-1"],
            evidence_policy="trace_required",
            baseline_experiment_id=None,
            execution_options={
                "repetitions": 1,
                "concurrency": 1,
                "timeout_seconds": 1,
                "max_retries": 0,
                "retry_backoff_seconds": 0,
            },
        )

    def start_item(self, experiment_id: str, **kwargs: Any) -> ExperimentItem:
        item = ExperimentItem(
            id=f"item-{len(self.items) + 1}",
            experiment_id=experiment_id,
            case_id=kwargs["case_id"],
            repetition=kwargs["repetition"],
            attempt=kwargs["attempt"],
            external_run_id=kwargs["external_run_id"],
            status="running",
            runtime_metadata=kwargs["runtime_metadata"],
            created_at=NOW,
            started_at=NOW,
        )
        self.items.append(item)
        return item

    def ingest_trace(self, trace: dict[str, Any]) -> str:
        self.traces.append(trace)
        return str(trace["trace_id"])

    def complete_item(self, _experiment_id: str, item_id: str, **kwargs: Any) -> ExperimentItem:
        item = self._item(item_id)
        updated = item.model_copy(
            update={"status": "completed", "finished_at": NOW, **kwargs}
        )
        self._replace(updated)
        return updated

    def fail_item(self, _experiment_id: str, item_id: str, **kwargs: Any) -> ExperimentItem:
        item = self._item(item_id)
        updated = item.model_copy(update={"status": "failed", "finished_at": NOW, **kwargs})
        self._replace(updated)
        return updated

    def cancel_item(self, _experiment_id: str, item_id: str) -> ExperimentItem:
        item = self._item(item_id)
        updated = item.model_copy(update={"status": "cancelled", "finished_at": NOW})
        self._replace(updated)
        return updated

    def finalize_experiment(self, _experiment_id: str) -> Experiment:
        self.finalize_calls += 1
        self.items = [
            item.model_copy(
                update={
                    "evidence_status": (
                        "complete" if item.status == "completed" else "not_required"
                    ),
                    "evidence_reasons": [],
                }
            )
            for item in self.items
        ]
        experiment = self.get_experiment("experiment-1")
        completed = sum(item.status == "completed" for item in self.items)
        failed = sum(item.status in {"failed", "cancelled"} for item in self.items)
        return experiment.model_copy(
            update={
                "status": "completed" if failed == 0 else "partial",
                "completed_cases": completed,
                "failed_cases": failed,
            }
        )

    def _item(self, item_id: str) -> ExperimentItem:
        return next(item for item in self.items if item.id == item_id)

    def _replace(self, updated: ExperimentItem) -> None:
        self.items = [updated if item.id == updated.id else item for item in self.items]


def test_sync_runner_isolates_errors_reports_progress_and_uploads_real_spans() -> None:
    dataset = selection()
    client = FakeClient(dataset)
    progress: list[tuple[str, str]] = []

    def task(case: DatasetCase) -> TaskResult:
        if case.id == "case-1":
            raise RuntimeError("one case failed")
        return TaskResult(output={"answer": case.id}, usage={"input_tokens": 2})

    result = ExperimentRunner(client).run(  # type: ignore[arg-type]
        dataset=dataset,
        task=task,
        release=release(),
        evaluator_version_ids=["eval-1"],
        name="sync",
        progress=lambda event: progress.append((event.case_id, event.status)),
    )

    assert [item.status for item in result.items] == ["completed", "failed", "completed"]
    assert [item.evidence_status for item in result.items] == [
        "complete",
        "not_required",
        "complete",
    ]
    assert len(progress) == 3
    assert result.experiment.status == "partial"
    assert len(client.traces) == 2
    root = client.traces[0]["spans"][-1]
    expected_root_attributes = {
        "agent_eval.project.id": "project-1",
        "agent_eval.experiment.id": "experiment-1",
        "agent_eval.experiment.item.id": "item-1",
        "agent_eval.dataset.id": "dataset-1",
        "agent_eval.dataset.version.id": "version-1",
        "agent_eval.case.id": "case-0",
        "agent_eval.agent.release": "git:abc",
        "agent_eval.execution.origin": "sdk_task",
        "agent_eval.repetition": 1,
        "openinference.span.kind": "AGENT",
    }
    assert expected_root_attributes.items() <= root["attributes"].items()
    assert root["parent_span_id"] is None


def test_async_runner_obeys_bounded_concurrency_and_case_association() -> None:
    dataset = selection(5)
    client = FakeClient(dataset)
    active = 0
    maximum = 0
    lock = Lock()

    async def task(case: DatasetCase) -> dict[str, str]:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        with lock:
            active -= 1
        return {"case": case.id}

    result = asyncio.run(
        ExperimentRunner(client).arun(  # type: ignore[arg-type]
            dataset=dataset,
            task=task,
            release=release(),
            evaluator_version_ids=["eval-1"],
            name="async",
            max_concurrency=2,
        )
    )

    assert maximum == 2
    assert {item.case_id: item.output for item in result.items} == {
        f"case-{index}": {"case": f"case-{index}"} for index in range(5)
    }


def test_timeout_retry_sanitization_cancellation_resume_and_flush_confirmation() -> None:
    dataset = selection(1)
    client = FakeClient(dataset)
    attempts = 0

    def failing(_case: DatasetCase) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("authorization=sk_secret_value_123456")

    result = ExperimentRunner(client).run(  # type: ignore[arg-type]
        dataset=dataset,
        task=failing,
        release=release(),
        evaluator_version_ids=["eval-1"],
        name="retry",
        max_retries=1,
        retry_backoff_seconds=0,
    )
    assert attempts == 2
    assert [item.attempt for item in client.items] == [1, 2]
    assert "sk_secret" not in (result.items[0].error_message or "")

    cancelled_client = FakeClient(dataset)
    token = CancellationToken()
    token.cancel()
    cancelled = ExperimentRunner(cancelled_client).run(  # type: ignore[arg-type]
        dataset=dataset,
        task=lambda case: case.id,
        release=release(),
        evaluator_version_ids=["eval-1"],
        name="cancelled",
        cancellation=token,
    )
    assert cancelled.items[0].status == "cancelled"

    resumed_client = FakeClient(dataset)
    already_done = resumed_client.start_item(
        "experiment-1",
        case_id="case-0",
        repetition=1,
        attempt=1,
        external_run_id="old",
        runtime_metadata={},
    )
    resumed_client.complete_item("experiment-1", already_done.id, output={"old": True})
    resumed = ExperimentRunner(resumed_client).run(  # type: ignore[arg-type]
        dataset=dataset,
        task=lambda _case: {"new": True},
        release=release(),
        evaluator_version_ids=["eval-1"],
        name="resume",
        resume_experiment_id="experiment-1",
    )
    assert resumed.items == []

    flush_client = FakeClient(dataset)
    runner = ExperimentRunner(flush_client)  # type: ignore[arg-type]
    runner.telemetry.force_flush = lambda timeout_millis=10000: False  # type: ignore[method-assign]
    try:
        runner.run(
            dataset=dataset,
            task=lambda case: case.id,
            release=release(),
            evaluator_version_ids=["eval-1"],
            name="flush",
        )
    except RuntimeError as exc:
        assert "not finalized" in str(exc)
    else:
        raise AssertionError("flush failure must stop finalization")
    assert flush_client.finalize_calls == 0


def test_sync_timeout_fails_without_waiting_for_the_slow_task() -> None:
    dataset = selection(1)
    client = FakeClient(dataset)

    def slow(_case: DatasetCase) -> str:
        time.sleep(0.3)
        return "late"

    started = time.monotonic()
    result = ExperimentRunner(client).run(  # type: ignore[arg-type]
        dataset=dataset,
        task=slow,
        release=release(),
        evaluator_version_ids=["eval-1"],
        name="timeout",
        timeout_seconds=0.02,
    )
    assert time.monotonic() - started < 0.2
    assert result.items[0].error_type == "TimeoutError"


def test_user_instrumentation_stays_nested_and_preserves_openinference_kinds() -> None:
    dataset = selection(1)
    client = FakeClient(dataset)
    user_tracer = trace.get_tracer("real-user-agent")

    def instrumented_task(case: DatasetCase) -> TaskResult:
        for kind in ["CHAIN", "LLM", "RETRIEVER", "TOOL", "TOOL_RESULT"]:
            with user_tracer.start_as_current_span(kind.lower()) as span:
                span.set_attribute("openinference.span.kind", kind)
                if kind == "LLM":
                    span.set_attribute("gen_ai.request.model", "real-model")
                    span.set_attribute("agent_eval.usage.present", True)
                if kind == "RETRIEVER":
                    span.set_attribute("retrieval.document_ids", ["doc-1"])
        with user_tracer.start_as_current_span("handled-error") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_status(Status(StatusCode.ERROR, "handled tool error"))
        return TaskResult(output={"case": case.id}, usage={"input_tokens": 1})

    ExperimentRunner(client).run(  # type: ignore[arg-type]
        dataset=dataset,
        task=instrumented_task,
        release=release(),
        evaluator_version_ids=["eval-1"],
        name="instrumented",
    )

    spans = client.traces[0]["spans"]
    root = next(span for span in spans if span["parent_span_id"] is None)
    children = [span for span in spans if span["parent_span_id"] == root["span_id"]]
    assert {span["attributes"].get("openinference.span.kind") for span in children} == {
        "CHAIN",
        "LLM",
        "RETRIEVER",
        "TOOL",
        "TOOL_RESULT",
    }
    assert {span["kind"] for span in children} >= {
        "agent",
        "llm",
        "retrieval",
        "tool",
        "tool_result",
    }
    assert next(span for span in children if span["name"] == "handled-error")["status"] == (
        "failed"
    )
