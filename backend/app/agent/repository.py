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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
    resolve_severity,
)
from app.common.ids import new_agent_run_id, new_approval_id, new_tool_call_id
from app.common.schemas import AlarmRef

__all__ = [
    "AgentRepositoryError",
    "RepositoryConflict",
    "RepositoryContractError",
    "RepositoryNotFound",
    "RepositoryUnavailable",
    "AgentRunRow",
    "CreateAgentRunCommand",
    "PredictionRow",
    "HumanReviewRow",
    "RUNTIME_REVIEW_LABEL_SOURCES",
    "create_agent_run",
    "get_agent_run",
    "find_active_run",
    "list_run_alarms",
    "set_run_action",
    "finish_agent_run",
    "insert_prediction",
    "get_prediction",
    "insert_human_prediction_review",
    "list_human_prediction_reviews",
    "RunActionRow",
    "ToolCallRow",
    "ApprovalRequestRow",
    "ActionDeliveryRow",
    "RESERVED_TOOL_OUTPUT_KEYS",
    "RESERVED_ERROR_MSG",
    "link_run_action",
    "get_run_action",
    "find_created_action",
    "reserve_tool_call",
    "finalize_tool_call",
    "list_tool_calls",
    "count_tool_calls",
    "create_approval_request",
    "get_approval_request",
    "insert_action_delivery",
    "get_action_delivery",
    "list_action_deliveries",
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


class RepositoryUnavailable(AgentRepositoryError):
    """DB에 닿지 못했다. 업무 판정이 아니다."""


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
}


def _constraint_name(error: BaseException) -> str | None:
    """psycopg가 준 제약 이름만 꺼낸다. **메시지는 읽지 않는다.**

    문자열 매칭으로 이름을 추출하면 driver 메시지 형식이 바뀔 때 조용히 어긋난다.
    psycopg3는 구조화된 `diag.constraint_name`을 준다.
    """

    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    return str(name) if name else None


def _translate(error: SQLAlchemyError) -> AgentRepositoryError:
    if isinstance(error, IntegrityError):
        name = _constraint_name(error)
        code = CONFLICT_CODES.get(name or "")
        if code is not None:
            return RepositoryConflict(code)
        # CHECK·FK·NOT NULL 위반. 원인은 `raise ... from`으로 보존한다.
        return RepositoryContractError("CONSTRAINT_VIOLATION")
    if isinstance(error, OperationalError | InterfaceError):
        # **연결 실패만 Unavailable이다.**
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
    "reviewer": 40,
    "disposition": 16,
    "label_source": 16,
    "cause_summary": 0,  # text — 상한 없음. 공백만 금지한다.
    "action_id": 20,
    "tool_name": 40,
    "request_hash": 64,
    "provider_message_id": 0,  # text
    "error_msg": 0,  # text
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
    retry_of_run_id: str | None = None
    llm_model: str | None = None
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
        status, autonomy_level, llm_model, prompt_version
    ) VALUES (
        :agent_run_id, :thread_id, :retry_of_run_id, :lot_id, :chamber_id,
        :requested_alarm_source, :requested_alarm_id,
        :representative_alarm_source, :representative_alarm_id,
        :status, :autonomy_level, :llm_model, :prompt_version
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
        llm_model=_optional_text(command.llm_model, "llm_model"),
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
        row = connection.execute(
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
        ).one()
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


#: `finish_agent_run()`이 받을 수 있는 terminal 상태와 그 감사 event.
TERMINAL_EVENTS: Final[Mapping[RunStatus, AuditEvent]] = {
    RunStatus.COMPLETED: AuditEvent.AGENT_RUN_COMPLETED,
    RunStatus.FAILED: AuditEvent.AGENT_RUN_FAILED,
}

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
        row = connection.execute(
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
        ).one_or_none()
        if row is None:  # pragma: no cover - RETURNING은 항상 1행이다
            raise RepositoryNotFound("RUN_NOT_FOUND")
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
    row = _fetch_one(
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
        "RUN_NOT_FOUND",
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
    row = _fetch_one(connection, _INSERT_RUN_ACTION, payload, "RUN_NOT_FOUND")
    return _run_action_row(row)


def get_run_action(connection: Connection, agent_run_id: str) -> RunActionRow:
    row = _fetch_one(
        connection,
        _SELECT_RUN_ACTION,
        {"run_id": agent_run_id},
        "RUN_ACTION_NOT_FOUND",
    )
    return _run_action_row(row)


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
_LOCK_RUN = text("SELECT 1 FROM agent_run WHERE agent_run_id = :run_id FOR UPDATE")

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
        return connection.execute(
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
        ).one()

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

    예산 8회 정책은 `V5-C-2.2`가 이 값 위에 구현한다. 이 계층은 세기만 한다.
    """

    try:
        return int(
            connection.execute(_COUNT_TOOL_CALLS, {"run_id": agent_run_id}).scalar_one()
        )
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc


# ---------------------------------------------------------------------------
# approval_request · action_delivery — 초기 row만. 상태 머신은 후속이다
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
        row = connection.execute(
            _INSERT_APPROVAL,
            {
                "approval_id": approval_id,
                "action_id": action_id,
                "agent_run_id": agent_run_id,
                "status": ApprovalStatus.PENDING.value,
            },
        ).one()
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

#: `request_hash char(64)`는 소문자 hex다. 형식 위반은 DB가 CHECK로 막지만 그 전에
#: 걸러야 caller 입력 오류가 driver 예외로 올라가지 않는다.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

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
    row = _fetch_one(
        connection,
        _INSERT_DELIVERY,
        {
            "action_id": action_id,
            "channel": resolved_channel.value,
            "status": resolved.value,
            "request_hash": request_hash,
        },
        "ACTION_NOT_FOUND",
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
