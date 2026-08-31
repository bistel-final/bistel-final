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

from pydantic import ValidationError
from sqlalchemy.engine import Connection

from app.agent.evidence_projection import (
    project_document_evidence,
    project_fdc_evidence,
)
from app.agent.public_schemas import (
    ActionDeliveryDetailItem,
    ActionDeliveryItem,
    ActionDetailResponse,
    ActionItem,
    AgentRunActionItem,
    AgentRunApprovalItem,
    AgentRunDetailResponse,
    GraphAskEvidence,
    PublicAgentRunItem,
    PublicApprovalItem,
    PublicDeliveryItem,
    PublicToolCallItem,
    RunAlarmEvidence,
    RunEvidenceItem,
)
from app.agent.repository import (
    PublicActionRecord,
    PublicAgentRunRecord,
    PublicApprovalRecord,
    PublicToolCallRecord,
    RepositoryContractError,
    get_action_public,
    get_agent_run_public,
    get_approval_public,
    get_prediction_or_none,
    list_actions_public,
    list_agent_runs_public,
    list_approvals_public,
    list_run_alarms,
    list_tool_calls,
    record_public_read_omission,
)
from app.common.boundary_adapters import (
    to_public_approval_status,
    to_public_channel,
)
from app.common.enums import ActionCode, FaultHypothesis, RunStatus, ToolCallStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

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


