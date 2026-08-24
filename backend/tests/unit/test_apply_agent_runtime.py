from __future__ import annotations

import json
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
    """**계약 상수에서 직접** catalog 행을 만든다.

    구 fixture는 `EXPECTED_CONSTRAINT_COUNTS`의 개수만큼 `agent_run_c_0` 같은 가짜
    이름을 찍어 냈다. 그러면 fixture가 통과한다는 사실이 "실제 schema가 계약과 같다"를
    전혀 뜻하지 않는다 — 계약이 개수와 문자열 조각뿐이었기 때문이다.

    지금은 `EXPECTED_CONSTRAINTS`·`EXPECTED_INDEXES`가 PostgreSQL 16 실측값이므로
    fixture도 그것을 그대로 되돌려 준다. 정규화는 멱등이라 이미 정규화된 정의를 넣어도
    같은 값이 나온다.
    """

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

    constraints = [
        {
            "table_name": contract.table,
            "constraint_name": name,
            "constraint_type": contract.contype,
            "definition": contract.definition,
            "local_columns": list(contract.columns),
            "referenced_table": contract.referenced_table,
            "referenced_columns": list(contract.referenced_columns),
            "on_delete": contract.on_delete,
            "on_update": contract.on_update,
        }
        for name, contract in runner.EXPECTED_CONSTRAINTS.items()
    ]

    indexes = [
        {
            "table_name": contract.table,
            "index_name": name,
            "is_unique": contract.unique,
            "method": contract.method,
            "definition": (
                f"CREATE UNIQUE INDEX {name} ON public.{contract.table} "
                f"USING {contract.method} ({', '.join(contract.columns)})"
                + (f" WHERE {contract.predicate}" if contract.predicate else "")
            ),
            "predicate": contract.predicate,
            "expressions": contract.expressions,
        }
        for name, contract in runner.EXPECTED_INDEXES.items()
    ]

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
        ["--preflight", "--recover-marker"],
        ["--rehearse", "--recover-marker"],
        ["--apply", "--verify"],
        ["--apply", "--recover-marker"],
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


def test_apply_is_no_longer_fail_closed() -> None:
    """`V5-CM-1.6`의 `FINAL_RUNTIME_MIGRATION_NOT_WIRED` 차단이 해제됐다.

    **source 문자열을 찾지 않는다.** 그 reason code는 모듈 docstring이 "무엇을 왜
    해제했는지" 설명하며 계속 인용한다. 문자열 검사는 그 설명까지 잡아 버려, 통과하려면
    이력을 지워야 하는 잘못된 압력이 된다. 대신 **engine까지 도달하는지**를 본다.
    """

    class _Boom(Exception):
        pass

    def engine_factory(_target: Any) -> Any:
        raise _Boom

    # engine까지 도달한다는 것이 차단 해제의 증거다.
    with pytest.raises(_Boom):
        runner.run_apply(
            _target(), change_reference="GH-1", engine_factory=engine_factory
        )


def test_apply_validates_the_manifest_before_opening_a_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """계약이 깨져 있으면 **접속할 이유가 없다.**

    manifest 검증을 engine 뒤에 두면 잘못된 계약으로도 공용 DB에 붙는다.
    """

    calls: list[str] = []

    def engine_factory(_target: Any) -> Any:
        calls.append("engine")
        raise AssertionError("engine factory가 호출되면 안 된다")

    def _broken(_target: Any) -> dict[str, Any]:
        raise runner.AgentRuntimeArtifactError("manifest 계약이 다릅니다")

    monkeypatch.setattr(runner, "_artifact_identity", _broken)

    with pytest.raises(runner.AgentRuntimeArtifactError):
        runner.run_apply(
            _target(),
            change_reference="GH-1",
            engine_factory=engine_factory,
            marker_root=tmp_path,
            report_root=tmp_path,
        )

    assert calls == [], "connector 호출 0회여야 한다"
    assert not list(tmp_path.iterdir()), "marker·report를 남기지 않는다"


def test_artifact_identity_does_not_read_the_v4_reference_marker() -> None:
    """`_artifact_identity()`의 V4 `001` 의존이 제거됐다(계획 §4.7 · 리뷰 필수 2).

    그 marker는 `V5-CM-1.2`가 history로 격리했고, SHA 입력이던 v4 SQL은 최종 계보가
    아니다. 남아 있으면 raise를 지워도 다음 줄에서 다시 막힌다.
    """

    # 계보에 묶인 V4 심볼이 module namespace에 없다 — import가 실제로 끊겼다.
    for name in ("load_reference_marker", "postcheck_reference_database"):
        assert not hasattr(runner, name), f"V4 계보 심볼 잔재: {name}"

    # marker 디렉터리가 **비어 있어도** identity가 성립한다.
    # 구 구현은 여기서 "001 marker가 없습니다"로 죽었다.
    identity = runner._artifact_identity(_target())
    assert set(identity) == {
        "dataset_epoch",
        "source_archive_sha256",
        "bootstrap_stage",
        "manifest_sha256",
    }
    assert identity["dataset_epoch"] == "fdc_final_20260818"
    assert identity["bootstrap_stage"] == "runtime_clean"


def test_the_corrected_producer_is_gone() -> None:
    """구 manifest producer와 CLI mode가 제거됐다(계획 §7.1)."""

    for name in ("build_runtime_manifest", "register_manifest"):
        assert not hasattr(runner, name), f"producer 잔재: {name}"

    parser = runner._parser()
    flags = {
        action.option_strings[0] for action in parser._actions if action.option_strings
    }
    assert "--register-manifests" not in flags


