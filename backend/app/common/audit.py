"""감사 쓰기 계약 (`V5-CM-4.2`).

Common은 **append 계약 하나**만 소유한다. 각 도메인이 자기 업무 트랜잭션에서 자기
event를 기록하고(`V5-C-0.1`·A), D가 append 원본을 조회한다(`V5-D-1.1`).

```text
도메인 업무 INSERT/UPDATE
        ↓ 같은 Connection·같은 활성 transaction
append_audit_log(connection, record)
        ↓ INSERT audit_log 1회
caller commit 또는 caller rollback
```

append-only는 세 계층이 함께 만든다. 이 모듈은 첫 번째 층만 담당한다.

```text
V5-CM-4.2   application helper: INSERT-only
V5-CM-3.5   DB role: audit_log SELECT·INSERT만
V5-D-1.2    API: GET만, write route 0건
```
"""

import json
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.common.enums import ActorType


class AuditEvent(StrEnum):
    DETECTION_COMPLETED = "DETECTION_COMPLETED"
    AGENT_RUN_STARTED = "AGENT_RUN_STARTED"
    HYPOTHESIS_GENERATED = "HYPOTHESIS_GENERATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    ACTION_SENT = "ACTION_SENT"
    ACTION_SEND_FAILED = "ACTION_SEND_FAILED"
    AGENT_RUN_COMPLETED = "AGENT_RUN_COMPLETED"
    AGENT_RUN_FAILED = "AGENT_RUN_FAILED"


class AuditEntityType(StrEnum):
    LOT_HIST = "LOT_HIST"
    AGENT_RUN = "AGENT_RUN"
    APPROVAL = "APPROVAL"
    ACTION = "ACTION"


#: event → entity 고정 mapping (API 명세 §6 · `002_agent_runtime_clean.sql` CHECK).
#:
#: **읽기 전용이다.** entity_type은 입력 조합이 아니라 event의 성질이므로, 실행 중에
#: 덮어쓰면 migration CHECK가 거부할 행을 애플리케이션이 만들어 낼 수 있다.
EVENT_ENTITY_TYPE: MappingProxyType[AuditEvent, AuditEntityType] = MappingProxyType(
    {
        AuditEvent.DETECTION_COMPLETED: AuditEntityType.LOT_HIST,
        AuditEvent.AGENT_RUN_STARTED: AuditEntityType.AGENT_RUN,
        AuditEvent.HYPOTHESIS_GENERATED: AuditEntityType.AGENT_RUN,
        AuditEvent.APPROVAL_REQUESTED: AuditEntityType.APPROVAL,
        AuditEvent.APPROVAL_DECIDED: AuditEntityType.APPROVAL,
        AuditEvent.ACTION_SENT: AuditEntityType.ACTION,
        AuditEvent.ACTION_SEND_FAILED: AuditEntityType.ACTION,
        AuditEvent.AGENT_RUN_COMPLETED: AuditEntityType.AGENT_RUN,
        AuditEvent.AGENT_RUN_FAILED: AuditEntityType.AGENT_RUN,
    }
)

#: `ACTION_SENT`·`ACTION_SEND_FAILED`가 반드시 남겨야 하는 `after` key.
#:
#: 시스템설계 §11: "ACTION_SENT·ACTION_SEND_FAILED는 channel과 transport를 기록한다."
#: 나머지 event의 payload schema는 Common이 추측하지 않는다 — 도메인 typed payload는
#: A/C 구현 Task가 소유한다.
ACTION_DELIVERY_KEYS = ("channel", "transport")
_ACTION_EVENTS = frozenset({AuditEvent.ACTION_SENT, AuditEvent.ACTION_SEND_FAILED})

# DB varchar 경계를 insert 전에 막는다. 넘겨 보내면 DataError로만 알 수 있고,
# 그때는 이미 caller의 업무 트랜잭션이 열려 있다.
_ENTITY_ID_MAX = 20  # audit_log.entity_id varchar(20)
_ACTOR_ID_MAX = 40  # audit_log.actor_id  varchar(40)

AuditEntityId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=_ENTITY_ID_MAX),
]
AuditActorId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=_ACTOR_ID_MAX),
]


class AuditContractError(ValueError):
    """감사 계약 위반. DB에 닿기 전에 발생한다."""


