"""Execute real user callbacks and persist each attempt through the control plane."""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from opentelemetry.trace import Status, StatusCode, use_span

from .client import Client
from .models import AgentRelease, DatasetCase, DatasetSelection, Experiment, ExperimentItem
from .telemetry import TelemetrySession

Task = Callable[[DatasetCase], Any]
AsyncTask = Callable[[DatasetCase], Awaitable[Any]]
ProgressCallback = Callable[["ProgressEvent"], None]


@dataclass(frozen=True)
class TaskResult:
    output: Any
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProgressEvent:
    completed: int
    total: int
    case_id: str
    status: str


@dataclass(frozen=True)
class ExperimentResult:
    experiment: Experiment
    items: list[ExperimentItem]


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


def safe_error(exc: Exception) -> tuple[str, str]:
    message = str(exc)
    message = re.sub(r"(?i)(api[_-]?key|authorization|token|secret|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    message = re.sub(r"\b(?:sk|aek)_[A-Za-z0-9_-]{8,}\b", "[REDACTED]", message)
    return type(exc).__name__[:100], message[:2000] or "task failed without an error message"


class ExperimentRunner:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.telemetry = TelemetrySession()

    def run(
        self,
        *,
        dataset: DatasetSelection,
        task: Task,
        release: AgentRelease,
        evaluator_version_ids: list[str],
        name: str,
        evidence_policy: str = "trace_required",
        baseline_experiment_id: str | None = None,
        max_concurrency: int = 1,
        repetitions: int = 1,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.2,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
        resume_experiment_id: str | None = None,
    ) -> ExperimentResult:
        if inspect.iscoroutinefunction(task):
            raise TypeError("async task passed to run(); use arun()")
        experiment = self._experiment(
            dataset=dataset,
            release=release,
            evaluator_version_ids=evaluator_version_ids,
            name=name,
            evidence_policy=evidence_policy,
            baseline_experiment_id=baseline_experiment_id,
            max_concurrency=max_concurrency,
            repetitions=repetitions,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            resume_experiment_id=resume_experiment_id,
        )
        work = self._pending_work(experiment.id, repetitions)
        items: list[ExperimentItem] = []
        total = len(work)

        def execute(entry: tuple[DatasetCase, int, int]) -> ExperimentItem:
            case, repetition, first_attempt = entry
            return self._run_sync_case(
                experiment,
                release,
                case,
                repetition,
                first_attempt,
                task,
                timeout_seconds,
                max_retries,
                retry_backoff_seconds,
                cancellation,
            )

        if max_concurrency == 1:
            for entry in work:
                item = execute(entry)
                items.append(item)
                self._progress(progress, len(items), total, item)
        else:
            with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
                for item in pool.map(execute, work):
                    items.append(item)
                    self._progress(progress, len(items), total, item)
        if not self.telemetry.force_flush():
            raise RuntimeError("OpenTelemetry flush failed; Experiment was not finalized")
        finalized = self._finalize(experiment.id, timeout_seconds)
        return ExperimentResult(
            experiment=finalized,
            items=self._refresh_finalized_items(experiment.id, items),
        )

    async def arun(
        self,
        *,
        dataset: DatasetSelection,
        task: AsyncTask,
        release: AgentRelease,
        evaluator_version_ids: list[str],
        name: str,
        evidence_policy: str = "trace_required",
        max_concurrency: int = 4,
        repetitions: int = 1,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.2,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ExperimentResult:
        if not inspect.iscoroutinefunction(task):
            raise TypeError("sync task passed to arun(); use run()")
        experiment = self._experiment(
            dataset=dataset,
            release=release,
            evaluator_version_ids=evaluator_version_ids,
            name=name,
            evidence_policy=evidence_policy,
            baseline_experiment_id=None,
            max_concurrency=max_concurrency,
            repetitions=repetitions,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            resume_experiment_id=None,
        )
        work = self._pending_work(experiment.id, repetitions)
        semaphore = asyncio.Semaphore(max_concurrency)
        completed = 0
        lock = asyncio.Lock()

        async def execute(entry: tuple[DatasetCase, int, int]) -> ExperimentItem:
            nonlocal completed
            async with semaphore:
                case, repetition, first_attempt = entry
                item = await self._run_async_case(
                    experiment,
                    release,
                    case,
                    repetition,
                    first_attempt,
                    task,
                    timeout_seconds,
                    max_retries,
                    retry_backoff_seconds,
                    cancellation,
                )
            async with lock:
                completed += 1
                self._progress(progress, completed, len(work), item)
            return item

        items = list(await asyncio.gather(*(execute(entry) for entry in work)))
        if not self.telemetry.force_flush():
            raise RuntimeError("OpenTelemetry flush failed; Experiment was not finalized")
        finalized = await asyncio.to_thread(
            self._finalize, experiment.id, timeout_seconds
        )
        return ExperimentResult(
            experiment=finalized,
            items=self._refresh_finalized_items(experiment.id, items),
        )

    def _refresh_finalized_items(
        self,
        experiment_id: str,
        items: list[ExperimentItem],
    ) -> list[ExperimentItem]:
        """Return server-derived evidence without changing this run's item ordering."""

        if not items:
            return []
        finalized_by_id = {
            attempt.id: attempt
            for manifest_item in self.client.iter_manifest(experiment_id)
            for attempt in manifest_item.attempts
        }
        missing = [item.id for item in items if item.id not in finalized_by_id]
        if missing:
            raise RuntimeError(
                "finalized Experiment manifest is missing executed Item IDs: "
                + ", ".join(missing)
            )
        return [finalized_by_id[item.id] for item in items]

    def _finalize(self, experiment_id: str, task_timeout_seconds: float) -> Experiment:
        """Wait for server-side evaluators while leaving timed-out runs resumable."""

        deadline = time.monotonic() + max(300.0, task_timeout_seconds * 3)
        delay = 0.2
        experiment = self.client.finalize_experiment(experiment_id)
        while experiment.status in {"queued", "running"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "server-side evaluators did not finish before the SDK deadline; "
                    f"Experiment {experiment_id} remains resumable"
                )
            time.sleep(delay)
            experiment = self.client.finalize_experiment(experiment_id)
            delay = min(delay * 1.5, 2.0)
        return experiment

    def _experiment(self, **options: Any) -> Experiment:
        resume_experiment_id = options.pop("resume_experiment_id")
        if resume_experiment_id is not None:
            page = self.client.get_manifest_page(resume_experiment_id, limit=1)
            if page.dataset_version_id != options["dataset"].version.id:
                raise ValueError("resume Experiment uses a different Dataset version")
            return self.client.get_experiment(resume_experiment_id)
        dataset = options.pop("dataset")
        release = options.pop("release")
        return self.client.create_experiment(
            dataset_version_id=dataset.version.id,
            release_id=release.id,
            execution_options={
                "concurrency": options.pop("max_concurrency"),
                "repetitions": options.pop("repetitions"),
                "timeout_seconds": options.pop("timeout_seconds"),
                "max_retries": options.pop("max_retries"),
                "retry_backoff_seconds": options.pop("retry_backoff_seconds"),
            },
            **options,
        )

    def _pending_work(
        self, experiment_id: str, repetitions: int
    ) -> list[tuple[DatasetCase, int, int]]:
        work: list[tuple[DatasetCase, int, int]] = []
        for manifest_item in self.client.iter_manifest(experiment_id):
            for repetition in range(1, repetitions + 1):
                attempts = [
                    item for item in manifest_item.attempts if item.repetition == repetition
                ]
                latest = max(attempts, key=lambda item: item.attempt, default=None)
                if latest is not None and latest.status == "completed":
                    continue
                work.append(
                    (
                        manifest_item.case,
                        repetition,
                        1 if latest is None else latest.attempt + 1,
                    )
                )
        return work

    def _run_sync_case(
        self,
        experiment: Experiment,
        release: AgentRelease,
        case: DatasetCase,
        repetition: int,
        first_attempt: int,
        task: Task,
        timeout_seconds: float,
        max_retries: int,
        backoff: float,
        cancellation: CancellationToken | None,
    ) -> ExperimentItem:
        last_item: ExperimentItem | None = None
        for attempt in range(first_attempt, first_attempt + max_retries + 1):
            if cancellation is not None and cancellation.cancelled:
                return self._cancel_before_call(experiment, case, repetition, attempt)
            item = self._start(experiment, case, repetition, attempt)
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                result = executor.submit(
                    self._invoke_traced, experiment, release, item, case, repetition, task
                ).result(timeout=timeout_seconds)
                return self._complete(experiment, item, result)
            except FutureTimeoutError:
                error: Exception = TimeoutError(
                    f"task exceeded {timeout_seconds:g} second timeout"
                )
            except Exception as exc:  # noqa: BLE001 - isolate arbitrary user task failures
                error = exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            last_item = self._fail(experiment, item, error)
            if attempt < first_attempt + max_retries:
                time.sleep(backoff * (2 ** (attempt - first_attempt)))
        assert last_item is not None
        return last_item

    async def _run_async_case(
        self,
        experiment: Experiment,
        release: AgentRelease,
        case: DatasetCase,
        repetition: int,
        first_attempt: int,
        task: AsyncTask,
        timeout_seconds: float,
        max_retries: int,
        backoff: float,
        cancellation: CancellationToken | None,
    ) -> ExperimentItem:
        last_item: ExperimentItem | None = None
        for attempt in range(first_attempt, first_attempt + max_retries + 1):
            if cancellation is not None and cancellation.cancelled:
                return self._cancel_before_call(experiment, case, repetition, attempt)
            item = self._start(experiment, case, repetition, attempt)
            try:
                result = await asyncio.wait_for(
                    self._invoke_async_traced(
                        experiment, release, item, case, repetition, task
                    ),
                    timeout=timeout_seconds,
                )
                return self._complete(experiment, item, result)
            except Exception as exc:  # noqa: BLE001 - isolate arbitrary user task failures
                last_item = self._fail(experiment, item, exc)
            if attempt < first_attempt + max_retries:
                await asyncio.sleep(backoff * (2 ** (attempt - first_attempt)))
        assert last_item is not None
        return last_item

    def _start(
        self, experiment: Experiment, case: DatasetCase, repetition: int, attempt: int
    ) -> ExperimentItem:
        return self.client.start_item(
            experiment.id,
            case_id=case.id,
            repetition=repetition,
            attempt=attempt,
            external_run_id=str(uuid.uuid4()),
            runtime_metadata={"sdk_version": "0.1.0", "python": sys.version.split()[0]},
        )

    def _root_span(
        self,
        experiment: Experiment,
        release: AgentRelease,
        item: ExperimentItem,
        case: DatasetCase,
        repetition: int,
    ) -> Any:
        span = self.telemetry.tracer.start_span(f"agent-eval.task.{case.id}")
        attributes = {
            "agent_eval.project.id": self.client.project_id,
            "agent_eval.experiment.id": experiment.id,
            "agent_eval.experiment.item.id": item.id,
            "agent_eval.dataset.id": experiment.configuration_snapshot["dataset_version"][
                "dataset_id"
            ],
            "agent_eval.dataset.version.id": experiment.dataset_version_id,
            "agent_eval.case.id": case.id,
            "agent_eval.agent.release": release.release_identity,
            "agent_eval.execution.origin": "sdk_task",
            "agent_eval.repetition": repetition,
            "openinference.span.kind": "AGENT",
        }
        for key, value in attributes.items():
            span.set_attribute(key, value)
        self.telemetry.set_json_attribute(span, "agent_eval.input", case.input)
        return span

    def _invoke_traced(
        self,
        experiment: Experiment,
        release: AgentRelease,
        item: ExperimentItem,
        case: DatasetCase,
        repetition: int,
        task: Task,
    ) -> tuple[TaskResult, dict[str, Any]]:
        span = self._root_span(experiment, release, item, case, repetition)
        with use_span(span, end_on_exit=True):
            try:
                raw = task(case)
                result = raw if isinstance(raw, TaskResult) else TaskResult(output=raw)
                self.telemetry.set_json_attribute(span, "agent_eval.output", result.output)
                self.telemetry.set_json_attribute(span, "agent_eval.usage", result.usage)
            except Exception as exc:
                error_type, error_message = safe_error(exc)
                self.telemetry.set_json_attribute(
                    span,
                    "agent_eval.error",
                    {"type": error_type, "message": error_message},
                )
                span.set_status(Status(StatusCode.ERROR, error_type))
                raise
        return result, self.telemetry.trace_payload(
            root_span=span, experiment_id=experiment.id, case_id=case.id
        )

    async def _invoke_async_traced(
        self,
        experiment: Experiment,
        release: AgentRelease,
        item: ExperimentItem,
        case: DatasetCase,
        repetition: int,
        task: AsyncTask,
    ) -> tuple[TaskResult, dict[str, Any]]:
        span = self._root_span(experiment, release, item, case, repetition)
        with use_span(span, end_on_exit=True):
            try:
                raw = await task(case)
                result = raw if isinstance(raw, TaskResult) else TaskResult(output=raw)
                self.telemetry.set_json_attribute(span, "agent_eval.output", result.output)
                self.telemetry.set_json_attribute(span, "agent_eval.usage", result.usage)
            except Exception as exc:
                error_type, error_message = safe_error(exc)
                self.telemetry.set_json_attribute(
                    span,
                    "agent_eval.error",
                    {"type": error_type, "message": error_message},
                )
                span.set_status(Status(StatusCode.ERROR, error_type))
                raise
        return result, self.telemetry.trace_payload(
            root_span=span, experiment_id=experiment.id, case_id=case.id
        )

    def _complete(
        self,
        experiment: Experiment,
        item: ExperimentItem,
        result_and_trace: tuple[TaskResult, dict[str, Any]],
    ) -> ExperimentItem:
        result, trace_payload = result_and_trace
        public_trace_id = self.client.ingest_trace(trace_payload)
        return self.client.complete_item(
            experiment.id,
            item.id,
            output=result.output,
            usage=result.usage,
            runtime_metadata=result.metadata,
            trace_id=public_trace_id,
        )

    def _fail(
        self, experiment: Experiment, item: ExperimentItem, exc: Exception
    ) -> ExperimentItem:
        error_type, error_message = safe_error(exc)
        return self.client.fail_item(
            experiment.id,
            item.id,
            error_type=error_type,
            error_message=error_message,
        )

    def _cancel_before_call(
        self, experiment: Experiment, case: DatasetCase, repetition: int, attempt: int
    ) -> ExperimentItem:
        item = self._start(experiment, case, repetition, attempt)
        return self.client.cancel_item(experiment.id, item.id)

    @staticmethod
    def _progress(
        callback: ProgressCallback | None,
        completed: int,
        total: int,
        item: ExperimentItem,
    ) -> None:
        if callback is not None:
            callback(
                ProgressEvent(
                    completed=completed,
                    total=total,
                    case_id=item.case_id,
                    status=item.status,
                )
            )
