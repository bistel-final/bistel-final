from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_schema as schema  # noqa: E402


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)


class ContractError(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _error(reason_code: str, exit_code: int) -> ContractError:
    return ContractError(reason_code, exit_code)


def _catalog_rows() -> list[dict[str, str]]:
    return [
        *(
            {"object_name": table, "relkind": "r"}
            for table in sorted(schema.EXPECTED_TABLES)
        ),
        *(
            {"object_name": index, "relkind": "i"}
            for index in sorted(schema.EXPECTED_INDEXES)
        ),
    ]


class _Connection:
    def __init__(self, relation_responses: list[list[dict[str, Any]]]) -> None:
        self.relation_responses = relation_responses
        self.sql: list[str] = []

    def exec_driver_sql(self, sql: str) -> _Result:
        self.sql.append(sql)
        if sql == schema.RELATIONS_SQL:
            return _Result(self.relation_responses.pop(0))
        if sql == schema.EXTENSIONS_SQL:
            return _Result([{"extname": "plpgsql"}])
        return _Result([])


def test_handler_executes_verified_sql_unchanged_and_returns_none() -> None:
    raw = (
        b"-- COPY is documentation only\n"
        b"CREATE TABLE example (id integer PRIMARY KEY);\n"
    )
    connection = _Connection([[]])
    handler, _ = schema.make_handlers(raw, _error)

    assert handler(connection, object()) is None
    assert connection.sql == [schema.RELATIONS_SQL, raw.decode()]


@pytest.mark.parametrize("raw", [b"/* unclosed", b"SELECT 'unclosed"])
def test_handler_rejects_unclosed_comment_or_literal(raw: bytes) -> None:
    connection = _Connection([[]])
    handler, _ = schema.make_handlers(raw, _error)
    with pytest.raises(ContractError) as raised:
        handler(connection, object())
    assert raised.value.reason_code == "SCHEMA_FORBIDDEN_STATEMENT"


def test_handler_rejects_non_fresh_target_before_ddl() -> None:
    connection = _Connection([[{"object_name": "existing", "relkind": "r"}]])
    handler, _ = schema.make_handlers(b"CREATE TABLE x(id int);", _error)

    with pytest.raises(ContractError) as raised:
        handler(connection, object())
    assert raised.value.reason_code == "TARGET_NOT_FRESH"
    assert len(connection.sql) == 1


@pytest.mark.parametrize("keyword", ["DROP", "TRUNCATE", "GRANT", "BEGIN", "COMMIT"])
def test_handler_rejects_forbidden_sql(keyword: str) -> None:
    connection = _Connection([[]])
    handler, _ = schema.make_handlers(f"{keyword};".encode(), _error)

    with pytest.raises(ContractError) as raised:
        handler(connection, object())
    assert raised.value.reason_code == "SCHEMA_FORBIDDEN_STATEMENT"


def test_postcheck_accepts_exact_nine_tables_and_thirteen_indexes() -> None:
    connection = _Connection([_catalog_rows()])
    _, postcheck = schema.make_handlers(b"SELECT 1;", _error)

    assert postcheck(connection, object()) is None
    assert len(schema.EXPECTED_TABLES) == 9
    assert len(schema.EXPECTED_INDEXES) == 13


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: rows.pop(),
        lambda rows: rows.append({"object_name": "document", "relkind": "r"}),
        lambda rows: rows.append({"object_name": "runtime_view", "relkind": "v"}),
    ],
)
def test_postcheck_rejects_wrong_object_name_sets(mutation: Any) -> None:
    rows = _catalog_rows()
    mutation(rows)
    connection = _Connection([rows])
    _, postcheck = schema.make_handlers(b"SELECT 1;", _error)

    with pytest.raises(ContractError) as raised:
        postcheck(connection, object())
    assert raised.value.reason_code == "MODE_CONTRACT_ERROR"


@pytest.mark.parametrize(
    ("relkind", "replacement"),
    [("r", "wrong_table_same_count"), ("i", "wrong_index_same_count")],
)
def test_postcheck_rejects_same_count_with_wrong_name(
    relkind: str, replacement: str
) -> None:
    rows = _catalog_rows()
    target = next(row for row in rows if row["relkind"] == relkind)
    target["object_name"] = replacement
    assert sum(row["relkind"] == "r" for row in rows) == len(schema.EXPECTED_TABLES)
    assert sum(row["relkind"] == "i" for row in rows) == len(schema.EXPECTED_INDEXES)
    connection = _Connection([rows])
    _, postcheck = schema.make_handlers(b"SELECT 1;", _error)

    with pytest.raises(ContractError) as raised:
        postcheck(connection, object())
    assert raised.value.reason_code == "MODE_CONTRACT_ERROR"


def test_postcheck_rejects_vector_extension() -> None:
    class VectorConnection(_Connection):
        def exec_driver_sql(self, sql: str) -> _Result:
            if sql == schema.EXTENSIONS_SQL:
                return _Result([{"extname": "plpgsql"}, {"extname": "vector"}])
            return super().exec_driver_sql(sql)

    _, postcheck = schema.make_handlers(b"SELECT 1;", _error)
    with pytest.raises(ContractError) as raised:
        postcheck(VectorConnection([_catalog_rows()]), object())
    assert raised.value.reason_code == "MODE_CONTRACT_ERROR"
