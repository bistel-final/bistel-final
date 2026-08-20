"""최종 epoch에서 폐기된 구 corrected entry point를 명시적으로 차단한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import manifest_v3

EXIT_RETIRED = manifest_v3.EXIT_USAGE
REGISTRY_PATH = manifest_v3.REPOSITORY_ROOT / "infra/bootstrap/retired-pipelines.json"

_TOP_LEVEL_KEYS = {
    "format_version",
    "artifact_type",
    "dataset_epoch",
    "retired_in_task",
    "entries",
}
_ENTRY_KEYS = {
    "entry_id",
    "script",
    "retired_epoch",
    "reason",
    "correction_stages",
    "replacement_task",
    "removal_task",
}
_ENTRY_CONTRACTS = {
    "build_corrected_dataset": {
        "script": "backend/scripts/build_corrected_dataset.py",
        "correction_stages": [
            "dim_parameter_seed",
            "trace_seq_no",
            "summary_alarm_time",
        ],
        "replacement_task": "V5-CM-2.1",
    },
    "load_corrected_base": {
        "script": "backend/scripts/load_corrected_base.py",
        "correction_stages": [],
        "replacement_task": "V5-CM-2.3",
    },
    "load_evaluation_mock": {
        "script": "backend/scripts/load_evaluation_mock.py",
        "correction_stages": [],
        "replacement_task": "V5-CM-2.3",
    },
}


class _RegistryError(RuntimeError):
    """폐기 등록부가 고정 계약을 만족하지 않을 때의 내부 오류."""


def _require_exact_keys(payload: dict[str, Any], expected: set[str]) -> None:
    if set(payload) != expected:
        raise _RegistryError


def _require_reason(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise _RegistryError
    if any(ord(character) < 32 for character in value):
        raise _RegistryError
    return value


def _validate_script_path(value: Any, expected: str) -> str:
    if not isinstance(value, str) or value != expected or "\\" in value:
        raise _RegistryError
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise _RegistryError
    if not (manifest_v3.REPOSITORY_ROOT / relative).is_file():
        raise _RegistryError
    return value


def _load_registry(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _RegistryError from exc
    if not isinstance(payload, dict):
        raise _RegistryError
    _require_exact_keys(payload, _TOP_LEVEL_KEYS)
    if (
        type(payload["format_version"]) is not int
        or payload["format_version"] != 1
        or payload["artifact_type"] != "retired_pipeline_registry"
        or payload["dataset_epoch"] != "fdc_final_20260818"
        or payload["retired_in_task"] != "V5-CM-1.5"
    ):
        raise _RegistryError

    raw_entries = payload["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) != len(_ENTRY_CONTRACTS):
        raise _RegistryError

    entries: dict[str, dict[str, Any]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise _RegistryError
        _require_exact_keys(raw_entry, _ENTRY_KEYS)
        entry_id = raw_entry["entry_id"]
        if not isinstance(entry_id, str) or entry_id in entries:
            raise _RegistryError
        contract = _ENTRY_CONTRACTS.get(entry_id)
        if contract is None:
            raise _RegistryError
        _validate_script_path(raw_entry["script"], contract["script"])
        _require_reason(raw_entry["reason"])
        if (
            raw_entry["retired_epoch"] != "kosa_0813"
            or raw_entry["correction_stages"] != contract["correction_stages"]
            or raw_entry["replacement_task"] != contract["replacement_task"]
            or raw_entry["removal_task"] != "V5-CM-1.6"
        ):
            raise _RegistryError
        entries[entry_id] = raw_entry

    if set(entries) != set(_ENTRY_CONTRACTS):
        raise _RegistryError
    return entries


def _emit(payload: dict[str, Any]) -> int:
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
    )
    return EXIT_RETIRED


def block(entry_id: str, *, registry_path: Path = REGISTRY_PATH) -> int:
    """폐기된 entry point를 차단하고 사유·대체 Task를 한 줄로 출력한다."""
    try:
        entries = _load_registry(Path(registry_path))
    except Exception:  # 안전 경계: 등록부 오류는 항상 fail-closed한다.
        return _emit({"reason_code": "RETIRED_REGISTRY_INVALID", "status": "BLOCKED"})

    entry = entries.get(entry_id) if isinstance(entry_id, str) else None
    if entry is None:
        return _emit({"reason_code": "RETIRED_REGISTRY_MISS", "status": "BLOCKED"})
    return _emit(
        {
            "entry_id": entry["entry_id"],
            "reason": entry["reason"],
            "reason_code": "RETIRED_PIPELINE",
            "removal_task": entry["removal_task"],
            "replacement_task": entry["replacement_task"],
            "status": "BLOCKED",
        }
    )
