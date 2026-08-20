"""TEXT2SQL_SCHEMA_MANIFEST_PATH override 검증.

공용 fresh bootstrap(V5-CM-2.x) 전까지 개발 기준 DB(fdc_final) 스키마로
컬럼 검증을 수행하기 위한 경로 override 가 동작하는지 고정한다.
"""

from __future__ import annotations

import json

from app.analytics import sql_validator


def _clear_cache() -> None:
    sql_validator._load_manifest_columns.cache_clear()


class TestManifestPathOverride:
    def test_override_path_columns_are_used(self, tmp_path, monkeypatch) -> None:
        manifest = tmp_path / "dev_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "tables": {
                        "trace_alarm_history": {
                            "columns": ["alarm_id", "equipment", "area"]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("TEXT2SQL_SCHEMA_MANIFEST_PATH", str(manifest))
        _clear_cache()

        columns = sql_validator._manifest_columns()
        assert columns is not None
        assert columns["trace_alarm_history"] == {"alarm_id", "equipment", "area"}

        # override 스키마 기준으로 실제 검증까지 통과해야 한다.
        result = sql_validator.validate_sql(
            "SELECT equipment, COUNT(*) AS cnt"
            " FROM trace_alarm_history GROUP BY equipment"
        )
        assert result.valid is True

        _clear_cache()

    def test_override_missing_file_stays_fail_closed(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "TEXT2SQL_SCHEMA_MANIFEST_PATH", str(tmp_path / "no_such.json")
        )
        _clear_cache()

        assert sql_validator._manifest_columns() is None

        # manifest 부재 시 컬럼 검증을 건너뛰지 않고 전체 거부한다.
        result = sql_validator.validate_sql("SELECT area FROM trace_alarm_history")
        assert result.valid is False

        _clear_cache()

    def test_default_path_without_override(self, monkeypatch) -> None:
        monkeypatch.delenv("TEXT2SQL_SCHEMA_MANIFEST_PATH", raising=False)
        _clear_cache()

        columns = sql_validator._manifest_columns()
        assert columns is not None
        assert "lot_history" in columns
