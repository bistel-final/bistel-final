"""action/severity pair guard successor runner (`V5-CM-3.3`).

`002_agent_runtime_clean.sql`의 익명 CHECK를 명명된 완전 pair CHECK로 **원자 교체**하고
`runtime_guarded` stage의 적용 증적을 발급한다.

## 이름 문제가 아니다

PostgreSQL CHECK는 결과가 **FALSE일 때만** 거부한다. 익명 CHECK는 반쪽 NULL에서 식
전체가 NULL이 되어 통과한다 — PostgreSQL 16 실측에서 16조합 중 **10건**이 수락됐다
(기대 4건). 반쪽 NULL 6종이 전부 구멍이다.

`V5-CM-3.2` 구현보고 §4.3의 "이름이 없을 뿐 계약은 산다"는 오류이며 이 Task가
역사 정정한다.

## 왜 002를 고치지 않는가

`002`는 공용 두 Runtime DB에 이미 적용됐고 `V5-CM-3.2`가 그 상태를
`agent_runtime_final` marker로 증명했다. 파일을 고치면 `migration_sha256`이 바뀌어 그
marker 2본이 무효가 된다. successor로 더한다 — 기존 것을 깨지 않고 쌓는다.

```text
runtime_clean                              runtime_guarded
  v5_001_reference_extensions_final    →     + 003_agent_run_severity_pair
  002_agent_runtime_clean                    + ck_agent_run_action_severity_pair
  agent_runtime_final marker  (불변)         + agent_severity_guard_final marker
```

## 두 matrix는 목적이 다르다

- **apply-time** — 새 CHECK를 추가한 **같은 transaction 안**에서 도는
  **commit 승인 Gate**다. 실패하면 DDL째 rollback된다.
- **`--verify-matrix`** — commit·`--verify` 뒤 **별도 transaction·새 connection**에서
  1회 도는 독립 재검증이다. WBS "배포 후" 증적이다.

`--verify-matrix`는 tracked marker를 다시 쓰지 않는다. 배포 후 재검증이 증명서를
갱신하면 marker가 무엇을 증명하는지 흐려진다.

## 공용 target 1개당 허용 mutation

| 구분 | 실행 | 영구 영향 |
|---|---|---|
| DDL | drop + add를 한 transaction에서 1회 | 새 CHECK로 원자 교체 |
| apply 승인 matrix | INSERT 시도 16건 | savepoint rollback · row 변화 0 |
| 배포 후 독립 matrix | INSERT 시도 16건 | savepoint + 전체 rollback · row 변화 0 |
| **합계** | INSERT 시도 32건 | **영구 DML row 변화 0건** |
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

import apply_agent_runtime as agent_runtime
import manifest_v3
from bootstrap_common import (
    ReferenceExtensionError,
    _canonical_hash,
    _engine_for,
    _result_rows,
    _single_row,
    _timezone_text,
    acquire_advisory_lock,
    validate_change_reference,
)
from db_target import (
    ALLOWED_DATABASES,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
)
from dotenv import load_dotenv
from manifest_v3 import (
    VerificationError,
    atomic_save_json,
    resolve_bootstrap_manifest_path,
    scan_for_sensitive_values,
)
from mutation_runtime import (
    MutationRuntimeError,
    prepare_transaction,
    resolve_exclusive_mode,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"
)
MARKER_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
REPORT_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "reports"

RUNTIME_DATABASES = agent_runtime.RUNTIME_DATABASES
RUNTIME_PROFILE = agent_runtime.RUNTIME_PROFILE

#: successor stage. `runtime_clean`은 predecessor로 registry에 남는다.
GUARDED_STAGE = "runtime_guarded"
MIGRATION_ID = "003_agent_run_severity_pair"
EXPECTED_LINEAGE = (
    "v5_001_reference_extensions_final",
    "002_agent_runtime_clean",
    MIGRATION_ID,
)

PREDECESSOR_CONSTRAINT = "agent_run_check1"
GUARD_CONSTRAINT = "ck_agent_run_action_severity_pair"
GUARD_TABLE = "agent_run"

FINAL_ARTIFACT_TYPE = "agent_severity_guard_final"
FINAL_MARKER_FORMAT_VERSION = 1
TASK_ID = "V5-CM-3.3"

EXIT_OK = 0
EXIT_MISMATCH = 1


class SeverityGuardError(RuntimeError):
    exit_code = 2
    default_reason_code = "CONTRACT_INVALID"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code or self.default_reason_code


class SeverityGuardStateError(SeverityGuardError):
    exit_code = 3
    default_reason_code = "SCHEMA_STATE_INVALID"


class SeverityGuardArtifactError(SeverityGuardError):
    exit_code = 5
    default_reason_code = "ARTIFACT_INVALID"


# ---------------------------------------------------------------------------
# 16조합 진리표 — 계약의 단일 출처
# ---------------------------------------------------------------------------

ACTIONS: tuple[str | None, ...] = (None, "MONITORING", "WARNING", "EQP_HOLD")
SEVERITIES: tuple[str | None, ...] = (None, "LOW", "MEDIUM", "HIGH")

#: 수락되어야 하는 **정확히 4조합**. 설계서 §3.4 표와 같다.
ACCEPTED_PAIRS: frozenset[tuple[str | None, str | None]] = frozenset(
    {
        (None, None),
        ("MONITORING", "LOW"),
        ("WARNING", "MEDIUM"),
        ("EQP_HOLD", "HIGH"),
    }
)


def truth_table() -> tuple[tuple[str | None, str | None, bool], ...]:
    """4×4 전수. `(action, severity, 수락되어야 하는가)`."""

    return tuple(
        (action, severity, (action, severity) in ACCEPTED_PAIRS)
        for action in ACTIONS
        for severity in SEVERITIES
    )


def expected_counts() -> tuple[int, int]:
    """`(수락, 거부)` = `(4, 12)`."""

    table = truth_table()
    accepted = sum(1 for *_, ok in table if ok)
    return accepted, len(table) - accepted


# ---------------------------------------------------------------------------
# SQL 계약
# ---------------------------------------------------------------------------

#: 어느 문장에도 있으면 안 되는 것.
#:
#: `BEGIN`·`COMMIT`은 여기에 없다 — DO guard의 `BEGIN`은 **PL/pgSQL 블록 키워드**이지
#: transaction 문장이 아니다. transaction 경계는 `_assert_alter_surface()`가 별도로
#: 본다(ALTER 문장 안의 `COMMIT`은 `ALTER_FORBIDDEN_SQL`이 잡는다).
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP\s+TABLE|CREATE\s+TABLE|GRANT|REVOKE"
    r"|ALTER\s+ROLE|CREATE\s+ROLE|NOT\s+VALID)\b",
    re.I,
)

#: ALTER 문장에만 추가로 금지되는 것. DO 블록과 달리 여기엔 transaction 제어가
#: 들어올 이유가 없다.
ALTER_FORBIDDEN_SQL = re.compile(r"\b(BEGIN|COMMIT|ROLLBACK)\b", re.I)


#: predecessor CHECK의 **PostgreSQL 16 실측 정의**.
#:
#: 003의 `DO` guard가 이 값과 exact 비교한다. 이름과 `contype`만 보면 같은 이름의
#: `CHECK (true)`를 그대로 drop하고 지나간다 — 무엇을 교체했는지 모르는 상태가 된다
#: (구현리뷰 필수 4).
PREDECESSOR_DEFINITION_SQL = (
    "CHECK (action IS NULL AND severity IS NULL OR action::text = 'MONITORING'::text "
    "AND severity::text = 'LOW'::text OR action::text = 'WARNING'::text AND "
    "severity::text = 'MEDIUM'::text OR action::text = 'EQP_HOLD'::text AND "
    "severity::text = 'HIGH'::text)"
)


def load_and_validate_sql(path: Path = MIGRATION_PATH) -> tuple[str, list[str]]:
    """003 SQL을 읽고 **완화·확장 문장이 섞이지 않았는지** 본다.

    `NOT VALID` 금지가 핵심이다 — 쓰면 기존 row가 재검증을 건너뛰어 이 migration이
    막으려는 바로 그 구멍이 남는다.

    statement 수만 세지 않는다. 대상 schema·table·constraint 이름과 DO guard가
    predecessor 정의를 exact 비교하는지까지 본다(구현리뷰 필수 4).
    """

    sql = path.read_text(encoding="utf-8")
    statements = agent_runtime.split_sql_statements(sql)
    if len(statements) != 2:
        raise SeverityGuardError("003은 guard DO 1문과 ALTER 1문이어야 합니다")
    guard, alter = statements
    if not guard.upper().startswith("DO "):
        raise SeverityGuardError("003 첫 문장은 DO guard여야 합니다")
    if not alter.upper().startswith("ALTER TABLE"):
        raise SeverityGuardError("003 둘째 문장은 ALTER TABLE이어야 합니다")

    _assert_alter_surface(agent_runtime._strip_comments(alter))
    _assert_guard_surface(agent_runtime._strip_comments(guard))
    return sql, statements


def _assert_alter_surface(body: str) -> None:
    """ALTER 문장이 **정확히 그 table, 그 두 constraint**만 건드리는지 본다."""

    lowered = body.lower()
    if FORBIDDEN_SQL.search(body) or ALTER_FORBIDDEN_SQL.search(body):
        raise SeverityGuardError("003에 허용되지 않은 문장이 있습니다")
    if f"alter table public.{GUARD_TABLE}" not in lowered:
        # schema를 생략하면 `search_path`에 따라 다른 table을 칠 수 있다.
        raise SeverityGuardError(f"003은 public.{GUARD_TABLE}만 대상으로 합니다")
    for required in (
        f"drop constraint {PREDECESSOR_CONSTRAINT}",
        f"add constraint {GUARD_CONSTRAINT}",
    ):
        if required not in lowered:
            raise SeverityGuardError(f"003에 {required}가 없습니다")
    if "if exists" in lowered or "if not exists" in lowered:
        # drift를 숨기고 통과시키는 완화다.
        raise SeverityGuardError("003은 IF (NOT) EXISTS를 쓰지 않습니다")
    # DROP·ADD가 **한 문장**이어야 사이에 창이 생기지 않는다.
    if lowered.count("alter table") != 1:
        raise SeverityGuardError("003 ALTER는 한 문장이어야 합니다")


def _assert_guard_surface(body: str) -> None:
    """DO guard가 **정의 exact 비교**를 담고 있는지 본다.

    이름만 보는 guard는 변조된 predecessor를 그대로 drop한다. 실측 정의 문자열이
    guard 안에 있어야 한다.
    """

    lowered = body.lower()
    if FORBIDDEN_SQL.search(body):
        raise SeverityGuardError("003 guard에 허용되지 않은 문장이 있습니다")
    if PREDECESSOR_DEFINITION_SQL.lower() not in lowered:
        raise SeverityGuardError("003 guard가 predecessor 정의를 대조하지 않습니다")
    for required in ("current_database()", "pg_get_constraintdef"):
        if required not in lowered:
            raise SeverityGuardError(f"003 guard에 {required}가 없습니다")


def migration_sha256(sql: str) -> str:
    return agent_runtime.migration_sha256(sql)


# ---------------------------------------------------------------------------
# catalog 판정
# ---------------------------------------------------------------------------

PAIR_CONSTRAINTS_SQL = """/* severity-guard:constraints */
SELECT con.conname AS constraint_name,
       pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = %s AND con.contype = 'c'
