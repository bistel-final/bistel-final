"""verify_source_data.py 의 DB 연결 없이 검증 가능한 순수 로직만 다룬다.

canonical hash 계산과 manifest 비교는 실제 PostgreSQL 연결 없이도 재현성을
보장해야 하므로 이 부분만 unit 범위로 둔다. --profile runtime/evaluation 의
실제 DB 조회는 사내망에서만 가능해 이 저장소에서는 검증하지 못한다.
"""

import importlib.util
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_source_data.py"
_spec = importlib.util.spec_from_file_location("verify_source_data", MODULE_PATH)
verify_source_data = importlib.util.module_from_spec(_spec)
sys.modules["verify_source_data"] = verify_source_data
_spec.loader.exec_module(verify_source_data)
vsd = verify_source_data


class TestCanonicalizeRow:
    def test_nfc_normalizes_strings(self) -> None:
        decomposed = "가̣"  # 가 + 결합 문자(분해형)
        row = {"reason": decomposed}

        canonical = vsd._canonicalize_row(row)

        import unicodedata

        assert canonical["reason"] == unicodedata.normalize("NFC", decomposed)

    def test_non_string_values_untouched(self) -> None:
        row = {"count": 3, "flag": True, "note": None}

        canonical = vsd._canonicalize_row(row)

        assert canonical == row


class TestJsonDefault:
    def test_datetime_and_date_use_isoformat(self) -> None:
        dt = datetime(2026, 8, 12, 9, 30)
        d = date(2026, 8, 12)

        assert vsd._json_default(dt) == dt.isoformat()
        assert vsd._json_default(d) == d.isoformat()

    def test_decimal_uses_str(self) -> None:
        assert vsd._json_default(Decimal("1.50")) == "1.50"


class TestCompareTables:
    ALLOWLIST = vsd.ALLOWLIST_TABLES

    def _full_section(self, row_count: int = 1, content_hash: str = "deadbeef") -> dict:
        return {
            table: {"row_count": row_count, "content_hash": content_hash}
            for table in self.ALLOWLIST
        }

    def test_identical_sections_have_no_mismatch(self) -> None:
        section = self._full_section()

        assert vsd.compare_tables(section, section) == []

    def test_row_count_mismatch_is_reported(self) -> None:
        expected = self._full_section(row_count=51)
        actual = self._full_section(row_count=51)
        actual["fdc_alarm"] = {"row_count": 50, "content_hash": "deadbeef"}

        mismatches = vsd.compare_tables(actual, expected)

        assert len(mismatches) == 1
        assert "fdc_alarm" in mismatches[0]
        assert "행 수 불일치" in mismatches[0]

    def test_content_hash_mismatch_is_reported(self) -> None:
        expected = self._full_section(content_hash="aaaa")
        actual = self._full_section(content_hash="bbbb")

        mismatches = vsd.compare_tables(actual, expected)

        assert len(mismatches) == len(self.ALLOWLIST)
        assert all("content hash 불일치" in m for m in mismatches)

    def test_missing_table_in_manifest_is_reported(self) -> None:
        expected = self._full_section()
        del expected[self.ALLOWLIST[0]]
        actual = self._full_section()

        mismatches = vsd.compare_tables(actual, expected)

        assert any("기준값이 없습니다" in m for m in mismatches)


class TestHashCanonicalRows:
    """실제 프로덕션 함수(`_hash_canonical_rows`)를 직접 호출한다 — 별도로
    재구현한 helper 는 실제 정렬 전략의 회귀를 잡지 못하기 때문이다."""

    def test_row_input_order_does_not_affect_hash(self) -> None:
        rows_a = [{"id": 1, "name": "가"}, {"id": 2, "name": "나"}]
        rows_b = [{"id": 2, "name": "나"}, {"id": 1, "name": "가"}]

        assert vsd._hash_canonical_rows(rows_a) == vsd._hash_canonical_rows(rows_b)

    def test_different_content_gives_different_hash(self) -> None:
        rows_a = [{"id": 1, "name": "가"}]
        rows_b = [{"id": 1, "name": "나"}]

        assert vsd._hash_canonical_rows(rows_a) != vsd._hash_canonical_rows(rows_b)

    def test_sort_key_is_codepoint_not_locale(self) -> None:
        """정렬은 canonical JSON 문자열의 Python 기본(codepoint) 비교로
        고정한다 — DB collation 이 달라도 항상 같은 순서가 나와야 한다."""
        rows = [{"name": "z"}, {"name": "a"}, {"name": "가"}]

        digest = vsd._hash_canonical_rows(rows)

        expected_order = sorted(
            json.dumps({"name": v}, ensure_ascii=False, separators=(",", ":"))
            for v in ("z", "a", "가")
        )
        import hashlib

        expected_digest = hashlib.sha256(
            ("[" + ",".join(expected_order) + "]").encode("utf-8")
        ).hexdigest()
        assert digest == expected_digest