def _public_datetime(value: datetime | None) -> datetime | None:
    """Serialize public Agent timestamps as Asia/Seoul.

    Final-data/legacy naive timestamps are already KST wall time. Runtime aware
    timestamps are instants and are converted to KST.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_KST)
    return value.astimezone(_KST)


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
        created_at=_public_datetime(record.created_at),
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
    status: RunStatus | None = None,
    predicted_fault_code: FaultHypothesis | None = None,
) -> list[PublicAgentRunItem]:
    lower, upper = public_run_date_bounds(date_from, date_to)
    records = list_agent_runs_public(
        connection,
        date_from=lower,
        date_to=upper,
        status=status,
        predicted_fault_code=predicted_fault_code,
    )
    items: list[PublicAgentRunItem] = []
    for record in records:
        try:
            items.append(_public_run(record))
        except RepositoryContractError as exc:
            record_public_read_omission("agent_run_projection", exc.code)
        except ValidationError:
            record_public_read_omission(
                "agent_run_projection",
                "PUBLIC_RUN_DTO_INVALID",
            )
    return items


def to_public_approval(record: PublicApprovalRecord) -> PublicApprovalItem:
    status = to_public_approval_status(record.status)
    return PublicApprovalItem(
        approval_id=record.approval_id,
        agent_run_id=record.agent_run_id,
        action_id=record.action_id,
        created_at=_public_datetime(record.created_at),
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
        decided_at=_public_datetime(record.decided_at),
        decision_comment=record.decision_comment,
        # REJECTED도 화면 alias는 canonical decision actor/time을 복사한다.
        approved_by=record.decided_by,
        approved_at=_public_datetime(record.decided_at),
    )


# 기존 묶음 1 단위 회귀와 module-private 호환. 공개 composition은 위 이름만 쓴다.
_public_approval = to_public_approval


def list_public_approvals(connection: Connection) -> list[PublicApprovalItem]:
    items: list[PublicApprovalItem] = []
    for record in list_approvals_public(connection):
        try:
            items.append(to_public_approval(record))
        except ValidationError:
            record_public_read_omission(
                "approval_projection",
                "PUBLIC_APPROVAL_DTO_INVALID",
            )
    return items


def _action_delivery(record: object) -> ActionDeliveryItem:
    return ActionDeliveryItem(
        channel=to_public_channel(record.channel),
        status=record.status,
    )


def _public_action(record: PublicActionRecord) -> ActionItem:
    status = (
        None
        if record.approval_status is None
        else to_public_approval_status(record.approval_status)
    )
    return ActionItem(
        action_id=record.action_id,
        agent_run_id=record.agent_run_id,
        created_by_agent_run_id=record.agent_run_id,
        action_code=record.action_code,
        lot_id=record.lot_id,
        lot=record.lot_id,
        equipment_id=record.equipment_id,
        equipment=record.equipment_id,
        chamber_id=record.chamber_id,
        chamber=record.chamber_id,
        reason=record.reason,
        approval_status=status,
        deliveries=[_action_delivery(delivery) for delivery in record.deliveries],
        created_at=_public_datetime(record.created_at),
    )


def _public_action_detail(record: PublicActionRecord) -> ActionDetailResponse:
    item = _public_action(record)
    return ActionDetailResponse(
        **item.model_dump(exclude={"deliveries"}),
        deliveries=[
            ActionDeliveryDetailItem(
                channel=to_public_channel(delivery.channel),
                status=delivery.status,
                started_at=_public_datetime(delivery.started_at),
                completed_at=_public_datetime(delivery.completed_at),
            )
            for delivery in record.deliveries
        ],
    )


def list_public_actions(
    connection: Connection,
    *,
    action_code: ActionCode | None,
) -> list[ActionItem]:
    return [
        _public_action(record)
        for record in list_actions_public(connection, action_code=action_code)
    ]


def load_public_action_detail(
    connection: Connection,
    action_id: str,
) -> ActionDetailResponse:
    return _public_action_detail(get_action_public(connection, action_id))


def _alarm_evidence(alarm: AlarmRef) -> RunAlarmEvidence:
    return RunAlarmEvidence(
        type="ALARM",
        source_id=alarm.to_token(),
        title=f"{alarm.source.value} alarm {alarm.alarm_id}",
        excerpt=(f"source={alarm.source.value}; alarm_id={alarm.alarm_id}"),
        alarm=alarm,
    )


def _stored_tool_evidence(
    connection: Connection,
    agent_run_id: str,
    *,
    supporting_relation_ids: tuple[str, ...],
) -> list[RunEvidenceItem]:
    items: list[RunEvidenceItem] = []
    graph_revision: str | None = None
    for call in list_tool_calls(connection, agent_run_id):
        if call.status is not ToolCallStatus.SUCCESS or call.output is None:
            continue
        try:
            if call.tool_name == "get_fdc_summary":
                projected = project_fdc_evidence(
                    FdcSummaryToolResult.model_validate(call.output)
                )
                if projected is not None:
                    items.append(projected)
            elif call.tool_name == "search_documents":
                items.extend(
                    project_document_evidence(
                        DocumentSearchToolResult.model_validate(call.output)
                    )
                )
            elif call.tool_name == "get_equipment_context":
                result = EquipmentContextToolResult.model_validate(call.output)
                if result.ok and result.graph_revision is not None:
                    graph_revision = result.graph_revision
        except (ValidationError, TypeError, ValueError):
            # 부분·구버전 payload는 해당 evidence만 생략한다.
            # 원문 오류는 공개하지 않는다.
            continue
    if graph_revision is not None:
        for relation_id in supporting_relation_ids:
            try:
                items.append(
                    GraphAskEvidence(
                        type="GRAPH",
                        source_id=relation_id,
                        title=f"Graph relation {relation_id}",
                        excerpt=(f"relation={relation_id}; revision={graph_revision}"),
                        relation_id=relation_id,
                        graph_revision=graph_revision,
                    )
                )
            except (ValidationError, TypeError, ValueError):
                # citation 하나가 손상되어도 검증 가능한 다른 근거는 유지한다.
                continue
    return items


def _prediction_citations(
    connection: Connection, agent_run_id: str
) -> tuple[tuple[AlarmRef, ...], tuple[str, ...]]:
    prediction = get_prediction_or_none(connection, agent_run_id)
    if prediction is None:
        return (), ()
    evidence = prediction.evidence
    alarms: list[AlarmRef] = []
    raw_alarms = evidence.get("supporting_alarms", ())
    if isinstance(raw_alarms, list | tuple):
        for item in raw_alarms:
            try:
                alarms.append(AlarmRef.model_validate(item))
            except (ValidationError, TypeError, ValueError):
                continue

    relations: list[str] = []
    raw_relations = evidence.get("supporting_relation_ids", ())
    if isinstance(raw_relations, list | tuple):
        for item in raw_relations:
            if isinstance(item, str) and item.strip():
                relations.append(item)
    return tuple(alarms), tuple(relations)


def load_public_agent_run_detail(
    connection: Connection,
    agent_run_id: str,
) -> AgentRunDetailResponse:
    record = get_agent_run_public(connection, agent_run_id)
    item = _public_run(record)
    cited_alarms, relation_ids = _prediction_citations(connection, agent_run_id)
    evidence: list[RunEvidenceItem] = [
        _alarm_evidence(alarm)
        for alarm in (*list_run_alarms(connection, agent_run_id), *cited_alarms)
    ]
    evidence.extend(
        _stored_tool_evidence(
            connection,
            agent_run_id,
            supporting_relation_ids=relation_ids,
        )
    )
    deduplicated: list[RunEvidenceItem] = []
    seen: set[str] = set()
    for evidence_item in evidence:
        if evidence_item.source_id in seen:
            continue
        seen.add(evidence_item.source_id)
        deduplicated.append(evidence_item)

    action_detail = (
        None
        if record.action_id is None
        else get_action_public(connection, record.action_id)
    )
    action = (
        None
        if action_detail is None
        else AgentRunActionItem(
            action_id=action_detail.action_id,
            agent_run_id=action_detail.agent_run_id,
            action_code=action_detail.action_code,
            reason=action_detail.reason,
            approval_status=(
                None
                if action_detail.approval_status is None
                else to_public_approval_status(action_detail.approval_status)
            ),
            deliveries=[
                _action_delivery(delivery) for delivery in action_detail.deliveries
            ],
        )
    )
    approval_record = (
        None
        if record.approval_id is None
        else get_approval_public(connection, record.approval_id)
    )
    approval = (
        None
        if approval_record is None
        else AgentRunApprovalItem(
            approval_id=approval_record.approval_id,
            action_id=approval_record.action_id,
            agent_run_id=approval_record.agent_run_id,
            status=to_public_approval_status(approval_record.status),
            decided_by=approval_record.decided_by,
            decided_at=_public_datetime(approval_record.decided_at),
            decision_comment=approval_record.decision_comment,
        )
    )
    return AgentRunDetailResponse(
        **item.model_dump(),
        evidence_items=deduplicated,
        approval=approval,
        action=action,
    )


__all__ = [
    "PublicDateRangeError",
    "list_public_agent_runs",
    "list_public_actions",
    "list_public_approvals",
    "load_public_action_detail",
    "load_public_agent_run_detail",
    "public_run_date_bounds",
    "to_public_approval",
]
