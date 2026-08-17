"""V4-CM-2.1 적용 runner의 상태기계와 artifact 계약을 검증한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_reference_extensions as migration  # noqa: E402
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


def _constraint_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    definitions = {
        "r03_alarm_history": {
            "p": ["PRIMARY KEY (alarm_id)"],
            "u": ["UNIQUE (lot_hist_id, parameter_id, recipe_step_no, policy_version)"],
            "f": [
                "FOREIGN KEY (lot_hist_id) REFERENCES lot_history(lot_hist_id)",
                "FOREIGN KEY (parameter_id) REFERENCES dim_parameter(parameter_id)",
            ],
            "c": [
                "CHECK (alarm_id ~ '^R03-[0-9a-f]{20}$')",
                "CHECK (recipe_step_no >= 1)",
                "CHECK (trigger_wafer_no >= 1)",
            ],
        },
        "document_corpus": {
            "p": ["PRIMARY KEY (corpus_revision)"],
            "c": [
                "CHECK (status IN ('STAGING','ACTIVE','RETIRED'))",
                "CHECK (embedding_dim = 1024)",
                "CHECK (manifest_sha256 ~ 'sha')",
                "CHECK (document_count >= 0)",
                "CHECK (chunk_count >= 0)",
            ],
        },
        "document": {
            "p": ["PRIMARY KEY (corpus_revision, doc_id)"],
            "f": [
                "FOREIGN KEY (corpus_revision) "
                "REFERENCES document_corpus(corpus_revision)"
            ],
            "c": [
                "CHECK (doc_type IN ('SPEC','MANUAL','TROUBLESHOOT'))",
                "CHECK (content_sha256 ~ 'sha')",
            ],
        },
        "document_chunk": {
            "p": ["PRIMARY KEY (corpus_revision, chunk_id)"],
            "u": ["UNIQUE (corpus_revision, doc_id, chunk_seq)"],
            "f": [
                "FOREIGN KEY (corpus_revision, doc_id) "
                "REFERENCES document(corpus_revision, doc_id)"
            ],
            "c": ["CHECK (chunk_seq >= 0)"],
        },
        "nl_query_log": {
            "p": ["PRIMARY KEY (nl_query_log_id)"],
            "c": [
                "CHECK (outcome IN ('SUCCESS','POLICY_REJECTED',"
                "'VALIDATION_FAILED','DB_ERROR'))",
                "CHECK (row_cnt >= 0)",
                "CHECK (latency_ms >= 0)",
                "CHECK (outcome = 'SUCCESS' OR outcome = 'DB_ERROR')",
            ],
        },
    }
    for table, by_type in definitions.items():
        counter = 0
        for constraint_type, items in by_type.items():
            for definition in items:
                counter += 1
                rows.append(
                    {
                        "table_name": table,
                        "constraint_name": f"{table}_{counter}",
                        "constraint_type": constraint_type,
                        "definition": definition,
                    }
                )
    return rows


def _column_rows(embedding_type: str = "vector(1024)") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table, columns in migration.EXPECTED_TABLE_COLUMNS.items():
        for ordinal, (name, data_type, nullable) in enumerate(columns, start=1):
            rows.append(
                {
                    "object_name": table,
                    "ordinal_position": ordinal,
                    "column_name": name,
                    "data_type": (
                        embedding_type
                        if table == "document_chunk" and name == "embedding"
                        else data_type
                    ),
                    "nullable": nullable,
                    "column_default": None,
                }
            )
    for ordinal, (name, data_type) in enumerate(
        zip(migration.VIEW_COLUMNS, migration.VIEW_COLUMN_TYPES, strict=True),
        start=1,
    ):
        rows.append(
            {
                "object_name": migration.REFERENCE_VIEW,
                "ordinal_position": ordinal,
                "column_name": name,
                "data_type": data_type,
                "nullable": True,
                "column_default": None,
            }
        )
    return rows


def test_view_catalog_types_include_postgresql_typmods() -> None:
    """Guard the exact format_type() values observed on PostgreSQL 16."""

    assert migration.VIEW_COLUMN_TYPES == (
        "character varying(10)",
        "character varying(24)",
        "timestamp without time zone",
        "character varying(10)",
        "character varying(20)",
        "character varying(24)",
        "character varying(20)",
        "character varying(20)",
        "character varying(20)",
        "smallint",
        "smallint",
        "smallint",
        "numeric(12,4)",
        "character varying(10)",
        "character varying(20)",
    )
    assert "character varying" not in migration.VIEW_COLUMN_TYPES
    assert "numeric" not in migration.VIEW_COLUMN_TYPES


class _Transaction(AbstractContextManager[None]):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.snapshot: tuple[bool, dict[str, str] | None] | None = None

    def __enter__(self) -> None:
        self.connection.events.append("transaction:begin")
        self.snapshot = (
            self.connection.present,
            deepcopy(self.connection.extension),
        )
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type:
            if self.snapshot is None:  # pragma: no cover
                raise AssertionError
            self.connection.present, self.connection.extension = self.snapshot
            self.connection.events.append("transaction:rollback")
        else:
            self.connection.events.append("transaction:commit")
        return False


class _Connection(AbstractContextManager["_Connection"]):
    def __init__(
        self,
        target: db_target.BootstrapTarget,
        *,
        present: bool = False,
        action_rows: int = 0,
    ) -> None:
        self.target = target
        self.present = present
        self.action_rows = action_rows
        self.base_ready = True
        self.extension: dict[str, str] | None = (
            {
                "extname": "vector",
                "extension_schema": "public",
                "extversion": "0.8.6",
            }
            if present
            else None
        )
        self.available_vector = True
        self.embedding_type = "vector(1024)"
        self.object_drift = False
        self.public_grants: set[tuple[str, str]] = set()
        self.events: list[str] = []

    def __enter__(self) -> _Connection:
        self.events.append("connection:open")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.events.append("connection:close")
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self)

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
        if "reference-extensions:base-tables" in sql:
            tables = migration.BASE_TABLES if self.base_ready else set()
            return _Result([{"table_name": table} for table in sorted(tables)])
        if "reference-extensions:objects" in sql:
            if self.object_drift:
                return _Result([{"object_name": "document", "relkind": "r"}])
            if not self.present:
                return _Result([])
            return _Result(
                [
                    {"object_name": table, "relkind": "r"}
                    for table in migration.REFERENCE_TABLES
                ]
                + [{"object_name": migration.REFERENCE_VIEW, "relkind": "v"}]
            )
        if "reference-extensions:extension" in sql:
            return _Result([self.extension] if self.extension else [])
        if "reference-extensions:available-extension" in sql:
            return _Result(
                [{"default_version": "0.8.6"}] if self.available_vector else []
            )
        if "reference-extensions:columns" in sql:
            return _Result(_column_rows(self.embedding_type))
        if "reference-extensions:constraints" in sql:
            return _Result(_constraint_rows())
        if "reference-extensions:indexes" in sql:
            return _Result(
                [
                    {
                        "table_name": "document_corpus",
                        "index_name": "ux_document_corpus_active",
                        "definition": "CREATE UNIQUE INDEX ux_document_corpus_active",
                        "predicate": "status = 'ACTIVE'",
                    }
                ]
            )
        if "reference-extensions:view */" in sql:
            return _Result(
                [
                    {
                        "view_definition": "SELECT ... UNION ALL SELECT ...",
                        "is_updatable": "NO",
                    }
                ]
            )
        if "reference-extensions:action-count" in sql:
            return _Result([{"row_count": self.action_rows}])
        if "reference-extensions:view-stats" in sql:
            count = 0 if self.action_rows == 0 and not self.present else 173
            return _Result(
                [
                    {
                        "view_count": count,
                        "trace_count": 0 if count == 0 else 126,
                        "summary_count": 0 if count == 0 else 47,
                        "r03_count": 0,
                        "view_trace_count": 0 if count == 0 else 126,
                        "view_summary_count": 0 if count == 0 else 47,
                        "view_r03_count": 0,
                        "null_lot_hist_count": 0,
                        "duplicate_ref_count": 0,
                        "invalid_source_count": 0,
                        "invalid_alarm_type_count": 0,
                        "required_null_count": 0,
                        "duplicate_lot_key_count": 0,
                    }
                ]
            )
        if "reference-extensions:view-triggers" in sql:
            return _Result([{"trigger_count": 0}])
        if "reference-extensions:sequence-count" in sql:
            return _Result([{"sequence_count": 1 if self.present else 0}])
        if normalized.startswith("SELECT has_table_privilege"):
            qualified, privilege = parameters
            object_name = str(qualified).split(".", 1)[1]
            return _Result(
                [{"granted": (object_name, privilege) in self.public_grants}]
            )
        if normalized.startswith("CREATE EXTENSION"):
            self.events.append("ddl:extension")
            if self.extension is None:
                self.extension = {
                    "extname": "vector",
                    "extension_schema": "public",
                    "extversion": "0.8.6",
                }
            return _Result([])
        if normalized.startswith("CREATE VIEW"):
            self.events.append("ddl:view")
            self.present = True
            return _Result([])
        if normalized.startswith(("CREATE TABLE", "CREATE UNIQUE INDEX")):
            self.events.append("ddl")
            return _Result([])
        raise AssertionError(f"예상하지 않은 SQL: {normalized}")


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection

    def dispose(self) -> None:
        self.connection.events.append("engine:dispose")


def _engine(target: db_target.BootstrapTarget, **kwargs: Any) -> _Engine:
    return _Engine(_Connection(target, **kwargs))


class TestSqlContract:
    def test_migration_has_exact_object_counts_and_no_data_mutation(self) -> None:
        sql, statements = migration.load_and_validate_sql()

        assert len(statements) == 8
        assert sum(s.upper().startswith("CREATE TABLE") for s in statements) == 5
        assert sum(s.upper().startswith("CREATE VIEW") for s in statements) == 1
        assert "action_history" not in sql
        assert "fdc_alarm" not in sql
        assert sql.upper().count("IF NOT EXISTS") == 1

    def test_document_chunk_check_is_in_catalog_expectation(self) -> None:
        sql, _ = migration.load_and_validate_sql()
        normalized = " ".join(sql.split())

        assert "chunk_seq integer NOT NULL CHECK (chunk_seq >= 0)" in normalized
        assert migration.EXPECTED_CONSTRAINT_COUNTS["document_chunk"] == Counter(
            {"p": 1, "u": 1, "f": 1, "c": 1}
        )

    def test_all_sql_constraint_counts_match_catalog_expectations(self) -> None:
        _, statements = migration.load_and_validate_sql()
        actual: dict[str, Counter[str]] = {}
        for statement in statements:
            if not statement.upper().startswith("CREATE TABLE"):
                continue
            match = re.fullmatch(
                r"CREATE\s+TABLE\s+([a-z0-9_]+)\s*\((.*)\)",
                statement,
                flags=re.IGNORECASE | re.DOTALL,
            )
            assert match is not None
            table, body = match.groups()
            actual[table] = Counter(
                {
                    "p": len(re.findall(r"\bPRIMARY\s+KEY\b", body, re.IGNORECASE)),
                    "u": len(re.findall(r"\bUNIQUE\s*\(", body, re.IGNORECASE)),
                    "f": len(re.findall(r"\bREFERENCES\b", body, re.IGNORECASE)),
                    "c": len(re.findall(r"\bCHECK\s*\(", body, re.IGNORECASE)),
                }
            )

        assert actual == migration.EXPECTED_CONSTRAINT_COUNTS

    @pytest.mark.parametrize(
        "forbidden",
        [
            "INSERT INTO action_history VALUES ('x')",
            "UPDATE dim_parameter SET parameter_name='x'",
            "CREATE TABLE fdc_alarm (id int)",
        ],
    )
    def test_forbidden_sql_is_rejected(self, forbidden: str) -> None:
        with pytest.raises(migration.ReferenceExtensionError):
            migration.split_sql_statements(f"{forbidden};")


class TestCliContract:
    def _args(self, **overrides: Any) -> argparse.Namespace:
        values = {
            "database": "kosa_agent",
            "dry_run": False,
            "preflight": False,
            "rehearse": False,
            "recover_marker": False,
            "confirm_target": None,
            "change_ref": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    @pytest.mark.parametrize(
        ("overrides", "mode"),
        [
            ({"dry_run": True}, "dry-run"),
            ({"preflight": True}, "preflight"),
            (
                {
                    "database": "kosa_agent_e2e",
                    "confirm_target": "kosa_agent_e2e",
                    "rehearse": True,
                },
                "rehearse",
            ),
            (
                {"confirm_target": "kosa_agent", "change_ref": "GH-50"},
                "apply",
            ),
            (
                {
                    "confirm_target": "kosa_agent",
                    "change_ref": "GH-50",
                    "recover_marker": True,
                },
                "recover-marker",
            ),
        ],
    )
    def test_explicit_modes(self, overrides: dict[str, Any], mode: str) -> None:
        assert migration.resolve_mode(self._args(**overrides)) == mode

    def test_no_mode_refuses_connection(self) -> None:
        with pytest.raises(migration.ReferenceExtensionError, match="접속하지"):
            migration.resolve_mode(self._args())

    def test_modes_are_mutually_exclusive(self) -> None:
        with pytest.raises(migration.ReferenceExtensionError):
            migration.resolve_mode(self._args(dry_run=True, preflight=True))
        with pytest.raises(migration.ReferenceExtensionError):
            migration.resolve_mode(
                self._args(
                    database="kosa_agent_e2e",
                    confirm_target="kosa_agent_e2e",
                    rehearse=True,
                    recover_marker=True,
                )
            )

    @pytest.mark.parametrize("database", ["kosa_agent", "kosa_text2sql"])
    def test_rehearse_rejects_non_e2e_database(self, database: str) -> None:
        with pytest.raises(migration.ReferenceExtensionError, match="kosa_agent_e2e"):
            migration.resolve_mode(
                self._args(
                    database=database,
                    confirm_target=database,
                    rehearse=True,
                )
            )

    def test_rehearse_rejects_change_reference(self) -> None:
        with pytest.raises(migration.ReferenceExtensionError, match="change-ref"):
            migration.resolve_mode(
                self._args(
                    database="kosa_agent_e2e",
                    confirm_target="kosa_agent_e2e",
                    rehearse=True,
                    change_ref="GH-50",
                )
            )

    @pytest.mark.parametrize("change_ref", [None, "", "issue-1", "GH-abc"])
    def test_mutation_requires_valid_change_reference(
        self, change_ref: str | None
    ) -> None:
        with pytest.raises(migration.ReferenceExtensionError, match="change_reference"):
            migration.resolve_mode(
                self._args(confirm_target="kosa_agent", change_ref=change_ref)
            )


class TestLifecycle:
    def test_rehearse_runs_real_postcheck_and_leaves_no_artifacts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target("kosa_agent_e2e")
        engine = _engine(target)
        original_postcheck = migration.postcheck_database

        def record_postcheck(*args: Any, **kwargs: Any) -> migration.PostcheckResult:
            engine.connection.events.append("postcheck")
            return original_postcheck(*args, **kwargs)

        monkeypatch.setattr(migration, "postcheck_database", record_postcheck)
        result = migration.run_rehearsal(
            target,
            engine_factory=lambda _: engine,
            marker_root=tmp_path / "markers",
        )

        assert result.action_history_rows == 0
        assert result.alarm_event_rows == 173
        assert engine.connection.present is False
        assert engine.connection.extension is None
        assert "postcheck" in engine.connection.events
        assert "transaction:rollback" in engine.connection.events
        assert engine.connection.events.count("advisory_lock") == 2
        assert not migration.marker_path(
            target.database, root=tmp_path / "markers"
        ).exists()

    @pytest.mark.parametrize("database", ["kosa_agent", "kosa_text2sql"])
    def test_rehearse_function_rejects_non_e2e_database(self, database: str) -> None:
        with pytest.raises(migration.ReferenceStateError, match="kosa_agent_e2e"):
            migration.run_rehearsal(_target(database))

    def test_apply_writes_committed_receipt_then_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _engine(target, action_rows=48)
        original_start = migration.start_receipt

        def record_start(*args: Any, **kwargs: Any) -> dict[str, Any]:
            engine.connection.events.append("receipt:started")
            return original_start(*args, **kwargs)

        monkeypatch.setattr(migration, "start_receipt", record_start)
        status = migration.run_apply_or_recover(
            target,
            recover_marker=False,
            change_reference="GH-50",
            engine_factory=lambda _: engine,
            marker_root=tmp_path / "markers",
            receipt_root=tmp_path / "reports",
        )

        assert status == "APPLIED"
        assert engine.connection.events.index("receipt:started") < (
            engine.connection.events.index("ddl:extension")
        )
        marker = json.loads(
            migration.marker_path(
                target.database, root=tmp_path / "markers"
            ).read_text()
        )
        assert marker["action_history_rows_before"] == 48
        assert marker["action_history_rows_after"] == 48
        assert marker["alarm_event_rows"] == 173
        receipts = migration.load_receipts(
            target,
            migration_sha256=marker["migration_sha256"],
            root=tmp_path / "reports",
        )
        assert [receipt["status"] for receipt in receipts] == ["COMMITTED"]
        assert "private-password" not in json.dumps(marker)

    def test_same_schema_and_marker_is_noop(self, tmp_path: Path) -> None:
        target = _target()
        engine = _engine(target)
        kwargs = {
            "target": target,
            "recover_marker": False,
            "change_reference": "GH-50",
            "engine_factory": lambda _: engine,
            "marker_root": tmp_path / "markers",
            "receipt_root": tmp_path / "reports",
        }
        assert migration.run_apply_or_recover(**kwargs) == "APPLIED"
        marker_path = migration.marker_path(target.database, root=tmp_path / "markers")
        marker_mtime = marker_path.stat().st_mtime_ns

        assert migration.run_apply_or_recover(**kwargs) == "NO_OP"
        assert marker_path.stat().st_mtime_ns == marker_mtime

    def test_transaction_failure_rolls_back_and_aborts_receipt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _engine(target)

        def fail_schema(connection: _Connection, statements: list[str]) -> None:
            connection.exec_driver_sql(statements[0])
            raise migration.ReferenceStateError("injected")

        monkeypatch.setattr(migration, "execute_schema", fail_schema)
        with pytest.raises(migration.ReferenceStateError, match="injected"):
            migration.run_apply_or_recover(
                target,
                recover_marker=False,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=tmp_path / "markers",
                receipt_root=tmp_path / "reports",
            )

        assert engine.connection.present is False
        assert engine.connection.extension is None
        sql, _ = migration.load_and_validate_sql()
        receipts = migration.load_receipts(
            target,
            migration_sha256=migration._migration_sha256(sql),
            root=tmp_path / "reports",
        )
        assert [receipt["status"] for receipt in receipts] == ["ABORTED"]
        assert not migration.marker_path(
            target.database, root=tmp_path / "markers"
        ).exists()

    def test_receipt_write_failure_after_commit_leaves_started_for_recovery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _engine(target)
        marker_root = tmp_path / "markers"
        receipt_root = tmp_path / "reports"
        original_commit = migration.commit_receipt

        def fail_commit(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise migration.ReferenceArtifactError("injected receipt failure")

        monkeypatch.setattr(migration, "commit_receipt", fail_commit)
        with pytest.raises(migration.ReferenceArtifactError, match="injected"):
            migration.run_apply_or_recover(
                target,
                recover_marker=False,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=marker_root,
                receipt_root=receipt_root,
            )
        assert engine.connection.present is True
        sql, _ = migration.load_and_validate_sql()
        migration_sha = migration._migration_sha256(sql)
        receipts = migration.load_receipts(
            target, migration_sha256=migration_sha, root=receipt_root
        )
        assert [receipt["status"] for receipt in receipts] == ["STARTED"]

        monkeypatch.setattr(migration, "commit_receipt", original_commit)
        assert (
            migration.run_apply_or_recover(
                target,
                recover_marker=True,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=marker_root,
                receipt_root=receipt_root,
            )
            == "VERIFIED_EXISTING"
        )

    def test_lost_schema_with_marker_is_never_recreated(self, tmp_path: Path) -> None:
        target = _target()
        engine = _engine(target)
        kwargs = {
            "target": target,
            "recover_marker": False,
            "change_reference": "GH-50",
            "engine_factory": lambda _: engine,
            "marker_root": tmp_path / "markers",
            "receipt_root": tmp_path / "reports",
        }
        assert migration.run_apply_or_recover(**kwargs) == "APPLIED"
        engine.connection.present = False

        with pytest.raises(migration.ReferenceStateError, match="LOST_SCHEMA"):
            migration.run_apply_or_recover(**kwargs)

    @pytest.mark.parametrize("receipt_status", ["STARTED", "COMMITTED"])
    def test_recover_marker_from_crash_receipt(
        self, receipt_status: str, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _engine(target)
        marker_root = tmp_path / "markers"
        receipt_root = tmp_path / "reports"
        assert (
            migration.run_apply_or_recover(
                target,
                recover_marker=False,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=marker_root,
                receipt_root=receipt_root,
            )
            == "APPLIED"
        )
        marker = json.loads(
            migration.marker_path(target.database, root=marker_root).read_text()
        )
        migration.marker_path(target.database, root=marker_root).unlink()
        receipts = migration.load_receipts(
            target,
            migration_sha256=marker["migration_sha256"],
            root=receipt_root,
        )
        if receipt_status == "STARTED":
            committed = receipts[0]
            started_keys = {
                "artifact_type",
                "format_version",
                "operation_id",
                "attempt",
                "database",
                "profile",
                "status",
                "migration_sha256",
                "change_reference",
                "started_at",
                "action_history_rows_before",
                "object_inventory_before",
                "vector_extension_before",
            }
            started = {key: committed[key] for key in started_keys}
            started["status"] = "STARTED"
            migration.save_receipt(
                started,
                target,
                migration_sha256=marker["migration_sha256"],
                root=receipt_root,
            )

        status = migration.run_apply_or_recover(
            target,
            recover_marker=True,
            change_reference="GH-50",
            engine_factory=lambda _: engine,
            marker_root=marker_root,
            receipt_root=receipt_root,
        )

        assert status == "VERIFIED_EXISTING"
        recovered = json.loads(
            migration.marker_path(target.database, root=marker_root).read_text()
        )
        assert recovered["status"] == "VERIFIED_EXISTING"

    def test_receiptless_present_requires_recovery_evidence(
        self, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _engine(target, present=True)

        with pytest.raises(migration.ReferenceStateError, match="receipt 없는 PRESENT"):
            migration.run_apply_or_recover(
                target,
                recover_marker=False,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=tmp_path / "markers",
                receipt_root=tmp_path / "reports",
            )

    def test_partial_objects_are_drift(self, tmp_path: Path) -> None:
        target = _target()
        engine = _engine(target)
        engine.connection.object_drift = True

        with pytest.raises(migration.ReferenceStateError, match="DRIFT"):
            migration.run_apply_or_recover(
                target,
                recover_marker=False,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=tmp_path / "markers",
                receipt_root=tmp_path / "reports",
            )

    def test_missing_base_is_rejected(self, tmp_path: Path) -> None:
        target = _target()
        engine = _engine(target)
        engine.connection.base_ready = False

        with pytest.raises(migration.ReferenceStateError, match="MISSING_BASE"):
            migration.run_apply_or_recover(
                target,
                recover_marker=False,
                change_reference="GH-50",
                engine_factory=lambda _: engine,
                marker_root=tmp_path / "markers",
                receipt_root=tmp_path / "reports",
            )

    def test_signature_drift_is_rejected_after_marker(self, tmp_path: Path) -> None:
        target = _target()
        engine = _engine(target)
        kwargs = {
            "target": target,
            "recover_marker": False,
            "change_reference": "GH-50",
            "engine_factory": lambda _: engine,
            "marker_root": tmp_path / "markers",
            "receipt_root": tmp_path / "reports",
        }
        assert migration.run_apply_or_recover(**kwargs) == "APPLIED"
        engine.connection.embedding_type = "vector(512)"

        with pytest.raises(migration.ReferenceStateError, match="signature"):
            migration.run_apply_or_recover(**kwargs)

    @pytest.mark.parametrize(
        ("extension_schema", "embedding_type", "match"),
        [
            ("extensions", "vector(1024)", "name/schema/version"),
            ("public", "vector(512)", "embedding"),
        ],
    )
    def test_signature_contract_rejects_extension_or_typmod_drift(
        self, extension_schema: str, embedding_type: str, match: str
    ) -> None:
        target = _target()
        connection = _Connection(target, present=True)
        assert connection.extension is not None
        connection.extension["extension_schema"] = extension_schema
        connection.embedding_type = embedding_type
        signature = migration.build_schema_signature(connection)

        with pytest.raises(migration.ReferenceStateError, match=match):
            migration._validate_signature_contract(signature)

    def test_pg17_not_null_contype_is_ignored(self) -> None:
        target = _target()
        connection = _Connection(target, present=True)
        signature = migration.build_schema_signature(connection)
        assert isinstance(signature["constraints"], list)
        signature["constraints"].append(
            {
                "table_name": "document_chunk",
                "constraint_name": "document_chunk_embedding_not_null",
                "constraint_type": "n",
                "definition": "NOT NULL embedding",
            }
        )

        assert migration._validate_signature_contract(signature) == "0.8.6"

    def test_stale_started_is_aborted_before_new_attempt(self, tmp_path: Path) -> None:
        target = _target()
        connection = _Connection(target)
        inspection = migration.inspect_database(connection)
        sql, _ = migration.load_and_validate_sql()
        migration_sha = migration._migration_sha256(sql)
        first = migration.start_receipt(
            target,
            migration_sha256=migration_sha,
            change_reference="GH-50",
            action_rows_before=0,
            inspection=inspection,
            root=tmp_path,
        )
        second = migration.start_receipt(
            target,
            migration_sha256=migration_sha,
            change_reference="GH-50",
            action_rows_before=0,
            inspection=inspection,
            root=tmp_path,
        )
        receipts = migration.load_receipts(
            target, migration_sha256=migration_sha, root=tmp_path
        )

        assert first["operation_id"] != second["operation_id"]
        assert [(item["attempt"], item["status"]) for item in receipts] == [
            (1, "ABORTED"),
            (2, "STARTED"),
        ]

    def test_preflight_requires_available_vector_when_absent(
        self, tmp_path: Path
    ) -> None:
        target = _target()
        engine = _engine(target)
        engine.connection.available_vector = False

        with pytest.raises(migration.ReferenceStateError, match="vector"):
            migration.run_preflight(
                target,
                engine_factory=lambda _: engine,
                marker_root=tmp_path,
            )


def test_public_privilege_exact_set() -> None:
    target = _target()
    connection = _Connection(target, present=True)
    connection.public_grants.add(("document", "SELECT"))

    assert migration.public_privilege_violations(connection) == [("document", "SELECT")]
