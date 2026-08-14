"""저장소에 등록된 source-data-manifest.json v3 artifact 회귀 검증."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY_ROOT / "infra" / "bootstrap" / "source-data-manifest.json"
EPOCH_PATH = REPOSITORY_ROOT / "infra" / "bootstrap" / "dataset-epoch.json"
MODULE_PATH = REPOSITORY_ROOT / "backend" / "scripts" / "manifest_v3.py"

_spec = importlib.util.spec_from_file_location("manifest_v3_artifact", MODULE_PATH)
manifest_v3 = importlib.util.module_from_spec(_spec)
sys.modules["manifest_v3_artifact"] = manifest_v3
_spec.loader.exec_module(manifest_v3)
mv3 = manifest_v3


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_epoch() -> dict:
    return json.loads(EPOCH_PATH.read_text(encoding="utf-8"))


def _validate(manifest: dict) -> None:
    mv3.validate_manifest_schema(
        manifest,
        expected_artifact_type="source_files",
        expected_archive_sha256=_load_epoch()["archive"]["sha256"],
    )


def test_registered_source_manifest_passes_v3_schema() -> None:
    _validate(_load_manifest())


def test_registered_source_manifest_is_linked_to_dataset_epoch_inventory() -> None:
    manifest = _load_manifest()
    epoch = _load_epoch()
    inventory_paths = {entry["path"] for entry in epoch["file_inventory"]}
    file_ids = {entry["file_id"] for entry in manifest["tables"].values()}

    assert manifest["source_archive_sha256"] == epoch["archive"]["sha256"]
    assert file_ids == set(mv3.SOURCE_TABLE_FILES.values())
    assert file_ids <= inventory_paths


def test_registered_source_manifest_fixed_contract_values() -> None:
    manifest = _load_manifest()

    assert manifest["format_version"] == 3
    assert manifest["artifact_type"] == "source_files"
    assert manifest["correction_version"] == "none"
    assert manifest["tables"]["action_history"]["row_count"] == 48
    row_counts = {
        table: entry["row_count"] for table, entry in manifest["tables"].items()
    }
    assert row_counts == {
        "action_history": 48,
        "evaluation": 4800,
        "fdc_trace": 14400,
        "lot_history": 600,
        "metrology": 48,
        "summary_alarm_history": 47,
        "summary_data": 4800,
        "trace_alarm_history": 126,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(format_version=2),
        lambda value: value.update(correction_version="v1"),
        lambda value: value.update(source_archive_sha256="0" * 64),
        lambda value: value["tables"]["action_history"].update(row_count=10),
        lambda value: value["tables"]["lot_history"].update(
            file_id="클린데이터셋/postgres/lot_history.csv"
        ),
    ],
    ids=(
        "format-version",
        "correction-version",
        "archive-sha",
        "action-row-count",
        "canonical-file-id",
    ),
)
def test_registered_source_manifest_tampering_is_rejected(mutate) -> None:
    manifest = copy.deepcopy(_load_manifest())
    mutate(manifest)

    with pytest.raises(mv3.VerificationError):
        _validate(manifest)