class TestCanonicalTableHashIdentifiers:
    def test_rejects_unsafe_column_identifier(self) -> None:
        conn = _FakeConnection(["safe"], {"safe": 1})

        with pytest.raises(ValueError, match="SQL 식별자 형식이 잘못됐습니다"):
            vsd.canonical_table_hash(conn, "fdc_alarm", ['alarm_id" FROM code_fault'])

    def test_rejects_unsafe_table_identifier(self) -> None:
        conn = _FakeConnection(["alarm_id"], {"alarm_id": "ALM-0001"})

        with pytest.raises(ValueError, match="SQL 식별자 형식이 잘못됐습니다"):
            vsd.canonical_table_hash(conn, 'fdc_alarm"; DROP TABLE x; --', ["alarm_id"])


class TestDescribeTargetRedaction:
    def test_no_credentials_leak_from_url_object(self) -> None:
        from sqlalchemy.engine import URL

        url = URL.create(
            drivername="postgresql+psycopg",
            username="kosa_readonly",
            password="super-secret",
            host="192.168.5.29",
            port=5432,
            database="kosa",
        )

        described = vsd.describe_target(url)

        assert described == {"host": "192.168.5.29", "port": "5432", "database": "kosa"}
        assert "super-secret" not in str(described)
        assert "kosa_readonly" not in str(described)

    def test_string_dsn_is_parsed_not_treated_as_username(self) -> None:
        dsn = "postgresql://kosa_readonly:super-secret@kosa165.iptime.org:53001/kosa"

        described = vsd.describe_target(dsn)

        assert described == {
            "host": "kosa165.iptime.org",
            "port": "53001",
            "database": "kosa",
        }
        assert "super-secret" not in str(described)
        assert "kosa_readonly" not in str(described)


class TestValidateTargetDatabase:
    def test_runtime_accepts_final_and_transitional_db_names(self) -> None:
        vsd.validate_target_database(
            "runtime", {"database": "kosa_agent", "host": "h", "port": "1"}
        )
        vsd.validate_target_database(
            "runtime", {"database": "kosa", "host": "h", "port": "1"}
        )

    def test_evaluation_rejects_runtime_db_name(self) -> None:
        target = {"database": "kosa_agent", "host": "h", "port": "1"}

        with pytest.raises(RuntimeError, match="대상 DB명이 예상과 다릅니다"):
            vsd.validate_target_database("evaluation", target)

    def test_evaluation_accepts_only_kosa_text2sql(self) -> None:
        vsd.validate_target_database(
            "evaluation", {"database": "kosa_text2sql", "host": "h", "port": "1"}
        )

    def test_runtime_rejects_evaluation_db_name(self) -> None:
        target = {"database": "kosa_text2sql", "host": "h", "port": "1"}

        with pytest.raises(RuntimeError, match="대상 DB명이 예상과 다릅니다"):
            vsd.validate_target_database("runtime", target)


class TestResolveProfileUrl:
    def test_evaluation_without_env_raises_clear_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TEXT2SQL_EVAL_DATABASE_URL", raising=False)

        with pytest.raises(RuntimeError, match="TEXT2SQL_EVAL_DATABASE_URL"):
            vsd.resolve_profile_url("evaluation")

    def test_evaluation_with_explicit_env_is_used_as_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEXT2SQL_EVAL_DATABASE_URL", "postgresql://x/y")

        resolved = vsd.resolve_profile_url("evaluation")

        assert resolved == "postgresql://x/y"