def test_artifact_identity_no_longer_reads_the_corrected_marker() -> None:
    """adoption identity에서 구 corrected marker 경로가 사라졌다(계획 §7.2)."""

    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "corrected_base." not in source
    assert "corrected_marker_sha256" not in source


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
                "is_unique": False,
                "method": "btree",
                "definition": "CREATE INDEX unexpected_index ON public.agent_run "
                "USING btree (status)",
                "predicate": None,
                "expressions": None,
            }
        )
    else:
        signature[part].pop()

    with pytest.raises(runner.AgentRuntimeStateError):
        runner._validate_signature_contract(signature)


@pytest.mark.parametrize(
    ("label", "predicate"),
    [
        (
            "값 추가",
            "((status)= any ((array['running', 'waiting_approval', 'completed'])))",
        ),
        # **구 검증이 놓치던 변이다.** 값 집합만 regex로 뽑으면 허용 값이 그대로라
        # 통과한다. partial index가 사실상 전체 index가 되는데도 계약은 green이었다.
        (
            "허용 값 유지 + OR 확장",
            "((status)= any ((array['running', 'waiting_approval'])) or true)",
        ),
        ("조건 삭제", None),
    ],
)
def test_signature_contract_rejects_predicate_tampering(
    label: str, predicate: str | None
) -> None:
    signature = _valid_signature()
    row = next(
        item
        for item in signature["indexes"]
        if item["index_name"] == "ux_agent_run_incident_active"
    )
    row["predicate"] = predicate

    with pytest.raises(runner.AgentRuntimeStateError, match="index 계약"):
        runner._validate_signature_contract(signature)


def test_signature_contract_rejects_index_columns_swapped() -> None:
    """컬럼 축과 predicate 축은 분리돼야 한다."""

    signature = _valid_signature()
    row = next(
        item
        for item in signature["indexes"]
        if item["index_name"] == "ux_agent_run_incident_active"
    )
    row["definition"] = row["definition"].replace(
        "(lot_id, chamber_id)", "(chamber_id, lot_id)"
    )

    with pytest.raises(runner.AgentRuntimeStateError, match="index 계약"):
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


# ---------------------------------------------------------------------------
# 상태 기계와 adopt 경로 (`V5-CM-3.2` 계획 §4.5 · §7.2)
# ---------------------------------------------------------------------------


def _inspection(state: str, signature_hash: str | None = None) -> Any:
    inventory = (
        ()
        if state == "ABSENT"
        else tuple(sorted((t, "r") for t in runner.RUNTIME_TABLES))
    )
    return runner.RuntimeInspection(state, inventory, None, signature_hash)


SIGNATURE = "a" * 64


def _marker(**overrides: Any) -> dict[str, Any]:
    payload = {
        "migration_sha256": "b" * 64,
        "schema_signature_sha256": SIGNATURE,
    }
    payload.update(overrides)
    return payload


class TestClassifyState:
    """판정을 `run_apply()` 본문에서 뗀 이유는 **판정 자체를 회귀로 잡기 위해서**다."""

    def test_absent_without_marker(self) -> None:
        assert (
            runner.classify_state(_inspection("ABSENT"), None, migration_sha="b" * 64)
            == "ABSENT"
        )

    def test_absent_with_marker_is_lost_schema(self) -> None:
        """marker는 있는데 schema가 없다 — **절대 자동 재생성하지 않는다.**"""

        state = runner.classify_state(
            _inspection("ABSENT"), _marker(), migration_sha="b" * 64
        )
        assert state == "LOST_SCHEMA"

    def test_present_without_marker_is_exact_unmarked(self) -> None:
        assert (
            runner.classify_state(
                _inspection("PRESENT", SIGNATURE), None, migration_sha="b" * 64
            )
            == "EXACT_UNMARKED"
        )

    def test_present_with_exact_marker(self) -> None:
        assert (
            runner.classify_state(
                _inspection("PRESENT", SIGNATURE), _marker(), migration_sha="b" * 64
            )
            == "EXACT_MARKED"
        )

    @pytest.mark.parametrize(
        "marker",
        [
            _marker(schema_signature_sha256="c" * 64),
            _marker(migration_sha256="d" * 64),
        ],
        ids=["schema 불일치", "migration 불일치"],
    )
    def test_marker_that_does_not_match_live_is_drift(
        self, marker: dict[str, Any]
    ) -> None:
        assert (
            runner.classify_state(
                _inspection("PRESENT", SIGNATURE), marker, migration_sha="b" * 64
            )
            == "DRIFT"
        )

    @pytest.mark.parametrize("state", ["PARTIAL", "DRIFT"])
    def test_broken_states_pass_through(self, state: str) -> None:
        assert (
            runner.classify_state(_inspection(state), None, migration_sha="b" * 64)
            == state
        )

    def test_every_state_has_a_name(self) -> None:
        """판정 결과가 전부 선언된 집합 안에 있다 — CLI reason과 1:1이다."""

        seen = {
            runner.classify_state(_inspection(s), m, migration_sha="b" * 64)
            for s in ("ABSENT", "PARTIAL", "DRIFT", "PRESENT")
            for m in (None, _marker())
        }
        assert seen <= runner.RUNTIME_STATES


class _StatementLog:
    """실제로 발행된 SQL을 **수집**한다.

    함수 이름이나 source 문자열을 찾는 정적 검사는 이 증거를 대체하지 않는다
    (계획 §7.4). adopt가 DB에 쓰지 않는다는 주장은 발행 문장으로만 증명된다.
    """

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.statements: list[str] = []
        self.rows = rows or {}

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> _Rows:
        self.statements.append(statement)
        for key, value in self.rows.items():
            if key in statement:
                return _Rows(value)
        return _Rows([{"row_count": 0}])

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))

    def begin(self) -> _Context:
        return _Context(None)

    @property
    def writes(self) -> list[str]:
        """container 회귀와 **같은 판정**을 쓴다."""

        return runner.mutating_statements(self.statements)


