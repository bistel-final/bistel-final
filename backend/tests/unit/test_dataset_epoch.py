"""V4-CM-0.1 신규 dataset epoch 등록 artifact 계약을 검증한다."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_PATH = REPOSITORY_ROOT / "infra" / "bootstrap" / "dataset-epoch.json"
ARCHIVE_SHA256 = "8bbe0bdd646290e2da300db0c293d6775927f61a35375721d4b042b239803c96"
INVENTORY_SHA256 = "4e3e5e54098cb62e19e18247e2219be5ce4b89e9603752d60557093a2a814dcc"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")

EXPECTED_FILES = [
    "kosa_0813/00_README.md",
    "kosa_0813/01_용어집.md",
    "kosa_0813/02_화면별_API_가이드.md",
    "kosa_0813/03_n8n_자동화_가이드.md",
    "kosa_0813/04_알람_재현_가이드.md",
    "kosa_0813/대시보드/FDC_알람_MVP.html",
    "kosa_0813/코드/_template_mvp.html",
    "kosa_0813/코드/build_mvp.py",
    "kosa_0813/코드/gen_sample_data.py",
    "kosa_0813/클린데이터셋/03_schema_clean.sql",
    "kosa_0813/클린데이터셋/docs_rag/SPEC_ET-7500_DryEtcher.md",
    "kosa_0813/클린데이터셋/docs_rag/SPEC_PH-9000_PhotoScanner.md",
    "kosa_0813/클린데이터셋/docs_rag/TROUBLE_FDC_FaultGuide.md",
    "kosa_0813/클린데이터셋/neo4j/master.cypher",
    "kosa_0813/클린데이터셋/postgres/action_history.csv",
    "kosa_0813/클린데이터셋/postgres/evaluation.csv",
    "kosa_0813/클린데이터셋/postgres/fdc_trace.csv",
    "kosa_0813/클린데이터셋/postgres/lot_history.csv",
    "kosa_0813/클린데이터셋/postgres/metrology.csv",
    "kosa_0813/클린데이터셋/postgres/summary_alarm_history.csv",
    "kosa_0813/클린데이터셋/postgres/summary_data.csv",
    "kosa_0813/클린데이터셋/postgres/trace_alarm_history.csv",
]


def _load_artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_dataset_epoch_schema_and_fixed_source_identity() -> None:
    artifact = _load_artifact()

    assert set(artifact) == {
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "received_date",
        "archive",
        "public_fault_ground_truth_available",
        "inventory_scope",
        "file_count",
        "file_inventory",
    }
    assert artifact["format_version"] == 1
    assert artifact["artifact_type"] == "dataset_epoch_registration"
    assert artifact["dataset_epoch"] == "kosa_0813"
    assert artifact["received_date"] == "2026-08-13"
    assert artifact["archive"] == {
        "filename": "kosa_0813.zip",
        "sha256": ARCHIVE_SHA256,
    }
    assert artifact["public_fault_ground_truth_available"] is False
    assert artifact["inventory_scope"] == "non_directory_zip_members"


def test_file_inventory_is_complete_unique_and_codepoint_sorted() -> None:
    artifact = _load_artifact()
    inventory = artifact["file_inventory"]
    paths = [entry["path"] for entry in inventory]

    assert artifact["file_count"] == len(EXPECTED_FILES) == len(inventory)
    assert paths == EXPECTED_FILES
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))


def test_file_inventory_entry_schema_and_digests() -> None:
    inventory = _load_artifact()["file_inventory"]

    for entry in inventory:
        assert set(entry) == {"path", "size_bytes", "sha256"}
        assert isinstance(entry["size_bytes"], int)
        assert entry["size_bytes"] > 0
        assert HEX_SHA256.fullmatch(entry["sha256"])

    canonical_inventory = json.dumps(
        inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(canonical_inventory).hexdigest() == INVENTORY_SHA256


def test_artifact_contains_no_absolute_or_traversal_paths() -> None:
    artifact = _load_artifact()

    assert (
        PurePosixPath(artifact["archive"]["filename"]).name
        == artifact["archive"]["filename"]
    )
    for entry in artifact["file_inventory"]:
        path = PurePosixPath(entry["path"])
        assert not path.is_absolute()
        assert ".." not in path.parts
        assert "\\" not in entry["path"]


def test_artifact_contains_no_secrets_or_local_locations() -> None:
    serialized = json.dumps(_load_artifact(), ensure_ascii=False).lower()
    forbidden = (
        "/users/",
        "c:\\",
        "file://",
        "postgresql://",
        "postgres://",
        "neo4j://",
        "bolt://",
        '"password"',
        '"username"',
        '"api_key"',
        '"dsn"',
        '"host"',
    )

    assert all(value not in serialized for value in forbidden)
