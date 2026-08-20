"""V5-CM-1.5 구 corrected entry point의 명시 차단 계약을 검증한다."""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import retired_pipelines  # noqa: E402

REGISTRY_PATH = REPOSITORY_ROOT / "infra/bootstrap/retired-pipelines.json"
ENTRY_CONTRACTS = {
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


def _registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _run_script(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / script), *args],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def _single_json_line(value: str) -> dict[str, Any]:
    assert len(value.splitlines()) == 1
    payload = json.loads(value)
    assert isinstance(payload, dict)
    return payload


def test_registry_exact_schema_and_metadata() -> None:
    registry = _registry()
    assert set(registry) == {
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "retired_in_task",
        "entries",
    }
    assert registry["format_version"] == 1
    assert registry["artifact_type"] == "retired_pipeline_registry"
    assert registry["dataset_epoch"] == "fdc_final_20260818"
    assert registry["retired_in_task"] == "V5-CM-1.5"
    assert len(registry["entries"]) == 3
    for entry in registry["entries"]:
        assert set(entry) == {
            "entry_id",
            "script",
            "retired_epoch",
            "reason",
            "correction_stages",
            "replacement_task",
            "removal_task",
        }


def test_registry_entries_match_scripts_stages_and_tasks() -> None:
    entries = {entry["entry_id"]: entry for entry in _registry()["entries"]}
    assert set(entries) == set(ENTRY_CONTRACTS)
    for entry_id, expected in ENTRY_CONTRACTS.items():
        entry = entries[entry_id]
        assert (REPOSITORY_ROOT / entry["script"]).is_file()
        assert entry["script"] == expected["script"]
        assert entry["retired_epoch"] == "kosa_0813"
        assert entry["correction_stages"] == expected["correction_stages"]
        assert entry["replacement_task"] == expected["replacement_task"]
        assert entry["removal_task"] == "V5-CM-1.6"
        assert entry["reason"].strip()

    wbs_path = REPOSITORY_ROOT / "docs/planning/Task분해_WBS_v5_작업본.md"
    wbs_task_ids = set(
        re.findall(r"^\| (V5-[^ |]+) \|", wbs_path.read_text(encoding="utf-8"), re.M)
    )
    referenced_tasks = {
        entry[task_field]
        for entry in entries.values()
        for task_field in ("replacement_task", "removal_task")
    }
    assert referenced_tasks <= wbs_task_ids


@pytest.mark.parametrize("entry", ENTRY_CONTRACTS.values())
def test_subprocess_entry_points_are_blocked(entry: dict[str, Any]) -> None:
    result = _run_script(entry["script"], "--help")
    assert result.returncode == retired_pipelines.EXIT_RETIRED
    assert result.stdout == ""
    payload = _single_json_line(result.stderr)
    assert payload["status"] == "BLOCKED"
    assert payload["reason_code"] == "RETIRED_PIPELINE"


@pytest.mark.parametrize("entry_id", ENTRY_CONTRACTS)
def test_direct_main_calls_are_blocked(
    entry_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    module = importlib.import_module(entry_id)
    assert module.main(["--archive", "ignored", "--help"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _single_json_line(captured.err)["reason_code"] == "RETIRED_PIPELINE"


@pytest.mark.parametrize("entry_id", ENTRY_CONTRACTS)
def test_module_import_has_no_output_or_exit_side_effect(entry_id: str) -> None:
    code = f"import sys; sys.path.insert(0, {str(SCRIPTS_ROOT)!r}); import {entry_id}"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_block_message_records_replacement_and_removal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert retired_pipelines.block("build_corrected_dataset") == 2
    payload = _single_json_line(capsys.readouterr().err)
    assert payload["replacement_task"] == "V5-CM-2.1"
    assert payload["removal_task"] == "V5-CM-1.6"
    assert payload["reason"]


def test_unknown_entry_fails_closed_without_reflecting_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "postgresql://secret@/private/absolute/path"
    assert retired_pipelines.block(sentinel) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert sentinel not in captured.err
    assert _single_json_line(captured.err) == {
        "reason_code": "RETIRED_REGISTRY_MISS",
        "status": "BLOCKED",
    }


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "{broken",
        json.dumps({"format_version": 1}),
        json.dumps(
            {
                **_registry(),
                "entries": [*_registry()["entries"], _registry()["entries"][0]],
            },
            ensure_ascii=False,
        ),
    ],
)
def test_invalid_registry_fails_closed(
    payload: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "retired-pipelines.json"
    if payload is not None:
        path.write_text(payload, encoding="utf-8")
    assert retired_pipelines.block("build_corrected_dataset", registry_path=path) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _single_json_line(captured.err) == {
        "reason_code": "RETIRED_REGISTRY_INVALID",
        "status": "BLOCKED",
    }


def test_registry_value_mutation_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = _registry()
    registry["entries"][0]["replacement_task"] = "V5-CM-9.9"
    path = tmp_path / "retired-pipelines.json"
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    assert retired_pipelines.block("build_corrected_dataset", registry_path=path) == 2
    payload = _single_json_line(capsys.readouterr().err)
    assert payload["reason_code"] == "RETIRED_REGISTRY_INVALID"
