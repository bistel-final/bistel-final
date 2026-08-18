"""V4-D-2.2·2.3 Text2SQL 안전 검증기.

LLM 이 생성한 SQL 을 실행 전에 검증한다. 계정 권한(kosa_readonly)이 1차
방어선이고 이 검증기는 2차 방어선이다. 수용 기준은 V4-D-2.1 fixture
(tests/unit/test_sql_validator.py)다.

방어 6종
    1. 쓰기·DDL 차단 — 단일 SELECT 문만 허용. CTE 내부·SELECT INTO 포함
    2. 다중 문장 차단 — 문장은 정확히 1개
    3. 비허용 객체 차단 — base 9 + reference 6 allowlist. 스코프 해석 기반
    4. 위험 함수 차단 — pg_sleep, pg_read_file, dblink 등
    5. 시스템 카탈로그 차단 — pg_catalog·information_schema, 무스키마 pg_* 포함
    6. 없는 컬럼 차단 — bootstrap manifest 의 컬럼 정의 기반

스코프 해석 (CTE 우회 차단의 핵심)
    테이블 참조가 CTE 인지 물리 테이블인지는 이름이 아니라 **스코프**로
    판정한다. sqlglot 의 scope 분석(build_scope)이 각 참조를 실제 소스로
    해석하므로, 서브쿼리 안 CTE 와 같은 이름의 바깥 테이블 참조를 CTE 로
    오인하는 경로가 원천적으로 없다. manifest 밖 신규 테이블도 동일하게
    물리 참조로 해석되어 allowlist 검사를 받는다.

fail-closed 원칙
    모르는 것과 실패한 것은 기본 거부다.
    - manifest 를 읽지 못하면(사유 불문) 컬럼 검증을 건너뛰지 않고 전체 거부
    - 테이블 위치의 함수 호출은 목록과 무관하게 전부 거부
    - 실제 물리 테이블과 겹치는 이름의 CTE 는 위장 시도로 보고 거부
    - 스코프 분석이 실패한 SQL 은 통과시키지 않고 거부

컬럼 검증이 DB 가 아니라 manifest 를 읽는 이유
    schema cache(V4-D-1.3)는 information_schema 조회에 실제 접속이 필요하다.
    검증기는 DB 없이도 결정론적으로 동작해야 테스트와 평가셋이 재현 가능하다.
    bootstrap manifest 는 실제 적재와 함께 검증된 컬럼 목록을 이미 담고
    있다. manifest 에 없는 객체(뷰 등)는 컬럼 검증을 건너뛰고 객체
    allowlist 만 적용한다.

예외 계약
    validate_sql 은 어떤 입력에도 예외를 던지지 않는다. 파싱 불가 SQL 도
    valid=False 와 reason 으로 응답한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import build_scope

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_MANIFEST_PATH = (
    REPOSITORY_ROOT / "infra" / "bootstrap" / "manifests" / "runtime.runtime_clean.json"
)

_DIALECT = "postgres"
_MAX_ROWS = 500

#: Text2SQL 이 조회할 수 있는 객체. base 9 table + reference 6종.
#: 감사로그(audit_log)·runtime 계열(agent_run 등)은 여기 없다 — 전용 API 소관.
ALLOWED_OBJECTS: frozenset[str] = frozenset(
    {
        # base 9
        "dim_parameter",
        "lot_history",
        "fdc_trace",
        "summary_data",
        "evaluation",
        "trace_alarm_history",
        "summary_alarm_history",
        "metrology",
        "action_history",
        # reference 6 (001_reference_extensions.sql)
        "r03_alarm_history",
        "v_alarm_event",
        "nl_query_log",
        "document_corpus",
        "document",
        "document_chunk",
    }
)

#: 실행 지연·파일 접근·외부 연결을 일으키는 함수. 소문자 비교.
DANGEROUS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_connect",
        "dblink_exec",
        "copy_from",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "set_config",
    }
)

#: 시스템 카탈로그 스키마. 무스키마 pg_* 접근도 search_path 로 해석되므로 차단.
_CATALOG_SCHEMAS: frozenset[str] = frozenset({"pg_catalog", "information_schema"})

#: 검증 단계 key. fixture 의 EXPECTED_CHECK_KEYS 와 일치해야 한다.
CHECK_KEYS: tuple[str, ...] = (
    "single_select",
    "allowed_objects",
    "column_allowlist",
    "no_catalog_access",
    "no_dangerous_function",
    "limit_enforced",
)


@dataclass(frozen=True)
class CheckResult:
    key: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """검증 결과. Tool 계약 {ok, ..., reason} 으로 그대로 옮길 수 있다."""

    valid: bool
    normalized_sql: str | None = None
    reason: str | None = None
    checks: tuple[CheckResult, ...] = field(default=())


@lru_cache(maxsize=1)
def _manifest_columns() -> dict[str, frozenset[str]] | None:
    """bootstrap manifest 에서 table -> 컬럼 집합을 읽는다.

    어떤 사유로든 읽지 못하면 None 을 반환하고 validate_sql 이 전체 검증을
    거부한다 (fail-closed). 특정 예외만 잡으면 목록 밖 예외(예: tables 가
    dict 가 아닐 때의 AttributeError)가 새어 나가 예외 계약이 깨지므로,
    여기서는 의도적으로 모든 예외를 실패로 취급한다.
    """
    try:
        payload = json.loads(RUNTIME_MANIFEST_PATH.read_text(encoding="utf-8"))
        tables = payload["tables"]
        columns = {
            str(name).lower(): frozenset(str(col).lower() for col in entry["columns"])
            for name, entry in tables.items()
            if isinstance(entry, dict) and isinstance(entry.get("columns"), list)
        }
    except Exception:
        return None
    return columns or None


def _fail(key: str, reason: str, passed_keys: set[str]) -> ValidationResult:
    checks = tuple(
        CheckResult(key=check_key, passed=(check_key in passed_keys))
        for check_key in CHECK_KEYS
    )
    return ValidationResult(valid=False, reason=reason, checks=checks)


def _collect_cte_names(statement: exp.Expression) -> set[str]:
    return {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _collect_alias_names(statement: exp.Expression) -> set[str]:
    """SELECT 출력 별칭. ORDER BY alarm_cnt 처럼 별칭 참조를 물리 컬럼으로
    오인하면 안 된다."""
    names: set[str] = set()
    for alias in statement.find_all(exp.Alias):
        if alias.alias:
            names.add(alias.alias.lower())
    return names


def _find_table_function(statement: exp.Expression) -> str | None:
    """FROM 절의 함수 호출을 찾는다. 있으면 함수 이름을 돌려준다.

    dblink 처럼 목록에 있는 함수는 위험 함수 검사로도 잡히지만, 목록에
    없는 테이블 함수가 기본 허용되는 fail-open 을 막으려면 테이블 위치의
    함수 호출 자체를 전부 거부해야 한다.
    """
    for table in statement.find_all(exp.Table):
        this = table.this
        if this is None or isinstance(this, exp.Identifier):
            continue
        if isinstance(this, exp.Anonymous):
            return str(this.this).lower()
        if isinstance(this, exp.Func):
            return this.sql_name().lower()
        return type(this).__name__.lower()
    return None


def _resolve_physical_tables(statement: exp.Expression) -> list[exp.Table] | None:
    """스코프 분석으로 물리 테이블 참조만 해석한다.

    이름 매칭이 아니라 각 스코프의 source 해석을 쓴다. source 가 exp.Table
    이면 물리 테이블이고, Scope(CTE·서브쿼리)면 파생 소스다. 서브쿼리 안
    CTE 와 같은 이름의 바깥 테이블 참조도 각자의 스코프에서 따로 해석되므로
    오인이 없다.

    분석이 실패하면 None 을 반환하고 호출부가 거부한다 (fail-closed).
    """
    try:
        root = build_scope(statement)
        if root is None:
            return None
        tables: list[exp.Table] = []
        for scope in root.traverse():
            for _name, source in scope.sources.items():
                if isinstance(source, exp.Table):
                    tables.append(source)
        return tables
    except Exception:
        return None


def validate_sql(sql: str) -> ValidationResult:
    """SQL 한 건을 검증한다. 어떤 입력에도 예외를 던지지 않는다."""
    passed: set[str] = set()

    # ── 1·2. 파싱과 문장 수 ─────────────────────────────────────────────
    try:
        statements = [
            s for s in sqlglot.parse(sql or "", read=_DIALECT) if s is not None
        ]
    except Exception:
        return _fail(
            "single_select",
            "SQL 구문을 해석할 수 없다. 문장을 확인하라.",
            passed,
        )

    if len(statements) == 0:
        return _fail("single_select", "SQL 문장이 없다.", passed)

    if len(statements) > 1:
        return _fail(
            "single_select",
            f"다중 문장은 허용되지 않는다: {len(statements)}개 문장이 발견됐다.",
            passed,
        )

    statement = statements[0]

    # ── 1. 단일 SELECT 강제 ─────────────────────────────────────────────
    write_nodes = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.TruncateTable,
        exp.Merge,
    )
    if not isinstance(statement, exp.Select):
        return _fail(
            "single_select",
            "SELECT 문만 허용된다. 쓰기·DDL 구문은 실행할 수 없다.",
            passed,
        )
    # CTE 내부 쓰기, SELECT INTO 까지 재귀로 잡는다.
    if statement.args.get("into") is not None or any(
        statement.find(node_type) for node_type in write_nodes
    ):
        return _fail(
            "single_select",
            "SELECT 문만 허용된다. 쓰기·DDL 구문은 실행할 수 없다.",
            passed,
        )
    passed.add("single_select")

    # ── 컬럼 정의 로드 (fail-closed) ───────────────────────────────────
    known_columns = _manifest_columns()
    if known_columns is None:
        return _fail(
            "column_allowlist",
            "컬럼 정의(bootstrap manifest)를 읽을 수 없어 검증을 수행할 수 "
            "없다. corrected build 상태를 확인하라.",
            passed,
        )

    cte_names = _collect_cte_names(statement)

    # ── CTE 이름 위장 차단 ─────────────────────────────────────────────
    # 물리 테이블(allowlist·manifest)과 겹치는 이름의 CTE 는 사람이 결과를
    # 읽을 때 혼동을 일으키므로 거부한다. 참조 해석 자체는 아래 스코프
    # 분석이 담당하며, manifest 밖 신규 테이블은 스코프 해석 결과
    # allowlist 검사에서 걸린다.
    physical_universe = ALLOWED_OBJECTS | set(known_columns)
    shadowing = {
        name
        for name in cte_names
        if name in physical_universe or name.startswith("pg_")
    }
    if shadowing:
        blocked = sorted(shadowing)[0]
        return _fail(
            "allowed_objects",
            f"실제 테이블과 겹치는 이름의 CTE 는 허용되지 않는다: {blocked}",
            passed,
        )

    # ── 테이블 위치 함수 차단 ──────────────────────────────────────────
    # 목록 기반 차단은 목록 밖 함수가 기본 허용되는 fail-open 구조다.
    # FROM 절의 함수 호출은 전부 거부한다.
    table_function = _find_table_function(statement)
    if table_function is not None:
        return _fail(
            "allowed_objects",
            f"테이블 위치의 함수 호출은 허용되지 않는다: {table_function}",
            passed,
        )

    # ── 스코프 해석 (fail-closed) ──────────────────────────────────────
    tables = _resolve_physical_tables(statement)
    if tables is None:
        return _fail(
            "allowed_objects",
            "SQL 참조 구조를 해석할 수 없어 허용 여부를 판정할 수 없다.",
            passed,
        )

    # ── 5. 시스템 카탈로그 (allowlist 보다 먼저 — 사유를 구분한다) ─────
    for table in tables:
        schema = (table.db or "").lower()
        name = table.name.lower()
        if schema in _CATALOG_SCHEMAS or (not schema and name.startswith("pg_")):
            return _fail(
                "no_catalog_access",
                f"시스템 카탈로그 접근은 차단된다: {name}",
                passed,
            )
    passed.add("no_catalog_access")

    # ── 4. 위험 함수 ────────────────────────────────────────────────────
    for node in statement.walk():
        func_name: str | None = None
        if isinstance(node, exp.Anonymous):
            func_name = str(node.this).lower()
        elif isinstance(node, exp.Func):
            func_name = node.sql_name().lower()
        if func_name and func_name in DANGEROUS_FUNCTIONS:
            return _fail(
                "no_dangerous_function",
                f"위험 함수 호출은 차단된다: {func_name}",
                passed,
            )
    passed.add("no_dangerous_function")

    # ── 3. 객체 allowlist (스코프 해석 결과 기반) ──────────────────────
    alias_to_table: dict[str, str] = {}
    for table in tables:
        catalog = (table.catalog or "").lower()
        schema = (table.db or "").lower()
        name = table.name.lower()

        # db+name 정규화: public 외 스키마·타 DB 참조는 이름과 무관하게 차단.
        if catalog or (schema and schema != "public"):
            qualified = ".".join(part for part in (catalog, schema, name) if part)
            return _fail(
                "allowed_objects",
                f"허용되지 않은 객체다: {qualified}",
                passed,
            )

        if name not in ALLOWED_OBJECTS:
            return _fail(
                "allowed_objects",
                f"허용되지 않은 객체다: {name}",
                passed,
            )

        alias_to_table[(table.alias_or_name or name).lower()] = name
    passed.add("allowed_objects")

    # ── 6. 컬럼 allowlist (manifest 기반) ──────────────────────────────
    alias_names = _collect_alias_names(statement)
    physical_names = set(alias_to_table.values())

    for column in statement.find_all(exp.Column):
        col_name = column.name.lower()
        qualifier = (column.table or "").lower()

        if qualifier:
            target = alias_to_table.get(qualifier)
            if target is None:
                # CTE·파생 테이블 별칭. 물리 컬럼 검증 대상이 아니다.
                continue
            columns = known_columns.get(target)
            if columns is not None and col_name not in columns:
                return _fail(
                    "column_allowlist",
                    f"존재하지 않는 컬럼이다: {target}.{col_name}",
                    passed,
                )
        else:
            if col_name in alias_names:
                continue
            candidates = [
                known_columns[name] for name in physical_names if name in known_columns
            ]
            # 참조 테이블 전부의 컬럼을 알 때만 판정한다. 하나라도 모르면
            # (뷰 등) 오탐을 피하기 위해 건너뛴다.
            if (
                candidates
                and len(candidates) == len(physical_names)
                and all(col_name not in columns for columns in candidates)
            ):
                return _fail(
                    "column_allowlist",
                    f"존재하지 않는 컬럼이다: {col_name}",
                    passed,
                )
    passed.add("column_allowlist")

    # ── LIMIT 주입·축소·유지 (V4-D-2.3) ────────────────────────────────
    limit_node = statement.args.get("limit")
    if limit_node is None:
        statement = statement.limit(_MAX_ROWS)
    else:
        try:
            current = int(limit_node.expression.this)
        except (AttributeError, TypeError, ValueError):
            statement = statement.limit(_MAX_ROWS)
        else:
            if current > _MAX_ROWS:
                statement = statement.limit(_MAX_ROWS)
    passed.add("limit_enforced")

    # 변환 결과를 통째로 재직렬화한다. normalized_sql 이 실행 대상이다.
    normalized = statement.sql(dialect=_DIALECT)

    checks = tuple(CheckResult(key=key, passed=True) for key in CHECK_KEYS)
    return ValidationResult(valid=True, normalized_sql=normalized, checks=checks)
