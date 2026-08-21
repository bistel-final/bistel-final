"""격리 rehearsal의 최종 적재 검증 (`V5-CM-2.4`).

`V5-CM-2.3`이 같은 transaction에 적재한 데이터를 최종 manifest v4와 대조한다.
schema 생성·COPY·lifecycle·commit/rollback은 이 모듈의 책임이 아니다. postcheck는
부수효과 없이 read-only SELECT만 하고 성공 시 `None`을 반환한다(`V5-CM-2.1` 결정 24).

typed hash 규약을 재구현하지 않는다. producer(`build_source_manifest_v4.py`)와 같은
`manifest_v3.hash_canonical_rows`·`value_normalization.normalize_db_row`를 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

import manifest_v3
import value_normalization

EXIT_MISMATCH = 1
EXIT_USAGE = 2

FORMAT_VERSION = 4
DATASET_EPOCH = "fdc_final_20260818"
TABLE_ENTRY_KEYS = frozenset(
    {
        "file_id",
        "columns",
        "column_types",
        "primary_key",
        "row_count",
        "content_hash",
        "included_by_profile",
    }
)
LOGICAL_TYPES = frozenset({"text", "numeric", "boolean", "timestamp"})
VALID_PROFILE_KEYS = frozenset({"runtime", "runtime-e2e", "evaluation"})
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# PostgreSQL `information_schema.columns.data_type` → manifest logical type.
# 최종 DDL이 실제로 쓰는 8종을 전부 덮는다(계획 §3.3 · 계획리뷰 2차 §2.1 실측).
DB_TYPE_MAP: Mapping[str, str] = MappingProxyType(
    {
        "character varying": "text",
        "character": "text",
        "text": "text",
        "smallint": "numeric",
        "integer": "numeric",
        "bigint": "numeric",
        "numeric": "numeric",
        "boolean": "boolean",
        "timestamp without time zone": "timestamp",
    }
)

EVALUATION_ONLY_TABLE = "action_history"
EMPTY_ROWS_HASH = manifest_v3.hash_canonical_rows([])

# 최종 DDL의 선언 FK 4개. `metrology.lot_hist_id`는 의미상 연결되지만 선언 FK가 아니므로
# "선언 FK 누락 0" 집계에 넣지 않는다(계획 §2.5).
DECLARED_FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("fdc_trace", "lot_hist_id", "lot_history", "lot_hist_id"),
    ("fdc_trace", "parameter_id", "dim_parameter", "parameter_id"),
    ("summary_data", "lot_hist_id", "lot_history", "lot_hist_id"),
    ("evaluation", "lot_hist_id", "lot_history", "lot_hist_id"),
)


class AcceptanceCheck(StrEnum):
    """어느 검사가 실패했는지 내부에서만 구분한다.

    production 경계에서는 전부 `MODE_CONTRACT_ERROR`(exit 1)로 매핑하고, 이 범주와 row
    값을 stderr에 직렬화하지 않는다(계획 §3.8).
    """

    DB_TYPE = "DB_TYPE"
    ROW_COUNT = "ROW_COUNT"
    CONTENT_HASH = "CONTENT_HASH"
    PK = "PK"
    FK = "FK"
    REFERENCE = "REFERENCE"
    TIMESTAMP = "TIMESTAMP"


class AcceptanceError(RuntimeError):
    def __init__(self, check: AcceptanceCheck) -> None:
        super().__init__(check.value)
        self.check = check


class ErrorFactory(Protocol):
    def __call__(self, reason_code: str, exit_code: int) -> BaseException: ...


@dataclass(frozen=True)
class TableAcceptance:
    """검증이 끝난 table 하나의 수용 기준. 값이 전부 immutable이다."""

    name: str
    columns: tuple[str, ...]
    column_types: Mapping[str, str] = field(repr=False)
    primary_key: tuple[str, ...] = ()
    source_row_count: int = 0
    source_content_hash: str = ""
    included_by_profile: tuple[str, ...] = ()

    def expected_for(self, profile: str) -> tuple[int, str]:
        """profile projection.

        runtime은 `action_history`를 적재하지 않아 source 12행 hash와 비교할 수 없다.
        0행·empty hash로 바꾼다(계획 §2.2).
        """

        if self.name == EVALUATION_ONLY_TABLE and profile != "evaluation":
            return 0, EMPTY_ROWS_HASH
        return self.source_row_count, self.source_content_hash


@dataclass(frozen=True)
class AcceptanceReference:
    """최종 epoch 전용 불변값. 테스트만 축소 fixture를 주입한다(계획 §3.8)."""

    evaluation_alarm_types: Mapping[str, int]
    trace_alarm_rows: int
    summary_alarm_rows: int
    trace_seq_values: tuple[str, ...]


FINAL_REFERENCE = AcceptanceReference(
    evaluation_alarm_types=MappingProxyType({"IN": 4538, "OOC": 216, "OOS": 46}),
    trace_alarm_rows=138,
    summary_alarm_rows=51,
    trace_seq_values=("0", "1", "2", "3", "4", "5"),
)


def _fail(check: AcceptanceCheck) -> AcceptanceError:
    return AcceptanceError(check)


def _require(condition: bool, fail: Callable[..., BaseException]) -> None:
    if not condition:
        raise fail("ARCHIVE_INVALID", EXIT_USAGE)


def build_acceptances(
    manifest: Mapping[str, Any],
    expected_tables: Sequence[str],
    fail: Callable[..., BaseException],
) -> tuple[TableAcceptance, ...]:
    """manifest v4의 full acceptance 계약을 lifecycle 전에 확정한다.

    구조 오류는 전부 `ARCHIVE_INVALID`(2)다. 정상 형식 사이의 내용 불일치는 DB 검증
    단계에서 `MODE_CONTRACT_ERROR`(1)로 나온다.
    """

    _require(isinstance(manifest, Mapping), fail)
    _require(manifest.get("format_version") == FORMAT_VERSION, fail)
    _require(manifest.get("dataset_epoch") == DATASET_EPOCH, fail)
    _require(manifest.get("hash_algorithm") == manifest_v3.HASH_ALGORITHM, fail)
    _require(
        manifest.get("value_normalization_version")
        == value_normalization.VALUE_NORMALIZATION_VERSION,
        fail,
    )

    tables = manifest.get("tables")
    _require(isinstance(tables, Mapping), fail)
    _require(set(tables) == set(expected_tables), fail)

    acceptances: list[TableAcceptance] = []
    for name in expected_tables:
        entry = tables[name]
        _require(isinstance(entry, Mapping), fail)
        _require(set(entry) == TABLE_ENTRY_KEYS, fail)

        columns = entry["columns"]
        _require(isinstance(columns, list) and bool(columns), fail)
        _require(
            all(isinstance(c, str) and IDENTIFIER.fullmatch(c) for c in columns), fail
        )
        _require(len(set(columns)) == len(columns), fail)

        column_types = entry["column_types"]
        _require(isinstance(column_types, Mapping), fail)
        _require(set(column_types) == set(columns), fail)
        # membership 전에 str을 확인한다. list/object가 오면 `in frozenset`이
        # raw TypeError를 내고 wrapper가 INTERNAL_ERROR로 잘못 분류한다
        # (구현리뷰 1차 필수 1).
        _require(
            all(
                isinstance(value, str) and value in LOGICAL_TYPES
                for value in column_types.values()
            ),
            fail,
        )

        primary_key = entry["primary_key"]
        _require(isinstance(primary_key, list) and bool(primary_key), fail)
        _require(all(isinstance(k, str) for k in primary_key), fail)
        _require(len(set(primary_key)) == len(primary_key), fail)
        _require(set(primary_key) <= set(columns), fail)

        row_count = entry["row_count"]
        _require(not isinstance(row_count, bool), fail)
        _require(isinstance(row_count, int) and row_count >= 0, fail)

        content_hash = entry["content_hash"]
        _require(isinstance(content_hash, str), fail)
        _require(bool(SHA256_HEX.fullmatch(content_hash)), fail)

        profiles = entry["included_by_profile"]
        _require(isinstance(profiles, list) and bool(profiles), fail)
        seen: list[str] = []
        for profile in profiles:
            _require(isinstance(profile, str) and profile in VALID_PROFILE_KEYS, fail)
            _require(profile not in seen, fail)
            seen.append(profile)

        acceptances.append(
            TableAcceptance(
                name=name,
                columns=tuple(columns),
                column_types=MappingProxyType(dict(column_types)),
                primary_key=tuple(primary_key),
                source_row_count=row_count,
                source_content_hash=content_hash,
                included_by_profile=tuple(profiles),
            )
        )
    return tuple(acceptances)


def _rows(result: Any) -> list[Mapping[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _scalar(connection: Any, statement: Any) -> Any:
    return connection.execute(statement).scalar()


def check_db_types(connection: Any, acceptances: Sequence[TableAcceptance]) -> None:
    """catalog 실제 타입과 manifest logical type을 고정 매핑으로 대조한다(계획 §3.3).

    typed hash **전에** 수행한다. manifest가 타입을 잘못 선언하면 producer도 같은
    선언으로 hash를 만들어 자기일치가 되므로, DDL을 정본으로 두는 이 대조가 필요하다.
    """

    from sqlalchemy import text

    rows = _rows(
        connection.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
    )
    catalog: dict[str, dict[str, str]] = {}
    for row in rows:
        catalog.setdefault(str(row["table_name"]), {})[str(row["column_name"])] = str(
            row["data_type"]
        )

    if set(catalog) != {entry.name for entry in acceptances}:
        raise _fail(AcceptanceCheck.DB_TYPE)
    for entry in acceptances:
        columns = catalog[entry.name]
        if set(columns) != set(entry.columns):
            raise _fail(AcceptanceCheck.DB_TYPE)
        for column, data_type in columns.items():
            logical = DB_TYPE_MAP.get(data_type)
            if logical is None or logical != entry.column_types[column]:
                raise _fail(AcceptanceCheck.DB_TYPE)


def check_row_counts_and_hashes(
    connection: Any, acceptances: Sequence[TableAcceptance], profile: str
) -> None:
    """행 수를 먼저 보고 그다음 typed content hash를 본다(계획 §3.8).

    순서가 진단을 만든다 — 행 수 불일치는 적재 cardinality 문제이고, 행 수가 맞은 뒤의
    hash 불일치는 값 문제다.
    """

    from psycopg import sql
    from sqlalchemy import text

    for entry in acceptances:
        expected_count, expected_hash = entry.expected_for(profile)
        count_sql = sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier("public", entry.name)
        )
        actual_count = _scalar(connection, text(count_sql.as_string(None)))
        if int(actual_count) != expected_count:
            raise _fail(AcceptanceCheck.ROW_COUNT)

        select_sql = sql.SQL("SELECT {columns} FROM {table}").format(
            columns=sql.SQL(", ").join(
                sql.Identifier(column) for column in entry.columns
            ),
            table=sql.Identifier("public", entry.name),
        )
        db_rows = _rows(connection.execute(text(select_sql.as_string(None))))
        try:
            normalized = [
                value_normalization.normalize_db_row(row, entry.column_types)
                for row in db_rows
            ]
        except value_normalization.ValueNormalizationError as exc:
            # numeric NaN/Infinity처럼 DB type은 유효하지만 어떤 manifest hash와도
            # 맞을 수 없는 값이다. 내용 불일치이지 내부 오류가 아니다
            # (구현리뷰 1차 필수 2). 원본 값은 전파하지 않는다.
            raise _fail(AcceptanceCheck.CONTENT_HASH) from exc
        if manifest_v3.hash_canonical_rows(normalized) != expected_hash:
            raise _fail(AcceptanceCheck.CONTENT_HASH)


def check_primary_keys(connection: Any, acceptances: Sequence[TableAcceptance]) -> None:
    """선언 PK로 중복 group 수가 0인지 명시적으로 확인한다.

    DDL 제약이 먼저 막더라도 생략하지 않는다 — verifier가 실제로 판정함을 고정한다.
    """

    from psycopg import sql
    from sqlalchemy import text

    for entry in acceptances:
        keys = sql.SQL(", ").join(sql.Identifier(k) for k in entry.primary_key)
        statement = sql.SQL(
            "SELECT count(*) FROM (SELECT {keys} FROM {table} "
            "GROUP BY {keys} HAVING count(*) > 1) AS duplicates"
        ).format(keys=keys, table=sql.Identifier("public", entry.name))
        if int(_scalar(connection, text(statement.as_string(None)))) != 0:
            raise _fail(AcceptanceCheck.PK)


def check_foreign_keys(connection: Any, loaded_tables: Sequence[str]) -> None:
    """선언 FK 4개의 anti-join missing row가 각각 0인지 확인한다."""

    from psycopg import sql
    from sqlalchemy import text

    loaded = set(loaded_tables)
    for child, child_column, parent, parent_column in DECLARED_FOREIGN_KEYS:
        if child not in loaded or parent not in loaded:
            continue
        statement = sql.SQL(
            "SELECT count(*) FROM {child} AS c "
            "LEFT JOIN {parent} AS p ON c.{child_column} = p.{parent_column} "
            "WHERE c.{child_column} IS NOT NULL AND p.{parent_column} IS NULL"
        ).format(
            child=sql.Identifier("public", child),
            parent=sql.Identifier("public", parent),
            child_column=sql.Identifier(child_column),
            parent_column=sql.Identifier(parent_column),
        )
        if int(_scalar(connection, text(statement.as_string(None)))) != 0:
            raise _fail(AcceptanceCheck.FK)


def check_reference_values(
    connection: Any, reference: AcceptanceReference, loaded_tables: Sequence[str]
) -> None:
    """최종 epoch 전용 분포를 row count·hash와 별도 gate로 남긴다(계획 §3.5)."""

    from psycopg import sql
    from sqlalchemy import text

    loaded = set(loaded_tables)
    if "evaluation" in loaded:
        alarm_sql = sql.SQL(
            "SELECT {column}, count(*) AS n FROM {table} GROUP BY {column}"
        ).format(
            column=sql.Identifier("alarm_type"),
            table=sql.Identifier("public", "evaluation"),
        )
        rows = _rows(connection.execute(text(alarm_sql.as_string(None))))
        actual = {str(row["alarm_type"]): int(row["n"]) for row in rows}
        if actual != dict(reference.evaluation_alarm_types):
            raise _fail(AcceptanceCheck.REFERENCE)

    for table, expected in (
        ("trace_alarm_history", reference.trace_alarm_rows),
        ("summary_alarm_history", reference.summary_alarm_rows),
    ):
        if table not in loaded:
            continue
        count_sql = sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier("public", table)
        )
        if int(_scalar(connection, text(count_sql.as_string(None)))) != expected:
            raise _fail(AcceptanceCheck.REFERENCE)

    if "fdc_trace" in loaded:
        seq_sql = sql.SQL(
            "SELECT DISTINCT {column} FROM {table} ORDER BY {column}"
        ).format(
            column=sql.Identifier("seq_no"),
            table=sql.Identifier("public", "fdc_trace"),
        )
        rows = _rows(connection.execute(text(seq_sql.as_string(None))))
        actual_seq = tuple(str(row["seq_no"]) for row in rows)
        if actual_seq != reference.trace_seq_values:
            raise _fail(AcceptanceCheck.REFERENCE)


def check_timestamp_projection(
    connection: Any,
    acceptances: Sequence[TableAcceptance],
    loaded_tables: Sequence[str],
) -> None:
    """naive wall time을 Asia/Seoul로 해석했을 때 `+09:00`이 되는지 확인한다(계획 §3.6).

    DB가 offset을 저장한다는 주장이 아니다. source naive 값을 Asia/Seoul wall time으로
    읽는 ingest·API 경계를 증명한다. 값이 이미 aware이거나 문자열이면 실패한다.
    """

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from psycopg import sql
    from sqlalchemy import text

    seoul = ZoneInfo("Asia/Seoul")
    loaded = set(loaded_tables)
    for entry in acceptances:
        if entry.name not in loaded:
            continue
        columns = [c for c, t in entry.column_types.items() if t == "timestamp"]
        if not columns:
            continue
        select_sql = sql.SQL("SELECT {columns} FROM {table}").format(
            columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            table=sql.Identifier("public", entry.name),
        )
        for row in _rows(connection.execute(text(select_sql.as_string(None)))):
            for column in columns:
                value = row[column]
                if value is None:
                    continue
                if not isinstance(value, datetime):
                    raise _fail(AcceptanceCheck.TIMESTAMP)
                if value.tzinfo is not None:
                    raise _fail(AcceptanceCheck.TIMESTAMP)
                attached = value.replace(tzinfo=seoul)
                if attached.timetuple()[:6] != value.timetuple()[:6]:
                    raise _fail(AcceptanceCheck.TIMESTAMP)
                if not attached.isoformat().endswith("+09:00"):
                    raise _fail(AcceptanceCheck.TIMESTAMP)


def make_acceptance_postcheck(
    acceptances: Sequence[TableAcceptance],
    loaded_tables: Sequence[str],
    profile: str,
    error_factory: ErrorFactory,
    *,
    reference: AcceptanceReference = FINAL_REFERENCE,
) -> Callable[[Any, Any], None]:
    """full acceptance postcheck. 성공 시 `None`을 반환한다."""

    def postcheck(connection: Any, _plan: Any) -> None:
        try:
            check_db_types(connection, acceptances)
            check_row_counts_and_hashes(connection, acceptances, profile)
            check_primary_keys(connection, acceptances)
            check_foreign_keys(connection, loaded_tables)
            check_reference_values(connection, reference, loaded_tables)
            check_timestamp_projection(connection, acceptances, loaded_tables)
        except AcceptanceError as exc:
            # 내부 범주는 로그·stderr에 직렬화하지 않는다(계획 §3.8).
            raise error_factory("MODE_CONTRACT_ERROR", EXIT_MISMATCH) from exc

    return postcheck
