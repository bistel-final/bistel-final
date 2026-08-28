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

# 격리 19파일. history/ 대비 원위치 부재를 함께 고정한다.
# `V5-CM-1.6`이 `runtime.corrected_base.json`을 추가로 격리했다.
ISOLATED_REGISTRATION = (
    "dataset-epoch.json",
    "source-data-manifest.json",
    "corrected-data-manifest.json",
)
ISOLATED_MANIFESTS = (
    "evaluation.base_schema.json",
    "runtime.base_schema.json",
    "evaluation.corrected_base.json",
    # `V5-CM-1.6`이 구 corrected 소비자를 제거하면서 함께 격리했다.
    "runtime.corrected_base.json",
)

#: **history에는 남지만 active 위치에 다시 발급된** manifest.
#:
#: `V5-CM-1.2`가 구 epoch 사본을 history로 격리했고, `V5-CM-2.7`이 최종
#: `project.zip`의 `master.cypher`로 **같은 이름의 다른 artifact**를 새로 발급했다.
#: history 사본은 복원한 것이 아니라 구 epoch 이력으로 남는다 — 두 파일의 내용은
#: 다르며 회귀가 그것을 고정한다.
REISSUED_MANIFESTS = ("neo4j.graph.json",)

#: history에 구 epoch 사본이 있고 **최종 적용으로 다시 발급된** marker.
#: `V5-CM-2.7` 묶음 2가 `ADOPTED_EXISTING`으로 발급했다.
REISSUED_MARKERS = ("neo4j_graph.neo4j.json",)
ISOLATED_MARKERS = (
    "base_schema.kosa_agent_e2e.json",
    "corrected.v1.json",
    "corrected_base.kosa_agent.json",
    "corrected_base.kosa_agent_e2e.json",
    "evaluation_mock.kosa_text2sql.json",
    "reference_extensions.kosa_agent.json",
    "reference_extensions.kosa_agent_e2e.json",
    "reference_extensions.kosa_text2sql.json",
    "runtime_clean.kosa_agent.json",
    "runtime_clean.kosa_agent_e2e.json",
)