class _StubEngine:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> _Context:
        return _Context(self.connection)

    def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def adopt_env(monkeypatch: pytest.MonkeyPatch) -> _StatementLog:
    """`EXACT_UNMARKED` 공용 DB를 흉내 낸다. 실제 접속은 없다."""

    log = _StatementLog()
    monkeypatch.setattr(runner, "_prepare_transaction", lambda *a, **k: None)
    monkeypatch.setattr(runner, "validate_prerequisites", lambda *a, **k: (0, 189))
    monkeypatch.setattr(
        runner, "inspect_database", lambda _c: _inspection("PRESENT", SIGNATURE)
    )
    monkeypatch.setattr(
        runner,
        "postcheck_database",
        lambda _c, **_k: runner.RuntimePostcheck({}, SIGNATURE, 0, 189),
    )
    return log


def test_adopting_an_existing_schema_writes_nothing_to_the_database(
    adopt_env: _StatementLog, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**`EXACT_UNMARKED`는 DB 쓰기 0건이다.**

    공용 두 Runtime DB에는 9 table이 이미 있다(`V5-CM-1.8` 묶음 3이 22/22 실측). 없는
    것을 만드는 게 아니라 그 상태를 증명하는 것이 남은 일이므로, DDL을 다시 치면 계약이
    아니라 사고다.

    `execute_schema`가 안 불렸는지만 보지 않는다 — 그 함수를 우회해 쓰는 경로가 생기면
    놓친다. **발행된 문장 전수**를 본다.
    """

    def _fail(*_a: Any, **_k: Any) -> None:
        raise AssertionError("adopt 경로는 DDL을 실행하면 안 된다")

    monkeypatch.setattr(runner, "execute_schema", _fail)

    status, result = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=lambda _t: _StubEngine(adopt_env),
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
    )

    assert status == "VERIFIED_EXISTING"
    assert result.action_history_rows == 0
    assert adopt_env.writes == [], adopt_env.writes


def test_adopt_issues_receipt_first_then_marker(
    adopt_env: _StatementLog, tmp_path: Path
) -> None:
    """marker는 **commit된 사실의 증명서**다. committed receipt 뒤에만 나온다."""

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()

    runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=lambda _t: _StubEngine(adopt_env),
        marker_root=marker_root,
        report_root=report_root,
    )

    marker = json.loads(
        runner.marker_path("kosa_agent", root=marker_root).read_text(encoding="utf-8")
    )
    assert marker["status"] == "VERIFIED_EXISTING"
    assert marker["artifact_type"] == "agent_runtime_final"
    assert marker["task_id"] == "V5-CM-3.2"
    assert marker["migration_id"] == "002_agent_runtime_clean"
    assert marker["dataset_epoch"] == "fdc_final_20260818"
    assert marker["action_history_rows"] == 0
    assert set(marker) == set(runner.MARKER_KEYS)

    receipts = list(report_root.glob("agent_runtime_final.kosa_agent.*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "COMMITTED"
    assert marker["applied_at"] == receipt["committed_at"]


def test_second_run_is_a_no_op(adopt_env: _StatementLog, tmp_path: Path) -> None:
    """발급 뒤 재실행은 `NO_OP`이며 marker를 다시 쓰지 않는다."""

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    kwargs: dict[str, Any] = {
        "change_reference": "GH-121",
        "engine_factory": lambda _t: _StubEngine(adopt_env),
        "marker_root": marker_root,
        "report_root": report_root,
    }

    runner.run_apply(_target(), **kwargs)
    path = runner.marker_path("kosa_agent", root=marker_root)
    first = path.read_bytes()

    status, _ = runner.run_apply(_target(), **kwargs)

    assert status == "NO_OP"
    assert path.read_bytes() == first, "no-op이 tracked marker를 더럽히면 안 된다"
    assert len(list(report_root.glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("state", "reason"),
    [("PARTIAL", "PARTIAL"), ("DRIFT", "DRIFT")],
)
def test_broken_states_are_never_auto_repaired(
    adopt_env: _StatementLog,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: str,
    reason: str,
) -> None:
    monkeypatch.setattr(runner, "inspect_database", lambda _c: _inspection(state))
    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=lambda _t: _StubEngine(adopt_env),
            marker_root=marker_root,
            report_root=report_root,
        )

    assert caught.value.reason_code == reason
    assert adopt_env.writes == []
    assert not list(marker_root.iterdir())
    assert not list(report_root.iterdir()), "중단은 artifact 0건이다"


def test_action_history_rows_stop_everything(
    adopt_env: _StatementLog, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`action_history`가 0행이 아니면 **DDL·artifact 0건**으로 멈춘다."""

    def _present(*_a: Any, **_k: Any) -> None:
        raise runner.AgentRuntimeStateError(
            "runtime action_history가 비어 있지 않습니다", reason_code="ACTION_PRESENT"
        )

    monkeypatch.setattr(runner, "validate_prerequisites", _present)
    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=lambda _t: _StubEngine(adopt_env),
            marker_root=marker_root,
            report_root=report_root,
        )

    assert caught.value.reason_code == "ACTION_PRESENT"
    assert not list(marker_root.iterdir())
    assert not list(report_root.iterdir())


