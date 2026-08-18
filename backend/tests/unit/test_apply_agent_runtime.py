from __future__ import annotations

import sys
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import psycopg

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime as runner  # noqa: E402
import db_target  # noqa: E402


def _target(database: str = "kosa_agent") -> db_target.BootstrapTarget:
    profile = "evaluation" if database == "kosa_text2sql" else "runtime"
    return db_target.BootstrapTarget(
        host="db.invalid",
        port=5432,
        username="bootstrap",
        password="hidden",
        database=database,
        profile=profile,
    )


class _Context(AbstractContextManager[Any]):
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, *_: object) -> bool:
        return False


class _Connection:
    def begin(self) -> _Context:
        return _Context(None)


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()
        self.disposed = False

    def connect(self) -> _Context:
        return _Context(self.connection)

    def dispose(self) -> None:
        self.disposed = True


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


def _valid_signature() -> dict[str, list[dict[str, Any]]]:
    columns = []
    for table, contracts in runner.EXPECTED_TABLE_COLUMNS.items():
        columns.extend(
            {
                "table_name": table,
                "ordinal_position": position,
                "column_name": contract.name,
                "data_type": contract.data_type,
                "nullable": contract.nullable,
                "column_default": contract.default,
            }
            for position, contract in enumerate(contracts, start=1)
        )

    required_definition = " ".join(
        (
            "FOREIGN KEY (retry_of_run_id) REFERENCES agent_run",
            "FOREIGN KEY (action_id) REFERENCES action_history",
            "PENDING APPROVED REJECTED EXPIRED",
            "BLOCKED WAITING SENDING SENT FAILED CANCELED UNKNOWN",
            "HYPOTHESIS_GENERATED",
        )
    )
    constraints = []
    for table, counts in runner.EXPECTED_CONSTRAINT_COUNTS.items():
        for constraint_type, count in counts.items():
            constraints.extend(
                {
                    "table_name": table,
                    "constraint_name": f"{table}_{constraint_type}_{offset}",
                    "constraint_type": constraint_type,
                    "definition": required_definition
                    if not constraints
                    else f"{constraint_type}_{offset}",
                }
                for offset in range(count)
            )

    indexes = []
    for name, columns_for_index in runner.EXPECTED_INDEX_COLUMNS.items():
        predicate = None
        if name == "ux_agent_run_incident_active":
            predicate = "status IN ('RUNNING', 'WAITING_APPROVAL')"
        elif name in {
            "ux_agent_run_action_created",
            "ux_agent_run_action_incident",
        }:
            predicate = "link_role = 'CREATED'"
        elif name == "ux_agent_run_alarm_representative":
            predicate = "is_representative"
        indexes.append(
            {
                "table_name": "unused",
                "index_name": name,
                "definition": (
                    f"CREATE INDEX {name} USING btree "
                    f"({', '.join(columns_for_index)})"
                ),
                "predicate": predicate,
            }
        )
    sequences = [
        {"sequence_name": name} for name in sorted(runner.EXPECTED_SEQUENCE_NAMES)
    ]
    return {
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "sequences": sequences,
    }


def test_function_entry_rejects_evaluation_profile() -> None:
    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_preflight(
            _target("kosa_text2sql"), engine_factory=lambda _: _Engine()
        )
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"


def test_cli_rejects_evaluation_before_loading_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: None)
    assert runner.main(["--database", "kosa_text2sql", "--preflight"]) == 3
    captured = capsys.readouterr()
    assert "reason=PROFILE_NOT_ALLOWED" in captured.err
    assert "hidden" not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["--preflight", "--rehearse"],
        ["--preflight", "--recover-artifact"],
        ["--register-manifests", "--rehearse"],
    ],
)
def test_modes_are_mutually_exclusive(argv: list[str]) -> None:
    args = runner._parser().parse_args(argv)
    with pytest.raises(Exception, match="하나만"):
        runner.resolve_mode(args)


def test_cli_mode_conflict_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: None)

    assert runner.main(["--database", "kosa_agent", "--preflight", "--rehearse"]) == 2
    captured = capsys.readouterr()
    assert "RUNTIME_FAIL database=kosa_agent reason=CONTRACT_INVALID" in captured.err
    assert "Traceback" not in captured.err
    assert str(runner.REPOSITORY_ROOT) not in captured.err


def test_rehearsal_is_e2e_only() -> None:
    with pytest.raises(runner.AgentRuntimeStateError, match="e2e"):
        runner.run_rehearsal(_target("kosa_agent"), engine_factory=lambda _: _Engine())


def test_execute_schema_uses_compiled_text_for_postgres_percent_marker() -> None:
    executed: list[str] = []

    class _ExecuteConnection:
        def execute(self, statement: Any) -> None:
            executed.append(str(statement.compile(dialect=psycopg.dialect())))

        def exec_driver_sql(self, *_: Any, **__: Any) -> None:
            pytest.fail("raw driver SQL must not receive the percent marker")

    runner.execute_schema(
        _ExecuteConnection(),
        ["RAISE EXCEPTION 'wrong database: %', current_database()"],
    )

    assert executed == ["RAISE EXCEPTION 'wrong database: %%', current_database()"]


