"""V5-CM-1.2 dataset epoch v2 등록 artifact 계약을 검증한다.

`fdc_final_20260818` epoch v2의 스키마·3자 대조와, 구 epoch `kosa_0813`에 대한
**동시 참조 금지**(격리 완결성 + 구 로더 fail-fast 두 겹)를 고정한다(작업계획 §2.4).
"""

import json
import re
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3 as mv3  # noqa: E402

BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
ARTIFACT_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"
INTAKE_PATH = BOOTSTRAP_ROOT / "final-zip-intake.json"
HISTORY_ROOT = BOOTSTRAP_ROOT / "history" / "kosa_0813"
REFERENCE_README = (
    REPOSITORY_ROOT / "docs" / "reference" / "mentor-final-20260818" / "README.md"
)

TARGET_EPOCH = "fdc_final_20260818"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")

# 격리 18파일(작업계획 §2.3). history/ 대비 원위치 부재를 함께 고정한다.
ISOLATED_REGISTRATION = (
    "dataset-epoch.json",
    "source-data-manifest.json",
    "corrected-data-manifest.json",
)
ISOLATED_MANIFESTS = (
    "evaluation.base_schema.json",
    "runtime.base_schema.json",
    "evaluation.corrected_base.json",
    "neo4j.graph.json",
)
ISOLATED_MARKERS = (
    "base_schema.kosa_agent_e2e.json",
    "corrected.v1.json",
    "corrected_base.kosa_agent.json",
    "corrected_base.kosa_agent_e2e.json",
    "evaluation_mock.kosa_text2sql.json",
    "neo4j_graph.neo4j.json",
    "reference_extensions.kosa_agent.json",
    "reference_extensions.kosa_agent_e2e.json",
    "reference_extensions.kosa_text2sql.json",
    "runtime_clean.kosa_agent.json",
    "runtime_clean.kosa_agent_e2e.json",
)

# 잔류 3파일(작업계획 §1.3) — 부재가 오류이고 읽는 코드가 현행 앱인 것만 남는다.
REMAINING_MANIFESTS = {
    "runtime.runtime_clean.json",
    "evaluation.evaluation_mock.json",
    "runtime.corrected_base.json",
}
# marker를 추가하는 Task는 이 allowlist도 함께 갱신해야 한다.
# 예정: V5-B-1.4가 rag_load.kosa_text2sql.json을 추가한다.
# 예정: V5-CM-2.6 묶음 3(공용 적용)이 postgres_profile.<database>.json 3종을 추가한다.
#       묶음 1(코드·격리)에서는 marker 파일을 만들지 않으므로 지금 넣으면
#       아래 실물 대조가 깨진다.
REMAINING_MARKERS = {
    "rag_load.kosa_agent.json",
    "rag_load.kosa_agent_e2e.json",
}


def _load_artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


# --- 1. v2 스키마 계약 ----------------------------------------------------------


def test_v2_schema_contract() -> None:
    artifact = _load_artifact()

    assert list(artifact) == [
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "received_date",
        "archive",
        "inventory_scope",
        "intake_artifact",
        "supersedes",
    ]
    assert artifact["format_version"] == 2
    assert artifact["artifact_type"] == "dataset_epoch_registration"
    assert artifact["dataset_epoch"] == TARGET_EPOCH
    assert artifact["received_date"] == "2026-08-18"
    assert set(artifact["archive"]) == {"filename", "sha256"}
    assert artifact["archive"]["filename"] == "project.zip"
    assert HEX_SHA256.fullmatch(artifact["archive"]["sha256"])
    assert artifact["inventory_scope"] == "selected_source_members"
    assert isinstance(artifact["intake_artifact"], str)
    assert artifact["supersedes"] == {
        "dataset_epoch": "kosa_0813",
        "isolated_to": "infra/bootstrap/history/kosa_0813/",
    }
    # v1의 전수 inventory는 v2 계약에 없다 — 원천은 intake와 CM-1.3 manifest v4다.
    assert "file_inventory" not in artifact
    assert "file_count" not in artifact


# --- 2. archive 3자 대조 --------------------------------------------------------


def test_archive_sha256_matches_reference_and_intake() -> None:
    """기준표 머리 SHA · epoch v2 · intake artifact의 3자 일치.

    `intake_artifact`가 경로 참조만 하는 대신(결정 7) 무결성은 이 대조가 담당한다.
    """
    documented = re.search(
        r"원본 SHA-256: `([0-9a-f]{64})`",
        REFERENCE_README.read_text(encoding="utf-8"),
    )
    assert documented is not None

    artifact = _load_artifact()
    intake = json.loads(INTAKE_PATH.read_text(encoding="utf-8"))

    assert artifact["archive"]["sha256"] == documented.group(1)
    assert artifact["archive"]["sha256"] == intake["archive"]["sha256"]


# --- 3. intake 참조 유효 --------------------------------------------------------