def test_the_action_lock_is_taken_before_state_is_read(
    adopt_env: _StatementLog, tmp_path: Path
) -> None:
    """lock → count → schema·marker 순서여야 concurrent INSERT가 배제된다."""

    seen: list[str] = []

    def _lock(_c: Any) -> None:
        seen.append("lock")

    def _prereq(*_a: Any, **_k: Any) -> tuple[int, int]:
        seen.append("count")
        return 0, 189

    def _inspect(_c: Any) -> Any:
        seen.append("inspect")
        return _inspection("PRESENT", SIGNATURE)

    import unittest.mock as mock

    with (
        mock.patch.object(runner, "lock_action_history", _lock),
        mock.patch.object(runner, "validate_prerequisites", _prereq),
        mock.patch.object(runner, "inspect_database", _inspect),
    ):
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=lambda _t: _StubEngine(adopt_env),
            marker_root=tmp_path / "m",
            report_root=tmp_path / "r",
        )

    assert seen == ["lock", "count", "inspect"]


class TestMarkerContract:
    """final marker는 **구 계보 marker를 축복하지 않는다**(계획 §4.7)."""

    def _valid_marker(self, tmp_path: Path) -> dict[str, Any]:
        identity = runner._artifact_identity(_target())
        return {
            "artifact_type": "agent_runtime_final",
            "format_version": 1,
            "task_id": "V5-CM-3.2",
            "database": "kosa_agent",
            "profile": "runtime",
            "status": "VERIFIED_EXISTING",
            "dataset_epoch": identity["dataset_epoch"],
            "source_archive_sha256": identity["source_archive_sha256"],
            "bootstrap_stage": identity["bootstrap_stage"],
            "migration_id": "002_agent_runtime_clean",
            "migration_sha256": "b" * 64,
            "manifest_sha256": identity["manifest_sha256"],
            "schema_signature_sha256": SIGNATURE,
            "action_history_rows": 0,
            "change_reference": "GH-121",
            "applied_at": "2026-08-24T00:00:00+00:00",
            "recorded_at": "2026-08-24T00:00:00+00:00",
        }

    def test_the_canonical_marker_is_accepted(self, tmp_path: Path) -> None:
        runner.validate_marker(
            self._valid_marker(tmp_path), _target(), migration_sha="b" * 64
        )

    def test_the_retired_lineage_marker_is_rejected(self, tmp_path: Path) -> None:
        """구 `runtime_clean` marker를 새 파일명에 넣어도 거부된다.

        `V5-CM-1.2`가 격리한 그 payload는 `artifact_type`이 다르고
        `task_id`·`bootstrap_stage`·`manifest_sha256`가 없다. **파일명만 바꾼 복원**이
        통하면 폐기 계보가 final 증적으로 되살아난다.
        """

        retired = {
            "artifact_type": "runtime_clean",
            "format_version": 1,
            "database": "kosa_agent",
            "profile": "runtime",
            "status": "APPLIED",
            "migration_sha256": "b" * 64,
            "schema_signature_sha256": SIGNATURE,
            "dataset_epoch": "kosa_0813",
            "correction_version": "corrected-base-v1",
            "value_normalization_version": "db-value-v1",
            "change_reference": "GH-1",
            "action_history_rows": 0,
            "applied_at": "2026-08-20T00:00:00+00:00",
            "recorded_at": "2026-08-20T00:00:00+00:00",
        }
        with pytest.raises(runner.AgentRuntimeArtifactError) as caught:
            runner.validate_marker(retired, _target(), migration_sha="b" * 64)
        assert caught.value.reason_code == "MARKER_STALE"

    @pytest.mark.parametrize(
        "field",
        [
            "dataset_epoch",
            "source_archive_sha256",
            "bootstrap_stage",
            "manifest_sha256",
        ],
    )
    def test_epoch_and_manifest_are_provenance(
        self, tmp_path: Path, field: str
    ) -> None:
        """001 marker를 뺀 자리를 이 네 값이 메운다 — 하나라도 다르면 거부다."""

        payload = self._valid_marker(tmp_path)
        payload[field] = "z" * 64 if field.endswith("sha256") else "other"
        with pytest.raises(runner.AgentRuntimeArtifactError, match="epoch/manifest"):
            runner.validate_marker(payload, _target(), migration_sha="b" * 64)

    def test_a_foreign_database_marker_is_rejected(self, tmp_path: Path) -> None:
        payload = self._valid_marker(tmp_path)
        payload["database"] = "kosa_agent_e2e"
        with pytest.raises(runner.AgentRuntimeArtifactError, match="provenance"):
            runner.validate_marker(payload, _target(), migration_sha="b" * 64)

    def test_a_non_sha256_signature_is_rejected(self, tmp_path: Path) -> None:
        payload = self._valid_marker(tmp_path)
        payload["schema_signature_sha256"] = "not-a-hash"
        with pytest.raises(runner.AgentRuntimeArtifactError, match="signature"):
            runner.validate_marker(payload, _target(), migration_sha="b" * 64)


def test_verify_requires_a_marker(adopt_env: _StatementLog, tmp_path: Path) -> None:
    """`--verify`는 `EXACT_UNMARKED`를 실패로 본다 — preflight와 다른 점이다."""

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_verify(
            _target(),
            engine_factory=lambda _t: _StubEngine(adopt_env),
            marker_root=tmp_path,
        )
    assert caught.value.reason_code == "EXACT_UNMARKED"
    assert adopt_env.writes == []


def test_cli_modes_are_explicit() -> None:
    """mutation을 암묵적 기본값으로 두지 않는다(계획 §6)."""

    parser = runner._parser()
    flags = {
        action.option_strings[0] for action in parser._actions if action.option_strings
    }
    assert {
        "--preflight",
        "--rehearse",
        "--apply",
        "--verify",
        "--recover-marker",
    } <= flags

    args = parser.parse_args(["--database", "kosa_agent"])
    with pytest.raises(runner.AgentRuntimeError, match="mode"):
        runner.resolve_mode(args)