class TestDoGenerateGuard:
    """--generate 가 오염된 DB 값을 조용히 기준값으로 확정하지 못하게 막는
    가드를 검증한다. 최초 생성도 --confirm 없이는 절대 쓰지 않는다. 실제
    파일시스템에 쓰되 MANIFEST_PATH 를 tmp_path 로 바꿔 저장소의 진짜
    manifest 는 건드리지 않는다."""

    ALLOWLIST = vsd.ALLOWLIST_TABLES
    _HEX64 = "a" * 64

    def _section(self, row_count: int = 1, content_hash: str = _HEX64) -> dict:
        return {
            table: {
                "columns": ["col_a", "col_b"],
                "row_count": row_count,
                "content_hash": content_hash,
            }
            for table in self.ALLOWLIST
        }

    def test_first_time_generate_without_confirm_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)

        code = vsd._do_generate("runtime", self._section(), confirm=False)

        assert code == 3
        assert not manifest_path.exists()

    def test_first_time_generate_with_confirm_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)

        code = vsd._do_generate("runtime", self._section(), confirm=True)

        assert code == 0
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert saved["source"]["tables"]["fdc_alarm"]["row_count"] == 1
        assert saved["hash_algorithm"] == vsd.HASH_ALGORITHM
        assert saved["format_version"] == vsd.MANIFEST_FORMAT_VERSION

    def test_unchanged_rerun_is_noop_without_confirm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)
        vsd._do_generate("runtime", self._section(), confirm=True)

        code = vsd._do_generate("runtime", self._section(), confirm=False)

        assert code == 0

    def test_changed_value_without_confirm_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)
        vsd._do_generate("runtime", self._section(row_count=1), confirm=True)

        code = vsd._do_generate("runtime", self._section(row_count=999), confirm=False)

        assert code == 3
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert saved["source"]["tables"]["fdc_alarm"]["row_count"] == 1

    def test_changed_value_with_confirm_overwrites(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)
        vsd._do_generate("runtime", self._section(row_count=1), confirm=True)

        code = vsd._do_generate("runtime", self._section(row_count=999), confirm=True)

        assert code == 0
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert saved["source"]["tables"]["fdc_alarm"]["row_count"] == 999

    def test_metadata_only_staleness_is_not_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """테이블 값은 같아도 format_version/hash_algorithm 이 낡았으면
        --confirm 없이는 아무 것도 쓰지 않아야 한다."""
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)
        section = self._section()
        manifest_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "hash_algorithm": "old",
                    "source": {"tables": section},
                }
            ),
            encoding="utf-8",
        )

        code = vsd._do_generate("runtime", section, confirm=False)

        assert code == 3
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert saved["format_version"] == 1  # 아직 갱신되지 않았다

    def test_metadata_only_staleness_can_be_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        monkeypatch.setattr(vsd, "MANIFEST_PATH", manifest_path)
        section = self._section()
        manifest_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "hash_algorithm": "old",
                    "source": {"tables": section},
                }
            ),
            encoding="utf-8",
        )

        code = vsd._do_generate("runtime", section, confirm=True)

        assert code == 0
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert saved["format_version"] == vsd.MANIFEST_FORMAT_VERSION
        assert saved["hash_algorithm"] == vsd.HASH_ALGORITHM


class TestManifestMetadataOk:
    def test_matching_metadata_is_ok(self) -> None:
        manifest = {
            "format_version": vsd.MANIFEST_FORMAT_VERSION,
            "hash_algorithm": vsd.HASH_ALGORITHM,
        }

        assert vsd.manifest_metadata_ok(manifest)

    def test_stale_format_version_is_not_ok(self) -> None:
        manifest = {"format_version": 1, "hash_algorithm": vsd.HASH_ALGORITHM}

        assert not vsd.manifest_metadata_ok(manifest)

    def test_stale_hash_algorithm_is_not_ok(self) -> None:
        manifest = {
            "format_version": vsd.MANIFEST_FORMAT_VERSION,
            "hash_algorithm": "old-algorithm",
        }

        assert not vsd.manifest_metadata_ok(manifest)

    def test_missing_metadata_is_not_ok(self) -> None:
        assert not vsd.manifest_metadata_ok({})


