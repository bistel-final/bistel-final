"""Runtime PostgreSQL epoch/schema/role와 reference successor live 검증."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app.common.readiness_markers import DATASET_EPOCH, RUNTIME_DATABASE

if TYPE_CHECKING:
    # 순수 수집 경계(Windows contract job)에 sqlalchemy를 끌어들이지 않는다.
    from sqlalchemy.engine import Connection

R03_COLUMNS = (
    ("alarm_id", "character varying", 24, False),
    ("occurred_at", "timestamp without time zone", None, False),
    ("lot_hist_id", "character varying", 20, False),
    ("lot_id", "character varying", 20, False),
    ("equipment_id", "character varying", 20, False),
    ("chamber_id", "character varying", 24, False),
    ("parameter_id", "character varying", 20, False),
    ("recipe_step_no", "smallint", None, False),
    ("trigger_wafer_no", "smallint", None, False),
    ("member_wafer_refs", "jsonb", None, False),
    ("member_alarm_refs", "jsonb", None, False),
    ("policy_version", "character varying", 20, False),
)
ALARM_VIEW_COLUMNS = (
    ("source", "character varying"),
    ("alarm_id", "character varying"),
    ("occurred_at", "timestamp without time zone"),
    ("area", "character varying"),
    ("equipment_id", "character varying"),
    ("chamber_id", "character varying"),
    ("parameter_id", "character varying"),
    ("recipe_id", "character varying"),
    ("lot_hist_id", "character varying"),
    ("lot_id", "character varying"),
    ("wafer_id", "character varying"),
    ("wafer_no", "smallint"),
    ("recipe_step_no", "smallint"),
    ("seq_no", "smallint"),
    ("value", "numeric"),
    ("alarm_type", "character varying"),
    ("rule_code", "character varying"),
)
R03_CONSTRAINTS = {
    "r03_alarm_history_pkey": ("p", "PRIMARY KEY (alarm_id)"),
    "r03_alarm_history_incident_key": (
        "u",
        "UNIQUE (lot_hist_id, parameter_id, recipe_step_no, policy_version)",
    ),
    "r03_alarm_history_lot_hist_id_fkey": (
        "f",
        "FOREIGN KEY (lot_hist_id) REFERENCES lot_history(lot_hist_id)",
    ),
    "r03_alarm_history_parameter_id_fkey": (
        "f",
        "FOREIGN KEY (parameter_id) REFERENCES dim_parameter(parameter_id)",
    ),
    "r03_alarm_history_alarm_id_check": (
        "c",
        "CHECK (((alarm_id)::text ~ '^R03-[0-9a-f]{20}$'::text))",
    ),
    "r03_alarm_history_recipe_step_no_check": (
        "c",
        "CHECK ((recipe_step_no >= 1))",
    ),
    "r03_alarm_history_trigger_wafer_no_check": (
        "c",
        "CHECK ((trigger_wafer_no >= 1))",
    ),
    "r03_alarm_history_member_wafer_refs_array_check": (
        "c",
        "CHECK ((jsonb_typeof(member_wafer_refs) = 'array'::text))",
    ),
    "r03_alarm_history_member_wafer_refs_len_check": (
        "c",
        "CHECK ((jsonb_array_length(member_wafer_refs) = 3))",
    ),
    "r03_alarm_history_member_alarm_refs_array_check": (
        "c",
        "CHECK ((jsonb_typeof(member_alarm_refs) = 'array'::text))",
    ),
    "r03_alarm_history_policy_version_check": (
        "c",
        "CHECK (((policy_version)::text = 'R03_CONSEC_V1'::text))",
    ),
}
CANONICAL_VIEW_SHA256 = (
    "4d25449fa98d4b503e73b361cfb95c2a8b626e0bc40438a9d6d9b3e2fb7f48c0"
)


class PostgresReadinessError(RuntimeError):
    """PostgreSQL live state가 packaged final 계약과 다르다."""


def validate_r03_columns(rows: Sequence[Mapping[str, Any]]) -> None:
    observed = tuple(
        (
            str(row["column_name"]),
            str(row["data_type"]),
            row["character_maximum_length"],
            str(row["is_nullable"]).upper() == "YES",
        )
        for row in rows
    )
    if observed != R03_COLUMNS:
        raise PostgresReadinessError("R03 column 계약이 다릅니다")


def validate_r03_constraints(rows: Sequence[Mapping[str, Any]]) -> None:
    observed = {
        str(row["conname"]): (
            str(row["contype"]),
            " ".join(str(row["definition"]).split()),
        )
        for row in rows
    }
    expected = {
        name: (kind, " ".join(definition.split()))
        for name, (kind, definition) in R03_CONSTRAINTS.items()
    }
    if observed != expected:
        raise PostgresReadinessError("R03 constraint 계약이 다릅니다")


def validate_view_columns(rows: Sequence[Mapping[str, Any]]) -> None:
    observed = tuple((str(row["column_name"]), str(row["data_type"])) for row in rows)
    if observed != ALARM_VIEW_COLUMNS:
        raise PostgresReadinessError("Alarm View column 계약이 다릅니다")


def validate_view_identity(definition: str) -> None:
    normalized = " ".join(definition.split())
    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != CANONICAL_VIEW_SHA256:
        raise PostgresReadinessError("Alarm View identity가 final 계약과 다릅니다")


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _scalar(
    connection: Connection, sql: str, params: Mapping[str, Any] | None = None
) -> Any:
    return connection.exec_driver_sql(sql, params or {}).scalar_one()


def _table_columns(
    connection: Connection, table_names: list[str]
) -> dict[str, list[str]]:
    rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT cls.relname AS table_name,
                   attr.attname AS column_name
              FROM pg_catalog.pg_class AS cls
              JOIN pg_catalog.pg_namespace AS ns
                ON ns.oid = cls.relnamespace
              JOIN pg_catalog.pg_attribute AS attr
                ON attr.attrelid = cls.oid
               AND attr.attnum > 0
               AND NOT attr.attisdropped
             WHERE ns.nspname = 'public'
               AND cls.relkind IN ('r', 'p')
               AND cls.relname = ANY (%(table_names)s)
             ORDER BY cls.relname, attr.attnum
            """,
            {"table_names": table_names},
        )
    )
    result: dict[str, list[str]] = {name: [] for name in table_names}
    for row in rows:
        result[str(row["table_name"])].append(str(row["column_name"]))
    return result


