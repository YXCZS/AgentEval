"""Format parsing and validation for evaluation dataset imports."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from agent_eval_api.contracts import DatasetCase
from agent_eval_api.dataset_export import (
    EXPORT_METADATA_FIELD,
    EXPORT_METADATA_RECORD_TYPE,
    EXPORT_RECORD_TYPE_FIELD,
)


class DatasetImportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    JSONL = "jsonl"


@dataclass(frozen=True)
class ImportIssue:
    line: int
    reason: str


@dataclass
class DatasetParseResult:
    cases: list[DatasetCase] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)


JSON_FIELDS = {
    "variables",
    "output_schema",
    "criteria",
    "expected_tools",
    "expected_state",
    "retrieval_context",
    "messages",
    "metadata",
    "source_span_ids",
    "source_mapping",
}


def parse_dataset_bytes(
    content: bytes,
    file_format: DatasetImportFormat,
    *,
    encoding: str = "utf-8",
    max_bytes: int = 5 * 1024 * 1024,
    field_mapping: dict[str, str] | None = None,
) -> DatasetParseResult:
    """Parse canonical DatasetCase records without writing any database rows."""

    if len(content) > max_bytes:
        return DatasetParseResult(issues=[ImportIssue(0, "file exceeds configured size limit")])
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError:
        return DatasetParseResult(issues=[ImportIssue(0, f"content is not valid {encoding}")])

    if file_format is DatasetImportFormat.CSV:
        return _parse_csv(text, field_mapping)
    if file_format is DatasetImportFormat.JSON:
        return _parse_json(text, field_mapping)
    if file_format is DatasetImportFormat.JSONL:
        return _parse_jsonl(text, field_mapping)
    return DatasetParseResult(issues=[ImportIssue(0, f"unsupported import format: {file_format}")])


def _parse_csv(text: str, field_mapping: dict[str, str] | None) -> DatasetParseResult:
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error as exc:
        return DatasetParseResult(issues=[ImportIssue(0, f"invalid CSV: {exc}")])
    if not rows:
        return DatasetParseResult()
    return _parse_rows(
        (
            (line_number, _decode_and_map_csv_row(row, field_mapping))
            for line_number, row in enumerate(rows, start=2)
            if not _is_csv_metadata_row(row)
        )
    )


def _parse_json(text: str, field_mapping: dict[str, str] | None) -> DatasetParseResult:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return DatasetParseResult(issues=[ImportIssue(exc.lineno, f"invalid JSON: {exc.msg}")])
    if not isinstance(raw, list):
        return DatasetParseResult(
            issues=[ImportIssue(1, "JSON import must be an array of case objects")]
        )
    return _parse_rows(
        (line_number, _map_row(row, field_mapping)) for line_number, row in enumerate(raw, start=1)
    )


def _parse_jsonl(text: str, field_mapping: dict[str, str] | None) -> DatasetParseResult:
    raw_rows: list[tuple[int, object]] = []
    result = DatasetParseResult()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if _is_jsonl_metadata_row(raw):
                continue
            raw_rows.append((line_number, _map_row(raw, field_mapping)))
        except json.JSONDecodeError as exc:
            result.issues.append(ImportIssue(line_number, f"invalid JSONL: {exc.msg}"))
    parsed = _parse_rows(raw_rows)
    result.cases.extend(parsed.cases)
    result.issues.extend(parsed.issues)
    return result


def _decode_csv_row(row: dict[Any, Any]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in row.items():
        if key is None or key.startswith("__agent_eval_") or value is None or value == "":
            continue
        if key in JSON_FIELDS:
            try:
                decoded[key] = json.loads(value)
            except json.JSONDecodeError as exc:
                decoded[key] = _InvalidJson(f"{key} is not valid JSON: {exc.msg}")
        elif key in {"input", "expected_output"}:
            decoded[key] = _maybe_json(value)
        else:
            decoded[key] = value
    return decoded


def _decode_and_map_csv_row(
    row: dict[str | None, str | None], field_mapping: dict[str, str] | None
) -> dict[str, Any]:
    mapped = _map_row(row, field_mapping)
    if not isinstance(mapped, dict):
        return {}
    return _decode_csv_row(mapped)


def _map_row(raw: object, field_mapping: dict[str, str] | None) -> object:
    if field_mapping is None or not isinstance(raw, dict):
        return raw
    return {
        canonical_name: raw[source_name]
        for canonical_name, source_name in field_mapping.items()
        if source_name in raw
    }


def _is_csv_metadata_row(row: dict[str | None, str | None]) -> bool:
    return row.get(EXPORT_RECORD_TYPE_FIELD) == EXPORT_METADATA_RECORD_TYPE


def _is_jsonl_metadata_row(raw: object) -> bool:
    return isinstance(raw, dict) and EXPORT_METADATA_FIELD in raw


@dataclass(frozen=True)
class _InvalidJson:
    reason: str


def _maybe_json(value: str) -> Any:
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _parse_rows(rows: Iterable[tuple[int, object]]) -> DatasetParseResult:
    result = DatasetParseResult()
    seen_ids: set[str] = set()
    for line_number, raw in rows:
        if not isinstance(raw, dict):
            result.issues.append(ImportIssue(line_number, "case must be a JSON object"))
            continue
        invalid_field = next(
            (value for value in raw.values() if isinstance(value, _InvalidJson)), None
        )
        if invalid_field is not None:
            result.issues.append(ImportIssue(line_number, invalid_field.reason))
            continue
        try:
            case = DatasetCase.model_validate(raw)
        except ValidationError as exc:
            result.issues.append(ImportIssue(line_number, _validation_reason(exc)))
            continue
        if case.id in seen_ids:
            result.issues.append(ImportIssue(line_number, f"duplicate case id: {case.id}"))
            continue
        seen_ids.add(case.id)
        result.cases.append(case)
    return result


def _validation_reason(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {first['msg']}"
