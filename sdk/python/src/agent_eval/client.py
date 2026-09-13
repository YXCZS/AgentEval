"""Typed HTTP client for user-owned Agent experiment runtimes."""

from __future__ import annotations

import os
from typing import Any, Literal, Self

import httpx

from .errors import AgentEvalApiError, AgentEvalConnectionError, IncompatibleServiceError
from .models import (
    AgentRelease,
    Dataset,
    DatasetSelection,
    DatasetVersion,
    Experiment,
    ExperimentItem,
    ManifestItem,
    ManifestPage,
)

SDK_CONTRACT_MAJOR = 1


class Client:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        project_id: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("AGENT_EVAL_BASE_URL") or "").rstrip("/")
        self.project_id = project_id or os.getenv("AGENT_EVAL_PROJECT_ID") or ""
        self._api_key = api_key or os.getenv("AGENT_EVAL_API_KEY") or ""
        if not self.base_url or not self.project_id or not self._api_key:
            raise ValueError(
                "base_url, project_id and api_key are required (or set AGENT_EVAL_* env vars)"
            )
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"X-Project-Key": self._api_key},
            timeout=timeout,
            transport=transport,
        )
        self._compatible = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AgentEvalConnectionError(
                f"Agent Eval API is unreachable for {method} {path}"
            ) from exc
        if response.is_error:
            raise AgentEvalApiError(response.status_code, method, path)
        if response.status_code == 204:
            return None
        return response.json()

    def check_compatibility(self) -> None:
        path = f"/projects/{self.project_id}/sdk-contract"
        payload = self._request("GET", path)
        if payload.get("contract") != "agent-eval-sdk" or (
            payload.get("major_version") != SDK_CONTRACT_MAJOR
        ):
            raise IncompatibleServiceError(
                "Agent Eval service is incompatible with this SDK; "
                f"required contract major {SDK_CONTRACT_MAJOR}"
            )
        self._compatible = True

    def _ensure_compatible(self) -> None:
        if not self._compatible:
            self.check_compatibility()

    def list_datasets(self) -> list[Dataset]:
        payload = self._request("GET", f"/projects/{self.project_id}/datasets")
        return [Dataset.model_validate(item) for item in payload]

    def get_dataset(
        self,
        name_or_id: str,
        *,
        version_id: str | None = None,
        version: int | None = None,
    ) -> DatasetSelection:
        if (version_id is None) == (version is None):
            raise ValueError("select exactly one immutable Dataset version_id or version number")
        datasets = self.list_datasets()
        matches = [item for item in datasets if item.id == name_or_id or item.name == name_or_id]
        if len(matches) != 1:
            raise ValueError("Dataset name or ID must resolve to exactly one Dataset")
        dataset = matches[0]
        versions_payload = self._request(
            "GET", f"/projects/{self.project_id}/datasets/{dataset.id}/versions"
        )
        versions = [DatasetVersion.model_validate(item) for item in versions_payload]
        selected = next(
            (
                item
                for item in versions
                if (version_id is not None and item.id == version_id)
                or (version is not None and item.version == version)
            ),
            None,
        )
        if selected is None:
            raise ValueError("requested immutable Dataset version was not found")
        return DatasetSelection(dataset=dataset, version=selected)

    def register_release(
        self,
        *,
        label: str,
        agent_type: Literal["rag", "tool", "custom"],
        release_identity: str,
        source_revision: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRelease:
        self._ensure_compatible()
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/agent-releases",
            json={
                "label": label,
                "agent_type": agent_type,
                "release_identity": release_identity,
                "source_revision": source_revision,
                "metadata": metadata or {},
            },
        )
        return AgentRelease.model_validate(payload)

    def list_releases(self) -> list[AgentRelease]:
        self._ensure_compatible()
        payload = self._request(
            "GET", f"/projects/{self.project_id}/agent-releases"
        )
        return [AgentRelease.model_validate(item) for item in payload]

    def get_release(self, release_id: str) -> AgentRelease:
        matches = [release for release in self.list_releases() if release.id == release_id]
        if len(matches) != 1:
            raise ValueError("Agent Release ID must resolve to exactly one release")
        return matches[0]

    def create_experiment(
        self,
        *,
        name: str,
        dataset_version_id: str,
        release_id: str,
        evaluator_version_ids: list[str],
        evidence_policy: str = "trace_required",
        baseline_experiment_id: str | None = None,
        execution_options: dict[str, Any] | None = None,
    ) -> Experiment:
        self._ensure_compatible()
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/experiments",
            json={
                "name": name,
                "dataset_version_id": dataset_version_id,
                "agent_version_id": release_id,
                "evaluator_version_ids": evaluator_version_ids,
                "execution_mode": "sdk_task",
                "evidence_policy": evidence_policy,
                "baseline_run_id": baseline_experiment_id,
                "execution_options": execution_options or {},
            },
        )
        return Experiment.model_validate(payload)

    def get_manifest_page(
        self, experiment_id: str, *, offset: int = 0, limit: int = 50
    ) -> ManifestPage:
        payload = self._request(
            "GET",
            f"/projects/{self.project_id}/experiments/{experiment_id}/manifest",
            params={"offset": offset, "limit": limit},
        )
        return ManifestPage.model_validate(payload)

    def iter_manifest(self, experiment_id: str, *, page_size: int = 50) -> list[ManifestItem]:
        items: list[ManifestItem] = []
        offset = 0
        while True:
            page = self.get_manifest_page(experiment_id, offset=offset, limit=page_size)
            items.extend(page.items)
            if page.next_offset is None:
                return items
            offset = page.next_offset

    def start_item(
        self,
        experiment_id: str,
        *,
        case_id: str,
        repetition: int,
        attempt: int,
        external_run_id: str,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> ExperimentItem:
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/experiments/{experiment_id}/items/start",
            json={
                "case_id": case_id,
                "repetition": repetition,
                "attempt": attempt,
                "external_run_id": external_run_id,
                "expected_status": "queued",
                "runtime_metadata": runtime_metadata or {},
            },
        )
        return ExperimentItem.model_validate(payload)

    def complete_item(
        self,
        experiment_id: str,
        item_id: str,
        *,
        output: Any,
        usage: dict[str, Any] | None = None,
        runtime_metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> ExperimentItem:
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/experiments/{experiment_id}/items/{item_id}/complete",
            json={
                "expected_status": "running",
                "output": output,
                "usage": usage or {},
                "runtime_metadata": runtime_metadata or {},
                "trace_id": trace_id,
            },
        )
        return ExperimentItem.model_validate(payload)

    def fail_item(
        self,
        experiment_id: str,
        item_id: str,
        *,
        error_type: str,
        error_message: str,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> ExperimentItem:
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/experiments/{experiment_id}/items/{item_id}/fail",
            json={
                "expected_status": "running",
                "error_type": error_type,
                "error_message": error_message,
                "runtime_metadata": runtime_metadata or {},
            },
        )
        return ExperimentItem.model_validate(payload)

    def cancel_item(self, experiment_id: str, item_id: str) -> ExperimentItem:
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/experiments/{experiment_id}/items/{item_id}/cancel",
            json={"expected_status": "running"},
        )
        return ExperimentItem.model_validate(payload)

    def finalize_experiment(self, experiment_id: str) -> Experiment:
        payload = self._request(
            "POST", f"/projects/{self.project_id}/experiments/{experiment_id}/finalize"
        )
        return Experiment.model_validate(payload)

    def get_experiment(self, experiment_id: str) -> Experiment:
        payload = self._request(
            "GET", f"/projects/{self.project_id}/experiments/{experiment_id}"
        )
        return Experiment.model_validate(payload)

    def ingest_trace(self, trace: dict[str, Any]) -> str:
        payload = self._request(
            "POST",
            f"/projects/{self.project_id}/traces/ingest",
            json={"source": "sdk", "trace": trace},
        )
        return str(payload["trace_id"])
