"""`V5-CM-3.1` final reference extension의 순수 계약.

DB를 열지 않는다. catalog를 읽어 온 행을 판정하는 순수 함수와 SQL·manifest 계약만
담는다. transaction·lifecycle·CLI는 묶음 2가 소유한다.

계약은 시스템설계서 v2.1에서 왔다.

- `r03_alarm_history` 12컬럼 — §3.2 `:389-390`
- `v_alarm_event` 17컬럼 — §3.5 `:472-473`
- `rule_code` 3종과 R03 `alarm_type=OOS` — §3.1 `:373-374`
- `h.wafer_id = a.wafer` resolve — §3.3 `:415`

**V4 `apply_reference_extensions.py`를 import하지 않는다.** 그 모듈은 이미 공용에 적용된
V4 계약을 동결한 것이라, final 계약이 그걸 참조하면 두 계보가 다시 얽힌다(계획 §3.2).
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = REPOSITORY_ROOT / "backend" / "migrations" / "v5"
CANONICAL_SQL = MIGRATION_DIR / "001_reference_extensions_final.sql"
SUCCESSOR_SQL = MIGRATION_DIR / "001_reference_extensions_final_from_v4.sql"

#: marker·receipt·profile manifest가 함께 쓰는 단일 식별자.
#: 구 `001_reference_extensions`와 이름이 겹치지 않아야 한다(계획 §4.1).
MIGRATION_ID = "v5_001_reference_extensions_final"

R03_TABLE = "r03_alarm_history"
ALARM_VIEW = "v_alarm_event"
SCHEMA = "public"


class ReferenceV5Error(Exception):
    """typed reason과 exit code를 함께 나른다. 값·경로는 담지 않는다."""

    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# r03_alarm_history 최종 계약 (계획 §5)
# ---------------------------------------------------------------------------

#: `(컬럼, information_schema.data_type, character_maximum_length, is_nullable)`.
#: **순서가 계약이다.** `ALTER TABLE ... ADD COLUMN`은 컬럼을 끝에 붙이고 PostgreSQL은
#: 순서를 바꿀 수 없어서, successor 경로가 canonical과 같은 순서로 수렴하려면 빈 table을
#: 버리고 다시 만들어야 한다. 격리 PostgreSQL 16 실측으로 확인했다.
R03_COLUMNS: tuple[tuple[str, str, int | None, bool], ...] = (
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

#: constraint 이름까지 계약이다. 두 migration 경로가 같은 이름으로 수렴해야
#: `schema_signature_sha256()`가 하나로 모인다(계획 §4.1).
#: `(contype, pg_get_constraintdef)`. **정의까지 계약이다.**
#:
#: 이름과 종류만 보면 `CHECK (false)`가 옳은 이름으로 들어와도 통과한다
#: (구현리뷰 1차 필수 2). 정의는 canonical DDL을 격리 PostgreSQL 16에 적용해 실측했다.
R03_CONSTRAINT_DEFINITIONS: Mapping[str, tuple[str, str]] = {
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

R03_CONSTRAINTS: Mapping[str, str] = {
    name: contype for name, (contype, _definition) in R03_CONSTRAINT_DEFINITIONS.items()
}

#: 계획 §5.2가 고정한 type별 개수. `R03_CONSTRAINTS`에서 유도한 값과 **같아야** 한다.
#: 판정은 이름·종류 exact 비교가 하고, 이 상수는 그 비교가 옳은지 검사하는 기준이다.
R03_CONSTRAINT_COUNTS: Mapping[str, int] = {"p": 1, "u": 1, "f": 2, "c": 7}


def constraint_type_counts(
    constraints: Mapping[str, str] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for contype in (constraints or R03_CONSTRAINTS).values():
        counts[contype] = counts.get(contype, 0) + 1
    return counts


#: base 9로 나가는 FK. delete action은 둘 다 `NO ACTION`(`a`)이어야 한다.
#: CASCADE면 base 9 DELETE가 R03로 번진다.
R03_FOREIGN_KEYS: Mapping[str, tuple[str, str]] = {
    "r03_alarm_history_lot_hist_id_fkey": ("lot_hist_id", "lot_history"),
    "r03_alarm_history_parameter_id_fkey": ("parameter_id", "dim_parameter"),
}
EXPECTED_FK_ACTION = "a"

POLICY_VERSION = "R03_CONSEC_V1"
ALARM_ID_PATTERN = re.compile(r"^R03-[0-9a-f]{20}$")
MEMBER_WAFER_REF_COUNT = 3


# ---------------------------------------------------------------------------
# v_alarm_event 최종 계약 (계획 §6)
# ---------------------------------------------------------------------------

#: `(컬럼, information_schema.data_type)` 17개. 설계 §3.5 순서 그대로다.
VIEW_COLUMNS: tuple[tuple[str, str], ...] = (
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

#: 정규화 AlarmEvidence(설계 §3.1 `:366-369`)에만 있는 필드. **View 컬럼이 아니다.**
#: Repository가 `dim_parameter` 등에서 보강하는 DTO enrichment다(계획 §6.1).
DTO_ONLY_FIELDS: frozenset[str] = frozenset(
    {"limit_type", "limit_value", "source_detail"}
)

VIEW_SOURCES: tuple[str, ...] = ("TRACE", "SUMMARY", "R03")
VIEW_RULE_CODES: Mapping[str, str] = {
    "TRACE": "TRACE_OOS",
    "SUMMARY": "SUMMARY_OOC",
    "R03": "R03_CONSEC",
}
R03_ALARM_TYPE = "OOS"

#: 전환 직후 기대 행 수. **schema 판정에 쓰지 않는다**(계획 §4.2 · §7.3).
#: `V5-A-1.4`가 R03 3건을 넣으면 `3 / 192`가 되고 그 역시 정상이다.
DATA_PHASES: Mapping[str, tuple[int, int]] = {
    "REFERENCE_EMPTY": (0, 189),
    "R03_POPULATED": (3, 192),
}
STORED_ALARM_ROWS = 189
BRANCH_ROWS_EMPTY: Mapping[str, int] = {"TRACE": 138, "SUMMARY": 51, "R03": 0}


# ---------------------------------------------------------------------------
# migration bundle identity (계획 §4.1)
# ---------------------------------------------------------------------------


def _normalize_sql(text: str) -> str:
    """ASCII whitespace를 한 칸으로 줄이고 trim한다.

    줄바꿈·들여쓰기 차이로 bundle identity가 흔들리면 marker가 무의미해진다.
    """

    return " ".join(text.split())


def _canonicalize_migration(text: str) -> str:
    """의미를 바꾸지 않는 정규화만 한다.

    `_normalize_sql()`은 **string literal 안의 공백까지** 줄인다. migration identity에
    쓰면 의미가 다른 SQL이 같은 hash를 가질 수 있다(구현리뷰 1차 권장 1). 여기서는
    줄바꿈 형식과 뒤쪽 공백만 정리한다.
    """

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


def migration_bundle_sha256(
    *, canonical: Path | None = None, successor: Path | None = None
) -> str:
    """두 SQL을 하나의 bundle identity로 묶는다.

    canonical과 successor는 **같은 schema로 수렴해야** 하므로 identity도 하나다.
    한쪽만 바뀌어도 bundle hash가 달라진다.

    정규화는 **의미를 바꾸지 않는 것만** 한다 — 줄바꿈 형식과 줄 끝 공백뿐이다.
    string literal 안의 공백까지 줄이면 의미가 다른 SQL이 같은 hash를 가질 수 있다.
    """

    digest = hashlib.sha256()
    for path in (canonical or CANONICAL_SQL, successor or SUCCESSOR_SQL):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ReferenceV5Error("MIGRATION_UNREADABLE", EXIT_USAGE) from exc
        digest.update(_canonicalize_migration(raw.decode("utf-8")).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# SQL guard (계획 §9.1)
# ---------------------------------------------------------------------------

#: V5 migration이 실행해도 되는 statement. `(operation, target)` 쌍이 계약이다.
#:
#: CREATE 대상만 보면 `DROP TABLE lot_history` 같은 base 9 mutation이 통과한다 —
#: base 9는 View가 참조하므로 금지 토큰에 넣을 수 없고, operation을 안 보면 막을 수단이
#: 없다(구현리뷰 1차 필수 1). 그래서 statement 단위로 판정한다.
CANONICAL_STATEMENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("CREATE TABLE", R03_TABLE),
        ("COMMENT ON TABLE", R03_TABLE),
        ("CREATE VIEW", ALARM_VIEW),
    }
)

#: successor는 위에 더해 read-only guard와 두 DROP만 허용한다.
SUCCESSOR_STATEMENTS: frozenset[tuple[str, str]] = CANONICAL_STATEMENTS | {
    ("DO", ""),
    ("DROP VIEW", ALARM_VIEW),
    ("DROP TABLE", R03_TABLE),
}

#: **집합만으로는 부족하다**(구현리뷰 7차 필수 3).
#:
#: `(operation, target)`이 허용 집합에 속하는지만 보면 빈 파일·일부 statement 누락·중복
#: `COMMENT ON TABLE`·순서 역전이 모두 통과한다. 특히 comment는 마지막 선언이 최종값이라
#: 정적 계약이 첫 값을 보고 정상으로 오판한다. route별 **정확한 순서**를 고정한다.
CANONICAL_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("CREATE TABLE", R03_TABLE),
    ("COMMENT ON TABLE", R03_TABLE),
    ("CREATE VIEW", ALARM_VIEW),
)
SUCCESSOR_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("DO", ""),
    ("DROP VIEW", ALARM_VIEW),
    ("DROP TABLE", R03_TABLE),
    *CANONICAL_SEQUENCE,
)
MIGRATION_ROUTES: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType(
    {"canonical": CANONICAL_SEQUENCE, "successor": SUCCESSOR_SEQUENCE}
)

#: `DO` 블록 본문에 나오면 안 되는 동사. guard는 읽기만 해야 한다.
DO_BLOCK_FORBIDDEN: tuple[str, ...] = (
    "insert",
    "update",
    "delete",
    "truncate",
    "drop",
    "alter",
    "create",
    "grant",
    "revoke",
    "copy",
)

#: SQL **식별자** 경계로 본다. `\b`는 Unicode 기준이라 `document_chunk를` 같이 뒤에
#: 비ASCII 단어 문자가 붙으면 매치되지 않는다 — string literal에 숨길 수 있다.
#: ASCII 식별자 문자만 경계로 삼으면 `nl_query_log_id`(다른 이름)는 그대로 통과한다.
_IDENTIFIER_BOUNDARY: Mapping[str, re.Pattern[str]] = {
    token: re.compile(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])")
    for token in (
        "document_chunk",
        "document_corpus",
        "document",
        "nl_query_log",
        "vector",
        "agent_run",
        "agent_tool_call",
        "agent_prediction",
        "approval_request",
        "action_delivery",
        "audit_log",
    )
}

#: 다른 영역이 소유한 객체. 이름이 실행 statement에 등장하면 거부한다.
FORBIDDEN_TOKENS: tuple[str, ...] = tuple(_IDENTIFIER_BOUNDARY)

#: 지원하지 않는 string literal prefix. 묵시적으로 받지 않고 거부한다.
_UNSUPPORTED_QUOTE = re.compile(r"(?<![a-z0-9_])(?:[EeBbXx]'|[Uu]&['\"])")

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_OPEN = "/*"

#: statement 앞머리에서 뽑는 `(operation, target)`.
_STATEMENT = re.compile(
    r"^\s*(?P<op>"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+VIEW|TABLE|VIEW|INDEX|EXTENSION|SCHEMA)"
    r"|COMMENT\s+ON\s+(?:TABLE|VIEW|COLUMN)"
    r"|DROP\s+(?:MATERIALIZED\s+VIEW|TABLE|VIEW|INDEX|EXTENSION|SCHEMA)"
    r"|ALTER\s+(?:TABLE|VIEW|INDEX|SCHEMA)"
    r"|TRUNCATE(?:\s+TABLE)?|INSERT\s+INTO|UPDATE|DELETE\s+FROM|COPY|GRANT|REVOKE"
    r"|SELECT|WITH|DO"
    r")"
    r"(?:\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?\"?(?P<target>[a-z_][a-z0-9_]*)\"?)?",
    re.IGNORECASE,
)


def _scan(text: str) -> tuple[str, ...]:
    """주석을 걷어내고 statement로 나눈다. **한 번의 상태 기반 주사로 한다.**

    정규식으로 `--`를 먼저 지우면 string literal 안의 `--`까지 주석으로 오인한다.
    아래 입력은 closing quote와 첫 `;`가 통째로 지워져 **두 statement가 하나의 허용된
    `COMMENT ON TABLE`로 보인다** — allowlist 우회다(구현리뷰 2차 필수 1).

    ```sql
    COMMENT ON TABLE r03_alarm_history IS 'hello -- inside literal';
    DROP TABLE lot_history;
    ```

    그래서 comment 제거와 statement 분할이 **같은 주사에서** 일어나야 한다. 둘을 따로
    하면 서로 다른 것을 보게 된다.

    다루는 상태: `'...'`(`''` escape) · `"..."`(`""` escape) · `$tag$...$tag$` ·
    `-- ...` · `/* ... */`(PostgreSQL은 중첩을 허용한다).

    닫히지 않은 literal·identifier·dollar quote·block comment는 **거부한다.** 어디까지가
    무엇인지 알 수 없으면 판정할 수 없다.
    """

    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        # --- 주석 ---
        if text.startswith("--", index):
            end_of_line = text.find("\n", index)
            index = length if end_of_line == -1 else end_of_line
            current.append(" ")
            continue
        if text.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if text.startswith("/*", index):
                    depth += 1
                    index += 2
                elif text.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)
            current.append(" ")
            continue

        # --- 지원하지 않는 quoted 문법은 **명시적으로 거부한다** ---
        # `E'...'`(backslash escape)와 `U&'...'`(unicode escape)는 escape 규칙이 달라
        # 지금 주사로 정확히 못 읽는다. 부분 지원 상태로 묵시적으로 받으면 그 안에
        # 숨긴 statement를 놓친다(구현리뷰 3차 권장 1).
        if _UNSUPPORTED_QUOTE.match(text, index):
            raise ReferenceV5Error("SQL_SYNTAX_UNSUPPORTED", EXIT_MISMATCH)

        # --- quoted 구간은 통째로 보존한다 ---
        if char in "'\"":
            closing = char
            current.append(char)
            index += 1
            while True:
                if index >= length:
                    raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)
                if text[index] == closing:
                    if text.startswith(closing * 2, index):
                        current.append(closing * 2)
                        index += 2
                        continue
                    current.append(closing)
                    index += 1
                    break
                current.append(text[index])
                index += 1
            continue
        if char == "$":
            match = re.match(r"\$[a-z_]*\$", text[index:], re.IGNORECASE)
            if match:
                tag = match.group(0)
                closing_at = text.find(tag, index + len(tag))
                if closing_at == -1:
                    raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)
                current.append(text[index : closing_at + len(tag)])
                index = closing_at + len(tag)
                continue

        # --- statement 경계 ---
        if char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    if "".join(current).strip():
        statements.append("".join(current))
    return tuple(statement for statement in statements if statement.strip())


def executable_sql(text: str) -> str:
    """주석을 걷어낸 실행 statement만 남긴다. literal 안은 보존된다."""

    return " ".join(_scan(text))


def split_statements(text: str) -> tuple[str, ...]:
    """`;`로 나누되 literal·identifier·dollar quote 안은 경계로 보지 않는다."""

    return _scan(text)


def classify_statement(statement: str) -> tuple[str, str]:
    """`(operation, target)`을 뽑는다. 못 알아보면 거부한다."""

    match = _STATEMENT.match(statement)
    if match is None:
        raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)
    operation = " ".join(match.group("op").upper().split())
    target = (match.group("target") or "").lower()
    if operation == "DO":
        target = ""
    return operation, target


def created_objects(text: str) -> tuple[str, ...]:
    """`CREATE`로 만들어지는 대상."""

    created: list[str] = []
    for statement in split_statements(text):
        operation, target = classify_statement(statement)
        if operation.startswith("CREATE"):
            created.append(target)
    return tuple(created)


def assert_sql_scope(
    text: str, *, allowed: frozenset[tuple[str, str]] | None = None
) -> None:
    """실행 statement가 허용 `(operation, target)` 밖으로 나가지 않는지 본다.

    base 9·RAG·Runtime을 **읽는** 것은 View 정의에 필요하므로 statement 단위로 본다.
    `DROP TABLE lot_history` 같은 mutation은 여기서 걸린다.
    """

    permitted = CANONICAL_STATEMENTS if allowed is None else allowed
    body = executable_sql(text).lower()
    for token in FORBIDDEN_TOKENS:
        if _IDENTIFIER_BOUNDARY[token].search(body):
            raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)
    for statement in split_statements(text):
        pair = classify_statement(statement)
        if pair not in permitted:
            raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)
        if pair[0] == "DO":
            inner = statement.lower()
            for verb in DO_BLOCK_FORBIDDEN:
                if re.search(rf"\b{verb}\b", inner):
                    # guard는 읽기만 해야 한다.
                    raise ReferenceV5Error("SQL_SCOPE_VIOLATION", EXIT_MISMATCH)


def assert_statement_sequence(text: str, *, route: str) -> None:
    """실행 statement가 route의 **정확한 순서**와 같은지 본다.

    개수까지 exact다. `DROP TABLE`이 두 번이거나 `COMMENT ON TABLE`이 하나 더 붙으면
    거부한다 — 뒤에 붙은 comment가 실제 최종값이 되기 때문이다(구현리뷰 7차 필수 3).
    """

    expected = MIGRATION_ROUTES.get(route)
    if expected is None:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    actual = tuple(classify_statement(st) for st in split_statements(text))
    if actual != expected:
        raise ReferenceV5Error("SQL_SEQUENCE_MISMATCH", EXIT_MISMATCH)


def assert_migration_files_in_scope() -> None:
    """두 실물 파일이 허용 집합 **과 정확한 순서** 안에 있는지 본다."""

    for route, path, allowed in (
        ("canonical", CANONICAL_SQL, CANONICAL_STATEMENTS),
        ("successor", SUCCESSOR_SQL, SUCCESSOR_STATEMENTS),
    ):
        text = path.read_text(encoding="utf-8")
        assert_sql_scope(text, allowed=allowed)
        assert_statement_sequence(text, route=route)


#: TRACE·SUMMARY resolve는 반드시 `h.wafer_id = a.wafer`다. final epoch에서 alarm의
#: `wafer`는 varchar(24) = wafer_id 문자열이라 `wafer_no`(smallint)와 비교할 수 없다.
WAFER_JOIN = "h.wafer_id = a.wafer"
LEGACY_WAFER_JOIN = "h.wafer_no = a.wafer"
WAFER_JOIN_COUNT = 2


def assert_view_sql_shape(text: str) -> None:
    """View SQL의 join·branch 형태를 본다. **보조 lint다.**

    정본 판정은 `assert_view_identity()`가 PostgreSQL이 정규화한 정의 hash로 한다
    (구현리뷰 1차 필수 3). 여기서는 사람이 읽기 쉬운 형태 오류만 빨리 잡는다.
    """

    body = " ".join(executable_sql(text).split())
    if body.count(WAFER_JOIN) != WAFER_JOIN_COUNT:
        raise ReferenceV5Error("VIEW_CONTRACT_MISMATCH", EXIT_MISMATCH)
    if LEGACY_WAFER_JOIN in body:
        raise ReferenceV5Error("VIEW_CONTRACT_MISMATCH", EXIT_MISMATCH)
    if body.count("UNION ALL") != len(VIEW_SOURCES) - 1:
        raise ReferenceV5Error("VIEW_CONTRACT_MISMATCH", EXIT_MISMATCH)
    for rule_code in VIEW_RULE_CODES.values():
        if f"'{rule_code}'" not in body:
            raise ReferenceV5Error("VIEW_CONTRACT_MISMATCH", EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# catalog 판정 (계획 §9.1)
# ---------------------------------------------------------------------------


def assert_r03_columns(rows: Sequence[Mapping[str, Any]]) -> None:
    """`information_schema.columns`를 ordinal 순으로 받아 12컬럼 계약과 대조한다."""

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
        raise ReferenceV5Error("R03_CONTRACT_MISMATCH", EXIT_MISMATCH)


def assert_r03_constraints(rows: Sequence[Mapping[str, Any]]) -> None:
    """`pg_constraint`의 이름·종류·**정의**가 계약과 exact인지 본다.

    `pg_get_constraintdef(oid)`를 `definition`으로 받는다. 이름과 종류만 보면
    `CHECK (false)`가 옳은 이름으로 들어와도 통과한다(구현리뷰 1차 필수 2).
    """

    observed = {
        str(row["conname"]): (
            str(row["contype"]),
            _normalize_sql(str(row["definition"])),
        )
        for row in rows
    }
    expected = {
        name: (contype, _normalize_sql(definition))
        for name, (contype, definition) in R03_CONSTRAINT_DEFINITIONS.items()
    }
    if observed != expected:
        raise ReferenceV5Error("R03_CONTRACT_MISMATCH", EXIT_MISMATCH)
    # type별 개수는 위 exact 비교가 이미 함의한다. 여기서 다시 세면 어떤 입력으로도
    # 단독으로 깨뜨릴 수 없는 죽은 방어가 된다(변이 N4). 개수 계약은
    # `R03_CONSTRAINT_COUNTS`와 `R03_CONSTRAINTS`의 **상수 일관성**으로 지킨다.


def assert_r03_foreign_keys(rows: Sequence[Mapping[str, Any]]) -> None:
    """FK 대상과 delete action을 본다. CASCADE면 base 9 DELETE가 번진다."""

    observed = {
        str(row["conname"]): (
            str(row["column_name"]),
            str(row["referenced_table"]),
            str(row["delete_action"]),
        )
        for row in rows
    }
    if set(observed) != set(R03_FOREIGN_KEYS):
        raise ReferenceV5Error("R03_CONTRACT_MISMATCH", EXIT_MISMATCH)
    for name, (column, parent) in R03_FOREIGN_KEYS.items():
        if observed[name] != (column, parent, EXPECTED_FK_ACTION):
            raise ReferenceV5Error("R03_CONTRACT_MISMATCH", EXIT_MISMATCH)


def assert_view_columns(rows: Sequence[Mapping[str, Any]]) -> None:
    """View 17컬럼의 이름·순서·타입을 본다."""

    observed = tuple((str(row["column_name"]), str(row["data_type"])) for row in rows)
    if observed != VIEW_COLUMNS:
        raise ReferenceV5Error("VIEW_CONTRACT_MISMATCH", EXIT_MISMATCH)
    # DTO enrichment 필드 배제도 위 exact 비교가 함의한다(변이 N6). 계약은
    # `VIEW_COLUMNS`와 `DTO_ONLY_FIELDS`가 겹치지 않는다는 **상수 성질**이다.


#: canonical DDL이 PostgreSQL **16**에서 만드는 `pg_get_viewdef(oid, true)`
#: 정규형의 hash.
#:
#: 정의를 hash하기만 하고 기대값과 대조하지 않으면, 틀린 View가 그대로 최초 marker의
#: 정본이 된다(구현리뷰 1차 필수 3). `V5-CM-2.6`의 `COMPAT_VIEW_SHA256`과 같은 방식이다.
#: major가 바뀌면 정규형도 바뀌므로 hash·계획·marker를 함께 재승인해야 한다.
CANONICAL_VIEW_SHA256 = (
    "4d25449fa98d4b503e73b361cfb95c2a8b626e0bc40438a9d6d9b3e2fb7f48c0"
)
VIEW_SERVER_MAJOR = 16


def assert_view_identity(definition: str) -> None:
    """live View 정의가 canonical과 **같은 것**인지 본다.

    두 migration 경로가 이 하나의 identity로 수렴해야 한다.
    """

    if view_definition_sha256(definition) != CANONICAL_VIEW_SHA256:
        raise ReferenceV5Error("VIEW_CONTRACT_MISMATCH", EXIT_MISMATCH)


def normalized_view_definition(definition: str) -> str:
    return _normalize_sql(definition)


def view_definition_sha256(definition: str) -> str:
    return hashlib.sha256(
        normalized_view_definition(definition).encode("utf-8")
    ).hexdigest()


def schema_signature_sha256(
    *,
    r03_columns: Sequence[Mapping[str, Any]],
    r03_constraints: Sequence[Mapping[str, Any]],
    view_columns: Sequence[Mapping[str, Any]],
    view_definition: str,
    r03_comment: Any,
    view_comment: Any,
) -> str:
    """final schema identity. **행 수를 담지 않는다.**

    `V5-A-1.4`가 R03 3건을 넣으면 `r03_rows`·`view_rows`가 `0/189 → 3/192`로 바뀐다.
    행 수를 identity에 넣으면 정상 진행한 DB가 drift로 판정된다(계획 §4.2).
    """

    import json

    # **non-canonical catalog는 signature를 만들 수 없다.**
    #
    # View만 identity를 확인하고 R03 컬럼·constraint·View 컬럼은 안 보면, 이름·종류는
    # 같고 `CHECK (false)`인 catalog가 canonical과 **같은 signature**를 낸다. 그러면
    # 잘못된 snapshot이 최초 marker의 schema identity가 된다(구현리뷰 2차 필수 3).
    assert_r03_columns(r03_columns)
    assert_r03_constraints(r03_constraints)
    assert_view_columns(view_columns)
    assert_view_identity(view_definition)
    # **comment도 schema 계약이다**(구현리뷰 7차 필수 1).
    # security signature는 owner·ACL만 보므로, 여기에 넣지 않으면 comment가 지워져도
    # 어느 signature에도 드러나지 않는다.
    assert_canonical_comments(r03_comment=r03_comment, view_comment=view_comment)
    payload = {
        "migration_id": MIGRATION_ID,
        "r03_comment": r03_comment,
        "view_comment": view_comment,
        "r03_columns": [
            [
                str(row["column_name"]),
                str(row["data_type"]),
                row["character_maximum_length"],
                str(row["is_nullable"]).upper(),
            ]
            for row in r03_columns
        ],
        "r03_constraints": sorted(
            [
                str(row["conname"]),
                str(row["contype"]),
                _normalize_sql(str(row["definition"])),
            ]
            for row in r03_constraints
        ),
        "view_columns": [
            [str(row["column_name"]), str(row["data_type"])] for row in view_columns
        ],
        "view_definition_sha256": view_definition_sha256(view_definition),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# canonical comment 계약 (계획 §6.5 · 7차 계획리뷰 필수 1)
# ---------------------------------------------------------------------------

#: **comment는 복원 대상이 아니라 canonical schema 계약이다.**
#:
#: owner·`relacl`은 운영이 부여한 보안 상태라 재생성 뒤 pre-state로 되돌린다. 그러나
#: comment는 이 migration이 새로 정의한다. pre-state를 복원하면 canonical DDL이 방금 쓴
#: 문자열을 지우고, base-only 경로와도 갈린다(7차 계획리뷰 필수 1 · 실측 V4 pre-state는
#: `None`이었다).
#:
#: 값은 두 migration SQL이 실제로 선언한 문자열이며, 회귀가 SQL 실물과 대조한다.
R03_COMMENT = "V5-CM-3.1 final reference extension. R03 파생은 V5-A-1.4가 적재한다."

#: canonical DDL은 View comment를 선언하지 않는다. legacy·CM-2.6 호환 View도 `NULL`이라
#: 정보 손실이 없다(8차 계획리뷰 실측).
VIEW_COMMENT: str | None = None

_COMMENT_ON_TABLE = re.compile(
    r"COMMENT\s+ON\s+TABLE\s+" + R03_TABLE + r"\s+IS\s+'((?:[^']|'')*)'",
    re.IGNORECASE,
)


def declared_table_comment(text: str) -> str | None:
    """migration SQL이 선언한 R03 comment를 꺼낸다.

    상수와 DDL이 따로 놀지 않도록 회귀가 이 값을 `R03_COMMENT`와 대조한다.

    **선언이 둘 이상이면 값을 돌려주지 않는다.** 첫 값을 반환하면 뒤에 붙은 선언이 실제
    최종값인데도 정상으로 오판한다(구현리뷰 7차 필수 3).
    """

    matches = _COMMENT_ON_TABLE.findall(executable_sql(text))
    if not matches:
        return None
    if len(matches) > 1:
        raise ReferenceV5Error("COMMENT_DECLARATION_AMBIGUOUS", EXIT_MISMATCH)
    return matches[0].replace("''", "'")


def assert_canonical_comments(*, r03_comment: Any, view_comment: Any) -> None:
    """두 경로 모두 canonical comment로 수렴했는지 본다.

    successor는 구 comment를 복원하지 않고, base-only는 애초에 복원할 것이 없다.
    그러므로 기대값은 **경로와 무관하게 하나**다(계획 §6.5·§9.2).
    """

    if r03_comment != R03_COMMENT:
        raise ReferenceV5Error("COMMENT_NOT_CANONICAL", EXIT_MISMATCH)
    if view_comment != VIEW_COMMENT:
        raise ReferenceV5Error("COMMENT_NOT_CANONICAL", EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# owner·ACL security signature (계획 §6.5·§7.3 · 6차 계획리뷰 필수 2)
# ---------------------------------------------------------------------------

#: `pg_class`에서 owner·ACL·comment를 한 번에 읽는다. CM-2.6의 `VIEW_IDENTITY_SQL`과
#: 같은 표현을 쓴다 — 두 Task가 같은 값을 다르게 읽으면 대조가 무의미하다.
RELATION_SECURITY_SQL = (
    "SELECT c.relname AS relname, pg_get_userbyid(c.relowner) AS owner, "
    "c.relacl::text AS acl, obj_description(c.oid, 'pg_class') AS comment "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = ANY(%(names)s)"
)

#: `relacl` 항목의 grantee가 비어 있으면 `PUBLIC`이다(`=r/kosa`).
PUBLIC_GRANTEE = "PUBLIC"

#: 적용 경로. base-only는 복원할 pre-state가 없다(계획 §7.2 · 7차 계획리뷰 권장 1).
SECURITY_MODES: frozenset[str] = frozenset({"successor", "base_only"})

#: 비-final dataset을 허용해도 되는 **유일한 조합**.
#:
#: `--allow-non-final-dataset`이 공용 `apply`에도 열려 있어 189/192·null owner·138/51을
#: 전부 끈 채 marker를 만들 수 있었다(구현리뷰 11차 필수 2). e2e에서 rehearse할 때만
#: 연다 — 그 경로는 commit하지 않는다.
NON_FINAL_DATASET_TARGET = "kosa_agent_e2e"
NON_FINAL_DATASET_MODE = "rehearse"


def assert_non_final_dataset_allowed(*, database: str, mode: str) -> None:
    """비-final 허용 조합인지 본다. 아니면 flag 자체를 거부한다."""

    if database != NON_FINAL_DATASET_TARGET or mode != NON_FINAL_DATASET_MODE:
        raise ReferenceV5Error("NON_FINAL_DATASET_NOT_ALLOWED", EXIT_USAGE)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_ACLITEM = re.compile(r"^(?P<grantee>[^=]*)=(?P<privileges>[^/]*)/(?P<grantor>.*)$")


def parse_acl(acl: Any) -> tuple[tuple[str, str, str], ...]:
    """`relacl::text`를 정렬된 `(grantee, privileges, grantor)`로 바꾼다.

    raw text를 그대로 비교하면 **부여 순서만 달라도** 다른 값이 된다. PostgreSQL은
    `aclitem[]` 순서를 보장하지 않으므로 정렬해 정규화한다.
    """

    if acl is None:
        return ()
    text = str(acl).strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise ReferenceV5Error("SECURITY_ACL_UNPARSABLE", EXIT_MISMATCH)
    body = text[1:-1].strip()
    if not body:
        return ()
    entries: list[tuple[str, str, str]] = []
    for raw in body.split(","):
        match = _ACLITEM.match(raw.strip().strip('"'))
        if match is None:
            raise ReferenceV5Error("SECURITY_ACL_UNPARSABLE", EXIT_MISMATCH)
        grantee = match.group("grantee") or PUBLIC_GRANTEE
        entries.append((grantee, match.group("privileges"), match.group("grantor")))
    return tuple(sorted(entries))


def assert_no_public_grant(acl: Any) -> None:
    """`PUBLIC` 권한 0건(계획 §6.5)."""

    if any(grantee == PUBLIC_GRANTEE for grantee, _p, _g in parse_acl(acl)):
        raise ReferenceV5Error("SECURITY_PUBLIC_GRANT", EXIT_MISMATCH)


def assert_relation_security(row: Mapping[str, Any], *, mode: str) -> None:
    """한 relation의 owner·ACL이 계약을 만족하는지 본다.

    `relacl IS NULL`은 successor에서 **중단 사유**다. CM-2.6이 `v_alarm_event`에서
    실제로 만난 상태이고, 그때도 자동 보정하지 않고 `TARGET_STATE_UNSUPPORTED`로 멈춘 뒤
    사람이 GRANT를 승인했다(계획 §6.5 · CM-2.6 최종검증 §10.9).

    base-only는 격리 rehearsal·fresh bootstrap 경로라 복원할 pre-state가 없다. creator
    owner를 그대로 두고 ACL 복원을 건너뛴다(계획 §7.2).
    """

    if mode not in SECURITY_MODES:
        raise ReferenceV5Error("SECURITY_MODE_NOT_ALLOWED", EXIT_USAGE)
    owner = row.get("owner")
    if not isinstance(owner, str) or not owner:
        raise ReferenceV5Error("SECURITY_OWNER_MISSING", EXIT_MISMATCH)
    acl = row.get("acl")
    if mode == "successor" and acl is None:
        raise ReferenceV5Error("SECURITY_ACL_MISSING", EXIT_MISMATCH)
    assert_no_public_grant(acl)


def security_projection(rows: Mapping[str, Mapping[str, Any]]) -> list[list[Any]]:
    """signature에 실리는 정규 형태. **comment는 넣지 않는다.**

    comment는 canonical schema 계약이라 `schema_signature_sha256()` 쪽 관심사다. 보안
    signature에 섞으면 comment 하나만 달라도 권한이 바뀐 것처럼 보인다.
    """

    return [
        [
            name,
            str(rows[name]["owner"]),
            [list(entry) for entry in parse_acl(rows[name].get("acl"))],
        ]
        for name in sorted(rows)
    ]


def security_signature_sha256(
    rows: Mapping[str, Mapping[str, Any]], *, mode: str
) -> str:
    """owner·ACL identity. marker에 실려 no-op 판정에 쓰인다(계획 §7.3).

    **marker와 live를 비교하는 값**이지 특정 role 이름을 계약으로 박은 값이 아니다.
    그래서 공용 successor(승인 pre-state ACL)와 base-only(creator owner)가 각자
    자기 자신과 일관되면 둘 다 `V5_REFERENCE_FINAL`이 될 수 있다(8차 계획리뷰 권장 1).

    `mode`는 **전제 조건**만 가른다 — successor는 `relacl IS NULL`을 거부하고
    base-only는 허용한다. 같은 owner·ACL이면 경로와 무관하게 같은 signature다.
    """

    import json

    if set(rows) != set(OWNED_BY_CM31):
        raise ReferenceV5Error("SECURITY_RELATION_SET_MISMATCH", EXIT_MISMATCH)
    for name in rows:
        assert_relation_security(rows[name], mode=mode)
    # **`mode`는 payload에 넣지 않는다.** 경로는 전제 조건일 뿐 보안 상태가 아니다.
    # 넣으면 같은 owner·ACL을 가진 DB가 경로만 달라 다른 signature를 갖고, no-op 때
    # 어느 mode로 다시 계산할지가 정해지지 않는다(변이 T19).
    payload = {
        "migration_id": MIGRATION_ID,
        "relations": security_projection(rows),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def assert_security_signature(*, marker: Any, live: str) -> None:
    """marker와 live가 다르면 no-op으로 통과시키지 않는다(계획 §7.3)."""

    for value in (marker, live):
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ReferenceV5Error("SECURITY_SIGNATURE_MALFORMED", EXIT_MISMATCH)
    if marker != live:
        raise ReferenceV5Error("SECURITY_SIGNATURE_MISMATCH", EXIT_MISMATCH)


#: Gate 0가 확인해야 하는 **공용 target 전체.** 일부만 넘겨도 통과하면 나머지 DB의 ACL
#: 미확인을 놓친다(구현리뷰 7차 필수 2). CM-2.6 적용 순서와 같다.
PUBLIC_TARGETS: tuple[str, ...] = ("kosa_agent_e2e", "kosa_agent", "kosa_text2sql")


def assert_security_targets_agree(
    by_target: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    """세 공용 target의 owner·ACL이 서로 같은지 본다.

    다르면 공통값으로 **자동 정규화하지 않는다.** 어느 target을 기준으로 삼을지는 사람이
    정할 문제다 — CM-2.6에서 `kosa_agent_e2e`만 ACL이 없었고, 그때도 멈춘 뒤 승인을
    받았다(계획 §6.5).
    """

    if not by_target:
        raise ReferenceV5Error("SECURITY_TARGET_SET_EMPTY", EXIT_USAGE)
    if set(by_target) != set(PUBLIC_TARGETS):
        # 누락·단일 target·모르는 target은 signature를 비교하기 전에 거부한다.
        raise ReferenceV5Error("SECURITY_TARGET_SET_MISMATCH", EXIT_USAGE)
    signatures = {
        target: security_signature_sha256(rows, mode="successor")
        for target, rows in by_target.items()
    }
    if len(set(signatures.values())) != 1:
        raise ReferenceV5Error("SECURITY_TARGET_DRIFT", EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# schema 상태 판정 (계획 §4.2)
# ---------------------------------------------------------------------------

#: `V5-CM-2.6`이 공용 3 DB에 남긴 임시 호환 View의 정의 hash.
#:
#: **정적 상수로 둔다.** `postgres_transition`을 import하면 SQLAlchemy까지 끌려와
#: "DB를 열지 않는 순수 계약 모듈"이 깨진다 — V4 registry와 같은 이유다(구현리뷰 6차
#: 필수 3). 실물 대조는 회귀가 `postgres_transition.COMPAT_VIEW_SHA256`과 한다.
COMPAT_VIEW_SHA256 = "79b35b5d5a5ea1874ab7d21a79bbf04ac0282c93f840214f0196e1f24ba2a7c8"

SCHEMA_STATES: tuple[str, ...] = (
    "BASE_ONLY",
    "V4_REFERENCE_COMPAT",
    "V5_REFERENCE_FINAL",
    "PARTIAL_OR_DRIFT",
)


def classify_schema_state(
    *,
    r03_present: bool,
    view_present: bool,
    r03_columns: Sequence[Mapping[str, Any]] | None = None,
    view_definition: str | None = None,
) -> str:
    """공용 target의 pre-state를 판정한다(계획 §4.2).

    **행 수를 보지 않는다.** `V5-A-1.4`가 R03 3건을 넣어도 schema 상태는 그대로다.
    어느 상태에도 정확히 들어맞지 않으면 `PARTIAL_OR_DRIFT`다 — 자동 보정하지 않고
    쓰기 0으로 멈추기 위한 판정이라 애매한 것은 전부 drift로 본다.
    """

    if not r03_present and not view_present:
        return "BASE_ONLY"
    if not r03_present or not view_present:
        # 한쪽만 있으면 어느 계보도 아니다.
        return "PARTIAL_OR_DRIFT"
    if view_definition is None or r03_columns is None:
        return "PARTIAL_OR_DRIFT"

    names = [str(row["column_name"]) for row in r03_columns]
    digest = view_definition_sha256(view_definition)
    if digest == COMPAT_VIEW_SHA256 and _matches_v4_columns(r03_columns):
        return "V4_REFERENCE_COMPAT"
    if digest == CANONICAL_VIEW_SHA256 and names == [
        name for name, _t, _l, _n in R03_COLUMNS
    ]:
        return "V5_REFERENCE_FINAL"
    return "PARTIAL_OR_DRIFT"


def _column_tuple(row: Mapping[str, Any]) -> tuple[str, str, int | None, bool]:
    length = row.get("character_maximum_length")
    return (
        str(row["column_name"]),
        str(row["data_type"]),
        None if length is None else int(length),
        str(row["is_nullable"]).upper() == "YES",
    )


def _matches_v4_columns(rows: Sequence[Mapping[str, Any]]) -> bool:
    """구 R03 컬럼이 **이름·type·길이·nullability까지** 계약과 같은지 본다."""

    try:
        actual = tuple(_column_tuple(row) for row in rows)
    except (KeyError, TypeError, ValueError):
        return False
    return actual == V4_R03_COLUMN_CONTRACT


def assert_successor_pre_state(
    *,
    r03_columns: Sequence[Mapping[str, Any]],
    r03_constraints: Sequence[Mapping[str, Any]],
    view_definition: str,
    view_security: Mapping[str, Any],
    r03_rows: int,
    dependents: Sequence[Mapping[str, Any]],
    triggers: Sequence[Mapping[str, Any]] = (),
) -> None:
    """**파괴적 drop 앞에서** 구 형상이 예상과 정확히 같은지 본다.

    `classify_schema_state()`가 route를 고르고, 이 판정이 그 route로 정말 지워도
    되는지를 확인한다. 하나라도 다르면 lock·DROP 이전에 멈춘다(구현리뷰 9차 필수 3).
    """

    if not _matches_v4_columns(r03_columns):
        raise ReferenceV5Error("PRE_STATE_COLUMNS_MISMATCH", EXIT_CONFIRM_REQUIRED)
    observed = {
        str(row["conname"]): (
            str(row["contype"]),
            _normalize_sql(str(row["definition"])),
        )
        for row in r03_constraints
    }
    expected = {
        name: (kind, _normalize_sql(definition))
        for name, (kind, definition) in V4_R03_CONSTRAINT_DEFINITIONS.items()
    }
    if observed != expected:
        raise ReferenceV5Error("PRE_STATE_CONSTRAINTS_MISMATCH", EXIT_CONFIRM_REQUIRED)
    if view_definition_sha256(view_definition) != COMPAT_VIEW_SHA256:
        raise ReferenceV5Error("PRE_STATE_VIEW_MISMATCH", EXIT_CONFIRM_REQUIRED)
    assert_relation_security(view_security, mode="successor")
    if not isinstance(r03_rows, int) or isinstance(r03_rows, bool) or r03_rows != 0:
        # 행이 있으면 내용을 추측하지 않는다.
        raise ReferenceV5Error("PRE_STATE_NOT_EMPTY", EXIT_CONFIRM_REQUIRED)
    if list(dependents):
        # 예상 못 한 dependent가 있으면 `DROP VIEW`가 무엇을 함께 끊을지 모른다.
        raise ReferenceV5Error("PRE_STATE_DEPENDENTS_PRESENT", EXIT_CONFIRM_REQUIRED)
    if list(triggers):
        # 사용자 trigger는 `DROP TABLE`과 함께 사라진다(계획 §6.5).
        raise ReferenceV5Error("PRE_STATE_TRIGGERS_PRESENT", EXIT_CONFIRM_REQUIRED)


def assert_targets_share_one_route(routes: Mapping[str, str]) -> str:
    """공용 세 target이 **같은 route**인지 본다.

    하나만 다른 상태면 순서대로 적용하다 중간에서 멈춘다. 시작 전에 안다.
    """

    if set(routes) != set(PUBLIC_TARGETS):
        raise ReferenceV5Error("SECURITY_TARGET_SET_MISMATCH", EXIT_USAGE)
    distinct = set(routes.values())
    if len(distinct) != 1:
        raise ReferenceV5Error("TARGET_ROUTE_DRIFT", EXIT_CONFIRM_REQUIRED)
    route = distinct.pop()
    if route not in MIGRATION_ROUTES:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    return route


def assert_state_allows_apply(state: str, *, route: str) -> None:
    """route가 그 상태에 적용될 수 있는지 본다.

    base-only DB에 successor를 돌리면 없는 table을 drop하고, `V4_REFERENCE_COMPAT`에
    canonical을 돌리면 이미 있는 table을 다시 만든다. 둘 다 transaction 안에서
    실패하지만 **연결 전에** 멈추는 쪽이 낫다.
    """

    if state not in SCHEMA_STATES:
        raise ReferenceV5Error("SCHEMA_STATE_NOT_ALLOWED", EXIT_USAGE)
    if route not in MIGRATION_ROUTES:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    expected = {"BASE_ONLY": "canonical", "V4_REFERENCE_COMPAT": "successor"}.get(state)
    if expected is None:
        # `V5_REFERENCE_FINAL`은 no-op verify, `PARTIAL_OR_DRIFT`는 쓰기 0이다.
        raise ReferenceV5Error("TARGET_STATE_UNSUPPORTED", EXIT_CONFIRM_REQUIRED)
    if route != expected:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_MISMATCH)


def classify_data_phase(*, r03_rows: int, view_rows: int) -> str:
    """행 수를 **schema와 별개로** 판정한다(계획 §4.2).

    `--verify`는 두 정상 phase를 모두 통과시키고 어느 쪽인지 결과에 적는다.
    """

    for phase, (expected_r03, expected_view) in DATA_PHASES.items():
        if (r03_rows, view_rows) == (expected_r03, expected_view):
            return phase
    raise ReferenceV5Error("DATA_PHASE_UNKNOWN", EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# profile manifest projection (계획 §3.2 · 2차 계획리뷰 필수 1)
# ---------------------------------------------------------------------------

#: **V5 final stage가 등록되면** profile별 `applied_migrations`가 이 값이어야 한다.
#:
#: 실제 강제는 `manifest_v3.BOOTSTRAP_STAGE_CONTRACTS`가 한다. 이 상수는 그 등록부에
#: 무엇을 넣어야 하는지 적어 둔 **기대값 문서**이며, CM-3.1이 직접 쓰지 않는다
#: (구현리뷰 4차 필수 1·2).
PROFILE_MIGRATIONS: Mapping[str, tuple[str, ...]] = {
    "runtime": (MIGRATION_ID, "002_agent_runtime_clean"),
    "evaluation": (MIGRATION_ID,),
}
#: **현재 공용 DB의 물리 inventory 개수다.** CM-2.6이 base 9 외 변경 0으로 보존했고
#: 계획 §4.1도 "runtime 23 · evaluation 14를 유지한다"고 못 박았다. R03는 이미 이 안에
#: 있으므로 CM-3.1이 개수를 바꾸지 않는다 — 바꾸는 것은 R03의 컬럼뿐이다.
#:
#: 3차 보완에서 이 값을 "구 계보의 흔적"으로 보고 inventory를 10개로 줄였는데, 그것이
#: 소유권과 물리 inventory를 혼동한 오류였다(구현리뷰 4차 필수 2).
PROFILE_TABLE_COUNTS: Mapping[str, int] = MappingProxyType(
    {"runtime": 23, "evaluation": 14}
)

#: base 9. profile manifest의 물리 inventory에 언제나 있다.
BASE_TABLE_NAMES: tuple[str, ...] = (
    "action_history",
    "dim_parameter",
    "evaluation",
    "fdc_trace",
    "lot_history",
    "metrology",
    "summary_alarm_history",
    "summary_data",
    "trace_alarm_history",
)

#: **소유권과 물리 inventory는 다른 것이다**(구현리뷰 4차 필수 2).
#:
#: CM-3.1이 만들거나 교체하는 것은 R03 table과 View뿐이다. 그러나 profile manifest는
#: `verify_bootstrap_state`가 `actual_tables != expected_tables`로 대조하는 **물리
#: inventory**라, CM-2.6이 보존한 Runtime·RAG·D·legacy handoff table을 전부
#: 담아야 한다. 계획도 "runtime 23 · evaluation 14를 **유지**한다"(§4.1)고 못 박았다.
#:
#: 3차 보완에서 이 둘을 같게 보고 inventory를 10개로 줄였는데, 그건 계획을 어기고
#: verifier와도 충돌한다. 아래는 **소유권 표시용 문서 상수**다.
#: inventory 필터가 아니다.
OWNED_BY_CM31: tuple[str, ...] = (R03_TABLE, ALARM_VIEW)
OWNED_BY_OTHER_TASKS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "V5-B-1.1": ("document", "document_chunk"),
        "V5-D-2.4": ("nl_query_log",),
        "V5-CM-3.2": (
            "action_delivery",
            "agent_prediction",
            "agent_prediction_review",
            "agent_run",
            "agent_run_action",
            "agent_run_alarm",
            "agent_tool_call",
            "approval_request",
            "audit_log",
        ),
    }
)

#: 최종 계보가 **채택하지 않는** table. 그러나 지금 공용 DB에 있고 CM-2.6이 legacy
#: handoff로 보존했다. **채택하지 않는 것과 inventory에서 숨기는 것은 다르다** —
#: 제거·격리 Task가 정해지기 전까지 manifest는 존재를 그대로 기록한다.
NOT_ADOPTED_TABLES: tuple[str, ...] = ("document_corpus",)

#: **데이터 정본은 최종 `project.zip`뿐이다.** 폐기된 `kosa_0813` epoch과 그 archive를
#: 근거로 쓰지 않는다(CLAUDE.md 최상단 규칙 · 계획 §4.1).
#: 값은 `infra/bootstrap/source-manifest-v4.json`(format v4)에서 왔다.
FINAL_DATASET_EPOCH = "fdc_final_20260818"
FINAL_SOURCE_ARCHIVE_SHA256 = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)

#: 폐기 계보의 보정 revision 접두어(`corrected-base-v1` 등). 최종 계보는 이 접두어를
#: 쓰지 않는다.
DEPRECATED_CORRECTION_PREFIX = "corrected-"

#: final base 9 행 수. `V5-CM-2.6`이 공용 3 DB에 실제로 적용한 값이다.
#: **merge 후 `postgres_transition` 상수와 교차 검증한다**(그 모듈은 아직 main에 없다).
FINAL_BASE_ROWS: Mapping[str, int] = MappingProxyType(
    {
        "dim_parameter": 8,
        "lot_history": 600,
        "fdc_trace": 14400,
        "summary_data": 4800,
        "evaluation": 4800,
        "metrology": 48,
        "trace_alarm_history": 138,
        "summary_alarm_history": 51,
    }
)
FINAL_ACTION_ROWS: Mapping[str, int] = MappingProxyType(
    {"runtime": 0, "evaluation": 12}
)


def assert_final_table_metadata(tables: Mapping[str, Any], *, profile: str) -> None:
    """base 9 행 수가 최종 정본인지 본다.

    envelope만 final로 바꾸면 TRACE 126·SUMMARY 47·evaluation action 48이 그대로 남는다.
    최종은 TRACE **138** · SUMMARY **51** · action runtime `0`/evaluation `12`다
    (구현리뷰 3차 필수 1).
    """

    if profile not in FINAL_ACTION_ROWS:
        raise ReferenceV5Error("PROFILE_NOT_ALLOWED", EXIT_USAGE)
    expected = {**FINAL_BASE_ROWS, "action_history": FINAL_ACTION_ROWS[profile]}
    for name, rows in expected.items():
        entry = tables.get(name)
        if not isinstance(entry, Mapping) or entry.get("row_count") != rows:
            raise ReferenceV5Error("MANIFEST_CONTENT_NOT_FINAL", EXIT_MISMATCH)
    r03 = tables.get(R03_TABLE)
    if not isinstance(r03, Mapping) or r03.get("row_count") != 0:
        # CM-3.1 직후 R03는 0행이다. 3건은 `V5-A-1.4`가 넣는다.
        raise ReferenceV5Error("MANIFEST_CONTENT_NOT_FINAL", EXIT_MISMATCH)


def assert_registered_manifest_contract(
    manifest: Mapping[str, Any], *, profile: str, stage: str
) -> None:
    """**기존 `manifest_v3` 검증기를 그대로 쓴다.** 따로 만들지 않는다.

    3차까지 top-level key·secret scan·entry 계약을 직접 구현했는데,
    `manifest_v3.validate_manifest_schema()`가 이미 extra-forbid·secret scan·
    column 계약·policy allowlist·`content_hash`·stage 전이를 본다. 재구현이 더 약했다
    (구현리뷰 4차 필수 1) — Windows drive·UNC·`file://`도 기존 scan만 잡는다.

    검증 대상은 **지금 등록부에 있는 manifest**다. 최종 epoch 계약은
    `assert_final_epoch_contract()`가 따로 본다. 둘을 한 함수에 넣었더니 어떤 입력도
    통과할 수 없는 죽은 gate가 됐다 — 이유는 `final_manifest_blockers()`에 있다.
    """

    import manifest_v3

    if profile not in PROFILE_MIGRATIONS:
        raise ReferenceV5Error("PROFILE_NOT_ALLOWED", EXIT_USAGE)
    try:
        manifest_v3.validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile=profile,
            expected_stage=stage,
        )
    except ReferenceV5Error:
        raise
    except Exception as exc:  # noqa: BLE001 - 외부 검증기의 예외 계층을 흡수한다
        raise ReferenceV5Error("MANIFEST_CONTRACT_MISMATCH", EXIT_MISMATCH) from exc


def assert_final_epoch_contract(manifest: Mapping[str, Any], *, profile: str) -> None:
    """최종 `project.zip` 계보인지 본다.

    envelope만 final로 바꾸면 TRACE 126 · SUMMARY 47 · evaluation action 48이 그대로
    남는다(구현리뷰 3차 필수 1). 그래서 epoch·archive·보정 revision과 실제 행 수를 함께
    본다.

    **이 계약을 통과하는 manifest는 아직 `manifest_v3` 등록부에 올릴 수 없다.**
    막는 것이 무엇인지는 `final_manifest_blockers()`가 코드로 답한다.
    """

    # profile allowlist를 여기서 또 보지 않는다. 아래 `assert_final_table_metadata()`가
    # 같은 reason code로 거부하므로, 두 번 보면 어느 쪽을 지워도 결과가 같아진다
    # (변이 Q30). 두 map의 key가 같다는 것은 회귀가 고정한다.
    if manifest.get("dataset_epoch") != FINAL_DATASET_EPOCH:
        raise ReferenceV5Error("MANIFEST_EPOCH_NOT_FINAL", EXIT_MISMATCH)
    if manifest.get("source_archive_sha256") != FINAL_SOURCE_ARCHIVE_SHA256:
        raise ReferenceV5Error("MANIFEST_ARCHIVE_NOT_FINAL", EXIT_MISMATCH)
    correction = manifest.get("correction_version")
    if not isinstance(correction, str) or not correction:
        raise ReferenceV5Error("MANIFEST_CORRECTION_MISSING", EXIT_MISMATCH)
    if correction.startswith(DEPRECATED_CORRECTION_PREFIX):
        raise ReferenceV5Error("MANIFEST_CORRECTION_DEPRECATED", EXIT_MISMATCH)
    tables = manifest.get("tables")
    if not isinstance(tables, Mapping):
        raise ReferenceV5Error("MANIFEST_CONTRACT_MISMATCH", EXIT_MISMATCH)
    assert_final_table_metadata(tables, profile=profile)


#: `V5-CM-3.1`이 최종 profile manifest를 발급하지 못하게 막는 것들.
#:
#: 구현리뷰 Gate 2가 "predecessor Task가 정해지지 않았다"고 지적했다. 그 Task가 **무엇을
#: 고쳐야 하는지**를 산문 대신 코드로 남긴다. 아래 함수가 빈 tuple을 돌려주는 순간
#: 등록이 가능해지고, 그때 회귀 하나가 실패하며 계획·코드·fixture를 함께 고치게 된다.
GATE2_BLOCKERS: tuple[str, ...] = (
    "MANIFEST_V3_EPOCH_IS_DEPRECATED",
    "EVALUATION_MOCK_PINS_48_ACTION_ROWS",
    "NO_STAGE_REGISTERS_THE_FINAL_MIGRATION",
    "V4_R03_TYPE_REGISTRY_STILL_ACTIVE",
    "TEXT2SQL_COLUMN_ALLOWLIST_IS_V4",
)

#: 위 5종 중 **런타임에 재지 않고 상수로 두는 것**(계획 §7.5-7).
#:
#: 이 둘을 실측하려면 동결 V4 모듈이나 Text2SQL validator를 import해야 하는데, 그러면
#: `db_target`→SQLAlchemy·sqlglot까지 끌려와 "DB를 열지 않는 순수 계약 모듈"이 깨진다
#: (구현리뷰 6차 필수 3). 실물 대조는 **테스트에서만** 한다.
#:
#: 이 상수 2종의 제거와 짝 회귀 전환은 `V5-CM-1.8` 범위다(WBS `V5-CM-1.8`).
STATIC_BLOCKERS: tuple[str, ...] = (
    "V4_R03_TYPE_REGISTRY_STILL_ACTIVE",
    "TEXT2SQL_COLUMN_ALLOWLIST_IS_V4",
)

#: 정적 blocker가 가리키는 실물. **테스트가 이 값을 실측과 대조한다.**
V4_R03_TYPE_REGISTRY_COLUMNS = 11
TEXT2SQL_ALLOWLIST_MANIFEST = "runtime.runtime_clean.json"


def final_manifest_blockers() -> tuple[str, ...]:
    """지금 `manifest_v3` 상태에서 최종 manifest 등록을 막는 이유를 센다."""

    import manifest_v3

    blockers: list[str] = []
    if manifest_v3.DATASET_EPOCH != FINAL_DATASET_EPOCH:
        # `_validate_common_envelope`가 `dataset_epoch`를 상수와 exact 비교한다.
        # 최종 epoch을 담은 manifest는 예외 없이 거부된다.
        blockers.append("MANIFEST_V3_EPOCH_IS_DEPRECATED")
    contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS.get(
        ("evaluation", "evaluation_mock")
    )
    if contract is not None and FINAL_ACTION_ROWS["evaluation"] != 48:
        # `_validate_db_tables`가 이 stage의 `action_history`를 MOCK·48행으로 못 박는다.
        # 최종 evaluation은 실제 12행이라 같은 stage로 표현할 수 없다.
        blockers.append("EVALUATION_MOCK_PINS_48_ACTION_ROWS")
    if not any(
        MIGRATION_ID in stage_contract.applied_migrations
        for stage_contract in manifest_v3.BOOTSTRAP_STAGE_CONTRACTS.values()
    ):
        blockers.append("NO_STAGE_REGISTERS_THE_FINAL_MIGRATION")
    # 정적 2종은 여기서 재지 않는다 — `STATIC_BLOCKERS` 주석 참고.
    blockers.extend(STATIC_BLOCKERS)
    return tuple(blockers)


#: **CM-3.1이 만드는 것은 profile manifest가 아니다.**
#:
#: 4차에서 task-scoped를 골라놓고 실제로는 구 `db_bootstrap` manifest를 그대로 두고
#: R03 컬럼만 바꿔 돌려줬다. 그 결과물은 기존 등록 validator를 **통과해버려서** 경계가
#: 없었다(구현리뷰 5차 필수 1). 그래서 별도 artifact로 분리한다.
#:
#: 이 `artifact_type`은 `manifest_v3.ARTIFACT_TYPES`에 **없다.** 그러므로
#: `validate_manifest_schema()`는 어떤 `expected_artifact_type`으로도 받지 않고,
#: `resolve_bootstrap_manifest_path()`가 가리키는 등록 디렉터리에도 들어가지 않는다.
MIGRATION_CONTRACT_TYPE = "v5_migration_contract"
MIGRATION_CONTRACT_FORMAT_VERSION = 1

#: artifact의 exact key. extra-forbid로 판정한다.
MIGRATION_CONTRACT_KEYS: frozenset[str] = frozenset(
    {
        "format_version",
        "artifact_type",
        "task_id",
        "migration_id",
        "dataset_epoch",
        "source_archive_sha256",
        "migration_bundle_sha256",
        "canonical_view_sha256",
        "owned_objects",
        "r03_columns",
        "supersedes_r03_columns",
        "supersedes_manifest",
        "profile_inventory_counts",
        "registration_blockers",
    }
)

#: artifact가 대체한다고 주장할 수 있는 **정확한 profile↔stage 쌍**.
#:
#: profile allowlist만 보면 `runtime`+`evaluation_mock` 같은 cross-profile 계보나
#: `postgresql://…` 같은 자유 문자열이 그대로 통과한다(구현리뷰 6차 필수 1).
#: 자유 문자열 자체를 없앤다. 이 표가 `manifest_v3.BOOTSTRAP_STAGE_CONTRACTS`의 실제
#: 등록 조합과 일치한다는 것은 회귀가 고정한다.
SUPERSEDED_STAGE_BY_PROFILE: Mapping[str, str] = MappingProxyType(
    {"runtime": "runtime_clean", "evaluation": "evaluation_mock"}
)

TASK_ID = "V5-CM-3.1"

#: 구 계보(V4)의 R03 11컬럼. artifact가 "무엇을 대체하는지" 기록하는 데만 쓴다.
#: 구 001이 만든 R03의 **컬럼 이름·type·길이·nullability**.
#:
#: 이름 목록만 보면 모든 컬럼을 `integer NULL`로 바꾼 catalog도
#: `V4_REFERENCE_COMPAT`으로 판정된다 — 그 상태에서 `DROP TABLE`이 돈다
#: (구현리뷰 9차 필수 3). 파괴적 작업
#: 앞이므로 exact로 본다. 값은 `backend/migrations/001_reference_extensions.sql`이며
#: 회귀가 그 파일과 대조한다.
V4_R03_COLUMN_CONTRACT: tuple[tuple[str, str, int | None, bool], ...] = (
    ("alarm_id", "character varying", 24, False),
    ("occurred_at", "timestamp without time zone", None, False),
    ("lot_hist_id", "character varying", 20, False),
    ("lot_id", "character varying", 20, False),
    ("equipment_id", "character varying", 20, False),
    ("chamber_id", "character varying", 24, False),
    ("parameter_id", "character varying", 20, False),
    ("recipe_step_no", "smallint", None, False),
    ("trigger_wafer_no", "smallint", None, False),
    ("member_refs", "jsonb", None, False),
    ("policy_version", "character varying", 20, False),
)

#: 구 001 R03의 constraint type 개수. PK 1 · unique 1 · FK 2 · CHECK 3.
V4_R03_CONSTRAINT_COUNTS: Mapping[str, int] = MappingProxyType(
    {"p": 1, "u": 1, "f": 2, "c": 3}
)

#: 구 001 R03 constraint의 **정규화 definition**.
#:
#: 개수만 보면 PK·FK·UNIQUE·CHECK를 전부 `CHECK (false)`로 바꾼 catalog도 exact V4로
#: 취급해 drop한다(구현리뷰 10차 필수 3). 값은 `001_reference_extensions.sql`에서 왔고
#: 회귀가 격리 PostgreSQL 실측과 대조한다.
V4_R03_CONSTRAINT_DEFINITIONS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "r03_alarm_history_pkey": ("p", "PRIMARY KEY (alarm_id)"),
        "r03_alarm_history_lot_hist_id_parameter_id_recipe_step_no_p_key": (
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
    }
)

#: R03·View에 걸린 **사용자 trigger**. 계획 §6.5는 있으면 중단하라고 한다 —
#: `DROP TABLE`이 함께 지워버린다(구현리뷰 10차 필수 3).
TRIGGERS_SQL = (
    "SELECT c.relname AS relname, t.tgname AS tgname FROM pg_trigger t "
    "JOIN pg_class c ON c.oid = t.tgrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE NOT t.tgisinternal AND n.nspname = 'public' "
    "AND c.relname = ANY(%(names)s)"
)

V4_R03_COLUMNS: list[str] = [
    "alarm_id",
    "occurred_at",
    "lot_hist_id",
    "lot_id",
    "equipment_id",
    "chamber_id",
    "parameter_id",
    "recipe_step_no",
    "trigger_wafer_no",
    "member_refs",
    "policy_version",
]


def build_migration_contract(
    manifest: Mapping[str, Any], *, profile: str, stage: str
) -> dict[str, Any]:
    """등록 manifest를 **읽어서** task-scoped migration contract artifact를 만든다.

    읽기만 한다. 결과는 `db_bootstrap` manifest가 아니고, 등록 디렉터리에도 들어가지
    않는다. "최종 stage가 등록될 때 R03 컬럼이 무엇이어야 하는지"를 실행 가능한 형태로
    고정하는 것이 전부다(구현리뷰 5차 필수 1 — 1안 task-scoped 유지).

    입력 검증은 기존 `manifest_v3` 검증기에 위임한다(4차 필수 1).
    """

    assert_registered_manifest_contract(manifest, profile=profile, stage=stage)
    tables = manifest["tables"]
    if R03_TABLE not in tables:
        raise ReferenceV5Error("MANIFEST_CONTRACT_MISMATCH", EXIT_MISMATCH)

    entry = tables[R03_TABLE]
    if not isinstance(entry, Mapping):
        raise ReferenceV5Error("MANIFEST_CONTRACT_MISMATCH", EXIT_MISMATCH)
    columns = list(entry.get("columns") or ())
    final_columns = [name for name, _type, _len, _null in R03_COLUMNS]
    if columns not in (V4_R03_COLUMNS, final_columns):
        # V4 11컬럼 source이거나 이미 final 12컬럼(no-op)일 때만 받는다.
        raise ReferenceV5Error("MANIFEST_CONTRACT_MISMATCH", EXIT_MISMATCH)

    artifact = {
        "format_version": MIGRATION_CONTRACT_FORMAT_VERSION,
        "artifact_type": MIGRATION_CONTRACT_TYPE,
        "task_id": TASK_ID,
        "migration_id": MIGRATION_ID,
        "dataset_epoch": FINAL_DATASET_EPOCH,
        "source_archive_sha256": FINAL_SOURCE_ARCHIVE_SHA256,
        # **어느 SQL bundle을 가리키는지**가 이 artifact의 provenance다. 같은
        # `migration_id`로 DDL이 바뀐 SQL이 와도 여기서 갈린다(구현리뷰 6차 필수 2).
        "migration_bundle_sha256": migration_bundle_sha256(),
        "canonical_view_sha256": CANONICAL_VIEW_SHA256,
        "owned_objects": {"tables": [R03_TABLE], "views": [ALARM_VIEW]},
        "r03_columns": final_columns,
        "supersedes_r03_columns": list(columns),
        # **등록본의 신원만 적는다.** 내용을 복사하지 않는다 — 복사하면 그 사본이 다시
        # profile manifest 행세를 하게 된다.
        "supersedes_manifest": {"profile": profile, "bootstrap_stage": stage},
        "profile_inventory_counts": dict(PROFILE_TABLE_COUNTS),
        "registration_blockers": list(final_manifest_blockers()),
    }
    # **결과를 여기서 판정한다.** caller가 별도 helper를 불러야만 안전한 구조로 두지
    # 않는다(1차 필수 4 · 3차 필수 1).
    assert_migration_contract(artifact)
    return artifact


def assert_migration_contract(artifact: Mapping[str, Any]) -> None:
    """artifact가 task-scoped 계약을 실제로 담았는지 본다.

    `build_migration_contract()`가 내부에서 부르지만 **공개 함수로 둔다.** 안에서만
    부르면 어떤 입력으로도 단독으로 깨뜨릴 수 없어 죽은 방어가 된다(변이 P13·P14).
    """

    if set(artifact) != MIGRATION_CONTRACT_KEYS:
        raise ReferenceV5Error("CONTRACT_SCHEMA_MISMATCH", EXIT_MISMATCH)
    # **민감 값 검사를 먼저 한다.** 뒤의 exact 대조가 어차피 거부하더라도, DSN·자격증명·
    # 절대경로가 들어온 사실은 다른 reason으로 드러나야 한다(구현리뷰 6차 필수 1).
    # 검사기는 기존 `manifest_v3` 것을 그대로 쓴다 — 재구현은 4차 필수 1에서 닫혔다.
    assert_no_sensitive_values(artifact)
    if artifact["format_version"] != MIGRATION_CONTRACT_FORMAT_VERSION:
        raise ReferenceV5Error("CONTRACT_SCHEMA_MISMATCH", EXIT_MISMATCH)
    if artifact["artifact_type"] != MIGRATION_CONTRACT_TYPE:
        raise ReferenceV5Error("CONTRACT_TYPE_MISMATCH", EXIT_MISMATCH)
    if artifact["task_id"] != TASK_ID or artifact["migration_id"] != MIGRATION_ID:
        raise ReferenceV5Error("CONTRACT_IDENTITY_MISMATCH", EXIT_MISMATCH)
    if artifact["dataset_epoch"] != FINAL_DATASET_EPOCH:
        raise ReferenceV5Error("MANIFEST_EPOCH_NOT_FINAL", EXIT_MISMATCH)
    if artifact["source_archive_sha256"] != FINAL_SOURCE_ARCHIVE_SHA256:
        raise ReferenceV5Error("MANIFEST_ARCHIVE_NOT_FINAL", EXIT_MISMATCH)
    # 실물 SQL 2종으로 **다시 계산해** 대조한다. 기록만 하면 stale artifact를 못 잡는다.
    if artifact["migration_bundle_sha256"] != migration_bundle_sha256():
        raise ReferenceV5Error("MIGRATION_BUNDLE_STALE", EXIT_MISMATCH)
    if artifact["canonical_view_sha256"] != CANONICAL_VIEW_SHA256:
        raise ReferenceV5Error("VIEW_IDENTITY_MISMATCH", EXIT_MISMATCH)
    if artifact["owned_objects"] != {"tables": [R03_TABLE], "views": [ALARM_VIEW]}:
        raise ReferenceV5Error("CONTRACT_OWNERSHIP_MISMATCH", EXIT_MISMATCH)
    if artifact["r03_columns"] != [name for name, _t, _l, _n in R03_COLUMNS]:
        raise ReferenceV5Error("CONTRACT_COLUMNS_MISMATCH", EXIT_MISMATCH)
    if artifact["supersedes_r03_columns"] not in (
        V4_R03_COLUMNS,
        artifact["r03_columns"],
    ):
        raise ReferenceV5Error("CONTRACT_LINEAGE_MISMATCH", EXIT_MISMATCH)
    superseded = artifact["supersedes_manifest"]
    if not isinstance(superseded, Mapping) or set(superseded) != {
        "profile",
        "bootstrap_stage",
    }:
        raise ReferenceV5Error("CONTRACT_SCHEMA_MISMATCH", EXIT_MISMATCH)
    assert_superseded_pair(superseded)
    if artifact["profile_inventory_counts"] != dict(PROFILE_TABLE_COUNTS):
        raise ReferenceV5Error("CONTRACT_INVENTORY_MISMATCH", EXIT_MISMATCH)
    if list(artifact["registration_blockers"]) != list(final_manifest_blockers()):
        # blocker가 닫히면 이 artifact도 다시 만들어야 한다.
        raise ReferenceV5Error("CONTRACT_BLOCKERS_STALE", EXIT_MISMATCH)
    assert_not_a_bootstrap_manifest(artifact)


def assert_no_sensitive_values(artifact: Mapping[str, Any]) -> None:
    """DSN·자격증명·절대경로가 artifact에 담기지 않았는지 본다.

    `manifest_v3.scan_for_sensitive_values()`를 그대로 쓴다. Windows drive·UNC·`file://`
    까지 그 검사기만 다룬다(구현리뷰 4차 필수 1).
    """

    import manifest_v3

    try:
        manifest_v3.scan_for_sensitive_values(artifact)
    except ReferenceV5Error:
        raise
    except Exception as exc:  # noqa: BLE001 - 외부 검증기의 예외 계층을 흡수한다
        raise ReferenceV5Error("CONTRACT_SENSITIVE_VALUE", EXIT_MISMATCH) from exc


def assert_superseded_pair(superseded: Mapping[str, Any]) -> None:
    """대체 대상이 **실제 등록된 profile↔stage 쌍**인지 본다.

    cross-profile 계보와 등록되지 않은 stage를 서로 다른 reason으로 가른다
    (구현리뷰 6차 필수 1).
    """

    profile = superseded.get("profile")
    stage = superseded.get("bootstrap_stage")
    if profile not in SUPERSEDED_STAGE_BY_PROFILE:
        raise ReferenceV5Error("PROFILE_NOT_ALLOWED", EXIT_USAGE)
    if stage not in set(SUPERSEDED_STAGE_BY_PROFILE.values()):
        raise ReferenceV5Error("CONTRACT_STAGE_NOT_REGISTERED", EXIT_MISMATCH)
    if stage != SUPERSEDED_STAGE_BY_PROFILE[profile]:
        raise ReferenceV5Error("CONTRACT_CROSS_PROFILE_LINEAGE", EXIT_MISMATCH)


def assert_not_a_bootstrap_manifest(artifact: Mapping[str, Any]) -> None:
    """**등록 artifact로 오인될 수 없어야 한다**(구현리뷰 5차 필수 1).

    `db_bootstrap` envelope key를 하나라도 갖고 있으면 경계가 무너진다. 특히 `tables`를
    담는 순간 그 사본이 다시 profile manifest 행세를 한다.
    """

    import manifest_v3

    if artifact.get("artifact_type") in manifest_v3.ARTIFACT_TYPES:
        raise ReferenceV5Error("CONTRACT_TYPE_MISMATCH", EXIT_MISMATCH)
    leaked = set(artifact) & {
        "tables",
        "profile",
        "bootstrap_stage",
        "schema_stage",
        "applied_migrations",
        "applies_to",
        "correction_version",
        "hash_algorithm",
        "value_normalization_version",
    }
    if leaked:
        raise ReferenceV5Error("CONTRACT_BOUNDARY_LEAK", EXIT_MISMATCH)


def assert_outside_bootstrap_registry(path: Path) -> None:
    """artifact를 등록 manifest 경로에 쓰지 못하게 한다.

    `resolve_bootstrap_manifest_path()`가 읽는 디렉터리에 들어가면 full verifier가
    이 artifact를 profile manifest로 집어들 수 있다.
    """

    import manifest_v3

    # 등록 파일 하나하나를 따로 보지 않는다. `BOOTSTRAP_MANIFEST_REGISTRY`의 값은 전부
    # `MANIFEST_REGISTRY_ROOT` 아래에 있으므로 아래 한 검사가 그것들을 모두 덮는다.
    # 두 번 보면 어느 쪽을 지워도 결과가 같아진다(변이 R36). 그 전제는 회귀가 고정한다.
    resolved = path.resolve()
    registry_root = manifest_v3.MANIFEST_REGISTRY_ROOT.resolve()
    if resolved == registry_root or registry_root in resolved.parents:
        raise ReferenceV5Error("CONTRACT_BOUNDARY_LEAK", EXIT_USAGE)


# ---------------------------------------------------------------------------
# runner 계약 — 묶음 2 (계획 §7.1·§7.2·§7.3)
#
# **여기 있는 것은 전부 순수 판정이다.** DB driver·SQLAlchemy를 module scope에서
# import하지 않는다. 실제 session을 여는 코드는 아래 `apply_to_target()`이 함수 안에서만
# import한다(구현리뷰 6차 필수 3의 연장).
# ---------------------------------------------------------------------------

#: database → profile. CM-2.6 `TARGET_PROFILE`과 같아야 한다. 정적 상수로 두고 회귀가
#: 대조한다 — `postgres_transition`을 import하면 SQLAlchemy가 딸려온다.
TARGET_PROFILE: Mapping[str, str] = MappingProxyType(
    {
        "kosa_agent_e2e": "runtime",
        "kosa_agent": "runtime",
        "kosa_text2sql": "evaluation",
    }
)

#: **CM-2.6과 같은 namespace를 쓴다.**
#:
#: 다른 값을 쓰면 두 Task의 runner가 같은 DB에서 동시에 돌 수 있다. CM-3.1 완료 뒤
#: CM-2.6은 `TARGET_STATE_UNSUPPORTED`로 멈추지만(계획 §7.4), 그 판정도 결국 DB를 읽은
#: 뒤의 일이다. lock을 공유하면 애초에 겹치지 않는다.
ADVISORY_LOCK_NAMESPACE = 0x5643_4D32  # "VCM2"
#: **CM-2.6과 bind 문법이 다르다.** 그쪽은 SQLAlchemy `:name`, 여기는 이 모듈의
#: `execute(sql, params)` 계약에 맞춘 psycopg `%(name)s`다. namespace·key 값은 같다.
#: **기다리지 않는다.** blocking `pg_advisory_lock()`은 다른 session이 잡고 있으면
#: 무제한 대기한다 — 계획 §10의 "lock 실패 → `TARGET_BUSY`, 쓰기 0"이 성립하지 않는다
#: (구현리뷰 12차 필수 2).
ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_lock(%(namespace)s, %(key)s)"
ADVISORY_UNLOCK_SQL = "SELECT pg_advisory_unlock(%(namespace)s, %(key)s)"


def advisory_lock_key(database: str) -> int:
    """database 이름 → 32bit advisory lock key. CM-2.6과 같은 계산이다."""

    if database not in TARGET_PROFILE:
        raise ReferenceV5Error("TARGET_NOT_ALLOWED", EXIT_USAGE)
    digest = hashlib.sha256(database.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF


ISOLATION_SQL = "SHOW transaction_isolation"
LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s'"
STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '600s'"
IDLE_TIMEOUT_SQL = "SET LOCAL idle_in_transaction_session_timeout = '60s'"

#: transaction 진입 직후 순서. isolation 확인이 먼저다 — timeout을 걸어놓고 isolation이
#: 기대와 다르면 이미 잘못된 transaction 안이다.
SESSION_PROLOGUE: tuple[str, ...] = (
    ISOLATION_SQL,
    LOCK_TIMEOUT_SQL,
    STATEMENT_TIMEOUT_SQL,
    IDLE_TIMEOUT_SQL,
)
EXPECTED_ISOLATION = "read committed"

CLI_MODES: tuple[str, ...] = (
    "preflight",
    "rehearse",
    "apply",
    "verify",
    "promote-receipt",
    "recover-marker",
)


def assert_single_mode(selected: Sequence[str]) -> str:
    """mode는 정확히 하나다.

    두 개를 함께 주면 어느 쪽이 이겼는지 모른 채 DB를 연다. **engine 생성 전에** 끊는다
    (계획 §9.1).
    """

    unknown = [mode for mode in selected if mode not in CLI_MODES]
    if unknown:
        raise ReferenceV5Error("CLI_MODE_NOT_ALLOWED", EXIT_USAGE)
    if len(selected) != 1:
        raise ReferenceV5Error("CLI_MODE_CONFLICT", EXIT_USAGE)
    return selected[0]


def assert_target_allowed(database: str, *, confirm_target: str | None) -> str:
    """대상 DB가 allowlist에 있고 `--confirm-target`과 같은지 본다."""

    if database not in TARGET_PROFILE:
        raise ReferenceV5Error("TARGET_NOT_ALLOWED", EXIT_USAGE)
    if confirm_target != database:
        raise ReferenceV5Error("TARGET_CONFIRM_REQUIRED", EXIT_CONFIRM_REQUIRED)
    return TARGET_PROFILE[database]


#: role·table 이름 형태. **DB에서 읽거나 호출자가 준 값을 SQL에 넣기 전에 본다.**
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _quote_identifier(name: str) -> str:
    if _SAFE_IDENTIFIER.fullmatch(name) is None:
        raise ReferenceV5Error("SECURITY_IDENTIFIER_UNSAFE", EXIT_MISMATCH)
    return f'"{name}"'


#: **구 epoch(`kosa_0813`) 잔재다.** 최종 epoch `fdc_final_20260818`에는 없다.
#:
#: `V5-B-1.1`이 **"채택하지 않는다"** 고 했고 `V5-CM-2.6`이 보존만 한 뒤 B가 정리한다
#: (`postgres_transition.LEGACY_HANDOFF_TABLES_BY_TARGET`).
#:
#: 등록 manifest에는 남아 있지만 **보존 대상이 아니다.** 근거 셋이다.
#:
#: 1. `backend/app/` 전체에 참조가 0건이다.
#: 2. B의 `apply_rag_schema.py`는 이 table이 남아 있으면 `RagSchemaError`를 던진다 —
#:    **없는 상태가 정상**이다.
#: 3. 공용 runtime 2 DB에는 실재하지 않고 `kosa_text2sql`에만 0행 껍데기로 남았다.
#:
#: 등록 inventory에서 기계적으로 유도하면 runner가 없는 table을 읽어 `UndefinedTable`로
#: 죽고, B가 `kosa_text2sql`에서 정리하는 순간 evaluation 쪽도 같이 깨진다
#: (Gate 0 조사 §3).
LEGACY_HANDOFF_TABLES: frozenset[str] = frozenset({"document_corpus"})


#: profile별 **보존 table exact allowlist.** 등록 manifest의 물리 inventory에서 base 9와
#: CM-3.1 소유 R03, 그리고 위 legacy handoff를 뺀 나머지다
#: (runtime 23-10-1=12 · evaluation 14-10-1=3).
#:
#: > **구·신 데이터 경계.** 유도 근거인 등록 manifest 3종은 전부 폐기 epoch
#: > `kosa_0813`이다(`V5-CM-1.8`이 최종 epoch로 재발급한다). 그래서 여기서는 그것을
#: > **table 이름의 물리 inventory로만** 쓴다 — epoch·행 수·hash·정정 계보는 어느 것도
#: > 근거로 쓰지 않는다. 그 경계가 흐려진 결과가 `document_corpus`였다: 구 epoch에만
#: > 있던 table을 보존 대상으로 잡아 신 epoch 공용 DB에서 `UndefinedTable`로 죽었다
#: > (Gate 0 조사 §3). 목록은 하드코딩이고 module은 manifest 파일을 읽지 않는다.
#: > 대조는 테스트가 한다.
#:
#: 호출자가 준 이름을 그대로 SQL에 넣으면 `x IN SHARE MODE; DROP TABLE lot_history; --`
#: 같은 값이 statement가 된다(구현리뷰 9차 필수 2). allowlist 밖은 아예 받지 않는다.
PRESERVED_TABLES_BY_PROFILE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "runtime": (
            "action_delivery",
            "agent_prediction",
            "agent_prediction_review",
            "agent_run",
            "agent_run_action",
            "agent_run_alarm",
            "agent_tool_call",
            "approval_request",
            "audit_log",
            "document",
            "document_chunk",
            "nl_query_log",
        ),
        "evaluation": (
            "document",
            "document_chunk",
            "nl_query_log",
        ),
    }
)


def locked_tables(profile: str) -> tuple[str, ...]:
    """`SHARE`로 잡는 table 전체. base 9 + profile 보존분이며 **R03는 없다.**"""

    if profile not in PRESERVED_TABLES_BY_PROFILE:
        raise ReferenceV5Error("PROFILE_NOT_ALLOWED", EXIT_USAGE)
    names = {*BASE_TABLE_NAMES, *PRESERVED_TABLES_BY_PROFILE[profile]}
    if OWNED_BY_CM31[0] in names or OWNED_BY_CM31[1] in names:
        raise ReferenceV5Error("SECURITY_RELATION_SET_MISMATCH", EXIT_MISMATCH)
    return tuple(sorted(names))


#: lock 경쟁에 해당하는 PostgreSQL SQLSTATE.
#:
#: `55P03` lock_not_available(=`NOWAIT`·`lock_timeout`), `40P01` deadlock_detected.
#: **이것만 `TARGET_BUSY`다.** 모든 예외를 잠금 경쟁으로 바꾸면 권한 없음·table 누락·
#: 연결 종료·SQL 오류가 전부 재시도 대상으로 위장된다(구현리뷰 13차 필수 1).
LOCK_CONTENTION_SQLSTATES: frozenset[str] = frozenset({"55P03", "40P01"})


def _sqlstate(error: BaseException) -> str | None:
    """driver 예외에서 SQLSTATE를 꺼낸다. SQLAlchemy는 `.orig`로 감싼다."""

    for candidate in (error, getattr(error, "orig", None), error.__cause__):
        if candidate is None:
            continue
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str) and value:
                return value
    return None


def is_lock_contention(error: BaseException) -> bool:
    return _sqlstate(error) in LOCK_CONTENTION_SQLSTATES


def share_lock_statements(profile: str, *, nowait: bool = False) -> tuple[str, ...]:
    """읽기만 하는 table을 이름 순으로 `SHARE`한다.

    View 정의가 base 9를 읽으므로 transaction 동안 형상이 바뀌면 안 된다. 순서를
    이름으로 고정하는 것은 deadlock을 피하려는 것이다 — 두 runner가 다른 순서로 잡으면
    서로 기다린다.

    이름은 allowlist에서만 오고, 그래도 quote를 거친다(구현리뷰 9차 필수 2).
    """

    suffix = " NOWAIT" if nowait else ""
    return tuple(
        f"LOCK TABLE public.{_quote_identifier(name)} IN SHARE MODE{suffix}"
        for name in locked_tables(profile)
    )


def exclusive_lock_statements() -> tuple[str, ...]:
    """CM-3.1이 바꾸는 두 객체만 `ACCESS EXCLUSIVE`.

    **상태를 확인하기 전에는 잡지 않는다.** drift인 DB에서 먼저 잡으면 쓰기 0으로
    끝내면서도 남의 읽기를 막는다.
    """

    return (f"LOCK TABLE public.{R03_TABLE} IN ACCESS EXCLUSIVE MODE",)


# ---------------------------------------------------------------------------
# receipt·marker 계약 (계획 §7.3)
# ---------------------------------------------------------------------------

RECEIPT_STATUSES: tuple[str, ...] = ("STARTED", "COMMITTED", "ABORTED")

#: commit **직후** 실측 identity. `COMMITTED` receipt는 이 셋을 반드시 갖는다.
#:
#: 없으면 아무 `COMMITTED` receipt로 marker를 되살릴 수 있다 — live가 canonical View만
#: 갖고 있으면 통과했다(구현리뷰 9차 필수 4).
POST_COMMIT_IDENTITY_KEYS: tuple[str, ...] = (
    "schema_signature_sha256",
    "security_signature_sha256",
    "excluded_projection_sha256",
)

RECEIPT_KEYS: frozenset[str] = frozenset(
    {
        "artifact_type",
        "format_version",
        "dataset_epoch",
        "database",
        "profile",
        "status",
        "change_ref",
        "migration_id",
        "migration_bundle_sha256",
        "route",
        "recorded_at",
        *POST_COMMIT_IDENTITY_KEYS,
    }
)

MARKER_KEYS: frozenset[str] = RECEIPT_KEYS | {
    "view_definition_sha256",
    "view_rows",
    "r03_rows",
    "action_history_rows",
    "committed_at",
}

RECEIPT_ARTIFACT_TYPE = "v5_reference_receipt"
MARKER_ARTIFACT_TYPE = "v5_reference_marker"

#: no-op 판정에서 **제외**하는 값. 적용 시점 관측 snapshot일 뿐이다.
#:
#: `V5-A-1.4`가 R03 3건을 넣으면 `0/189 → 3/192`가 된다. 행 수를 identity로 쓰면 정상
#: 진행한 DB가 drift가 된다(계획 §7.3).
MARKER_VOLATILE_KEYS: frozenset[str] = frozenset(
    {"view_rows", "r03_rows", "action_history_rows", "recorded_at", "committed_at"}
)


def assert_receipt_contract(payload: Mapping[str, Any]) -> None:
    """receipt가 exact key와 허용 status를 갖는지 본다."""

    if set(payload) != RECEIPT_KEYS:
        raise ReferenceV5Error("RECEIPT_SCHEMA_MISMATCH", EXIT_MISMATCH)
    if payload["artifact_type"] != RECEIPT_ARTIFACT_TYPE:
        raise ReferenceV5Error("RECEIPT_SCHEMA_MISMATCH", EXIT_MISMATCH)
    status = payload["status"]
    if status not in RECEIPT_STATUSES:
        raise ReferenceV5Error("RECEIPT_STATUS_NOT_ALLOWED", EXIT_MISMATCH)
    _assert_post_commit_identity(payload, required=status == "COMMITTED")
    _assert_common_identity(payload)


def assert_marker_contract(payload: Mapping[str, Any]) -> None:
    """success marker 계약. **commit과 postcheck 뒤에만** 저장된다."""

    if set(payload) != MARKER_KEYS:
        raise ReferenceV5Error("MARKER_SCHEMA_MISMATCH", EXIT_MISMATCH)
    if payload["artifact_type"] != MARKER_ARTIFACT_TYPE:
        raise ReferenceV5Error("MARKER_SCHEMA_MISMATCH", EXIT_MISMATCH)
    if payload["status"] != "COMMITTED":
        # 실패한 적용이 marker를 남기면 no-op이 그것을 정본으로 삼는다.
        raise ReferenceV5Error("MARKER_STATUS_NOT_COMMITTED", EXIT_MISMATCH)
    _assert_post_commit_identity(payload, required=True)
    _assert_common_identity(payload)
    if payload["view_definition_sha256"] != CANONICAL_VIEW_SHA256:
        raise ReferenceV5Error("VIEW_IDENTITY_MISMATCH", EXIT_MISMATCH)
    for key in ("view_rows", "r03_rows", "action_history_rows"):
        value = payload[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ReferenceV5Error("MARKER_SCHEMA_MISMATCH", EXIT_MISMATCH)


def _assert_post_commit_identity(payload: Mapping[str, Any], *, required: bool) -> None:
    """commit 직후 identity 세 값의 형식을 본다.

    `STARTED`·`ABORTED`는 아직 없으므로 `None`이어야 한다 — 자리만 비워두면 나중에
    아무 값이나 채워도 계약이 모른다.
    """

    for key in POST_COMMIT_IDENTITY_KEYS:
        value = payload[key]
        if not required:
            if value is not None:
                raise ReferenceV5Error("RECEIPT_IDENTITY_UNEXPECTED", EXIT_MISMATCH)
            continue
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ReferenceV5Error("ARTIFACT_HASH_MALFORMED", EXIT_MISMATCH)


def _assert_common_identity(payload: Mapping[str, Any]) -> None:
    """receipt·marker가 공유하는 신원. 민감 값은 애초에 자리가 없다."""

    if payload["format_version"] != MIGRATION_CONTRACT_FORMAT_VERSION:
        raise ReferenceV5Error("ARTIFACT_VERSION_MISMATCH", EXIT_MISMATCH)
    if payload["dataset_epoch"] != FINAL_DATASET_EPOCH:
        raise ReferenceV5Error("MANIFEST_EPOCH_NOT_FINAL", EXIT_MISMATCH)
    if payload["migration_id"] != MIGRATION_ID:
        raise ReferenceV5Error("CONTRACT_IDENTITY_MISMATCH", EXIT_MISMATCH)
    if payload["migration_bundle_sha256"] != migration_bundle_sha256():
        raise ReferenceV5Error("MIGRATION_BUNDLE_STALE", EXIT_MISMATCH)
    database = payload["database"]
    if database not in TARGET_PROFILE:
        raise ReferenceV5Error("TARGET_NOT_ALLOWED", EXIT_USAGE)
    if payload["profile"] != TARGET_PROFILE[database]:
        raise ReferenceV5Error("PROFILE_NOT_ALLOWED", EXIT_USAGE)
    if payload["route"] not in MIGRATION_ROUTES:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    change_ref = payload["change_ref"]
    if not isinstance(change_ref, str) or not change_ref:
        raise ReferenceV5Error("CHANGE_REF_REQUIRED", EXIT_USAGE)
    assert_no_sensitive_values(payload)


def assert_marker_allows_noop(
    marker: Mapping[str, Any],
    *,
    schema_signature: str,
    security_signature: str,
    excluded_projection: str,
) -> None:
    """marker와 live가 **셋 다** 같을 때만 no-op으로 통과시킨다(계획 §7.3).

    excluded를 빼면 Runtime·RAG·base가 바뀐 target도 no-op이 된다(구현리뷰 9차 필수 4).
    행 수는 보지 않는다 — `MARKER_VOLATILE_KEYS`가 그 이유를 담고 있다.
    """

    assert_marker_contract(marker)
    live = {
        "schema_signature_sha256": schema_signature,
        "security_signature_sha256": security_signature,
        "excluded_projection_sha256": excluded_projection,
    }
    for key, reason in (
        ("schema_signature_sha256", "SCHEMA_SIGNATURE_MISMATCH"),
        ("security_signature_sha256", "SECURITY_SIGNATURE_MISMATCH"),
        ("excluded_projection_sha256", "EXCLUDED_PROJECTION_MISMATCH"),
    ):
        value = live[key]
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ReferenceV5Error("ARTIFACT_HASH_MALFORMED", EXIT_MISMATCH)
        if marker[key] != value:
            raise ReferenceV5Error(reason, EXIT_MISMATCH)


def assert_recovery_is_allowed(
    receipt: Mapping[str, Any],
    *,
    schema_signature: str,
    security_signature: str,
    excluded_projection: str,
    view_definition_sha256_value: str,
) -> None:
    """marker 유실을 exact `COMMITTED` receipt와 live 실측으로만 복구한다(계획 §10).

    receipt가 commit 직후 identity를 갖고 있고 **live가 그것과 같을 때만** 복구다.
    이게 없으면 다른 target의 `COMMITTED` receipt로도 marker를 만들 수 있다
    (구현리뷰 9차 필수 4).
    """

    assert_receipt_contract(receipt)
    if receipt["status"] != "COMMITTED":
        raise ReferenceV5Error("RECEIPT_STATUS_NOT_ALLOWED", EXIT_MISMATCH)
    if view_definition_sha256_value != CANONICAL_VIEW_SHA256:
        raise ReferenceV5Error("TARGET_STATE_UNSUPPORTED", EXIT_CONFIRM_REQUIRED)
    live = {
        "schema_signature_sha256": schema_signature,
        "security_signature_sha256": security_signature,
        "excluded_projection_sha256": excluded_projection,
    }
    for key, reason in (
        ("schema_signature_sha256", "SCHEMA_SIGNATURE_MISMATCH"),
        ("security_signature_sha256", "SECURITY_SIGNATURE_MISMATCH"),
        ("excluded_projection_sha256", "EXCLUDED_PROJECTION_MISMATCH"),
    ):
        value = live[key]
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ReferenceV5Error("ARTIFACT_HASH_MALFORMED", EXIT_MISMATCH)
        if receipt[key] != value:
            raise ReferenceV5Error(reason, EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# owner·ACL 복원 statement (계획 §6.5 · §7.2)
#
# **migration SQL에 넣지 않는다.** canonical DDL은 base-only 경로와 공유하므로 target별
# 승인값을 파일에 담을 수 없고, SQL guard가 `GRANT`·`REVOKE`를 금지 토큰으로 갖고 있다.
# runner가 같은 transaction 안에서 발행한다(7차 계획리뷰 권장 1).
# ---------------------------------------------------------------------------

#: `aclitem` 권한 문자 → SQL 권한. PostgreSQL 16 table/view 권한만 다룬다.
ACL_PRIVILEGE_SQL: Mapping[str, str] = MappingProxyType(
    {
        "r": "SELECT",
        "a": "INSERT",
        "w": "UPDATE",
        "d": "DELETE",
        "D": "TRUNCATE",
        "x": "REFERENCES",
        "t": "TRIGGER",
    }
)


def restore_security_statements(
    rows: Mapping[str, Mapping[str, Any]], *, mode: str
) -> tuple[str, ...]:
    """재생성한 R03·View에 승인 pre-state owner·ACL을 되돌린다.

    base-only는 복원할 pre-state가 없다 — creator owner를 그대로 두고 statement를 0개
    낸다(계획 §7.2 · 회귀가 "ACL statement 0회"를 고정한다).
    """

    # mode allowlist를 여기서 또 보지 않는다 — 아래 `assert_relation_security()`가 같은
    # reason code로 거부하므로 두 번 보면 어느 쪽을 지워도 결과가 같다(변이 X22).
    if mode == "base_only":
        return ()
    if set(rows) != set(OWNED_BY_CM31):
        raise ReferenceV5Error("SECURITY_RELATION_SET_MISMATCH", EXIT_MISMATCH)

    statements: list[str] = []
    for name in OWNED_BY_CM31:
        row = rows[name]
        assert_relation_security(row, mode=mode)
        keyword = "VIEW" if name == ALARM_VIEW else "TABLE"
        owner = _quote_identifier(str(row["owner"]))
        statements.append(f"ALTER {keyword} public.{name} OWNER TO {owner}")
        for grantee, privileges, _grantor in parse_acl(row.get("acl")):
            if grantee == PUBLIC_GRANTEE:
                # 여기까지 올 수 없다 — `assert_relation_security()`가 먼저 거부한다.
                raise ReferenceV5Error("SECURITY_PUBLIC_GRANT", EXIT_MISMATCH)
            if grantee == str(row["owner"]):
                # 재생성 직후 owner는 이미 모든 권한을 갖는다. 다시 GRANT해도 no-op
                # 이지만 statement 수가 달라져 회귀가 흔들린다.
                continue
            unknown = [c for c in privileges if c not in ACL_PRIVILEGE_SQL]
            if unknown:
                raise ReferenceV5Error("SECURITY_ACL_UNSUPPORTED", EXIT_MISMATCH)
            granted = ", ".join(
                ACL_PRIVILEGE_SQL[c] for c in sorted(privileges, key=str.lower)
            )
            statements.append(
                f"GRANT {granted} ON public.{name} TO {_quote_identifier(grantee)}"
            )
    return tuple(statements)


# ---------------------------------------------------------------------------
# catalog 조회 SQL (묶음 2)
# ---------------------------------------------------------------------------

RELATION_PRESENCE_SQL = (
    "SELECT c.relname AS relname, c.relkind AS relkind FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = ANY(%(names)s)"
)
R03_COLUMNS_SQL = (
    "SELECT column_name, data_type, character_maximum_length, is_nullable "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = %(table)s ORDER BY ordinal_position"
)
R03_CONSTRAINTS_SQL = (
    "SELECT conname, contype::text AS contype, "
    "pg_get_constraintdef(oid) AS definition FROM pg_constraint "
    "WHERE conrelid = 'public.r03_alarm_history'::regclass ORDER BY conname"
)
VIEW_COLUMNS_SQL = (
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = %(view)s ORDER BY ordinal_position"
)
#: `%(view)s::regclass`는 psycopg에서는 되지만 SQLAlchemy `text()`가 `:view::regclass`로
#: 옮기면 파싱하지 못한다 — production adapter에서만 깨진다(구현리뷰 11차 필수 1).
#: 명시 `CAST`는 양쪽 모두에서 동작한다.
VIEW_DEFINITION_SQL = (
    "SELECT pg_get_viewdef(CAST(%(view)s AS regclass), true) AS definition"
)

#: R03·View에 기대던 것 **외의** dependent object. 있으면 `DROP`이 무엇을 함께 끊을지
#: 모르므로 중단한다(구현리뷰 9차 필수 3).
DEPENDENTS_SQL = (
    "SELECT DISTINCT dependent.relname AS relname, dependent.relkind::text AS relkind "
    "FROM pg_depend d "
    "JOIN pg_rewrite r ON r.oid = d.objid "
    "JOIN pg_class dependent ON dependent.oid = r.ev_class "
    "JOIN pg_class referenced ON referenced.oid = d.refobjid "
    "JOIN pg_namespace n ON n.oid = referenced.relnamespace "
    "WHERE n.nspname = 'public' AND referenced.relname = ANY(%(names)s) "
    "AND dependent.relname <> ALL(%(names)s)"
)


def read_relation_presence(execute: Any) -> tuple[bool, bool]:
    """R03 table과 View가 있는지 본다. `relkind`까지 확인한다.

    같은 이름의 다른 종류(예: View 자리에 table)가 있으면 있는 것으로 세면 안 된다.
    """

    rows = execute(RELATION_PRESENCE_SQL, {"names": list(OWNED_BY_CM31)})
    kinds = {str(row["relname"]): str(row["relkind"]) for row in rows}
    return kinds.get(R03_TABLE) == "r", kinds.get(ALARM_VIEW) == "v"


# ---------------------------------------------------------------------------
# transaction 구동 (계획 §7.2)
#
# `execute`는 `(sql, params) -> rows` 호출 가능 객체다. **module이 driver를 고르지
# 않는다** — 회귀는 statement를 기록하는 가짜를, container 테스트는 실제 psycopg를
# 넘긴다. 순수 계약 모듈 성질도 이렇게 지킨다.
# ---------------------------------------------------------------------------


def read_live_schema(execute: Any) -> dict[str, Any]:
    """postcheck·signature에 필요한 catalog를 한 번에 읽는다."""

    definition = execute(VIEW_DEFINITION_SQL, {"view": ALARM_VIEW})[0]["definition"]
    security = {
        str(row["relname"]): row
        for row in execute(RELATION_SECURITY_SQL, {"names": list(OWNED_BY_CM31)})
    }
    return {
        "r03_columns": execute(R03_COLUMNS_SQL, {"table": R03_TABLE}),
        "r03_constraints": execute(R03_CONSTRAINTS_SQL, None),
        "view_columns": execute(VIEW_COLUMNS_SQL, {"view": ALARM_VIEW}),
        "view_definition": definition,
        "security": security,
    }


def live_signatures(live: Mapping[str, Any], *, mode: str) -> tuple[str, str]:
    """schema·security signature를 실측에서 만든다.

    `assert_*`가 먼저 돌기 때문에 **non-canonical catalog는 signature를 만들 수 없다**
    (구현리뷰 2차 필수 3).
    """

    security = live["security"]
    schema = schema_signature_sha256(
        r03_columns=live["r03_columns"],
        r03_constraints=live["r03_constraints"],
        view_columns=live["view_columns"],
        view_definition=live["view_definition"],
        r03_comment=security[R03_TABLE].get("comment"),
        view_comment=security[ALARM_VIEW].get("comment"),
    )
    return schema, security_signature_sha256(security, mode=mode)


def apply_plan(
    *, route: str, profile: str, security: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    """§7.2 순서를 그대로 편 statement 목록.

    순서 자체가 계약이다 — View를 먼저 내려야 `DROP TABLE`이 dependent에 막히지 않고,
    권한 복원은 재생성 **직후**에 와야 postcheck가 실제 상태를 본다.
    """

    if route not in MIGRATION_ROUTES:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    mode = "successor" if route == "successor" else "base_only"
    plan: list[str] = [*SESSION_PROLOGUE, *share_lock_statements(profile)]
    source = SUCCESSOR_SQL if route == "successor" else CANONICAL_SQL
    text = source.read_text(encoding="utf-8")
    # **파일이 route의 본문이다.** DROP을 따로 덧붙이면 파일 안의 것과 중복된다.
    # statement 단위로 펴서 driver가 한 번에 하나씩 실행하게 한다.
    #
    # 순서만 보면 `FROM trace_alarm_history`를 `FROM document`로 바꾼 SQL이 통과한다 —
    # operation/target 열은 그대로이기 때문이다(구현리뷰 9차 필수 2). scope guard를
    # 함께 돌린다.
    allowed = SUCCESSOR_STATEMENTS if route == "successor" else CANONICAL_STATEMENTS
    assert_sql_scope(text, allowed=allowed)
    assert_statement_sequence(text, route=route)
    if route == "successor":
        # 상태를 확인한 뒤에야 배타 lock을 잡는다. guard는 같은 transaction에서 읽는다.
        plan.extend(exclusive_lock_statements())
    plan.extend(split_statements(text))
    plan.extend(restore_security_statements(security, mode=mode))
    return tuple(plan)


def assert_plan_shape(
    plan: Sequence[str],
    *,
    route: str,
    profile: str,
    security: Mapping[str, Mapping[str, Any]],
) -> None:
    """계획이 기대 plan과 **전부** 같은지 본다.

    앞서는 순서 관계만 봤는데, 그러면 base `SHARE` 9개 중 8개를 지운 plan도 통과했다
    (구현리뷰 9차 필수 2). 기대 plan을 다시 만들어 exact 비교한다 — 조각 검사로는
    "빠진 것"을 잡을 수 없다.
    """

    expected = apply_plan(route=route, profile=profile, security=security)
    if tuple(plan) != expected:
        raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)


def assert_plan_invariants(plan: Sequence[str], *, route: str) -> None:
    """plan이 §7.2 순서 규칙을 지켰는지 본다.

    `assert_plan_shape()`가 exact 비교를 하므로 이 판정은 **기대 plan 자체가 규칙을
    지키는지** 확인하는 용도다. 회귀가 단독으로 쓴다.
    """

    if route not in MIGRATION_ROUTES:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    if tuple(plan[: len(SESSION_PROLOGUE)]) != SESSION_PROLOGUE:
        raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)
    body = list(plan[len(SESSION_PROLOGUE) :])
    shares = [i for i, s in enumerate(body) if "IN SHARE MODE" in s]
    if shares != list(range(len(shares))):
        # `SHARE`는 앞쪽에 연속으로 와야 한다.
        raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)
    exclusive = [i for i, s in enumerate(body) if "ACCESS EXCLUSIVE" in s]
    drops = [i for i, s in enumerate(body) if s.lstrip().upper().startswith("DROP ")]
    grants = [i for i, s in enumerate(body) if s.startswith(("GRANT ", "ALTER "))]
    if route == "successor":
        if not exclusive or not drops or not grants:
            raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)
        if not (max(shares) < exclusive[0] < drops[0] and max(drops) < grants[0]):
            raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)
        if not body[drops[0]].rstrip().endswith(ALARM_VIEW):
            # View를 먼저 내리지 않으면 `DROP TABLE`이 dependent에 막힌다.
            raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)
    else:
        if exclusive or drops or grants:
            # base-only는 지울 것도 복원할 pre-state도 없다.
            raise ReferenceV5Error("PLAN_ORDER_MISMATCH", EXIT_MISMATCH)


def assert_postcheck(
    live: Mapping[str, Any],
    *,
    profile: str,
    mode: str,
    view_rows: int,
    r03_rows: int,
    action_rows: int,
    require_final_dataset: bool = True,
) -> str | None:
    """commit 전에 실제 catalog와 행 수를 본다. 통과하면 data phase를 돌려준다.

    `require_final_dataset=False`는 **격리 rehearsal 전용**이다. CM-2.6 fixture는 형상
    재현용이라 최종 dataset의 189/192를 갖지 않는다(계획 §9.2). 그때도 schema·security·
    action 계약은 그대로 보고, data phase 판정만 건너뛴다.

    공용 target은 언제나 기본값 `True`로 부른다 — 최종 dataset이 아니면 멈춰야 한다.
    """

    live_signatures(live, mode=mode)  # 계약 위반이면 여기서 멈춘다
    if profile not in FINAL_ACTION_ROWS:
        raise ReferenceV5Error("PROFILE_NOT_ALLOWED", EXIT_USAGE)
    if action_rows != FINAL_ACTION_ROWS[profile]:
        raise ReferenceV5Error("ACTION_ROWS_CHANGED", EXIT_MISMATCH)
    if not require_final_dataset:
        return None
    return classify_data_phase(r03_rows=r03_rows, view_rows=view_rows)


# ---------------------------------------------------------------------------
# excluded object fingerprint (계획 §7.2 · 구현리뷰 9차 필수 4·5)
# ---------------------------------------------------------------------------

EXCLUDED_COLUMNS_SQL = (
    "SELECT table_name, column_name, data_type, is_nullable "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = ANY(%(names)s) "
    "ORDER BY table_name, ordinal_position"
)

#: 보존 객체의 constraint·index. **형상과 행 수만으로는 부족하다** — 행 수를 유지한 채
#: constraint를 떼도 hash가 같았다(구현리뷰 10차 필수 5).
EXCLUDED_CONSTRAINTS_SQL = (
    "SELECT c.relname AS relname, con.conname AS conname, "
    "con.contype::text AS contype, pg_get_constraintdef(con.oid) AS definition "
    "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = ANY(%(names)s) "
    "ORDER BY c.relname, con.conname"
)
EXCLUDED_INDEXES_SQL = (
    "SELECT tablename AS relname, indexname, indexdef FROM pg_indexes "
    "WHERE schemaname = 'public' AND tablename = ANY(%(names)s) "
    "ORDER BY tablename, indexname"
)

#: base 9의 **값**까지 본다. 보존 Runtime·RAG·D는 대상이 아니다 — 운영 중 값이 바뀔 수
#: 있고 그건 CM-3.1이 막을 일이 아니다.
EXCLUDED_CONTENT_SQL = (
    "SELECT md5(coalesce(string_agg(t.row_text, chr(10) ORDER BY t.row_text), '')) "
    "AS digest FROM (SELECT %(table)s || ':' || x::text AS row_text "
    "FROM public.{table} AS x) AS t"
)


def excluded_projection_sha256(execute: Any, *, profile: str) -> str:
    """CM-3.1이 **건드리지 않는** 객체의 형상과 행 수를 하나의 hash로 묶는다.

    base 9와 profile 보존분이 대상이다. `SHARE` lock 안에서 재므로 transaction 동안
    다른 session이 바꿀 수 없다 — 전후 값이 같아야 "변경 0건"이 증명된다.
    """

    import json

    names = list(locked_tables(profile))
    columns = execute(EXCLUDED_COLUMNS_SQL, {"names": names})
    constraints = execute(EXCLUDED_CONSTRAINTS_SQL, {"names": names})
    indexes = execute(EXCLUDED_INDEXES_SQL, {"names": names})
    security = {
        str(row["relname"]): row
        for row in execute(RELATION_SECURITY_SQL, {"names": names})
    }
    counts: dict[str, int] = {}
    content: dict[str, Any] = {}
    for name in names:
        quoted = _quote_identifier(name)
        counts[name] = int(
            execute(f"SELECT count(*) AS n FROM public.{quoted}")[0]["n"]
        )
        if name in BASE_TABLE_NAMES:
            # base 9는 **값까지** 본다. 행 수를 유지한 채 값만 바꾼 변이를 잡으려면
            # 내용이 필요하다(구현리뷰 10차 필수 5).
            content[name] = execute(
                EXCLUDED_CONTENT_SQL.format(table=quoted), {"table": name}
            )[0]["digest"]
    payload = {
        "migration_id": MIGRATION_ID,
        "profile": profile,
        "columns": [
            [
                str(row["table_name"]),
                str(row["column_name"]),
                str(row["data_type"]),
                str(row["is_nullable"]).upper(),
            ]
            for row in columns
        ],
        "constraints": sorted(
            [
                str(row["relname"]),
                str(row["conname"]),
                str(row["contype"]),
                _normalize_sql(str(row["definition"])),
            ]
            for row in constraints
        ),
        "indexes": sorted(
            [
                str(row["relname"]),
                str(row["indexname"]),
                _normalize_sql(str(row["indexdef"])),
            ]
            for row in indexes
        ),
        "security": [
            [
                name,
                str(security[name]["owner"]) if name in security else None,
                [list(e) for e in parse_acl(security.get(name, {}).get("acl"))],
            ]
            for name in names
        ],
        "row_counts": counts,
        "base_content": content,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# artifact 저장 (계획 §7.3 · 구현리뷰 9차 필수 4)
# ---------------------------------------------------------------------------

ARTIFACT_DIR_MODE = 0o700
ARTIFACT_FILE_MODE = 0o600


def receipt_name(database: str, change_ref: str) -> str:
    return f"receipt.{_safe_component(database)}.{_safe_component(change_ref)}.json"


def marker_name(database: str, change_ref: str) -> str:
    return f"marker.{_safe_component(database)}.{_safe_component(change_ref)}.json"


_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _safe_component(value: str) -> str:
    """파일 이름에 들어갈 조각. 경로 조작을 막는다."""

    if not isinstance(value, str) or _SAFE_COMPONENT.fullmatch(value) is None:
        raise ReferenceV5Error("ARTIFACT_NAME_UNSAFE", EXIT_USAGE)
    return value


def write_artifact(path: Path, payload: Mapping[str, Any]) -> str:
    """canonical JSON을 **원자적으로** 쓴다. 반환값은 그 파일의 SHA-256이다.

    같은 디렉터리에 임시 파일을 만들고 fsync한 뒤 `os.replace`한다. 부분 기록된
    artifact가 남으면 복구가 그것을 정본으로 삼는다.
    """

    import json
    import os
    import tempfile

    assert_no_sensitive_values(payload)
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    directory = path.parent
    directory.mkdir(mode=ARTIFACT_DIR_MODE, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, ARTIFACT_FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        with contextlib_suppress():
            os.unlink(temporary)
        raise
    # **디렉터리 fsync는 POSIX 것이다.** Windows에서는 디렉터리를 열어 fsync할 수 없어
    # `OSError`가 난다(구현리뷰 10차 권장 1). 파일 자체는 이미 fsync했고 `os.replace`가
    # 원자적이므로, 디렉터리 동기화 실패는 저장 실패로 보지 않는다.
    #
    # 0600도 POSIX 의미다. Windows ACL은 같지 않으므로 그 플랫폼에서 권한을 이 값으로
    # 판단하지 않는다.
    if os.name == "posix":
        directory_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def contextlib_suppress() -> Any:
    """임시 파일 정리 실패가 원래 예외를 가리지 않게 한다."""

    import contextlib

    return contextlib.suppress(OSError)


def read_artifact(path: Path) -> dict[str, Any]:
    """artifact를 읽는다. JSON이 아니면 typed reason으로 끝낸다."""

    import json

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReferenceV5Error("ARTIFACT_UNREADABLE", EXIT_USAGE) from exc
    if not isinstance(payload, dict):
        raise ReferenceV5Error("ARTIFACT_UNREADABLE", EXIT_USAGE)
    return payload


# ---------------------------------------------------------------------------
# runner lifecycle (계획 §7.1·§7.2 · 구현리뷰 9차 필수 1)
# ---------------------------------------------------------------------------


def read_target_state(execute: Any, *, profile: str) -> dict[str, Any]:
    """route 판정과 pre-state 검증에 필요한 것을 한 번에 읽는다. **쓰기 0.**"""

    r03_present, view_present = read_relation_presence(execute)
    columns = execute(R03_COLUMNS_SQL, {"table": R03_TABLE}) if r03_present else None
    definition = (
        execute(VIEW_DEFINITION_SQL, {"view": ALARM_VIEW})[0]["definition"]
        if view_present
        else None
    )
    state = classify_schema_state(
        r03_present=r03_present,
        view_present=view_present,
        r03_columns=columns,
        view_definition=definition,
    )
    security = {
        str(row["relname"]): row
        for row in execute(RELATION_SECURITY_SQL, {"names": list(OWNED_BY_CM31)})
    }
    return {
        "state": state,
        "route": {"BASE_ONLY": "canonical", "V4_REFERENCE_COMPAT": "successor"}.get(
            state
        ),
        "r03_columns": columns,
        "r03_constraints": execute(R03_CONSTRAINTS_SQL, None) if r03_present else [],
        "view_definition": definition,
        "security": security,
        "excluded_projection_sha256": excluded_projection_sha256(
            execute, profile=profile
        ),
    }


def _now(clock: Any = None) -> str:
    from datetime import datetime, timedelta, timezone

    stamp = (clock or datetime.now)(timezone(timedelta(hours=9)))
    return stamp.isoformat(timespec="seconds")


def _artifact_base(
    *, database: str, profile: str, route: str, change_ref: str, status: str, when: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_type": RECEIPT_ARTIFACT_TYPE,
        "format_version": MIGRATION_CONTRACT_FORMAT_VERSION,
        "dataset_epoch": FINAL_DATASET_EPOCH,
        "database": database,
        "profile": profile,
        "status": status,
        "change_ref": change_ref,
        "migration_id": MIGRATION_ID,
        "migration_bundle_sha256": migration_bundle_sha256(),
        "route": route,
        "recorded_at": when,
    }
    for key in POST_COMMIT_IDENTITY_KEYS:
        payload[key] = None
    return payload


def apply_to_target(
    execute: Any,
    *,
    database: str,
    confirm_target: str | None,
    change_ref: str,
    artifact_root: Path,
    commit: Any,
    rollback: Any,
    dry_run: bool = False,
    require_final_dataset: bool = True,
    clock: Any = None,
) -> dict[str, Any]:
    """한 target에 대한 전체 lifecycle.

    ```
    target/confirm 확인 → advisory lock → transaction 진입 → prologue
    → Gate 0 재확인 → route 판정 → pre-state exact → plan 실행
    → postcheck·excluded 재대조 → commit → COMMITTED receipt → marker-last
    ```

    `dry_run`(= `rehearse`)은 **commit 대신 rollback**한다. 그래서 실제 DDL을 돌려보되
    아무것도 남기지 않는다. marker도 쓰지 않는다.
    """

    profile = assert_target_allowed(database, confirm_target=confirm_target)
    if not require_final_dataset:
        # CLI를 거치지 않고 불러도 같은 제한을 받는다(구현리뷰 11차 필수 2).
        assert_non_final_dataset_allowed(
            database=database, mode="rehearse" if dry_run else "apply"
        )
    lock_params = {
        "namespace": ADVISORY_LOCK_NAMESPACE,
        "key": advisory_lock_key(database),
    }
    receipt_path = artifact_root / receipt_name(database, change_ref)
    started = _artifact_base(
        database=database,
        profile=profile,
        route="successor",
        change_ref=change_ref,
        status="STARTED",
        when=_now(clock),
    )
    if not _acquired(execute(ADVISORY_LOCK_SQL, lock_params)):
        # 다른 실행이 같은 target을 잡고 있다. **쓰기 0으로 끝난다.**
        raise ReferenceV5Error("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
    released = False
    try:
        result = _apply_locked(
            execute,
            database=database,
            profile=profile,
            change_ref=change_ref,
            artifact_root=artifact_root,
            receipt_path=receipt_path,
            started=started,
            commit=commit,
            rollback=rollback,
            dry_run=dry_run,
            require_final_dataset=require_final_dataset,
            clock=clock,
        )
    except BaseException:
        # **원래 예외가 우선이다.** unlock 실패로 원인을 가리면 무엇이 실패했는지 모른다
        # (구현리뷰 10차 필수 6).
        with contextlib_suppress_all():
            released = bool(_release_lock(execute, lock_params))
        raise
    # 성공 경로에서는 unlock 실패를 **숨기지 않는다.** lock이 남은 채 COMMITTED를
    # 돌려주면 다음 실행이 이유 없이 막힌다(구현리뷰 11차 필수 4).
    released = bool(_release_lock(execute, lock_params))
    if not released:
        raise ReferenceV5Error("ADVISORY_UNLOCK_FAILED", EXIT_MISMATCH)
    return result


def _acquired(rows: Sequence[Mapping[str, Any]]) -> bool:
    """`pg_try_advisory_lock()`의 boolean 결과."""

    if not rows:
        return False
    value = next(iter(rows[0].values()))
    return value is True or value == 1


def _release_lock(execute: Any, lock_params: Mapping[str, Any]) -> bool:
    """`pg_advisory_unlock()`의 boolean 결과까지 본다.

    `False`면 lock을 갖고 있지 않았다는 뜻이다 — lifecycle이 흐트러진 상태다.
    """

    rows = execute(ADVISORY_UNLOCK_SQL, dict(lock_params))
    if not rows:
        return False
    value = next(iter(rows[0].values()))
    return value is True or value == 1


def contextlib_suppress_all() -> Any:
    """정리 실패가 원래 예외를 가리지 않게 한다."""

    import contextlib

    return contextlib.suppress(Exception)


def _apply_locked(
    execute: Any,
    *,
    database: str,
    profile: str,
    change_ref: str,
    artifact_root: Path,
    receipt_path: Path,
    started: Mapping[str, Any],
    commit: Any,
    rollback: Any,
    dry_run: bool,
    require_final_dataset: bool,
    clock: Any,
) -> dict[str, Any]:
    """lock을 잡은 뒤의 본문. 호출자가 `finally`에서 unlock을 보장한다."""

    started = dict(started)
    try:
        isolation = execute(ISOLATION_SQL)[0]["transaction_isolation"]
        if isolation != EXPECTED_ISOLATION:
            raise ReferenceV5Error("ISOLATION_UNEXPECTED", EXIT_MISMATCH)
        for statement in SESSION_PROLOGUE[1:]:
            execute(statement)

        before = read_target_state(execute, profile=profile)
        route = before["route"]
        if route is None:
            raise ReferenceV5Error("TARGET_STATE_UNSUPPORTED", EXIT_CONFIRM_REQUIRED)
        assert_state_allows_apply(before["state"], route=route)
        started["route"] = route
        write_artifact(receipt_path, started)

        security = before["security"] if route == "successor" else {}
        plan = apply_plan(route=route, profile=profile, security=security)
        assert_plan_shape(plan, route=route, profile=profile, security=security)
        assert_plan_invariants(plan, route=route)

        body = list(plan[len(SESSION_PROLOGUE) :])
        for index, statement in enumerate(body):
            execute(statement)
            if route == "successor" and "ACCESS EXCLUSIVE" in statement:
                # **lock을 잡은 뒤 다시 잰다.** lock 대기 중 schema가 바뀌었을 수 있다
                # (구현리뷰 10차 필수 3). 여기서 멈추면 아직 DROP 전이다.
                assert_successor_pre_state(**read_successor_pre_state(execute))
                assert not any(
                    body[i].lstrip().upper().startswith("DROP ")
                    for i in range(index + 1)
                )

        live = read_live_schema(execute)
        mode = "successor" if route == "successor" else "base_only"
        schema_signature, security_signature = live_signatures(live, mode=mode)
        excluded = excluded_projection_sha256(execute, profile=profile)
        if excluded != before["excluded_projection_sha256"]:
            # CM-3.1은 base 9·Runtime·RAG·D를 건드리지 않는다.
            raise ReferenceV5Error("EXCLUDED_PROJECTION_MISMATCH", EXIT_MISMATCH)
        counts = read_row_counts(execute)
        assert_postcheck(
            live,
            profile=profile,
            mode=mode,
            view_rows=counts["view_rows"],
            r03_rows=counts["r03_rows"],
            action_rows=counts["action_rows"],
            require_final_dataset=require_final_dataset,
        )
        assert_view_branches(
            execute,
            r03_rows=counts["r03_rows"],
            view_rows=counts["view_rows"],
            require_final_dataset=require_final_dataset,
        )
    except BaseException:
        rollback()
        aborted = {**started, "status": "ABORTED", "recorded_at": _now(clock)}
        with contextlib_suppress():
            write_artifact(receipt_path, aborted)
        raise

    identity = {
        "schema_signature_sha256": schema_signature,
        "security_signature_sha256": security_signature,
        "excluded_projection_sha256": excluded,
    }

    if dry_run:
        # rehearse는 실제 DDL을 돌려보되 아무것도 남기지 않는다.
        rollback()
        return {**started, "status": "ABORTED", "recorded_at": _now(clock)}

    # **commit 직전, 같은 transaction 안에서** commit identity를 DB에 남긴다.
    # 그래야 receipt 쓰기가 실패해도 복구 근거가 남는다(구현리뷰 11차 필수 5).
    try:
        commit()
    except BaseException as exc:
        # **commit 실패를 "DB 미변경"으로 단정하지 않는다**(구현리뷰 12차 필수 5).
        #
        # COMMIT은 성공했는데 응답이 유실됐을 수 있다. 그 상태에서 `ABORTED`를 쓰면
        # DB는 final인데 artifact는 재적용 대상으로 표시된다. `STARTED`를 그대로 두고
        # 사람이 `--verify`로 결정한다.
        with contextlib_suppress_all():
            rollback()
        raise ReferenceV5Error("COMMIT_OUTCOME_UNKNOWN", EXIT_CONFIRM_REQUIRED) from exc

    committed = {
        **started,
        "status": "COMMITTED",
        "recorded_at": _now(clock),
        **identity,
    }
    marker = {
        **committed,
        "artifact_type": MARKER_ARTIFACT_TYPE,
        "view_definition_sha256": view_definition_sha256(live["view_definition"]),
        "view_rows": counts["view_rows"],
        "r03_rows": counts["r03_rows"],
        "action_history_rows": counts["action_rows"],
        "committed_at": _now(clock),
    }
    # **DB는 이미 커밋됐다.** 어느 쪽이 실패하든 재적용 대상이 아니라 복구 대상이다
    # (구현리뷰 10차 필수 6). 다만 **남은 상태가 다르므로 다음 명령도 다르다**
    # (구현리뷰 14차 필수 1).
    try:
        write_artifact(receipt_path, committed)
    except BaseException as exc:
        # receipt가 `STARTED`로 남았다 → 승격 후 marker 복구.
        raise ReferenceV5Error("RECEIPT_WRITE_FAILED", EXIT_CONFIRM_REQUIRED) from exc
    try:
        assert_marker_contract(marker)
        # **marker-last.** commit·postcheck·receipt 저장이 모두 끝난 뒤에만 쓴다.
        write_artifact(artifact_root / marker_name(database, change_ref), marker)
    except BaseException as exc:
        # receipt는 `COMMITTED`다 → 승격하면 안 되고 바로 marker 복구다.
        raise ReferenceV5Error("MARKER_WRITE_FAILED", EXIT_CONFIRM_REQUIRED) from exc
    return marker


#: **DB에는 아무 record도 남기지 않는다.** 복구 근거는 artifact와 live 실측뿐이다.
#:
#: CM-3.1이 소유한 DB object는 `r03_alarm_history`와 `v_alarm_event` 둘뿐이다. commit
#: identity를 담을 table·retention·ACL·다중 migration key는 상위 계약에 없으므로 이
#: Task는 범위를 늘리지 않는다(구현리뷰 12차 필수 1).
#:
#: 그래서 복구 절차는 **디스크에 남은 receipt 상태**로 갈린다(구현리뷰 14차 필수 1).
#: 하나의 안내를 항상 출력하면, COMMITTED가 이미 남은 marker-only 실패에서 사용자가
#: 승격을 시도했다가 `RECEIPT_STATUS_NOT_ALLOWED`로 막힌다.


#: 계약 밖 예외의 reason code. 뒤에 SQLSTATE 또는 예외 class 이름이 붙는다.
UNEXPECTED_REASON = "UNEXPECTED_ERROR"


def report_unexpected(error: BaseException) -> int:
    """예상 못 한 예외를 **reason code로만** 낸다.

    `main()`이 `ReferenceV5Error`만 잡던 동안 driver 예외가 traceback으로 샜다.
    traceback에는 로컬 **절대경로**가 들어가고, 연결 실패 메시지에는 host·port가
    들어간다. 둘 다 비노출 대상이다(Gate 0 조사 §5).

    그래서 **예외 메시지를 찍지 않는다.** SQLSTATE는 5자 코드라 안전하고 원인을
    좁히는 데 그것으로 충분하다. 없으면 예외 class 이름으로 대신한다.
    """

    detail = _sqlstate(error) or type(error).__name__
    print(f"{UNEXPECTED_REASON} {detail}", file=sys.stderr)
    return EXIT_MISMATCH


#: 복구 runbook을 출력할 reason. commit 뒤 상태가 남는 것들이다.
RECOVERABLE_REASONS: frozenset[str] = frozenset(
    {"COMMIT_OUTCOME_UNKNOWN", "RECEIPT_WRITE_FAILED", "MARKER_WRITE_FAILED"}
)

#: 계약이나 identity가 어긋난 receipt. 상태 근거로 쓰지 않는다.
RECEIPT_UNTRUSTED = "UNTRUSTED"

#: runbook이 가리킬 진입점. console script가 설치돼 있지 않고 이 파일에는 shebang도
#: 실행 권한(현재 mode `0644`)도 없다 — 이름만 적으면 `PATH`에 없어서 그대로 실행할 수
#: 없다(구현리뷰 15차 필수 1). 그래서 지금 interpreter와 resolve된 경로를 함께 낸다.
RECOVERY_SCRIPT: Path = Path(__file__).resolve()


def recovery_entrypoint() -> tuple[str, ...]:
    """`python /abs/path/apply_reference_extensions_v5.py` — 저장소에서 바로 돈다."""

    return (sys.executable, str(RECOVERY_SCRIPT))


def format_command(argv: Sequence[str]) -> str:
    """argv를 **그 platform의 규칙으로** 한 줄로 만든다.

    문자열을 먼저 만들고 나중에 쪼개는 순서가 아니다. argv가 원본이고 표시가 파생이다.
    전에는 f-string 보간이라 공백이 든 artifact 경로가 `shlex.split()`에서 두 인자로
    갈라졌다(구현리뷰 15차 필수 1).
    """

    import os

    if os.name == "nt":
        import subprocess

        return subprocess.list2cmdline(list(argv))
    import shlex

    return shlex.join(argv)


def recovery_commands(
    *,
    database: str,
    change_ref: str,
    artifact_root: Path,
    receipt_status: str | None,
) -> tuple[tuple[str, ...], ...]:
    """남은 상태에 맞는 **다음 명령 argv**. 표시는 `format_command()`가 맡는다.

    `--verify`를 무조건 앞에 두지 않는다. standalone verify는 marker/receipt가 없으면
    route를 `successor`로 단정해 base-only target에서 `SECURITY_ACL_MISSING`으로
    실패한다. `promote-receipt`는 receipt route로 내부 verify를 하므로 그 단계가 없어도
    된다.
    """

    entry = recovery_entrypoint()
    target = ("--database", database, "--confirm-target", database)
    full = (*target, "--change-ref", change_ref, "--artifact-root", str(artifact_root))
    if receipt_status == "COMMITTED":
        return ((*entry, "--recover-marker", *full),)
    if receipt_status == "STARTED":
        return (
            (*entry, "--promote-receipt", "--confirm-recovery", *full),
            (*entry, "--recover-marker", *full),
        )
    return ((*entry, "--verify", *target),)


def recovery_runbook(
    *,
    database: str,
    change_ref: str,
    artifact_root: Path,
    receipt_status: str | None,
) -> tuple[str, ...]:
    """설명 한 줄 + **실행 가능한 다음 명령**들."""

    if receipt_status == "COMMITTED":
        note = "COMMITTED receipt가 이미 있다. **승격하지 않는다.**"
    elif receipt_status == "STARTED":
        note = "STARTED receipt가 남았다. 승격이 live 실측을 함께 확인한다."
    elif receipt_status == RECEIPT_UNTRUSTED:
        note = "receipt 계약·identity가 어긋난다. 상태 근거로 쓰지 않는다."
    else:
        note = f"receipt를 찾을 수 없다({artifact_root}). 적용 여부를 먼저 확인한다."
    commands = recovery_commands(
        database=database,
        change_ref=change_ref,
        artifact_root=artifact_root,
        receipt_status=receipt_status,
    )
    return (note, *(format_command(argv) for argv in commands))


def read_receipt_status(
    artifact_root: Path | None, database: str, change_ref: str | None
) -> str | None:
    """복구 안내에 쓸 receipt 상태. 읽을 수 없으면 `None`.

    **`status` 문자열만 보지 않는다**(구현리뷰 15차 권장 1). 손상된 receipt나 다른
    target의 receipt가 같은 이름으로 놓이면 잘못된 절차를 고른다. 전체 계약과
    identity를 통과한 것만 상태 근거로 쓰고, 나머지는 `UNTRUSTED`로 표시한다.
    """

    if artifact_root is None or not change_ref:
        return None
    try:
        payload = read_artifact(artifact_root / receipt_name(database, change_ref))
    except ReferenceV5Error:
        return None
    try:
        assert_receipt_contract(payload)
    except ReferenceV5Error:
        return RECEIPT_UNTRUSTED
    if payload["database"] != database or payload["change_ref"] != change_ref:
        return RECEIPT_UNTRUSTED
    status = payload["status"]
    return status if isinstance(status, str) else RECEIPT_UNTRUSTED


def read_row_counts(execute: Any) -> dict[str, int]:
    """postcheck·marker가 쓰는 행 수를 한 번에 읽는다."""

    return {
        "view_rows": int(
            execute(f"SELECT count(*) AS n FROM public.{ALARM_VIEW}")[0]["n"]
        ),
        "r03_rows": int(
            execute(f"SELECT count(*) AS n FROM public.{R03_TABLE}")[0]["n"]
        ),
        "action_rows": int(
            execute("SELECT count(*) AS n FROM public.action_history")[0]["n"]
        ),
    }


def read_successor_pre_state(execute: Any) -> dict[str, Any]:
    """파괴적 DDL 직전에 구 형상을 다시 읽는다."""

    return {
        "r03_columns": execute(R03_COLUMNS_SQL, {"table": R03_TABLE}),
        "r03_constraints": execute(R03_CONSTRAINTS_SQL, None),
        "view_definition": execute(VIEW_DEFINITION_SQL, {"view": ALARM_VIEW})[0][
            "definition"
        ],
        "view_security": {
            str(row["relname"]): row
            for row in execute(RELATION_SECURITY_SQL, {"names": list(OWNED_BY_CM31)})
        }[ALARM_VIEW],
        "r03_rows": int(
            execute(f"SELECT count(*) AS n FROM public.{R03_TABLE}")[0]["n"]
        ),
        "dependents": execute(DEPENDENTS_SQL, {"names": list(OWNED_BY_CM31)}),
        "triggers": execute(TRIGGERS_SQL, {"names": list(OWNED_BY_CM31)}),
    }


VIEW_BRANCH_SQL = (
    "SELECT source, count(*) AS n, "
    "count(*) FILTER (WHERE lot_hist_id IS NULL) AS null_owner "
    "FROM public.v_alarm_event GROUP BY source ORDER BY source"
)
VIEW_DUPLICATE_SQL = (
    "SELECT count(*) AS n FROM ("
    "SELECT source, alarm_id FROM public.v_alarm_event "
    "GROUP BY source, alarm_id HAVING count(*) > 1) AS d"
)


def assert_view_branches(
    execute: Any,
    *,
    r03_rows: int,
    view_rows: int,
    require_final_dataset: bool = True,
) -> None:
    """branch 합·null owner·`(source, alarm_id)` 중복을 본다(계획 §12-3).

    **null owner는 data 속성이다.** `h.wafer_id = a.wafer`가 풀리려면 alarm의 `wafer`가
    최종 dataset의 wafer_id 문자열(`W00000001`)이어야 한다. CM-2.6 fixture는 legacy 값
    (`'1'`)을 final 컬럼 타입에 담고 있어 전부 NULL로 남는다 — 그래서 189/192와 같은
    gate에 둔다. branch 합과 중복 0은 구조 속성이라 언제나 본다.
    """

    rows = {str(row["source"]): row for row in execute(VIEW_BRANCH_SQL)}
    if set(rows) - set(VIEW_SOURCES):
        raise ReferenceV5Error("VIEW_BRANCH_UNKNOWN", EXIT_MISMATCH)
    if sum(int(row["n"]) for row in rows.values()) != view_rows:
        raise ReferenceV5Error("VIEW_BRANCH_MISMATCH", EXIT_MISMATCH)
    if int(rows.get("R03", {"n": 0})["n"]) != r03_rows:
        raise ReferenceV5Error("VIEW_BRANCH_MISMATCH", EXIT_MISMATCH)
    if int(execute(VIEW_DUPLICATE_SQL)[0]["n"]) != 0:
        raise ReferenceV5Error("VIEW_ALARM_REF_DUPLICATE", EXIT_MISMATCH)
    if not require_final_dataset:
        return
    # **분포까지 exact다.** 합계만 보면 `TRACE 137 / SUMMARY 52`도 통과한다 —
    # 저장 알람이 branch 사이에서 옮겨간 상태다(구현리뷰 10차 필수 4).
    expected = {**BRANCH_ROWS_EMPTY, "R03": r03_rows}
    observed = {source: int(rows.get(source, {"n": 0})["n"]) for source in VIEW_SOURCES}
    if observed != expected:
        raise ReferenceV5Error("VIEW_BRANCH_MISMATCH", EXIT_MISMATCH)
    if any(int(row["null_owner"]) for row in rows.values()):
        raise ReferenceV5Error("VIEW_NULL_OWNER_KEY", EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# mode별 handler (계획 §7.1 · 구현리뷰 10차 필수 2)
# ---------------------------------------------------------------------------


def preflight_target(
    execute: Any, *, database: str, confirm_target: str | None
) -> dict[str, Any]:
    """read-only Gate 0 bundle. **쓰기 0.**"""

    profile = assert_target_allowed(database, confirm_target=confirm_target)
    state = read_target_state(execute, profile=profile)
    counts = read_row_counts(execute)
    return {
        "database": database,
        "profile": profile,
        "state": state["state"],
        "route": state["route"],
        "excluded_projection_sha256": state["excluded_projection_sha256"],
        "view_rows": counts["view_rows"],
        "r03_rows": counts["r03_rows"],
        "action_history_rows": counts["action_rows"],
    }


def _security_mode_for(route: str) -> str:
    """route가 security mode를 정한다.

    base-only canonical 적용은 creator owner와 `relacl IS NULL`이 정상이다. 그 target을
    `successor`로 검증하면 `SECURITY_ACL_MISSING`으로 실패한다(구현리뷰 11차 필수 3).
    """

    if route not in MIGRATION_ROUTES:
        raise ReferenceV5Error("MIGRATION_ROUTE_NOT_ALLOWED", EXIT_USAGE)
    return "successor" if route == "successor" else "base_only"


def verify_target(
    execute: Any,
    *,
    database: str,
    confirm_target: str | None,
    marker: Mapping[str, Any] | None = None,
    route: str | None = None,
    require_final_dataset: bool = True,
) -> dict[str, Any]:
    """final state를 전면 재검증한다. `marker`가 있으면 no-op 판정까지 한다.

    **재적용이 아니다.** 이미 final인 DB에 `apply`를 다시 부르면
    `TARGET_STATE_UNSUPPORTED`로 끝나는데, 그건 "정상 no-op"과 구분되지 않는다
    (구현리뷰 10차 필수 2).

    `route`는 marker/receipt에서 온다. 없으면 공용 pre-state인 `successor`로 본다.
    """

    profile = assert_target_allowed(database, confirm_target=confirm_target)
    resolved_route = route or (marker or {}).get("route") or "successor"
    mode = _security_mode_for(str(resolved_route))
    # 검사 중 live가 바뀌지 않도록 읽는 것들을 `SHARE`로 묶는다(구현리뷰 11차 필수 3).
    #
    # **`NOWAIT`이라 기다리지 않고 session 설정도 남기지 않는다.** 이전에는 `SET`으로
    # `lock_timeout`을 걸어 caller connection에 남겼고, 모든 예외를 `TARGET_BUSY`로
    # 바꿔 권한 없음·table 누락까지 잠금 경쟁으로 위장했다(구현리뷰 13차 필수 1).
    for statement in share_lock_statements(profile, nowait=True):
        try:
            execute(statement)
        except Exception as exc:  # noqa: BLE001 - driver 예외 계층을 흡수한다
            if is_lock_contention(exc):
                raise ReferenceV5Error("TARGET_BUSY", EXIT_CONFIRM_REQUIRED) from exc
            # 잠금 경쟁이 아니면 원인을 숨기지 않는다.
            raise
    state = read_target_state(execute, profile=profile)
    if state["state"] != "V5_REFERENCE_FINAL":
        raise ReferenceV5Error("TARGET_STATE_UNSUPPORTED", EXIT_CONFIRM_REQUIRED)
    live = read_live_schema(execute)
    schema_signature, security_signature = live_signatures(live, mode=mode)
    counts = read_row_counts(execute)
    phase = assert_postcheck(
        live,
        profile=profile,
        mode=mode,
        view_rows=counts["view_rows"],
        r03_rows=counts["r03_rows"],
        action_rows=counts["action_rows"],
        require_final_dataset=require_final_dataset,
    )
    assert_view_branches(
        execute,
        r03_rows=counts["r03_rows"],
        view_rows=counts["view_rows"],
        require_final_dataset=require_final_dataset,
    )
    result = {
        "database": database,
        "profile": profile,
        "route": resolved_route,
        "state": state["state"],
        "data_phase": phase,
        "schema_signature_sha256": schema_signature,
        "security_signature_sha256": security_signature,
        "excluded_projection_sha256": state["excluded_projection_sha256"],
        "noop": False,
        **counts,
    }
    if marker is not None:
        assert_marker_allows_noop(
            marker,
            schema_signature=schema_signature,
            security_signature=security_signature,
            excluded_projection=state["excluded_projection_sha256"],
        )
        result["noop"] = True
    return result


def promote_receipt(
    execute: Any,
    *,
    database: str,
    confirm_target: str | None,
    change_ref: str,
    artifact_root: Path,
    confirm_recovery: bool,
    require_final_dataset: bool = True,
    clock: Any = None,
) -> dict[str, Any]:
    """`STARTED` receipt를 live 실측으로 검증해 `COMMITTED`로 승격한다.

    commit 응답이 유실됐거나 commit 뒤 receipt 쓰기가 실패한 상태를 복구하는
    **유일한 경로**다. DB에 record를 남기지 않으므로 근거는 셋뿐이다.

    1. exact `STARTED` receipt — target·change_ref·bundle이 모두 맞아야 한다
    2. 새 connection 전체 `verify` — live가 정말 final이어야 한다
    3. operator의 명시 확인 — 자동으로 승격하지 않는다

    이게 없으면 사람이 JSON을 손으로 위조하는 수밖에 없고, 오타 하나로 잘못된 marker가
    정본이 된다(구현리뷰 13차 필수 2).
    """

    assert_target_allowed(database, confirm_target=confirm_target)
    if not confirm_recovery:
        raise ReferenceV5Error("RECOVERY_CONFIRM_REQUIRED", EXIT_CONFIRM_REQUIRED)
    path = artifact_root / receipt_name(database, change_ref)
    if not path.exists():
        raise ReferenceV5Error("RECEIPT_MISSING", EXIT_CONFIRM_REQUIRED)
    receipt = read_artifact(path)
    assert_receipt_contract(receipt)
    if receipt["status"] != "STARTED":
        # 이미 결론난 receipt는 승격 대상이 아니다.
        raise ReferenceV5Error("RECEIPT_STATUS_NOT_ALLOWED", EXIT_USAGE)
    if receipt["database"] != database or receipt["change_ref"] != change_ref:
        raise ReferenceV5Error("RECEIPT_TARGET_MISMATCH", EXIT_MISMATCH)

    verified = verify_target(
        execute,
        database=database,
        confirm_target=confirm_target,
        route=str(receipt["route"]),
        require_final_dataset=require_final_dataset,
    )
    promoted = {
        **receipt,
        "status": "COMMITTED",
        "recorded_at": _now(clock),
        **{key: verified[key] for key in POST_COMMIT_IDENTITY_KEYS},
    }
    assert_receipt_contract(promoted)
    write_artifact(path, promoted)
    return promoted


def recover_marker(
    execute: Any,
    *,
    database: str,
    confirm_target: str | None,
    change_ref: str,
    artifact_root: Path,
    require_final_dataset: bool = True,
    clock: Any = None,
) -> dict[str, Any]:
    """유실된 marker를 다시 만든다.

    exact `COMMITTED` receipt와 live 실측이 모두 일치할 때만이다(계획 §10).
    **`verify_target()`과 같은 전체 postcheck를 거친다.** schema/security/excluded만
    보면 action row·phase·null owner·중복이 잘못된 DB도 복구된다(구현리뷰 11차 필수 3).
    """

    assert_target_allowed(database, confirm_target=confirm_target)
    marker_path = artifact_root / marker_name(database, change_ref)
    if marker_path.exists():
        # 이미 있으면 복구가 아니다. 덮어쓰면 기존 정본을 잃는다.
        raise ReferenceV5Error("MARKER_ALREADY_PRESENT", EXIT_USAGE)
    receipt = read_artifact(artifact_root / receipt_name(database, change_ref))
    if receipt.get("database") != database:
        raise ReferenceV5Error("RECEIPT_TARGET_MISMATCH", EXIT_MISMATCH)
    verified = verify_target(
        execute,
        database=database,
        confirm_target=confirm_target,
        route=str(receipt.get("route") or "successor"),
        require_final_dataset=require_final_dataset,
    )
    live = read_live_schema(execute)
    assert_recovery_is_allowed(
        receipt,
        schema_signature=verified["schema_signature_sha256"],
        security_signature=verified["security_signature_sha256"],
        excluded_projection=verified["excluded_projection_sha256"],
        view_definition_sha256_value=view_definition_sha256(live["view_definition"]),
    )
    marker = {
        **receipt,
        "artifact_type": MARKER_ARTIFACT_TYPE,
        "view_definition_sha256": view_definition_sha256(live["view_definition"]),
        "view_rows": verified["view_rows"],
        "r03_rows": verified["r03_rows"],
        "action_history_rows": verified["action_rows"],
        "committed_at": _now(clock),
    }
    assert_marker_contract(marker)
    write_artifact(marker_path, marker)
    return marker


# ---------------------------------------------------------------------------
# CLI (계획 §7.1 · 구현리뷰 9차 필수 1)
# ---------------------------------------------------------------------------


def build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="apply_reference_extensions_v5",
        description="V5-CM-3.1 final reference extension runner",
    )
    for mode in CLI_MODES:
        parser.add_argument(f"--{mode}", action="store_true")
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--artifact-root")
    parser.add_argument(
        "--confirm-recovery",
        action="store_true",
        help="`promote-receipt` 전용. STARTED receipt를 COMMITTED로 승격한다.",
    )
    parser.add_argument(
        "--allow-non-final-dataset",
        action="store_true",
        help="격리 rehearsal 전용. 공용 target에는 쓰지 않는다.",
    )
    return parser


def selected_modes(args: Any) -> list[str]:
    return [mode for mode in CLI_MODES if getattr(args, mode.replace("-", "_"))]


class _SessionOwner:
    """connection과 engine을 함께 닫는다. 둘 중 하나만 닫으면 pool이 남는다."""

    def __init__(self, connection: Any, engine: Any) -> None:
        self._connection = connection
        self._engine = engine

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            self._engine.dispose()


def open_session(database: str) -> Any:
    """production adapter. **여기서만 driver를 고른다.**

    순수 계약 부분은 `execute(sql, params)`만 받는다. 이 함수는 진입점에서만 불린다.

    `BootstrapTarget`에는 URL 필드가 없고 `create_url()`만 있다 — 전에는 없는 속성을
    읽어 첫 연결 전에 `AttributeError`로 죽었다(구현리뷰 11차 필수 1).
    """

    import db_target
    from sqlalchemy import create_engine, text

    try:
        target = db_target.load_bootstrap_target(database=database)
        url = target.create_url()
        db_target.validate_url_components(url, target)
    except db_target.TargetValidationError as exc:
        raise ReferenceV5Error("TARGET_ENV_INVALID", EXIT_USAGE) from exc

    engine = create_engine(url, future=True)
    try:
        connection = engine.connect()
    except Exception as exc:  # noqa: BLE001 - driver 예외 계층을 흡수한다
        engine.dispose()
        raise ReferenceV5Error("TARGET_CONNECT_FAILED", EXIT_USAGE) from exc

    owner = _SessionOwner(connection, engine)
    try:
        db_target.validate_connected_identity(connection, target)
        db_target.set_and_validate_public_search_path(connection)
    except BaseException as exc:
        owner.close()
        if isinstance(exc, db_target.TargetValidationError):
            raise ReferenceV5Error("TARGET_IDENTITY_MISMATCH", EXIT_USAGE) from exc
        raise

    def execute(sql: str, params: Any = None) -> list[dict[str, Any]]:
        result = connection.execute(text(_to_named_binds(sql)), params or {})
        if not result.returns_rows:
            return []
        return [dict(row) for row in result.mappings()]

    return owner, execute


_PSYCOPG_BIND = re.compile(r"%\((\w+)\)s")


def _to_named_binds(sql: str) -> str:
    """이 모듈의 `%(name)s`를 SQLAlchemy `:name`으로 옮긴다.

    계약 SQL은 psycopg 문법으로 쓰여 있고 container 회귀가 그대로 쓴다. adapter만
    변환한다 — 두 벌을 유지하면 반드시 갈라진다.
    """

    return _PSYCOPG_BIND.sub(r":\1", sql)


def run_mode(execute: Any, connection: Any, args: Any, mode: str) -> dict[str, Any]:
    """mode별 실제 handler. **아무 일도 하지 않는 mode는 없다.**"""

    require_final = not args.allow_non_final_dataset
    root = Path(args.artifact_root).expanduser() if args.artifact_root else None
    if mode == "preflight":
        return preflight_target(
            execute, database=args.database, confirm_target=args.confirm_target
        )
    if mode == "verify":
        marker = None
        if root is not None and args.change_ref:
            path = root / marker_name(args.database, args.change_ref)
            if path.exists():
                marker = read_artifact(path)
        return verify_target(
            execute,
            database=args.database,
            confirm_target=args.confirm_target,
            marker=marker,
            require_final_dataset=require_final,
        )
    if mode == "promote-receipt":
        return promote_receipt(
            execute,
            database=args.database,
            confirm_target=args.confirm_target,
            change_ref=args.change_ref,
            artifact_root=root,
            confirm_recovery=args.confirm_recovery,
            require_final_dataset=require_final,
        )
    if mode == "recover-marker":
        return recover_marker(
            execute,
            database=args.database,
            confirm_target=args.confirm_target,
            change_ref=args.change_ref,
            artifact_root=root,
        )
    return apply_to_target(
        execute,
        database=args.database,
        confirm_target=args.confirm_target,
        change_ref=args.change_ref,
        artifact_root=root,
        commit=connection.commit,
        rollback=connection.rollback,
        dry_run=mode == "rehearse",
        require_final_dataset=require_final,
    )


def main(argv: Sequence[str] | None = None, *, opener: Any = None) -> int:
    """진입점. **DB를 열기 전에** mode·target·artifact root를 판정한다.

    engine을 만든 뒤에 인자를 보면 잘못된 대상에 이미 붙은 뒤다(계획 §9.1).
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        mode = assert_single_mode(selected_modes(args))
        assert_target_allowed(args.database, confirm_target=args.confirm_target)
        root = Path(args.artifact_root).expanduser() if args.artifact_root else None
        if args.allow_non_final_dataset:
            assert_non_final_dataset_allowed(database=args.database, mode=mode)
        if mode in {"apply", "rehearse", "recover-marker", "promote-receipt"}:
            if not args.change_ref:
                raise ReferenceV5Error("CHANGE_REF_REQUIRED", EXIT_USAGE)
            _safe_component(args.change_ref)
            if not args.artifact_root:
                raise ReferenceV5Error("ARTIFACT_ROOT_REQUIRED", EXIT_USAGE)
            assert_outside_bootstrap_registry(Path(args.artifact_root).expanduser())
        # **session 열기도 같은 try 안이다.** 밖에 두면 `TargetValidationError`가
        # traceback으로 샌다(구현리뷰 11차 필수 1).
        connection, execute = (opener or open_session)(args.database)
    except ReferenceV5Error as error:
        print(error.reason_code, file=sys.stderr)
        return error.exit_code
    except Exception as error:
        # 연결 실패 메시지에는 host·port가 들어간다. 그대로 새면 안 된다.
        return report_unexpected(error)

    try:
        result = run_mode(execute, connection, args, mode)
    except ReferenceV5Error as error:
        print(error.reason_code, file=sys.stderr)
        if error.reason_code in RECOVERABLE_REASONS:
            # **실행 가능한 다음 명령을 낸다.** reason code만 던지면 절차를 알 수 없고,
            # 하나의 안내를 항상 내면 상태에 따라 그대로 실행했을 때 실패한다
            # (구현리뷰 13차 필수 2 · 14차 필수 1).
            for line in recovery_runbook(
                database=args.database,
                change_ref=args.change_ref or "",
                artifact_root=root if root is not None else Path("."),
                receipt_status=read_receipt_status(
                    root, args.database, args.change_ref
                ),
            ):
                print(line, file=sys.stderr)
        return error.exit_code
    except Exception as error:
        # 계약 밖 driver 예외. reason code만 내고 traceback을 내보내지 않는다.
        return report_unexpected(error)
    finally:
        # 어떤 결과에서도 connection·engine을 닫아 session lock을 최종 해제한다.
        with contextlib_suppress_all():
            connection.close()

    import json

    print(json.dumps({"mode": mode, **result}, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - 진입점
    sys.exit(main())
