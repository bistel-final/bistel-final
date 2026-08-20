"""저장소에 등록된 source-manifest-v4.json artifact 회귀 검증 (V5-CM-1.3 묶음 2).

합성 fixture 검증은 `test_build_source_manifest_v4.py`(묶음 1)가 맡는다. 이 파일은
**실 발급물**이 기준표·epoch v2·intake와 정합한지를 저장소 상태로 고정한다.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_source_manifest_v4 as builder  # noqa: E402
import manifest_v3 as mv3  # noqa: E402
import value_normalization as vn  # noqa: E402

BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
MANIFEST_PATH = BOOTSTRAP_ROOT / "source-manifest-v4.json"
EPOCH_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"
INTAKE_PATH = BOOTSTRAP_ROOT / "final-zip-intake.json"
REFERENCE_README = (
    REPOSITORY_ROOT / "docs" / "reference" / "mentor-final-20260818" / "README.md"
)

HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _intake() -> dict:
    return json.loads(INTAKE_PATH.read_text(encoding="utf-8"))


# --- 1. v4 스키마 계약 ----------------------------------------------------------


def test_registered_manifest_matches_v4_schema() -> None:
    manifest = _manifest()

    assert list(manifest) == [
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "source_archive_sha256",
        "selected_entry_manifest_sha256",
        "schema_sha256",
        "generator_sha256",
        "canonicalization_version",
        "hash_algorithm",
        "value_normalization_version",
        "derived_from",
        "tables",
        "artifacts",
        "origin_package",
    ]
    assert manifest["format_version"] == 4
    assert manifest["artifact_type"] == "source_files"
    assert manifest["dataset_epoch"] == "fdc_final_20260818"
    assert manifest["hash_algorithm"] == mv3.HASH_ALGORITHM
    assert manifest["value_normalization_version"] == vn.VALUE_NORMALIZATION_VERSION
    assert set(manifest["tables"]) == set(builder.EXPECTED_ROW_COUNTS)
    for table, entry in manifest["tables"].items():
        assert entry["file_id"] == builder.TABLE_MEMBERS[table]
        assert tuple(entry["columns"]) == builder.EXPECTED_COLUMNS[table]
        assert entry["column_types"] == builder.EXPECTED_COLUMN_TYPES[table]
        assert tuple(entry["primary_key"]) == builder.EXPECTED_PRIMARY_KEYS[table]
        assert tuple(entry["included_by_profile"]) == builder.INCLUDED_BY_PROFILE[table]
        assert HEX_SHA256.fullmatch(entry["content_hash"])
    artifacts = manifest["artifacts"]
    assert set(artifacts) == {
        "schema_sql",
        "master_cypher",
        "generator",
        "rag_documents",
    }
    assert len(artifacts["rag_documents"]) == 3
    # 기준표 §8 확대(2026-08-20)로 RAG 3종 원본 해시도 고정됐다 — intake 판단 승계.
    # V5-B-1.2 정정본은 별도 경로의 정본이고 이 원본 해시는 보존된다(기준표 §7).
    assert all(entry["pinned"] is True for entry in artifacts["rag_documents"])


# --- 2. 4자 대조 ---------------------------------------------------------------


def test_archive_sha256_agrees_across_four_sources() -> None:
    """기준표 머리 · epoch v2 · intake · manifest v4의 archive SHA-256 일치."""
    documented = re.search(
        r"원본 SHA-256: `([0-9a-f]{64})`",
        REFERENCE_README.read_text(encoding="utf-8"),
    )
    assert documented is not None

    epoch = json.loads(EPOCH_PATH.read_text(encoding="utf-8"))
    manifest = _manifest()

    assert manifest["source_archive_sha256"] == documented.group(1)
    assert manifest["source_archive_sha256"] == epoch["archive"]["sha256"]
    assert manifest["source_archive_sha256"] == _intake()["archive"]["sha256"]


def test_derived_from_references_are_valid() -> None:
    manifest = _manifest()
    derived = manifest["derived_from"]
    assert derived == {
        "dataset_epoch_artifact": "infra/bootstrap/dataset-epoch.json",
        "intake_artifact": "infra/bootstrap/final-zip-intake.json",
    }
    for reference in derived.values():
        assert (REPOSITORY_ROOT / reference).is_file()
    epoch = json.loads(EPOCH_PATH.read_text(encoding="utf-8"))
    assert manifest["dataset_epoch"] == epoch["dataset_epoch"]


# --- 3. 기준표 tripwire ---------------------------------------------------------


def test_reference_table_section2_row_count_tripwire() -> None:
    """기준표 §2 표·상수·발급물 세 곳의 행 수가 일치한다.

    기준표가 바뀌면 여기서 실패해 사람이 의도적으로 상수를 동기화하게 한다.
    """
    section = (
        REFERENCE_README.read_text(encoding="utf-8").split("## 2.")[1].split("## 3.")[0]
    )
    documented = {
        table: int(count.replace(",", ""))
        for table, count in re.findall(r"\| `(\w+)` \| ([\d,]+) \|", section)
    }
    assert documented == builder.EXPECTED_ROW_COUNTS

    manifest = _manifest()
    measured = {
        table: entry["row_count"] for table, entry in manifest["tables"].items()
    }
    assert measured == documented


def test_reference_table_section8_artifact_hash_tripwire() -> None:
    """`03_schema_clean.sql`·`master.cypher`·Generator 해시의 3자 일치.

    기준표 §8 · intake 등록값 · manifest v4가 같은 값을 가리킨다(Task 완료 기준 3).
    """
    section = (
        REFERENCE_README.read_text(encoding="utf-8").split("## 8.")[1].split("## 9.")[0]
    )
    pins = {
        f"project/repository/{path}": sha
        for path, sha in re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", section)
    }
    intake_hashes = {
        member["path"]: member["sha256"] for member in _intake()["selected_members"]
    }
    artifacts = _manifest()["artifacts"]
    for key in ("schema_sql", "master_cypher", "generator"):
        entry = artifacts[key]
        assert entry["sha256"] == pins[entry["file_id"]], key
        assert entry["sha256"] == intake_hashes[entry["file_id"]], key


def test_rag_and_csv_file_hashes_match_intake() -> None:
    """비-pinned 파일까지 포함해 manifest의 파일 참조가 intake 등록과 정합한다."""
    intake_hashes = {
        member["path"]: member["sha256"] for member in _intake()["selected_members"]
    }
    manifest = _manifest()
    for entry in manifest["artifacts"]["rag_documents"]:
        assert entry["sha256"] == intake_hashes[entry["file_id"]]
    for entry in manifest["tables"].values():
        assert entry["file_id"] in intake_hashes


# --- 4. ① origin package (WBS 확대분) -------------------------------------------


def test_origin_package_artifacts_are_registered() -> None:
    """① 3파일의 sha256·role이 발급물에 있고 배포패키지_기준.md §3.1 표와 일치한다.

    문서↔상수 축은 `test_build_source_manifest_v4.py`의 tripwire가, 여기서는
    문서↔발급물 축을 고정해 3원 대조가 완성된다.
    """
    section = (
        (REPOSITORY_ROOT / "docs" / "reference" / "배포패키지_기준.md")
        .read_text(encoding="utf-8")
        .split("### 3.1")[1]
        .split("### 3.2")[0]
    )
    documented = dict(re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", section))
    assert len(documented) == 3

    origin = _manifest()["origin_package"]
    assert origin["package"] == "교육생_배포패키지"
    assert origin["selection_rule"] == "final-package-first"
    assert origin["reference"] == "docs/reference/배포패키지_기준.md"
    registered = {
        entry["file_id"]: entry["sha256"] for entry in origin["artifacts"].values()
    }
    assert registered == documented
    assert all(entry["role"] for entry in origin["artifacts"].values())


def test_origin_artifacts_do_not_shadow_final_package() -> None:
    """① 출처 file_id가 ③ 선별 member와 겹치지 않는다 — 대체 금지의 발급물 증적."""
    final_basenames = {
        member["path"].rsplit("/", 1)[-1] for member in _intake()["selected_members"]
    }
    origin = _manifest()["origin_package"]["artifacts"]
    origin_basenames = {
        entry["file_id"].rsplit("/", 1)[-1] for entry in origin.values()
    }
    assert origin_basenames & final_basenames == set()


# --- 5. 시스템설계 §2.3 필수 필드 (최종검증 1차 필수 1) --------------------------


def test_design_mandated_fields_are_registered() -> None:
    """§2.3의 6개 필수 계약이 실 발급물에 있고 각 원천과 일치한다."""
    manifest = _manifest()

    # selected_entry_manifest_sha256 = intake artifact 바이트 해시(provenance 결합).
    assert (
        manifest["selected_entry_manifest_sha256"]
        == hashlib.sha256(INTAKE_PATH.read_bytes()).hexdigest()
    )

    # schema/generator 최상위 사본은 artifacts·기준표 §8 pin과 같다.
    section = (
        REFERENCE_README.read_text(encoding="utf-8").split("## 8.")[1].split("## 9.")[0]
    )
    pins = {
        f"project/repository/{path}": sha
        for path, sha in re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", section)
    }
    assert manifest["schema_sha256"] == manifest["artifacts"]["schema_sql"]["sha256"]
    assert (
        manifest["schema_sha256"]
        == pins[manifest["artifacts"]["schema_sql"]["file_id"]]
    )
    assert manifest["generator_sha256"] == manifest["artifacts"]["generator"]["sha256"]
    assert (
        manifest["generator_sha256"]
        == pins[manifest["artifacts"]["generator"]["file_id"]]
    )

    assert manifest["canonicalization_version"] == "canonical-json-nfc-codepoint-v1"
    assert (
        "sha256-" + manifest["canonicalization_version"] == manifest["hash_algorithm"]
    )


def test_action_history_is_evaluation_only() -> None:
    """시스템설계 §2.4 — runtime·runtime-e2e는 action_history를 적재하지 않는다.

    최종검증 1차 필수 1 수정방향 3의 회귀 고정이다.
    """
    tables = _manifest()["tables"]
    assert tables["action_history"]["included_by_profile"] == ["evaluation"]
    for table, entry in tables.items():
        if table != "action_history":
            assert entry["included_by_profile"] == [
                "runtime",
                "runtime-e2e",
                "evaluation",
            ]


def test_primary_keys_match_final_ddl_declaration() -> None:
    """실 발급물의 PK가 최종 DDL 선언과 일치한다(값 고정)."""
    tables = _manifest()["tables"]
    assert {table: entry["primary_key"] for table, entry in tables.items()} == {
        "action_history": ["action_id"],
        "dim_parameter": ["parameter_id"],
        "evaluation": ["lot_hist_id", "parameter", "step_no"],
        "fdc_trace": ["lot_hist_id", "parameter_id", "seq_no"],
        "lot_history": ["lot_hist_id"],
        "metrology": ["metrology_id"],
        "summary_alarm_history": ["alarm_id"],
        "summary_data": ["lot_hist_id", "parameter", "step_no"],
        "trace_alarm_history": ["alarm_id"],
    }
