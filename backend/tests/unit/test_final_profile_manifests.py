"""최종 profile manifest candidate builder 계약 (`V5-CM-1.8`).

**DB 없이 결정론적으로 만들어지는가**가 핵심이다. 계획 §0.2가 "DB 상태를 읽어 그대로
manifest로 복사하지 않는다"고 못박았고, 그 규율이 코드에서 지켜지는지를 잠근다.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime  # noqa: E402
import apply_reference_extensions_v5 as v5  # noqa: E402
import final_profile_manifests as builder  # noqa: E402
import manifest_v3  # noqa: E402

#: provenance Gate를 통과했다고 가정한 Runtime RAG 입력.
GOOD_RAG: dict[str, dict[str, Any]] = {
    "document": {
        "row_count": builder.RUNTIME_RAG_ROWS["document"],
        "content_hash": "a" * 64,
    },
    "document_chunk": {
        "row_count": builder.RUNTIME_RAG_ROWS["document_chunk"],
        "content_hash": "b" * 64,
    },
}


@pytest.fixture(scope="module")
def source() -> dict[str, Any]:
    return builder.load_source_manifest()


def _candidate(profile: str, source: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    if profile == "runtime":
        kwargs.setdefault("runtime_rag", GOOD_RAG)
    return builder.build_profile_candidate(profile, source=source, **kwargs)


# ---------------------------------------------------------------------------
# 1. inventory — 22 / 13
# ---------------------------------------------------------------------------


class TestInventory:
    @pytest.mark.parametrize(
        ("profile", "expected"), [("runtime", 22), ("evaluation", 13)]
    )
    def test_table_count_matches_the_final_contract(
        self, profile: str, expected: int, source: dict[str, Any]
    ) -> None:
        candidate = _candidate(profile, source)

        assert len(candidate["tables"]) == expected
        assert v5.FINAL_PROFILE_TABLE_COUNTS[profile] == expected

    def test_runtime_carries_the_agent_runtime_tables(
        self, source: dict[str, Any]
    ) -> None:
        tables = set(_candidate("runtime", source)["tables"])

        assert set(apply_agent_runtime.EXPECTED_TABLE_COLUMNS) <= tables

    def test_evaluation_does_not_carry_them(self, source: dict[str, Any]) -> None:
        tables = set(_candidate("evaluation", source)["tables"])

        assert not (set(apply_agent_runtime.EXPECTED_TABLE_COLUMNS) & tables)

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_retired_legacy_table_is_absent(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        """`document_corpus`는 `V5-B-1.1`이 정리했다 — 두 profile 모두 금지다."""

        assert "document_corpus" not in _candidate(profile, source)["tables"]

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_view_is_not_a_base_table(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        assert "v_alarm_event" not in _candidate(profile, source)["tables"]


# ---------------------------------------------------------------------------
# 2. profile projection — §3.4 표
# ---------------------------------------------------------------------------


class TestTablePolicy:
    def test_runtime_action_history_is_empty_without_a_fixture(
        self, source: dict[str, Any]
    ) -> None:
        entry = _candidate("runtime", source)["tables"]["action_history"]

        assert entry["verification_policy"] == "bootstrap_empty"
        assert entry["row_count"] == 0
        assert "fixture_type" not in entry

    def test_evaluation_action_history_carries_the_source_rows(
        self, source: dict[str, Any]
    ) -> None:
        entry = _candidate("evaluation", source)["tables"]["action_history"]
        origin = source["tables"]["action_history"]

        assert entry["verification_policy"] == "immutable_content"
        assert entry["row_count"] == origin["row_count"] == 12
        assert entry["content_hash"] == origin["content_hash"]
        assert entry["fixture_type"] == "REFERENCE"

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_base_eight_come_from_the_source_manifest(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        """**base는 만들지 않고 옮긴다.** 값을 새로 계산하면 정본이 둘이 된다."""

        tables = _candidate(profile, source)["tables"]

        for table in sorted(builder.SOURCE_TABLES - {"action_history"}):
            origin = source["tables"][table]
            assert tables[table]["row_count"] == origin["row_count"], table
            assert tables[table]["content_hash"] == origin["content_hash"], table
            assert tables[table]["columns"] == origin["columns"], table
            assert tables[table]["verification_policy"] == "immutable_content"

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_r03_is_empty_and_carries_twelve_columns(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        """`A-1.4` 전이라 0행이고, 컬럼은 **V5 12개**다(§3.5-2)."""

        entry = _candidate(profile, source)["tables"][v5.R03_TABLE]

        assert entry["verification_policy"] == "bootstrap_empty"
        assert entry["row_count"] == 0
        assert entry["columns"] == [n for n, _t, _l, _x in v5.R03_COLUMNS]
        assert len(entry["columns"]) == 12
        assert "member_wafer_refs" in entry["columns"]
        assert "member_alarm_refs" in entry["columns"]
        assert "member_refs" not in entry["columns"]

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_nl_query_log_is_schema_only(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        entry = _candidate(profile, source)["tables"]["nl_query_log"]

        assert entry["verification_policy"] == "schema_only"
        assert "row_count" not in entry
        assert "content_hash" not in entry

    def test_evaluation_rag_is_empty(self, source: dict[str, Any]) -> None:
        """`V5-B-1.1`이 Evaluation schema를 적용한 뒤 0행이다."""

        tables = _candidate("evaluation", source)["tables"]

        for table in ("document", "document_chunk"):
            assert tables[table]["row_count"] == 0, table
            assert tables[table]["verification_policy"] == "bootstrap_empty"

    def test_runtime_document_pins_content_minus_the_load_timestamp(
        self, source: dict[str, Any]
    ) -> None:
        """**`created_at`만 hash에서 뺀다**(구현리뷰 16차 필수 1).

        `schema_only`로 두면 3행이 0행이 돼도, 업무 컬럼이 변조돼도 통과한다 —
        검증을 없애는 것이지 해결이 아니다.
        """

        entry = _candidate("runtime", source)["tables"]["document"]

        assert entry["verification_policy"] == "immutable_content"
        assert entry["row_count"] == 3
        assert manifest_v3.HEX_SHA256_PATTERN.fullmatch(entry["content_hash"])
        assert "created_at" in entry["columns"]
        assert "created_at" not in entry["content_columns"]
        assert entry["content_columns"] == [
            c for c in entry["columns"] if c != "created_at"
        ]

    def test_only_the_load_timestamp_is_excluded(self, source: dict[str, Any]) -> None:
        """catalog 대조는 전체 컬럼으로 그대로 한다."""

        tables = _candidate("runtime", source)["tables"]

        assert "content_columns" not in tables["document_chunk"]
        for name, entry in tables.items():
            if name == "document":
                continue
            assert "content_columns" not in entry, name

    def test_runtime_document_chunk_still_pins_content(
        self, source: dict[str, Any]
    ) -> None:
        entry = _candidate("runtime", source)["tables"]["document_chunk"]

        assert entry["verification_policy"] == "immutable_content"
        assert entry["row_count"] == builder.RUNTIME_RAG_ROWS["document_chunk"]

    def test_runtime_only_tables_start_empty(self, source: dict[str, Any]) -> None:
        tables = _candidate("runtime", source)["tables"]

        for table in apply_agent_runtime.EXPECTED_TABLE_COLUMNS:
            assert tables[table]["row_count"] == 0, table
            assert tables[table]["verification_policy"] == "bootstrap_empty"

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_empty_hash_is_derived_not_copied(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        """빈 hash를 문자열 상수로 두면 해싱 규약이 바뀔 때 갈린다(§3.4)."""

        expected = manifest_v3.hash_canonical_rows([])
        tables = _candidate(profile, source)["tables"]

        empties = [
            entry
            for entry in tables.values()
            if entry["verification_policy"] == "bootstrap_empty"
        ]
        assert empties
        for entry in empties:
            assert entry["content_hash"] == expected


# ---------------------------------------------------------------------------
# 3. Runtime RAG provenance — live 값을 그냥 복사하지 않는다
# ---------------------------------------------------------------------------


class TestRuntimeRagProvenance:
    def test_runtime_needs_the_provenance_input(self, source: dict[str, Any]) -> None:
        """**입력이 없으면 candidate 자체를 만들지 않는다**(§3.4-5)."""

        with pytest.raises(builder.CandidateError, match="RAG_PROVENANCE_REQUIRED"):
            builder.build_profile_candidate("runtime", source=source)

    @pytest.mark.parametrize("table", ["document_chunk"])
    def test_a_missing_table_is_refused(
        self, table: str, source: dict[str, Any]
    ) -> None:
        partial = {k: v for k, v in GOOD_RAG.items() if k != table}

        with pytest.raises(builder.CandidateError, match="RAG_PROVENANCE"):
            builder.build_profile_candidate(
                "runtime", source=source, runtime_rag=partial
            )

    @pytest.mark.parametrize(
        ("table", "row_count"),
        [
            ("document_chunk", 24),
            ("document_chunk", 0),
            ("document_chunk", 26),
        ],
    )
    def test_a_drifted_row_count_is_refused(
        self, table: str, row_count: int, source: dict[str, Any]
    ) -> None:
        drifted = copy.deepcopy(GOOD_RAG)
        drifted[table]["row_count"] = row_count

        with pytest.raises(builder.CandidateError, match="RAG_PROVENANCE_MISMATCH"):
            builder.build_profile_candidate(
                "runtime", source=source, runtime_rag=drifted
            )

    @pytest.mark.parametrize("content_hash", ["", "zz", "A" * 64, None, 1])
    def test_a_malformed_hash_is_refused(
        self, content_hash: Any, source: dict[str, Any]
    ) -> None:
        drifted = copy.deepcopy(GOOD_RAG)
        drifted["document_chunk"]["content_hash"] = content_hash

        with pytest.raises(builder.CandidateError, match="RAG_PROVENANCE_MISMATCH"):
            builder.build_profile_candidate(
                "runtime", source=source, runtime_rag=drifted
            )

    def test_the_expected_rows_match_the_loaded_corpus(self) -> None:
        assert dict(builder.RUNTIME_RAG_ROWS) == {"document": 3, "document_chunk": 35}


# ---------------------------------------------------------------------------
# 4. source manifest 입력 계약
# ---------------------------------------------------------------------------


class TestSourceManifestGate:
    def test_the_repository_artifact_loads(self) -> None:
        payload = builder.load_source_manifest()

        assert payload["dataset_epoch"] == manifest_v3.DATASET_EPOCH
        assert payload["source_archive_sha256"] == manifest_v3.FINAL_ARCHIVE_SHA256
        assert set(payload["tables"]) == builder.SOURCE_TABLES

    def test_the_source_table_set_is_the_v4_one_not_v3(self) -> None:
        """v3의 8종은 `dim_parameter`가 없다 — 잘못 쓰면 base 하나가 빠진다."""

        import build_source_manifest_v4 as source_v4

        assert builder.SOURCE_TABLES == frozenset(source_v4.TABLE_MEMBERS)
        assert len(builder.SOURCE_TABLES) == 9
        assert "dim_parameter" in builder.SOURCE_TABLES
        assert builder.SOURCE_TABLES != frozenset(manifest_v3.SOURCE_TABLE_FILES)

    #: (라벨, 변조 함수). 파일·주입 두 경로에서 **모두** 거부돼야 한다.
    MUTATIONS = [
        ("epoch", lambda d: d.__setitem__("dataset_epoch", "kosa_0813")),
        ("archive", lambda d: d.__setitem__("source_archive_sha256", "c" * 64)),
        ("format_version", lambda d: d.__setitem__("format_version", 999)),
        ("artifact_type", lambda d: d.__setitem__("artifact_type", "db_bootstrap")),
        ("hash_algorithm", lambda d: d.__setitem__("hash_algorithm", "sha256")),
        ("top-level extra", lambda d: d.__setitem__("surprise", 1)),
        ("top-level 누락(비참조)", lambda d: d.pop("derived_from")),
        ("top-level 누락(참조)", lambda d: d.pop("format_version")),
        ("table 누락", lambda d: d["tables"].pop("dim_parameter")),
        (
            "table 추가",
            lambda d: d["tables"].__setitem__(
                "extra", copy.deepcopy(d["tables"]["dim_parameter"])
            ),
        ),
        ("entry key 누락", lambda d: d["tables"]["dim_parameter"].pop("primary_key")),
        ("entry key 추가", lambda d: d["tables"]["dim_parameter"].__setitem__("x", 1)),
        (
            "row_count",
            lambda d: d["tables"]["dim_parameter"].__setitem__("row_count", 9),
        ),
        (
            "columns",
            lambda d: d["tables"]["dim_parameter"].__setitem__("columns", ["x"]),
        ),
        (
            "column_types 값",
            lambda d: d["tables"]["evaluation"]["column_types"].__setitem__(
                "wafer", "numeric"
            ),
        ),
        (
            "column_types 키",
            lambda d: d["tables"]["evaluation"]["column_types"].pop("wafer"),
        ),
        (
            "primary_key",
            lambda d: d["tables"]["evaluation"].__setitem__("primary_key", ["nope"]),
        ),
        (
            "content_hash",
            lambda d: d["tables"]["evaluation"].__setitem__("content_hash", "z" * 64),
        ),
    ]

    @pytest.mark.parametrize(("label", "mutate"), MUTATIONS)
    def test_a_drifted_source_manifest_is_refused_from_the_file_path(
        self, tmp_path: Path, source: dict[str, Any], label: str, mutate
    ) -> None:
        drifted = copy.deepcopy(source)
        mutate(drifted)
        path = tmp_path / "source-manifest-v4.json"
        path.write_text(json.dumps(drifted), encoding="utf-8")

        with pytest.raises(builder.CandidateError):
            builder.load_source_manifest(path)

    @pytest.mark.parametrize(("label", "mutate"), MUTATIONS)
    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_injected_path_is_gated_the_same_way(
        self, source: dict[str, Any], profile: str, label: str, mutate
    ) -> None:
        """**주입 경로가 loader를 우회했다**(구현리뷰 7차 필수 1).

        검증을 loader에만 두면 `source=`로 넣은 입력은 epoch·archive 검사조차 받지
        않는다. 손상된 source가 candidate와 DB logical type의 정본으로 승격된다.
        """

        drifted = copy.deepcopy(source)
        mutate(drifted)

        with pytest.raises(builder.CandidateError):
            _candidate(profile, drifted)

    def test_the_payload_digest_pins_the_whole_artifact(
        self, source: dict[str, Any]
    ) -> None:
        """유효한 64자리이기만 한 변조 hash도 거부한다."""

        assert (
            manifest_v3.canonical_payload_sha256(source)
            == builder.SOURCE_PAYLOAD_SHA256
        )


# ---------------------------------------------------------------------------
# 5. 결과물 계약
# ---------------------------------------------------------------------------


class TestCandidateEnvelope:
    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_candidate_passes_the_active_validator(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        """**자기 산출물을 기존 검증기로 다시 본다**(계획 §3.6)."""

        candidate = _candidate(profile, source)

        manifest_v3.validate_manifest_schema(
            candidate,
            expected_artifact_type="db_bootstrap",
            expected_profile=profile,
            expected_stage=candidate["bootstrap_stage"],
            expected_archive_sha256=manifest_v3.FINAL_ARCHIVE_SHA256,
        )

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_envelope_uses_the_final_epoch_and_stage(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        candidate = _candidate(profile, source)
        stage = v5.REGISTRAR_STAGE_BY_PROFILE[profile]
        contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]

        assert candidate["dataset_epoch"] == manifest_v3.DATASET_EPOCH
        assert candidate["source_archive_sha256"] == manifest_v3.FINAL_ARCHIVE_SHA256
        assert candidate["correction_version"] == manifest_v3.FINAL_CORRECTION_VERSION
        assert candidate["bootstrap_stage"] == stage
        assert candidate["schema_stage"] == contract.schema_stage
        assert candidate["applied_migrations"] == list(contract.applied_migrations)
        assert candidate["applies_to"] == list(manifest_v3.PROFILE_APPLIES_TO[profile])

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_candidate_is_not_the_superseded_lineage(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        """history 계보로 오인될 수 없다."""

        candidate = _candidate(profile, source)

        assert (
            candidate["source_archive_sha256"] != manifest_v3.SUPERSEDED_ARCHIVE_SHA256
        )
        with pytest.raises(manifest_v3.ManifestMetadataError):
            manifest_v3.validate_historical_bootstrap_manifest(
                candidate, profile=profile, stage=candidate["bootstrap_stage"]
            )

    def test_building_is_deterministic(self, source: dict[str, Any]) -> None:
        """같은 입력이면 같은 결과다 — live 값이 섞이면 깨진다."""

        first = builder.build_final_bundle(source=source, runtime_rag=GOOD_RAG)
        second = builder.build_final_bundle(source=source, runtime_rag=GOOD_RAG)

        assert first == second
        assert set(first) == {"evaluation", "runtime"}

    def test_the_bundle_fails_whole_when_one_profile_fails(
        self, source: dict[str, Any]
    ) -> None:
        with pytest.raises(builder.CandidateError):
            builder.build_final_bundle(source=source)

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_a_short_inventory_is_refused_at_build_time(
        self, monkeypatch: pytest.MonkeyPatch, profile: str, source: dict[str, Any]
    ) -> None:
        """**inventory 수 가드를 직접 겨냥한다.**

        결과 개수만 단언하면 가드를 지워도 통과한다 — 정상 입력에서는 어차피 22/13이
        나오기 때문이다. 구성 목록을 줄여 가드가 실제로 발화하는지 본다.
        """

        monkeypatch.setattr(builder, "REFERENCE_TABLES", builder.REFERENCE_TABLES[:-1])

        with pytest.raises(builder.CandidateError, match="inventory"):
            _candidate(profile, source)

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    @pytest.mark.parametrize("delta", [1, -1])
    def test_the_guard_compares_against_the_cm31_constant(
        self,
        monkeypatch: pytest.MonkeyPatch,
        profile: str,
        delta: int,
        source: dict[str, Any],
    ) -> None:
        """가드는 `V5-CM-3.1`의 22/13 상수와 대조한다 — 그 상수가 바뀌면 발화한다."""

        counts = dict(v5.FINAL_PROFILE_TABLE_COUNTS)
        counts[profile] += delta
        monkeypatch.setattr(v5, "FINAL_PROFILE_TABLE_COUNTS", counts)

        with pytest.raises(builder.CandidateError, match="inventory"):
            _candidate(profile, source)

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_builder_refuses_its_own_invalid_output(
        self, monkeypatch: pytest.MonkeyPatch, profile: str, source: dict[str, Any]
    ) -> None:
        """**자체 검증 호출을 직접 겨냥한다.**

        결과를 밖에서 다시 검증하는 테스트만 두면 builder 안의 호출을 지워도 통과한다.
        envelope을 깨뜨려 builder가 스스로 거부하는지 본다.
        """

        # `PROFILE_APPLIES_TO`를 바꾸면 builder와 validator가 **같은** 값을 보므로
        # 어긋나지 않는다. validator만 거부하는 형태를 만든다 — `bootstrap_empty`는
        # 0행이어야 한다.
        def _bad_empty(columns: Any) -> dict[str, Any]:
            return {
                "columns": list(columns),
                "verification_policy": "bootstrap_empty",
                "row_count": 1,
                "content_hash": manifest_v3.hash_canonical_rows([]),
            }

        monkeypatch.setattr(builder, "_empty_entry", _bad_empty)

        with pytest.raises(manifest_v3.VerificationError):
            _candidate(profile, source)

    def test_an_unknown_profile_is_refused(self, source: dict[str, Any]) -> None:
        with pytest.raises(builder.CandidateError):
            builder.build_profile_candidate("corrected", source=source)

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_no_secret_reaches_the_candidate(
        self, profile: str, source: dict[str, Any]
    ) -> None:
        payload = json.dumps(_candidate(profile, source), ensure_ascii=False)

        for marker in ("postgresql://", "password", "/Users/", "C:\\", "@"):
            assert marker not in payload, marker