class TestDoVerify:
    def test_matching_tables_return_zero(self) -> None:
        section = {
            t: {"row_count": 1, "content_hash": "x"} for t in vsd.ALLOWLIST_TABLES
        }

        assert vsd._do_verify("runtime", section, section) == 0

    def test_mismatch_returns_one(self) -> None:
        expected = {
            t: {"row_count": 1, "content_hash": "x"} for t in vsd.ALLOWLIST_TABLES
        }
        actual = dict(expected)
        actual["fdc_alarm"] = {"row_count": 2, "content_hash": "x"}

        assert vsd._do_verify("runtime", actual, expected) == 1


# --- 아래는 실제 PostgreSQL Connection 을 흉내내는 최소 stub이다. 진짜 SQL
# 파서가 아니라 "SELECT 절에 나열된 컬럼만 돌려준다"는 real PostgreSQL 동작만
# 재현한다. #1 회귀(migration이 컬럼을 추가해도 manifest 원본 컬럼만 조회하면
# 해시가 그대로인지)를 DB 없이 검증하기 위한 용도다.


class _FakeScalarResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> "_FakeScalarResult":
        return self

    def all(self) -> list[str]:
        return self._values


class _FakeMappingResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeMappingResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _FakeConnection:
    def __init__(self, all_columns: list[str], row: dict) -> None:
        self.all_columns = all_columns
        self.row = row

    def execute(self, query: object, params: dict | None = None):
        sql = str(query)
        if "information_schema.columns" in sql:
            return _FakeScalarResult(self.all_columns)
        import re

        requested = re.findall(r'"([^"]+)"', sql.split("FROM")[0])
        return _FakeMappingResult([{c: self.row.get(c) for c in requested}])


class TestDiscoverColumns:
    def test_returns_columns_in_order(self) -> None:
        conn = _FakeConnection(["a", "b", "c"], {})

        assert vsd.discover_columns(conn, "t") == ["a", "b", "c"]

    def test_raises_when_table_missing(self) -> None:
        conn = _FakeConnection([], {})

        with pytest.raises(RuntimeError, match="테이블을 찾을 수 없습니다"):
            vsd.discover_columns(conn, "nonexistent")


class TestEnsureColumnsExist:
    def test_passes_when_all_present(self) -> None:
        conn = _FakeConnection(["a", "b", "c"], {})

        vsd._ensure_columns_exist(conn, "t", ["a", "b"])  # 예외 없어야 정상

    def test_raises_on_missing_column(self) -> None:
        conn = _FakeConnection(["a", "b"], {})

        with pytest.raises(
            RuntimeError, match="manifest 원본 컬럼이 대상 DB에 없습니다"
        ):
            vsd._ensure_columns_exist(conn, "t", ["a", "b", "c"])


class TestMigrationColumnIsolation:
    """001_agent_runtime.sql 이 action_history 에 컬럼을 추가해도, manifest가
    기록한 원본 컬럼만 조회하면 해시가 그대로 유지되는지 확인한다 — 이번
    리뷰의 핵심 회귀(#1)에 대한 테스트."""

    def test_extra_migration_columns_are_ignored_when_verifying(self) -> None:
        original_columns = ["action_id", "lot_id", "reason"]
        migrated_columns = [*original_columns, "send_started_at", "send_attempt_count"]
        row = {
            "action_id": "ACT-0001",
            "lot_id": "LOT-0001",
            "reason": "테스트",
            "send_started_at": None,
            "send_attempt_count": 0,
        }

        pristine_conn = _FakeConnection(original_columns, row)
        _, pristine_hash = vsd.canonical_table_hash(
            pristine_conn, "action_history", original_columns
        )

        migrated_conn = _FakeConnection(migrated_columns, row)
        vsd._ensure_columns_exist(migrated_conn, "action_history", original_columns)
        _, restricted_hash = vsd.canonical_table_hash(
            migrated_conn, "action_history", original_columns
        )

        assert restricted_hash == pristine_hash

    def test_full_column_scan_would_have_differed(self) -> None:
        """대조군: v1 처럼 대상 DB의 컬럼 전체를 그대로 조회했다면 migration
        이후 해시가 달라졌을 것이다 — 그래서 원본 컬럼 고정이 필요하다."""
        original_columns = ["action_id", "reason"]
        migrated_columns = [*original_columns, "send_attempt_count"]
        row = {"action_id": "ACT-0001", "reason": "테스트", "send_attempt_count": 0}

        pristine_conn = _FakeConnection(original_columns, row)
        _, pristine_hash = vsd.canonical_table_hash(
            pristine_conn, "action_history", original_columns
        )

        migrated_conn = _FakeConnection(migrated_columns, row)
        _, full_scan_hash = vsd.canonical_table_hash(
            migrated_conn, "action_history", migrated_columns
        )

        assert full_scan_hash != pristine_hash


