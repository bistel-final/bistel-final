"""Runtime Repository (`V5-C-0.1`).

설계 §3.4의 Runtime 9 table을 소비하는 최소 저장 계층이다.

## 이 계층이 소유하지 않는 것

**transaction을 소유하지 않는다.** 모든 write 함수는 호출자가 연 `Connection`을 받고
그 안에서만 실행한다. `create_engine`·`begin`·`commit`·`rollback`을 부르지 않는다.
업무 DML과 `app.common.audit.append_audit_log()`가 **같은 unit of work**에 있어야
업무가 rollback될 때 감사도 함께 사라진다 — `V5-CM-4.2`가 그 증명을 이 Task에
명시적으로 넘겼다.

**물리 스키마를 다시 정의하지 않는다.** `002_agent_runtime_clean.sql`과
`003_agent_run_severity_pair.sql`이 계약이다. ORM table·`create_all`·자동 보정은 없다.

**업무 판정을 하지 않는다.** 대표 알람 선정은 `V5-C-1.1`, 조치 결정은 `V5-C-3.*`,
Tool 예산 정책은 `V5-C-2.2`가 소유한다. 이 계층은 그들이 정한 값을 안전하게 저장하고
되읽는다.

## 어길 수 없게 만든 것

계약을 "검증"하는 대신 **표현할 수 없게** 만든 자리가 둘 있다.

1. `action`과 `severity`를 따로 받지 않는다. `action` 하나를 받아
   `resolve_severity()`로 파생하므로 잘못된 짝을 만들 public 경로가 없다.
2. `is_representative`를 caller가 주지 않는다. `agent_run`의 대표 scalar와의 equality로
   파생하므로 두 저장 위치의 불일치와 대표 2건을 만들 수 없다.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from threading import Lock
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)
from sqlalchemy.sql.elements import TextClause

from app.common.audit import (
    AuditContractError,
    AuditEvent,
    AuditRecord,
    append_audit_log,
)
from app.common.enums import (
    ActionCode,
    ActionLinkRole,
    ActorType,
    AlarmSource,
    ApprovalStatus,
    Decision,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
    requires_approval,
    resolve_delivery_channels,
    resolve_severity,
)
from app.common.ids import new_agent_run_id, new_approval_id, new_tool_call_id
from app.common.schemas import AlarmRef

logger = logging.getLogger(__name__)

__all__ = [
    "AgentRepositoryError",
    "RepositoryConflict",
    "RepositoryContractError",
    "RepositoryNotFound",
    "RepositoryRetryable",
    "RepositoryUnavailable",
    "AgentRunRow",
    "CreateAgentRunCommand",
    "PredictionRow",
    "HumanReviewRow",
    "RUNTIME_REVIEW_LABEL_SOURCES",
    "execute_read_all",
    "translate_db_error",
    "create_agent_run",
    "get_agent_run",
    "lock_agent_run",
    "record_run_llm_usage",
    "find_active_run",
    "list_run_alarms",
    "set_run_action",
    "finish_agent_run",
    "finish_agent_run_with_active_latency",
    "active_run_latency_ms",
    "merge_run_action_provenance",
    "ACTION_PROVENANCE_KEY",
    "ACTION_PROVENANCE_SCHEMA",
    "REHYDRATION_SNAPSHOT_KEY",
    "insert_prediction",
    "get_prediction",
    "get_prediction_or_none",
    "insert_human_prediction_review",
    "list_human_prediction_reviews",
    "RunActionRow",
    "ActionHistoryRow",
    "ActionBundle",
    "ToolBudgetCounts",
    "ToolCallRow",
    "ApprovalRequestRow",
    "ActionDeliveryRow",
    "MesDeliveryClaim",
    "DeliveryCallbackTransition",
    "DeliveryRecoveryReason",
    "DeliveryRecoveryResult",
    "ApprovalDecisionRow",
    "PublicAgentRunRecord",
    "PublicActionDeliveryRecord",
    "PublicActionRecord",
    "PublicApprovalRecord",
    "PublicDeliveryRecord",
    "PublicToolCallRecord",
    "public_read_omission_counts",
    "RESERVED_TOOL_OUTPUT_KEYS",
    "RESERVED_ERROR_MSG",
    "link_run_action",
    "get_run_action",
    "find_run_action",
    "find_created_action",
    "insert_action_history",
    "get_action_history",
    "get_action_bundle",
    "reserve_tool_call",
    "finalize_tool_call",
    "list_tool_calls",
    "list_actions_public",
    "get_action_public",
    "get_agent_run_public",
    "count_tool_calls",
    "count_tool_calls_for_budget",
    "tool_call_consumes_budget",
    "create_approval_request",
    "get_approval_request",
    "begin_approval_wait",
    "resume_from_approval",
    "decide_approval",
    "insert_action_delivery",
    "get_action_delivery",
    "list_action_deliveries",
    "list_agent_runs_public",
    "list_approvals_public",
    "get_approval_public",
    "get_agent_run_by_thread_exact",
    "begin_email_delivery",
    "settle_email_delivery",
    "begin_mes_delivery",
    "settle_mes_webhook",
    "settle_delivery_callback",
    "list_stale_deliveries",
    "mark_delivery_unknown",
    "retry_failed_delivery",
    "INITIAL_DELIVERY_PAIRS",
]


# ---------------------------------------------------------------------------
# 오류 계약 — driver 메시지·SQL 원문·DSN을 상위로 흘리지 않는다
# ---------------------------------------------------------------------------


class AgentRepositoryError(RuntimeError):
    """Runtime Repository 경계의 sanitized 오류.

    `code`는 상위 계층이 분기할 수 있는 **안정 문자열**이다. driver 메시지를 그대로
    노출하면 상위 Task가 매번 psycopg 예외를 해석하게 되고, SQL·DSN이 응답·로그로
    새어 나간다.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class RepositoryNotFound(AgentRepositoryError):
    """대상 row가 없다."""


class RepositoryConflict(AgentRepositoryError):
    """재시도·멱등 경계에서 예상 가능한 업무 충돌이다."""


class RepositoryContractError(AgentRepositoryError):
    """입력 또는 물리 계약 위반. 대부분 DB에 닿기 전에 걸린다."""


class RepositoryRetryable(AgentRepositoryError):
    """**일시적 경합이다.** 같은 요청을 그대로 다시 보내면 성공할 수 있다.

    deadlock·직렬화 실패·lock 대기 만료·statement 취소가 여기 온다. psycopg3에서
    이 넷은 모두 `OperationalError` 계열이라 이전에는 `DATABASE_UNAVAILABLE`로
    분류됐다. 그러면 상위가 503으로 올려 **재시도하면 되는 상황을 장애로 보고**한다
    (구현리뷰 필수 2).

    이 Task가 그 상황을 새로 만든다 — `reserve_tool_call()`의 `FOR UPDATE`가 같은
    run의 예약을 직렬화하므로, `lock_timeout`이 걸린 DB에서는 두 번째 예약이
    `55P03`으로 만료된다.
    """


class RepositoryUnavailable(AgentRepositoryError):
    """**접속·소켓 또는 DB 가용성 문제다.** 업무 판정도 일시적 경합도 아니다.

    경합(`RepositoryRetryable`)을 먼저 걸러 낸 뒤 남는 것이 여기 온다. 접속 실패만
    있는 것이 아니다 — `DiskFull(53100)`·`OutOfMemory(53200)`·
    `TooManyConnections(53300)`처럼 **연결이 성립한 뒤 생기는 가용성 오류**도 psycopg
    `OperationalError` 계열이라 같은 분류로 온다(구현리뷰 편집 1).
    """


#: 알려진 제약 이름 → 안정 conflict code.
#:
#: **이름은 `002_agent_runtime_clean.sql` 실측이다.** PK 3종은 PostgreSQL이
#: `<table>_pkey`로 자동 명명한다. 여기 없는 CHECK·FK 위반은
#: `RepositoryContractError`다 — 재시도로 풀리는 충돌이 아니라 잘못된 입력이다.
CONFLICT_CODES: Final[Mapping[str, str]] = {
    "ux_agent_run_incident_active": "ACTIVE_RUN_EXISTS",
    "ux_agent_run_action_incident": "CREATED_ACTION_EXISTS",
    "ux_agent_run_action_created": "ACTION_ALREADY_CREATED",
    "agent_tool_call_agent_run_id_call_seq_key": "TOOL_CALL_SEQUENCE_CONFLICT",
    "approval_request_action_id_key": "APPROVAL_ALREADY_EXISTS",
    "ux_agent_run_alarm_representative": "REPRESENTATIVE_ALARM_EXISTS",
    "agent_run_action_pkey": "RUN_ACTION_ALREADY_LINKED",
    "action_delivery_pkey": "DELIVERY_ALREADY_EXISTS",
    "agent_run_alarm_pkey": "ALARM_ALREADY_LINKED",
    "agent_prediction_pkey": "PREDICTION_ALREADY_EXISTS",
}


def _constraint_name(error: BaseException) -> str | None:
    """psycopg가 준 제약 이름만 꺼낸다. **메시지는 읽지 않는다.**

    문자열 매칭으로 이름을 추출하면 driver 메시지 형식이 바뀔 때 조용히 어긋난다.
    psycopg3는 구조화된 `diag.constraint_name`을 준다.
    """

    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    return str(name) if name else None


#: SQLSTATE → 재시도 가능한 경합 code.
#:
#: **`OperationalError` 하위 클래스 이름이 아니라 SQLSTATE로 본다.** psycopg는
#: `DeadlockDetected`·`SerializationFailure`·`LockNotAvailable`·`QueryCanceled`를
#: 모두 `OperationalError` 아래에 두므로 클래스만으로는 접속 실패와 구분되지 않는다.
RETRYABLE_SQLSTATES: Final[Mapping[str, str]] = {
    "40001": "SERIALIZATION_FAILURE",
    "40P01": "DEADLOCK_DETECTED",
    "55P03": "LOCK_NOT_AVAILABLE",
    "57014": "STATEMENT_CANCELED",
}

#: `23503`. FK 위반의 SQLSTATE.
FOREIGN_KEY_VIOLATION: Final[str] = "23503"

#: FK 이름 → "참조하는 부모 row가 없다"는 안정 code.
#:
#: **이름은 격리 PostgreSQL 16 실측이다.** `002`가 FK에 이름을 주지 않으므로
#: PostgreSQL이 `<table>_<column>_fkey`로 자동 명명한다.
#:
#: 이 표는 `_insert_one()`만 참조한다. `23503`은 방향을 구분하지 못하기 때문이다 —
#: 자식 INSERT(부모 없음)와 부모 DELETE(자식 남음)가 **같은 constraint 이름·같은
#: `diag.table_name`**을 준다(실측). INSERT 문에서는 후자가 불가능하므로 그 자리에
#: 한정해야 "부모가 없다"가 참이 된다.
FOREIGN_KEY_CODES: Final[Mapping[str, str]] = {
    "agent_run_retry_of_run_id_fkey": "RETRY_SOURCE_RUN_NOT_FOUND",
    "agent_prediction_agent_run_id_fkey": "RUN_NOT_FOUND",
    "agent_prediction_review_agent_run_id_fkey": "RUN_NOT_FOUND",
    "agent_run_action_agent_run_id_fkey": "RUN_NOT_FOUND",
    "agent_run_action_action_id_fkey": "ACTION_NOT_FOUND",
    "agent_tool_call_agent_run_id_fkey": "RUN_NOT_FOUND",
    "approval_request_agent_run_id_fkey": "RUN_NOT_FOUND",
    "approval_request_action_id_fkey": "ACTION_NOT_FOUND",
    "action_delivery_action_id_fkey": "ACTION_NOT_FOUND",
}


def _sqlstate(error: BaseException) -> str | None:
    """psycopg가 준 SQLSTATE만 꺼낸다. **메시지는 읽지 않는다.**"""

    return getattr(getattr(error, "orig", None), "sqlstate", None)


def translate_db_error(error: SQLAlchemyError) -> AgentRepositoryError:
    """SQLAlchemy 오류를 이 계층의 안정 code로 옮긴다. **public seam이다.**

    `_translate()`가 오래 private이었는데 C-1.1·C-1.2·C-1.3의 Repository가 각각 그것을
    직접 import하게 됐다. 새 Task가 하나 늘 때마다 private seam 결합점이 늘고, 이름이나
    서명을 바꾸려면 흩어진 자리를 전부 찾아야 한다.

    같은 판단을 `require_active_transaction()`에 대해 이미 했다(구현리뷰 권고 1).

    `V5-C-2.1`부터 `run_guard_repository`·`incident_repository`·
    `routing_repository`가 모두 이 public seam을 쓴다. 신규 외부 Repository도 private
    `_translate()`를 import하지 않는다.
    """

    return _translate(error)


def execute_read_all(
    connection: Connection,
    statement: TextClause,
    params: Mapping[str, Any],
) -> Sequence[Row[Any]]:
    """읽기 statement를 실행하고 DB 오류를 공용 계층으로 옮긴다.

    transaction·재시도 정책은 소유하지 않는다. caller가 제공한 connection과 snapshot에서
    한 statement를 실행하는 얇은 public seam이다.
    """

    try:
        return connection.execute(statement, params).all()
    except SQLAlchemyError as exc:
        raise translate_db_error(exc) from exc


def _translate(error: SQLAlchemyError) -> AgentRepositoryError:
    retryable = RETRYABLE_SQLSTATES.get(_sqlstate(error) or "")
    if retryable is not None:
        # **경합을 장애보다 먼저 본다.** 아래 `OperationalError` 분기가 이것을
        # 삼키면 재시도 가능한 상황이 `DATABASE_UNAVAILABLE`이 된다.
        return RepositoryRetryable(retryable)
    if isinstance(error, IntegrityError):
        name = _constraint_name(error)
        code = CONFLICT_CODES.get(name or "")
        if code is not None:
            return RepositoryConflict(code)
        # CHECK·FK·NOT NULL 위반. 원인은 `raise ... from`으로 보존한다.
        return RepositoryContractError("CONSTRAINT_VIOLATION")
    if isinstance(error, OperationalError | InterfaceError):
        # **연결 실패만 Unavailable이다.** 경합은 위에서 이미 갈라졌다.
        #
        # 이전에는 모든 `DBAPIError`를 여기로 보냈다. 그래서 `thread_id`가 varchar
        # 경계를 넘겨 생긴 `DataError`가 "DB를 못 쓴다"로 분류돼 caller 입력 오류가
        # 503 후보가 됐다(구현리뷰 묶음 1 필수 2).
        return RepositoryUnavailable("DATABASE_UNAVAILABLE")
    if isinstance(error, DBAPIError):
        # 값·타입·SQL 계약 위반. 재시도로 풀리지 않는다.
        return RepositoryContractError("DATA_CONTRACT_VIOLATION")
    return RepositoryUnavailable("DATABASE_ERROR")


#: `002_agent_runtime_clean.sql`의 varchar 경계.
#:
#: **DB에 도달하기 전에 막는다.** 넘겨 보내면 PostgreSQL `DataError`로만 알 수 있고,
#: 그것은 caller 입력 오류인데도 driver 예외로 상위에 올라간다.
COLUMN_LIMITS: Final[Mapping[str, int]] = {
    "thread_id": 36,
    "lot_id": 20,
    "chamber_id": 24,
    "alarm_id": 24,
    "retry_of_run_id": 20,
    "llm_model": 64,
    "prompt_version": 40,
    "action_policy_version": 40,
    "reviewer": 40,
    "disposition": 16,
    "label_source": 16,
    "cause_summary": 0,  # text — 상한 없음. 공백만 금지한다.
    "action_id": 20,
    "approval_id": 20,
    "tool_name": 40,
    "request_hash": 64,
    "provider_message_id": 0,  # text
    "error_msg": 0,  # text
    "decided_by": 40,
    "decision_comment": 1000,
}


def _require_text(value: object, field: str) -> str:
    """trim 후 비어 있지 않고 컬럼 경계 안인지 본다."""

    if not isinstance(value, str):
        raise RepositoryContractError(f"INVALID_{field.upper()}")
    trimmed = value.strip()
    if not trimmed:
        raise RepositoryContractError(f"EMPTY_{field.upper()}")
    limit = COLUMN_LIMITS.get(field, 0)
    if limit and len(trimmed) > limit:
        raise RepositoryContractError(f"{field.upper()}_TOO_LONG")
    return trimmed


def _optional_text(value: object, field: str) -> str | None:
    """`None`이거나 유효한 문자열이다. **빈 문자열은 `None`이 아니다.**"""

    return None if value is None else _require_text(value, field)


def _json_payload(value: Mapping[str, Any] | None, field: str) -> str | None:
    """JSON을 **DML 전에** 직렬화하고 실패를 sanitized로 바꾼다.

    `json.dumps()`가 `TypeError`를 그대로 올리면 Repository의 오류 계약이 깨지고,
    직렬화 못 한 객체의 타입 이름이 예외 문자열에 실려 나간다. SQL 실행 전이라
    원자성은 멀쩡하지만 계약은 아니다(구현리뷰 묶음 1 2차 필수 1-B).

    ## `allow_nan=False`가 필요한 이유

    기본값은 `True`라 `NaN`·`Infinity`를 **성공적으로** 직렬화한다. 그런데 그 출력은
    JSON이 아니고 PostgreSQL `::jsonb`가 `invalid input syntax`로 거부한다. 즉
    "DML 전 한 곳에서 닫는다"는 계약이 이 입력에서만 깨져, SQL을 한 번 보낸 뒤
    `DATA_CONTRACT_VIOLATION`으로 끝났다(구현리뷰 묶음 1 3차 필수 1).

    `allow_nan=False`면 `ValueError`가 되어 기존 변환 경로를 그대로 탄다.
    """

    if value is None:
        return None
    try:
        return json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        # 원인은 `from`으로만 보존한다 — payload도 타입 이름도 메시지에 넣지 않는다.
        raise RepositoryContractError(f"INVALID_JSON_{field.upper()}") from exc


