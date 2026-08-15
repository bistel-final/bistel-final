"""V4-CM-1.5 공용 DB bootstrap 실행기의 안전 경계를 검증한다."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import bootstrap_base_schema as bootstrap  # noqa: E402
import db_target  # noqa: E402


def _environment(**overrides: str) -> dict[str, str]:
    host = overrides.get("POSTGRES_BOOTSTRAP_HOST", "db.example.internal")
    port = int(overrides.get("POSTGRES_BOOTSTRAP_PORT", "5432"))
    values = {
        "POSTGRES_BOOTSTRAP_HOST": host,
        "POSTGRES_BOOTSTRAP_PORT": str(port),
        "POSTGRES_BOOTSTRAP_USER": "bootstrap_ddl",
        "POSTGRES_BOOTSTRAP_PASSWORD": "private-password",
        "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": db_target.host_fingerprint(
            host, port
        ),
        # 반드시 무시돼야 하는 기존 앱 설정이다.
        "POSTGRES_HOST": "wrong.example.internal",
        "APP_DATABASE_URL": "postgresql://wrong:wrong@wrong.example/kosa_agent",
    }
    values.update(overrides)
    return values


def _target(database: str = "kosa_agent") -> db_target.BootstrapTarget:
    return db_target.load_bootstrap_target(database, environ=_environment())


class _Mappings:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[Mapping[str, Any]]:
        return self.rows

    def one(self) -> Mapping[str, Any]:
        if len(self.rows) != 1:
            raise LookupError
        return self.rows[0]


class _Result:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self.rows)


class _Transaction(AbstractContextManager[None]):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> None:
        self.events.append("transaction:begin")
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.events.append("transaction:rollback" if exc_type else "transaction:commit")
        return False


class _Connection(AbstractContextManager["_Connection"]):
    def __init__(self, target: db_target.BootstrapTarget, events: list[str]) -> None:
        self.target = target
        self.events = events
        self.ddl: list[str] = []

    def __enter__(self) -> _Connection:
        self.events.append("connection:open")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.events.append("connection:close")
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self.events)

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Result:
        normalized = " ".join(sql.split())
        if "current_database() AS database_name" in normalized:
            self.events.append("identity")
            return _Result(
                [
                    {
                        "database_name": self.target.database,
                        "public_exists": True,
                        "can_use": True,
                        "can_create": True,
                    }
                ]
            )
        if normalized == "SET LOCAL search_path = public":
            self.events.append("search_path:set")
            return _Result([])
        if "current_schema() AS schema_name" in normalized:
            self.events.append("search_path:verify")
            return _Result([{"schema_name": "public"}])
        if "pg_try_advisory_xact_lock" in normalized:
            self.events.append("advisory_lock")
            return _Result([{"acquired": True}])
        if normalized == "SET TRANSACTION READ ONLY":
            self.events.append("readonly")
            return _Result([])
        self.ddl.append(sql)
        self.events.append("ddl")
        return _Result([])


class _Engine:
    def __init__(self, target: db_target.BootstrapTarget) -> None:
        self.events: list[str] = []
        self.connection = _Connection(target, self.events)

    def connect(self) -> _Connection:
        return self.connection

    def dispose(self) -> None:
        self.events.append("engine:dispose")


class _CatalogConnection:
    """PostgreSQL catalog가 반환할 행을 고정 계약에서 독립 조립한다."""

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Result:
        if "base-schema:objects" in sql:
            return _Result(
                [
                    {"object_name": table, "relkind": "r"}
                    for table in sorted(bootstrap.BASE_COLUMNS)
                ]
            )
        if "base-schema:columns" in sql:
            return _Result(
                [
                    {
                        "table_name": table,
                        "ordinal_position": position,
                        "column_name": column.name,
                        "data_type": column.data_type,
                        "nullable": column.nullable,
                        "column_default": (
                            f"'{column.default}'::character varying"
                            if column.default in {"OOC", "OOS"}
                            else column.default
                        ),
                    }
                    for table, columns in sorted(bootstrap.BASE_COLUMNS.items())
                    for position, column in enumerate(columns, start=1)
                ]
            )
        if "base-schema:constraints" in sql:
            rows: list[dict[str, Any]] = []
            for table, columns in sorted(bootstrap.PRIMARY_KEYS.items()):
                rows.append(
                    {
                        "table_name": table,
                        "contype": "p",
                        "columns": columns,
                        "reference_table": None,
                        "reference_columns": [],
                        "update_action": "a",
                        "delete_action": "a",
                        "definition": "PRIMARY KEY",
                    }
                )
            for table, foreign_keys in sorted(bootstrap.FOREIGN_KEYS.items()):
                for columns, reference_table, reference_columns in foreign_keys:
                    rows.append(
                        {
                            "table_name": table,
                            "contype": "f",
                            "columns": columns,
                            "reference_table": reference_table,
                            "reference_columns": reference_columns,
                            "update_action": "a",
                            "delete_action": "a",
                            "definition": "FOREIGN KEY",
                        }
                    )
            for table, checks in sorted(bootstrap.CHECKS.items()):
                for columns, values in checks:
                    rows.append(
                        {
                            "table_name": table,
                            "contype": "c",
                            "columns": columns,
                            "reference_table": None,
                            "reference_columns": [],
                            "update_action": "a",
                            "delete_action": "a",
                            "definition": "CHECK (value IN ("
                            + ",".join(f"'{value}'" for value in values)
                            + "))",
                        }
                    )
            return _Result(rows)
        if "base-schema:indexes" in sql:
            constraint_rows = [
                {
                    "table_name": table,
                    "index_name": f"{table}_pkey",
                    "method": "btree",
                    "is_unique": True,
                    "is_primary": True,
                    "is_constraint": True,
                    "columns": columns,
                }
                for table, columns in sorted(bootstrap.PRIMARY_KEYS.items())
            ]
            explicit_rows = [
                {
                    "table_name": table,
                    "index_name": name,
                    "method": "btree",
                    "is_unique": False,
                    "is_primary": False,
                    "is_constraint": False,
                    "columns": columns,
                }
                for name, (table, columns) in sorted(bootstrap.EXPLICIT_INDEXES.items())
            ]
            return _Result(constraint_rows + explicit_rows)
        if "SELECT count(*) AS row_count" in sql:
            return _Result([{"row_count": 0}])
        raise AssertionError(f"예상하지 않은 catalog query: {sql}")


def _inspection(state: str) -> bootstrap.Inspection:
    return bootstrap.Inspection(
        state,
        bootstrap.EXPECTED_SIGNATURE if state == "EXACT_EMPTY" else None,
        {table: 0 for table in bootstrap.BASE_COLUMNS}
        if state == "EXACT_EMPTY"
        else {},
        4 if state == "EXACT_EMPTY" else 0,
        9 if state == "EXACT_EMPTY" else 0,
        13 if state == "EXACT_EMPTY" else 0,
    )


def _inspection_sequence(
    monkeypatch: pytest.MonkeyPatch, *states: str
) -> Iterator[bootstrap.Inspection]:
    inspections = iter(_inspection(state) for state in states)
    monkeypatch.setattr(
        bootstrap, "inspect_database", lambda connection: next(inspections)
    )
    return inspections


class TestTargetGuard:
    @pytest.mark.parametrize(
        ("database", "profile"),
        [
            ("kosa_agent", "runtime"),
            ("kosa_agent_e2e", "runtime"),
            ("kosa_text2sql", "evaluation"),
        ],
    )
    def test_database_allowlist_and_profile_mapping(
        self, database: str, profile: str
    ) -> None:
        target = db_target.load_bootstrap_target(database, environ=_environment())

        assert target.database == database
        assert target.profile == profile
        assert target.host == "db.example.internal"

    def test_legacy_application_settings_are_ignored(self) -> None:
        target = db_target.load_bootstrap_target(
            "kosa_agent", environ=_environment(POSTGRES_HOST="localhost")
        )

        assert target.host == "db.example.internal"

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "0.0.0.0"])
    def test_local_hosts_are_rejected(self, host: str) -> None:
        environment = _environment(POSTGRES_BOOTSTRAP_HOST=host)
        with pytest.raises(db_target.TargetValidationError, match="로컬"):
            db_target.load_bootstrap_target("kosa_agent", environ=environment)

    def test_host_fingerprint_mismatch_is_rejected(self) -> None:
        environment = _environment(POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256="0" * 64)
        with pytest.raises(db_target.TargetValidationError, match="fingerprint"):
            db_target.load_bootstrap_target("kosa_agent", environ=environment)

    @pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
    def test_invalid_port_is_rejected(self, port: str) -> None:
        environment = _environment()
        environment["POSTGRES_BOOTSTRAP_PORT"] = port
        with pytest.raises(db_target.TargetValidationError, match="port"):
            db_target.load_bootstrap_target("kosa_agent", environ=environment)

    def test_unknown_database_is_rejected_before_connect(self) -> None:
        with pytest.raises(db_target.TargetValidationError, match="허용되지 않은"):
            db_target.load_bootstrap_target("kosa", environ=_environment())

    def test_url_does_not_render_password(self) -> None:
        target = _target()

        assert target.password not in str(target.create_url())

    def test_target_repr_hides_connection_values(self) -> None:
        target = _target()
        rendered = repr(target)

        assert target.host not in rendered
        assert str(target.port) not in rendered
        assert target.username not in rendered
        assert target.password not in rendered
        assert target.database in rendered


class TestCliMode:
    def _args(self, **overrides: Any) -> argparse.Namespace:
        values = {
            "database": "kosa_agent",
            "dry_run": False,
            "preflight": False,
            "recover_marker": False,
            "verify_rollback": False,
            "confirm_target": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_no_mode_refuses_connection(self) -> None:
        with pytest.raises(bootstrap.BootstrapError, match="접속하지 않았습니다"):
            bootstrap.resolve_mode(self._args())

    def test_main_without_mode_does_not_build_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = False

        def engine_for(target: db_target.BootstrapTarget) -> _Engine:
            nonlocal called
            called = True
            return _Engine(target)

        monkeypatch.setattr(bootstrap, "_engine_for", engine_for)

        assert bootstrap.main(["--database", "kosa_agent"]) == 2
        assert called is False

    def test_confirmation_must_match_database(self) -> None:
        with pytest.raises(bootstrap.BootstrapError, match="다릅니다"):
            bootstrap.resolve_mode(self._args(confirm_target="kosa_agent_e2e"))

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"dry_run": True}, "dry-run"),
            ({"preflight": True}, "preflight"),
            ({"confirm_target": "kosa_agent"}, "apply"),
            (
                {"confirm_target": "kosa_agent", "recover_marker": True},
                "recover-marker",
            ),
            (
                {"confirm_target": "kosa_agent", "verify_rollback": True},
                "verify-rollback",
            ),
        ],
    )
    def test_explicit_modes(self, overrides: dict[str, Any], expected: str) -> None:
        assert bootstrap.resolve_mode(self._args(**overrides)) == expected

    def test_connection_error_is_redacted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for key, value in _environment().items():
            monkeypatch.setenv(key, value)

        def fail_preflight(target: db_target.BootstrapTarget) -> bootstrap.Inspection:
            raise SQLAlchemyError("private-password db.example.internal bootstrap_ddl")

        monkeypatch.setattr(bootstrap, "run_preflight", fail_preflight)

        result = bootstrap.main(["--database", "kosa_agent", "--preflight"])
        output = capsys.readouterr()

        assert result == 2
        assert "BootstrapConnectionError" in output.err
        assert "private-password" not in output.err
        assert "db.example.internal" not in output.err
        assert "bootstrap_ddl" not in output.err
        assert "Traceback" not in output.err


class TestBootstrapLifecycle:
    def test_index_catalog_join_excludes_foreign_key_duplicates(self) -> None:
        assert "con.contype IN ('p','u','x')" in bootstrap.INDEXES_SQL

    def test_catalog_signature_and_empty_state_match_exact_contract(self) -> None:
        inspection = bootstrap.inspect_database(_CatalogConnection())

        assert inspection.state == "EXACT_EMPTY"
        assert inspection.signature == bootstrap.EXPECTED_SIGNATURE
        assert inspection.explicit_index_count == 4
        assert inspection.constraint_index_count == 9
        assert inspection.total_index_count == 13
        assert inspection.row_counts == {table: 0 for table in bootstrap.BASE_COLUMNS}

    def test_apply_absent_schema_and_write_applied_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _Engine(target)
        _inspection_sequence(monkeypatch, "ABSENT", "EXACT_EMPTY")

        status = bootstrap.run_apply_or_recover(
            target,
            recover_marker=False,
            engine_factory=lambda _: engine,
            marker_root=tmp_path,
        )

        assert status == "APPLIED"
        assert len(engine.connection.ddl) == 24
        marker = json.loads(
            bootstrap.marker_path(target.database, root=tmp_path).read_text()
        )
        assert marker["status"] == "APPLIED"
        assert marker["applied_at"] == marker["recorded_at"]
        assert "password" not in json.dumps(marker).lower()
        assert engine.events.index("advisory_lock") < engine.events.index("ddl")

    def test_exact_schema_without_marker_requires_explicit_recovery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        _inspection_sequence(monkeypatch, "EXACT_EMPTY")

        with pytest.raises(bootstrap.DatabaseStateError, match="recover-marker"):
            bootstrap.run_apply_or_recover(
                target,
                recover_marker=False,
                engine_factory=lambda _: _Engine(target),
                marker_root=tmp_path,
            )

    def test_recover_marker_does_not_execute_ddl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _Engine(target)
        _inspection_sequence(monkeypatch, "EXACT_EMPTY")

        status = bootstrap.run_apply_or_recover(
            target,
            recover_marker=True,
            engine_factory=lambda _: engine,
            marker_root=tmp_path,
        )

        assert status == "VERIFIED_EXISTING"
        assert engine.connection.ddl == []
        marker = json.loads(
            bootstrap.marker_path(target.database, root=tmp_path).read_text()
        )
        assert marker["status"] == "VERIFIED_EXISTING"
        assert "applied_at" not in marker

    def test_advisory_lock_failure_prevents_inspection_and_ddl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _Engine(target)
        inspected = False

        def reject_lock(connection: Any, database: str) -> None:
            raise bootstrap.AdvisoryLockError("busy")

        def inspect(connection: Any) -> bootstrap.Inspection:
            nonlocal inspected
            inspected = True
            return _inspection("ABSENT")

        monkeypatch.setattr(bootstrap, "acquire_advisory_lock", reject_lock)
        monkeypatch.setattr(bootstrap, "inspect_database", inspect)
        with pytest.raises(bootstrap.AdvisoryLockError, match="busy"):
            bootstrap.run_apply_or_recover(
                target,
                recover_marker=False,
                engine_factory=lambda _: engine,
                marker_root=tmp_path,
            )

        assert inspected is False
        assert engine.connection.ddl == []
        assert not bootstrap.marker_path(target.database, root=tmp_path).exists()

    @pytest.mark.parametrize("state", ["CONFLICT"])
    def test_conflict_is_never_mutated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
    ) -> None:
        target = _target()
        engine = _Engine(target)
        _inspection_sequence(monkeypatch, state)

        with pytest.raises(bootstrap.DatabaseStateError, match="partial/conflict"):
            bootstrap.run_apply_or_recover(
                target,
                recover_marker=False,
                engine_factory=lambda _: engine,
                marker_root=tmp_path,
            )
        assert engine.connection.ddl == []

    def test_preflight_is_read_only_and_locks_before_inspection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _Engine(target)

        def inspect(connection: Any) -> bootstrap.Inspection:
            engine.events.append("inspect")
            return _inspection("ABSENT")

        monkeypatch.setattr(bootstrap, "inspect_database", inspect)
        inspection = bootstrap.run_preflight(
            target,
            engine_factory=lambda _: engine,
            marker_root=tmp_path,
        )

        assert inspection.state == "ABSENT"
        assert engine.events.index("readonly") < engine.events.index("identity")
        assert engine.events.index("advisory_lock") < engine.events.index("inspect")
        assert engine.connection.ddl == []

    def test_rollback_probe_is_e2e_only_and_leaves_no_objects(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target("kosa_agent_e2e")
        engine = _Engine(target)
        _inspection_sequence(monkeypatch, "ABSENT", "ABSENT")

        bootstrap.run_rollback_verification(
            target,
            engine_factory=lambda _: engine,
            marker_root=tmp_path,
        )

        assert len(engine.connection.ddl) == 24
        assert "transaction:rollback" in engine.events
        assert engine.events.count("advisory_lock") == 2
        assert not bootstrap.marker_path(target.database, root=tmp_path).exists()

    @pytest.mark.parametrize("database", ["kosa_agent", "kosa_text2sql"])
    def test_rollback_probe_rejects_non_e2e_database(self, database: str) -> None:
        with pytest.raises(bootstrap.DatabaseStateError, match="kosa_agent_e2e"):
            bootstrap.run_rollback_verification(_target(database))


class TestMarkerContract:
    def test_marker_rejects_extra_or_secret_fields(self) -> None:
        target = _target()
        sql, _ = bootstrap.load_and_validate_sql()
        marker = bootstrap.build_marker(
            target,
            status="APPLIED",
            sql_sha256=bootstrap._sql_sha256(sql),
            recorded_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
        marker["password"] = "secret"

        with pytest.raises(bootstrap.MarkerError, match="key 집합"):
            bootstrap.validate_marker(
                marker, target, sql_sha256=bootstrap._sql_sha256(sql)
            )

    def test_marker_database_mismatch_is_rejected(self) -> None:
        target = _target()
        sql, _ = bootstrap.load_and_validate_sql()
        marker = bootstrap.build_marker(
            target, status="VERIFIED_EXISTING", sql_sha256=bootstrap._sql_sha256(sql)
        )
        marker["database"] = "kosa_text2sql"

        with pytest.raises(bootstrap.MarkerError, match="provenance"):
            bootstrap.validate_marker(
                marker, target, sql_sha256=bootstrap._sql_sha256(sql)
            )

    def test_windows_lock_adapter_uses_msvcrt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[int] = []

        class FakeMsvcrt:
            LK_LOCK = 1
            LK_UNLCK = 2

            @staticmethod
            def locking(file_descriptor: int, operation: int, length: int) -> None:
                assert file_descriptor >= 0
                assert length == 1
                calls.append(operation)

        monkeypatch.setattr(bootstrap.sys, "platform", "win32")
        monkeypatch.setattr(bootstrap, "_msvcrt", FakeMsvcrt)
        lock_path = tmp_path / "marker.lock"

        with lock_path.open("a+b") as lock_file:
            bootstrap._acquire_file_lock(lock_file)
            bootstrap._release_file_lock(lock_file)

        assert calls == [FakeMsvcrt.LK_LOCK, FakeMsvcrt.LK_UNLCK]