class TestValidateManifestSchema:
    """columns 가 없거나 손상된 v2 manifest 가 discover_columns fallback 으로
    조용히 통과하지 못하게 막는 스키마 검증. DB 조회 전에 실행돼야 한다."""

    ALLOWLIST = vsd.ALLOWLIST_TABLES
    _HEX64 = "a" * 64

    def _valid_tables(self) -> dict:
        return {
            table: {
                "columns": ["col_a", "col_b"],
                "row_count": 1,
                "content_hash": self._HEX64,
            }
            for table in self.ALLOWLIST
        }

    def test_valid_manifest_has_no_errors(self) -> None:
        assert vsd.validate_manifest_schema(self._valid_tables()) == []

    def test_missing_columns_key_is_rejected(self) -> None:
        tables = self._valid_tables()
        del tables["action_history"]["columns"]

        errors = vsd.validate_manifest_schema(tables)

        assert any("columns 가 없거나 비어 있습니다" in e for e in errors)

    def test_empty_columns_list_is_rejected(self) -> None:
        tables = self._valid_tables()
        tables["action_history"]["columns"] = []

        errors = vsd.validate_manifest_schema(tables)

        assert any("columns 가 없거나 비어 있습니다" in e for e in errors)

    def test_duplicate_columns_are_rejected(self) -> None:
        tables = self._valid_tables()
        tables["action_history"]["columns"] = ["action_id", "action_id"]

        errors = vsd.validate_manifest_schema(tables)

        assert any("columns 에 중복이 있습니다" in e for e in errors)

    @pytest.mark.parametrize("column", ["", "   ", 'bad"column', "1starts_with_digit"])
    def test_invalid_column_name_is_rejected(self, column: str) -> None:
        tables = self._valid_tables()
        tables["action_history"]["columns"] = ["action_id", column]

        errors = vsd.validate_manifest_schema(tables)

        assert any("잘못된 SQL 식별자가 있습니다" in e for e in errors)

    def test_invalid_content_hash_format_is_rejected(self) -> None:
        tables = self._valid_tables()
        tables["action_history"]["content_hash"] = "not-a-sha256"

        errors = vsd.validate_manifest_schema(tables)

        assert any("content_hash 형식이 잘못됐습니다" in e for e in errors)

    def test_negative_row_count_is_rejected(self) -> None:
        tables = self._valid_tables()
        tables["action_history"]["row_count"] = -1

        errors = vsd.validate_manifest_schema(tables)

        assert any("row_count 가 0 이상 정수가 아닙니다" in e for e in errors)

    def test_non_integer_row_count_is_rejected(self) -> None:
        tables = self._valid_tables()
        tables["action_history"]["row_count"] = "10"

        errors = vsd.validate_manifest_schema(tables)

        assert any("row_count 가 0 이상 정수가 아닙니다" in e for e in errors)

    def test_missing_table_is_rejected(self) -> None:
        tables = self._valid_tables()
        del tables["action_history"]

        errors = vsd.validate_manifest_schema(tables)

        assert any("테이블 누락" in e and "action_history" in e for e in errors)

    def test_unexpected_extra_table_is_rejected(self) -> None:
        tables = self._valid_tables()
        tables["not_an_allowlisted_table"] = {
            "columns": ["x"],
            "row_count": 0,
            "content_hash": self._HEX64,
        }

        errors = vsd.validate_manifest_schema(tables)

        assert any("허용되지 않은 테이블" in e for e in errors)
