"""V5-CM-5.1 semantic API sync gate.

The evaluator is pure: callers provide normalized OpenAPI.  The CLI performs the
application import lazily and never connects to PostgreSQL, Neo4j, n8n, or Kafka.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
REPOSITORY_ROOT = BACKEND_ROOT.parent
CANONICAL_PATH = REPOSITORY_ROOT / "docs" / "deliverables" / "api" / "api_spec_v3.json"
DOCUMENT_CSV_PATH = (
    REPOSITORY_ROOT / "docs" / "deliverables" / "api" / "API명세서_v3_작업본.csv"
)
EXCLUDED_FRAMEWORK_PATHS = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
OperationKey = tuple[str, str]
NormalizedContract = dict[OperationKey, dict[str, Any]]


class ApiSyncError(ValueError):
    """The gate inputs are malformed and no verdict can be issued."""


def load_canonical(path: Path = CANONICAL_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApiSyncError(f"canonical model cannot be read: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("operations"), list):
        raise ApiSyncError("canonical model has no operations")
    return value


def operation_key(operation: Mapping[str, Any]) -> OperationKey:
    method, path = operation.get("method"), operation.get("path")
    if not isinstance(method, str) or not isinstance(path, str):
        raise ApiSyncError("operation method/path is invalid")
    return method, path


def canonical_semantics(spec: Mapping[str, Any]) -> NormalizedContract:
    operations = spec.get("operations")
    if not isinstance(operations, list):
        raise ApiSyncError("canonical operations must be an array")
    result: NormalizedContract = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ApiSyncError("canonical operation must be an object")
        semantic = operation.get("semantic")
        if semantic is not None:
            if not isinstance(semantic, dict):
                raise ApiSyncError("canonical semantic contract must be an object")
            key = operation_key(operation)
            _validate_canonical_semantic(semantic, f"{key[0]} {key[1]}")
            result[key] = semantic
    return dict(sorted(result.items()))


def _validate_canonical_semantic(value: Any, where: str) -> None:
    """Reject canonical storage that cannot be compared to normalized OpenAPI."""

    if isinstance(value, Mapping):
        if "rules" in value:
            raise ApiSyncError(f"{where}.rules must be operation-level metadata")
        enum = value.get("enum")
        if enum is not None and (
            not isinstance(enum, list)
            or not enum
            or any(not isinstance(item, str) or not item for item in enum)
            or len(enum) != len(set(enum))
            or enum != sorted(enum)
        ):
            raise ApiSyncError(f"{where}.enum must be a sorted unique string list")
        for key, child in value.items():
            _validate_canonical_semantic(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_canonical_semantic(child, f"{where}[{index}]")


def _fixture_keys(fixture: Mapping[str, Any]) -> set[OperationKey]:
    operations = fixture.get("operations")
    if not isinstance(operations, list):
        raise ApiSyncError("fixture operations must be an array")
    return {operation_key(item) for item in operations if isinstance(item, Mapping)}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compare_operations(
    expected: Mapping[OperationKey, dict[str, Any]],
    actual: Mapping[OperationKey, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method, path in sorted(expected):
        key = method, path
        if key not in actual:
            rows.append({"method": method, "path": path, "status": "MISSING"})
        elif actual[key] == expected[key]:
            rows.append({"method": method, "path": path, "status": "PASS"})
        else:
            rows.append(
                {
                    "actual_sha256": _digest(actual[key]),
                    "expected_sha256": _digest(expected[key]),
                    "method": method,
                    "path": path,
                    "status": "DRIFT",
                }
            )
    return rows


def evaluate_sync(
    spec: Mapping[str, Any],
    actual: Mapping[OperationKey, dict[str, Any]],
    required_fixture: Mapping[str, Any],
    optional_fixture: Mapping[str, Any],
    team_fixture: Mapping[str, Any],
) -> dict[str, Any]:
    operations = spec.get("operations")
    if not isinstance(operations, list):
        raise ApiSyncError("canonical operations must be an array")
    document_keys = {
        operation_key(item) for item in operations if isinstance(item, Mapping)
    }
    if len(document_keys) != 35:
        raise ApiSyncError(
            f"document catalog must contain 35 operations: {len(document_keys)}"
        )

    required_keys = _fixture_keys(required_fixture)
    optional_keys = _fixture_keys(optional_fixture)
    team_keys = _fixture_keys(team_fixture)
    if len(required_keys) != 14 or len(team_keys) != 5:
        raise ApiSyncError("required/team fixture inventory drift")
    release_keys = required_keys | team_keys
    classified_keys = required_keys | optional_keys | team_keys
    if len(release_keys) != 19:
        raise ApiSyncError("release inventory must contain 19 operations")

    expected = canonical_semantics(spec)
    comparisons = compare_operations(expected, actual)
    by_key = {(item["method"], item["path"]): item["status"] for item in comparisons}
    actual_business = {key for key in actual if key[1] not in EXCLUDED_FRAMEWORK_PATHS}
    unclassified = sorted(actual_business - classified_keys)
    undocumented = sorted(actual_business - document_keys)
    implemented_optional = actual_business - required_keys
    live_keys = required_keys | implemented_optional

    release_pass = sum(by_key.get(key) == "PASS" for key in release_keys)
    live_pass = sum(by_key.get(key) == "PASS" for key in live_keys)
    status_counts = {
        name: sum(item["status"] == name for item in comparisons)
        for name in ("PASS", "DRIFT", "MISSING")
    }
    document_gate = not undocumented and len(document_keys) == 35
    release_gate = release_pass == 19
    live_gate = len(live_keys) == 24 and live_pass == 24 and not unclassified
    overall = "PASS" if document_gate and release_gate and live_gate else "BLOCKED"
    return {
        "classified_live_count": len(live_keys),
        "document_catalog_count": len(document_keys),
        "document_gate": "PASS" if document_gate else "DRIFT",
        "format_version": 1,
        "live_gate": "PASS" if live_gate else "BLOCKED",
        "live_pass_count": live_pass,
        "operations": comparisons,
        "overall": overall,
        "release_gate": "PASS" if release_gate else "BLOCKED",
        "release_pass_count": release_pass,
        "status_counts": status_counts,
        "unclassified_live": [f"{method} {path}" for method, path in unclassified],
        "undocumented_live": [f"{method} {path}" for method, path in undocumented],
    }


def current_report() -> dict[str, Any]:
    """Build the current local report without starting application lifespan."""

    from app.main import app
    from tests.support.api_contract_baseline import (
        load_optional_contract,
        load_required_contract,
        load_team_release_contract,
        normalize_openapi_contract,
    )

    actual = normalize_openapi_contract(app.openapi(), resolve_schemas=True)
    return evaluate_sync(
        load_canonical(),
        actual,
        load_required_contract(),
        load_optional_contract(),
        load_team_release_contract(),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = current_report()
    except (ApiSyncError, ValueError, OSError) as exc:
        parser.exit(2, f"API_SYNC_INPUT_ERROR: {exc}\n")
    raw = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(raw, encoding="utf-8")
    else:
        print(raw, end="")
    raise SystemExit(0 if report["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