def test_cli_rejects_evaluation_before_touching_dotenv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**parser 직후 경계.** `load_dotenv`·target loader·engine보다 앞이다."""

    calls: list[str] = []
    monkeypatch.setattr(runner, "load_dotenv", lambda *a, **k: calls.append("dotenv"))
    monkeypatch.setattr(
        runner,
        "load_bootstrap_target",
        lambda *a, **k: calls.append("loader"),
    )

    assert runner.main(["--database", "kosa_text2sql", "--apply"]) == 3

    assert calls == [], "거부되는 입력에 자격증명을 읽으면 안 된다"
    assert "reason=PROFILE_NOT_ALLOWED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 구현리뷰 보완 회귀
# ---------------------------------------------------------------------------


def _receipt_target() -> db_target.BootstrapTarget:
    return _target()


class TestReceiptContract:
    """**필수 1.** 저장한 receipt를 다시 찾을 수 있어야 계약이 성립한다."""

    def _start(self, tmp_path: Path, **overrides: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "migration_sha": "b" * 64,
            "change_reference": "GH-121",
            "adoption_identity": runner._artifact_identity(_target()),
            "status_result": "APPLIED",
            "root": tmp_path,
        }
        kwargs.update(overrides)
        return runner._start_receipt(_target(), **kwargs)

    def test_a_saved_receipt_is_found_again(self, tmp_path: Path) -> None:
        """구현은 final prefix로 저장하고 **구 prefix로 찾았다** — 항상 0건이었다."""

        saved = self._start(tmp_path)
        found = runner._load_receipts(_target(), root=tmp_path)

        assert [item["operation_id"] for item in found] == [saved["operation_id"]]

    def test_the_retired_prefix_is_not_searched(self, tmp_path: Path) -> None:
        """계획이 "읽어 증적으로 승격하지 않는다"고 못 박은 prefix다."""

        self._start(tmp_path)
        (
            tmp_path
            / "agent_runtime.kosa_agent.11111111-1111-1111-1111-111111111111.json"
        ).write_text("{}", encoding="utf-8")

        assert len(runner._load_receipts(_target(), root=tmp_path)) == 1

    def test_a_minimal_json_is_not_a_receipt(self, tmp_path: Path) -> None:
        """`{"operation_id": "<uuid>"}` 한 필드짜리 파일이 복구 근거가 되면 안 된다."""

        operation = "22222222-2222-2222-2222-222222222222"
        (tmp_path / f"agent_runtime_final.kosa_agent.{operation}.json").write_text(
            json.dumps({"operation_id": operation}), encoding="utf-8"
        )

        with pytest.raises(runner.AgentRuntimeArtifactError):
            runner._load_receipts(_target(), root=tmp_path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("database", "kosa_agent_e2e"),
            ("profile", "evaluation"),
            ("task_id", "V5-CM-1.8"),
            ("migration_id", "001_reference_extensions"),
            ("status_result", "SOMETHING"),
            ("attempt", 0),
            ("action_history_rows_before", 1),
        ],
    )
    def test_a_tampered_field_is_rejected(
        self, tmp_path: Path, field: str, value: Any
    ) -> None:
        payload = dict(self._start(tmp_path))
        payload[field] = value
        with pytest.raises(runner.AgentRuntimeArtifactError):
            runner.validate_receipt(payload, database="kosa_agent")

    def test_an_extra_field_is_rejected(self, tmp_path: Path) -> None:
        payload = dict(self._start(tmp_path))
        payload["extra"] = 1
        with pytest.raises(runner.AgentRuntimeArtifactError, match="key 계약"):
            runner.validate_receipt(payload, database="kosa_agent")

    def test_filename_and_payload_must_agree(self, tmp_path: Path) -> None:
        """어느 쪽이 참인지 모르는 상태를 남기지 않는다."""

        payload = self._start(tmp_path)
        other = "33333333-3333-3333-3333-333333333333"
        (tmp_path / f"agent_runtime_final.kosa_agent.{other}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        with pytest.raises(runner.AgentRuntimeArtifactError, match="파일명"):
            runner._load_receipts(_target(), root=tmp_path)

    def test_the_apply_method_is_carried_into_recovery(self, tmp_path: Path) -> None:
        """fresh `APPLIED`가 recovery에서 `VERIFIED_EXISTING`으로 바뀌면 안 된다."""

        applied = self._start(tmp_path, status_result="APPLIED")
        adopted = self._start(
            tmp_path, status_result="VERIFIED_EXISTING", change_reference="GH-999"
        )

        assert applied["status_result"] == "APPLIED"
        assert adopted["status_result"] == "VERIFIED_EXISTING"


def test_recovery_requires_the_same_change_reference(
    adopt_env: _StatementLog, tmp_path: Path
) -> None:
    """다른 변경 건의 committed receipt로 marker를 발급하지 않는다."""

    report_root = tmp_path / "reports"
    marker_root = tmp_path / "markers"
    report_root.mkdir()
    marker_root.mkdir()

    started = runner._start_receipt(
        _target(),
        migration_sha=runner.migration_sha256(runner.load_and_validate_sql()[0]),
        change_reference="GH-999",
        adoption_identity=runner._artifact_identity(_target()),
        status_result="APPLIED",
        root=report_root,
    )
    runner._finish_receipt(
        started,
        _target(),
        result=runner.RuntimePostcheck({}, SIGNATURE, 0, 189),
        root=report_root,
    )

    with pytest.raises(runner.AgentRuntimeArtifactError, match="정확히 1건"):
        runner.run_recover_marker(
            _target(),
            change_reference="GH-121",
            engine_factory=lambda _t: _StubEngine(adopt_env),
            marker_root=marker_root,
            report_root=report_root,
        )


class TestCatalogNormalizer:
    """**권장 1.** 입력 대소문자에 따라 의미가 달라지면 계약이 될 수 없다."""

    def test_it_is_idempotent(self) -> None:
        raw = "((status)::text = ANY ((ARRAY['RUNNING'::character varying])::text[]))"
        once = runner.normalize_catalog_text(raw)
        assert runner.normalize_catalog_text(once) == once

    def test_lowercase_keywords_are_not_eaten(self) -> None:
        """구 패턴 `::[a-z_ ]+`는 아래 둘을 같은 문자열로 접었다."""

        is_null = runner.normalize_catalog_text("check (status::text is null)")
        not_null = runner.normalize_catalog_text("check (status::text is not null)")

        assert is_null != not_null
        assert "is null" in str(is_null)
        assert "is not null" in str(not_null)

    def test_an_unknown_type_cast_is_left_alone(self) -> None:
        """모르는 type은 지우지 않는다 — 지우면 서로 다른 정의가 수렴할 수 있다."""

        assert "::mytype" in str(runner.normalize_catalog_text("check (a::mytype = b)"))

    def test_none_stays_none(self) -> None:
        assert runner.normalize_catalog_text(None) is None


def test_the_module_does_not_import_the_v4_reference_module() -> None:
    """**필수 4.** 계획 §4.7·§5·§7.1의 완료 조건이다.

    심볼 2개의 부재만 보면 module import가 그대로 남아도 통과한다. **import 자체**를
    본다. epoch 중립 배관은 `bootstrap_common`으로 옮겼다.
    """

    import ast

    source = Path(runner.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "apply_reference_extensions" not in imported
    assert "bootstrap_common" in imported
    # v5 판정기는 계속 쓴다 — 그것이 001 provenance의 절반이다.
    assert "apply_reference_extensions_v5" in imported


def test_bootstrap_common_is_shared_not_duplicated() -> None:
    """V4가 재수출하므로 두 module이 **같은 객체**를 본다.

    각자 정의하면 `except ReferenceExtensionError`가 상대편 예외를 잡지 못한다.
    """

    import apply_reference_extensions as reference_v4
    import bootstrap_common

    assert (
        reference_v4.ReferenceExtensionError is bootstrap_common.ReferenceExtensionError
    )
    assert runner.ReferenceExtensionError is bootstrap_common.ReferenceExtensionError
    assert reference_v4._result_rows is bootstrap_common._result_rows


class TestReceiptValueContract:
    """**2차 필수 2.** key가 아니라 값이 계약이다.

    1차 보완은 status별 key 집합·UUID·SHA 모양까지 봤지만, adoption identity와 행 수·
    시각은 **key 존재만** 확인했다. 아래가 전부 통과했었다.
    """

    def _committed(self, tmp_path: Path, **overrides: Any) -> dict[str, Any]:
        started = runner._start_receipt(
            _target(),
            migration_sha="b" * 64,
            change_reference="GH-121",
            adoption_identity=runner._artifact_identity(_target()),
            status_result="APPLIED",
            root=tmp_path,
        )
        payload = dict(started)
        payload.update(
            status="COMMITTED",
            committed_at="2026-08-24T00:00:00+00:00",
            action_history_rows_after=0,
            schema_signature_sha256="a" * 64,
        )
        payload.update(overrides)
        return payload

    def test_the_canonical_committed_receipt_is_accepted(self, tmp_path: Path) -> None:
        runner.validate_receipt(self._committed(tmp_path), database="kosa_agent")

    @pytest.mark.parametrize(
        ("label", "identity"),
        [
            ("epoch가 숫자", {"dataset_epoch": 123}),
            ("epoch가 구 계보", {"dataset_epoch": "kosa_0813"}),
            ("archive sha가 문자열 쓰레기", {"source_archive_sha256": "not-a-hash"}),
            ("stage가 null", {"bootstrap_stage": None}),
            ("stage가 다른 값", {"bootstrap_stage": "evaluation_reference"}),
            ("manifest sha가 리스트", {"manifest_sha256": []}),
        ],
    )
    def test_a_malformed_adoption_identity_is_rejected(
        self, tmp_path: Path, label: str, identity: dict[str, Any]
    ) -> None:
        payload = self._committed(tmp_path)
        payload["adoption_identity"] = {**payload["adoption_identity"], **identity}
        with pytest.raises(runner.AgentRuntimeArtifactError):
            runner.validate_receipt(payload, database="kosa_agent")

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("문자열", "not-zero"),
            ("0이 아닌 정수", 1),
            # `bool`은 `int`의 부분형이라 `== 0`만으로는 걸러지지 않는다.
            ("False", False),
            ("실수", 0.0),
            ("None", None),
        ],
    )
    def test_a_non_zero_row_count_is_rejected(
        self, tmp_path: Path, label: str, value: Any
    ) -> None:
        payload = self._committed(tmp_path, action_history_rows_after=value)
        with pytest.raises(runner.AgentRuntimeArtifactError, match="행 수"):
            runner.validate_receipt(payload, database="kosa_agent")

    @pytest.mark.parametrize("field", ["started_at", "committed_at"])
    def test_a_naive_timestamp_is_rejected(self, tmp_path: Path, field: str) -> None:
        """naive 시각을 받으면 두 DB의 시각을 비교할 수 없다."""

        payload = self._committed(tmp_path, **{field: "2026-08-24T00:00:00"})
        with pytest.raises(runner.AgentRuntimeArtifactError, match=field):
            runner.validate_receipt(payload, database="kosa_agent")

    def test_an_arbitrary_abort_reason_is_rejected(self, tmp_path: Path) -> None:
        started = runner._start_receipt(
            _target(),
            migration_sha="b" * 64,
            change_reference="GH-121",
            adoption_identity=runner._artifact_identity(_target()),
            status_result="APPLIED",
            root=tmp_path,
        )
        payload = dict(started)
        payload.update(
            status="ABORTED",
            aborted_at="2026-08-24T00:00:00+00:00",
            abort_reason="whatever",
        )
        with pytest.raises(runner.AgentRuntimeArtifactError, match="중단 사유"):
            runner.validate_receipt(payload, database="kosa_agent")

        payload["abort_reason"] = "APPLY_FAILED"
        runner.validate_receipt(payload, database="kosa_agent")


def test_the_r03_row_allowlist_is_fixed_outside_the_judge() -> None:
    """**2차 필수 1.** `{0, 3}`을 바깥에서 고정해야 판정기를 완화하지 않아도 된다."""

    assert runner.ALLOWED_R03_ROWS == frozenset({0, 3})


def test_the_data_gate_is_not_weakened(tmp_path: Path) -> None:
    """`assert_view_branches`를 `require_final_dataset=False`로 부르지 않는다.

    그 flag는 판정기에서 **즉시 반환**을 뜻한다 — TRACE 138·SUMMARY 51 분포와
    null owner 검사가 통째로 죽는다. source에서 그 인자를 직접 고정한다.
    """

    import ast

    source = Path(runner.__file__).read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "assert_view_branches"
    ]
    assert len(calls) == 1, "호출이 하나여야 계약을 한 곳에서 본다"
    flags = {
        keyword.arg: keyword.value
        for keyword in calls[0].keywords
        if keyword.arg == "require_final_dataset"
    }
    assert "require_final_dataset" in flags
    assert isinstance(flags["require_final_dataset"], ast.Constant)
    assert flags["require_final_dataset"].value is True


def test_the_v4_module_still_exposes_its_public_surface() -> None:
    """**2차 권장 1.** 재수출이 linter에 지워지지 않았는지 본다.

    1차 보완에서 `ruff --fix`가 "쓰지 않는 import"로 세 이름을 지웠다. 소비자가
    당장 안 쓴다는 이유로 호환 surface가 조용히 사라지면 안 된다.
    """

    import apply_reference_extensions as reference_v4
    import bootstrap_common

    for name in (
        "ReferenceExtensionError",
        "ReferenceStateError",
        "ReferenceLockError",
        "ReferenceArtifactError",
        "CHANGE_REFERENCE_PATTERN",
        "BASE_TABLES",
        "REFERENCE_TABLES",
        "REFERENCE_VIEW",
        "acquire_advisory_lock",
        "validate_change_reference",
    ):
        assert hasattr(reference_v4, name), f"재수출이 사라졌다: {name}"
        assert getattr(reference_v4, name) is getattr(bootstrap_common, name), name


# ---------------------------------------------------------------------------
# PR #123 팀 리뷰 보완
# ---------------------------------------------------------------------------


class TestMutationJudgment:
    """**필수 1.** DB 쓰기 0 주장을 세우는 장치 자체에 우회로가 있으면 안 된다."""

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT 1",
            "/* agent-runtime:tables */\nSELECT table_name FROM x",
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
            "LOCK TABLE action_history IN SHARE MODE",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
        ],
    )
    def test_non_mutating_statements(self, statement: str) -> None:
        assert runner.is_mutating_statement(statement) is False

    @pytest.mark.parametrize(
        ("label", "statement"),
        [
            ("맨 DDL", "CREATE TABLE x (a int)"),
            # **구 구현이 통과시키던 것.** 선행 블록 주석은 문장의 종류를 바꾸지 않는데
            # `/* AGENT-RUNTIME:` prefix를 무해 목록에 넣어 뒀었다.
            (
                "주석으로 위장한 DDL",
                "/* agent-runtime: adopt */ CREATE TABLE x (a int)",
            ),
            ("줄 주석으로 위장", "-- agent-runtime\nDROP TABLE x"),
            ("여러 주석", "/* a */ /* b */ ALTER TABLE x ADD COLUMN y int"),
            ("DML", "INSERT INTO audit_log (audit_id) VALUES (1)"),
            ("TRUNCATE", "TRUNCATE TABLE agent_run"),
            ("GRANT", "GRANT SELECT ON agent_run TO PUBLIC"),
        ],
    )
    def test_mutating_statements(self, label: str, statement: str) -> None:
        assert runner.is_mutating_statement(statement) is True, label

    def test_catalog_queries_are_not_mutations(self) -> None:
        """runner의 catalog SQL이 전부 무해로 판정돼야 한다.

        주석 prefix 항목만 지우면 이것들이 쓰기로 잡힌다 — 그래서 **제거가 아니라
        주석 스트립**이 옳은 수정이다.
        """

        for sql in (
            runner.TABLES_SQL,
            runner.COLUMNS_SQL,
            runner.CONSTRAINTS_SQL,
            runner.INDEXES_SQL,
            runner.SEQUENCES_SQL,
        ):
            assert runner.is_mutating_statement(sql) is False

    def test_the_002_migration_is_all_mutations(self) -> None:
        """반대 방향 — 002의 실제 DDL은 전부 쓰기로 잡혀야 한다."""

        _sql, statements = runner.load_and_validate_sql()
        assert runner.mutating_statements(statements) == statements

    def test_both_suites_share_one_judgment(self) -> None:
        """판정이 두 벌이면 갈린다 — 실제로 container와 unit이 이미 달랐다."""

        import ast

        for name in (
            "test_agent_runtime_v5_container.py",
            "test_apply_agent_runtime.py",
        ):
            source = (Path(__file__).parent / name).read_text(encoding="utf-8")
            tree = ast.parse(source)
            writes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "writes"
            ]
            assert len(writes) == 1, name
            calls = {
                node.func.attr
                for node in ast.walk(writes[0])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            assert "mutating_statements" in calls, f"{name}이 자체 판정을 갖고 있다"


class TestUnknownStateIsNotAMutation:
    """**필수 2.** 판정을 못 하는 상태에서 기본 동작이 mutation이면 안 된다."""

    def test_refuse_never_returns(self) -> None:
        import typing

        hints = typing.get_type_hints(runner._refuse)
        assert hints["return"] is typing.NoReturn

    def test_an_unknown_state_does_not_run_ddl(
        self, adopt_env: _StatementLog, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`classify_state()`에 상태가 하나 늘어도 DDL로 떨어지지 않는다."""

        monkeypatch.setattr(runner, "classify_state", lambda *a, **k: "SOMETHING_NEW")

        def _fail(*_a: Any, **_k: Any) -> None:
            raise AssertionError("미지 상태에서 DDL이 실행되면 안 된다")

        monkeypatch.setattr(runner, "execute_schema", _fail)
        marker_root = tmp_path / "m"
        report_root = tmp_path / "r"
        marker_root.mkdir()
        report_root.mkdir()

        with pytest.raises(runner.AgentRuntimeStateError, match="알 수 없는"):
            runner.run_apply(
                _target(),
                change_reference="GH-121",
                engine_factory=lambda _t: _StubEngine(adopt_env),
                marker_root=marker_root,
                report_root=report_root,
            )

        assert adopt_env.writes == []
        assert not list(marker_root.iterdir())
        assert not list(report_root.iterdir())

    def test_classify_state_only_returns_declared_states(self) -> None:
        seen = {
            runner.classify_state(_inspection(s), m, migration_sha="b" * 64)
            for s in ("ABSENT", "PARTIAL", "DRIFT", "PRESENT")
            for m in (None, _marker(), _marker(schema_signature_sha256="c" * 64))
        }
        assert seen <= runner.RUNTIME_STATES