def _require_transaction(connection: Connection) -> None:
    """write 진입점은 **활성 transaction**을 요구한다.

    `append_audit_log()`와 같은 가드다. 이것이 증명하는 범위는 좁지만("앞서 statement가
    한 번 실행됐다"), 업무 DML이 unit of work의 첫 statement가 되어 commit 없이 사라지는
    실수는 막는다. 같은 Connection·caller commit·rollback 원자성은 container 회귀가
    증명한다.
    """

    if not connection.in_transaction():
        raise RepositoryContractError(
            "NO_ACTIVE_TRANSACTION",
            "write는 호출자가 연 transaction 안에서만 수행합니다",
        )


# ---------------------------------------------------------------------------
# 내부 타입 — API DTO를 DB 입력으로 그대로 쓰지 않는다
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateAgentRunCommand:
    """`agent_run` 생성 입력.

    `002`가 `NOT NULL`로 요구하는 값을 **전부 명시적으로** 받는다. 대표 알람은
    `V5-C-1.1`이 고른 값을 그대로 저장하며 이 계층이 선정하지 않는다.
    """

    thread_id: str
    lot_id: str
    chamber_id: str
    autonomy_level: int
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    member_alarms: tuple[AlarmRef, ...]
    llm_model: str
    retry_of_run_id: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunRow:
    agent_run_id: str
    thread_id: str
    lot_id: str
    chamber_id: str
    status: RunStatus
    autonomy_level: int
    requested_alarm: AlarmRef
    representative_alarm: AlarmRef
    action: ActionCode | None
    severity: Severity | None
    retry_of_run_id: str | None
    llm_model: str | None
    prompt_version: str | None
    # **저장한 것은 되읽을 수 있어야 한다**(구현리뷰 묶음 1 필수 3).
    #
    # 이전에는 `finish_agent_run()`이 세 메트릭을 UPDATE하고도 row 계약에 없어
    # 반환값과 이후 조회가 방금 저장한 값을 버렸다. `evidence`는 아예 저장 경로가
    # 없었다.
    evidence: dict[str, Any] | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class PredictionRow:
    agent_run_id: str
    predicted_fault_code: FaultHypothesis
    confidence: float
    cause_summary: str
    evidence: dict[str, Any]
    llm_model: str
    prompt_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HumanReviewRow:
    review_id: int
    agent_run_id: str
    reviewed_fault_code: FaultHypothesis | None
    disposition: str
    label_source: str
    reviewer: str
    reviewed_at: datetime
    comment: str | None


#: Runtime 기본 경로가 쓰는 label. **`HIDDEN_GOLD`는 없다.**
#:
#: DB CHECK는 3값을 허용하지만 Runtime이 정답 label을 읽거나 쓰면 평가가 오염된다.
#: `HIDDEN_GOLD` 결합은 prediction hash를 먼저 고정하는 `V5-C-6.2`가 격리 adapter로
#: 수행한다.
RUNTIME_REVIEW_LABEL_SOURCES: Final[tuple[str, ...]] = (
    "HUMAN_REVIEW",
    "MENTOR_REVIEW",
)
HIDDEN_GOLD: Final = "HIDDEN_GOLD"


def _alarm_ref(source: object, alarm_id: object) -> AlarmRef:
    return AlarmRef(source=AlarmSource(str(source)), alarm_id=str(alarm_id))