ORDER BY con.conname
"""

VIOLATION_SQL = f"""/* severity-guard:violations */
SELECT count(*) AS row_count FROM public.{GUARD_TABLE}
WHERE NOT (
    (action IS NULL AND severity IS NULL)
    OR (action IS NOT NULL AND severity IS NOT NULL
        AND ((action = 'MONITORING' AND severity = 'LOW')
          OR (action = 'WARNING' AND severity = 'MEDIUM')
          OR (action = 'EQP_HOLD' AND severity = 'HIGH')))
)
"""

ROW_COUNT_SQL = f"SELECT count(*) AS row_count FROM public.{GUARD_TABLE}"


@dataclass(frozen=True)
class GuardInspection:
    state: str
    predecessor: str | None
    guard: str | None
    schema_signature_sha256: str | None


#: runner가 판정하는 전체 상태. CLI reason code와 1:1이다.
GUARD_STATES = frozenset(
    {
        "BASELINE_MARKED",
        "GUARDED_UNMARKED",
        "UNPROVEN_GUARDED",
        "GUARDED_MARKED",
        "PARTIAL_OR_DRIFT",
        "DRIFT",
        "GUARDED_DRIFT",
        "BASELINE_DRIFT",
        "INVALID_EXISTING_ROWS",
    }
)


def _pair_definitions(connection: Any) -> dict[str, str]:
    rows = _result_rows(
        connection.exec_driver_sql(PAIR_CONSTRAINTS_SQL, (GUARD_TABLE,))
    )
    return {
        str(row["constraint_name"]): agent_runtime.normalize_catalog_text(
            row["definition"]
        )
        or ""
        for row in rows
    }


def inspect_guard(connection: Any) -> GuardInspection:
    """predecessor·successor 존재를 판정한다. **읽기만 한다.**

    둘 다 있거나 둘 다 없으면 `PARTIAL_OR_DRIFT`다 — 어느 쪽도 자동 보정하지 않지만
    "적용이 끊겼다"와 "누가 손댔다"를 한 이름으로 묶지 않는다.
    """

    definitions = _pair_definitions(connection)
    predecessor = definitions.get(PREDECESSOR_CONSTRAINT)
    guard = definitions.get(GUARD_CONSTRAINT)
    if (predecessor is None) == (guard is None):
        return GuardInspection("PARTIAL_OR_DRIFT", predecessor, guard, None)

    expected = agent_runtime.EXPECTED_CONSTRAINTS
    if predecessor is not None:
        if predecessor != expected[PREDECESSOR_CONSTRAINT].definition:
            return GuardInspection("DRIFT", predecessor, guard, None)
        state = "BASELINE_MARKED"
    else:
        if guard != GUARD_DEFINITION:
            return GuardInspection("DRIFT", predecessor, guard, None)
        state = "GUARDED_UNMARKED"
    # **signature 계산을 `agent_runtime`에 위임한다.**
    #
    # 여기서 `_canonical_hash(_json_safe(signature))`를 따로 계산하면 정본이 두
    # 갈래가 된다. marker는 이쪽 값을, `assert_guarded_marker_agrees()`의 live
    # 비교는 `postcheck_database()` 값을 쓰므로, 두 계산이 갈리는 순간 apply는
    # 성공하고 `--verify`가 곧바로 `DRIFT`로 떨어진다(팀 리뷰 필수 2).
    #
    # 지금 두 값이 같은 것은 `_json_safe()`가 이 payload에서 항등이기 때문이며
    # **우연에 걸려 있다.** `build_schema_signature()`가 `Decimal`·`datetime`·tuple을
    # 하나라도 담게 되면 갈린다.
    #
    # `GUARDED_CONSTRAINTS`를 `EXPECTED_CONSTRAINTS`에서 파생시킨 것과 같은 규칙이다.
    signature_sha256 = agent_runtime.schema_signature_sha256(
        connection,
        expected_constraints=(
            GUARDED_CONSTRAINTS
            if state == "GUARDED_UNMARKED"
            else agent_runtime.EXPECTED_CONSTRAINTS
        ),
    )
    return GuardInspection(state, predecessor, guard, signature_sha256)


#: 새 CHECK의 정규화 정의. **PostgreSQL 16 실측값이다.**
#:
#: `IS NOT NULL` 가드를 앞에 세워 3값 논리에 기대지 않는다 —
#: 실측 `TRUE 4 · FALSE 12 · NULL 0`.
GUARD_DEFINITION = (
    "check (action is null and severity is null or action is not null and "
    "severity is not null and (action = 'monitoring' and severity = 'low' or "
    "action = 'warning' and severity = 'medium' or action = 'eqp_hold' and "
    "severity = 'high'))"
)


#: `runtime_guarded`의 constraint allowlist.
#:
#: **CM-3.2 baseline에서 파생한다.** 손으로 복제하면 두 벌이 갈린다 — successor가
#: 바꾸는 것은 CHECK **하나**뿐이고 나머지 77개는 그대로다.
#:
#: 이것이 없으면 정상 guarded DB가 baseline allowlist 전수 비교에서 반드시 실패한다
#: (구현리뷰 필수 A). `-agent_run_check1 +ck_agent_run_action_severity_pair`가
#: 그대로 mismatch로 잡힌다.
def _guarded_constraints() -> Mapping[str, agent_runtime.ConstraintContract]:
    baseline = dict(agent_runtime.EXPECTED_CONSTRAINTS)
    predecessor = baseline.pop(PREDECESSOR_CONSTRAINT)
    baseline[GUARD_CONSTRAINT] = agent_runtime.ConstraintContract(
        table=predecessor.table,
        contype=predecessor.contype,
        columns=predecessor.columns,
        referenced_table=predecessor.referenced_table,
        referenced_columns=predecessor.referenced_columns,
        on_delete=predecessor.on_delete,
        on_update=predecessor.on_update,
        definition=GUARD_DEFINITION,
    )
    return MappingProxyType(baseline)


GUARDED_CONSTRAINTS: Mapping[str, agent_runtime.ConstraintContract] = (
    _guarded_constraints()
)


def violation_rows(connection: Any) -> int:
    row = _single_row(connection.exec_driver_sql(VIOLATION_SQL), label="pair violation")
    return int(row["row_count"])


def row_count(connection: Any) -> int:
    row = _single_row(connection.exec_driver_sql(ROW_COUNT_SQL), label="agent_run rows")
    return int(row["row_count"])


def classify_state(
    inspection: GuardInspection,
    marker: Mapping[str, Any] | None,
    *,
    receipt: Mapping[str, Any] | None = None,
) -> str:
    """물리 상태 + artifact를 **하나의 판정**으로 접는다.

    분기를 `run_apply()` 본문에 인라인으로 두면 판정 자체를 회귀가 잡지 못한다
    (`V5-CM-3.2` `classify_state()`와 같은 이유).
    """

    if inspection.state in {"PARTIAL_OR_DRIFT", "DRIFT"}:
        return inspection.state
    if inspection.state == "BASELINE_MARKED":
        return "BASELINE_MARKED"
    # guard가 서 있다.
    if marker is not None:
        return "GUARDED_MARKED"
    if receipt is None:
        # receipt 없는 수동 DDL을 조용히 축복하지 않는다.
        return "UNPROVEN_GUARDED"
    return "GUARDED_UNMARKED"


def _refuse(state: str) -> NoReturn:
    """자동 보정하지 않는 상태를 sanitized reason으로 끝낸다. **`NoReturn`이다.**"""

    messages = {
        # commit은 됐는데 marker만 없다 — `--recover-marker`로 닫는다.
        # 구현은 이 key가 없어 `KeyError`가 났다(구현리뷰 필수 B).
        "GUARDED_UNMARKED": "guard는 적용됐으나 marker가 없습니다",
        "PARTIAL_OR_DRIFT": "predecessor/successor 상태가 성립하지 않습니다",
        "DRIFT": "constraint 정의가 계약과 다릅니다",
        "UNPROVEN_GUARDED": "증명 receipt 없는 guard는 채택하지 않습니다",
        "GUARDED_DRIFT": "guarded 물리 계약이 성립하지 않습니다",
        "INVALID_EXISTING_ROWS": "기존 행이 pair 계약을 위반합니다",
    }
    # **없는 key를 `KeyError`로 터뜨리지 않는다.**
    #
    # 구현리뷰 필수 B가 `GUARDED_UNMARKED` 누락으로 `KeyError`가 난 건이었다.
    # 호출부가 실제 상태를 그대로 넘기게 바꾸면서(팀 리뷰 권고 1) 새 상태가 들어올
    # 여지가 생겼으므로 fallback을 명시한다. 상태 이름 자체는 계약이므로
    # `GUARD_STATES` 밖이면 그것을 먼저 알린다.
    if state not in GUARD_STATES:
        raise SeverityGuardStateError(
            "알 수 없는 guard 상태입니다", reason_code="CONTRACT_INVALID"
        )
    raise SeverityGuardStateError(
        messages.get(state, "guard 상태가 자동 보정 대상이 아닙니다"),
        reason_code=state,
    )


# ---------------------------------------------------------------------------
# 16조합 matrix
# ---------------------------------------------------------------------------

MATRIX_INSERT = f"""INSERT INTO public.{GUARD_TABLE}
    (agent_run_id, thread_id, lot_id, chamber_id,
     requested_alarm_source, requested_alarm_id,
     representative_alarm_source, representative_alarm_id,
     status, autonomy_level, action, severity)