class TestForeignKeyAllowlist:
    """**권고 1.** denylist면 새 알람 계보 table을 통과시킨다."""

    def _fk(self, referenced: str) -> list[dict[str, Any]]:
        return [
            {
                "constraint_type": "f",
                "constraint_name": "x_fkey",
                "table_name": "agent_run_alarm",
                "referenced_table": referenced,
            }
        ]

    @pytest.mark.parametrize("target", ["agent_run", "action_history"])
    def test_allowed_targets(self, target: str) -> None:
        runner._validate_legacy_alarm_fk(self._fk(target))

    @pytest.mark.parametrize(
        "target",
        [
            "trace_alarm_history",
            "summary_alarm_history",
            "r03_alarm_history",
            "fdc_alarm",
            # denylist였다면 통과했을 것 — 아직 존재하지 않는 계보다.
            "r04_alarm_history",
            "some_future_alarm_table",
        ],
    )
    def test_every_other_target_is_refused(self, target: str) -> None:
        with pytest.raises(runner.AgentRuntimeStateError) as caught:
            runner._validate_legacy_alarm_fk(self._fk(target))
        assert caught.value.reason_code == "LEGACY_ALARM_FK"

    def test_the_contract_matches_the_allowlist(self) -> None:
        referenced = {
            c.referenced_table
            for c in runner.EXPECTED_CONSTRAINTS.values()
            if c.contype == "f"
        }
        assert referenced == runner.ALLOWED_FK_TARGETS


