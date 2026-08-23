from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_rag_schema as runner  # noqa: E402


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one(self) -> dict[str, Any]:
        if len(self.rows) != 1:
            raise LookupError("expected one row")
        return self.rows[0]


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def exec_driver_sql(self, sql: str, _parameters: Any = None) -> _Rows:
        self.statements.append(" ".join(sql.split()))
        if "information_schema.tables" in sql and "IN ('document_corpus'" in sql:
            return _Rows(
                [
                    {"table_name": "document_corpus"},
                    {"table_name": "document"},
                    {"table_name": "document_chunk"},
                ]
            )
        if "count(*)" in sql:
            return _Rows([{"row_count": 7}])
        return _Rows([])


def test_schema_sql_touches_only_rag_tables() -> None:
    sql = runner.RAG_SCHEMA_SQL

    assert "DROP TABLE IF EXISTS document_chunk" in sql
    assert "DROP TABLE IF EXISTS document" in sql
    assert "DROP TABLE IF EXISTS document_corpus" in sql
    assert "CREATE TABLE document (" in sql
    assert "CREATE TABLE document_chunk (" in sql
    for forbidden in [
        "lot_history",
        "trace_alarm_history",
        "summary_alarm_history",
        "summary_data",
        "action_history",
        "agent_run",
        "r03_alarm_history",
        "nl_query_log",
        "v_alarm_event",
    ]:
        assert forbidden not in sql


def test_apply_executes_rag_schema_statements_only() -> None:
    connection = _Connection()

    runner.apply_rag_schema(connection)

    executed = "\n".join(connection.statements)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in executed
    assert "DROP TABLE IF EXISTS document_chunk" in executed
    assert "CREATE TABLE document_chunk" in executed
    assert "action_history" not in executed


def test_inspect_counts_only_rag_objects() -> None:
    counts = runner.inspect_rag_objects(_Connection())

    assert counts == {
        "document_chunk": 7,
        "document": 7,
        "document_corpus": 7,
    }


def test_cli_requires_confirm_target_to_match_database() -> None:
    args = runner.parse_args(
        ["--database", "kosa_agent", "--confirm-target", "kosa_agent"]
    )
    assert args.database == "kosa_agent"

    with pytest.raises(runner.RagSchemaError):
        runner.parse_args(
            ["--database", "kosa_agent", "--confirm-target", "kosa_agent_e2e"]
        )


def test_the_allowlist_covers_all_three_databases() -> None:
    """`V5-B-1.1`은 3개 DB 전부가 대상이다.

    처음에는 runtime 2개만 열려 있어 `kosa_text2sql`의 RAG 3 table이 구 epoch
    형상으로 남았다. 그 결과 evaluation 물리 inventory가 13이 아니라 14였다.
    """

    assert runner.ALLOWED_RAG_DATABASES == {
        "kosa_agent",
        "kosa_agent_e2e",
        "kosa_text2sql",
    }
    args = runner.parse_args(
        ["--database", "kosa_text2sql", "--confirm-target", "kosa_text2sql"]
    )
    assert args.database == "kosa_text2sql"


@pytest.mark.parametrize("flag", ["--database", "--confirm-target"])
def test_an_unknown_database_is_refused_by_each_flag(flag: str) -> None:
    """**두 flag를 따로 본다.** 한쪽만 allowlist를 잃어도 다른 쪽이 가려버린다."""

    argv = ["--database", "kosa_agent", "--confirm-target", "kosa_agent"]
    argv[argv.index(flag) + 1] = "kosa"
    with pytest.raises(SystemExit):
        runner.parse_args(argv)


def test_an_unknown_database_is_refused_at_runtime_too() -> None:
    with pytest.raises(runner.TargetValidationError):
        runner.validate_rag_target("kosa")


def test_a_populated_table_is_not_dropped_silently() -> None:
    """**적재가 끝난 DB를 실수로 겨누면 embedding이 사라진다.**

    `kosa_agent`에는 지금 `document` 3행 · `document_chunk` 25행이 있다.
    allowlist가 3개로 늘면서 오조작 여지도 함께 늘었다.
    """

    with pytest.raises(runner.RagSchemaError) as caught:
        runner.assert_replaceable(
            {"document": 3, "document_chunk": 25, "document_corpus": 0},
            allow_data_loss=False,
        )
    message = str(caught.value)
    assert "--allow-data-loss" in message
    assert "document=3" in message and "document_chunk=25" in message
    # 0행은 근거로 쓰지 않는다.
    assert "document_corpus" not in message


