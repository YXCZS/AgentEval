"""Back up, restore, and upgrade representative legacy data before destructive migrations."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.orm import Session

from agent_eval_api.db import (
    AgentVersionRecord,
    ApiKeyRecord,
    DatasetCaseRecord,
    EvaluationRunRecord,
    ProjectRecord,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "legacy_project_1.json"
BASELINE_REVISION = "ffe07a933165"
DEFAULT_PROJECT_ID = "default-project"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def run_alembic(database_path: Path, revision: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = sqlite_url(database_path)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
    )


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fixture_file:
        data = json.load(fixture_file)
    if not isinstance(data, dict):
        raise ValueError("legacy fixture must be a JSON object")
    return data


def seed_legacy_project(database_path: Path, fixture: dict[str, Any]) -> None:
    engine = create_engine(sqlite_url(database_path))
    metadata = MetaData()
    metadata.reflect(bind=engine)
    created_at = datetime.now(UTC)

    def insert_row(table_name: str, values: dict[str, Any]) -> None:
        table = metadata.tables[table_name]
        values_with_timestamp = {"created_at": created_at, **values}
        values_for_baseline = {
            key: value for key, value in values_with_timestamp.items() if key in table.c
        }
        session.execute(table.insert().values(values_for_baseline))

    try:
        with Session(engine) as session:
            project = fixture["project"]
            insert_row("projects", project)

            api_key = fixture["api_key"]
            insert_row("api_keys", {"project_id": project["id"], **api_key})

            agent = fixture["agent"]
            insert_row("agents", {"project_id": project["id"], **agent, "updated_at": created_at})

            agent_version = fixture["agent_version"]
            insert_row("agent_versions", {"agent_id": agent["id"], **agent_version})

            dataset = fixture["dataset"]
            insert_row(
                "datasets",
                {"project_id": project["id"], **dataset, "updated_at": created_at},
            )

            evaluator = fixture["evaluator"]
            insert_row(
                "evaluator_versions",
                {
                    "project_id": project["id"],
                    "requires": evaluator.get("requires", []),
                    "supported_agent_types": evaluator.get("supported_agent_types", []),
                    "score_min": evaluator.get("score_min"),
                    "score_max": evaluator.get("score_max"),
                    "rubric": evaluator.get("rubric"),
                    "judge_model": evaluator.get("judge_model"),
                    **evaluator,
                },
            )

            dataset_version = fixture["dataset_version"]
            insert_row(
                "dataset_versions",
                {
                    "dataset_id": dataset["id"],
                    "metadata": dataset_version.get("metadata", {}),
                    **dataset_version,
                },
            )
            session.execute(
                metadata.tables["datasets"]
                .update()
                .where(metadata.tables["datasets"].c.id == dataset["id"])
                .values(current_version_id=dataset_version["id"])
            )

            case_data = fixture["dataset_case"]
            insert_row(
                "dataset_cases",
                {
                    "dataset_version_id": dataset_version["id"],
                    "input": case_data["input"],
                    "expected_tools": case_data.get("expected_tools", []),
                    "expected_state": case_data.get("expected_state"),
                    "retrieval_context": case_data.get("retrieval_context", []),
                    "messages": case_data.get("messages", []),
                    "output_schema": case_data.get("output_schema"),
                    "source_trace_id": case_data.get("source_trace_id"),
                    "metadata": case_data.get("metadata", {}),
                    **case_data,
                },
            )

            run = fixture["run"]
            insert_row(
                "evaluation_runs",
                {
                    "project_id": project["id"],
                    "agent_version_id": agent_version["id"],
                    "dataset_version_id": dataset_version["id"],
                    **run,
                },
            )
            session.commit()
    finally:
        engine.dispose()


def copy_sqlite_database(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(destination) as target_connection,
    ):
        source_connection.backup(target_connection)


def verify_restored_project(database_path: Path, fixture: dict[str, Any]) -> None:
    engine = create_engine(sqlite_url(database_path))
    try:
        with Session(engine) as session:
            project = session.get(ProjectRecord, DEFAULT_PROJECT_ID)
            assert project is not None and project.name == fixture["project"]["name"]
            assert session.get(ProjectRecord, fixture["project"]["id"]) is None

            key = session.get(ApiKeyRecord, fixture["api_key"]["id"])
            assert key is not None and key.key_hash == fixture["api_key"]["key_hash"]
            assert key.project_id == DEFAULT_PROJECT_ID

            version = session.get(AgentVersionRecord, fixture["agent_version"]["id"])
            assert version is not None
            assert version.agent_type == "prompt"
            assert version.project_id == DEFAULT_PROJECT_ID
            assert version.release_identity == version.label
            assert version.metadata_json == {}
            assert version.agent.project_id == DEFAULT_PROJECT_ID

            agent_version_columns = {
                column["name"] for column in inspect(engine).get_columns("agent_versions")
            }
            assert "prompt_config" not in agent_version_columns
            experiment_item_columns = {
                column["name"]
                for column in inspect(engine).get_columns("experiment_item_attempts")
            }
            assert {
                "experiment_id",
                "case_id",
                "repetition",
                "attempt",
                "external_run_id",
                "result_hash",
                "source_trace_id",
                "evidence_status",
                "evidence_reasons",
                "runtime_metadata",
            } <= experiment_item_columns
            score_columns = {
                column["name"] for column in inspect(engine).get_columns("scores")
            }
            assert {"experiment_item_id", "repetition", "attempt"} <= score_columns

            case = session.get(DatasetCaseRecord, fixture["dataset_case"]["id"])
            assert case is not None and case.input_json == fixture["dataset_case"]["input"]

            run = session.get(EvaluationRunRecord, fixture["run"]["id"])
            assert run is not None
            assert run.name == "Historical Experiment"
            assert run.configuration_snapshot == fixture["run"]["configuration_snapshot"]
            assert run.configuration_snapshot["prompt_config"] == {
                "model": "fixture-model"
            }
            assert run.project_id == DEFAULT_PROJECT_ID
            assert run.execution_mode == "remote_trigger"
            assert run.evidence_policy == "trace_required"
    finally:
        engine.dispose()


def rehearse_migration(workdir: Path, fixture_path: Path, baseline_revision: str) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = load_fixture(fixture_path)
    legacy_database = workdir / "legacy-before-upgrade.sqlite3"
    backup_database = workdir / "legacy-backup.sqlite3"
    restored_database = workdir / "restored-and-upgraded.sqlite3"

    for database_path in (legacy_database, backup_database, restored_database):
        if database_path.exists():
            database_path.unlink()

    run_alembic(legacy_database, baseline_revision)
    seed_legacy_project(legacy_database, fixture)
    copy_sqlite_database(legacy_database, backup_database)
    copy_sqlite_database(backup_database, restored_database)
    run_alembic(restored_database, "head")
    verify_restored_project(restored_database, fixture)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--baseline-revision", default=BASELINE_REVISION)
    parser.add_argument(
        "--workdir",
        type=Path,
        help=(
            "Directory for temporary SQLite databases. "
            "Defaults to a disposable temporary directory."
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.fixture.is_file():
        raise FileNotFoundError(f"fixture not found: {arguments.fixture}")

    if arguments.workdir is None:
        temporary_directory = Path(tempfile.mkdtemp(prefix="agent-eval-migration-"))
        try:
            rehearse_migration(temporary_directory, arguments.fixture, arguments.baseline_revision)
        finally:
            shutil.rmtree(temporary_directory)
    else:
        rehearse_migration(arguments.workdir, arguments.fixture, arguments.baseline_revision)

    print(
        "Migration rehearsal passed: legacy project-1 data was backed up, "
        "restored, and upgraded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