def test_apply_orders_lock_count_ddl_and_postcheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    engine = _Engine()
    target = _target()
    result = runner.RuntimePostcheck({}, "a" * 64, 0, 173)

    monkeypatch.setattr(runner, "_artifact_identity", lambda _target: {"id": "x"})
    monkeypatch.setattr(
        runner, "_prepare_transaction", lambda *args, **kwargs: calls.append("prepare")
    )
    monkeypatch.setattr(
        runner, "lock_action_history", lambda connection: calls.append("lock")
    )

    def prerequisites(connection: Any, target: Any) -> tuple[int, int]:
        calls.append("count")
        return 0, 173

    monkeypatch.setattr(runner, "validate_prerequisites", prerequisites)
    monkeypatch.setattr(
        runner,
        "inspect_database",
        lambda connection: runner.RuntimeInspection("ABSENT", (), None, None),
    )
    monkeypatch.setattr(runner, "load_marker", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner,
        "_start_receipt",
        lambda *args, **kwargs: {
            "status": "STARTED",
            "operation_id": "00000000-0000-0000-0000-000000000001",
            "change_reference": "GH-1",
        },
    )
    monkeypatch.setattr(
        runner, "execute_schema", lambda *args, **kwargs: calls.append("ddl")
    )

    def postcheck(*args: Any, **kwargs: Any) -> runner.RuntimePostcheck:
        calls.append("postcheck")
        return result

    monkeypatch.setattr(runner, "postcheck_database", postcheck)
    monkeypatch.setattr(
        runner,
        "_finish_receipt",
        lambda receipt, *args, **kwargs: {
            **receipt,
            "status": "COMMITTED",
            "committed_at": "2026-08-18T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        runner,
        "_marker_candidate",
        lambda *args, **kwargs: {
            "schema_signature_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(runner, "save_marker", lambda *args, **kwargs: None)

    status, actual = runner.run_apply(
        target,
        change_reference="GH-1",
        engine_factory=lambda _: engine,
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
    )

    assert status == "APPLIED"
    assert actual == result
    assert calls == ["prepare", "lock", "count", "ddl", "postcheck"]
    assert engine.disposed is True


def test_present_schema_requires_exact_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _Engine()
    monkeypatch.setattr(runner, "_artifact_identity", lambda _target: {})
    monkeypatch.setattr(runner, "_prepare_transaction", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "lock_action_history", lambda *args: None)
    monkeypatch.setattr(runner, "validate_prerequisites", lambda *args: (0, 173))
    monkeypatch.setattr(
        runner,
        "inspect_database",
        lambda connection: runner.RuntimeInspection("PRESENT", (), {}, "a" * 64),
    )
    monkeypatch.setattr(runner, "load_marker", lambda *args, **kwargs: None)
    with pytest.raises(runner.AgentRuntimeArtifactError, match="자동 채택"):
        runner.run_apply(
            _target(),
            change_reference="GH-1",
            engine_factory=lambda _: engine,
            marker_root=tmp_path,
            report_root=tmp_path,
        )


def test_register_manifest_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "runtime.runtime_clean.json"
    original = runner.resolve_bootstrap_manifest_path
    monkeypatch.setattr(
        runner,
        "resolve_bootstrap_manifest_path",
        lambda profile, stage: target
        if (profile, stage) == ("runtime", "runtime_clean")
        else original(profile, stage),
    )
    assert runner.register_manifest() == "REGISTERED"
    before = target.read_bytes()
    assert runner.register_manifest() == "NO_OP"
    assert target.read_bytes() == before


def test_signature_contract_accepts_canonical_catalog() -> None:
    assert len(runner._validate_signature_contract(_valid_signature())) == 64


@pytest.mark.parametrize("part", ["columns", "constraints", "indexes", "sequences"])
def test_signature_contract_rejects_catalog_drift(part: str) -> None:
    signature = _valid_signature()
    if part == "columns":
        signature[part].pop()
    elif part == "constraints":
        signature[part].pop()
    elif part == "indexes":
        signature[part].append(
            {
                "table_name": "agent_run",
                "index_name": "unexpected_index",
                "definition": "CREATE INDEX unexpected_index (status)",
                "predicate": None,
            }
        )
    else:
        signature[part].pop()

    with pytest.raises(runner.AgentRuntimeStateError):
        runner._validate_signature_contract(signature)


def test_signature_contract_rejects_broadened_partial_predicate() -> None:
    signature = _valid_signature()
    row = next(
        item
        for item in signature["indexes"]
        if item["index_name"] == "ux_agent_run_incident_active"
    )
    row["predicate"] = "status IN ('RUNNING', 'WAITING_APPROVAL', 'COMPLETED')"

    with pytest.raises(runner.AgentRuntimeStateError, match="predicate"):
        runner._validate_signature_contract(signature)


def test_build_schema_signature_queries_all_catalog_parts() -> None:
    expected = _valid_signature()

    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        def exec_driver_sql(self, statement: str, parameters: Any = None) -> _Rows:
            self.calls.append((statement, parameters))
            if statement == runner.COLUMNS_SQL:
                return _Rows(expected["columns"])
            if statement == runner.CONSTRAINTS_SQL:
                return _Rows(expected["constraints"])
            if statement == runner.INDEXES_SQL:
                return _Rows(expected["indexes"])
            if statement == runner.SEQUENCES_SQL:
                return _Rows(expected["sequences"])
            raise AssertionError("unexpected catalog query")

    connection = Connection()
    assert runner.build_schema_signature(connection) == expected
    assert connection.calls[-1][1] == (list(runner.RUNTIME_TABLES),)


def test_public_privilege_check_uses_lowercase_pseudo_role() -> None:
    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        def exec_driver_sql(self, statement: str, parameters: Any = None) -> _Rows:
            self.calls.append((statement, parameters))
            granted = parameters == ("public.agent_run", "SELECT")
            return _Rows([{"allowed": granted}])

    connection = Connection()
    violations = runner._privilege_violations(connection)

    assert violations == [("table", "agent_run", "SELECT")]
    assert all("('public', %s, %s)" in statement for statement, _ in connection.calls)
    assert all("'PUBLIC'" not in statement for statement, _ in connection.calls)
