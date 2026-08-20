"""V4-CM-1.5 base schema SQL과 profile manifest를 DB 없이 검증한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# V5-CM-1.2 epoch 발급으로 kosa_0813 artifact가 격리돼 깨지는 테스트의 개별 skip.
# 해제 경로는 사유에 적힌 후속 Task가 소유한다(작업계획 §2.5·§6).
SKIP_KOSA_0813 = pytest.mark.skip(
    reason="kosa_0813 폐기(V5-CM-1.2) — V5-CM-2.2가 대체, V5-CM-1.6이 삭제"
)


SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import bootstrap_base_schema as bootstrap  # noqa: E402
import manifest_v3 as mv3  # noqa: E402


class TestBaseSchemaSql:
    def test_statement_inventory_is_schema_only(self) -> None:
        sql, statements = bootstrap.load_and_validate_sql()

        assert len(statements) == 24
        assert sum(item.upper().startswith("CREATE TABLE") for item in statements) == 9
        assert sum(item.upper().startswith("CREATE INDEX") for item in statements) == 4
        assert sum(item.upper().startswith("COMMENT ON") for item in statements) == 11
        assert (
            bootstrap.FORBIDDEN_SQL.search(bootstrap._strip_sql_comments(sql)) is None
        )

    @pytest.mark.parametrize(
        "statement",
        [
            "DROP TABLE lot_history;",
            "TRUNCATE action_history;",
            "DELETE FROM lot_history;",
            "UPDATE lot_history SET lot_id = 'x';",
            "INSERT INTO lot_history VALUES ('x');",
            "COPY lot_history FROM '/tmp/x.csv';",
            "GRANT SELECT ON lot_history TO role_name;",
            "REVOKE SELECT ON lot_history FROM role_name;",
            "CREATE DATABASE unsafe;",
            "CREATE ROLE unsafe;",
            "ALTER ROLE unsafe LOGIN;",
            "BEGIN;",
            "COMMIT;",
        ],
    )
    def test_mutating_or_role_statements_are_rejected(self, statement: str) -> None:
        with pytest.raises(bootstrap.BootstrapError, match="금지문"):
            bootstrap.split_sql_statements(statement)

    def test_comments_do_not_trigger_forbidden_keyword_scan(self) -> None:
        assert bootstrap.split_sql_statements(
            "-- INSERT is prohibited here\nCREATE TABLE fixture (id integer);"
        ) == ["CREATE TABLE fixture (id integer)"]

    def test_missing_final_semicolon_is_rejected(self) -> None:
        with pytest.raises(bootstrap.BootstrapError, match="세미콜론"):
            bootstrap.split_sql_statements("CREATE TABLE fixture (id integer)")

    def test_corrected_semantic_comments_are_explicit(self) -> None:
        sql = bootstrap.BASE_SCHEMA_SQL_PATH.read_text(encoding="utf-8")

        assert "웨이퍼 번호 1~25" in sql
        assert "공개 Fault 정답이 아니며 판단 입력 금지" in sql
        assert "제품 CD 품질 근거이며 Fault Mode 정답이 아님" in sql
        assert "Summary 동적 CL±3σ는 별도 계산" in sql
        assert "클린데이터셋/postgres/<table>.csv" in sql
        assert "웨이퍼 번호 1~10" not in sql
        assert "정답 라벨(모델 평가용)" not in sql
        assert "클린_데이터셋" not in sql

    def test_static_signature_covers_exact_base_objects(self) -> None:
        signature = bootstrap.EXPECTED_SIGNATURE

        assert set(signature["tables"]) == set(bootstrap.BASE_COLUMNS)
        assert len(signature["tables"]) == bootstrap.EXPECTED_TABLE_COUNT
        assert set(bootstrap.PRIMARY_KEYS) == set(bootstrap.BASE_COLUMNS)
        assert len(bootstrap.EXPLICIT_INDEXES) == 4
        assert bootstrap.EXPECTED_SIGNATURE_SHA256 == bootstrap._canonical_hash(
            bootstrap.build_expected_signature()
        )


class TestBaseSchemaManifest:
    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    @SKIP_KOSA_0813
    def test_tracked_manifest_is_deterministic(self, profile: str) -> None:
        path = bootstrap.MANIFEST_ROOT / f"{profile}.base_schema.json"
        tracked = json.loads(path.read_text(encoding="utf-8"))
        generated = bootstrap.build_base_manifest(profile)

        assert tracked == generated
        assert tracked["format_version"] == 3
        assert tracked["bootstrap_stage"] == "base_schema"
        assert tracked["schema_stage"] == "base"
        assert tracked["applied_migrations"] == []
        assert set(tracked["tables"]) == set(bootstrap.BASE_COLUMNS)
        assert all(
            table["verification_policy"] == "bootstrap_empty"
            and table["row_count"] == 0
            and table["content_hash"] == bootstrap.EMPTY_ROWS_SHA256
            for table in tracked["tables"].values()
        )
        mv3.validate_manifest_schema(
            tracked,
            expected_artifact_type="db_bootstrap",
            expected_profile=profile,
            expected_stage="base_schema",
            expected_archive_sha256=tracked["source_archive_sha256"],
        )

    @SKIP_KOSA_0813
    def test_runtime_and_evaluation_profiles_do_not_share_targets(self) -> None:
        runtime = bootstrap.build_base_manifest("runtime")
        evaluation = bootstrap.build_base_manifest("evaluation")

        assert runtime["applies_to"] == ["kosa_agent", "kosa_agent_e2e"]
        assert evaluation["applies_to"] == ["kosa_text2sql"]
        assert set(runtime["applies_to"]).isdisjoint(evaluation["applies_to"])
