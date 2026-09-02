"""원인 가설 prompt 계약 (`V5-C-2.3`).

동적 근거를 결정론적 JSON으로 조립하고, 최종 messages 전체를
데이터셋 정답 label 노출 패턴으로 검사한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Final

from app.agent.diagnostics import (
    EvidenceAssessmentBlock,
    ImpactScopeBlock,
    IncidentDiagnosticSnapshot,
    WaferParameterObservation,
)
from app.agent.routing import ResolvedIncidentRoute
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

PROMPT_VERSION: Final = "agent-hypothesis-v2-ko1"
MAX_PROMPT_CHARS: Final = 12_000
MAX_DOCUMENT_EXCERPT_CHARS: Final = 500
MAX_PROMPT_MEMBER_ALARMS: Final = 12
MAX_PROMPT_WAFER_OBSERVATIONS: Final = 6
TRUNCATION_MARKER: Final = "…[truncated]"

# `_`는 regex word 문자이므로 predicted_fault_code 안의 부분 문자열은
# 매칭되지 않고, 독립 token만 막힌다.
_BLOCKED_TOKENS: Final[tuple[str, ...]] = (
    "fault_code",
    "FAULTCODE",
    "FAULTS",
    "is_fault",
    "fault_of",
    "faulty_lots",
    "NRM",
)
_BLOCKED_PATTERN: Final = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(value) for value in _BLOCKED_TOKENS)
    + r")(?!\w)",
    re.IGNORECASE,
)


class HypothesisPromptError(ValueError):
    """prompt 생성·검사 계약 위반. 동적 원문을 메시지에 담지 않는다."""

    _CODES: Final[frozenset[str]] = frozenset(
        {"HYPOTHESIS_PROMPT_BLOCKED", "HYPOTHESIS_PROMPT_TOO_LARGE"}
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "HYPOTHESIS_PROMPT_BLOCKED"
        super().__init__(code)
        self.code = code


def _excerpt(content: str) -> str:
    if len(content) <= MAX_DOCUMENT_EXCERPT_CHARS:
        return content
    return content[:MAX_DOCUMENT_EXCERPT_CHARS] + TRUNCATION_MARKER


def _prompt_member_alarms(route: ResolvedIncidentRoute) -> list[AlarmRef]:
    """LLM 인용 후보를 결정론적으로 제한하되 핵심 incident identity는 보존한다.

    Runtime incident는 같은 LOT·chamber의 TRACE·SUMMARY 전체를 포함할 수 있어 최종
    데이터의 R03 사례에서는 49건까지 커진다. 전체 목록은 DB·State·조치 규칙에 그대로
    유지하고, prompt에는 요청·대표·source 양끝과 원래 순서 표본만 싣는다.
    """

    members = tuple(route.incident.member_alarms)
    selected: list[AlarmRef] = []
    seen: set[str] = set()

    def add(alarm: AlarmRef) -> None:
        token = alarm.to_token()
        if token in seen or len(selected) >= MAX_PROMPT_MEMBER_ALARMS:
            return
        seen.add(token)
        selected.append(alarm)

    add(route.incident.requested_alarm)
    add(route.incident.representative_alarm)
    for source in ("R03", "TRACE", "SUMMARY"):
        candidates = [alarm for alarm in members if alarm.source.value == source]
        if candidates:
            add(candidates[0])
            add(candidates[-1])
    for alarm in members:
        add(alarm)
    return selected


def _route_payload(route: ResolvedIncidentRoute) -> dict[str, Any]:
    member_alarms = tuple(route.incident.member_alarms)
    source_counts = {
        source: sum(alarm.source.value == source for alarm in member_alarms)
        for source in ("TRACE", "SUMMARY", "R03")
    }
    prompt_members = _prompt_member_alarms(route)
    return {
        "incident": {
            "lot_id": route.incident.lot_id,
            "chamber_id": route.incident.chamber_id,
            "requested_alarm": route.incident.requested_alarm.model_dump(mode="json"),
            "representative_alarm": route.incident.representative_alarm.model_dump(
                mode="json"
            ),
            "member_alarms": [
                alarm.model_dump(mode="json") for alarm in prompt_members
            ],
            "member_alarm_count": len(member_alarms),
            "member_alarm_source_counts": source_counts,
            "member_alarms_omitted_count": len(member_alarms) - len(prompt_members),
        },
        "route_consistency": route.route_consistency,
        "graph_evidence": [
            {
                "chamber_id": item.chamber_id,
                "equipment_id": item.equipment_id,
                "model_code": item.model_code,
                "process_step_id": item.process_step_id,
                "upstream_process_step_ids": list(item.upstream_process_step_ids),
                "downstream_process_step_ids": list(item.downstream_process_step_ids),
                "relation_ids": list(item.relation_ids),
                "graph_revision": item.graph_revision,
            }
            for item in route.graph_evidence
        ],
        "mismatches": [
            {
                "code": item.code,
                "wafer_id": item.wafer_id,
                "from_lot_hist_id": item.from_lot_hist_id,
                "to_lot_hist_id": item.to_lot_hist_id,
                "postgres_ids": list(item.postgres_ids),
                "graph_ids": list(item.graph_ids),
                "relation_ids": list(item.relation_ids),
            }
            for item in route.mismatches
        ],
    }


def _observation_rank(observation: WaferParameterObservation) -> tuple[Any, ...]:
    severity = {"OOS": 0, "OOC": 1, "IN": 2}[observation.alarm_type.value]
    magnitude = (
        -1.0
        if observation.deviation is None or observation.deviation.magnitude is None
        else observation.deviation.magnitude
    )
    return (
        severity,
        -observation.oos_point_count,
        -observation.ooc_point_count,
        -magnitude,
        observation.lot_hist_id,
        observation.wafer_id,
        observation.recipe_step_no,
        observation.parameter_id,
    )


def _prompt_wafer_observations(
    snapshot: IncidentDiagnosticSnapshot,
) -> list[WaferParameterObservation]:
    """WAFER별 대표를 먼저 보존하고 나머지는 이상 심각도 순으로 제한한다."""

    observations = tuple(snapshot.wafer_observations)
    selected: list[WaferParameterObservation] = []
    seen: set[tuple[str, str, int, str]] = set()

    def identity(item: WaferParameterObservation) -> tuple[str, str, int, str]:
        return (
            item.lot_hist_id,
            item.wafer_id,
            item.recipe_step_no,
            item.parameter_id,
        )

    def add(item: WaferParameterObservation) -> None:
        key = identity(item)
        if key in seen or len(selected) >= MAX_PROMPT_WAFER_OBSERVATIONS:
            return
        seen.add(key)
        selected.append(item)

    wafer_keys = sorted({(item.lot_hist_id, item.wafer_id) for item in observations})
    for wafer_key in wafer_keys:
        candidates = [
            item
            for item in observations
            if (item.lot_hist_id, item.wafer_id) == wafer_key
        ]
        add(min(candidates, key=_observation_rank))
    for observation in sorted(observations, key=_observation_rank):
        add(observation)
    return selected


def _diagnostic_payload(
    snapshot: IncidentDiagnosticSnapshot,
    route: ResolvedIncidentRoute,
) -> dict[str, Any]:
    """전체 진단은 유지하고 LLM에만 대표 관측·인용 가능한 ID를 싣는다."""

    payload = snapshot.model_dump(mode="json")
    observations = tuple(snapshot.wafer_observations)
    prompt_observations = _prompt_wafer_observations(snapshot)
    payload["wafer_observations"] = [
        item.model_dump(mode="json") for item in prompt_observations
    ]
    payload["wafer_observation_count"] = len(observations)
    payload["wafer_observations_omitted_count"] = len(observations) - len(
        prompt_observations
    )

    source_ids = dict(payload["source_ids"])
    alarm_refs = tuple(snapshot.source_ids.alarm_refs)
    allowed_alarm_refs = set(alarm_refs)
    prompt_alarm_refs = [
        alarm.to_token()
        for alarm in _prompt_member_alarms(route)
        if alarm.to_token() in allowed_alarm_refs
    ]
    source_ids["alarm_refs"] = prompt_alarm_refs
    source_ids["alarm_ref_count"] = len(alarm_refs)
    source_ids["alarm_refs_omitted_count"] = len(alarm_refs) - len(prompt_alarm_refs)
    payload["source_ids"] = source_ids
    return payload


def _document_payload(result: DocumentSearchToolResult | None) -> Any:
    if result is None:
        return None
    if not result.ok:
        return result.model_dump(mode="json")
    return {
        "ok": True,
        "reason": "",
        "hits": [
            {
                **hit.model_dump(mode="json", exclude={"content"}),
                "content": _excerpt(hit.content),
            }
            for hit in sorted(result.hits, key=lambda value: value.chunk_id)
        ],
    }


def build_hypothesis_messages(
    fdc_evidence: FdcSummaryToolResult | None | Sequence[FdcSummaryToolResult | None],
    graph_evidence: EquipmentContextToolResult | None,
    document_evidence: DocumentSearchToolResult | None,
    route: ResolvedIncidentRoute,
    *,
    correction_reason: str | None = None,
    diagnostic_snapshot: IncidentDiagnosticSnapshot | None = None,
    evidence_assessment: EvidenceAssessmentBlock | None = None,
    impact_scope: ImpactScopeBlock | None = None,
) -> list[dict[str, str]]:
    """초도·보정 시도가 같은 근거 builder를 쓰는 messages를 만든다."""

    fdc_items = (
        list(fdc_evidence) if isinstance(fdc_evidence, Sequence) else [fdc_evidence]
    )
    evidence = {
        "diagnostic_snapshot": (
            None
            if diagnostic_snapshot is None
            else _diagnostic_payload(diagnostic_snapshot, route)
        ),
        "document": _document_payload(document_evidence),
        "evidence_assessment": (
            None
            if evidence_assessment is None
            else evidence_assessment.model_dump(mode="json")
        ),
        "equipment": (
            None if graph_evidence is None else graph_evidence.model_dump(mode="json")
        ),
        "fdc": [
            None if item is None else item.model_dump(mode="json")
            for item in (() if diagnostic_snapshot is not None else fdc_items)
        ],
        "impact_scope": (
            None if impact_scope is None else impact_scope.model_dump(mode="json")
        ),
        "route": _route_payload(route),
    }
    evidence_json = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    system = (
        "제공된 근거만 사용해 반도체 FDC 원인 가설 하나를 생성하세요. "
        "응답은 JSON 객체 하나만 반환하고 다음 키를 모두 포함하세요: "
        "predicted_fault_code, confidence, cause_summary, supporting_alarms, "
        "supporting_chunk_ids, supporting_relation_ids, supporting_lot_hist_ids, "
        "supporting_parameter_ids, uncertainty, observations, evidence_synthesis, "
        "alternative_hypotheses, impact_summary, verification_steps, limitations. "
        "predicted_fault_code는 FOC, RFM, MFD, TMD, OTH 중 하나여야 합니다. "
        "supporting_alarms 항목은 다음 형식을 사용하세요: "
        '{"source":"TRACE|SUMMARY|R03","alarm_id":"..."}. '
        "제공된 식별자만 인용하세요. member alarm은 최소 하나 인용하고, 문서 hit나 "
        "관계 식별자가 존재하면 각각 최소 하나를 인용하세요. 알람은 "
        "route.incident.member_alarms의 제공된 인용 후보, 문서 chunk는 "
        "document.hits[].chunk_id, "
        "관계는 route.graph_evidence[].relation_ids의 값을 정확히 복사하세요. "
        "lot history와 parameter 식별자는 diagnostic_snapshot.source_ids에서만 "
        "복사하세요. 측정값, 설비, 공정 단계, 문서 또는 관계를 만들어내지 마세요. "
        "diagnostic_snapshot.wafer_observations는 WAFER별 대표 관측이며 전체 건수와 "
        "생략 건수는 같은 객체의 count 필드로 확인하세요. "
        "impact_scope.check_required는 확인 대상이며 확정 피해가 아닙니다. "
        "모든 설명형 문자열은 한국어로 작성하세요. 영어는 근거에서 그대로 복사한 "
        "식별자, enum 코드, 모델명, parameter 이름과 단위에만 허용됩니다. 이 규칙은 "
        "cause_summary, uncertainty, observations, evidence_synthesis, "
        "alternative_hypotheses의 summary와 lower_rank_reason, impact_summary, "
        "verification_steps, limitations에 모두 적용됩니다. "
        "추가 키 없이 다음 15개 키와 값 형태를 정확히 사용하세요: "
        '{"predicted_fault_code":"OTH","confidence":0.0,"cause_summary":"...",'
        '"supporting_alarms":[{"source":"SUMMARY","alarm_id":"..."}],'
        '"supporting_chunk_ids":["..."],"supporting_relation_ids":["..."],'
        '"supporting_lot_hist_ids":["..."],"supporting_parameter_ids":["..."],'
        '"uncertainty":"...","observations":["..."],'
        '"evidence_synthesis":"...","alternative_hypotheses":['
        '{"summary":"...","lower_rank_reason":"..."}],'
        '"impact_summary":"...","verification_steps":["..."],'
        '"limitations":["..."]}. confidence는 0부터 1 사이의 숫자이며 모든 '
        "supporting 필드는 JSON 배열입니다."
    )
    user = f"Evidence JSON:\n{evidence_json}"
    if correction_reason is not None:
        user += (
            "\n이전 출력은 다음 안전 사유 코드로 거부되었습니다: "
            f"{correction_reason}. 같은 근거로 JSON을 다시 작성하세요. 인용 "
            "식별자는 시스템 지시에 명시된 배열에서 정확히 복사하고 document_id, "
            "title, chamber_id 또는 추론한 식별자로 대체하지 마세요."
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    scan_hypothesis_messages(messages)
    return messages


def scan_hypothesis_messages(messages: list[dict[str, str]]) -> None:
    """최종 전송 문자열 전체에서 금지 독립 token과 길이를 검사한다."""

    contents = [message.get("content") for message in messages]
    if any(not isinstance(content, str) for content in contents):
        raise HypothesisPromptError("HYPOTHESIS_PROMPT_BLOCKED")
    combined = "\n".join(contents)  # type: ignore[arg-type]
    if _BLOCKED_PATTERN.search(combined):
        raise HypothesisPromptError("HYPOTHESIS_PROMPT_BLOCKED")
    if len(combined) > MAX_PROMPT_CHARS:
        raise HypothesisPromptError("HYPOTHESIS_PROMPT_TOO_LARGE")


__all__ = [
    "MAX_DOCUMENT_EXCERPT_CHARS",
    "MAX_PROMPT_MEMBER_ALARMS",
    "MAX_PROMPT_WAFER_OBSERVATIONS",
    "MAX_PROMPT_CHARS",
    "PROMPT_VERSION",
    "TRUNCATION_MARKER",
    "HypothesisPromptError",
    "build_hypothesis_messages",
    "scan_hypothesis_messages",
]