def _verify_runtime_select_privileges(
    connection: Connection,
    *,
    table_names: list[str],
    lot_history_columns: list[str],
) -> None:
    """kosa_app 최소권한을 실제 final role matrix와 exact 대조한다.

    ``information_schema.columns``는 현재 role이 읽을 수 없는 column/table을
    숨긴다. 따라서 catalog inventory와 privilege 검증을 분리해야 한다. Runtime
    app은 평가 전용 ``metrology``와 D 전용 ``nl_query_log``를 읽지 못하고,
    ``lot_history``는 합성 label ``fault_code``를 제외한 column만 읽는다.
    """

    column_scoped = "lot_history"
    denied_tables = {"metrology", "nl_query_log"}
    expected_table_select = set(table_names) - denied_tables - {column_scoped}
    rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT name,
                   has_table_privilege(
                       current_user,
                       'public.' || quote_ident(name),
                       'SELECT'
                   ) AS selectable
              FROM unnest(%(table_names)s::text[]) AS names(name)
             ORDER BY name
            """,
            {"table_names": table_names},
        )
    )
    observed_table_select = {str(row["name"]): bool(row["selectable"]) for row in rows}
    if observed_table_select != {
        name: name in expected_table_select for name in table_names
    }:
        raise PostgresReadinessError("Runtime kosa_app table privilege가 다릅니다")

    rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT attr.attname AS column_name,
                   has_column_privilege(
                       current_user,
                       cls.oid,
                       attr.attname,
                       'SELECT'
                   ) AS selectable
              FROM pg_catalog.pg_class AS cls
              JOIN pg_catalog.pg_namespace AS ns
                ON ns.oid = cls.relnamespace
              JOIN pg_catalog.pg_attribute AS attr
                ON attr.attrelid = cls.oid
               AND attr.attnum > 0
               AND NOT attr.attisdropped
             WHERE ns.nspname = 'public'
               AND cls.relname = 'lot_history'
             ORDER BY attr.attnum
            """
        )
    )
    observed_lot_select = {
        str(row["column_name"]): bool(row["selectable"]) for row in rows
    }
    expected_lot_select = {name: name != "fault_code" for name in lot_history_columns}
    if observed_lot_select != expected_lot_select:
        raise PostgresReadinessError("Runtime kosa_app label privilege가 다릅니다")

    rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT name,
                   has_any_column_privilege(
                       current_user,
                       'public.' || quote_ident(name),
                       'SELECT'
                   ) AS selectable
              FROM unnest(%(table_names)s::text[]) AS names(name)
             ORDER BY name
            """,
            {"table_names": sorted(denied_tables)},
        )
    )
    if {str(row["name"]): bool(row["selectable"]) for row in rows} != {
        name: False for name in sorted(denied_tables)
    }:
        raise PostgresReadinessError("Runtime kosa_app 격리 privilege가 다릅니다")


def verify_postgresql_runtime(
    connection: Connection,
    manifest: Mapping[str, Any],
) -> None:
    identity = (
        connection.exec_driver_sql(
            """
        SELECT current_database() AS database,
               current_user AS role,
               has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
               has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
               current_setting('transaction_read_only') AS transaction_read_only
        """
        )
        .mappings()
        .one()
    )
    if (
        identity["database"] != RUNTIME_DATABASE
        or identity["role"] != "kosa_app"
        or identity["schema_usage"] is not True
        or identity["schema_create"] is not False
        or identity["transaction_read_only"] != "off"
        or manifest.get("dataset_epoch") != DATASET_EPOCH
        or manifest.get("bootstrap_stage") != "runtime_checkpointed"
    ):
        raise PostgresReadinessError("Runtime database/role/stage가 다릅니다")

    tables = manifest.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise PostgresReadinessError("Runtime manifest table 계약이 없습니다")
    expected_columns: dict[str, list[str]] = {}
    for name, contract in tables.items():
        if not isinstance(name, str) or not isinstance(contract, Mapping):
            raise PostgresReadinessError("Runtime manifest table 계약이 잘못됐습니다")
        columns = contract.get("columns")
        if (
            not isinstance(columns, list)
            or not columns
            or any(not isinstance(column, str) or not column for column in columns)
        ):
            raise PostgresReadinessError("Runtime manifest column 계약이 잘못됐습니다")
        expected_columns[name] = columns
    actual_columns = _table_columns(connection, sorted(expected_columns))
    if actual_columns != {
        name: expected_columns[name] for name in sorted(expected_columns)
    }:
        raise PostgresReadinessError("Runtime schema inventory가 manifest와 다릅니다")

    _verify_runtime_select_privileges(
        connection,
        table_names=sorted(expected_columns),
        lot_history_columns=expected_columns["lot_history"],
    )


def verify_reference_migration(connection: Connection) -> None:
    validate_r03_columns(
        _rows(
            connection.exec_driver_sql(
                """
                SELECT column_name, data_type, character_maximum_length, is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'r03_alarm_history'
                 ORDER BY ordinal_position
                """
            )
        )
    )
    validate_view_columns(
        _rows(
            connection.exec_driver_sql(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'v_alarm_event'
                 ORDER BY ordinal_position
                """
            )
        )
    )

    validate_r03_constraints(
        _rows(
            connection.exec_driver_sql(
                """
                SELECT con.conname, con.contype::text AS contype,
                       pg_get_constraintdef(con.oid) AS definition
                  FROM pg_constraint AS con
                  JOIN pg_class AS rel ON rel.oid = con.conrelid
                  JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
                 WHERE ns.nspname = 'public'
                   AND rel.relname = 'r03_alarm_history'
                """
            )
        )
    )

    view_definition = str(
        _scalar(
            connection,
            "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true)",
        )
    )
    validate_view_identity(view_definition)
