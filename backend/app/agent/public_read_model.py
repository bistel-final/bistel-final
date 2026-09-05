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

from app.agent.diagnostics import (
    CANONICAL_INCIDENT_KEYS,
    DiagnosisBlock,
    EvidenceAssessmentBlock,
    ImpactScopeBlock,
    IncidentDiagnosticSnapshot,
    PostActionObservationBlock,
    SimilarIncidentItem,
    SimilarIncidentsBlock,
    abnormal_parameter_ids,
    parameter_jaccard,
    similar_incident_score,
)
from app.agent.evidence_projection import (
    project_document_evidence,
    project_fdc_evidence,
)
from app.agent.public_schemas import (
    ActionDeliveryDetailItem,
    ActionDeliveryItem,
    ActionDetailResponse,
    ActionItem,
    AgentPredictionDetailItem,
    AgentRunActionItem,
    AgentRunApprovalItem,
    AgentRunDetailResponse,
    GraphAskEvidence,
    PublicAgentRunItem,
    PublicApprovalItem,
    PublicDeliveryItem,
    PublicToolCallItem,
    ReactStepPublic,
    RunAlarmEvidence,
    RunEvidenceItem,
)
from app.agent.react import ReactStep
from app.agent.repository import (
    PublicActionRecord,
    PublicAgentRunRecord,
    PublicApprovalRecord,
    PublicToolCallRecord,
    RepositoryContractError,
    get_action_public,
    get_agent_run_public,
    get_approval_public,
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
_V2_PROMPTS: Final = frozenset(
    {
        "agent-hypothesis-v2",
        "agent-hypothesis-v2-ko",
        "agent-hypothesis-v2-ko1",
        "agent-hypothesis-v3-ko1",
    }
)

# 결과 본문·에러 문자열은 요약에 사용하지 않는다. 이름도 Runtime Agent가 호출할 수 있는
# 4종만 허용해 임의 tool_name이 화면으로 흘러가는 것을 막는다.
_SUCCESS_SUMMARIES: Final = MappingProxyType(
    {
        "get_fdc_summary": "FDC summary loaded",
        "get_equipment_context": "Equipment context loaded",
        "search_documents": "Document search completed",
        "send_action": "Action delivery processed",
        "get_chamber_parameter_history": "Chamber parameter history loaded",
        "get_metrology_result": "Metrology result loaded",
    }
)
_ERROR_SUMMARIES: Final = MappingProxyType(
    {
        "get_fdc_summary": "FDC summary unavailable",
        "get_equipment_context": "Equipment context unavailable",
        "search_documents": "Document search unavailable",
        "send_action": "Action delivery unavailable",
        "get_chamber_parameter_history": "Chamber parameter history unavailable",
        "get_metrology_result": "Metrology result unavailable",
    }
)
_TIMEOUT_SUMMARIES: Final = MappingProxyType(
    {
        "get_fdc_summary": "FDC summary timed out",
        "get_equipment_context": "Equipment context timed out",
        "search_documents": "Document search timed out",
        "send_action": "Action delivery timed out",
        "get_chamber_parameter_history": "Chamber parameter history timed out",
        "get_metrology_result": "Metrology result timed out",
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
        created_at=record.created_at,
    )


def _public_action_detail(record: PublicActionRecord) -> ActionDetailResponse:
    item = _public_action(record)
    return ActionDetailResponse(
        **item.model_dump(exclude={"deliveries"}),
        deliveries=[
            ActionDeliveryDetailItem(
                channel=to_public_channel(delivery.channel),
                status=delivery.status,
                started_at=delivery.started_at,
                completed_at=delivery.completed_at,
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
    diagnostic_snapshot: IncidentDiagnosticSnapshot | None,
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
    # Level 2의 정상 실행은 load_incident에서 이미 route graph를 읽고, route가
    # 일관되면 중복 get_equipment_context Tool 호출을 생략한다. 그 경우에도 v2
    # diagnostic snapshot에는 당시 relation 집합과 graph revision이 함께 고정된다.
    # snapshot에 실제 포함된 relation만 같은 revision의 공개 근거로 복원한다.
    if graph_revision is None and diagnostic_snapshot is not None:
        source_ids = diagnostic_snapshot.source_ids
        cited_relations = set(supporting_relation_ids)
        if (
            cited_relations <= set(source_ids.relation_ids)
            and len(source_ids.graph_revisions) == 1
        ):
            graph_revision = source_ids.graph_revisions[0]

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
    evidence: dict[str, object] | None,
) -> tuple[tuple[AlarmRef, ...], tuple[str, ...]]:
    if evidence is None:
        return (), ()
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


def _diagnostic_snapshot(
    evidence: dict[str, object] | None,
) -> IncidentDiagnosticSnapshot | None:
    if evidence is None or evidence.get("schema_version") not in {
        "agent-evidence-v2",
        "agent-evidence-v3",
    }:
        return None
    raw = evidence.get("diagnostic_snapshot")
    if raw is None:
        raise RepositoryContractError("PUBLIC_DIAGNOSTIC_SNAPSHOT_INVALID")
    try:
        return IncidentDiagnosticSnapshot.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryContractError("PUBLIC_DIAGNOSTIC_SNAPSHOT_INVALID") from exc


def _public_trace(record: PublicAgentRunRecord) -> dict[str, object]:
    if record.autonomy_level != 3:
        return {"trace_state": "NOT_APPLICABLE", "react_trace": []}
    if record.status in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
        return {"trace_state": "PENDING", "react_trace": []}
    raw = (record.run_evidence or {}).get("react_trace")
    if raw is None:
        return {"trace_state": "UNAVAILABLE", "react_trace": []}
    try:
        if not isinstance(raw, list) or len(raw) > 11:
            raise ValueError("trace bound")
        steps = [ReactStep.model_validate(item) for item in raw]
        if [step.seq for step in steps] != list(range(1, len(steps) + 1)):
            raise ValueError("trace sequence")
        public = [
            ReactStepPublic.model_validate(
                step.model_dump(exclude={"argument_digest", "llm_model"})
            )
            for step in steps
        ]
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryContractError("PUBLIC_REACT_TRACE_INVALID") from exc
    return {"trace_state": "AVAILABLE", "react_trace": public}


def _diagnosis_block(
    record: PublicAgentRunRecord,
    snapshot: IncidentDiagnosticSnapshot | None,
) -> DiagnosisBlock:
    raw = record.prediction_evidence
    if snapshot is None or raw is None or record.predicted_fault_code is None:
        return DiagnosisBlock(
            status="EMPTY",
            reason_code="V2_DIAGNOSIS_NOT_AVAILABLE",
            predicted_fault_code=None,
            confidence=None,
            observations=(),
            evidence_synthesis=None,
            cause_summary=None,
            alternative_hypotheses=(),
            verification_steps=(),
            limitations=("이 실행에는 v2 종합 진단 snapshot이 없습니다.",),
            diagnostic_coverage=None,
        )
    try:
        return DiagnosisBlock(
            status="AVAILABLE",
            reason_code=None,
            predicted_fault_code=record.predicted_fault_code,
            confidence=record.confidence,
            observations=tuple(raw.get("observations", ())),
            evidence_synthesis=raw.get("evidence_synthesis", ""),
            cause_summary=record.prediction_cause_summary,
            alternative_hypotheses=tuple(raw.get("alternative_hypotheses", ())),
            verification_steps=tuple(raw.get("verification_steps", ())),
            limitations=tuple(raw.get("limitations", ())),
            parameter_findings=tuple(raw.get("parameter_findings", ())),
            origin_assessment=raw.get("origin_assessment"),
            diagnostic_coverage=(
                f"상세 진단 {snapshot.observed_wafer_count} / "
                f"대상 WAFER {snapshot.target_wafer_count} · "
                f"incident 연결 Alarm {snapshot.member_alarm_count}"
            ),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryContractError("PUBLIC_DIAGNOSIS_INVALID") from exc


def _assessment_block(
    evidence: dict[str, object] | None,
    *,
    legacy_missing_sources: tuple[str, ...] = (),
    available_sources: tuple[str, ...] = (),
) -> EvidenceAssessmentBlock:
    raw = None if evidence is None else evidence.get("evidence_assessment")
    if raw is None:
        if legacy_missing_sources:
            return EvidenceAssessmentBlock(
                status="PARTIAL",
                reason_codes=("LEGACY_CITATION_PROVENANCE_NOT_AVAILABLE",),
                available_sources=available_sources,
                missing_sources=legacy_missing_sources,
                conflicting_source_ids=(),
            )
        return EvidenceAssessmentBlock(
            status="EMPTY",
            reason_codes=("V2_EVIDENCE_ASSESSMENT_NOT_AVAILABLE",),
            available_sources=(),
            missing_sources=(),
            conflicting_source_ids=(),
        )
    try:
        return EvidenceAssessmentBlock.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryContractError("PUBLIC_EVIDENCE_ASSESSMENT_INVALID") from exc


def _impact_block(evidence: dict[str, object] | None) -> ImpactScopeBlock:
    raw = None if evidence is None else evidence.get("impact_scope")
    if raw is None:
        return ImpactScopeBlock(
            status="EMPTY",
            reason_code="V2_IMPACT_SCOPE_NOT_AVAILABLE",
            direct=(),
            check_required=(),
            summary=None,
            graph_conflict=False,
        )
    try:
        return ImpactScopeBlock.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryContractError("PUBLIC_IMPACT_SCOPE_INVALID") from exc


def _similar_incidents(
    connection: Connection,
    current: PublicAgentRunRecord,
    snapshot: IncidentDiagnosticSnapshot | None,
) -> SimilarIncidentsBlock:
    if snapshot is None:
        return SimilarIncidentsBlock(
            status="EMPTY",
            reason_code="NOT_ENOUGH_RUNTIME_HISTORY",
            items=(),
        )
    current_key = (snapshot.lot_id, snapshot.chamber_id)
    if current_key not in CANONICAL_INCIDENT_KEYS:
        return SimilarIncidentsBlock(
            status="EMPTY",
            reason_code="NOT_ENOUGH_RUNTIME_HISTORY",
            items=(),
        )
    current_parameters = abnormal_parameter_ids(snapshot)
    current_models = frozenset(snapshot.direct_scope.model_codes)
    scored: list[SimilarIncidentItem] = []
    candidates = sorted(
        list_agent_runs_public(
            connection,
            date_from=None,
            date_to=None,
            status=RunStatus.COMPLETED,
        ),
        key=lambda item: (item.created_at, item.agent_run_id),
    )
    seen_incidents: set[tuple[str, str]] = set()
    for candidate in candidates:
        if (
            candidate.agent_run_id == current.agent_run_id
            or candidate.retry_of_run_id is not None
            or candidate.prompt_version not in _V2_PROMPTS
            or candidate.predicted_fault_code is None
            or candidate.recommended_action is None
            or candidate.lot_id is None
            or current.lot_id is None
            or (candidate.lot_id, candidate.chamber_id)
            == (current.lot_id, current.chamber_id)
        ):
            continue
        candidate_snapshot = _diagnostic_snapshot(candidate.prediction_evidence)
        if candidate_snapshot is None:
            continue
        incident_key = (candidate.lot_id, candidate.chamber_id)
        if incident_key not in CANONICAL_INCIDENT_KEYS:
            continue
        if incident_key in seen_incidents:
            continue
        seen_incidents.add(incident_key)
        similarity = parameter_jaccard(
            current_parameters,
            abnormal_parameter_ids(candidate_snapshot),
        )
        scored.append(
            SimilarIncidentItem(
                agent_run_id=candidate.agent_run_id,
                lot_id=candidate.lot_id,
                chamber_id=candidate.chamber_id,
                score=similar_incident_score(
                    same_model=(
                        current_models
                        == frozenset(candidate_snapshot.direct_scope.model_codes)
                    ),
                    parameter_similarity=similarity,
                    same_fault=(
                        current.predicted_fault_code is candidate.predicted_fault_code
                    ),
                    same_action=(
                        current.recommended_action is candidate.recommended_action
                    ),
                ),
                parameter_jaccard=similarity,
                predicted_fault_code=candidate.predicted_fault_code,
                recommended_action=candidate.recommended_action,
            )
        )
    scored.sort(
        key=lambda item: (
            -item.score,
            item.lot_id,
            item.chamber_id,
            item.agent_run_id,
        )
    )
    items = tuple(scored[:3])
    return SimilarIncidentsBlock(
        status="AVAILABLE" if items else "EMPTY",
        reason_code=None if items else "NOT_ENOUGH_RUNTIME_HISTORY",
        items=items,
    )


def load_public_agent_run_detail(
    connection: Connection,
    agent_run_id: str,
) -> AgentRunDetailResponse:
    record = get_agent_run_public(connection, agent_run_id)
    item = _public_run(record)
    snapshot = _diagnostic_snapshot(record.prediction_evidence)
    cited_alarms, relation_ids = _prediction_citations(record.prediction_evidence)
    evidence: list[RunEvidenceItem] = [
        _alarm_evidence(alarm)
        for alarm in (*list_run_alarms(connection, agent_run_id), *cited_alarms)
    ]
    evidence.extend(
        _stored_tool_evidence(
            connection,
            agent_run_id,
            supporting_relation_ids=relation_ids,
            diagnostic_snapshot=snapshot,
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
                ActionDeliveryDetailItem(
                    channel=to_public_channel(delivery.channel),
                    status=delivery.status,
                    started_at=delivery.started_at,
                    completed_at=delivery.completed_at,
                )
                for delivery in action_detail.deliveries
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
            decided_at=approval_record.decided_at,
            decision_comment=approval_record.decision_comment,
        )
    )
    prediction = None
    legacy_missing_sources: set[str] = set()
    if record.predicted_fault_code is not None:
        raw = record.prediction_evidence
        if (
            raw is None
            or raw.get("schema_version")
            not in {"agent-evidence-v1", "agent-evidence-v2", "agent-evidence-v3"}
            or record.prediction_cause_summary is None
            or record.prediction_llm_model is None
            or record.prediction_prompt_version is None
            or record.prompt_version is None
            or record.prediction_llm_model != record.llm_model
            or record.prediction_prompt_version != record.prompt_version
            or record.prediction_created_at is None
            or record.input_tokens is None
            or record.output_tokens is None
        ):
            raise RepositoryContractError("PUBLIC_PREDICTION_PROVENANCE_INVALID")
        citation_fields = (
            "supporting_alarms",
            "supporting_chunk_ids",
            "supporting_relation_ids",
        )
        if any(not isinstance(raw.get(field, []), list) for field in citation_fields):
            raise RepositoryContractError("PUBLIC_PREDICTION_EVIDENCE_INVALID")
        try:
            prediction_alarms = [
                AlarmRef.model_validate(value)
                for value in raw.get("supporting_alarms", ())
            ]
            prediction_chunks = list(raw.get("supporting_chunk_ids", ()))
            prediction_relations = list(raw.get("supporting_relation_ids", ()))
            # v1은 Graph citation을 저장했어도 당시 graph revision이나 Graph Tool
            # 결과를 남기지 않은 실행이 있다. 원본을 현재 Graph와 섞어 소급 검증하지
            # 않고, 공개 projection에서 실제 저장 근거로 검증 가능한 citation만
            # 노출한다. v2는 immutable snapshot이 있으므로 기존 fail-closed를 유지한다.
            if raw.get("schema_version") == "agent-evidence-v1":
                public_source_ids = {item.source_id for item in deduplicated}
                if any(
                    alarm.to_token() not in public_source_ids
                    for alarm in prediction_alarms
                ):
                    legacy_missing_sources.add("POSTGRES_ROUTE")
                if any(
                    chunk_id not in public_source_ids for chunk_id in prediction_chunks
                ):
                    legacy_missing_sources.add("RAG")
                if any(
                    relation_id not in public_source_ids
                    for relation_id in prediction_relations
                ):
                    legacy_missing_sources.add("GRAPH")
                prediction_alarms = [
                    alarm
                    for alarm in prediction_alarms
                    if alarm.to_token() in public_source_ids
                ]
                prediction_chunks = [
                    chunk_id
                    for chunk_id in prediction_chunks
                    if chunk_id in public_source_ids
                ]
                prediction_relations = [
                    relation_id
                    for relation_id in prediction_relations
                    if relation_id in public_source_ids
                ]
            prediction = AgentPredictionDetailItem(
                predicted_fault_code=record.predicted_fault_code,
                confidence=record.confidence,
                cause_summary=record.prediction_cause_summary,
                supporting_alarms=prediction_alarms,
                supporting_chunk_ids=prediction_chunks,
                supporting_relation_ids=prediction_relations,
                uncertainty=raw.get("uncertainty", ""),
                llm_model=record.prediction_llm_model,
                prompt_version=record.prediction_prompt_version,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                generated_at=record.prediction_created_at,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise RepositoryContractError("PUBLIC_PREDICTION_DTO_INVALID") from exc
    try:
        return AgentRunDetailResponse(
            **item.model_dump(),
            evidence_items=deduplicated,
            prediction=prediction,
            approval=approval,
            action=action,
            diagnosis=_diagnosis_block(record, snapshot),
            evidence_assessment=_assessment_block(
                record.prediction_evidence,
                legacy_missing_sources=tuple(
                    source
                    for source in ("FDC", "POSTGRES_ROUTE", "GRAPH", "RAG")
                    if source in legacy_missing_sources
                ),
                available_sources=tuple(
                    source
                    for source, evidence_type in (
                        ("FDC", "TRACE"),
                        ("POSTGRES_ROUTE", "ALARM"),
                        ("GRAPH", "GRAPH"),
                        ("RAG", "DOCUMENT"),
                    )
                    if any(item.type == evidence_type for item in deduplicated)
                ),
            ),
            impact_scope=_impact_block(record.prediction_evidence),
            similar_incidents=_similar_incidents(connection, record, snapshot),
            post_action_observation=PostActionObservationBlock(),
            autonomy_level=record.autonomy_level,
            remaining_read_calls=max(
                0,
                (8 if record.autonomy_level == 3 else 6)
                - sum(1 for tool in record.tools if tool.tool_name != "send_action"),
            ),
            **_public_trace(record),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryContractError("PUBLIC_AGENT_RUN_DETAIL_INVALID") from exc


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