def test_intake_reference_is_valid() -> None:
    artifact = _load_artifact()

    referenced = REPOSITORY_ROOT / artifact["intake_artifact"]
    assert artifact["intake_artifact"] == "infra/bootstrap/final-zip-intake.json"
    assert referenced.is_file()

    intake = json.loads(referenced.read_text(encoding="utf-8"))
    assert intake["artifact_type"] == "final_zip_intake"
    assert intake["declared_target_epoch"] == artifact["dataset_epoch"]


# --- 4·5. 동시 참조 금지 — 구 로더 fail-fast 두 겹 -------------------------------


def test_old_loader_rejects_current_artifact_by_key_set() -> None:
    """구 파이프라인의 실제 차단 지점(`manifest_v3.py:425-438` 키 집합 검사)을 고정한다.

    v2 payload는 v1과 키 집합이 달라 epoch 문자열 비교에 도달하기 전에
    `ManifestSchemaError`로 fail-fast한다. 이것이 "동시 참조 금지"의 구현이다(계획 §0).
    """
    with pytest.raises(mv3.ManifestSchemaError):
        mv3.load_dataset_epoch(ARTIFACT_PATH)


def test_old_loader_epoch_guard_rejects_new_epoch(tmp_path: Path) -> None:
    """epoch 검증 자체(`manifest_v3.py:440-441`)도 고정한다.

    v1 키 집합을 그대로 두고 `dataset_epoch`만 새 epoch로 바꾼 합성 payload는 키
    검사를 통과하므로, 여기서 `ManifestMetadataError`가 나야 `:440-441`이 실제로
    새 epoch를 거부함이 증명된다. 저장소 파일이 아닌 합성 payload를 쓰므로 격리·발급
    상태와 무관하게 성립한다(Task 완료 기준 3-(b)).
    """
    synthetic = {
        "format_version": 1,
        "artifact_type": "dataset_epoch_registration",
        "dataset_epoch": TARGET_EPOCH,
        "received_date": "2026-08-18",
        "archive": {"filename": "project.zip", "sha256": "ab" * 32},
        "public_fault_ground_truth_available": False,
        "inventory_scope": "non_directory_zip_members",
        "file_count": 1,
        "file_inventory": [{"path": "a", "size_bytes": 1, "sha256": "ab" * 32}],
    }
    path = tmp_path / "dataset-epoch.json"
    path.write_text(json.dumps(synthetic, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mv3.ManifestMetadataError):
        mv3.load_dataset_epoch(path)


# --- 6. 격리 완결성 -------------------------------------------------------------


def test_isolation_is_complete_for_18_files() -> None:
    """격리 18파일이 history에만 있고 원위치에 없다."""
    for name in ISOLATED_REGISTRATION:
        assert (HISTORY_ROOT / name).is_file(), f"history에 없음: {name}"
    # 등록 3파일 중 dataset-epoch.json 원위치는 v2로 교체됐고(§2.2 계약이 위에서
    # 고정), 나머지 2개는 원위치에서 사라져야 한다.
    assert not (BOOTSTRAP_ROOT / "source-data-manifest.json").exists()
    assert not (BOOTSTRAP_ROOT / "corrected-data-manifest.json").exists()

    for name in ISOLATED_MANIFESTS:
        assert (HISTORY_ROOT / "manifests" / name).is_file(), f"history에 없음: {name}"
        assert not (
            BOOTSTRAP_ROOT / "manifests" / name
        ).exists(), f"원위치 잔존: {name}"

    for name in ISOLATED_MARKERS:
        assert (HISTORY_ROOT / "markers" / name).is_file(), f"history에 없음: {name}"
        assert not (BOOTSTRAP_ROOT / "markers" / name).exists(), f"원위치 잔존: {name}"

    assert (
        len(ISOLATED_REGISTRATION) + len(ISOLATED_MANIFESTS) + len(ISOLATED_MARKERS)
        == 18
    )


# --- 7. 현행 잔류 artifact allowlist --------------------------------------------


def test_remaining_artifacts_are_allowlisted_and_secret_free() -> None:
    """현행 manifest·RAG marker만 남고 marker payload에는 비밀값이 없어야 한다.

    RAG marker 2개는 Knowledge 적재 완료 증적으로 의도적으로 커밋한다. 파일 집합을
    allowlist로 고정해 다른 실행 marker나 실수로 생성된 JSON이 섞이면 실패시킨다.
    """
    remaining = {path.name for path in (BOOTSTRAP_ROOT / "manifests").glob("*.json")}
    assert remaining == REMAINING_MANIFESTS

    marker_paths = sorted((BOOTSTRAP_ROOT / "markers").glob("*.json"))
    assert {path.name for path in marker_paths} == REMAINING_MARKERS
    for path in marker_paths:
        mv3.scan_for_sensitive_values(json.loads(path.read_text(encoding="utf-8")))