# 잔류 2파일 — 부재가 오류이고 읽는 코드가 현행 앱인 것만 남는다.
#
# `runtime.corrected_base.json`은 `V5-CM-1.6`이 마지막 소비자
# (`apply_agent_runtime`의 corrected producer)를 제거하면서 history로 옮겼다.
# 남은 둘은 `V5-CM-1.8`이 최종 epoch manifest로 교체한다.
#: `V5-CM-1.8`이 active를 final로 교체했다 — `evaluation_mock`은 history로 갔고
#: `evaluation_reference`가 그 자리를 대신한다.
REMAINING_MANIFESTS = {
    # `V5-CM-3.3` successor. predecessor `runtime.runtime_clean.json`은 CM-3.2
    # marker가 증명하는 계약이라 **덮어쓰지 않고 나란히 둔다**.
    "runtime.runtime_guarded.json",
    "runtime.runtime_clean.json",
    "evaluation.evaluation_reference.json",
    # `V5-CM-2.7`이 최종 `master.cypher`로 발급한다.
    "neo4j.graph.json",
    # `V5-CM-3.4` successor. predecessor `runtime.runtime_guarded.json`은 CM-3.3
    # marker가 증명하는 계약이라 **덮어쓰지 않고 나란히 둔다**.
    "runtime.runtime_checkpointed.json",
}
# marker를 추가하는 Task는 이 allowlist도 함께 갱신해야 한다.
# 예정: V5-CM-2.6 묶음 3(공용 적용)이 postgres_profile.<database>.json 3종을 추가한다.
#       묶음 1(코드·격리)에서는 marker 파일을 만들지 않으므로 지금 넣으면
#       아래 실물 대조가 깨진다.
REMAINING_MARKERS = {
    # RAG 적재 증적 (`V5-B-1.3`)
    "rag_load.kosa_agent.json",
    "rag_load.kosa_agent_e2e.json",
    # RAG evaluation DB 적재 증적 (`V5-B-1.4`)
    "rag_load.kosa_text2sql.json",
    # Runtime 002 적용 증적 (`V5-CM-3.2` 묶음 2)
    #
    # 구 계보 `runtime_clean.<db>.json`과 **이름이 다르다.** 같은 이름을 재사용하면
    # `history/kosa_0813/markers/`의 폐기 marker를 final로 잘못 복원·승격하는 사고가
    # 구조적으로 가능해진다.
    "agent_runtime_final.kosa_agent.json",
    "agent_runtime_final.kosa_agent_e2e.json",
    # Runtime 003 적용 증적 (`V5-CM-3.3` 묶음 2)
    #
    # predecessor `agent_runtime_final.<db>.json`을 **덮어쓰지 않는다.** CM-3.2가
    # 증명한 `runtime_clean` 계약과 CM-3.3이 증명한 `runtime_guarded` 계약은 서로
    # 다른 것을 주장하며, 둘을 잇는 것은 이 marker의
    # `baseline_schema_signature_sha256`이다.
    "agent_severity_guard_final.kosa_agent.json",
    "agent_severity_guard_final.kosa_agent_e2e.json",
    # Neo4j 최종 graph 적용 증적 (`V5-CM-2.7` 묶음 2)
    "neo4j_graph.neo4j.json",
    # checkpoint 저장소 적용 증적 (`V5-CM-3.4` 묶음 2)
    #
    # **runtime 2 DB만이다.** checkpoint는 Agent graph의 thread 상태를 담으므로
    # 평가 DB(`kosa_text2sql`)에는 적용 대상이 아니다 — `assert_runtime_database()`가
    # 거부한다. 그래서 여기에도 `kosa_text2sql` 이름이 오면 안 된다.
    "checkpoint_setup_final.kosa_agent.json",
    "checkpoint_setup_final.kosa_agent_e2e.json",
    # 공용 3-DB role/grant 적용 증적 (`V5-CM-3.5` 묶음 3)
    #
    # `core`는 role·CONNECT·스키마·테이블 GRANT를, `checkpoint`는 checkpoint 4종의
    # DML GRANT를 각각 증명한다. **한 marker로 합치지 않는다** — checkpoint table은
    # `V5-CM-3.4` 적용 이후에만 존재하므로 두 적용은 선행관계가 다르고, core만 적용된
    # 상태와 둘 다 적용된 상태를 marker로 구분할 수 있어야 한다.
    #
    # `checkpoint` 쪽에 `kosa_text2sql`이 없는 이유는 바로 위 `checkpoint_setup_final`과
    # 같다. `core`는 평가 DB에도 role·grant가 필요하므로 3 DB 전부 있다.
    "role_matrix_core.kosa_agent.json",
    "role_matrix_core.kosa_agent_e2e.json",
    "role_matrix_core.kosa_text2sql.json",
    "role_matrix_checkpoint.kosa_agent.json",
    "role_matrix_checkpoint.kosa_agent_e2e.json",
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


# --- 4·5. 전환 완료 — active loader는 최종 epoch을 읽는다 -----------------------
#
# `V5-CM-1.2`는 "구 로더가 새 artifact를 거부한다"를 동시 참조 금지로 고정했다.
# `V5-CM-1.8`이 loader를 v2로 전환하면서 그 전제를 **해제**한다. 삭제하지 않고
# 역방향으로 뒤집어, 전환이 실제로 끝났다는 사실을 계속 지킨다(계획 §3.1·§3.8).
#
# 거부 계약은 두 축으로 **분리**한다. 한 fixture가 두 실패 원인을 동시에 가지면
# schema-version 때문에 실패했는지 epoch drift 때문인지 구분되지 않는다.


def test_the_active_loader_reads_the_final_artifact() -> None:
    """전환 완료. active loader가 저장소의 v2 artifact를 그대로 읽는다."""

    epoch = mv3.load_dataset_epoch(ARTIFACT_PATH)

    assert epoch["format_version"] == mv3.DATASET_EPOCH_FORMAT_VERSION == 2
    assert epoch["dataset_epoch"] == mv3.DATASET_EPOCH == TARGET_EPOCH
    assert epoch["archive"]["sha256"] == mv3.FINAL_ARCHIVE_SHA256
    assert epoch["supersedes"]["dataset_epoch"] == mv3.SUPERSEDED_DATASET_EPOCH


def test_v1_shape_is_rejected_even_with_the_final_epoch(tmp_path: Path) -> None:
    """**축 1 — schema version.**

    epoch 문자열은 최종값이지만 key shape가 v1이다. v1/v2 union parser를 만들면
    이것이 통과하고, 구 artifact가 최종 경로로 다시 흘러든다.
    """

    synthetic = {
        "format_version": 1,
        "artifact_type": "dataset_epoch_registration",
        "dataset_epoch": TARGET_EPOCH,
        "received_date": "2026-08-18",
        "archive": {"filename": "project.zip", "sha256": mv3.FINAL_ARCHIVE_SHA256},
        "public_fault_ground_truth_available": False,
        "inventory_scope": "non_directory_zip_members",
        "file_count": 1,
        "file_inventory": [{"path": "a", "size_bytes": 1, "sha256": "ab" * 32}],
    }
    path = tmp_path / "dataset-epoch.json"
    path.write_text(json.dumps(synthetic, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mv3.ManifestSchemaError):
        mv3.load_dataset_epoch(path)


def test_the_deprecated_epoch_is_rejected_in_exact_v2_shape(tmp_path: Path) -> None:
    """**축 2 — epoch drift.**

    key shape는 v2 exact인데 `dataset_epoch`만 폐기 epoch이다. schema 검사를 통과하므로
    epoch 비교가 실제로 동작해야만 거부된다.
    """

    synthetic = {
        "format_version": 2,
        "artifact_type": "dataset_epoch_registration",
        "dataset_epoch": mv3.SUPERSEDED_DATASET_EPOCH,
        "received_date": "2026-08-13",
        "archive": {"filename": "project.zip", "sha256": mv3.FINAL_ARCHIVE_SHA256},
        "inventory_scope": "selected_source_members",
        "intake_artifact": "infra/bootstrap/final-zip-intake.json",
        "supersedes": {
            "dataset_epoch": mv3.SUPERSEDED_DATASET_EPOCH,
            "isolated_to": mv3.SUPERSEDED_ISOLATION_ROOT,
        },
    }
    path = tmp_path / "dataset-epoch.json"
    path.write_text(json.dumps(synthetic, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mv3.ManifestMetadataError):
        mv3.load_dataset_epoch(path)


# --- 6. 격리 완결성 -------------------------------------------------------------


def test_isolation_is_complete() -> None:
    """격리 19파일이 history에만 있고 원위치에 없다."""
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

    # 재발급본은 **양쪽에 다 있다.** 다만 내용이 같으면 구 epoch를 복원한 것이므로
    # 실패시킨다(`V5-CM-2.7`).
    for name in REISSUED_MANIFESTS:
        history = HISTORY_ROOT / "manifests" / name
        active = BOOTSTRAP_ROOT / "manifests" / name
        assert history.is_file(), f"history에 없음: {name}"
        assert active.is_file(), f"active에 없음: {name}"
        assert history.read_bytes() != active.read_bytes(), f"구 epoch 복원: {name}"
        # **"다르다"만으로는 부족하다.** 임의의 다른 내용도 통과한다.
        # active가 **최종 발급본과 같은지**는 `test_master_cypher.py`의
        # `test_the_active_artifacts_match_the_final_source_exactly`가
        # `validate_generated_artifacts()`로 본다(구현리뷰 필수 1).
        assert json.loads(active.read_text(encoding="utf-8"))["dataset_epoch"] == (
            "fdc_final_20260818"
        )

    for name in ISOLATED_MARKERS:
        assert (HISTORY_ROOT / "markers" / name).is_file(), f"history에 없음: {name}"
        assert not (BOOTSTRAP_ROOT / "markers" / name).exists(), f"원위치 잔존: {name}"

    for name in REISSUED_MARKERS:
        history = HISTORY_ROOT / "markers" / name
        active = BOOTSTRAP_ROOT / "markers" / name
        assert history.is_file(), f"history에 없음: {name}"
        assert active.is_file(), f"active에 없음: {name}"
        assert history.read_bytes() != active.read_bytes(), f"구 epoch 복원: {name}"
        assert json.loads(active.read_text(encoding="utf-8"))["dataset_epoch"] == (
            "fdc_final_20260818"
        )

    assert (
        len(ISOLATED_REGISTRATION)
        + len(ISOLATED_MANIFESTS)
        + len(REISSUED_MANIFESTS)
        + len(ISOLATED_MARKERS)
        + len(REISSUED_MARKERS)
        == 19
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