def test_a_duplicate_index_name_is_rejected() -> None:
    """**권고 3.** constraint 쪽과 형태를 맞춘다."""

    signature = _valid_signature()
    signature["indexes"].append(dict(signature["indexes"][0]))

    with pytest.raises(runner.AgentRuntimeStateError, match="중복"):
        runner._validate_signature_contract(signature)


def test_the_adoption_identity_is_read_once(
    adopt_env: _StatementLog, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**권고 2.** 검증한 identity와 receipt에 박히는 identity는 같은 읽기여야 한다."""

    calls: list[str] = []
    real = runner._artifact_identity

    def _counted(target: Any) -> dict[str, Any]:
        calls.append("read")
        return real(target)

    monkeypatch.setattr(runner, "_artifact_identity", _counted)
    marker_root = tmp_path / "m"
    report_root = tmp_path / "r"
    marker_root.mkdir()
    report_root.mkdir()

    runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=lambda _t: _StubEngine(adopt_env),
        marker_root=marker_root,
        report_root=report_root,
    )

    # `validate_marker()`는 자기 읽기를 유지한다 — 독립 검증이라 넘겨받으면 대조가
    # 무의미해진다. `run_apply` 본문과 marker candidate는 `adoption` 하나를 공유한다.
    assert len(calls) == 2, calls

    import inspect

    body = inspect.getsource(runner.run_apply)
    assert body.count("_artifact_identity(target)") == 1, "본문에서 1회만 읽는다"


def test_the_receipt_opens_before_either_branch(
    adopt_env: _StatementLog, tmp_path: Path
) -> None:
    """**확인 2.** 완료 기준이 "receipt 먼저"다. 두 분기가 같은 순서를 탄다."""

    import inspect

    source = inspect.getsource(runner.run_apply)
    body = source[source.index("state = classify_state") :]
    start = body.index("_start_receipt(")
    for later in ("execute_schema(", "postcheck_database("):
        assert body.index(later, start) > start, later
    assert body.count("_start_receipt(") == 1, "분기마다 따로 열지 않는다"