VALUES (%s, %s, %s, %s, 'TRACE', %s, 'TRACE', %s, 'COMPLETED', 1, %s, %s)"""


def _is_integrity_error(exc: BaseException) -> bool:
    """제약 위반 계열인지 driver에 관계없이 판정한다.

    `_constraint_name()`이 `None`을 내는 위반이 있다 — NOT NULL은
    `diag.constraint_name`이 비고 `column_name`만 채워진다. 그런 실패를 그대로
    전파하면 `MATRIX_FIXTURE_INVALID`와 구분되지 않는다(팀 리뷰 권고 3).
    """

    for candidate in (exc, getattr(exc, "orig", None)):
        if candidate is None:
            continue
        for klass in type(candidate).__mro__:
            if klass.__name__ in {"IntegrityError", "DatabaseError"}:
                return True
    return False


def _matrix_row(
    index: int, action: str | None, severity: str | None
) -> tuple[Any, ...]:
    """조합마다 **다른 incident key**를 쓴다.

    `ux_agent_run_incident_active`는 `status IN ('RUNNING','WAITING_APPROVAL')`에만
    걸리므로 `COMPLETED`면 충돌하지 않지만, `lot_id`·`chamber_id`도 갈라 두면 이
    fixture가 다른 제약에 걸릴 여지가 없다.

    **fixture가 action/severity 외 제약 때문에 실패하면 증적이 아니다** — 그 경우
    `run_matrix()`가 `MATRIX_FIXTURE_INVALID`로 끝낸다.
    """

    token = f"{index:016x}"
    return (
        f"RUN-{token}",
        f"cm33-{token}",
        f"LOT-CM33-{index:02d}",
        f"EQP01-PM-CM33-{index:02d}",
        f"TA-CM33-{index:02d}",
        f"TA-CM33-{index:02d}",
        action,
        severity,
    )


@dataclass(frozen=True)
class MatrixResult:
    accepted: tuple[tuple[str | None, str | None], ...]
    rejected: tuple[tuple[str | None, str | None], ...]
    constraint_names: tuple[str, ...]

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.accepted), len(self.rejected)


def run_matrix(connection: Any) -> MatrixResult:
    """16조합을 **실제로 INSERT**하고 전부 savepoint rollback한다.

    read-only가 아니다. 계약이 "실제 INSERT·rollback으로 증명"이므로 쓰기를 시도하되
    영구 변화를 0으로 만든다. 성공 조합도 즉시 rollback한다 — 4건이 남으면 그것이
    데이터다.
    """

    accepted: list[tuple[str | None, str | None]] = []
    rejected: list[tuple[str | None, str | None]] = []
    names: set[str] = set()

    for index, (action, severity, _ok) in enumerate(truth_table()):
        connection.exec_driver_sql("SAVEPOINT cm33_matrix")
        try:
            connection.exec_driver_sql(
                MATRIX_INSERT, _matrix_row(index, action, severity)
            )
        # **driver를 고르지 않는다.** SQLAlchemy는 `SQLAlchemyError`로 감싸지만
        # 격리 container의 psycopg 어댑터는 `psycopg.errors.CheckViolation`을
        # 그대로 낸다. 둘 다 CHECK 위반이므로 constraint 이름으로 판정한다.
        except Exception as exc:
            name = _constraint_name(exc)
            if name is None:
                # **이름 없는 제약 위반도 fixture 결함이다.**
                #
                # NOT NULL 위반은 `diag.constraint_name`이 비고 `column_name`만
                # 채워진다. 그대로 전파하면 승인 Gate의 실패 원인이 "guard가 4/12가
                # 아니다"인지 "fixture가 틀렸다"인지 구분되지 않는다
                # (팀 리뷰 권고 3).
                if _is_integrity_error(exc):
                    raise SeverityGuardStateError(
                        "matrix fixture가 pair guard 외 제약에 걸렸습니다",
                        reason_code="MATRIX_FIXTURE_INVALID",
                    ) from exc
                raise
            connection.exec_driver_sql("ROLLBACK TO SAVEPOINT cm33_matrix")
            if name != GUARD_CONSTRAINT:
                # 다른 제약에 걸린 거부를 pair guard 증거로 오인하지 않는다.
                raise SeverityGuardStateError(
                    "matrix fixture가 pair guard 외 제약에 걸렸습니다",
                    reason_code="MATRIX_FIXTURE_INVALID",
                ) from exc
            rejected.append((action, severity))
            names.add(name)
        else:
            connection.exec_driver_sql("ROLLBACK TO SAVEPOINT cm33_matrix")
            accepted.append((action, severity))

    return MatrixResult(tuple(accepted), tuple(rejected), tuple(sorted(names)))


def _constraint_name(exc: BaseException) -> str | None:
    """CHECK 위반 예외에서 constraint 이름을 꺼낸다.

    SQLAlchemy는 driver 예외를 `orig`로 감싸고, psycopg는 `diag`를 직접 가진다.
    둘 다 본다 — runner가 driver를 고르지 않는다.
    """

    for candidate in (getattr(exc, "orig", None), exc):
        diag = getattr(candidate, "diag", None)
        name = getattr(diag, "constraint_name", None)
        if name:
            return str(name)
    return None


def assert_matrix(result: MatrixResult) -> None:
    """수락 4 · 거부 12 · **오판 0**을 강제한다."""

    accepted_expected, rejected_expected = expected_counts()
    if result.counts != (accepted_expected, rejected_expected):
        raise SeverityGuardStateError(
            "16조합 수락·거부 수가 계약과 다릅니다", reason_code="MATRIX_MISMATCH"
        )
    if set(result.accepted) != set(ACCEPTED_PAIRS):
        # 개수만 맞고 조합이 다를 수 있다.
        raise SeverityGuardStateError(
            "수락된 조합이 계약과 다릅니다", reason_code="MATRIX_MISMATCH"
        )
    if result.constraint_names not in ((), (GUARD_CONSTRAINT,)):
        raise SeverityGuardStateError(
            "거부 constraint 이름이 계약과 다릅니다", reason_code="MATRIX_MISMATCH"
        )


# ---------------------------------------------------------------------------
# artifact — marker·receipt
# ---------------------------------------------------------------------------

RECEIPT_ARTIFACT_TYPE = "agent_severity_guard_final_receipt"

#: `guarded_identity()`가 내는 key 집합. receipt·marker가 같은 모양을 요구한다.
_IDENTITY_KEYS = frozenset(
    {
        "dataset_epoch",
        "source_archive_sha256",
        "bootstrap_stage",
        "manifest_sha256",
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
    }
)
RECEIPT_STATUSES = frozenset({"STARTED", "COMMITTED", "ABORTED"})
ABORT_REASONS = frozenset({"APPLY_FAILED", "SUPERSEDED_BEFORE_RETRY"})

MARKER_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "database",
        "profile",
        "status",
        "dataset_epoch",
        "source_archive_sha256",
        "bootstrap_stage",
        "migration_id",
        "migration_sha256",
        "manifest_sha256",
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
        "baseline_schema_signature_sha256",
        "guarded_schema_signature_sha256",
        "agent_run_rows",
        "matrix_accepted",
        "matrix_rejected",
        "change_reference",
        "applied_at",
        "recorded_at",
    }
)


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in RUNTIME_DATABASES:
        raise SeverityGuardArtifactError("guard marker database가 허용되지 않았습니다")
    return root / f"{FINAL_ARTIFACT_TYPE}.{database}.json"


def receipt_path(database: str, operation_id: str, *, root: Path = REPORT_ROOT) -> Path:
    try:
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise SeverityGuardArtifactError(
            "guard receipt operation id가 잘못됐습니다"
        ) from exc
    return root / f"{FINAL_ARTIFACT_TYPE}.{database}.{operation_id}.json"


def guarded_identity(target: BootstrapTarget) -> dict[str, Any]:
    """successor manifest와 predecessor marker에서 계보 identity를 낸다.

    **predecessor marker를 읽는다.** `V5-CM-3.2`가 발급한 `agent_runtime_final`이
    없으면 이 Task가 무엇 위에 쌓는지 알 수 없다 — successor는 predecessor를
    전제한다.
    """

    if target.database not in RUNTIME_DATABASES or target.profile != RUNTIME_PROFILE:
        raise SeverityGuardStateError(
            "003은 runtime profile에만 적용할 수 있습니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )
    path = resolve_bootstrap_manifest_path(RUNTIME_PROFILE, GUARDED_STAGE)
    manifest = _read_json(path)
    try:
        manifest_v3.validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile=RUNTIME_PROFILE,
            expected_stage=GUARDED_STAGE,
            expected_archive_sha256=manifest_v3.FINAL_ARCHIVE_SHA256,
        )
    except VerificationError as exc:
        raise SeverityGuardArtifactError(
            "guarded manifest가 최종 계약과 다릅니다"
        ) from exc
    if tuple(manifest["applied_migrations"]) != EXPECTED_LINEAGE:
        raise SeverityGuardArtifactError("guarded lineage가 다릅니다")

    # **CM-3.2 계약으로 검증한다.** 파일 존재와 secret scan만 보면 같은 경로의 임의
    # JSON도 successor provenance가 된다(구현리뷰 필수 C).
    runtime_sql, _ = agent_runtime.load_and_validate_sql()
    try:
        predecessor_marker = agent_runtime.load_marker(
            target, migration_sha=agent_runtime.migration_sha256(runtime_sql)
        )
    except agent_runtime.AgentRuntimeError as exc:
        raise SeverityGuardArtifactError(
            "predecessor marker가 계약과 다릅니다", reason_code="MISSING_PREDECESSOR"
        ) from exc
    if predecessor_marker is None:
        raise SeverityGuardArtifactError(
            "predecessor marker가 없습니다", reason_code="MISSING_PREDECESSOR"
        )
    return {
        "dataset_epoch": str(manifest["dataset_epoch"]),
        "source_archive_sha256": str(manifest["source_archive_sha256"]),
        "bootstrap_stage": GUARDED_STAGE,
        "manifest_sha256": _canonical_hash(manifest),
        "predecessor_marker_sha256": _canonical_hash(predecessor_marker),
        # baseline signature는 apply transaction이 실측과 대조한다.
        "predecessor_schema_signature_sha256": str(
            predecessor_marker["schema_signature_sha256"]
        ),
    }


def _is_exact_int(value: Any, expected: int | None = None) -> bool:
    """JSON 증적의 정수 계약.

    `True == 1`·`4.0 == 4`이므로 동등 비교만으로는 타입이 눌린다. `bool`을 먼저
    배제하고 `int`만 받는다(구현리뷰 필수 H).
    """

    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return True if expected is None else value == expected


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_marker(
    payload: Mapping[str, Any], target: BootstrapTarget, *, migration_sha: str
) -> None:
    """successor marker 계약. **key가 아니라 값이 계약이다.**"""

    if (
        set(payload) != MARKER_KEYS
        or payload.get("artifact_type") != FINAL_ARTIFACT_TYPE
        or not _is_exact_int(payload.get("format_version"), FINAL_MARKER_FORMAT_VERSION)
        or payload.get("task_id") != TASK_ID
    ):
        raise SeverityGuardArtifactError(
            "guard marker key/value 계약이 다릅니다", reason_code="MARKER_STALE"
        )
    if (
        payload.get("database") != target.database
        or payload.get("profile") != RUNTIME_PROFILE
        or payload.get("migration_id") != MIGRATION_ID
        or payload.get("migration_sha256") != migration_sha
    ):
        raise SeverityGuardArtifactError("guard marker provenance가 다릅니다")

    identity = guarded_identity(target)
    for key in identity:
        if payload.get(key) != identity[key]:
            raise SeverityGuardArtifactError("guard marker 계보가 다릅니다")
    if (
        payload["baseline_schema_signature_sha256"]
        != identity["predecessor_schema_signature_sha256"]
    ):
        # baseline은 CM-3.2 marker가 증명한 그 signature여야 한다.
        raise SeverityGuardArtifactError("guard marker baseline이 다릅니다")

    if payload.get("status") != "APPLIED":
        raise SeverityGuardArtifactError("guard marker 상태가 다릅니다")
    for key in (
        "baseline_schema_signature_sha256",
        "guarded_schema_signature_sha256",
    ):
        if not _is_sha256(payload.get(key)):
            raise SeverityGuardArtifactError(f"guard marker {key}가 잘못됐습니다")
    if (
        payload["baseline_schema_signature_sha256"]
        == payload["guarded_schema_signature_sha256"]
    ):
        # 교체가 일어났으면 signature가 반드시 달라진다.
        raise SeverityGuardArtifactError("guard marker signature가 바뀌지 않았습니다")

    accepted, rejected = expected_counts()
    if not _is_exact_int(payload.get("matrix_accepted"), accepted) or not _is_exact_int(
        payload.get("matrix_rejected"), rejected
    ):
        raise SeverityGuardArtifactError("guard marker matrix 수가 계약과 다릅니다")
    rows = payload.get("agent_run_rows")
    if not _is_exact_int(rows) or rows < 0:
        raise SeverityGuardArtifactError("guard marker 행 수가 잘못됐습니다")
    for key in ("applied_at", "recorded_at"):
        if not _is_timestamp(payload.get(key)):
            raise SeverityGuardArtifactError(f"guard marker {key}가 잘못됐습니다")
    validate_change_reference(str(payload.get("change_reference")))
    scan_for_sensitive_values(payload)


def load_marker(
    target: BootstrapTarget, *, migration_sha: str, root: Path = MARKER_ROOT
) -> dict[str, Any] | None:
    path = marker_path(target.database, root=root)
    if not path.exists():
        return None
    payload = _read_json(path)
    validate_marker(payload, target, migration_sha=migration_sha)
    return payload


def save_marker(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    migration_sha: str,
    root: Path = MARKER_ROOT,
) -> None:
    validate_marker(payload, target, migration_sha=migration_sha)
    try:
        atomic_save_json(marker_path(target.database, root=root), dict(payload))
    except OSError as exc:
        raise SeverityGuardArtifactError("guard marker 저장에 실패했습니다") from exc


def _read_json(path: Path) -> dict[str, Any]:
    payload = manifest_v3._load_json(path)
    if not isinstance(payload, dict):
        raise SeverityGuardArtifactError("guard artifact는 object여야 합니다")
    scan_for_sensitive_values(payload)
    return payload


def _receipt_files(database: str, *, root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob(f"{FINAL_ARTIFACT_TYPE}.{database}.*.json"))


def validate_receipt(payload: Mapping[str, Any], *, database: str) -> None:
    status = payload.get("status")
    if status not in RECEIPT_STATUSES:
        raise SeverityGuardArtifactError("guard receipt status가 잘못됐습니다")
    common = {
        "artifact_type",
        "format_version",
        "task_id",
        "operation_id",
        "database",
        "profile",
        "status",
        "migration_id",
        "migration_sha256",
        "guarded_identity",
        "change_reference",
        "started_at",
        "agent_run_rows_before",
    }
    extra = {
        "STARTED": frozenset(),
        "COMMITTED": frozenset(
            {
                "committed_at",
                "agent_run_rows_after",
                "baseline_schema_signature_sha256",
                "guarded_schema_signature_sha256",
                "matrix_accepted",
                "matrix_rejected",
            }
        ),
        "ABORTED": frozenset({"aborted_at", "abort_reason"}),
    }[str(status)]
    if set(payload) != common | extra:
        raise SeverityGuardArtifactError("guard receipt key 계약이 다릅니다")
    if (
        payload.get("artifact_type") != RECEIPT_ARTIFACT_TYPE
        or not _is_exact_int(payload.get("format_version"), 1)
        or payload.get("task_id") != TASK_ID
        or payload.get("migration_id") != MIGRATION_ID
        or payload.get("database") != database
        or payload.get("profile") != RUNTIME_PROFILE
    ):
        raise SeverityGuardArtifactError("guard receipt 계보가 다릅니다")
    if not _is_sha256(payload.get("migration_sha256")):
        raise SeverityGuardArtifactError("guard receipt migration sha가 잘못됐습니다")

    # **key가 아니라 값이 계약이다**(구현리뷰 필수 F).
    #
    # 없으면 `format_version=999`·`operation_id="not-a-uuid"`·`matrix 0/0`인 receipt가
    # 통과해 recovery에서 정상 증적으로 승격된다.
    try:
        parsed = uuid.UUID(str(payload.get("operation_id")))
    except ValueError as exc:
        raise SeverityGuardArtifactError(
            "guard receipt operation id가 잘못됐습니다"
        ) from exc
    if str(parsed) != str(payload["operation_id"]):
        raise SeverityGuardArtifactError("guard receipt operation id가 잘못됐습니다")
    for key in ("started_at", *(k for k in extra if k.endswith("_at"))):
        if not _is_timestamp(payload.get(key)):
            raise SeverityGuardArtifactError(f"guard receipt {key}가 잘못됐습니다")

    identity = payload.get("guarded_identity")
    if not isinstance(identity, Mapping) or set(identity) != _IDENTITY_KEYS:
        raise SeverityGuardArtifactError("guard receipt identity 구조가 다릅니다")
    for key in (
        "source_archive_sha256",
        "manifest_sha256",
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
    ):
        if not _is_sha256(identity.get(key)):
            raise SeverityGuardArtifactError(
                f"guard receipt identity {key}가 잘못됐습니다"
            )
    if identity.get("bootstrap_stage") != GUARDED_STAGE:
        raise SeverityGuardArtifactError("guard receipt identity stage가 다릅니다")

    if status == "COMMITTED":
        for key in (
            "baseline_schema_signature_sha256",
            "guarded_schema_signature_sha256",
        ):
            if not _is_sha256(payload.get(key)):
                raise SeverityGuardArtifactError(f"guard receipt {key}가 잘못됐습니다")
        if (
            payload["baseline_schema_signature_sha256"]
            == payload["guarded_schema_signature_sha256"]
        ):
            # 교체가 일어났으면 signature가 반드시 달라진다.
            raise SeverityGuardArtifactError("guard receipt signature가 같습니다")
        if (
            payload["baseline_schema_signature_sha256"]
            != identity["predecessor_schema_signature_sha256"]
        ):
            raise SeverityGuardArtifactError(
                "guard receipt baseline이 identity와 다릅니다"
            )
        accepted, rejected = expected_counts()
        if not _is_exact_int(
            payload.get("matrix_accepted"), accepted
        ) or not _is_exact_int(payload.get("matrix_rejected"), rejected):
            # 4/12를 증명하지 않은 receipt는 증적이 아니다.
            raise SeverityGuardArtifactError(
                "guard receipt matrix 수가 계약과 다릅니다"
            )
    before = payload.get("agent_run_rows_before")
    if not _is_exact_int(before) or before < 0:
        raise SeverityGuardArtifactError("guard receipt 행 수가 잘못됐습니다")
    if status == "COMMITTED":
        # `0.0 == 0`·`False == 0`이라 동등 비교만으로는 타입이 눌린다
        # (구현리뷰 필수 H-2).
        after = payload.get("agent_run_rows_after")
        if not _is_exact_int(after) or after < 0:
            raise SeverityGuardArtifactError("guard receipt 완료 행 수가 잘못됐습니다")
        # 전환은 행을 바꾸지 않는다. 다르면 matrix가 남긴 것이다.
        if after != before:
            raise SeverityGuardArtifactError("guard receipt 행 수가 변했습니다")
    if status == "ABORTED" and payload.get("abort_reason") not in ABORT_REASONS:
        raise SeverityGuardArtifactError("guard receipt 중단 사유가 잘못됐습니다")
    validate_change_reference(str(payload.get("change_reference")))
    scan_for_sensitive_values(payload)


def load_receipts(target: BootstrapTarget, *, root: Path) -> list[dict[str, Any]]:
    receipts = []
    for path in _receipt_files(target.database, root=root):
        payload = _read_json(path)
        validate_receipt(payload, database=target.database)
        receipts.append(payload)
    return receipts


def _save_receipt(
    payload: Mapping[str, Any], target: BootstrapTarget, *, root: Path
) -> None:
    validate_receipt(payload, database=target.database)
    try:
        atomic_save_json(
            receipt_path(target.database, str(payload["operation_id"]), root=root),
            dict(payload),
        )
    except OSError as exc:
        raise SeverityGuardArtifactError("guard receipt 저장에 실패했습니다") from exc


def _start_receipt(
    target: BootstrapTarget,
    *,
    migration_sha: str,
    change_reference: str,
    identity: Mapping[str, Any],
    rows_before: int,
    root: Path,
) -> dict[str, Any]:
    payload = {
        "artifact_type": RECEIPT_ARTIFACT_TYPE,
        "format_version": 1,
        "task_id": TASK_ID,
        "operation_id": str(uuid.uuid4()),
        "database": target.database,
        "profile": target.profile,
        "status": "STARTED",
        "migration_id": MIGRATION_ID,
        "migration_sha256": migration_sha,
        "guarded_identity": dict(identity),
        "change_reference": change_reference,
        "started_at": _timezone_text(datetime.now(UTC)),
        # **실측이다.** 계획은 기존 유효 row 보존을 요구한다 — 0으로 고정하면
        # valid row가 있는 DB에서 artifact가 거짓을 기록한다(구현리뷰 필수 C).
        "agent_run_rows_before": rows_before,
    }
    _save_receipt(payload, target, root=root)
    return payload


def _finish_receipt(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    root: Path,
    baseline: str | None = None,
    guarded: str | None = None,
    matrix: MatrixResult | None = None,
    rows_after: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in receipt.items()
        if key
        not in {
            "committed_at",
            "agent_run_rows_after",
            "baseline_schema_signature_sha256",
            "guarded_schema_signature_sha256",
            "matrix_accepted",
            "matrix_rejected",
            "aborted_at",
            "abort_reason",
        }
    }
    if matrix is None:
        payload.update(
            status="ABORTED",
            aborted_at=_timezone_text(datetime.now(UTC)),
            abort_reason=reason or "APPLY_FAILED",
        )
    else:
        accepted, rejected = matrix.counts
        payload.update(
            status="COMMITTED",
            committed_at=_timezone_text(datetime.now(UTC)),
            agent_run_rows_after=rows_after,
            baseline_schema_signature_sha256=baseline,
            guarded_schema_signature_sha256=guarded,
            matrix_accepted=accepted,
            matrix_rejected=rejected,
        )
    _save_receipt(payload, target, root=root)
    return payload


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _prepare(connection: Any, target: BootstrapTarget, *, readonly: bool) -> None:
    if target.database not in RUNTIME_DATABASES or target.profile != RUNTIME_PROFILE:
        raise SeverityGuardStateError(
            "003은 runtime profile에만 적용할 수 있습니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )
    prepare_transaction(
        connection, target, readonly=readonly, acquire_lock=acquire_advisory_lock
    )


def assert_guarded_postcondition(connection: Any) -> str:
    """guarded 상태에서도 **CM-3.2 물리 계약 전체**를 본다.

    `inspect_guard()`의 signature에는 `PUBLIC` privilege·전체 table allowlist·row
    불변식이 없다. 그래서 적용 후 `PUBLIC SELECT`를 준 DB가 `--verify`·`NO_OP`를 전부
    통과했다(구현리뷰 필수 J). 공용 순서의 verify → verify-matrix → no-op이 통째로
    거짓 green이 될 수 있었다.

    baseline과 달리 **guarded allowlist**로 부른다 — 003이 이미 얹혀 있다.

    반환값은 live guarded signature다.
    """

    try:
        result = agent_runtime.postcheck_database(
            connection,
            alarm_rows_before=agent_runtime.alarm_event_count(connection),
            expected_constraints=GUARDED_CONSTRAINTS,
        )
    except agent_runtime.AgentRuntimeError as exc:
        raise SeverityGuardStateError(
            "guarded 물리 계약이 성립하지 않습니다", reason_code="GUARDED_DRIFT"
        ) from exc
    return str(result.schema_signature_sha256)


def assert_guarded_marker_agrees(
    connection: Any, *, marker: Mapping[str, Any], identity: Mapping[str, Any]
) -> str:
    """guarded 판정의 **세 축을 한 번에** 본다.

    ```text
    ① 물리 계약        assert_guarded_postcondition()
    ② marker guarded   marker == live signature
    ```

    둘을 따로 두면 진입점마다 다르게 조합된다. 실제로 그랬다 — `run_verify()`만
    ②를 봤고 `run_preflight()`·`run_apply()` no-op은 `assert_guarded_postcondition()`의
    **반환값을 버렸다.** 그래서 guarded signature를 바꾼 marker가 `GUARDED_MARKED`·
    `NO_OP`로 통과했다(구현리뷰 필수 J-2).

    ②만 여기 있는 이유는 **live DB가 있어야 판정할 수 있기 때문**이다. marker의
    `baseline_schema_signature_sha256`은 artifact끼리의 비교라 `validate_marker()`가
    `load_marker()` 안에서 이미 본다 — 여기서 또 보면 도달할 수 없는 분기가 된다.

    `identity`는 그 계약을 문서화하기 위해 받는다.
    """

    del identity  # `validate_marker()`가 계보를 이미 강제했다.
    live = assert_guarded_postcondition(connection)
    if marker["guarded_schema_signature_sha256"] != live:
        raise SeverityGuardStateError(
            "marker와 live signature가 다릅니다", reason_code="DRIFT"
        )
    return live


def assert_baseline_precondition(
    connection: Any, *, identity: Mapping[str, Any]
) -> str:
    """DDL 전에 **CM-3.2 물리 계약 전체**를 통과해야 한다.

    schema signature 비교만으로는 부족하다. `inspect_guard()`가 내는 signature에는
    `PUBLIC` privilege·전체 table allowlist·row 불변식이 들어 있지 않다 — 실제로
    `PUBLIC`에 `agent_run SELECT`를 준 DB가 signature는 같은 채로 통과했다
    (구현리뷰 필수 G).

    `postcheck_database()`가 그 축들을 본다. baseline allowlist로 부르는 것이
    중요하다 — 아직 003을 얹기 **전**이다.

    반환값은 live baseline signature다.
    """

    try:
        result = agent_runtime.postcheck_database(
            connection,
            alarm_rows_before=agent_runtime.alarm_event_count(connection),
            expected_constraints=agent_runtime.EXPECTED_CONSTRAINTS,
        )
    except agent_runtime.AgentRuntimeError as exc:
        raise SeverityGuardStateError(
            "baseline 물리 계약이 성립하지 않습니다", reason_code="BASELINE_DRIFT"
        ) from exc
    if (
        result.schema_signature_sha256
        != identity["predecessor_schema_signature_sha256"]
    ):
        raise SeverityGuardStateError(
            "live baseline이 predecessor marker와 다릅니다",
            reason_code="BASELINE_DRIFT",
        )
    return str(result.schema_signature_sha256)


def run_preflight(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> str:
    """read-only 상태 판정. **아무것도 쓰지 않는다.**

    ## 두 가지 실패 방식

    **live DB drift는 상태로 낸다** — `BASELINE_DRIFT`·`GUARDED_DRIFT`.
    운영자가 "지금 어떤 상태인가"를 알아야 하기 때문이다.

    **artifact 계약 위반은 예외로 낸다.** `load_and_validate_sql()`과
    `guarded_identity()`가 그렇다 — 후자는 predecessor marker가 없거나 계보가
    다르면 `SeverityGuardArtifactError`를 던진다. 이건 DB 상태가 아니라 이 Task를
    실행할 자격의 문제이고, apply도 같은 지점에서 같은 예외로 멈춘다. CLI가
    `GUARD_FAIL reason=CONTRACT_INVALID`(exit 2)로 받는다.

    predecessor marker는 **두 분기 모두** 필요하다 — baseline은 apply 예측에,
    guarded는 marker 계보 대조에 쓴다. 그래서 DB 상태와 무관하게 진입 시점에 한 번
    읽는다. 분기에서 읽으면 같은 artifact 결함이 DB 상태에 따라 나기도 하고 안 나기도
    한다.
    """

    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    identity = guarded_identity(target)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare(connection, target, readonly=True)
            inspection = inspect_guard(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            committed = [
                item
                for item in load_receipts(target, root=report_root)
                if item.get("status") == "COMMITTED"
                and item.get("migration_sha256") == migration_sha
            ]
            state = classify_state(
                inspection, marker, receipt=committed[0] if committed else None
            )
            # **모든 분기가 apply와 같은 판정을 쓴다.**
            #
            # 상태 보고가 apply보다 느슨하면 preflight가 통과시킨 것이
            # apply에서 막힌다. `inspect_guard()`는 두 상태만 내므로
            # if/else로 써서 분기가 빠지지 않게 한다.
            if inspection.state == "BASELINE_MARKED":
                if violation_rows(connection) > 0:
                    return "INVALID_EXISTING_ROWS"
                try:
                    assert_baseline_precondition(connection, identity=identity)
                except SeverityGuardStateError:
                    return "BASELINE_DRIFT"
            else:
                try:
                    # marker가 있으면 **그것까지** 본다. 물리 계약만 보면 변조된
                    # 증명서가 `GUARDED_MARKED`로 통과한다(구현리뷰 필수 J-2).
                    if marker is None:
                        assert_guarded_postcondition(connection)
                    else:
                        assert_guarded_marker_agrees(
                            connection, marker=marker, identity=identity
                        )
                except SeverityGuardStateError:
                    return "GUARDED_DRIFT"
            return state
    finally:
        engine.dispose()


def run_apply(
    target: BootstrapTarget,
    *,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> tuple[str, MatrixResult | None]:
    """baseline → guarded 원자 전환.

    ## 한 transaction 안의 순서

    ```text
    advisory xact lock → LOCK TABLE ACCESS EXCLUSIVE
    → predecessor 이름·정의 exact 재확인
    → 기존 row 전수 사전검증
    → DROP + ADD (한 문장)
    → catalog exact postcheck
    → 16조합 matrix (savepoint)          ← commit 승인 Gate
    → row delta 0 확인
    → commit
    → receipt COMMITTED → marker-last
    ```

    **matrix가 실패하면 DDL째 rollback된다.** 그것이 이 순서의 이유다 — guard가
    실제로 4/12로 동작하지 않으면 애초에 commit하지 않는다.
    """

    change_reference = validate_change_reference(change_reference)
    sql, statements = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    # 계약이 깨져 있으면 접속할 이유가 없다. identity를 **한 번만** 읽는다.
    identity = guarded_identity(target)

    engine = engine_factory(target)
    receipt: dict[str, Any] | None = None
    try:
        with engine.connect() as connection, connection.begin():
            _prepare(connection, target, readonly=False)
            connection.exec_driver_sql(
                f"LOCK TABLE public.{GUARD_TABLE} IN ACCESS EXCLUSIVE MODE"
            )
            inspection = inspect_guard(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            committed = [
                item
                for item in load_receipts(target, root=report_root)
                if item.get("status") == "COMMITTED"
                and item.get("migration_sha256") == migration_sha
            ]
            state = classify_state(
                inspection, marker, receipt=committed[0] if committed else None
            )
            if state == "GUARDED_MARKED":
                # no-op도 같은 판정을 쓴다 — 확인 없는 no-op은 "아무 일도 없었다"가
                # 아니라 "아무것도 보지 않았다"이다(구현리뷰 필수 J).
                #
                # 물리 계약만 보고 marker를 안 보면 변조된 증명서가 `NO_OP`로
                # 통과한다(필수 J-2). `marker`는 `GUARDED_MARKED`의 정의상 있다.
                assert marker is not None
                assert_guarded_marker_agrees(
                    connection, marker=marker, identity=identity
                )
                return "NO_OP", None
            if state != "BASELINE_MARKED":
                _refuse(state)

            if violation_rows(connection) > 0:
                _refuse("INVALID_EXISTING_ROWS")
            rows_before = row_count(connection)
            # **DDL 전에 CM-3.2 물리 계약 전체를 통과한다**(구현리뷰 필수 E·G).
            #
            # 여기서 멈춰야 아무것도 남지 않는다. marker 저장 시점에야 drift가
            # 드러나면 이미 COMMITTED receipt가 남는다.
            baseline_signature = assert_baseline_precondition(
                connection, identity=identity
            )

            receipt = _start_receipt(
                target,
                migration_sha=migration_sha,
                change_reference=change_reference,
                identity=identity,
                rows_before=rows_before,
                root=report_root,
            )

            # **CM-3.2의 실행 판정을 그대로 쓴다.**
            #
            # `exec_driver_sql()`은 literal `%`를 psycopg에 그대로 넘기고, psycopg는
            # DO guard의 `RAISE EXCEPTION ... %` 서식 지정자를 bind placeholder로
            # 오인한다. `execute_schema()`가 `text()` 컴파일로 그것을 escape한다 —
            # CM-3.2가 이미 겪고 고친 문제이며 여기서 새로 구현하지 않는다.
            agent_runtime.execute_schema(connection, statements)

            guarded = inspect_guard(connection)
            if guarded.state != "GUARDED_UNMARKED":
                raise SeverityGuardStateError(
                    "전환 후 catalog가 계약과 다릅니다", reason_code="DRIFT"
                )
            if guarded.guard != GUARD_DEFINITION:
                raise SeverityGuardStateError(
                    "전환 후 guard 정의가 계약과 다릅니다", reason_code="DRIFT"
                )

            # **commit 승인 Gate.** 여기서 실패하면 DDL째 rollback된다.
            matrix = run_matrix(connection)
            assert_matrix(matrix)

            if row_count(connection) != rows_before:
                raise SeverityGuardStateError(
                    "matrix가 행 수를 바꿨습니다", reason_code="ROW_DELTA"
                )
            guarded_signature = guarded.schema_signature_sha256

        receipt = _finish_receipt(
            receipt,
            target,
            root=report_root,
            baseline=baseline_signature,
            guarded=guarded_signature,
            matrix=matrix,
            rows_after=rows_before,
        )
        save_marker(
            _marker_payload(
                target,
                identity=identity,
                migration_sha=migration_sha,
                change_reference=change_reference,
                baseline=str(baseline_signature),
                guarded=str(guarded_signature),
                matrix=matrix,
                rows=rows_before,
                applied_at=str(receipt.get("committed_at")),
            ),
            target,
            migration_sha=migration_sha,
            root=marker_root,
        )
        return "APPLIED", matrix
    except Exception:
        if receipt is not None and receipt.get("status") == "STARTED":
            _finish_receipt(receipt, target, root=report_root)
        raise
    finally:
        engine.dispose()


def _marker_payload(
    target: BootstrapTarget,
    *,
    identity: Mapping[str, Any],
    migration_sha: str,
    change_reference: str,
    baseline: str,
    guarded: str,
    matrix: MatrixResult,
    rows: int,
    applied_at: str,
) -> dict[str, Any]:
    accepted, rejected = matrix.counts
    now = _timezone_text(datetime.now(UTC))
    return {
        "artifact_type": FINAL_ARTIFACT_TYPE,
        "format_version": FINAL_MARKER_FORMAT_VERSION,
        "task_id": TASK_ID,
        "database": target.database,
        "profile": target.profile,
        "status": "APPLIED",
        **dict(identity),
        "migration_id": MIGRATION_ID,
        "migration_sha256": migration_sha,
        "baseline_schema_signature_sha256": baseline,
        "guarded_schema_signature_sha256": guarded,
        "agent_run_rows": rows,
        "matrix_accepted": accepted,
        "matrix_rejected": rejected,
        "change_reference": change_reference,
        "applied_at": applied_at,
        "recorded_at": now,
    }


def run_verify(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> str:
    """live·marker full 대조. **read-only다.**"""

    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare(connection, target, readonly=True)
            inspection = inspect_guard(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            if marker is None:
                raise SeverityGuardStateError(
                    "guard marker가 없습니다", reason_code="GUARDED_UNMARKED"
                )
            if inspection.state != "GUARDED_UNMARKED":
                _refuse(inspection.state)
            # **유일한 guarded 판정을 쓴다**(구현리뷰 필수 J·J-2).
            assert_guarded_marker_agrees(
                connection, marker=marker, identity=guarded_identity(target)
            )
            return "GUARDED_MARKED"
    finally:
        engine.dispose()


def run_verify_matrix(
    target: BootstrapTarget,
    *,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> MatrixResult:
    """배포 후 독립 재검증. **tracked marker를 다시 쓰지 않는다.**

    배포 후 재검증이 증명서를 갱신하면 marker가 무엇을 증명하는지 흐려진다.

    **`--verify`를 먼저 통과해야 한다.** 그러지 않으면 provenance 없는 수동 guard도
    4/12만 맞으면 "배포 후 증적"으로 출력된다(구현리뷰 필수 B).
    """

    change_reference = validate_change_reference(change_reference)
    run_verify(target, engine_factory=engine_factory, marker_root=marker_root)

    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            _prepare(connection, target, readonly=False)
            before = row_count(connection)
            matrix = run_matrix(connection)
            assert_matrix(matrix)
            if row_count(connection) != before:
                raise SeverityGuardStateError(
                    "matrix가 행 수를 바꿨습니다", reason_code="ROW_DELTA"
                )
            transaction.rollback()
        _save_matrix_report(
            target,
            matrix=matrix,
            rows=before,
            change_reference=change_reference,
            root=report_root,
        )
        return matrix
    finally:
        engine.dispose()


def _save_matrix_report(
    target: BootstrapTarget,
    *,
    matrix: MatrixResult,
    rows: int,
    change_reference: str,
    root: Path,
) -> dict[str, Any]:
    """배포 후 증적. **ignored report이며 tracked marker를 건드리지 않는다.**"""

    accepted, rejected = matrix.counts
    payload = {
        "artifact_type": f"{FINAL_ARTIFACT_TYPE}_matrix",
        "format_version": 1,
        "task_id": TASK_ID,
        "database": target.database,
        "profile": target.profile,
        "change_reference": change_reference,
        "matrix_accepted": accepted,
        "matrix_rejected": rejected,
        "agent_run_rows_before": rows,
        "agent_run_rows_after": rows,
        "recorded_at": _timezone_text(datetime.now(UTC)),
    }
    scan_for_sensitive_values(payload)
    try:
        atomic_save_json(
            root / f"{FINAL_ARTIFACT_TYPE}_matrix.{target.database}.json", payload
        )
    except OSError as exc:
        raise SeverityGuardArtifactError("matrix report 저장에 실패했습니다") from exc
    return payload


def run_rehearse(
    target: BootstrapTarget,
    *,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
) -> MatrixResult:
    """격리 target에서 DDL·matrix를 돌리고 **전체 rollback**한다.

    artifact를 쓰지 않는다 — rehearsal은 증명서를 만들지 않는다.
    """

    validate_change_reference(change_reference)
    sql, statements = load_and_validate_sql()
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            _prepare(connection, target, readonly=False)
            connection.exec_driver_sql(
                f"LOCK TABLE public.{GUARD_TABLE} IN ACCESS EXCLUSIVE MODE"
            )
            rehearsal = inspect_guard(connection)
            if rehearsal.state != "BASELINE_MARKED":
                # **실제 상태를 넘긴다.** 상수로 뭉개면 `DRIFT`가
                # `PARTIAL_OR_DRIFT`로 보고된다(팀 리뷰 권고 1).
                _refuse(rehearsal.state)
            if violation_rows(connection) > 0:
                _refuse("INVALID_EXISTING_ROWS")
            # rehearsal도 **같은** precondition을 쓴다 — 리허설이 실제보다 느슨하면
            # 리허설을 통과한 것이 배포에서 막힌다.
            assert_baseline_precondition(connection, identity=guarded_identity(target))
            before = row_count(connection)
            # **CM-3.2의 실행 판정을 그대로 쓴다.**
            #
            # `exec_driver_sql()`은 literal `%`를 psycopg에 그대로 넘기고, psycopg는
            # DO guard의 `RAISE EXCEPTION ... %` 서식 지정자를 bind placeholder로
            # 오인한다. `execute_schema()`가 `text()` 컴파일로 그것을 escape한다 —
            # CM-3.2가 이미 겪고 고친 문제이며 여기서 새로 구현하지 않는다.
            agent_runtime.execute_schema(connection, statements)
            matrix = run_matrix(connection)
            assert_matrix(matrix)
            if row_count(connection) != before:
                raise SeverityGuardStateError(
                    "matrix가 행 수를 바꿨습니다", reason_code="ROW_DELTA"
                )
            transaction.rollback()
        return matrix
    finally:
        engine.dispose()


def _matrix_from_receipt(receipt: Mapping[str, Any]) -> MatrixResult:
    """receipt에 기록된 matrix 수를 `MatrixResult`로 되살린다.

    `validate_receipt()`가 4/12와 constraint 계약을 이미 강제했으므로 조합 목록은
    진리표에서 복원해도 무손실이다. **수는 receipt에서 온다.**
    """

    accepted = tuple(sorted(ACCEPTED_PAIRS, key=lambda pair: str(pair)))
    rejected = tuple((a, s) for a, s, ok in truth_table() if not ok)
    if (len(accepted), len(rejected)) != (
        int(receipt["matrix_accepted"]),
        int(receipt["matrix_rejected"]),
    ):
        raise SeverityGuardArtifactError("guard receipt matrix 수가 계약과 다릅니다")
    return MatrixResult(accepted, rejected, (GUARD_CONSTRAINT,))


def run_recover_marker(
    target: BootstrapTarget,
    *,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> str:
    """commit은 됐는데 marker 쓰기가 실패한 경우만 되살린다. **DB는 건드리지 않는다.**

    exact COMMITTED receipt·현재 identity·live guarded signature·행 수를 모두 대조한
    뒤에만 marker를 쓴다. 그 조건이 없으면 "무엇을 증명하는지" 모르는 marker가 나온다.
    """

    change_reference = validate_change_reference(change_reference)
    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    identity = guarded_identity(target)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare(connection, target, readonly=True)
            if load_marker(target, migration_sha=migration_sha, root=marker_root):
                raise SeverityGuardArtifactError("marker가 이미 있습니다")
            inspection = inspect_guard(connection)
            if inspection.state != "GUARDED_UNMARKED":
                _refuse(inspection.state)
            # **marker를 쓰기 전에 물리 계약 전체를 본다.**
            #
            # `inspect_guard()`의 signature에는 `PUBLIC` privilege·table
            # allowlist가 없다. 그것만 보고 marker를 쓰면 drift가 있는 DB에
            # “증명서”가 발급된다 — 필수 J와 같은 원인이 복구 경로에
            # 그대로 남아 있었다.
            live = assert_guarded_postcondition(connection)
            if live != inspection.schema_signature_sha256:
                raise SeverityGuardStateError(
                    "guarded signature가 확인 중 바뀌었습니다", reason_code="DRIFT"
                )
            rows = row_count(connection)
            candidates = [
                item
                for item in load_receipts(target, root=report_root)
                if item.get("status") == "COMMITTED"
                and item.get("migration_sha256") == migration_sha
                and item.get("change_reference") == change_reference
                and item.get("guarded_identity") == identity
                and item.get("guarded_schema_signature_sha256")
                == inspection.schema_signature_sha256
                and item.get("agent_run_rows_after") == rows
            ]
            if len(candidates) != 1:
                raise SeverityGuardArtifactError(
                    "복구 receipt 후보는 정확히 1건이어야 합니다"
                )
            receipt = candidates[0]
    finally:
        engine.dispose()

    save_marker(
        _marker_payload(
            target,
            identity=identity,
            migration_sha=migration_sha,
            change_reference=change_reference,
            baseline=str(receipt["baseline_schema_signature_sha256"]),
            guarded=str(receipt["guarded_schema_signature_sha256"]),
            # **receipt가 증명한 값을 그대로 쓴다.**
            #
            # canonical 4/12를 새로 합성하면 receipt가 실제로 증명하지 않은 수가
            # marker에 기록된다 — "exact receipt 기반 복구"가 아니게 된다
            # (구현리뷰 필수 F). validator가 4/12를 이미 강제하므로 여기서는
            # receipt 값을 소비하기만 한다.
            matrix=_matrix_from_receipt(receipt),
            rows=rows,
            applied_at=str(receipt["committed_at"]),
        ),
        target,
        migration_sha=migration_sha,
        root=marker_root,
    )
    return "RECOVERED"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5-CM-3.3 action/severity pair guard")
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--verify-matrix", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--recover-marker", action="store_true")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    """mode는 **하나만** 명시한다. mutation을 암묵적 기본값으로 두지 않는다."""

    mode = resolve_exclusive_mode(
        {
            "preflight": args.preflight,
            "apply": args.apply,
            "verify": args.verify,
            "verify_matrix": args.verify_matrix,
            "rehearse": args.rehearse,
            "recover": args.recover_marker,
        },
        default_mode="",
        mutually_exclusive_message="guard mode는 하나만 선택해야 합니다",
    )
    if not mode:
        raise SeverityGuardError(
            "guard mode를 하나 명시해야 합니다 "
            "(--preflight/--rehearse/--apply/--verify/--verify-matrix"
            "/--recover-marker)"
        )
    return mode


def assert_runtime_database(database: str | None) -> str:
    """**parser 직후 경계.** `load_dotenv`·loader·engine보다 앞이다."""

    if database is None:
        raise SeverityGuardError("--database가 필요합니다")
    if database not in RUNTIME_DATABASES:
        raise SeverityGuardStateError(
            "003은 runtime profile에만 적용할 수 있습니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )
    return database


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        mode = resolve_mode(args)
        database = assert_runtime_database(args.database)
        # 여기서부터만 자격증명을 읽는다.
        load_dotenv(REPOSITORY_ROOT / ".env", override=False)
        target = load_bootstrap_target(database)

        if mode == "preflight":
            state = run_preflight(target)
            print(f"GUARD_PREFLIGHT database={target.database} state={state}")
            return EXIT_OK
        if mode == "verify":
            state = run_verify(target)
            print(f"GUARD_VERIFY_OK database={target.database} state={state}")
            return EXIT_OK

        # 아래는 실제 INSERT를 시도한다 — 승인이 필요하다.
        if args.confirm_target != target.database:
            raise SeverityGuardError("--confirm-target이 대상 database와 다릅니다")
        if not args.change_ref:
            raise SeverityGuardError("apply/matrix에는 --change-ref가 필요합니다")

        if mode == "rehearse":
            matrix = run_rehearse(target, change_reference=args.change_ref)
            accepted, rejected = matrix.counts
            print(
                f"GUARD_REHEARSAL_OK database={target.database} "
                f"accepted={accepted} rejected={rejected}"
            )
            return EXIT_OK
        if mode == "recover":
            status = run_recover_marker(target, change_reference=args.change_ref)
            print(f"GUARD_{status} database={target.database}")
            return EXIT_OK
        if mode == "verify_matrix":
            matrix = run_verify_matrix(target, change_reference=args.change_ref)
            accepted, rejected = matrix.counts
            print(
                f"GUARD_MATRIX_OK database={target.database} "
                f"accepted={accepted} rejected={rejected}"
            )
            return EXIT_OK

        status, matrix = run_apply(target, change_reference=args.change_ref)
        counts = matrix.counts if matrix else (0, 0)
        print(
            f"GUARD_{status} database={target.database} "
            f"accepted={counts[0]} rejected={counts[1]}"
        )
        return EXIT_OK
    except (
        SeverityGuardError,
        MutationRuntimeError,
        ReferenceExtensionError,
        VerificationError,
        TargetValidationError,
    ) as exc:
        fallback = (
            "TARGET_VALIDATION_FAILED"
            if isinstance(exc, TargetValidationError)
            else "CONTRACT_INVALID"
        )
        reason = getattr(exc, "reason_code", getattr(exc, "code", fallback))
        print(
            f"GUARD_FAIL database={args.database or 'none'} reason={reason}",
            file=sys.stderr,
        )
        return getattr(exc, "exit_code", 2)
    except SQLAlchemyError:
        print(
            f"GUARD_FAIL database={args.database or 'none'} "
            "reason=CONNECT_OR_QUERY_FAILED",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
