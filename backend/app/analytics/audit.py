"""감사로그 조회 — FR-D-07 · NFR-05(append-only).

GET /audit-logs 하나만 제공한다. UPDATE·DELETE 경로를 만들지 않는다.
audit_log 는 runtime DB 소유 테이블이므로 앱 계정 engine 으로 읽는다
(kosa_readonly 는 Text2SQL LLM 생성 SQL 전용 계약 — 여기서 쓰지 않는다).

모든 필터는 bound parameter 로만 전달한다. 문자열 조립으로 값을 넣지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

# DDL CHECK (event_type, entity_type) 쌍과 동일한 9종 — 화면 유형 목록·집계 축
AUDIT_EVENT_TYPES: tuple[str, ...] = (
    "DETECTION_COMPLETED",
    "AGENT_RUN_STARTED",
    "HYPOTHESIS_GENERATED",
    "APPROVAL_REQUESTED",
    "APPROVAL_DECIDED",
    "ACTION_SENT",
    "ACTION_SEND_FAILED",
    "AGENT_RUN_COMPLETED",
    "AGENT_RUN_FAILED",
)

# NFR-13: date 필터는 Asia/Seoul 자정 기준으로 해석한다.
_KST = timezone(timedelta(hours=9))


class AuditLogItem(BaseModel):
    audit_id: int
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    event_type: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    detail: str | None


class AuditLogPageResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    size: int
    event_types: list[str]
    event_type_counts: dict[str, int]


def _build_where(
    *,
    event_type: str | None,
    actor_type: str | None,
    entity_type: str | None,
    entity_id: str | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if event_type:
        clauses.append("event_type = :event_type")
        params["event_type"] = event_type
    if actor_type:
        clauses.append("actor_type = :actor_type")
        params["actor_type"] = actor_type
    if entity_type:
        clauses.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if entity_id:
        # 부분 일치 — 와일드카드는 bound 값 쪽에 붙인다
        clauses.append("entity_id ILIKE :entity_id")
        params["entity_id"] = f"%{entity_id}%"
    if date_from:
        clauses.append("occurred_at >= :date_from")
        params["date_from"] = datetime.combine(date_from, time.min, _KST)
    if date_to:
        # 종료일 포함 — 다음날 자정 미만
        clauses.append("occurred_at < :date_to")
        params["date_to"] = datetime.combine(date_to + timedelta(days=1), time.min, _KST)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def fetch_audit_logs(
    engine: Engine,
    *,
    event_type: str | None = None,
    actor_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    size: int = 20,
) -> AuditLogPageResponse:
    where, params = _build_where(
        event_type=event_type,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
    )

    items_sql = text(
        "SELECT audit_id, occurred_at, actor_type, actor_id, event_type,"
        "       entity_type, entity_id, before_json, after_json, detail"
        f" FROM audit_log {where}"
        " ORDER BY occurred_at DESC, audit_id DESC"
        " LIMIT :limit OFFSET :offset"
    )
    counts_sql = text(
        f"SELECT event_type, count(*) AS cnt FROM audit_log {where} GROUP BY event_type"
    )

    with engine.connect() as connection:
        rows = connection.execute(
            items_sql,
            {**params, "limit": size, "offset": (page - 1) * size},
        ).mappings()
        items = [
            AuditLogItem(
                audit_id=row["audit_id"],
                occurred_at=row["occurred_at"],
                actor_type=row["actor_type"],
                actor_id=row["actor_id"],
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                before=row["before_json"],
                after=row["after_json"],
                detail=row["detail"],
            )
            for row in rows
        ]

        counts = {row["event_type"]: row["cnt"] for row in connection.execute(counts_sql, params).mappings()}

    event_type_counts = {t: int(counts.get(t, 0)) for t in AUDIT_EVENT_TYPES}
    return AuditLogPageResponse(
        items=items,
        total=sum(event_type_counts.values()),
        page=page,
        size=size,
        event_types=list(AUDIT_EVENT_TYPES),
        event_type_counts=event_type_counts,
    )