def _run_row(row: Row[Any]) -> AgentRunRow:
    action = ActionCode(row.action) if row.action is not None else None
    return AgentRunRow(
        agent_run_id=row.agent_run_id,
        thread_id=row.thread_id,
        lot_id=row.lot_id,
        chamber_id=row.chamber_id,
        status=RunStatus(row.status),
        autonomy_level=int(row.autonomy_level),
        requested_alarm=_alarm_ref(row.requested_alarm_source, row.requested_alarm_id),
        representative_alarm=_alarm_ref(
            row.representative_alarm_source, row.representative_alarm_id
        ),
        action=action,
        severity=Severity(row.severity) if row.severity is not None else None,
        retry_of_run_id=row.retry_of_run_id,
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        evidence=None if row.evidence is None else dict(row.evidence),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


_RUN_COLUMNS = """
    agent_run_id, thread_id, retry_of_run_id, lot_id, chamber_id,
    requested_alarm_source, requested_alarm_id,
    representative_alarm_source, representative_alarm_id,
    status, autonomy_level, action, severity,
    llm_model, prompt_version, evidence,
    input_tokens, output_tokens, latency_ms,
    started_at, ended_at
"""


# ---------------------------------------------------------------------------
# agent_run · agent_run_alarm
# ---------------------------------------------------------------------------

_INSERT_RUN = text(
    f"""
    INSERT INTO agent_run (
        agent_run_id, thread_id, retry_of_run_id, lot_id, chamber_id,
        requested_alarm_source, requested_alarm_id,
        representative_alarm_source, representative_alarm_id,
        status, autonomy_level, llm_model, prompt_version, latency_ms
    ) VALUES (
        :agent_run_id, :thread_id, :retry_of_run_id, :lot_id, :chamber_id,
        :requested_alarm_source, :requested_alarm_id,
        :representative_alarm_source, :representative_alarm_id,
        :status, :autonomy_level, :llm_model, :prompt_version, 0
    )
    RETURNING {_RUN_COLUMNS}
    """
)

_INSERT_RUN_ALARM = text(
    """
    INSERT INTO agent_run_alarm (
        agent_run_id, alarm_source, alarm_id, is_representative
    ) VALUES (:agent_run_id, :alarm_source, :alarm_id, :is_representative)
    """
)


def _validate_create_command(
    command: CreateAgentRunCommand,
) -> CreateAgentRunCommand:
    """SQL 실행 **전에** 입력 계약을 닫고 **정규화된 command를 돌려준다.**

    문자열 경계까지 여기서 본다. DB에 보내면 `DataError`가 되고, 그것은 caller 입력
    오류인데도 driver 예외로 상위에 도달한다(1차 필수 2).

    ## 반환값을 쓰지 않으면 검증이 아무것도 보장하지 않는다

    초판은 trim한 값을 만들어 놓고 버린 뒤 원문을 bind했다. 그래서
    `" " + "x"*36 + " "`는 **검증에서 길이 36으로 통과하고 실제로는 38자가
    bind**됐다 — 단위 테스트가 증명한다고 믿은 계약이 write 경로에 없었다
    (구현리뷰 묶음 1 2차 필수 1-A). 이제 caller가 반환값을 쓰지 않으면 정규화되지
    않은 값이 저장된다는 사실이 호출부에서 눈에 보인다.

    AlarmRef는 정규화하지 않는다 — `AlarmRef.alarm_id`가 `NonEmptyId`
    (`strip_whitespace=True`)라 pydantic이 이미 trim한다. 여기서는 길이만 본다.
    """

    normalized = CreateAgentRunCommand(
        thread_id=_require_text(command.thread_id, "thread_id"),
        lot_id=_require_text(command.lot_id, "lot_id"),
        chamber_id=_require_text(command.chamber_id, "chamber_id"),
        autonomy_level=command.autonomy_level,
        requested_alarm=command.requested_alarm,
        representative_alarm=command.representative_alarm,
        member_alarms=command.member_alarms,
        retry_of_run_id=_optional_text(command.retry_of_run_id, "retry_of_run_id"),
        # 공개 Agent DTO는 llm_model을 필수로 반환한다. 신규 run이 legacy NULL을
        # 더 만들 수 없도록 DB write 경계에서도 필수값으로 고정한다.
        llm_model=_require_text(command.llm_model, "llm_model"),
        prompt_version=_optional_text(command.prompt_version, "prompt_version"),
    )
    for alarm in (
        command.requested_alarm,
        command.representative_alarm,
        *command.member_alarms,
    ):
        _require_text(alarm.alarm_id, "alarm_id")
    if not command.member_alarms:
        raise RepositoryContractError("EMPTY_MEMBER_ALARMS")
    tokens = [alarm.to_token() for alarm in command.member_alarms]
    if len(set(tokens)) != len(tokens):
        raise RepositoryContractError("DUPLICATE_MEMBER_ALARM")
    for label, alarm in (
        ("REQUESTED_ALARM_NOT_MEMBER", command.requested_alarm),
        ("REPRESENTATIVE_ALARM_NOT_MEMBER", command.representative_alarm),
    ):
        if alarm.to_token() not in tokens:
            raise RepositoryContractError(label)
    if command.autonomy_level not in (1, 2, 3):
        raise RepositoryContractError("INVALID_AUTONOMY_LEVEL")
    return normalized


def create_agent_run(
    connection: Connection,
    command: CreateAgentRunCommand,
    *,
    actor_type: ActorType = ActorType.AGENT,
    actor_id: str | None = None,
) -> AgentRunRow:
    """run + member AlarmRef + `AGENT_RUN_STARTED` 감사를 **한 UoW**로 저장한다.

    `is_representative`는 caller 입력이 아니라 `representative_alarm`과의 equality로
    파생한다. 따라서 `agent_run`의 대표 scalar와 `agent_run_alarm`의 대표 행이 어긋난
    상태를 이 API로 만들 수 없고, 대표 2건도 표현할 수 없다.
    """

    _require_transaction(connection)
    # **정규화된 command만 쓴다.** 아래에서 `command`를 다시 읽지 않는다.
    command = _validate_create_command(command)

    agent_run_id = new_agent_run_id()
    representative = command.representative_alarm.to_token()
    # **감사 record를 먼저 만든다.** 구성 실패가 업무 DML 뒤에 오면 안 된다.
    record = _run_audit_record(
        AuditEvent.AGENT_RUN_STARTED,
        entity_id=agent_run_id,
        actor_type=actor_type,
        actor_id=actor_id,
        after={
            "status": RunStatus.RUNNING.value,
            "lot_id": command.lot_id,
            "chamber_id": command.chamber_id,
            "representative_alarm": representative,
        },
    )

    def _run() -> Any:
        row = _insert_one(
            connection,
            _INSERT_RUN,
            {
                "agent_run_id": agent_run_id,
                "thread_id": command.thread_id,
                "retry_of_run_id": command.retry_of_run_id,
                "lot_id": command.lot_id,
                "chamber_id": command.chamber_id,
                "requested_alarm_source": command.requested_alarm.source.value,
                "requested_alarm_id": command.requested_alarm.alarm_id,
                "representative_alarm_source": (
                    command.representative_alarm.source.value
                ),
                "representative_alarm_id": command.representative_alarm.alarm_id,
                "status": RunStatus.RUNNING.value,
                "autonomy_level": command.autonomy_level,
                "llm_model": command.llm_model,
                "prompt_version": command.prompt_version,
            },
        )
        for alarm in command.member_alarms:
            connection.execute(
                _INSERT_RUN_ALARM,
                {
                    "agent_run_id": agent_run_id,
                    "alarm_source": alarm.source.value,
                    "alarm_id": alarm.alarm_id,
                    "is_representative": alarm.to_token() == representative,
                },
            )
        # **감사도 같은 경계 안이다.** 실패하면 sanitized 오류로 나간다.
        append_audit_log(connection, record)
        return row

    return _run_row(_write(connection, _run))


_SELECT_RUN = text(f"SELECT {_RUN_COLUMNS} FROM agent_run WHERE agent_run_id = :run_id")

_SELECT_RUN_BY_THREAD = text(
    f"SELECT {_RUN_COLUMNS} FROM agent_run WHERE thread_id = :thread_id"
)

#: 활성 incident는 `ux_agent_run_incident_active`가 강제하는 그 상태 집합이다.
ACTIVE_RUN_STATUSES: Final[tuple[str, ...]] = (
    RunStatus.RUNNING.value,
    RunStatus.WAITING_APPROVAL.value,
)

_SELECT_ACTIVE_RUN = text(
    f"""
    SELECT {_RUN_COLUMNS} FROM agent_run
    WHERE lot_id = :lot_id AND chamber_id = :chamber_id
      AND status = ANY(:statuses)
    """
)

_SELECT_RUN_ALARMS = text(
    """
    SELECT alarm_source, alarm_id, is_representative
    FROM agent_run_alarm
    WHERE agent_run_id = :run_id
    ORDER BY is_representative DESC, alarm_source, alarm_id
    """
)


def get_agent_run(connection: Connection, agent_run_id: str) -> AgentRunRow:
    row = _fetch_one(connection, _SELECT_RUN, {"run_id": agent_run_id}, "RUN_NOT_FOUND")
    return _run_row(row)


def get_agent_run_by_thread_exact(
    connection: Connection, thread_id: str
) -> AgentRunRow:
    """보상 경계에서 thread와 결속된 run이 정확히 한 건인지 확인한다.

    물리 스키마에는 ``thread_id`` UNIQUE가 없으므로 ``one_or_none``에 기대지 않는다.
    중복은 임의 한 행을 FAILED로 만들 수 없는 identity corruption이다.
    """

    rows = _fetch_all(
        connection,
        _SELECT_RUN_BY_THREAD,
        {"thread_id": _require_text(thread_id, "thread_id")},
    )
    if not rows:
        raise RepositoryNotFound("RUN_THREAD_NOT_FOUND")
    if len(rows) != 1:
        raise RepositoryContractError("RUN_THREAD_NOT_EXACTLY_ONE")
    return _run_row(rows[0])


_SELECT_RUN_FOR_UPDATE = text(
    f"SELECT {_RUN_COLUMNS} FROM agent_run WHERE agent_run_id = :run_id FOR UPDATE"
)


def lock_agent_run(connection: Connection, agent_run_id: str) -> AgentRunRow:
    """run row를 현 transaction 안에서 잠그고 반환한다."""

    _require_transaction(connection)
    row = _fetch_one(
        connection,
        _SELECT_RUN_FOR_UPDATE,
        {"run_id": agent_run_id},
        "RUN_NOT_FOUND",
    )
    return _run_row(row)


_UPDATE_RUN_LLM_USAGE = text(
    f"""
    UPDATE agent_run
    SET llm_model = :llm_model, prompt_version = :prompt_version,
        input_tokens = :input_tokens, output_tokens = :output_tokens
    WHERE agent_run_id = :run_id AND status = ANY(:active)
    RETURNING {_RUN_COLUMNS}
    """
)


def _token_count(value: object, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > 2_147_483_647
    ):
        raise RepositoryContractError(f"INVALID_{field.upper()}")
    return value


def record_run_llm_usage(
    connection: Connection,
    agent_run_id: str,
    *,
    llm_model: str,
    prompt_version: str,
    input_tokens: int,
    output_tokens: int,
) -> AgentRunRow:
    """실제 LLM usage를 활성 run에 더한다.

    동일 run의 경쟁 writer를 ``FOR UPDATE``로 직렬화하며, model·prompt
    provenance가 다른 값으로 조용히 덮어쓰이지 않게 한다.
    """

    _require_transaction(connection)
    llm_model = _require_text(llm_model, "llm_model")
    prompt_version = _require_text(prompt_version, "prompt_version")
    input_tokens = _token_count(input_tokens, "input_tokens")
    output_tokens = _token_count(output_tokens, "output_tokens")

    current = lock_agent_run(connection, agent_run_id)
    if current.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
        raise RepositoryConflict("RUN_NOT_ACTIVE")
    if current.llm_model is not None and current.llm_model != llm_model:
        raise RepositoryConflict("PREDICTION_CONFLICT")
    if current.prompt_version is not None and current.prompt_version != prompt_version:
        raise RepositoryConflict("PREDICTION_CONFLICT")

    total_input = (current.input_tokens or 0) + input_tokens
    total_output = (current.output_tokens or 0) + output_tokens
    if (
        total_input > 2_147_483_647
        or total_output > 2_147_483_647
        or total_input + total_output > 2_147_483_647
    ):
        raise RepositoryContractError("RUN_TOKEN_OVERFLOW")

    try:
        row = connection.execute(
            _UPDATE_RUN_LLM_USAGE,
            {
                "run_id": agent_run_id,
                "active": list(ACTIVE_RUN_STATUSES),
                "llm_model": llm_model,
                "prompt_version": prompt_version,
                "input_tokens": total_input,
                "output_tokens": total_output,
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        raise RepositoryConflict("RUN_NOT_ACTIVE")
    return _run_row(row)


def find_active_run(
    connection: Connection, *, lot_id: str, chamber_id: str
) -> AgentRunRow | None:
    """활성 incident run을 찾는다. 없으면 `None`이다 — 없음은 오류가 아니다."""

    try:
        row = connection.execute(
            _SELECT_ACTIVE_RUN,
            {
                "lot_id": lot_id,
                "chamber_id": chamber_id,
                "statuses": list(ACTIVE_RUN_STATUSES),
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return None if row is None else _run_row(row)


def list_run_alarms(connection: Connection, agent_run_id: str) -> list[AlarmRef]:
    """member AlarmRef를 **안정 정렬**로 돌려준다. 대표가 항상 먼저다."""

    rows = _fetch_all(connection, _SELECT_RUN_ALARMS, {"run_id": agent_run_id})
    return [_alarm_ref(row.alarm_source, row.alarm_id) for row in rows]


_UPDATE_RUN_ACTION = text(
    f"""
    UPDATE agent_run SET action = :action, severity = :severity
    WHERE agent_run_id = :run_id
    RETURNING {_RUN_COLUMNS}
    """
)


def set_run_action(
    connection: Connection, agent_run_id: str, action: ActionCode | None
) -> AgentRunRow:
    """조치를 저장한다. **severity를 인자로 받지 않는다.**

    `resolve_severity()`가 유일한 파생 경로이므로 `(MONITORING, HIGH)` 같은 짝을 이
    API로 만들 수 없다. `003`의 named CHECK가 최종 방어선이다.
    """

    _require_transaction(connection)
    severity = None if action is None else resolve_severity(action)
    row = _fetch_one(
        connection,
        _UPDATE_RUN_ACTION,
        {
            "run_id": agent_run_id,
            "action": None if action is None else action.value,
            "severity": None if severity is None else severity.value,
        },
        "RUN_NOT_FOUND",
    )
    updated = _run_row(row)
    # 저장된 값을 되읽어 파생 규칙과 대조한다. DB가 조용히 다른 값을 갖는 상태를
    # 통과시키지 않는다.
    if (updated.action, updated.severity) != (action, severity):
        raise RepositoryContractError("ACTION_SEVERITY_MISMATCH")
    return updated


ACTION_PROVENANCE_SCHEMA: Final = "action-provenance-v1"
ACTION_PROVENANCE_KEY: Final = "action_provenance"
REHYDRATION_SNAPSHOT_KEY: Final = "rehydration_snapshot"

_UPDATE_RUN_EVIDENCE = text(
    f"""
    UPDATE agent_run SET evidence = CAST(:evidence AS jsonb)
    WHERE agent_run_id = :run_id AND status = ANY(:active)
    RETURNING {_RUN_COLUMNS}
    """
)


def merge_run_action_provenance(
    connection: Connection,
    agent_run_id: str,
    *,
    action_policy_version: str | None = None,
    member_alarms: Sequence[AlarmRef] | None = None,
    rehydration_snapshot: Mapping[str, Any] | None = None,
    terminal_evidence: Mapping[str, Any] | None = None,
) -> AgentRunRow:
    """action provenance와 terminal evidence를 기존 JSON에 손실 없이 합친다.

    action 생성 시에는 policy version과 AlarmRef snapshot을 함께 받고, graph 종료
    시에는 ``terminal_evidence``만 받아 이미 저장된 provenance를 보존한다. 기존
    provenance와 다른 값을 같은 run에 다시 쓰는 요청은 조용히 교체하지 않는다.
    """

    _require_transaction(connection)
    if (action_policy_version is None) != (member_alarms is None):
        raise RepositoryContractError("ACTION_PROVENANCE_INCOMPLETE")
    if terminal_evidence is not None:
        reserved = {ACTION_PROVENANCE_KEY, REHYDRATION_SNAPSHOT_KEY}
        if reserved.intersection(terminal_evidence):
            raise RepositoryContractError("ACTION_PROVENANCE_RESERVED")

    current = lock_agent_run(connection, agent_run_id)
    if current.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
        raise RepositoryConflict("RUN_NOT_ACTIVE")
    merged: dict[str, Any] = dict(current.evidence or {})

    if action_policy_version is not None and member_alarms is not None:
        version = _require_text(action_policy_version, "action_policy_version")
        if not member_alarms:
            raise RepositoryContractError("EMPTY_ACTION_MEMBER_ALARMS")
        tokens = [alarm.to_token() for alarm in member_alarms]
        if len(tokens) != len(set(tokens)):
            raise RepositoryContractError("DUPLICATE_ACTION_MEMBER_ALARMS")
        provenance = {
            "schema": ACTION_PROVENANCE_SCHEMA,
            "action_policy_version": version,
            "member_alarms": [alarm.model_dump(mode="json") for alarm in member_alarms],
        }
        existing = merged.get(ACTION_PROVENANCE_KEY)
        if existing is not None and existing != provenance:
            raise RepositoryConflict("ACTION_PROVENANCE_MISMATCH")
        merged[ACTION_PROVENANCE_KEY] = provenance

    if rehydration_snapshot is not None:
        # 전용 DTO 검증은 C-3.4 service가 쓰기 전에 수행한다. Repository는 JSON
        # identity를 멱등하게 합치되, 같은 run의 snapshot 교체는 허용하지 않는다.
        snapshot = dict(rehydration_snapshot)
        existing_snapshot = merged.get(REHYDRATION_SNAPSHOT_KEY)
        if existing_snapshot is not None and existing_snapshot != snapshot:
            raise RepositoryConflict("REHYDRATE_SNAPSHOT_CONFLICT")
        merged[REHYDRATION_SNAPSHOT_KEY] = snapshot

    if terminal_evidence is not None:
        merged.update(dict(terminal_evidence))

    evidence_json = _json_payload(merged, "evidence")
    try:
        row = connection.execute(
            _UPDATE_RUN_EVIDENCE,
            {
                "run_id": agent_run_id,
                "active": list(ACTIVE_RUN_STATUSES),
                "evidence": evidence_json,
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        raise RepositoryConflict("RUN_NOT_ACTIVE")
    return _run_row(row)


#: `finish_agent_run()`이 받을 수 있는 terminal 상태와 그 감사 event.
TERMINAL_EVENTS: Final[Mapping[RunStatus, AuditEvent]] = {
    RunStatus.COMPLETED: AuditEvent.AGENT_RUN_COMPLETED,
    RunStatus.FAILED: AuditEvent.AGENT_RUN_FAILED,
}

ACTIVE_TIMING_KEY: Final = "active_timing"
ACTIVE_TIMING_SCHEMA: Final = "agent-active-timing-v1"


def _active_latency_snapshot_ms(
    *,
    status: RunStatus,
    subtotal_ms: int | None,
    started_at: datetime,
    timing: object,
    now: datetime,
) -> int:
    """저장 subtotal과 현재 활성 구간으로 HITL 제외 latency를 계산한다.

    실행 갱신과 public read model이 이 함수를 함께 사용한다. SQL에서 timestamp를
    재해석하거나 ``started_at``으로 별도 fallback하지 않는다.
    """

    if now.tzinfo is None:
        raise RepositoryContractError("ACTIVE_TIMING_CLOCK_INVALID")
    if subtotal_ms is None or subtotal_ms < 0:
        raise RepositoryContractError("ACTIVE_TIMING_SUBTOTAL_INVALID")
    if status is not RunStatus.RUNNING:
        return subtotal_ms

    if timing is None:
        active_started_at = started_at if subtotal_ms == 0 else None
    else:
        if (
            not isinstance(timing, Mapping)
            or timing.get("schema") != ACTIVE_TIMING_SCHEMA
        ):
            raise RepositoryContractError("ACTIVE_TIMING_INVALID")
        raw = timing.get("active_started_at")
        if raw is None:
            active_started_at = None
        elif not isinstance(raw, str):
            raise RepositoryContractError("ACTIVE_TIMING_INVALID")
        else:
            try:
                active_started_at = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise RepositoryContractError("ACTIVE_TIMING_INVALID") from exc
            if active_started_at.tzinfo is None:
                raise RepositoryContractError("ACTIVE_TIMING_INVALID")

    if active_started_at is None:
        raise RepositoryContractError("ACTIVE_TIMING_START_MISSING")
    try:
        elapsed_ms = int((now - active_started_at).total_seconds() * 1000)
    except (TypeError, OverflowError) as exc:
        raise RepositoryContractError("ACTIVE_TIMING_CLOCK_INVALID") from exc
    return min(2_147_483_647, subtotal_ms + max(0, elapsed_ms))


def active_run_latency_ms(run: AgentRunRow, *, now: datetime) -> int:
    """HITL 사람 대기를 제외한 현재 활성시간 snapshot을 계산한다."""

    return _active_latency_snapshot_ms(
        status=run.status,
        subtotal_ms=run.latency_ms,
        started_at=run.started_at,
        timing=(run.evidence or {}).get(ACTIVE_TIMING_KEY),
        now=now,
    )


def _timing_evidence(
    run: AgentRunRow,
    *,
    active_started_at: datetime | None,
) -> dict[str, Any]:
    evidence = dict(run.evidence or {})
    evidence[ACTIVE_TIMING_KEY] = {
        "schema": ACTIVE_TIMING_SCHEMA,
        "active_started_at": (
            None if active_started_at is None else active_started_at.isoformat()
        ),
    }
    return evidence


_FINISH_RUN = text(
    f"""
    UPDATE agent_run
    SET status = :status, ended_at = clock_timestamp(),
        evidence = coalesce(CAST(:evidence AS jsonb), evidence),
        input_tokens = coalesce(:input_tokens, input_tokens),
        output_tokens = coalesce(:output_tokens, output_tokens),
        latency_ms = coalesce(:latency_ms, latency_ms)
    WHERE agent_run_id = :run_id AND status = ANY(:active)
    RETURNING {_RUN_COLUMNS}
    """
)

_RUN_EXISTS = text("SELECT 1 FROM agent_run WHERE agent_run_id = :run_id")


def finish_agent_run(
    connection: Connection,
    agent_run_id: str,
    status: RunStatus,
    *,
    actor_type: ActorType = ActorType.AGENT,
    actor_id: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
) -> AgentRunRow:
    """활성 run을 terminal로 옮기고 같은 transaction에 감사를 남긴다.

    **활성 상태에서만 전이한다.** 이미 terminal인 run을 다시 끝내면 `ended_at`이
    덮이고 감사가 두 번 남는다. `WHERE status = ANY(active)`가 그것을 0행으로 만든다.

    consolidated `evidence`는 이 terminal 저장이 소유한다 — run이 끝나는 시점에
    확정되는 값이므로 상태 전이와 **원자적으로** 함께 쓴다. `None`이면 기존 값을
    보존한다(구현리뷰 묶음 1 필수 3).
    """

    _require_transaction(connection)
    event = TERMINAL_EVENTS.get(status)
    if event is None:
        raise RepositoryContractError("NOT_TERMINAL_STATUS")
    record = _run_audit_record(
        event,
        entity_id=agent_run_id,
        actor_type=actor_type,
        actor_id=actor_id,
        after={"status": status.value},
    )
    evidence_json = _json_payload(evidence, "evidence")

    def _run() -> Any:
        row = connection.execute(
            _FINISH_RUN,
            {
                "run_id": agent_run_id,
                "status": status.value,
                "active": list(ACTIVE_RUN_STATUSES),
                "evidence": evidence_json,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
            },
        ).one_or_none()
        if row is None:
            # **없는 run과 이미 끝난 run을 구분한다**(구현리뷰 묶음 1 권장 1).
            #
            # 둘 다 `RepositoryNotFound`면 상위가 NotFound를 404로 mapping할 때
            # 존재하는 run의 상태 충돌까지 404가 된다.
            exists = connection.execute(
                _RUN_EXISTS, {"run_id": agent_run_id}
            ).one_or_none()
            if exists is None:
                raise RepositoryNotFound("RUN_NOT_FOUND")
            raise RepositoryConflict("RUN_NOT_ACTIVE")
        append_audit_log(connection, record)
        return row

    return _run_row(_write(connection, _run))


def finish_agent_run_with_active_latency(
    connection: Connection,
    agent_run_id: str,
    status: RunStatus,
    *,
    now: datetime,
    evidence: Mapping[str, Any] | None = None,
) -> AgentRunRow:
    """활성 구간을 닫고 terminal 상태·누적 latency를 원자적으로 저장한다."""

    _require_transaction(connection)
    run = lock_agent_run(connection, agent_run_id)
    latency_ms = active_run_latency_ms(run, now=now)
    closed = dict(run.evidence or {}) if evidence is None else dict(evidence)
    closed[ACTIVE_TIMING_KEY] = {
        "schema": ACTIVE_TIMING_SCHEMA,
        "active_started_at": None,
    }
    return finish_agent_run(
        connection,
        agent_run_id,
        status,
        evidence=closed,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# agent_prediction · agent_prediction_review
# ---------------------------------------------------------------------------

_PREDICTION_COLUMNS = """
    agent_run_id, predicted_fault_code, confidence, cause_summary,
    evidence, llm_model, prompt_version, created_at
"""

_INSERT_PREDICTION = text(
    f"""
    INSERT INTO agent_prediction (
        agent_run_id, predicted_fault_code, confidence, cause_summary,
        evidence, llm_model, prompt_version
    ) VALUES (
        :agent_run_id, :predicted_fault_code, :confidence, :cause_summary,
        CAST(:evidence AS jsonb), :llm_model, :prompt_version
    )
    RETURNING {_PREDICTION_COLUMNS}
    """
)

_SELECT_PREDICTION = text(
    f"SELECT {_PREDICTION_COLUMNS} FROM agent_prediction WHERE agent_run_id = :run_id"
)


def _prediction_row(row: Row[Any]) -> PredictionRow:
    confidence = row.confidence
    return PredictionRow(
        agent_run_id=row.agent_run_id,
        predicted_fault_code=FaultHypothesis(row.predicted_fault_code),
        confidence=float(confidence) if isinstance(confidence, Decimal) else confidence,
        cause_summary=row.cause_summary,
        evidence=dict(row.evidence or {}),
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        created_at=row.created_at,
    )


def insert_prediction(
    connection: Connection,
    *,
    agent_run_id: str,
    predicted_fault_code: FaultHypothesis,
    confidence: float,
    cause_summary: str,
    evidence: Mapping[str, Any],
    llm_model: str,
    prompt_version: str,
    actor_type: ActorType = ActorType.AGENT,
    actor_id: str | None = None,
) -> PredictionRow:
    """가설을 저장하고 `HYPOTHESIS_GENERATED` 감사를 같은 transaction에 남긴다."""

    _require_transaction(connection)
    # **정규화 결과를 그대로 쓴다.** 원문을 bind하면 검증이 보장하는 것이 없다.
    cause_summary = _require_text(cause_summary, "cause_summary")
    llm_model = _require_text(llm_model, "llm_model")
    prompt_version = _require_text(prompt_version, "prompt_version")
    if not 0 <= float(confidence) <= 1:
        raise RepositoryContractError("CONFIDENCE_OUT_OF_RANGE")
    evidence_json = _json_payload(evidence, "evidence")
    record = _run_audit_record(
        AuditEvent.HYPOTHESIS_GENERATED,
        entity_id=agent_run_id,
        actor_type=actor_type,
        actor_id=actor_id,
        # **전체 evidence를 복제하지 않는다.** 감사는 무엇이 언제 생겼는지를 남기고
        # 본문은 원본 table이 갖는다.
        after={
            "predicted_fault_code": predicted_fault_code.value,
            "confidence": float(confidence),
        },
    )

    def _run() -> Any:
        row = _insert_one(
            connection,
            _INSERT_PREDICTION,
            {
                "agent_run_id": agent_run_id,
                "predicted_fault_code": predicted_fault_code.value,
                "confidence": confidence,
                "cause_summary": cause_summary,
                "evidence": evidence_json,
                "llm_model": llm_model,
                "prompt_version": prompt_version,
            },
        )
        append_audit_log(connection, record)
        return row

    return _prediction_row(_write(connection, _run))


def get_prediction(connection: Connection, agent_run_id: str) -> PredictionRow:
    row = _fetch_one(
        connection,
        _SELECT_PREDICTION,
        {"run_id": agent_run_id},
        "PREDICTION_NOT_FOUND",
    )
    return _prediction_row(row)


def get_prediction_or_none(
    connection: Connection, agent_run_id: str
) -> PredictionRow | None:
    """예측이 없는 정상 상태를 ``None``으로 반환한다."""

    try:
        row = connection.execute(
            _SELECT_PREDICTION, {"run_id": agent_run_id}
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return None if row is None else _prediction_row(row)


_REVIEW_COLUMNS = """
    review_id, agent_run_id, reviewed_fault_code, disposition,
    label_source, reviewer, reviewed_at, comment
"""

_INSERT_REVIEW = text(
    f"""
    INSERT INTO agent_prediction_review (
        agent_run_id, reviewed_fault_code, disposition,
        label_source, reviewer, comment
    ) VALUES (
        :agent_run_id, :reviewed_fault_code, :disposition,
        :label_source, :reviewer, :comment
    )
    RETURNING {_REVIEW_COLUMNS}
    """
)

#: **`HIDDEN_GOLD`를 SQL에서 제외한다.** 애플리케이션 필터로 걸러 내면 새 조회 경로가
#: 생길 때마다 같은 실수를 반복할 수 있다.
_SELECT_REVIEWS = text(
    f"""
    SELECT {_REVIEW_COLUMNS} FROM agent_prediction_review
    WHERE agent_run_id = :run_id AND label_source = ANY(:allowed)
    ORDER BY review_id
    """
)


def _review_row(row: Row[Any]) -> HumanReviewRow:
    return HumanReviewRow(
        review_id=int(row.review_id),
        agent_run_id=row.agent_run_id,
        reviewed_fault_code=(
            FaultHypothesis(row.reviewed_fault_code)
            if row.reviewed_fault_code is not None
            else None
        ),
        disposition=row.disposition,
        label_source=row.label_source,
        reviewer=row.reviewer,
        reviewed_at=row.reviewed_at,
        comment=row.comment,
    )


def insert_human_prediction_review(
    connection: Connection,
    *,
    agent_run_id: str,
    disposition: str,
    label_source: str,
    reviewer: str,
    reviewed_fault_code: FaultHypothesis | None = None,
    comment: str | None = None,
) -> HumanReviewRow:
    """사람 review를 저장한다. **`HIDDEN_GOLD`는 받지 않는다.**

    감사 event를 남기지 않는다 — `EVENT_ENTITY_TYPE` 9종에 review에 해당하는 event가
    없고, 없는 event를 이 Task가 만들지 않는다.
    """

    _require_transaction(connection)
    reviewer = _require_text(reviewer, "reviewer")
    disposition = _require_text(disposition, "disposition")
    if label_source not in RUNTIME_REVIEW_LABEL_SOURCES:
        raise RepositoryContractError("LABEL_SOURCE_NOT_ALLOWED")
    row = _insert_one(
        connection,
        _INSERT_REVIEW,
        {
            "agent_run_id": agent_run_id,
            "reviewed_fault_code": (
                None if reviewed_fault_code is None else reviewed_fault_code.value
            ),
            "disposition": disposition,
            "label_source": label_source,
            "reviewer": reviewer,
            "comment": comment,
        },
    )
    return _review_row(row)


def list_human_prediction_reviews(
    connection: Connection, agent_run_id: str
) -> list[HumanReviewRow]:
    rows = _fetch_all(
        connection,
        _SELECT_REVIEWS,
        {"run_id": agent_run_id, "allowed": list(RUNTIME_REVIEW_LABEL_SOURCES)},
    )
    return [_review_row(row) for row in rows]


# ---------------------------------------------------------------------------
# 실행 helper
# ---------------------------------------------------------------------------


def _fetch_one(
    connection: Connection,
    statement: Any,
    params: Mapping[str, Any],
    missing_code: str,
) -> Row[Any]:
    try:
        row = connection.execute(statement, dict(params)).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        raise RepositoryNotFound(missing_code)
    return row


def _insert_one(
    connection: Connection, statement: Any, params: Mapping[str, Any]
) -> Row[Any]:
    """`INSERT ... RETURNING` 전용 실행. **"행이 없다" 분기가 없다.**

    성공하면 정확히 1행이고 실패하면 0행이 아니라 예외다. 그래서 INSERT 자리에
    `_fetch_one(..., missing_code)`를 쓰면 그 `missing_code`가 **한 번도 도달하지
    못한다**(구현리뷰 필수 1). 죽은 분기라서가 아니라, 후속 C Task가 "없는 run이면
    `RUN_NOT_FOUND`"로 읽고 404로 mapping할 것이라서 문제다.

    실제로 "부모가 없다"를 알려 주는 것은 FK 위반이다. **INSERT 문에서 `23503`은
    자식을 넣는데 부모가 없다는 뜻뿐이므로** 여기서만 `RepositoryNotFound`로 올린다.
    CHECK 위반은 그대로 `CONSTRAINT_VIOLATION`이라 이제 둘이 구분된다.
    """

    try:
        row = connection.execute(statement, dict(params)).one()
    except IntegrityError as exc:
        if _sqlstate(exc) == FOREIGN_KEY_VIOLATION:
            code = FOREIGN_KEY_CODES.get(_constraint_name(exc) or "")
            if code is not None:
                raise RepositoryNotFound(code) from exc
        raise _translate(exc) from exc
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return row


def _fetch_all(
    connection: Connection, statement: Any, params: Mapping[str, Any]
) -> Sequence[Row[Any]]:
    try:
        return connection.execute(statement, dict(params)).all()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc


def _run_audit_record(
    event: AuditEvent,
    *,
    entity_id: str,
    actor_type: ActorType,
    actor_id: str | None,
    after: dict[str, Any],
    before: dict[str, Any] | None = None,
) -> AuditRecord:
    """감사 record를 **업무 DML 전에** 만든다.

    `AuditRecord` 검증(entity_id·actor_id 길이 등)이 업무 DML 뒤에 일어나면, 그 예외는
    DB transaction을 abort하지 않는다. caller가 transaction block 안에서 그것을 잡고
    계속하면 **업무 row만 commit**될 여지가 생긴다(구현리뷰 묶음 1 필수 1).

    구성 실패는 caller 입력 오류이므로 sanitized `RepositoryContractError`다.
    """

    try:
        return AuditRecord(
            event_type=event,
            actor_type=actor_type,
            entity_id=entity_id,
            actor_id=actor_id,
            before=before,
            after=after,
        )
    except (AuditContractError, ValueError) as exc:
        raise RepositoryContractError("AUDIT_RECORD_INVALID") from exc


def _write(connection: Connection, action: Any) -> Any:
    """업무 DML과 감사 append를 **하나의 Repository 예외 경계**로 감싼다.

    이전에는 업무 DML만 `_translate()`로 감쌌고 감사 append는 그 바깥이었다. 그래서
    Common helper의 SQL이 실패하면 `DataError`가 그대로 상위로 올라갔고, statement와
    parameter가 예외 문자열에 실려 나갈 수 있었다 — 계획 §1.1·§6의 "SQL·DSN·driver
    message를 public exception에 넣지 않는다"와 정면으로 충돌한다.
    """

    try:
        return action()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    except AuditContractError as exc:
        raise RepositoryContractError("AUDIT_CONTRACT_VIOLATION") from exc


# ---------------------------------------------------------------------------
# action_history — Runtime action 생성 projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActionHistoryRow:
    action_id: str
    lot_id: str
    chamber_id: str
    action_code: ActionCode
    reason: str
    approval_required: bool
    approval_status: ApprovalStatus
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


_ACTION_HISTORY_COLUMNS = """
    action_id, lot_id, chamber_id, action_code, reason,
    approval_required, approval_status, approved_by, approved_at, created_at
"""

_INSERT_ACTION_HISTORY = text(
    f"""
    INSERT INTO action_history (
        action_id, lot_id, recipe_step_name, equipment_id, chamber_id,
        trigger_alarm_lot_hist_id, action_code, reason,
        approval_required, approval_status, approved_by, approved_at,
        notify_status, notify_at, mes_status, mes_at, created_at
    ) VALUES (
        :action_id, :lot_id, NULL, NULL, :chamber_id,
        NULL, :action_code, :reason,
        :approval_required, :approval_status, :approved_by, :approved_at,
        NULL, NULL, NULL, NULL, :created_at
    )
    RETURNING {_ACTION_HISTORY_COLUMNS}
    """
)

_SELECT_ACTION_HISTORY = text(
    f"SELECT {_ACTION_HISTORY_COLUMNS} FROM action_history WHERE action_id = :action_id"
)


def _action_history_row(row: Row[Any]) -> ActionHistoryRow:
    required = str(row.approval_required).strip()
    if required not in {"Y", "N"}:
        raise RepositoryContractError("ACTION_APPROVAL_FLAG_INVALID")
    try:
        action = ActionCode(row.action_code)
        status = ApprovalStatus(row.approval_status)
    except ValueError as exc:
        raise RepositoryContractError("ACTION_HISTORY_CONTRACT_INVALID") from exc
    return ActionHistoryRow(
        action_id=row.action_id,
        lot_id=row.lot_id,
        chamber_id=row.chamber_id,
        action_code=action,
        reason=row.reason,
        approval_required=required == "Y",
        approval_status=status,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        created_at=row.created_at,
    )


def insert_action_history(
    connection: Connection,
    *,
    action_id: str,
    lot_id: str,
    chamber_id: str,
    action_code: ActionCode,
    reason: str,
    created_at: datetime,
) -> ActionHistoryRow:
    """규칙 결정 한 건을 base ``action_history`` projection으로 저장한다."""

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    lot_id = _require_text(lot_id, "lot_id")
    chamber_id = _require_text(chamber_id, "chamber_id")
    reason = _require_text(reason, "reason")
    try:
        action = ActionCode(action_code)
    except ValueError as exc:
        raise RepositoryContractError("INVALID_ACTION_CODE") from exc
    approval_required = requires_approval(action)
    status = ApprovalStatus.PENDING if approval_required else ApprovalStatus.AUTO
    row = _insert_one(
        connection,
        _INSERT_ACTION_HISTORY,
        {
            "action_id": action_id,
            "lot_id": lot_id,
            "chamber_id": chamber_id,
            "action_code": action.value,
            "reason": reason,
            "approval_required": "Y" if approval_required else "N",
            "approval_status": status.value,
            "approved_by": None if approval_required else "system",
            "approved_at": None if approval_required else created_at,
            "created_at": created_at,
        },
    )
    return _action_history_row(row)


def get_action_history(connection: Connection, action_id: str) -> ActionHistoryRow:
    """승인 identity 검증에 필요한 action projection을 읽는다."""

    row = _fetch_one(
        connection,
        _SELECT_ACTION_HISTORY,
        {"action_id": _require_text(action_id, "action_id")},
        "ACTION_NOT_FOUND",
    )
    return _action_history_row(row)


# ---------------------------------------------------------------------------
# agent_run_action — action은 만들지 않고 **link만** 한다
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunActionRow:
    agent_run_id: str
    action_id: str
    link_role: ActionLinkRole
    lot_id: str
    chamber_id: str
    trigger_alarm: AlarmRef
    linked_at: datetime


_RUN_ACTION_COLUMNS = """
    agent_run_id, action_id, link_role, lot_id, chamber_id,
    trigger_alarm_source, trigger_alarm_id, linked_at
"""

_INSERT_RUN_ACTION = text(
    f"""
    INSERT INTO agent_run_action (
        agent_run_id, action_id, link_role, lot_id, chamber_id,
        trigger_alarm_source, trigger_alarm_id
    ) VALUES (
        :agent_run_id, :action_id, :link_role, :lot_id, :chamber_id,
        :trigger_alarm_source, :trigger_alarm_id
    )
    RETURNING {_RUN_ACTION_COLUMNS}
    """
)

_SELECT_RUN_ACTION = text(
    f"SELECT {_RUN_ACTION_COLUMNS} FROM agent_run_action WHERE agent_run_id = :run_id"
)

#: `ux_agent_run_action_incident`가 강제하는 그 조건이다 — incident당 `CREATED` 1건.
_SELECT_CREATED_ACTION = text(
    f"""
    SELECT {_RUN_ACTION_COLUMNS} FROM agent_run_action
    WHERE lot_id = :lot_id AND chamber_id = :chamber_id
      AND link_role = :created
    """
)


def _run_action_row(row: Row[Any]) -> RunActionRow:
    return RunActionRow(
        agent_run_id=row.agent_run_id,
        action_id=row.action_id,
        link_role=ActionLinkRole(row.link_role),
        lot_id=row.lot_id,
        chamber_id=row.chamber_id,
        trigger_alarm=_alarm_ref(row.trigger_alarm_source, row.trigger_alarm_id),
        linked_at=row.linked_at,
    )


def link_run_action(
    connection: Connection,
    *,
    agent_run_id: str,
    action_id: str,
    link_role: ActionLinkRole,
    lot_id: str,
    chamber_id: str,
    trigger_alarm: AlarmRef,
) -> RunActionRow:
    """기존 action을 run에 잇는다. **action을 만들지 않는다.**

    `action_id`는 후속 action 생성 transaction이 `new_action_id()`로 만든 값을 받는다
    (`V5-C-3.2` 소관). 이 계층은 그 값을 link할 뿐이다.

    감사 event를 남기지 않는다 — `EVENT_ENTITY_TYPE` 9종에 link event가 없고, 없는
    event를 이 Task가 만들지 않는다.
    """

    _require_transaction(connection)
    payload = {
        "agent_run_id": agent_run_id,
        "action_id": _require_text(action_id, "action_id"),
        "link_role": ActionLinkRole(link_role).value,
        "lot_id": _require_text(lot_id, "lot_id"),
        "chamber_id": _require_text(chamber_id, "chamber_id"),
        "trigger_alarm_source": trigger_alarm.source.value,
        "trigger_alarm_id": _require_text(trigger_alarm.alarm_id, "alarm_id"),
    }
    # FK 두 개가 서로 다른 뜻이다 — run이 없으면 `RUN_NOT_FOUND`,
    # action이 없으면 `ACTION_NOT_FOUND`. 단일 `missing_code`로는 표현할 수 없었다.
    row = _insert_one(connection, _INSERT_RUN_ACTION, payload)
    return _run_action_row(row)


def get_run_action(connection: Connection, agent_run_id: str) -> RunActionRow:
    row = _fetch_one(
        connection,
        _SELECT_RUN_ACTION,
        {"run_id": agent_run_id},
        "RUN_ACTION_NOT_FOUND",
    )
    return _run_action_row(row)


def find_run_action(connection: Connection, agent_run_id: str) -> RunActionRow | None:
    """run의 action link를 찾는다. 멱등 판정을 위해 없음은 ``None``이다."""

    try:
        row = connection.execute(
            _SELECT_RUN_ACTION, {"run_id": agent_run_id}
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return None if row is None else _run_action_row(row)


def find_created_action(
    connection: Connection, *, lot_id: str, chamber_id: str
) -> RunActionRow | None:
    """incident의 `CREATED` link를 찾는다. 없으면 `None`이다.

    `V5-C-3.2`가 "incident당 유효 action 1건"을 판정할 때 쓰는 입력이며, 그 판정 자체는
    이 계층의 것이 아니다.
    """

    try:
        row = connection.execute(
            _SELECT_CREATED_ACTION,
            {
                "lot_id": lot_id,
                "chamber_id": chamber_id,
                "created": ActionLinkRole.CREATED.value,
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return None if row is None else _run_action_row(row)


# ---------------------------------------------------------------------------
# agent_tool_call — 예약과 종료는 **서로 다른 unit of work**다
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCallRow:
    tool_call_id: str
    agent_run_id: str
    call_seq: int
    tool_name: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    status: ToolCallStatus
    latency_ms: int | None
    called_at: datetime
    error_msg: str | None


@dataclass(frozen=True, slots=True)
class ToolBudgetCounts:
    """run row lock 아래에서 읽은 예산 소비 Tool 호출 집계."""

    total: int
    by_tool: Mapping[str, int]
    pending_reservations: int
    autonomy_level: int


#: 예약 row가 쓰는 sentinel.
#:
#: `002`의 status CHECK는 `SUCCESS·ERROR·TIMEOUT` 3값뿐이라 `STARTED`가 없다. 그래서
#: 예약을 **보수적으로** 표현한다 — 끝나지 않은 호출은 실패로 센다. 예산이 실제보다
#: 적게 세어지는 쪽이 안전하다.
RESERVED_ERROR_MSG: Final = "CALL_RESERVED_NOT_COMPLETED"

#: Tool output 최상위에 오면 안 되는 실행 metadata key.
#:
#: domain payload와 실행 metadata를 섞으면 `agent_tool_call`의 컬럼과 output이 같은
#: 사실을 두 벌로 갖게 되고, 그 둘이 갈리는 순간 어느 쪽이 정본인지 알 수 없다.
RESERVED_TOOL_OUTPUT_KEYS: Final[tuple[str, ...]] = (
    "latency_ms",
    "status",
    "call_seq",
    "tool_call_id",
)

_TOOL_CALL_COLUMNS = """
    tool_call_id, agent_run_id, call_seq, tool_name, input, output,
    status, latency_ms, called_at, error_msg
"""

#: **run row를 잠근다.** `max(call_seq)+1`은 lock 없이는 두 session이 같은 값을 본다.
_LOCK_RUN = text(
    "SELECT autonomy_level FROM agent_run " "WHERE agent_run_id = :run_id FOR UPDATE"
)

_NEXT_CALL_SEQ = text(
    """
    SELECT coalesce(max(call_seq), 0) + 1 AS next_seq
    FROM agent_tool_call WHERE agent_run_id = :run_id
    """
)

_INSERT_TOOL_CALL = text(
    f"""
    INSERT INTO agent_tool_call (
        tool_call_id, agent_run_id, call_seq, tool_name, input,
        status, error_msg
    ) VALUES (
        :tool_call_id, :agent_run_id, :call_seq, :tool_name,
        CAST(:input AS jsonb), :status, :error_msg
    )
    RETURNING {_TOOL_CALL_COLUMNS}
    """
)

#: **예약 row만 갱신한다.** 조건이 sentinel 전체와 일치할 때만 1행이다.
_FINALIZE_TOOL_CALL = text(
    f"""
    UPDATE agent_tool_call
    SET status = :status, output = CAST(:output AS jsonb),
        latency_ms = :latency_ms, error_msg = :error_msg
    WHERE tool_call_id = :tool_call_id AND agent_run_id = :agent_run_id
      AND status = :reserved_status AND error_msg = :reserved_error
      AND output IS NULL AND latency_ms IS NULL
    RETURNING {_TOOL_CALL_COLUMNS}
    """
)

_TOOL_CALL_EXISTS = text(
    "SELECT 1 FROM agent_tool_call "
    "WHERE tool_call_id = :tool_call_id AND agent_run_id = :agent_run_id"
)

_SELECT_TOOL_CALLS = text(
    f"""
    SELECT {_TOOL_CALL_COLUMNS} FROM agent_tool_call
    WHERE agent_run_id = :run_id ORDER BY call_seq
    """
)

_COUNT_TOOL_CALLS = text(
    "SELECT count(*) AS total FROM agent_tool_call WHERE agent_run_id = :run_id"
)


def _tool_call_row(row: Row[Any]) -> ToolCallRow:
    return ToolCallRow(
        tool_call_id=row.tool_call_id,
        agent_run_id=row.agent_run_id,
        call_seq=int(row.call_seq),
        tool_name=row.tool_name,
        input=None if row.input is None else dict(row.input),
        output=None if row.output is None else dict(row.output),
        status=ToolCallStatus(row.status),
        latency_ms=row.latency_ms,
        called_at=row.called_at,
        error_msg=row.error_msg,
    )


def tool_call_consumes_budget(call: ToolCallRow) -> bool:
    """실제 외부 효과가 없다고 명시된 성공 send_action만 예산에서 제외한다.

    이전 형식·실패·timeout·미종료 예약은 모두 보수적으로 소비 처리한다. 감사 행은
    삭제하지 않으며 이 함수는 예산 projection에만 영향을 준다.
    """

    if call.tool_name != "send_action":
        return True
    output = call.output
    return not (
        call.status is ToolCallStatus.SUCCESS
        and isinstance(output, Mapping)
        and output.get("ok") is True
        and output.get("effect_attempted") is False
        and output.get("effect_channel") is None
    )


def _assert_no_reserved_keys(output: Mapping[str, Any] | None) -> None:
    if output is None:
        return
    for key in RESERVED_TOOL_OUTPUT_KEYS:
        if key in output:
            raise RepositoryContractError("RESERVED_OUTPUT_KEY")


def reserve_tool_call(
    connection: Connection,
    *,
    agent_run_id: str,
    tool_name: str,
    input: Mapping[str, Any] | None = None,
) -> ToolCallRow:
    """호출 **전에** 자리를 잡는다. **이 호출은 자기 unit of work다.**

    ## caller 계약

    ```text
    UoW 1: reserve_tool_call() → caller commit → lock 해제
    (여기서 외부 Tool 실행 — lock을 쥐지 않는다)
    UoW 2: finalize_tool_call() → caller commit
    ```

    **반드시 commit하고 나서 외부 Tool을 부른다.** 그러지 않으면 두 가지가 깨진다.

    1. crash 표현이 무의미해진다 — 예약이 commit되지 않았으면 프로세스가 죽을 때
       row도 함께 사라져 시도한 사실이 남지 않는다.
    2. `agent_run` row lock을 Tool 실행 시간 내내 쥐어 같은 run의 다른 예약을 막는다.

    ## 왜 run row를 잠그나

    `max(call_seq)+1`은 lock이 없으면 두 session이 같은 값을 읽는다. 그러면 하나는
    `agent_tool_call_agent_run_id_call_seq_key`에 걸려 실패한다. run row를 잠그면
    같은 run의 예약이 직렬화되고 다른 run은 서로 막지 않는다.
    """

    _require_transaction(connection)
    tool_name = _require_text(tool_name, "tool_name")
    input_json = _json_payload(input, "input")

    def _run() -> Any:
        locked = connection.execute(_LOCK_RUN, {"run_id": agent_run_id}).one_or_none()
        if locked is None:
            raise RepositoryNotFound("RUN_NOT_FOUND")
        next_seq = connection.execute(
            _NEXT_CALL_SEQ, {"run_id": agent_run_id}
        ).scalar_one()
        return _insert_one(
            connection,
            _INSERT_TOOL_CALL,
            {
                "tool_call_id": new_tool_call_id(),
                "agent_run_id": agent_run_id,
                "call_seq": int(next_seq),
                "tool_name": tool_name,
                "input": input_json,
                "status": ToolCallStatus.ERROR.value,
                "error_msg": RESERVED_ERROR_MSG,
            },
        )

    return _tool_call_row(_write(connection, _run))


def finalize_tool_call(
    connection: Connection,
    *,
    tool_call_id: str,
    agent_run_id: str,
    status: ToolCallStatus,
    latency_ms: int,
    output: Mapping[str, Any] | None = None,
    error_msg: str | None = None,
) -> ToolCallRow:
    """예약한 그 행을 **정확히 한 번** 닫는다.

    조건이 sentinel 전체와 일치할 때만 1행이므로 재종료는 0행이다. 0행일 때 행 존재를
    한 번 더 확인해 "없는 ID"와 "이미 닫힌 호출"을 구분한다 — 상위가 404와 409를
    나눠야 하기 때문이다.
    """

    _require_transaction(connection)
    if latency_ms < 0:
        raise RepositoryContractError("NEGATIVE_LATENCY")
    _assert_no_reserved_keys(output)
    output_json = _json_payload(output, "output")
    resolved = ToolCallStatus(status)

    def _run() -> Any:
        row = connection.execute(
            _FINALIZE_TOOL_CALL,
            {
                "tool_call_id": tool_call_id,
                "agent_run_id": agent_run_id,
                "status": resolved.value,
                "output": output_json,
                "latency_ms": latency_ms,
                "error_msg": _optional_text(error_msg, "error_msg"),
                "reserved_status": ToolCallStatus.ERROR.value,
                "reserved_error": RESERVED_ERROR_MSG,
            },
        ).one_or_none()
        if row is None:
            exists = connection.execute(
                _TOOL_CALL_EXISTS,
                {"tool_call_id": tool_call_id, "agent_run_id": agent_run_id},
            ).one_or_none()
            if exists is None:
                raise RepositoryNotFound("TOOL_CALL_NOT_FOUND")
            raise RepositoryConflict("TOOL_CALL_ALREADY_FINALIZED")
        return row

    return _tool_call_row(_write(connection, _run))


def list_tool_calls(connection: Connection, agent_run_id: str) -> list[ToolCallRow]:
    """`call_seq` 순서로 돌려준다. 예약만 된 호출도 포함한다."""

    rows = _fetch_all(connection, _SELECT_TOOL_CALLS, {"run_id": agent_run_id})
    return [_tool_call_row(row) for row in rows]


def count_tool_calls(connection: Connection, agent_run_id: str) -> int:
    """총 호출 **시도** 수. 예약만 된 것도 센다.

    `V5-C-2.1`은 이 값으로 State의 DB-derived `ToolBudget` snapshot을 만들고,
    예산 8회 정책은 `V5-C-2.2`가 그 위에 구현한다. 이 계층은 세기만 한다.
    """

    try:
        return int(
            connection.execute(_COUNT_TOOL_CALLS, {"run_id": agent_run_id}).scalar_one()
        )
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc


def count_tool_calls_for_budget(
    connection: Connection,
    agent_run_id: str,
) -> ToolBudgetCounts:
    """run을 잠근 뒤 예산 소비 Tool의 총·Tool별·미종료 수를 읽는다.

    caller는 이 함수와 :func:`reserve_tool_call`을 같은 transaction에서 호출한다.
    멱등 성공 no-call 감사 행만 제외하며 sentinel·이전 형식은 일반 시도처럼 센다.
    """

    _require_transaction(connection)
    try:
        locked = connection.execute(
            _LOCK_RUN,
            {"run_id": agent_run_id},
        ).one_or_none()
        if locked is None:
            raise RepositoryNotFound("RUN_NOT_FOUND")
        try:
            autonomy_level = locked.autonomy_level
        except AttributeError:
            raise RepositoryContractError("AUTONOMY_LEVEL_INVALID") from None
        if type(autonomy_level) is not int or autonomy_level not in (1, 2, 3):
            raise RepositoryContractError("AUTONOMY_LEVEL_INVALID")
        rows = connection.execute(_SELECT_TOOL_CALLS, {"run_id": agent_run_id}).all()
    except AgentRepositoryError:
        raise
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc

    calls = [_tool_call_row(row) for row in rows]
    budget_calls = [call for call in calls if tool_call_consumes_budget(call)]
    by_tool: dict[str, int] = {}
    pending = 0
    for call in budget_calls:
        by_tool[call.tool_name] = by_tool.get(call.tool_name, 0) + 1
        if (
            call.status is ToolCallStatus.ERROR
            and call.error_msg == RESERVED_ERROR_MSG
            and call.output is None
            and call.latency_ms is None
        ):
            pending += 1
    return ToolBudgetCounts(
        total=sum(by_tool.values()),
        by_tool=by_tool,
        pending_reservations=pending,
        autonomy_level=autonomy_level,
    )


# ---------------------------------------------------------------------------
# approval_request · action_delivery — 초기 row + C-4.3 EMAIL 발신 전이
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalRequestRow:
    approval_id: str
    action_id: str
    agent_run_id: str
    status: ApprovalStatus
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None


_APPROVAL_COLUMNS = """
    approval_id, action_id, agent_run_id, status,
    requested_at, decided_by, decided_at, decision_comment
"""

_INSERT_APPROVAL = text(
    f"""
    INSERT INTO approval_request (approval_id, action_id, agent_run_id, status)
    VALUES (:approval_id, :action_id, :agent_run_id, :status)
    RETURNING {_APPROVAL_COLUMNS}
    """
)

_SELECT_APPROVAL = text(
    f"SELECT {_APPROVAL_COLUMNS} FROM approval_request WHERE approval_id = :approval_id"
)


def _approval_row(row: Row[Any]) -> ApprovalRequestRow:
    return ApprovalRequestRow(
        approval_id=row.approval_id,
        action_id=row.action_id,
        agent_run_id=row.agent_run_id,
        status=ApprovalStatus(row.status),
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_comment=row.decision_comment,
    )


def create_approval_request(
    connection: Connection,
    *,
    action_id: str,
    agent_run_id: str,
    actor_type: ActorType = ActorType.AGENT,
    actor_id: str | None = None,
) -> ApprovalRequestRow:
    """승인 요청을 만들고 `APPROVAL_REQUESTED` 감사를 같은 transaction에 남긴다.

    **`status` 인자를 받지 않는다.** `002`의 CHECK는 `PENDING·APPROVED·REJECTED·
    EXPIRED` 4값인데 공통 `ApprovalStatus`에는 `AUTO`가 더 있다. 인자로 열어 두면
    DTO는 통과하고 insert가 실패하는 조합을 만들 수 있다 — `AUTO`는
    `action_history` projection 값이지 이 table의 상태가 아니다.

    결정 전이(`APPROVED`·`REJECTED`·`EXPIRED`)는 후속 C Task가 소유한다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    approval_id = new_approval_id()
    # 감사 entity는 approval이다 — `EVENT_ENTITY_TYPE`이 그렇게 고정한다.
    record = _run_audit_record(
        AuditEvent.APPROVAL_REQUESTED,
        entity_id=approval_id,
        actor_type=actor_type,
        actor_id=actor_id,
        after={"action_id": action_id, "agent_run_id": agent_run_id},
    )

    def _run() -> Any:
        row = _insert_one(
            connection,
            _INSERT_APPROVAL,
            {
                "approval_id": approval_id,
                "action_id": action_id,
                "agent_run_id": agent_run_id,
                "status": ApprovalStatus.PENDING.value,
            },
        )
        append_audit_log(connection, record)
        return row

    return _approval_row(_write(connection, _run))


def get_approval_request(
    connection: Connection, approval_id: str
) -> ApprovalRequestRow:
    row = _fetch_one(
        connection,
        _SELECT_APPROVAL,
        {"approval_id": approval_id},
        "APPROVAL_NOT_FOUND",
    )
    return _approval_row(row)


_UPDATE_RUN_WAITING_APPROVAL = text(
    f"""
    UPDATE agent_run
    SET status = :waiting, latency_ms = :latency_ms,
        evidence = CAST(:evidence AS jsonb)
    WHERE agent_run_id = :run_id AND status = :running
    RETURNING {_RUN_COLUMNS}
    """
)

_UPDATE_RUN_RESUMED = text(
    f"""
    UPDATE agent_run
    SET status = :running, evidence = CAST(:evidence AS jsonb)
    WHERE agent_run_id = :run_id AND status = :waiting
    RETURNING {_RUN_COLUMNS}
    """
)

_UPDATE_APPROVAL_DECISION = text(
    f"""
    UPDATE approval_request
    SET status = :status, decided_by = :decided_by,
        decided_at = clock_timestamp(), decision_comment = :decision_comment
    WHERE approval_id = :approval_id AND status = :pending
    RETURNING {_APPROVAL_COLUMNS}
    """
)

_UPDATE_ACTION_APPROVAL = text(
    f"""
    UPDATE action_history
    SET approval_status = :status,
        approved_by = :approved_by,
        approved_at = :approved_at
    WHERE action_id = :action_id
      AND approval_required = 'Y'
      AND approval_status = :pending
    RETURNING {_ACTION_HISTORY_COLUMNS}
    """
)

_UPDATE_MES_DECISION = text(
    """
    UPDATE action_delivery
    SET status = :status
    WHERE action_id = :action_id AND channel = :channel
      AND status = :blocked
    RETURNING action_id, channel, status, request_hash, attempt_count,
              provider_message_id, started_at, completed_at, last_error, result
    """
)


@dataclass(frozen=True, slots=True)
class ApprovalDecisionRow:
    """한 승인 결정 transaction이 확정한 세 projection."""

    approval: ApprovalRequestRow
    action: ActionHistoryRow
    mes_delivery: ActionDeliveryRow


def _require_created_hold_bundle(
    connection: Connection,
    *,
    approval: ApprovalRequestRow,
    run: AgentRunRow,
) -> tuple[RunActionRow, ActionHistoryRow]:
    """approval→run→CREATED action identity와 EQP_HOLD 계약을 결속한다."""

    try:
        link = get_run_action(connection, run.agent_run_id)
        action = get_action_history(connection, approval.action_id)
    except RepositoryNotFound as exc:
        raise RepositoryConflict("APPROVAL_IDENTITY_MISMATCH") from exc
    if (
        approval.agent_run_id != run.agent_run_id
        or link.link_role is not ActionLinkRole.CREATED
        or link.action_id != approval.action_id
        or action.action_id != approval.action_id
        or action.action_code is not ActionCode.EQP_HOLD
        or not action.approval_required
        or run.action is not ActionCode.EQP_HOLD
    ):
        raise RepositoryConflict("APPROVAL_IDENTITY_MISMATCH")
    return link, action


def begin_approval_wait(
    connection: Connection,
    *,
    agent_run_id: str,
    approval_id: str,
    now: datetime | None = None,
) -> AgentRunRow:
    """결속된 EQP_HOLD bundle과 run을 같은 UoW에서 WAITING으로 만든다.

    이미 WAITING인 같은 bundle은 replay-safe no-op이다. 그 밖의 상태나 결속 실패는
    조용히 성공시키지 않는다.
    """

    _require_transaction(connection)
    run = lock_agent_run(connection, agent_run_id)
    approval = get_approval_request(connection, approval_id)
    _require_created_hold_bundle(connection, approval=approval, run=run)
    if approval.status is not ApprovalStatus.PENDING:
        raise RepositoryConflict("ACTION_APPROVAL_NOT_PENDING")
    if run.status is RunStatus.WAITING_APPROVAL:
        return run
    if run.status is not RunStatus.RUNNING:
        raise RepositoryConflict("RUN_STATE_INVALID")
    observed_at = datetime.now(UTC) if now is None else now
    latency_ms = active_run_latency_ms(run, now=observed_at)
    evidence = _timing_evidence(run, active_started_at=None)
    try:
        row = connection.execute(
            _UPDATE_RUN_WAITING_APPROVAL,
            {
                "run_id": agent_run_id,
                "running": RunStatus.RUNNING.value,
                "waiting": RunStatus.WAITING_APPROVAL.value,
                "latency_ms": latency_ms,
                "evidence": _json_payload(evidence, "evidence"),
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        raise RepositoryConflict("RUN_STATE_INVALID")
    return _run_row(row)


def resume_from_approval(
    connection: Connection,
    *,
    agent_run_id: str,
    approval_id: str,
    now: datetime | None = None,
) -> AgentRunRow:
    """terminal 승인과 결속된 WAITING run만 RUNNING으로 CAS한다."""

    _require_transaction(connection)
    run = lock_agent_run(connection, agent_run_id)
    approval = get_approval_request(connection, approval_id)
    _require_created_hold_bundle(connection, approval=approval, run=run)
    if run.status is not RunStatus.WAITING_APPROVAL:
        raise RepositoryConflict("RUN_NOT_WAITING_APPROVAL")
    if approval.status is ApprovalStatus.PENDING:
        raise RepositoryConflict("APPROVAL_STILL_PENDING")
    if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        raise RepositoryConflict("APPROVAL_NOT_RESUMABLE")
    observed_at = datetime.now(UTC) if now is None else now
    if observed_at.tzinfo is None:
        raise RepositoryContractError("ACTIVE_TIMING_CLOCK_INVALID")
    evidence = _timing_evidence(run, active_started_at=observed_at)
    try:
        row = connection.execute(
            _UPDATE_RUN_RESUMED,
            {
                "run_id": agent_run_id,
                "running": RunStatus.RUNNING.value,
                "waiting": RunStatus.WAITING_APPROVAL.value,
                "evidence": _json_payload(evidence, "evidence"),
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        raise RepositoryConflict("RUN_NOT_WAITING_APPROVAL")
    return _run_row(row)


def decide_approval(
    connection: Connection,
    *,
    approval_id: str,
    decision: Decision,
    decided_by: str,
    decision_comment: str | None = None,
) -> ApprovalDecisionRow:
    """승인·action·MES projection과 감사를 한 caller transaction에서 확정한다."""

    _require_transaction(connection)
    approval_id = _require_text(approval_id, "approval_id")
    actor_id = _require_text(decided_by, "decided_by")
    comment = _optional_text(decision_comment, "decision_comment")
    try:
        resolved = Decision(decision)
    except ValueError as exc:
        raise RepositoryContractError("INVALID_APPROVAL_DECISION") from exc
    target = (
        ApprovalStatus.APPROVED
        if resolved is Decision.APPROVE
        else ApprovalStatus.REJECTED
    )
    mes_status = (
        DeliveryStatus.WAITING
        if resolved is Decision.APPROVE
        else DeliveryStatus.CANCELED
    )

    # identity를 찾은 뒤 그 approval이 가리키는 run을 잠근다. 경쟁 결정은 같은
    # run lock에서 직렬화되며, lock 획득 뒤 approval을 반드시 다시 읽는다.
    identity = get_approval_request(connection, approval_id)
    run = lock_agent_run(connection, identity.agent_run_id)
    approval = get_approval_request(connection, approval_id)
    _link, action = _require_created_hold_bundle(connection, approval=approval, run=run)
    if approval.status is not ApprovalStatus.PENDING:
        raise RepositoryConflict("APPROVAL_NOT_PENDING")
    if run.status is not RunStatus.WAITING_APPROVAL:
        raise RepositoryConflict("RUN_NOT_WAITING_APPROVAL")
    if action.approval_status is not ApprovalStatus.PENDING:
        raise RepositoryConflict("ACTION_APPROVAL_NOT_PENDING")

    # actor/entity 길이와 payload shape를 첫 DML 전에 검증한다. decided_at만 DB가
    # 발급한 뒤 model_copy로 채운다.
    audit = _run_audit_record(
        AuditEvent.APPROVAL_DECIDED,
        entity_id=approval.approval_id,
        actor_type=ActorType.HUMAN,
        actor_id=actor_id,
        before={"status": ApprovalStatus.PENDING.value},
        after={
            "status": target.value,
            "approval_id": approval.approval_id,
            "action_id": approval.action_id,
            "agent_run_id": approval.agent_run_id,
        },
    )

    def _run() -> ApprovalDecisionRow:
        approval_row = connection.execute(
            _UPDATE_APPROVAL_DECISION,
            {
                "approval_id": approval.approval_id,
                "pending": ApprovalStatus.PENDING.value,
                "status": target.value,
                "decided_by": actor_id,
                "decision_comment": comment,
            },
        ).one_or_none()
        if approval_row is None:
            raise RepositoryConflict("APPROVAL_NOT_PENDING")
        decided = _approval_row(approval_row)

        action_row = connection.execute(
            _UPDATE_ACTION_APPROVAL,
            {
                "action_id": approval.action_id,
                "pending": ApprovalStatus.PENDING.value,
                "status": target.value,
                "approved_by": actor_id if resolved is Decision.APPROVE else None,
                "approved_at": (
                    decided.decided_at if resolved is Decision.APPROVE else None
                ),
            },
        ).one_or_none()
        if action_row is None:
            raise RepositoryConflict("ACTION_APPROVAL_NOT_PENDING")

        delivery_row = connection.execute(
            _UPDATE_MES_DECISION,
            {
                "action_id": approval.action_id,
                "channel": DeliveryChannel.MES_MOCK.value,
                "blocked": DeliveryStatus.BLOCKED.value,
                "status": mes_status.value,
            },
        ).one_or_none()
        if delivery_row is None:
            raise RepositoryConflict("MES_DELIVERY_NOT_BLOCKED")

        decided_at = decided.decided_at
        if decided_at is None:  # DB RETURNING 계약 위반
            raise RepositoryContractError("DECIDED_AT_MISSING")
        record = audit.model_copy(
            update={
                "after": {
                    **(audit.after or {}),
                    "decided_at": decided_at.isoformat(),
                }
            }
        )
        append_audit_log(connection, record)
        return ApprovalDecisionRow(
            approval=decided,
            action=_action_history_row(action_row),
            mes_delivery=_delivery_row(delivery_row),
        )

    return _write(connection, _run)


@dataclass(frozen=True, slots=True)
class ActionDeliveryRow:
    action_id: str
    channel: DeliveryChannel
    status: DeliveryStatus
    request_hash: str
    attempt_count: int
    provider_message_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    last_error: str | None
    result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class MesDeliveryClaim:
    """승인·run·equipment와 결속된 MES ``SENDING`` claim."""

    delivery: ActionDeliveryRow
    action: ActionHistoryRow
    approval: ApprovalRequestRow
    equipment_id: str


@dataclass(frozen=True, slots=True)
class DeliveryCallbackTransition:
    delivery: ActionDeliveryRow
    duplicate: bool


class DeliveryRecoveryReason(StrEnum):
    """운영 복구 명령이 출력해도 되는 안정적인 판정 코드."""

    APPLIED = "APPLIED"
    NO_TARGET = "NO_TARGET"
    STILL_FRESH = "STILL_FRESH"
    CALLBACK_WON = "CALLBACK_WON"
    STATE_NOT_ALLOWED = "STATE_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class DeliveryRecoveryResult:
    reason: DeliveryRecoveryReason
    delivery: ActionDeliveryRow | None
    previous_status: DeliveryStatus | None


_DELIVERY_COLUMNS = """
    action_id, channel, status, request_hash, attempt_count,
    provider_message_id, started_at, completed_at, last_error, result
"""

_INSERT_DELIVERY = text(
    f"""
    INSERT INTO action_delivery (action_id, channel, status, request_hash)
    VALUES (:action_id, :channel, :status, :request_hash)
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_SELECT_DELIVERY = text(
    f"""
    SELECT {_DELIVERY_COLUMNS} FROM action_delivery
    WHERE action_id = :action_id AND channel = :channel
    """
)

_SELECT_DELIVERIES = text(
    f"""
    SELECT {_DELIVERY_COLUMNS} FROM action_delivery
    WHERE action_id = :action_id ORDER BY channel
    """
)

_BEGIN_EMAIL_DELIVERY = text(
    f"""
    UPDATE action_delivery
    SET status = 'SENDING',
        attempt_count = attempt_count + 1,
        started_at = clock_timestamp(),
        last_error = NULL,
        completed_at = NULL
    WHERE action_id = :action_id
      AND channel = 'EMAIL'
      AND status = 'WAITING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_SELECT_EMAIL_DELIVERY_FOR_UPDATE = text(
    f"""
    SELECT {_DELIVERY_COLUMNS}
    FROM action_delivery
    WHERE action_id = :action_id AND channel = 'EMAIL'
    FOR UPDATE
    """
)

_SELECT_DELIVERY_FOR_UPDATE = text(
    f"""
    SELECT {_DELIVERY_COLUMNS}
    FROM action_delivery
    WHERE action_id = :action_id AND channel = :channel
    FOR UPDATE
    """
)

_STALE_DELIVERY_PREDICATE = """
    status = 'SENDING'
    AND started_at IS NOT NULL
    AND started_at <= clock_timestamp()
        - make_interval(secs => CAST(:stale_after_seconds AS double precision))
"""

_SELECT_STALE_DELIVERIES = text(
    f"""
    SELECT {_DELIVERY_COLUMNS}
    FROM action_delivery
    WHERE {_STALE_DELIVERY_PREDICATE}
    ORDER BY started_at, action_id, channel
    """
)

_SELECT_DELIVERY_RECOVERY_FOR_UPDATE = text(
    f"""
    SELECT {_DELIVERY_COLUMNS},
           ({_STALE_DELIVERY_PREDICATE}) AS is_stale
    FROM action_delivery
    WHERE action_id = :action_id AND channel = :channel
    FOR UPDATE
    """
)

_UPDATE_DELIVERY_UNKNOWN = text(
    f"""
    UPDATE action_delivery
    SET status = 'UNKNOWN',
        completed_at = clock_timestamp(),
        last_error = 'DELIVERY_RESULT_UNKNOWN',
        result = CAST(:result AS jsonb)
    WHERE action_id = :action_id
      AND channel = :channel
      AND {_STALE_DELIVERY_PREDICATE}
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_UPDATE_FAILED_DELIVERY_FOR_RETRY = text(
    f"""
    UPDATE action_delivery
    SET status = 'WAITING',
        provider_message_id = NULL,
        completed_at = NULL,
        result = NULL
    WHERE action_id = :action_id
      AND channel = :channel
      AND status = 'FAILED'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_UPDATE_DELIVERY_CALLBACK = text(
    f"""
    UPDATE action_delivery
    SET status = :status,
        provider_message_id = :provider_message_id,
        completed_at = :completed_at,
        last_error = :error_code,
        result = CAST(:result AS jsonb)
    WHERE action_id = :action_id
      AND channel = :channel
      AND status = 'SENDING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)
_UPDATE_EMAIL_DELIVERY_FAILED = text(
    f"""
    UPDATE action_delivery
    SET status = 'FAILED', completed_at = clock_timestamp(), last_error = :last_error
    WHERE action_id = :action_id AND channel = 'EMAIL' AND status = 'SENDING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_UPDATE_EMAIL_DELIVERY_UNCERTAIN = text(
    f"""
    UPDATE action_delivery
    SET last_error = :last_error
    WHERE action_id = :action_id AND channel = 'EMAIL' AND status = 'SENDING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_BEGIN_MES_DELIVERY = text(
    f"""
    UPDATE action_delivery
    SET status = 'SENDING',
        attempt_count = attempt_count + 1,
        started_at = clock_timestamp(),
        last_error = NULL,
        completed_at = NULL
    WHERE action_id = :action_id
      AND channel = 'MES_MOCK'
      AND status = 'WAITING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_UPDATE_MES_DELIVERY_FAILED = text(
    f"""
    UPDATE action_delivery
    SET status = 'FAILED', completed_at = clock_timestamp(), last_error = :last_error
    WHERE action_id = :action_id AND channel = 'MES_MOCK' AND status = 'SENDING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_UPDATE_MES_DELIVERY_UNCERTAIN = text(
    f"""
    UPDATE action_delivery
    SET last_error = :last_error
    WHERE action_id = :action_id AND channel = 'MES_MOCK' AND status = 'SENDING'
    RETURNING {_DELIVERY_COLUMNS}
    """
)

_SELECT_APPROVAL_FOR_ACTION = text(
    f"""
    SELECT {_APPROVAL_COLUMNS}
    FROM approval_request
    WHERE action_id = :action_id
    """
)

_SELECT_INCIDENT_EQUIPMENT = text(
    """
    SELECT DISTINCT equipment_id
    FROM lot_history
    WHERE lot_id = :lot_id AND chamber_id = :chamber_id
    ORDER BY equipment_id
    """
)

#: `request_hash char(64)`는 소문자 hex다. 형식 위반은 DB가 CHECK로 막지만 그 전에
#: 걸러야 caller 입력 오류가 driver 예외로 올라가지 않는다.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: callback 시각은 n8n host, ``started_at``은 PostgreSQL host에서 생성된다. 정상적인
#: 짧은 왕복을 host 간 NTP 오차로 거부하지 않되, 5초를 넘는 역행은 계약 위반으로 막는다.
_DELIVERY_CLOCK_SKEW_TOLERANCE: Final = timedelta(seconds=5)

#: 생성 시점의 **정확한 (channel, status) 조합**. 설계 §7.1이 둘로 고정한다.
#:
#: ```text
#: WARNING   → EMAIL=WAITING
#: EQP_HOLD  → EMAIL=WAITING, MES_MOCK=BLOCKED
#: ```
#:
#: ## 상태 목록으로 두면 안 되는 이유
#:
#: 초판은 `BLOCKED·WAITING·CANCELED·UNKNOWN`을 허용하고 channel과 status를 **독립**
#: 검증했다. 그래서 설계에 없는 네 조합이 초기 INSERT로 만들어졌다
#: (구현리뷰 묶음 2 필수 1).
#:
#: | 조합 | 무엇이 깨지나 |
#: |---|---|
#: | `MES_MOCK=WAITING` | **승인 전 Kafka 0회**의 저장 근거가 사라진다 |
#: | `EMAIL=CANCELED` | §7.2의 반려 전이(`BLOCKED → CANCELED`)를 우회한다 |
#: | `EMAIL=UNKNOWN` | 전송 시도 없이 미확정 상태가 생긴다 |
#: | `EMAIL=BLOCKED` | 설계에 없는 조합이다 |
#:
#: 상태 전이를 후속 Task로 넘겼다면 생성 함수는 **생성 시점의 조합만** 표현해야 한다.
INITIAL_DELIVERY_PAIRS: Final[Mapping[DeliveryChannel, DeliveryStatus]] = {
    DeliveryChannel.EMAIL: DeliveryStatus.WAITING,
    DeliveryChannel.MES_MOCK: DeliveryStatus.BLOCKED,
}


def _delivery_row(row: Row[Any]) -> ActionDeliveryRow:
    return ActionDeliveryRow(
        action_id=row.action_id,
        channel=DeliveryChannel(row.channel),
        status=DeliveryStatus(row.status),
        request_hash=row.request_hash,
        attempt_count=int(row.attempt_count),
        provider_message_id=row.provider_message_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        last_error=row.last_error,
        result=None if row.result is None else dict(row.result),
    )


def insert_action_delivery(
    connection: Connection,
    *,
    action_id: str,
    channel: DeliveryChannel,
    status: DeliveryStatus,
    request_hash: str,
) -> ActionDeliveryRow:
    """전송 전 초기 row를 만든다. **상태 전이는 하지 않는다.**

    설계 §7.1이 고정한 `(EMAIL, WAITING)`·`(MES_MOCK, BLOCKED)` 두 조합만 만든다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    request_hash = _require_text(request_hash, "request_hash")
    if not _HEX64.fullmatch(request_hash):
        raise RepositoryContractError("INVALID_REQUEST_HASH")
    resolved_channel = DeliveryChannel(channel)
    resolved = DeliveryStatus(status)
    if INITIAL_DELIVERY_PAIRS[resolved_channel] is not resolved:
        # channel과 status를 따로 보지 않는다 — 조합이 계약이다.
        raise RepositoryContractError("NOT_INITIAL_DELIVERY_PAIR")
    row = _insert_one(
        connection,
        _INSERT_DELIVERY,
        {
            "action_id": action_id,
            "channel": resolved_channel.value,
            "status": resolved.value,
            "request_hash": request_hash,
        },
    )
    return _delivery_row(row)


def get_action_delivery(
    connection: Connection, *, action_id: str, channel: DeliveryChannel
) -> ActionDeliveryRow:
    row = _fetch_one(
        connection,
        _SELECT_DELIVERY,
        {"action_id": action_id, "channel": DeliveryChannel(channel).value},
        "DELIVERY_NOT_FOUND",
    )
    return _delivery_row(row)


def list_action_deliveries(
    connection: Connection, action_id: str
) -> list[ActionDeliveryRow]:
    rows = _fetch_all(connection, _SELECT_DELIVERIES, {"action_id": action_id})
    return [_delivery_row(row) for row in rows]


def _validated_stale_after_seconds(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise RepositoryContractError("DELIVERY_STALE_AFTER_INVALID")
    return value


def list_stale_deliveries(
    connection: Connection,
    *,
    stale_after_seconds: int,
) -> list[ActionDeliveryRow]:
    """DB 시각 기준으로 cutoff를 지난 ``SENDING`` delivery만 조회한다."""

    stale_after_seconds = _validated_stale_after_seconds(stale_after_seconds)
    try:
        rows = connection.execute(
            _SELECT_STALE_DELIVERIES,
            {"stale_after_seconds": stale_after_seconds},
        ).all()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return [_delivery_row(row) for row in rows]


def mark_delivery_unknown(
    connection: Connection,
    *,
    action_id: str,
    channel: DeliveryChannel,
    stale_after_seconds: int,
) -> DeliveryRecoveryResult:
    """stale ``SENDING`` 한 건을 운영자 확인 뒤 ``UNKNOWN``으로 확정한다.

    callback과 같은 row lock을 사용하고 lock 획득 뒤 DB 시각으로 cutoff를 다시
    판정한다. 따라서 dry-run 목록은 참고값이고 이 함수의 결과가 정본이다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    stale_after_seconds = _validated_stale_after_seconds(stale_after_seconds)
    try:
        resolved_channel = DeliveryChannel(channel)
    except ValueError as exc:
        raise RepositoryContractError("INVALID_DELIVERY_CHANNEL") from exc
    params = {
        "action_id": action_id,
        "channel": resolved_channel.value,
        "stale_after_seconds": stale_after_seconds,
    }
    try:
        row = connection.execute(
            _SELECT_DELIVERY_RECOVERY_FOR_UPDATE,
            params,
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        return DeliveryRecoveryResult(DeliveryRecoveryReason.NO_TARGET, None, None)

    locked = _delivery_row(row)
    if locked.status in {DeliveryStatus.SENT, DeliveryStatus.FAILED}:
        return DeliveryRecoveryResult(
            DeliveryRecoveryReason.CALLBACK_WON,
            locked,
            locked.status,
        )
    if locked.status is not DeliveryStatus.SENDING:
        return DeliveryRecoveryResult(
            DeliveryRecoveryReason.STATE_NOT_ALLOWED,
            locked,
            locked.status,
        )
    if not bool(row.is_stale):
        return DeliveryRecoveryResult(
            DeliveryRecoveryReason.STILL_FRESH,
            locked,
            locked.status,
        )

    transport = "N8N_WEBHOOK" if resolved_channel is DeliveryChannel.EMAIL else "KAFKA"
    try:
        updated_row = connection.execute(
            _UPDATE_DELIVERY_UNKNOWN,
            {
                **params,
                "result": _json_payload(
                    {
                        "operator_decision": "UNKNOWN_CONFIRMED",
                        "transport": transport,
                    },
                    "result",
                ),
            },
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if updated_row is None:
        raise RepositoryConflict("DELIVERY_STATE_CHANGED")
    return DeliveryRecoveryResult(
        DeliveryRecoveryReason.APPLIED,
        _delivery_row(updated_row),
        DeliveryStatus.SENDING,
    )


def retry_failed_delivery(
    connection: Connection,
    *,
    action_id: str,
    channel: DeliveryChannel,
) -> ActionDeliveryRow:
    """명시적 운영 명령만 ``FAILED→WAITING``으로 되돌린다.

    stable request identity와 이전 오류는 보존한다. 다음 attempt는 이 함수가 아니라
    이후 ``begin_*_delivery`` claim이 증가시킨다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    try:
        resolved_channel = DeliveryChannel(channel)
    except ValueError as exc:
        raise RepositoryContractError("INVALID_DELIVERY_CHANNEL") from exc
    locked = _delivery_row(
        _fetch_one(
            connection,
            _SELECT_DELIVERY_FOR_UPDATE,
            {"action_id": action_id, "channel": resolved_channel.value},
            "DELIVERY_NOT_FOUND",
        )
    )
    if locked.status is not DeliveryStatus.FAILED:
        raise RepositoryConflict("DELIVERY_RETRY_STATE_NOT_ALLOWED")
    try:
        row = connection.execute(
            _UPDATE_FAILED_DELIVERY_FOR_RETRY,
            {"action_id": action_id, "channel": resolved_channel.value},
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if row is None:
        raise RepositoryConflict("DELIVERY_STATE_CHANGED")
    return _delivery_row(row)


def begin_email_delivery(
    connection: Connection, *, action_id: str
) -> ActionDeliveryRow | None:
    """EMAIL ``WAITING`` 한 건만 ``SENDING``으로 claim한다.

    0행은 경합 오류가 아니라 안전한 no-op이다. caller는 이 반환값이 ``None``이면
    webhook을 호출하면 안 된다. network I/O는 이 transaction이 commit된 뒤에만 한다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    try:
        row = connection.execute(
            _BEGIN_EMAIL_DELIVERY, {"action_id": action_id}
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    return None if row is None else _delivery_row(row)


_DELIVERY_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_TERMINAL_EMAIL_FAILURE_CODES = frozenset({"WEBHOOK_401", "WEBHOOK_422"})


def settle_email_delivery(
    connection: Connection,
    *,
    action_id: str,
    request_hash: str,
    failure_code: str | None,
    terminal_failure: bool,
) -> ActionDeliveryRow:
    """HTTP 뒤 EMAIL row를 callback 정본과 수렴시킨다.

    같은 hash의 ``SENT|FAILED``가 이미 보이면 callback 결과이므로 절대 덮어쓰지 않는다.
    아직 ``SENDING``일 때만 401/422 확정 실패 또는 미확정 transport 오류를 기록한다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    request_hash = _require_text(request_hash, "request_hash")
    if not _HEX64.fullmatch(request_hash):
        raise RepositoryContractError("INVALID_REQUEST_HASH")
    if failure_code is not None and not _DELIVERY_ERROR_CODE.fullmatch(failure_code):
        raise RepositoryContractError("INVALID_DELIVERY_ERROR_CODE")
    if terminal_failure and failure_code is None:
        raise RepositoryContractError("DELIVERY_FAILURE_CODE_REQUIRED")
    if terminal_failure and failure_code not in _TERMINAL_EMAIL_FAILURE_CODES:
        raise RepositoryContractError("DELIVERY_TERMINAL_FAILURE_INVALID")
    if not terminal_failure and failure_code in _TERMINAL_EMAIL_FAILURE_CODES:
        raise RepositoryContractError("DELIVERY_TERMINAL_FLAG_REQUIRED")

    locked = _delivery_row(
        _fetch_one(
            connection,
            _SELECT_EMAIL_DELIVERY_FOR_UPDATE,
            {"action_id": action_id},
            "DELIVERY_NOT_FOUND",
        )
    )
    if locked.request_hash != request_hash:
        raise RepositoryConflict("DELIVERY_REQUEST_HASH_MISMATCH")
    if locked.status in {DeliveryStatus.SENT, DeliveryStatus.FAILED}:
        return locked
    if locked.status is not DeliveryStatus.SENDING:
        raise RepositoryConflict("DELIVERY_NOT_SENDING")
    if failure_code is None:
        return locked

    if not terminal_failure:
        row = _fetch_one(
            connection,
            _UPDATE_EMAIL_DELIVERY_UNCERTAIN,
            {"action_id": action_id, "last_error": failure_code},
            "DELIVERY_STATE_CHANGED",
        )
        return _delivery_row(row)

    record = _run_audit_record(
        AuditEvent.ACTION_SEND_FAILED,
        entity_id=action_id,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        after={
            "channel": DeliveryChannel.EMAIL.value,
            "transport": "N8N_WEBHOOK",
            "reason_code": failure_code,
        },
    )

    def _run() -> Any:
        row = _fetch_one(
            connection,
            _UPDATE_EMAIL_DELIVERY_FAILED,
            {"action_id": action_id, "last_error": failure_code},
            "DELIVERY_STATE_CHANGED",
        )
        append_audit_log(connection, record)
        return row

    return _delivery_row(_write(connection, _run))


def _same_decision_instant(
    action_decided_at: datetime | None,
    approval_decided_at: datetime | None,
) -> bool:
    """naive ``timestamp``와 aware ``timestamptz``를 세션 TimeZone 기준으로 비교한다.

    ``approved_at``은 ``decided_at`` 값을 ``timestamp`` 컬럼에 쓴 것이라 PostgreSQL이
    **세션 TimeZone**의 local naive로 저장한다. psycopg는 ``timestamptz``를 같은 세션
    TimeZone의 aware datetime으로 돌려주므로 tzinfo만 떼면 두 값의 기준이 일치한다.
    naive 값을 UTC로 가정하면 공용 DB(``TimeZone=Asia/Seoul``)에서 9시간 어긋나 승인된
    EQP_HOLD가 ``MES_APPROVAL_IDENTITY_MISMATCH``로 거부된다.
    """

    if action_decided_at is None or approval_decided_at is None:
        return False
    if approval_decided_at.tzinfo is None:
        return False
    if action_decided_at.tzinfo is not None:
        return action_decided_at == approval_decided_at
    return action_decided_at == approval_decided_at.replace(tzinfo=None)


def begin_mes_delivery(
    connection: Connection, *, action_id: str
) -> MesDeliveryClaim | None:
    """승인된 EQP_HOLD 한 건만 ``WAITING→SENDING``으로 claim한다.

    delivery row를 먼저 잠그므로 같은 action의 동시 호출 중 하나만 외부 I/O
    자격을 얻는다.
    이미 claim·terminal·canceled 상태면 안전한 ``None``이며, 아직 WAITING인 row는
    approval→run→CREATED action과 ``lot_history`` equipment 유일성을 같은
    transaction에서 재검증한 뒤에만 전이한다.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    locked = _delivery_row(
        _fetch_one(
            connection,
            _SELECT_DELIVERY_FOR_UPDATE,
            {
                "action_id": action_id,
                "channel": DeliveryChannel.MES_MOCK.value,
            },
            "DELIVERY_NOT_FOUND",
        )
    )
    if locked.status is not DeliveryStatus.WAITING:
        return None

    action = get_action_history(connection, action_id)
    try:
        approval_row = connection.execute(
            _SELECT_APPROVAL_FOR_ACTION,
            {"action_id": action_id},
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if approval_row is None:
        raise RepositoryConflict("MES_APPROVAL_IDENTITY_MISMATCH")
    approval = _approval_row(approval_row)
    try:
        link = get_run_action(connection, approval.agent_run_id)
        run = get_agent_run(connection, approval.agent_run_id)
    except RepositoryNotFound as exc:
        raise RepositoryConflict("MES_APPROVAL_IDENTITY_MISMATCH") from exc

    # legacy ``action_history.approved_at``은 timestamp, Runtime
    # ``approval_request.decided_at``은 timestamptz다. 둘은 같은 DB 값이어도 psycopg가
    # 각각 naive/aware datetime으로 돌려주므로 직접 equality는 항상 거짓이다.
    same_decision_time = _same_decision_instant(action.approved_at, approval.decided_at)

    if (
        action.action_code is not ActionCode.EQP_HOLD
        or not action.approval_required
        or action.approval_status is not ApprovalStatus.APPROVED
        or approval.action_id != action_id
        or approval.status is not ApprovalStatus.APPROVED
        or approval.decided_by is None
        or approval.decided_at is None
        or action.approved_by != approval.decided_by
        or not same_decision_time
        or link.link_role is not ActionLinkRole.CREATED
        or link.action_id != action_id
        or link.lot_id != action.lot_id
        or link.chamber_id != action.chamber_id
        or run.action is not ActionCode.EQP_HOLD
        or run.lot_id != action.lot_id
        or run.chamber_id != action.chamber_id
    ):
        raise RepositoryConflict("MES_APPROVAL_IDENTITY_MISMATCH")

    try:
        equipment_rows = connection.execute(
            _SELECT_INCIDENT_EQUIPMENT,
            {"lot_id": action.lot_id, "chamber_id": action.chamber_id},
        ).all()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    equipment_ids = tuple(
        str(row.equipment_id).strip()
        for row in equipment_rows
        if row.equipment_id is not None and str(row.equipment_id).strip()
    )
    if len(equipment_rows) != 1 or len(equipment_ids) != 1:
        raise RepositoryContractError("MES_EQUIPMENT_NOT_UNIQUE")

    try:
        claimed_row = connection.execute(
            _BEGIN_MES_DELIVERY,
            {"action_id": action_id},
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc
    if claimed_row is None:
        raise RepositoryConflict("DELIVERY_STATE_CHANGED")
    return MesDeliveryClaim(
        delivery=_delivery_row(claimed_row),
        action=action,
        approval=approval,
        equipment_id=equipment_ids[0],
    )


_TERMINAL_MES_FAILURE_CODES = frozenset({"WEBHOOK_401", "WEBHOOK_422"})


def settle_mes_webhook(
    connection: Connection,
    *,
    action_id: str,
    request_hash: str,
    failure_code: str | None,
    terminal_failure: bool,
) -> ActionDeliveryRow:
    """WF3 응답 뒤 MES row를 비동기 callback DB 정본과 수렴시킨다."""

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    request_hash = _require_text(request_hash, "request_hash")
    if not _HEX64.fullmatch(request_hash):
        raise RepositoryContractError("INVALID_REQUEST_HASH")
    if failure_code is not None and not _DELIVERY_ERROR_CODE.fullmatch(failure_code):
        raise RepositoryContractError("INVALID_DELIVERY_ERROR_CODE")
    if terminal_failure and failure_code not in _TERMINAL_MES_FAILURE_CODES:
        raise RepositoryContractError("DELIVERY_TERMINAL_FAILURE_INVALID")
    if not terminal_failure and failure_code in _TERMINAL_MES_FAILURE_CODES:
        raise RepositoryContractError("DELIVERY_TERMINAL_FLAG_REQUIRED")

    locked = _delivery_row(
        _fetch_one(
            connection,
            _SELECT_DELIVERY_FOR_UPDATE,
            {
                "action_id": action_id,
                "channel": DeliveryChannel.MES_MOCK.value,
            },
            "DELIVERY_NOT_FOUND",
        )
    )
    if locked.request_hash != request_hash:
        raise RepositoryConflict("DELIVERY_REQUEST_HASH_MISMATCH")
    if locked.status in {DeliveryStatus.SENT, DeliveryStatus.FAILED}:
        return locked
    if locked.status is not DeliveryStatus.SENDING:
        raise RepositoryConflict("DELIVERY_NOT_SENDING")
    if failure_code is None:
        return locked

    if not terminal_failure:
        row = _fetch_one(
            connection,
            _UPDATE_MES_DELIVERY_UNCERTAIN,
            {"action_id": action_id, "last_error": failure_code},
            "DELIVERY_STATE_CHANGED",
        )
        return _delivery_row(row)

    record = _run_audit_record(
        AuditEvent.ACTION_SEND_FAILED,
        entity_id=action_id,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        after={
            "channel": DeliveryChannel.MES_MOCK.value,
            "transport": "KAFKA",
            "reason_code": failure_code,
        },
    )

    def _run() -> Any:
        row = _fetch_one(
            connection,
            _UPDATE_MES_DELIVERY_FAILED,
            {"action_id": action_id, "last_error": failure_code},
            "DELIVERY_STATE_CHANGED",
        )
        append_audit_log(connection, record)
        return row

    return _delivery_row(_write(connection, _run))


def settle_delivery_callback(
    connection: Connection,
    *,
    action_id: str,
    channel: DeliveryChannel,
    status: DeliveryStatus,
    provider_message_id: str | None,
    request_hash: str,
    completed_at: datetime,
    error_code: str | None,
    event_id: str,
) -> DeliveryCallbackTransition:
    """Lock one delivery and apply at most one terminal callback transition.

    A matching terminal row is returned unchanged as a duplicate.  The callback
    cannot revive pre-send or canceled rows and cannot replace a terminal result.
    ``request_hash`` contains ``action_id`` and is compared with the locked row,
    binding the signed body to the otherwise unsigned path identity.
    A callback within the cross-host clock-skew tolerance is accepted and clamped
    to ``started_at`` so the deployed monotonic timestamp CHECK remains valid.
    """

    _require_transaction(connection)
    action_id = _require_text(action_id, "action_id")
    request_hash = _require_text(request_hash, "request_hash")
    event_id = _require_text(event_id, "event_id")
    provider_message_id = _optional_text(
        provider_message_id,
        "provider_message_id",
    )
    error_code = _optional_text(error_code, "error_code")
    if not _HEX64.fullmatch(request_hash):
        raise RepositoryContractError("INVALID_REQUEST_HASH")
    if not isinstance(completed_at, datetime) or completed_at.utcoffset() is None:
        raise RepositoryContractError("INVALID_COMPLETED_AT")
    try:
        resolved_channel = DeliveryChannel(channel)
        resolved_status = DeliveryStatus(status)
    except ValueError as exc:
        raise RepositoryContractError("INVALID_DELIVERY_CALLBACK_ENUM") from exc
    if resolved_status is DeliveryStatus.SENT:
        if provider_message_id is None or error_code is not None:
            raise RepositoryContractError("INVALID_SENT_CALLBACK")
    elif resolved_status is DeliveryStatus.FAILED:
        if error_code is None:
            raise RepositoryContractError("INVALID_FAILED_CALLBACK")
    else:
        raise RepositoryContractError("CALLBACK_STATUS_NOT_TERMINAL")

    locked = _delivery_row(
        _fetch_one(
            connection,
            _SELECT_DELIVERY_FOR_UPDATE,
            {"action_id": action_id, "channel": resolved_channel.value},
            "DELIVERY_NOT_FOUND",
        )
    )
    if locked.request_hash != request_hash:
        raise RepositoryConflict("DELIVERY_REQUEST_HASH_MISMATCH")
    if locked.status in {DeliveryStatus.SENT, DeliveryStatus.FAILED}:
        if locked.status is not resolved_status:
            raise RepositoryConflict("DELIVERY_TERMINAL_STATUS_CHANGED")
        return DeliveryCallbackTransition(delivery=locked, duplicate=True)
    if locked.status is not DeliveryStatus.SENDING:
        raise RepositoryConflict("DELIVERY_NOT_SENDING")
    started_at = locked.started_at
    if started_at is None or completed_at < started_at - _DELIVERY_CLOCK_SKEW_TOLERANCE:
        raise RepositoryContractError("DELIVERY_COMPLETED_AT_INVALID")
    stored_completed_at = max(completed_at, started_at)

    transport = "N8N_WEBHOOK" if resolved_channel is DeliveryChannel.EMAIL else "KAFKA"
    result_json = _json_payload(
        {"event_id": event_id, "transport": transport},
        "result",
    )
    audit = _run_audit_record(
        (
            AuditEvent.ACTION_SENT
            if resolved_status is DeliveryStatus.SENT
            else AuditEvent.ACTION_SEND_FAILED
        ),
        entity_id=action_id,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        after={
            "channel": resolved_channel.value,
            "transport": transport,
        },
    )

    def _run() -> Any:
        row = connection.execute(
            _UPDATE_DELIVERY_CALLBACK,
            {
                "action_id": action_id,
                "channel": resolved_channel.value,
                "status": resolved_status.value,
                "provider_message_id": provider_message_id,
                "completed_at": stored_completed_at,
                "error_code": error_code,
                "result": result_json,
            },
        ).one_or_none()
        if row is None:
            raise RepositoryConflict("DELIVERY_STATE_CHANGED")
        append_audit_log(connection, audit)
        return row

    updated = _delivery_row(_write(connection, _run))
    return DeliveryCallbackTransition(delivery=updated, duplicate=False)


@dataclass(frozen=True, slots=True)
class ActionBundle:
    """멱등 재사용 판정에 필요한 action 저장 bundle의 최소 projection."""

    action_id: str
    action_code: ActionCode
    approval_id: str | None
    approval_status: ApprovalStatus | None
    approval_agent_run_id: str | None
    delivery_channels: tuple[DeliveryChannel, ...]


_SELECT_ACTION_BUNDLE = text(
    """
    SELECT h.action_id, h.action_code, p.approval_id,
           p.status AS approval_status,
           p.agent_run_id AS approval_agent_run_id
    FROM action_history AS h
    LEFT JOIN approval_request AS p ON p.action_id = h.action_id
    WHERE h.action_id = :action_id
    """
)


def get_action_bundle(connection: Connection, action_id: str) -> ActionBundle:
    """action·approval·delivery identity를 한 snapshot에서 읽는다."""

    action_id = _require_text(action_id, "action_id")
    row = _fetch_one(
        connection,
        _SELECT_ACTION_BUNDLE,
        {"action_id": action_id},
        "ACTION_NOT_FOUND",
    )
    try:
        action_code = ActionCode(row.action_code)
        approval_status = (
            None if row.approval_status is None else ApprovalStatus(row.approval_status)
        )
    except ValueError as exc:
        raise RepositoryContractError("ACTION_BUNDLE_INVALID") from exc
    deliveries = list_action_deliveries(connection, action_id)
    return ActionBundle(
        action_id=row.action_id,
        action_code=action_code,
        approval_id=row.approval_id,
        approval_status=approval_status,
        approval_agent_run_id=row.approval_agent_run_id,
        delivery_channels=tuple(item.channel for item in deliveries),
    )


# ---------------------------------------------------------------------------
# API v3 공개 목록 read model — 한 endpoint당 고정 1 query
# ---------------------------------------------------------------------------

_PUBLIC_READ_OMISSIONS: Counter[tuple[str, str]] = Counter()
_PUBLIC_READ_OMISSIONS_LOCK = Lock()


def record_public_read_omission(surface: str, code: str) -> None:
    """손상 행 제외를 식별자 없이 로그·process-local counter에 남긴다."""

    with _PUBLIC_READ_OMISSIONS_LOCK:
        _PUBLIC_READ_OMISSIONS[(surface, code)] += 1
    logger.warning("public %s row omitted (code=%s)", surface, code)


def public_read_omission_counts() -> dict[tuple[str, str], int]:
    """운영 관측·테스트용 snapshot. 내부 mutable counter를 직접 노출하지 않는다."""

    with _PUBLIC_READ_OMISSIONS_LOCK:
        return dict(_PUBLIC_READ_OMISSIONS)


@dataclass(frozen=True, slots=True)
class PublicToolCallRecord:
    tool_name: str
    status: ToolCallStatus


@dataclass(frozen=True, slots=True)
class PublicDeliveryRecord:
    channel: DeliveryChannel
    status: DeliveryStatus


@dataclass(frozen=True, slots=True)
class PublicActionDeliveryRecord:
    channel: DeliveryChannel
    status: DeliveryStatus
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PublicAgentRunRecord:
    agent_run_id: str
    created_at: datetime
    requested_alarm: AlarmRef
    chamber_id: str
    predicted_fault_code: FaultHypothesis | None
    confidence: float | None
    recommended_action: ActionCode | None
    status: RunStatus
    action_id: str | None
    approval_id: str | None
    tools: tuple[PublicToolCallRecord, ...]
    deliveries: tuple[PublicDeliveryRecord, ...]
    latency_ms: int
    llm_model: str
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    prediction_cause_summary: str | None = None
    prediction_evidence: dict[str, Any] | None = None
    prediction_llm_model: str | None = None
    prediction_prompt_version: str | None = None
    prediction_created_at: datetime | None = None
    lot_id: str | None = None
    retry_of_run_id: str | None = None
    autonomy_level: int = 2
    run_evidence: dict[str, Any] | None = None


# API v3가 bare array를 유지하므로 DB가 무한히 자라도 한 요청이 모두 읽지 않는다.
PUBLIC_AGENT_RUN_LIMIT: Final = 500


@dataclass(frozen=True, slots=True)
class PublicApprovalRecord:
    approval_id: str
    agent_run_id: str
    action_id: str
    created_at: datetime
    lot_id: str
    equipment_id: str
    chamber_id: str
    predicted_fault_code: FaultHypothesis
    action_code: ActionCode
    reason: str
    status: ApprovalStatus
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None


@dataclass(frozen=True, slots=True)
class PublicActionRecord:
    action_id: str
    agent_run_id: str
    action_code: ActionCode
    lot_id: str
    equipment_id: str | None
    chamber_id: str
    reason: str
    approval_status: ApprovalStatus | None
    deliveries: tuple[PublicActionDeliveryRecord, ...]
    created_at: datetime


_SELECT_PUBLIC_AGENT_RUNS = text(
    """
    SELECT r.agent_run_id,
           r.started_at AS created_at,
           r.lot_id,
           r.retry_of_run_id,
           r.requested_alarm_source,
           r.requested_alarm_id,
           r.chamber_id,
           p.predicted_fault_code,
           p.confidence,
           r.action AS recommended_action,
           r.status,
           linked.action_id,
           action.action_code AS stored_action_code,
           approval.approval_id,
           approval.agent_run_id AS approval_agent_run_id,
           COALESCE(tool_rows.items, '[]'::jsonb) AS tools,
           COALESCE(delivery_rows.items, '[]'::jsonb) AS deliveries,
           r.latency_ms,
           r.evidence -> 'active_timing' AS active_timing,
           r.autonomy_level,
           r.evidence AS run_evidence,
           clock_timestamp() AS observed_at,
           r.llm_model,
           r.prompt_version,
           r.input_tokens,
           r.output_tokens,
           p.cause_summary AS prediction_cause_summary,
           p.evidence AS prediction_evidence,
           p.llm_model AS prediction_llm_model,
           p.prompt_version AS prediction_prompt_version,
           p.created_at AS prediction_created_at
    FROM agent_run AS r
    LEFT JOIN agent_prediction AS p
      ON p.agent_run_id = r.agent_run_id
    LEFT JOIN agent_run_action AS linked
      ON linked.agent_run_id = r.agent_run_id
     AND linked.link_role = 'CREATED'
    LEFT JOIN action_history AS action
      ON action.action_id = linked.action_id
    LEFT JOIN approval_request AS approval
      ON approval.action_id = linked.action_id
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
                   jsonb_build_object(
                       'tool_name', tool.tool_name,
                       'status', tool.status
                   )
                   ORDER BY tool.call_seq
               ) AS items
        FROM agent_tool_call AS tool
        WHERE tool.agent_run_id = r.agent_run_id
    ) AS tool_rows ON TRUE
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
                   jsonb_build_object(
                       'channel', delivery.channel,
                       'status', delivery.status
                   )
                   ORDER BY delivery.channel
               ) AS items
        FROM action_delivery AS delivery
        WHERE delivery.action_id = linked.action_id
    ) AS delivery_rows ON TRUE
    WHERE (
        CAST(:date_from AS timestamptz) IS NULL
        OR r.started_at >= CAST(:date_from AS timestamptz)
    )
      AND (
        CAST(:date_to AS timestamptz) IS NULL
        OR r.started_at < CAST(:date_to AS timestamptz)
      )
      AND (CAST(:run_status AS text) IS NULL OR r.status = CAST(:run_status AS text))
      AND (
        CAST(:fault_code AS text) IS NULL
        OR p.predicted_fault_code = CAST(:fault_code AS text)
      )
      AND (CAST(:run_id AS text) IS NULL OR r.agent_run_id = CAST(:run_id AS text))
    ORDER BY r.started_at DESC, r.agent_run_id DESC
    LIMIT :limit
    """
)


_PUBLIC_ACTION_SELECT = """
    SELECT action.action_id,
           linked.agent_run_id,
           action.action_code,
           action.lot_id,
           COALESCE(
               action.equipment_id,
               incident_equipment.equipment_id
           ) AS equipment_id,
           action.chamber_id,
           action.reason,
           approval.status AS approval_status,
           COALESCE(delivery_rows.items, '[]'::jsonb) AS deliveries,
           action.created_at
    FROM action_history AS action
    LEFT JOIN agent_run_action AS linked
      ON linked.action_id = action.action_id
     AND linked.link_role = 'CREATED'
    LEFT JOIN approval_request AS approval
      ON approval.action_id = action.action_id
    LEFT JOIN LATERAL (
        SELECT CASE
                 WHEN count(DISTINCT history.equipment_id) = 1
                 THEN min(history.equipment_id)
                 ELSE NULL
               END AS equipment_id
        FROM lot_history AS history
        WHERE history.lot_id = action.lot_id
          AND history.chamber_id = action.chamber_id
          AND history.equipment_id IS NOT NULL
    ) AS incident_equipment ON TRUE
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
                   jsonb_build_object(
                       'channel', delivery.channel,
                       'status', delivery.status,
                       'started_at', delivery.started_at,
                       'completed_at', delivery.completed_at
                   )
                   ORDER BY delivery.channel
               ) AS items
        FROM action_delivery AS delivery
        WHERE delivery.action_id = action.action_id
    ) AS delivery_rows ON TRUE
"""

_SELECT_PUBLIC_ACTIONS = text(
    f"""{_PUBLIC_ACTION_SELECT}
    WHERE (CAST(:action_code AS text) IS NULL
           OR action.action_code = CAST(:action_code AS text))
    ORDER BY action.created_at DESC, action.action_id DESC
    """
)

_SELECT_PUBLIC_ACTION = text(
    f"""{_PUBLIC_ACTION_SELECT}
    WHERE action.action_id = :action_id
    """
)


_PUBLIC_APPROVAL_SELECT = """
    SELECT approval.approval_id,
           approval.agent_run_id,
           approval.action_id,
           approval.requested_at AS created_at,
           run.lot_id,
           run.chamber_id,
           prediction.predicted_fault_code,
           action.action_code,
           action.reason,
           approval.status,
           approval.decided_by,
           approval.decided_at,
           approval.decision_comment,
           linked.agent_run_id AS linked_agent_run_id,
           linked.action_id AS linked_action_id,
           linked.lot_id AS linked_lot_id,
           linked.chamber_id AS linked_chamber_id,
           action.lot_id AS action_lot_id,
           action.chamber_id AS action_chamber_id,
           ARRAY(
               SELECT DISTINCT history.equipment_id
               FROM lot_history AS history
               WHERE history.lot_id = run.lot_id
                 AND history.chamber_id = run.chamber_id
                 AND history.equipment_id IS NOT NULL
               ORDER BY history.equipment_id
           ) AS equipment_ids
    FROM approval_request AS approval
    LEFT JOIN agent_run AS run
      ON run.agent_run_id = approval.agent_run_id
    LEFT JOIN agent_prediction AS prediction
      ON prediction.agent_run_id = approval.agent_run_id
    LEFT JOIN agent_run_action AS linked
      ON linked.agent_run_id = approval.agent_run_id
     AND linked.action_id = approval.action_id
     AND linked.link_role = 'CREATED'
    LEFT JOIN action_history AS action
      ON action.action_id = approval.action_id
"""

_SELECT_PUBLIC_APPROVALS = text(
    f"""{_PUBLIC_APPROVAL_SELECT}
    WHERE approval.status = ANY(:public_statuses)
    ORDER BY approval.requested_at DESC, approval.approval_id DESC
    """
)

_SELECT_PUBLIC_APPROVAL = text(
    f"""{_PUBLIC_APPROVAL_SELECT}
    WHERE approval.approval_id = :approval_id
      AND approval.status = ANY(:public_statuses)
    """
)


def _public_tool_records(value: object) -> tuple[PublicToolCallRecord, ...]:
    if not isinstance(value, list):
        raise RepositoryContractError("PUBLIC_TOOL_ROWS_INVALID")
    records: list[PublicToolCallRecord] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"tool_name", "status"}:
            raise RepositoryContractError("PUBLIC_TOOL_ROW_INVALID")
        try:
            records.append(
                PublicToolCallRecord(
                    tool_name=_require_text(item["tool_name"], "tool_name"),
                    status=ToolCallStatus(item["status"]),
                )
            )
        except ValueError as exc:
            raise RepositoryContractError("PUBLIC_TOOL_STATUS_INVALID") from exc
    return tuple(records)


def _public_delivery_records(value: object) -> tuple[PublicDeliveryRecord, ...]:
    if not isinstance(value, list):
        raise RepositoryContractError("PUBLIC_DELIVERY_ROWS_INVALID")
    records: list[PublicDeliveryRecord] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"channel", "status"}:
            raise RepositoryContractError("PUBLIC_DELIVERY_ROW_INVALID")
        try:
            records.append(
                PublicDeliveryRecord(
                    channel=DeliveryChannel(item["channel"]),
                    status=DeliveryStatus(item["status"]),
                )
            )
        except ValueError as exc:
            raise RepositoryContractError("PUBLIC_DELIVERY_VALUE_INVALID") from exc
    return tuple(records)


def _public_agent_run_record(row: Row[Any]) -> PublicAgentRunRecord:
    try:
        predicted = (
            None
            if row.predicted_fault_code is None
            else FaultHypothesis(row.predicted_fault_code)
        )
        recommended = (
            None
            if row.recommended_action is None
            else ActionCode(row.recommended_action)
        )
        status = RunStatus(row.status)
    except ValueError as exc:
        raise RepositoryContractError("PUBLIC_RUN_ENUM_INVALID") from exc
    if row.action_id is not None:
        try:
            stored_action = ActionCode(row.stored_action_code)
        except (TypeError, ValueError) as exc:
            raise RepositoryContractError("PUBLIC_RUN_ACTION_MISSING") from exc
        if stored_action is not recommended:
            raise RepositoryContractError("PUBLIC_RUN_ACTION_MISMATCH")
    if row.approval_id is not None and row.approval_agent_run_id != row.agent_run_id:
        raise RepositoryContractError("PUBLIC_RUN_APPROVAL_MISMATCH")
    latency_ms = _active_latency_snapshot_ms(
        status=status,
        subtotal_ms=row.latency_ms,
        started_at=row.created_at,
        timing=row.active_timing,
        now=row.observed_at,
    )
    llm_model = _require_text(row.llm_model, "llm_model")
    confidence = row.confidence
    return PublicAgentRunRecord(
        agent_run_id=row.agent_run_id,
        created_at=row.created_at,
        requested_alarm=_alarm_ref(row.requested_alarm_source, row.requested_alarm_id),
        chamber_id=row.chamber_id,
        predicted_fault_code=predicted,
        confidence=(
            float(confidence) if isinstance(confidence, Decimal) else confidence
        ),
        recommended_action=recommended,
        status=status,
        action_id=row.action_id,
        approval_id=row.approval_id,
        tools=_public_tool_records(row.tools),
        deliveries=_public_delivery_records(row.deliveries),
        latency_ms=latency_ms,
        llm_model=llm_model,
        prompt_version=getattr(row, "prompt_version", None),
        input_tokens=getattr(row, "input_tokens", None),
        output_tokens=getattr(row, "output_tokens", None),
        prediction_cause_summary=getattr(row, "prediction_cause_summary", None),
        prediction_evidence=(
            None
            if getattr(row, "prediction_evidence", None) is None
            else dict(row.prediction_evidence)
        ),
        prediction_llm_model=getattr(row, "prediction_llm_model", None),
        prediction_prompt_version=getattr(row, "prediction_prompt_version", None),
        prediction_created_at=getattr(row, "prediction_created_at", None),
        lot_id=getattr(row, "lot_id", None),
        retry_of_run_id=getattr(row, "retry_of_run_id", None),
        autonomy_level=int(getattr(row, "autonomy_level", 2)),
        run_evidence=(
            None
            if getattr(row, "run_evidence", None) is None
            else dict(row.run_evidence)
        ),
    )


def list_agent_runs_public(
    connection: Connection,
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    status: RunStatus | None = None,
    predicted_fault_code: FaultHypothesis | None = None,
    run_id: str | None = None,
) -> list[PublicAgentRunRecord]:
    """공개 실행 목록을 행 수와 무관한 단일 query로 읽는다.

    Tool input/output/error와 delivery 전송 상세는 SELECT 자체에서 제외한다. 공개
    serializer가 필드를 버리는 방식보다 DB 경계에서 읽지 않는 편이 누출 면적이 작다.
    """

    rows = _fetch_all(
        connection,
        _SELECT_PUBLIC_AGENT_RUNS,
        {
            "date_from": date_from,
            "date_to": date_to,
            "limit": PUBLIC_AGENT_RUN_LIMIT,
            "run_status": None if status is None else RunStatus(status).value,
            "fault_code": (
                None
                if predicted_fault_code is None
                else FaultHypothesis(predicted_fault_code).value
            ),
            "run_id": None if run_id is None else _require_text(run_id, "run_id"),
        },
    )
    records: list[PublicAgentRunRecord] = []
    for row in rows:
        try:
            records.append(_public_agent_run_record(row))
        except RepositoryContractError as exc:
            # 식별자·row·원문은 로그에 넣지 않는다. 한 손상 행이 정상 실행 이력을
            # 가리지 않되 운영자는 고정 code로 원인을 집계할 수 있다.
            record_public_read_omission("agent_run", exc.code)
    return records


def get_agent_run_public(
    connection: Connection, agent_run_id: str
) -> PublicAgentRunRecord:
    records = list_agent_runs_public(
        connection,
        date_from=None,
        date_to=None,
        run_id=agent_run_id,
    )
    if not records:
        raise RepositoryNotFound("RUN_NOT_FOUND")
    if len(records) != 1:
        raise RepositoryContractError("PUBLIC_RUN_NOT_EXACTLY_ONE")
    return records[0]


def _public_action_delivery_records(
    value: object,
) -> tuple[PublicActionDeliveryRecord, ...]:
    if not isinstance(value, list):
        raise RepositoryContractError("PUBLIC_ACTION_DELIVERIES_INVALID")
    records: list[PublicActionDeliveryRecord] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "channel",
            "status",
            "started_at",
            "completed_at",
        }:
            raise RepositoryContractError("PUBLIC_ACTION_DELIVERY_INVALID")
        try:
            records.append(
                PublicActionDeliveryRecord(
                    channel=DeliveryChannel(item["channel"]),
                    status=DeliveryStatus(item["status"]),
                    started_at=item["started_at"],
                    completed_at=item["completed_at"],
                )
            )
        except ValueError as exc:
            raise RepositoryContractError(
                "PUBLIC_ACTION_DELIVERY_VALUE_INVALID"
            ) from exc
    return tuple(records)


def _public_action_record(row: Row[Any]) -> PublicActionRecord:
    if row.agent_run_id is None:
        raise RepositoryContractError("PUBLIC_ACTION_RUN_MISSING")
    try:
        action_code = ActionCode(row.action_code)
        approval_status = (
            None if row.approval_status is None else ApprovalStatus(row.approval_status)
        )
    except ValueError as exc:
        raise RepositoryContractError("PUBLIC_ACTION_ENUM_INVALID") from exc
    if action_code is ActionCode.EQP_HOLD:
        if approval_status not in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
        }:
            raise RepositoryContractError("PUBLIC_ACTION_APPROVAL_MISSING")
    elif approval_status is not None:
        raise RepositoryContractError("PUBLIC_ACTION_APPROVAL_UNEXPECTED")
    deliveries = _public_action_delivery_records(row.deliveries)
    expected_channels = resolve_delivery_channels(action_code)
    if tuple(item.channel for item in deliveries) != tuple(sorted(expected_channels)):
        raise RepositoryContractError("PUBLIC_ACTION_DELIVERY_SET_INVALID")
    return PublicActionRecord(
        action_id=_require_text(row.action_id, "action_id"),
        agent_run_id=_require_text(row.agent_run_id, "agent_run_id"),
        action_code=action_code,
        lot_id=_require_text(row.lot_id, "lot_id"),
        equipment_id=_optional_text(row.equipment_id, "equipment_id"),
        chamber_id=_require_text(row.chamber_id, "chamber_id"),
        reason=_require_text(row.reason, "reason"),
        approval_status=approval_status,
        deliveries=deliveries,
        created_at=row.created_at,
    )


def list_actions_public(
    connection: Connection,
    *,
    action_code: ActionCode | None = None,
) -> list[PublicActionRecord]:
    rows = _fetch_all(
        connection,
        _SELECT_PUBLIC_ACTIONS,
        {
            "action_code": (
                None if action_code is None else ActionCode(action_code).value
            )
        },
    )
    return [_public_action_record(row) for row in rows]


def get_action_public(connection: Connection, action_id: str) -> PublicActionRecord:
    row = _fetch_one(
        connection,
        _SELECT_PUBLIC_ACTION,
        {"action_id": _require_text(action_id, "action_id")},
        "ACTION_NOT_FOUND",
    )
    return _public_action_record(row)


def _public_approval_record(row: Row[Any]) -> PublicApprovalRecord:
    required_child_values = (
        row.lot_id,
        row.chamber_id,
        row.predicted_fault_code,
        row.action_code,
        row.reason,
        row.linked_agent_run_id,
        row.linked_action_id,
        row.linked_lot_id,
        row.linked_chamber_id,
        row.action_lot_id,
        row.action_chamber_id,
    )
    if any(value is None for value in required_child_values):
        raise RepositoryContractError("PUBLIC_APPROVAL_CHILD_MISSING")

    equipment_ids = row.equipment_ids
    if not isinstance(equipment_ids, list) or len(equipment_ids) != 1:
        raise RepositoryContractError("PUBLIC_APPROVAL_EQUIPMENT_NOT_EXACTLY_ONE")
    equipment_id = _require_text(equipment_ids[0], "equipment_id")
    if (
        row.linked_agent_run_id != row.agent_run_id
        or row.linked_action_id != row.action_id
        or row.linked_lot_id != row.lot_id
        or row.linked_chamber_id != row.chamber_id
        or row.action_lot_id != row.lot_id
        or row.action_chamber_id != row.chamber_id
    ):
        raise RepositoryContractError("PUBLIC_APPROVAL_IDENTITY_MISMATCH")
    try:
        status = ApprovalStatus(row.status)
        if status not in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
        }:
            raise ValueError
        action_code = ActionCode(row.action_code)
        if action_code is not ActionCode.EQP_HOLD:
            raise ValueError
        predicted = FaultHypothesis(row.predicted_fault_code)
    except ValueError as exc:
        raise RepositoryContractError("PUBLIC_APPROVAL_ENUM_INVALID") from exc

    return PublicApprovalRecord(
        approval_id=row.approval_id,
        agent_run_id=row.agent_run_id,
        action_id=row.action_id,
        created_at=row.created_at,
        lot_id=_require_text(row.lot_id, "lot_id"),
        equipment_id=equipment_id,
        chamber_id=_require_text(row.chamber_id, "chamber_id"),
        predicted_fault_code=predicted,
        action_code=action_code,
        reason=_require_text(row.reason, "reason"),
        status=status,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_comment=row.decision_comment,
    )


def list_approvals_public(connection: Connection) -> list[PublicApprovalRecord]:
    """공개 승인 이력을 단일 query로 읽고 손상 행만 fail-closed 격리한다."""

    rows = _fetch_all(
        connection,
        _SELECT_PUBLIC_APPROVALS,
        {
            "public_statuses": [
                ApprovalStatus.PENDING.value,
                ApprovalStatus.APPROVED.value,
                ApprovalStatus.REJECTED.value,
            ]
        },
    )
    records: list[PublicApprovalRecord] = []
    for row in rows:
        try:
            records.append(_public_approval_record(row))
        except RepositoryContractError as exc:
            record_public_read_omission("approval", exc.code)
    return records


def get_approval_public(
    connection: Connection, approval_id: str
) -> PublicApprovalRecord:
    """결정 UoW 안에서 대상 한 건과 child cardinality를 되읽는다."""

    target = _require_text(approval_id, "approval_id")
    row = _fetch_one(
        connection,
        _SELECT_PUBLIC_APPROVAL,
        {
            "approval_id": target,
            "public_statuses": [
                ApprovalStatus.PENDING.value,
                ApprovalStatus.APPROVED.value,
                ApprovalStatus.REJECTED.value,
            ],
        },
        "APPROVAL_NOT_FOUND",
    )
    return _public_approval_record(row)
