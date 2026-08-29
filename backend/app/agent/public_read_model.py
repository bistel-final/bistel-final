"""API v3 Agent 목록 projection 조립.

Repository record와 공개 DTO 사이의 유일한 serializer다. 호환 alias는 모두 canonical
값에서만 만들고, Tool 요약은 저장된 raw output/error를 읽지 않는 고정 allowlist로
만든다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Connection

from app.agent.public_schemas import (
    PublicAgentRunItem,
    PublicApprovalItem,
    PublicDeliveryItem,
    PublicToolCallItem,
)
from app.agent.repository import (
    PublicAgentRunRecord,
    PublicApprovalRecord,
    PublicToolCallRecord,
    RepositoryContractError,
    list_agent_runs_public,
    list_approvals_public,
)
from app.common.boundary_adapters import (
    to_public_approval_status,
    to_public_channel,
)
from app.common.enums import ToolCallStatus

_KST: Final = ZoneInfo("Asia/Seoul")

# 결과 본문·에러 문자열은 요약에 사용하지 않는다. 이름도 Runtime Agent가 호출할 수 있는
# 4종만 허용해 임의 tool_name이 화면으로 흘러가는 것을 막는다.
_SUCCESS_SUMMARIES: Final = MappingProxyType(
    {
        "get_fdc_summary": "FDC summary loaded",
        "get_equipment_context": "Equipment context loaded",
        "search_documents": "Document search completed",
        "send_action": "Action delivery processed",
    }
)
_ERROR_SUMMARIES: Final = MappingProxyType(
    {
        "get_fdc_summary": "FDC summary unavailable",
        "get_equipment_context": "Equipment context unavailable",
        "search_documents": "Document search unavailable",
        "send_action": "Action delivery unavailable",
    }
)
_TIMEOUT_SUMMARIES: Final = MappingProxyType(
    {
        "get_fdc_summary": "FDC summary timed out",
        "get_equipment_context": "Equipment context timed out",
        "search_documents": "Document search timed out",
        "send_action": "Action delivery timed out",
    }
)


class PublicDateRangeError(ValueError):
    """date query pair 또는 순서가 API v3 계약과 다르다."""


def public_run_date_bounds(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime | None, datetime | None]:
    """KST 포함 일자를 PostgreSQL half-open 경계로 바꾼다."""

    if (date_from is None) != (date_to is None):
        raise PublicDateRangeError("DATE_RANGE_PAIR_REQUIRED")
    if date_from is None or date_to is None:
        return None, None
    if date_from > date_to:
        raise PublicDateRangeError("DATE_RANGE_ORDER_INVALID")
    try:
        exclusive_to = date_to + timedelta(days=1)
    except OverflowError as exc:
        raise PublicDateRangeError("DATE_RANGE_OVERFLOW") from exc
    return (
        datetime.combine(date_from, time.min, _KST),
        datetime.combine(exclusive_to, time.min, _KST),
    )


def _tool_summary(record: PublicToolCallRecord) -> str:
    summaries = {
        ToolCallStatus.SUCCESS: _SUCCESS_SUMMARIES,
        ToolCallStatus.ERROR: _ERROR_SUMMARIES,
        ToolCallStatus.TIMEOUT: _TIMEOUT_SUMMARIES,
    }[record.status]
    try:
        return summaries[record.tool_name]
    except KeyError as exc:
        raise RepositoryContractError("PUBLIC_TOOL_NAME_INVALID") from exc


def _public_tool(record: PublicToolCallRecord) -> PublicToolCallItem:
    return PublicToolCallItem(
        tool_name=record.tool_name,
        status=record.status,
        result_summary=_tool_summary(record),
        n=record.tool_name,
        s=record.status,
    )


def _public_run(record: PublicAgentRunRecord) -> PublicAgentRunItem:
    predicted = record.predicted_fault_code
    return PublicAgentRunItem(
        agent_run_id=record.agent_run_id,
        created_at=record.created_at,
        alarm_source=record.requested_alarm.source,
        alarm_id=record.requested_alarm.alarm_id,
        chamber_id=record.chamber_id,
        chamber=record.chamber_id,
        predicted_fault_code=predicted,
        fault_code=predicted,
        fault_name=None,
        fault_color=None,
        confidence=record.confidence,
        recommended_action=record.recommended_action,
        status=record.status,
        action_id=record.action_id,
        approval_id=record.approval_id,
        tools=[_public_tool(tool) for tool in record.tools],
        deliveries=[
            PublicDeliveryItem(
                channel=to_public_channel(delivery.channel),
                status=delivery.status,
            )
            for delivery in record.deliveries
        ],
        latency_ms=record.latency_ms,
        llm_model=record.llm_model,
    )


def list_public_agent_runs(
    connection: Connection,
    *,
    date_from: date | None,
    date_to: date | None,
) -> list[PublicAgentRunItem]:
    lower, upper = public_run_date_bounds(date_from, date_to)
    records = list_agent_runs_public(
        connection,
        date_from=lower,
        date_to=upper,
    )
    return [_public_run(record) for record in records]


def to_public_approval(record: PublicApprovalRecord) -> PublicApprovalItem:
    status = to_public_approval_status(record.status)
    return PublicApprovalItem(
        approval_id=record.approval_id,
        agent_run_id=record.agent_run_id,
        action_id=record.action_id,
        created_at=record.created_at,
        lot_id=record.lot_id,
        lot=record.lot_id,
        equipment_id=record.equipment_id,
        equipment=record.equipment_id,
        chamber_id=record.chamber_id,
        chamber=record.chamber_id,
        predicted_fault_code=record.predicted_fault_code,
        fault_code=record.predicted_fault_code,
        action_code=record.action_code,
        reason=record.reason,
        status=status,
        decided_by=record.decided_by,
        decided_at=record.decided_at,
        decision_comment=record.decision_comment,
        # REJECTED도 화면 alias는 canonical decision actor/time을 복사한다.
        approved_by=record.decided_by,
        approved_at=record.decided_at,
    )


# 기존 묶음 1 단위 회귀와 module-private 호환. 공개 composition은 위 이름만 쓴다.
_public_approval = to_public_approval


def list_public_approvals(connection: Connection) -> list[PublicApprovalItem]:
    return [to_public_approval(record) for record in list_approvals_public(connection)]


__all__ = [
    "PublicDateRangeError",
    "list_public_agent_runs",
    "list_public_approvals",
    "public_run_date_bounds",
    "to_public_approval",
]
