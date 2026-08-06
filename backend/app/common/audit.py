import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.common.enums import ActorType
from app.common.ids import NonEmptyId


class AuditEvent(StrEnum):
    DETECTION_COMPLETED = "DETECTION_COMPLETED"
    AGENT_RUN_STARTED = "AGENT_RUN_STARTED"
    CLASSIFICATION_COMPLETED = "CLASSIFICATION_COMPLETED"
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


# 이벤트마다 entity_type 이 고정이다. 새 이벤트를 추가하지 않는다.
EVENT_ENTITY_TYPE: dict[AuditEvent, AuditEntityType] = {
    AuditEvent.DETECTION_COMPLETED: AuditEntityType.LOT_HIST,
    AuditEvent.AGENT_RUN_STARTED: AuditEntityType.AGENT_RUN,
    AuditEvent.CLASSIFICATION_COMPLETED: AuditEntityType.AGENT_RUN,
    AuditEvent.APPROVAL_REQUESTED: AuditEntityType.APPROVAL,
    AuditEvent.APPROVAL_DECIDED: AuditEntityType.APPROVAL,
    AuditEvent.ACTION_SENT: AuditEntityType.ACTION,
    AuditEvent.ACTION_SEND_FAILED: AuditEntityType.ACTION,
    AuditEvent.AGENT_RUN_COMPLETED: AuditEntityType.AGENT_RUN,
    AuditEvent.AGENT_RUN_FAILED: AuditEntityType.AGENT_RUN,
}


class AuditRecord(BaseModel):
    # 공백만 있는 entity_id 가 min_length 를 통과하지 않도록 먼저 strip 한다.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: AuditEvent
    actor_type: ActorType
    entity_id: NonEmptyId
    actor_id: NonEmptyId | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detail: str | None = None
    occurred_at: datetime | None = None

    @property
    def entity_type(self) -> AuditEntityType:
        """이벤트마다 고정이므로 입력으로 받지 않는다. 잘못된 조합 자체를 만들 수 없다."""
        return EVENT_ENTITY_TYPE[self.event_type]


_INSERT_AUDIT_LOG = text(
    """
    INSERT INTO audit_log (
        occurred_at, actor_type, actor_id, event_type,
        entity_type, entity_id, before_json, after_json, detail
    ) VALUES (
        COALESCE(:occurred_at, now()), :actor_type, :actor_id, :event_type,
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
    """
    payload = record.model_dump(mode="json")
    row = connection.execute(
        _INSERT_AUDIT_LOG,
        {
            "occurred_at": payload["occurred_at"],
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
