from __future__ import annotations

from typing import Any

import pytest

from app.common import postgres_readiness as subject


class _MappingsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingsResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


def test_table_columns_uses_catalog_so_denied_columns_remain_visible() -> None:
    class Connection:
        def exec_driver_sql(
            self,
            sql: str,
            params: dict[str, Any],
        ) -> _MappingsResult:
            assert "pg_catalog.pg_attribute" in sql
            assert "information_schema.columns" not in sql
            assert params == {"table_names": ["lot_history", "metrology"]}
            return _MappingsResult(
                [
                    {"table_name": "lot_history", "column_name": "lot_hist_id"},
                    {"table_name": "lot_history", "column_name": "fault_code"},
                    {"table_name": "metrology", "column_name": "metrology_id"},
                ]
            )

    assert subject._table_columns(
        Connection(),  # type: ignore[arg-type]
        ["lot_history", "metrology"],
    ) == {
        "lot_history": ["lot_hist_id", "fault_code"],
        "metrology": ["metrology_id"],
    }


class _PrivilegeConnection:
    def __init__(self, *, metrology_column_select: bool = False) -> None:
        self.metrology_column_select = metrology_column_select

    def exec_driver_sql(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> _MappingsResult:
        if "has_table_privilege" in sql:
            names = params["table_names"]  # type: ignore[index]
            return _MappingsResult(
                [
                    {
                        "name": name,
                        "selectable": name
                        not in {
                            "lot_history",
                            "metrology",
                            "nl_query_log",
                        },
                    }
                    for name in names
                ]
            )
        if "cls.relname = 'lot_history'" in sql:
            return _MappingsResult(
                [
                    {"column_name": "lot_hist_id", "selectable": True},
                    {"column_name": "fault_code", "selectable": False},
                ]
            )
        if "has_any_column_privilege" in sql:
            names = params["table_names"]  # type: ignore[index]
            return _MappingsResult(
                [
                    {
                        "name": name,
                        "selectable": (
                            self.metrology_column_select and name == "metrology"
                        ),
                    }
                    for name in names
                ]
            )
        raise AssertionError(sql)


def test_runtime_select_privileges_match_final_role_matrix() -> None:
    subject._verify_runtime_select_privileges(
        _PrivilegeConnection(),  # type: ignore[arg-type]
        table_names=[
            "agent_run",
            "lot_history",
            "metrology",
            "nl_query_log",
        ],
        lot_history_columns=["lot_hist_id", "fault_code"],
    )


def test_runtime_select_privileges_reject_evaluation_column_leak() -> None:
    with pytest.raises(subject.PostgresReadinessError, match="격리 privilege"):
        subject._verify_runtime_select_privileges(
            _PrivilegeConnection(metrology_column_select=True),  # type: ignore[arg-type]
            table_names=[
                "agent_run",
                "lot_history",
                "metrology",
                "nl_query_log",
            ],
            lot_history_columns=["lot_hist_id", "fault_code"],
        )