class AuditRecord(BaseModel):
    # 공백만 있는 entity_id 가 min_length 를 통과하지 않도록 먼저 strip 한다.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: AuditEvent
    actor_type: ActorType
    entity_id: AuditEntityId
    actor_id: AuditActorId | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detail: str | None = None

    # `occurred_at`은 입력 field가 아니다. append 시각을 caller가 과거·미래로 주입하면
    # 감사 원본의 의미가 깨진다. 시각은 DB가 기록한다(`_INSERT_AUDIT_LOG`).

    @property
    def entity_type(self) -> AuditEntityType:
        """이벤트마다 고정이므로 입력으로 받지 않는다.

        잘못된 조합 자체를 만들 수 없게 하려는 목적이다.
        """
        return EVENT_ENTITY_TYPE[self.event_type]

    @model_validator(mode="after")
    def _require_action_delivery_context(self) -> "AuditRecord":
        if self.event_type not in _ACTION_EVENTS:
            # 근거 없는 요구를 다른 event로 넓히지 않는다.
            return self

        after = self.after or {}
        for key in ACTION_DELIVERY_KEYS:
            value = after.get(key)
            if not isinstance(value, str) or not value.strip():
                raise AuditContractError(
                    f"{self.event_type.value}는 after.{key}가 필요합니다"
                )
        return self


# **`clock_timestamp()`이며 `now()`가 아니다.**
#
# PostgreSQL `now()`는 `transaction_timestamp()`의 별칭이라 한 트랜잭션 안에서 값이
# 움직이지 않는다. 설계 §12.1의 advisory lock 획득·재조회·DML이 한 트랜잭션이므로,
# `now()`를 쓰면 lock 대기 시간만큼 실제 event 시각과 벌어진다. 설계 §11이 MES
# `ACTION_SENT`를 "fdc.actions.result에서 성공을 확인한 시점"에 기록하라고 한 것도
# statement 시각을 요구한다. DDL의 `DEFAULT now()`를 타지 않도록 값을 명시한다.
_INSERT_AUDIT_LOG = text(
    """
    INSERT INTO audit_log (
        occurred_at, actor_type, actor_id, event_type,
        entity_type, entity_id, before_json, after_json, detail
    ) VALUES (
        clock_timestamp(), :actor_type, :actor_id, :event_type,
        :entity_type, :entity_id,
        CAST(:before_json AS jsonb), CAST(:after_json AS jsonb), :detail
    )
    RETURNING audit_id
    """
)


def append_audit_log(connection: Connection, record: AuditRecord) -> int:
    """감사로그를 append 한다. append-only 이므로 UPDATE·DELETE 는 제공하지 않는다.

    호출자가 연 트랜잭션 Connection 을 그대로 사용하고 내부에서 commit 하지 않는다.
    업무 트랜잭션이 롤백되면 감사로그도 함께 롤백돼야 하기 때문이다.

    **`in_transaction()` 가드가 증명하는 범위는 좁다.** SQLAlchemy 2.x는 첫
    `execute()`에서 암묵적으로 transaction을 연다. 따라서 참이라는 사실은 "caller가
    transaction을 소유하고 commit할 의사가 있다"가 아니라 "앞서 statement가 한 번
    실행됐다"는 뜻이다. 이 가드는 **감사 INSERT가 unit of work의 첫 statement가 되는
    실수**만 막는다 — 그 경우 `with engine.connect()` 블록이 commit 없이 끝나면 감사가
    조용히 사라진다. 같은 Connection·caller commit·rollback 원자성 증명은 실제 DB
    fixture를 쓰는 `V5-C-0.1` 통합 테스트가 소유한다.
    """
    if not connection.in_transaction():
        raise AuditContractError(
            "감사 append는 호출자가 연 트랜잭션 안에서만 수행합니다"
        )

    payload = record.model_dump(mode="json")
    row = connection.execute(
        _INSERT_AUDIT_LOG,
        {
            "actor_type": payload["actor_type"],
            "actor_id": payload["actor_id"],
            "event_type": payload["event_type"],
            "entity_type": record.entity_type.value,
            "entity_id": payload["entity_id"],
            "before_json": _dump_json(payload["before"]),
            "after_json": _dump_json(payload["after"]),
            "detail": payload["detail"],
        },
    ).one()

    return int(row.audit_id)


def _dump_json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
