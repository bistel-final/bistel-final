"""격리 rehearsal의 profile별 CSV 적재 (`V5-CM-2.3`).

검증된 archive snapshot에서 profile에 해당하는 CSV member만 골라 고정 FK 순서로
`COPY ... FROM STDIN` 한다. schema DDL 실행·lifecycle·transaction 소유는 이 모듈의
책임이 아니다 — handler는 부수효과만 내고 `None`을 반환한다(`V5-CM-2.1` 결정 24).

`rehearse_schema`를 import하지 않는다. 실패는 주입받은 error factory로만 만든다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

EXIT_MISMATCH = 1
EXIT_USAGE = 2

# 적재 순서. 1~5는 최종 DDL의 FK가 강제한다.
#   fdc_trace    → lot_history · dim_parameter
#   summary_data → lot_history
#   evaluation   → lot_history
# 6~9는 FK로 강제되지 않으며 멘토 `00_load.sh`의 for 루프 순서를 승계한다(계획 §2.3).
LOAD_ORDER: tuple[str, ...] = (
    "dim_parameter",
    "lot_history",
    "fdc_trace",
    "summary_data",
    "evaluation",
    "trace_alarm_history",
    "summary_alarm_history",
    "metrology",
    "action_history",
)
FINAL_TABLES = frozenset(LOAD_ORDER)
PROFILE_TABLE_COUNT = {"runtime": 8, "evaluation": 9}
EVALUATION_ONLY_TABLE = "action_history"
UTF8_BOM = b"\xef\xbb\xbf"
# manifest v4 entry는 `V5-CM-1.3`이 정한 7키다. 이 Task가 쓰는 3키는 필수이고,
# 나머지 4키(`column_types`·`content_hash`·`primary_key`·`row_count`)는 2.4가 쓴다.
REQUIRED_TABLE_ENTRY_KEYS = frozenset({"file_id", "columns", "included_by_profile"})
VALID_PROFILE_KEYS = frozenset({"runtime", "runtime-e2e", "evaluation"})
# 식별자는 소문자·숫자·밑줄만 허용한다. 최종 스키마의 실제 column이 모두 이 형태이고,
# manifest가 변조돼도 SQL 구조를 바꿀 수 없게 하는 1차 방어다(구현리뷰 1차 필수 1).
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class ErrorFactory(Protocol):
    def __call__(self, reason_code: str, exit_code: int) -> BaseException: ...


Handler = Callable[[Any, Any], None]
PostCheck = Callable[[Any, Any], None]


def _rows(result: Any) -> list[Mapping[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _member_bytes(
    archive: Any, member: str, fail: Callable[..., BaseException]
) -> bytes:
    """ZIP entry가 정확히 1개이고 directory·symlink가 아님을 확인한 뒤 읽는다."""

    matches = [info for info in archive.infolist() if info.filename == member]
    if len(matches) != 1:
        raise fail("ARCHIVE_INVALID", EXIT_USAGE)
    info = matches[0]
    if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise fail("ARCHIVE_INVALID", EXIT_USAGE)
    return archive.read(info)


def _csv_body(
    raw: bytes, columns: Sequence[str], fail: Callable[..., BaseException]
) -> bytes:
    """선두 BOM만 제거하고 header가 manifest columns와 순서까지 같은지 확인한다."""

    body = raw[len(UTF8_BOM) :] if raw.startswith(UTF8_BOM) else raw
    if UTF8_BOM in body or b"\x00" in body:
        raise fail("ARCHIVE_INVALID", EXIT_USAGE)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise fail("ARCHIVE_INVALID", EXIT_USAGE) from exc
    try:
        header = next(csv.reader(io.StringIO(text), strict=True))
    except (csv.Error, StopIteration) as exc:
        raise fail("ARCHIVE_INVALID", EXIT_USAGE) from exc
    if header != list(columns):
        raise fail("ARCHIVE_MISMATCH", EXIT_MISMATCH)
    return body


def validate_manifest_tables(
    manifest_tables: Any, fail: Callable[..., BaseException]
) -> Mapping[str, Mapping[str, Any]]:
    """table entry의 key·type·식별자 형태를 lifecycle 전에 확인한다.

    구조 오류는 `ARCHIVE_INVALID`(2)다. 값은 맞고 내용이 다른 경우와 구분한다.
    """

    if not isinstance(manifest_tables, Mapping):
        raise fail("ARCHIVE_INVALID", EXIT_USAGE)
    for table, entry in manifest_tables.items():
        if not isinstance(table, str) or not IDENTIFIER.fullmatch(table):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if not isinstance(entry, Mapping):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if not REQUIRED_TABLE_ENTRY_KEYS <= set(entry):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if not isinstance(entry["file_id"], str) or not entry["file_id"]:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        columns = entry["columns"]
        if not isinstance(columns, list) or not columns:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if any(not isinstance(c, str) or not IDENTIFIER.fullmatch(c) for c in columns):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if len(set(columns)) != len(columns):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        profiles = entry["included_by_profile"]
        # set()을 먼저 만들면 unhashable 원소에서 raw TypeError가 난다.
        # 원소를 순서대로 확인한 뒤에만 집합 연산을 쓴다(구현리뷰 2차 필수 1).
        if not isinstance(profiles, list) or not profiles:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        seen_profiles: list[str] = []
        for profile in profiles:
            if not isinstance(profile, str) or profile not in VALID_PROFILE_KEYS:
                raise fail("ARCHIVE_INVALID", EXIT_USAGE)
            if profile in seen_profiles:
                raise fail("ARCHIVE_INVALID", EXIT_USAGE)
            seen_profiles.append(profile)
    return manifest_tables


def validate_intake_members(
    selected: Any, fail: Callable[..., BaseException]
) -> Mapping[str, Mapping[str, Any]]:
    """intake `selected_members`의 key·type·path 유일성을 확인한다."""

    if not isinstance(selected, list):
        raise fail("ARCHIVE_INVALID", EXIT_USAGE)
    members: dict[str, Mapping[str, Any]] = {}
    for entry in selected:
        if not isinstance(entry, Mapping):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        path = entry.get("path")
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        if not isinstance(path, str) or not path:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        # bool 은 int 의 하위 타입이라 명시적으로 배제한다.
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if not isinstance(digest, str) or not SHA256_HEX.fullmatch(digest):
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if path in members:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        members[path] = entry
    return members


def select_tables(
    manifest_tables: Mapping[str, Mapping[str, Any]],
    profile: str,
    fail: Callable[..., BaseException],
) -> tuple[str, ...]:
    """manifest `included_by_profile`에서 profile 적재 집합을 고른다."""

    if profile not in PROFILE_TABLE_COUNT:
        raise fail("PROFILE_MISMATCH", EXIT_MISMATCH)
    if set(manifest_tables) != FINAL_TABLES:
        raise fail("ARCHIVE_MISMATCH", EXIT_MISMATCH)

    def _for(key: str) -> set[str]:
        return {
            table
            for table, entry in manifest_tables.items()
            if key in entry["included_by_profile"]
        }

    runtime = _for("runtime")
    e2e = _for("runtime-e2e")
    evaluation = _for("evaluation")
    if runtime != e2e or evaluation - runtime != {EVALUATION_ONLY_TABLE}:
        raise fail("PROFILE_MISMATCH", EXIT_MISMATCH)

    selected = runtime if profile == "runtime" else evaluation
    if len(selected) != PROFILE_TABLE_COUNT[profile]:
        raise fail("PROFILE_MISMATCH", EXIT_MISMATCH)
    return tuple(table for table in LOAD_ORDER if table in selected)


def verified_csv_bodies(
    archive: Any,
    manifest_tables: Mapping[str, Mapping[str, Any]],
    intake_members: Mapping[str, Mapping[str, Any]],
    tables: Sequence[str],
    fail: Callable[..., BaseException],
) -> dict[str, bytes]:
    """적재 전에 member 무결성·encoding·header를 모두 확인한다."""

    bodies: dict[str, bytes] = {}
    for table in tables:
        entry = manifest_tables[table]
        member = entry["file_id"]
        raw = _member_bytes(archive, member, fail)
        declared = intake_members.get(member)
        if declared is None:
            raise fail("ARCHIVE_INVALID", EXIT_USAGE)
        if (
            declared.get("size_bytes") != len(raw)
            or declared.get("sha256") != hashlib.sha256(raw).hexdigest()
        ):
            raise fail("ARCHIVE_MISMATCH", EXIT_MISMATCH)
        bodies[table] = _csv_body(raw, entry["columns"], fail)
    return bodies


def make_load_handlers(
    csv_bodies: Mapping[str, bytes],
    columns_by_table: Mapping[str, tuple[str, ...]],
    tables: Sequence[str],
    profile: str,
    error_factory: ErrorFactory,
) -> tuple[Handler, PostCheck]:
    """고정 순서 COPY handler와 최소 profile postcheck를 만든다.

    `csv_bodies`·`columns_by_table`은 호출자가 검증을 끝낸 불변 스냅샷이다.
    """

    def fail(reason_code: str, exit_code: int = EXIT_MISMATCH) -> BaseException:
        return error_factory(reason_code, exit_code)

    ordered = tuple(table for table in LOAD_ORDER if table in tables)

    def handler(connection: Any, _plan: Any) -> None:
        from psycopg import sql

        driver = connection.connection
        for table in ordered:
            # 식별자를 문자열로 잇지 않는다. manifest가 변조돼도 SQL 구조가 바뀌지
            # 않도록 psycopg가 quote한다(구현리뷰 1차 필수 1).
            statement = sql.SQL(
                "COPY {table} ({columns}) FROM STDIN "
                "WITH (FORMAT CSV, HEADER TRUE, NULL '', ENCODING 'UTF8')"
            ).format(
                table=sql.Identifier("public", table),
                columns=sql.SQL(", ").join(
                    sql.Identifier(column) for column in columns_by_table[table]
                ),
            )
            with driver.cursor() as cursor:
                with cursor.copy(statement) as copy:
                    copy.write(csv_bodies[table])

    def postcheck(connection: Any, _plan: Any) -> None:
        from sqlalchemy import text

        for table in ordered:
            if table == EVALUATION_ONLY_TABLE:
                continue
            # table 이름은 LOAD_ORDER 상수에서만 오고 IDENTIFIER를 통과한 값이다.
            probe = f'SELECT EXISTS (SELECT 1 FROM public."{table}") AS present'
            rows = _rows(connection.execute(text(probe)))
            if not rows or rows[0]["present"] is not True:
                raise fail("MODE_CONTRACT_ERROR")

        count_sql = f'SELECT count(*) AS n FROM public."{EVALUATION_ONLY_TABLE}"'
        rows = _rows(connection.execute(text(count_sql)))
        expected_action = 12 if profile == "evaluation" else 0
        if not rows or int(rows[0]["n"]) != expected_action:
            raise fail("MODE_CONTRACT_ERROR")

    return handler, postcheck