def test_empty_tables_replace_without_a_flag() -> None:
    """`kosa_text2sql`의 실제 상태 — 세 table 모두 0행이라 그대로 진행한다."""

    runner.assert_replaceable(
        {"document": 0, "document_chunk": 0, "document_corpus": 0},
        allow_data_loss=False,
    )
    runner.assert_replaceable({}, allow_data_loss=False)


def test_data_loss_is_possible_when_explicitly_confirmed() -> None:
    """재적재를 의도한 경우는 막지 않는다."""

    runner.assert_replaceable({"document_chunk": 25}, allow_data_loss=True)


def test_the_cli_defaults_to_refusing_data_loss() -> None:
    args = runner.parse_args(
        ["--database", "kosa_text2sql", "--confirm-target", "kosa_text2sql"]
    )
    assert args.allow_data_loss is False
    args = runner.parse_args(
        [
            "--database",
            "kosa_agent",
            "--confirm-target",
            "kosa_agent",
            "--allow-data-loss",
        ]
    )
    assert args.allow_data_loss is True


class _Engine:
    """`run_apply` 배선만 확인하기 위한 최소 fake."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.disposed = False

    def begin(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def _ctx() -> Any:
            yield self._connection

        return _ctx()

    def dispose(self) -> None:
        self.disposed = True


def _wire(monkeypatch: Any, counts: dict[str, int]) -> dict[str, Any]:
    """DB를 열지 않고 `run_apply`를 끝까지 돌린다. 호출 흔적을 돌려준다."""

    seen: dict[str, Any] = {"applied": False, "verified": False}

    class _Target:
        database = "kosa_text2sql"

        def create_url(self) -> str:
            return "postgresql+psycopg://placeholder/kosa_text2sql"

    monkeypatch.setattr(runner, "load_dotenv", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "validate_rag_target", lambda _db: _Target())
    monkeypatch.setattr(runner, "validate_url_components", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "create_engine", lambda *_a, **_k: _Engine(object()))
    monkeypatch.setattr(runner, "validate_connected_identity", lambda *_a: None)
    monkeypatch.setattr(runner, "set_and_validate_public_search_path", lambda *_a: None)
    monkeypatch.setattr(runner, "inspect_rag_objects", lambda *_a: dict(counts))

    def _apply(_connection: Any) -> None:
        seen["applied"] = True

    def _verify(_connection: Any) -> None:
        seen["verified"] = True

    monkeypatch.setattr(runner, "apply_rag_schema", _apply)
    monkeypatch.setattr(runner, "verify_rag_schema", _verify)
    return seen


def test_run_apply_refuses_before_dropping_anything(monkeypatch: Any) -> None:
    """**guard가 배선돼 있는지 본다** — 함수만 맞고 호출되지 않으면 소용없다.

    `DROP`이 실행되지 않았다는 것까지 확인한다.
    """

    seen = _wire(monkeypatch, {"document": 3, "document_chunk": 25})
    with pytest.raises(runner.RagSchemaError):
        runner.run_apply(database="kosa_agent")
    assert seen["applied"] is False, "행이 있는데 DROP이 실행됐다"
    assert seen["verified"] is False


def test_run_apply_proceeds_on_empty_tables(monkeypatch: Any) -> None:
    seen = _wire(
        monkeypatch, {"document": 0, "document_chunk": 0, "document_corpus": 0}
    )
    result = runner.run_apply(database="kosa_text2sql")
    assert seen["applied"] is True and seen["verified"] is True
    assert result["database"] == "kosa_text2sql"
    assert result["row_counts_before"] == {
        "document": 0,
        "document_chunk": 0,
        "document_corpus": 0,
    }


def test_run_apply_honours_an_explicit_data_loss_confirmation(monkeypatch: Any) -> None:
    seen = _wire(monkeypatch, {"document_chunk": 25})
    runner.run_apply(database="kosa_agent", allow_data_loss=True)
    assert seen["applied"] is True
