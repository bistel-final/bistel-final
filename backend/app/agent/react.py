"""V5-C-7.1 Level 3 ReAct — LLM이 다음 읽기 Tool을 고르고, 코드가 가드한다.

원칙: **조사는 에이전트가, 조치는 규칙이.** 이 모듈은 다음 관찰 행동(FDC 요약·문서 검색·
설비 컨텍스트·중단)만 고른다. `send_action`은 선택지에 없고, 인자는 route가 이미 가진
식별자 allowlist로만 검증한다. 가설 생성·조치 결정·HITL·전송 노드는 Level 1·2와 같다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import Field, ValidationError, model_validator

from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import LlmUsage, StateModel
from app.common import llm
from app.common.config import AGENT_MAX_RETRY
from app.common.enums import ToolCallStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    ChamberParameterHistoryToolResult,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    MetrologyResultToolResult,
)

logger = logging.getLogger(__name__)

REACT_PROMPT_VERSION: Final = "agent-react-v2-ko1"
REACT_MAX_STEPS: Final = 10
REACT_MAX_GUARD_REJECTIONS: Final = 2
REACT_TOOLS: Final = (
    "get_fdc_summary",
    "get_chamber_parameter_history",
    "get_metrology_result",
    "search_documents",
    "get_equipment_context",
)
_RATIONALE_MAX: Final = 120
_OBSERVATION_MAX: Final = 160
_QUERY_MAX: Final = 200
_FORBIDDEN_QUERY_CHARS: Final = frozenset("<>{}[]|\\`")

ReactNext = Literal[
    "get_fdc_summary",
    "get_chamber_parameter_history",
    "get_metrology_result",
    "search_documents",
    "get_equipment_context",
    "stop",
]
ReactPhase = Literal["SELECTED", "OBSERVED", "REJECTED", "STOPPED"]
ReactStopReason = Literal[
    "LLM_STOP",
    "STEP_CAP",
    "BUDGET_EXHAUSTED",
    "GUARD_LIMIT",
    "REACT_STRUCTURE_INVALID",
    "LLM_TIMEOUT",
    "LLM_DEPENDENCY",
]

REACT_SELECT_SCHEMA: Final[dict[str, object]] = {
    "name": "agent_react_select",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rationale_summary": {"type": "string"},
            "next": {"type": "string", "enum": [*REACT_TOOLS, "stop"]},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fdc_candidate_id": {"type": ["string", "null"]},
                    "history_candidate_id": {"type": ["string", "null"]},
                    "metrology_candidate_id": {"type": ["string", "null"]},
                    "query": {"type": ["string", "null"]},
                },
                "required": [
                    "fdc_candidate_id",
                    "history_candidate_id",
                    "metrology_candidate_id",
                    "query",
                ],
            },
            "stop_reason": {"type": ["string", "null"]},
        },
        "required": ["rationale_summary", "next", "arguments", "stop_reason"],
    },
}


class ReactSelectionError(RuntimeError):
    """LLM 선택 단계의 fail-closed 오류. 원문·비밀을 담지 않는다."""

    def __init__(self, code: str, *, usage: LlmUsage | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.usage_or_none = usage


class ReactArguments(StateModel):
    fdc_candidate_id: str | None = Field(default=None, max_length=20)
    history_candidate_id: str | None = Field(default=None, max_length=20)
    metrology_candidate_id: str | None = Field(default=None, max_length=20)
    query: str | None = Field(default=None, max_length=1000)


class ReactSelection(StateModel):
    """LLM 출력 그대로(스키마 통과). 가드 판정은 `guard_selection`이 한다."""

    rationale_summary: str = Field(min_length=1, max_length=_RATIONALE_MAX)
    next: ReactNext
    arguments: ReactArguments = Field(default_factory=ReactArguments)
    stop_reason: str | None = Field(default=None, max_length=500)


class ReactSelectionOutcome(StateModel):
    selection: ReactSelection
    llm_usage: LlmUsage


class SelectorTokens(StateModel):
    input: int = Field(ge=0)
    output: int = Field(ge=0)


CandidateRelation = Literal["CURRENT", "UPSTREAM", "DOWNSTREAM"]


class FdcCandidate(StateModel):
    candidate_id: str = Field(pattern=r"^F[1-9][0-9]*$")
    lot_hist_id: str = Field(min_length=1, max_length=20)
    wafer_id: str = Field(min_length=1, max_length=24)
    wafer_ordinal: int = Field(ge=1)
    relation: CandidateRelation
    step_id: str = Field(min_length=1, max_length=20)
    chamber_id: str = Field(min_length=1, max_length=24)
    track_in_at: datetime
    lot_first_track_in_at: datetime | None = None


class HistoryCandidate(StateModel):
    candidate_id: str = Field(pattern=r"^H[1-9][0-9]*$")
    scope: Literal["CURRENT", "SIBLING"]
    chamber_id: str = Field(min_length=1, max_length=24)
    parameter_id: str = Field(min_length=1, max_length=20)
    step_no: int = Field(ge=1)
    before: datetime
    current_lot_id: str = Field(min_length=1, max_length=20)
    incident_step_id: str = Field(min_length=1, max_length=20)


class MetrologyCandidate(StateModel):
    candidate_id: str = Field(pattern=r"^M[1-9][0-9]*$")
    lot_id: str = Field(min_length=1, max_length=20)
    step_id: str = Field(min_length=1, max_length=20)
    relation: CandidateRelation


class ReactCandidates(StateModel):
    run_id: str = Field(min_length=1)
    fdc: tuple[FdcCandidate, ...] = ()
    history: tuple[HistoryCandidate, ...] = ()
    metrology: tuple[MetrologyCandidate, ...] = ()


class ReactStep(StateModel):
    """안전하게 공개 가능한 ReAct event. raw thought·query·식별자 인자를 담지 않는다."""

    seq: int = Field(ge=1)
    phase: ReactPhase
    rationale_summary: str | None = Field(default=None, max_length=_RATIONALE_MAX)
    tool: ReactNext | None = None
    argument_digest: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    argument_summary: str | None = Field(default=None, max_length=80)
    observation_summary: str | None = Field(default=None, max_length=_OBSERVATION_MAX)
    guard_code: str | None = Field(default=None, max_length=64)
    react_prompt_version: str | None = Field(default=None, max_length=40)
    llm_model: str | None = Field(default=None, max_length=64)
    selector_tokens: SelectorTokens = Field(
        default_factory=lambda: SelectorTokens(input=0, output=0)
    )
    stop_reason: ReactStopReason | None = None
    degraded: bool = False

    @model_validator(mode="after")
    def _phase_contract(self) -> ReactStep:
        selector_metadata = (
            self.react_prompt_version is not None and self.llm_model is not None
        )
        if (self.react_prompt_version is None) != (self.llm_model is None):
            raise ValueError("selector provenance는 함께 설정해야 합니다")
        if self.phase in {"SELECTED", "OBSERVED"}:
            if (
                self.tool not in REACT_TOOLS
                or self.rationale_summary is None
                or not selector_metadata
                or self.guard_code is not None
                or self.stop_reason is not None
                or (self.phase == "SELECTED" and self.observation_summary is not None)
                or (self.phase == "OBSERVED" and self.observation_summary is None)
            ):
                raise ValueError("SELECTED/OBSERVED trace 계약이 올바르지 않습니다")
        elif self.phase == "REJECTED":
            schema_rejection = self.guard_code == "REACT_SCHEMA_INVALID"
            guard_rejection = bool(
                self.guard_code and self.guard_code.startswith("REACT_GUARD_")
            )
            if (
                not (schema_rejection or guard_rejection)
                or self.stop_reason is not None
                or self.observation_summary is not None
                or (
                    schema_rejection
                    and (self.tool is not None or self.rationale_summary is not None)
                )
                or (
                    guard_rejection
                    and (
                        self.tool not in (*REACT_TOOLS, "stop")
                        or self.rationale_summary is None
                        or not selector_metadata
                    )
                )
            ):
                raise ValueError("REJECTED trace 계약이 올바르지 않습니다")
        elif (
            self.stop_reason is None
            or self.guard_code is not None
            or self.observation_summary is not None
            or (
                self.stop_reason == "LLM_STOP"
                and (
                    self.tool != "stop"
                    or self.rationale_summary is None
                    or not selector_metadata
                    or self.degraded
                )
            )
            or (
                self.stop_reason != "LLM_STOP"
                and (
                    self.tool is not None
                    or self.rationale_summary is not None
                    or not self.degraded
                )
            )
        ):
            raise ValueError("STOPPED trace 계약이 올바르지 않습니다")
        return self


class ReactContext(StateModel):
    """선택 프롬프트에 주는 관찰 요약(식별자·수치만). label·prompt 원문 없음."""

    lot_id: str
    chamber_id: str
    representative_alarm: AlarmRef
    member_alarm_count: int = Field(ge=0)
    r03_present: bool
    candidates: ReactCandidates
    fetched_fdc_candidate_ids: tuple[str, ...]
    observed_parameter_keys: tuple[tuple[str, int], ...]
    fdc_observations: tuple[str, ...]
    equipment_observation: str | None
    sibling_chamber_ids: tuple[str, ...] = ()
    history_observations: tuple[str, ...]
    metrology_observations: tuple[str, ...]
    document_observations: tuple[str, ...]
    remaining_tool_calls: int = Field(ge=0)
    remaining_steps: int = Field(ge=0)
    guard_rejections: int = Field(ge=0)
    structure_retry: bool = False
    recent_tool_events: tuple[str, ...] = ()


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_initial_candidates(
    *,
    run_id: str,
    route: ResolvedIncidentRoute,
    current_lot_hist_ids: Sequence[str],
) -> ReactCandidates:
    """route의 실제 순서에서 current/upstream/downstream token을 결정적으로 발급한다."""

    current_ids = frozenset(current_lot_hist_ids)
    pending_fdc: list[tuple[int, int, datetime, str, Any]] = []
    ordered_wafers = sorted(
        route.wafer_routes,
        key=lambda wafer: (
            next(
                (step.wafer_no for step in wafer.steps if step.wafer_no is not None),
                2**31,
            ),
            wafer.wafer_id,
        ),
    )
    for wafer_ordinal, wafer_route in enumerate(ordered_wafers, start=1):
        current_indexes = [
            index
            for index, step in enumerate(wafer_route.steps)
            if step.lot_hist_id in current_ids
        ]
        if not current_indexes:
            continue
        first_current = min(current_indexes)
        last_current = max(current_indexes)
        for index, step in enumerate(wafer_route.steps):
            if step.lot_hist_id in current_ids:
                relation: CandidateRelation = "CURRENT"
                relation_order = 0
            elif index < first_current:
                relation = "UPSTREAM"
                relation_order = 1
            elif index > last_current:
                relation = "DOWNSTREAM"
                relation_order = 2
            else:
                continue
            pending_fdc.append(
                (
                    relation_order,
                    wafer_ordinal,
                    step.track_in_at,
                    step.lot_hist_id,
                    (step, relation),
                )
            )
    pending_fdc.sort(key=lambda item: item[:4])
    fdc_items: list[FdcCandidate] = []
    for index, pending in enumerate(pending_fdc, start=1):
        _relation_order, wafer_ordinal, _track_in_at, _lot_hist_id, payload = pending
        step, relation = payload
        fdc_items.append(
            FdcCandidate(
                candidate_id=f"F{index}",
                lot_hist_id=step.lot_hist_id,
                wafer_id=step.wafer_id,
                wafer_ordinal=wafer_ordinal,
                relation=relation,
                step_id=step.step_id,
                chamber_id=step.chamber_id,
                track_in_at=step.track_in_at,
                lot_first_track_in_at=step.lot_first_track_in_at,
            )
        )
    fdc = tuple(fdc_items)

    seen_metrology: set[tuple[str, str]] = set()
    metrology_items: list[tuple[int, str, CandidateRelation]] = []
    relation_order = {"CURRENT": 0, "UPSTREAM": 1, "DOWNSTREAM": 2}
    for candidate in fdc:
        key = (route.incident.lot_id, candidate.step_id)
        if key in seen_metrology:
            continue
        seen_metrology.add(key)
        metrology_items.append(
            (relation_order[candidate.relation], candidate.step_id, candidate.relation)
        )
    metrology_items.sort(key=lambda item: (item[0], item[1]))
    metrology = tuple(
        MetrologyCandidate(
            candidate_id=f"M{index}",
            lot_id=route.incident.lot_id,
            step_id=step_id,
            relation=relation,
        )
        for index, (_order, step_id, relation) in enumerate(
            metrology_items,
            start=1,
        )
    )
    return ReactCandidates(run_id=run_id, fdc=fdc, metrology=metrology)


def refresh_history_candidates(
    candidates: ReactCandidates,
    *,
    fdc_results: Sequence[FdcSummaryToolResult | None],
    equipment: EquipmentContextToolResult | None,
) -> ReactCandidates:
    """성공 FDC 관찰과 성공 설비 관찰만으로 H token을 재파생한다."""

    fdc_by_lot_hist = {item.lot_hist_id: item for item in candidates.fdc}
    pending: dict[tuple[object, ...], HistoryCandidate] = {}
    for result in fdc_results:
        if result is None or not result.ok or result.wafer is None:
            continue
        fdc_candidate = fdc_by_lot_hist.get(result.wafer.lot_hist_id)
        if fdc_candidate is None or fdc_candidate.relation != "CURRENT":
            continue
        before = fdc_candidate.lot_first_track_in_at or min(
            item.track_in_at
            for item in candidates.fdc
            if item.relation == "CURRENT"
            and item.chamber_id == fdc_candidate.chamber_id
            and item.step_id == fdc_candidate.step_id
        )
        for parameter in result.parameters:
            current_key = (
                "CURRENT",
                fdc_candidate.chamber_id,
                parameter.parameter_id,
                parameter.recipe_step_no,
                before,
            )
            pending[current_key] = HistoryCandidate(
                candidate_id="H1",
                scope="CURRENT",
                chamber_id=fdc_candidate.chamber_id,
                parameter_id=parameter.parameter_id,
                step_no=parameter.recipe_step_no,
                before=before,
                current_lot_id=result.wafer.lot_id,
                incident_step_id=fdc_candidate.step_id,
            )
            if (
                fdc_candidate.relation == "CURRENT"
                and equipment is not None
                and equipment.ok
            ):
                for sibling in equipment.sibling_chamber_ids:
                    sibling_key = (
                        "SIBLING",
                        str(sibling),
                        parameter.parameter_id,
                        parameter.recipe_step_no,
                        before,
                    )
                    pending[sibling_key] = HistoryCandidate(
                        candidate_id="H1",
                        scope="SIBLING",
                        chamber_id=str(sibling),
                        parameter_id=parameter.parameter_id,
                        step_no=parameter.recipe_step_no,
                        before=before,
                        current_lot_id=result.wafer.lot_id,
                        incident_step_id=fdc_candidate.step_id,
                    )
    ordered = sorted(
        pending.values(),
        key=lambda item: (
            0 if item.scope == "CURRENT" else 1,
            item.chamber_id,
            item.parameter_id,
            item.step_no,
            item.before,
        ),
    )

    def identity(item: HistoryCandidate) -> tuple[object, ...]:
        return (
            item.scope,
            item.chamber_id,
            item.parameter_id,
            item.step_no,
            item.before,
        )

    # 새 관찰이 정렬 앞에 생겨도 이미 발급한 H token의 의미는 바꾸지 않는다.
    previous = {identity(item): item.candidate_id for item in candidates.history}
    next_index = max(
        (int(item.candidate_id[1:]) for item in candidates.history), default=0
    )
    history_items = []
    for item in ordered:
        token = previous.get(identity(item))
        if token is None:
            next_index += 1
            token = f"H{next_index}"
        history_items.append(item.model_copy(update={"candidate_id": token}))
    history = tuple(sorted(history_items, key=lambda item: int(item.candidate_id[1:])))
    return candidates.model_copy(update={"history": history})


def summarize_fdc(result: FdcSummaryToolResult | None) -> str | None:
    if result is None or not result.ok or result.wafer is None:
        return None
    flagged = []
    for item in result.parameters:
        if not (item.ooc_point_cnt or item.oos_point_cnt):
            continue
        above = (
            item.ctrl_upper is not None
            and item.value_max is not None
            and item.value_max > item.ctrl_upper
        )
        below = (
            item.ctrl_lower is not None
            and item.value_min is not None
            and item.value_min < item.ctrl_lower
        )
        if above and below:
            direction = "BOTH"
        elif above:
            direction = "ABOVE"
        elif below:
            direction = "BELOW"
        else:
            direction = "UNKNOWN"
        flagged.append(
            f"{item.parameter_id}(ooc={item.ooc_point_cnt},"
            f"oos={item.oos_point_cnt},direction={direction})"
        )
    anomaly = ""
    if result.anomaly is not None and result.anomaly.is_anomaly is not None:
        anomaly = f" anomaly={'Y' if result.anomaly.is_anomaly else 'N'}"
    return _clip(
        f"wafer={result.wafer.wafer_no} params={len(result.parameters)} "
        f"flagged=[{', '.join(flagged) or '-'}]" + anomaly,
        _OBSERVATION_MAX,
    )


def summarize_equipment(result: EquipmentContextToolResult | None) -> str | None:
    if result is None or not result.ok:
        return None
    return _clip(
        f"upstream={len(result.upstream_process_step_ids)} "
        f"downstream={len(result.downstream_process_step_ids)} "
        f"siblings={len(result.sibling_chamber_ids)} "
        f"parameters={len(result.parameter_ids)}",
        _OBSERVATION_MAX,
    )


def summarize_documents(result: DocumentSearchToolResult | None) -> str | None:
    if result is None or not result.ok:
        return None
    titles = [f"{hit.title}#{hit.section or '-'}" for hit in result.hits[:4]]
    joined = "; ".join(titles) or "-"
    return _clip(f"hits={len(result.hits)} [{joined}]", _OBSERVATION_MAX)


def summarize_history(result: ChamberParameterHistoryToolResult | None) -> str | None:
    if result is None or not result.ok:
        return None
    return _clip(
        f"scope={result.scope} trend={result.trend} "
        f"prior={len(result.prior)} sample={result.sample_count}",
        _OBSERVATION_MAX,
    )


def summarize_metrology(result: MetrologyResultToolResult | None) -> str | None:
    if result is None or not result.ok:
        return None
    return _clip(
        f"samples={len(result.results)} fail_count={result.fail_count} "
        "Fault label 아님",
        _OBSERVATION_MAX,
    )


def arguments_digest(arguments: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _argument_matrix_guard(selection: ReactSelection) -> str | None:
    expected = {
        "get_fdc_summary": {"fdc_candidate_id"},
        "get_chamber_parameter_history": {"history_candidate_id"},
        "get_metrology_result": {"metrology_candidate_id"},
        "search_documents": {"query"},
        "get_equipment_context": set(),
        "stop": set(),
    }
    populated = {
        key
        for key, value in selection.arguments.model_dump(mode="json").items()
        if value is not None
    }
    if populated != expected[selection.next]:
        return "REACT_GUARD_ARGUMENT_MATRIX"
    return None


def resolve_call(
    selection: ReactSelection,
    context: ReactContext,
    *,
    document_model_code: str | None = None,
) -> dict[str, Any] | None:
    """검증된 token을 canonical Tool input과 안전한 표시 문구로 복원한다."""

    if selection.next == "stop":
        return None
    if selection.next == "get_fdc_summary":
        candidate = next(
            (
                item
                for item in context.candidates.fdc
                if item.candidate_id == selection.arguments.fdc_candidate_id
            ),
            None,
        )
        if candidate is None:
            return None
        label = {"CURRENT": "현재", "UPSTREAM": "상류", "DOWNSTREAM": "하류"}
        return {
            "tool": selection.next,
            "request": {"lot_hist_id": candidate.lot_hist_id},
            "argument_summary": (
                f"후보 wafer {candidate.wafer_ordinal}({label[candidate.relation]})"
            ),
        }
    if selection.next == "get_chamber_parameter_history":
        candidate = next(
            (
                item
                for item in context.candidates.history
                if item.candidate_id == selection.arguments.history_candidate_id
            ),
            None,
        )
        if candidate is None:
            return None
        return {
            "tool": selection.next,
            "request": {
                "chamber_id": candidate.chamber_id,
                "parameter_id": candidate.parameter_id,
                "step_no": candidate.step_no,
                "before": candidate.before.isoformat(),
                "n_lots": 3,
            },
            "internal_context": {
                "current_lot_id": candidate.current_lot_id,
                "incident_step_id": candidate.incident_step_id,
                "scope": candidate.scope,
            },
            "argument_summary": (
                f"이력 {candidate.candidate_id[1:]}"
                f"({'현재' if candidate.scope == 'CURRENT' else '형제'})"
            ),
        }
    if selection.next == "get_metrology_result":
        candidate = next(
            (
                item
                for item in context.candidates.metrology
                if item.candidate_id == selection.arguments.metrology_candidate_id
            ),
            None,
        )
        if candidate is None:
            return None
        label = {"CURRENT": "현재", "UPSTREAM": "상류", "DOWNSTREAM": "하류"}
        return {
            "tool": selection.next,
            "request": {"lot_id": candidate.lot_id, "step_id": candidate.step_id},
            "argument_summary": (
                f"계측 {candidate.candidate_id[1:]}({label[candidate.relation]})"
            ),
        }
    if selection.next == "search_documents":
        return {
            "tool": selection.next,
            "request": {
                "query": selection.arguments.query,
                "model_code": document_model_code,
            },
            "argument_summary": "문서 검색",
        }
    return {
        "tool": selection.next,
        "request": {"chamber_id": context.chamber_id},
        "argument_summary": "설비 컨텍스트",
    }


def guard_selection(
    selection: ReactSelection,
    context: ReactContext,
    *,
    equipment_fetched: bool,
    tool_history: Sequence[Any] = (),
    document_model_code: str | None = None,
) -> str | None:
    """코드가 강제하는 안전 가드. 위반 코드를 돌려주고 None이면 허용."""

    if matrix_guard := _argument_matrix_guard(selection):
        return matrix_guard
    if selection.next == "stop":
        return None
    if context.remaining_tool_calls <= 0:
        return "REACT_GUARD_BUDGET_EXHAUSTED"
    attempts = sum(
        1 for item in tool_history if getattr(item, "tool_name", None) == selection.next
    )
    if attempts >= AGENT_MAX_RETRY + 1:
        return "REACT_GUARD_BUDGET_EXHAUSTED"
    successful_inputs = tuple(
        getattr(item, "input", None)
        for item in tool_history
        if getattr(item, "status", None) is ToolCallStatus.SUCCESS
        and getattr(item, "tool_name", None) == selection.next
    )
    if selection.next == "get_fdc_summary":
        candidate = next(
            (
                item
                for item in context.candidates.fdc
                if item.candidate_id == selection.arguments.fdc_candidate_id
            ),
            None,
        )
        if candidate is None:
            return "REACT_GUARD_CANDIDATE_UNKNOWN"
        if candidate.candidate_id in context.fetched_fdc_candidate_ids:
            return "REACT_GUARD_TARGET_REPEATED"
        if any(
            isinstance(value, Mapping)
            and value.get("lot_hist_id") == candidate.lot_hist_id
            for value in successful_inputs
        ):
            return "REACT_GUARD_TARGET_REPEATED"
        return None
    if selection.next == "get_chamber_parameter_history":
        candidate = next(
            (
                item
                for item in context.candidates.history
                if item.candidate_id == selection.arguments.history_candidate_id
            ),
            None,
        )
        if candidate is None:
            return "REACT_GUARD_CANDIDATE_UNKNOWN"
        if (
            candidate.parameter_id,
            candidate.step_no,
        ) not in context.observed_parameter_keys:
            return "REACT_GUARD_PARAMETER_NOT_OBSERVED"
        if candidate.scope == "SIBLING" and not equipment_fetched:
            return "REACT_GUARD_SIBLING_UNRESOLVED"
        allowed_chambers = (
            (context.chamber_id,)
            if candidate.scope == "CURRENT"
            else context.sibling_chamber_ids
        )
        if candidate.chamber_id not in allowed_chambers:
            return "REACT_GUARD_CHAMBER_NOT_ALLOWED"
        if candidate.current_lot_id != context.lot_id:
            return "REACT_GUARD_CANDIDATE_UNKNOWN"
        resolved = resolve_call(selection, context)
        request = None if resolved is None else resolved["request"]
        if any(value == request for value in successful_inputs):
            return "REACT_GUARD_TARGET_REPEATED"
        return None
    if selection.next == "get_metrology_result":
        candidate = next(
            (
                item
                for item in context.candidates.metrology
                if item.candidate_id == selection.arguments.metrology_candidate_id
            ),
            None,
        )
        if candidate is None:
            return "REACT_GUARD_CANDIDATE_UNKNOWN"
        if candidate.lot_id != context.lot_id or candidate.step_id not in {
            item.step_id for item in context.candidates.fdc
        }:
            return "REACT_GUARD_CANDIDATE_UNKNOWN"
        resolved = resolve_call(selection, context)
        request = None if resolved is None else resolved["request"]
        if any(value == request for value in successful_inputs):
            return "REACT_GUARD_TARGET_REPEATED"
        return None
    if selection.next == "search_documents":
        query = selection.arguments.query
        if not query or not query.strip():
            return "REACT_GUARD_QUERY_EMPTY"
        if len(query) > _QUERY_MAX or any(ch in _FORBIDDEN_QUERY_CHARS for ch in query):
            return "REACT_GUARD_QUERY_INVALID"
        if any(
            isinstance(value, Mapping)
            and value.get("query") == query
            and value.get("model_code") == document_model_code
            for value in successful_inputs
        ):
            return "REACT_GUARD_QUERY_REPEATED"
        return None
    if selection.next == "get_equipment_context":
        if equipment_fetched or any(
            getattr(item, "tool_name", None) == "get_equipment_context"
            and getattr(item, "status", None) is ToolCallStatus.SUCCESS
            for item in tool_history
        ):
            return "REACT_GUARD_EQUIPMENT_REPEATED"
        return None
    return "REACT_GUARD_TOOL_NOT_ALLOWED"


def build_react_select_messages(context: ReactContext) -> list[dict[str, str]]:
    system = (
        "당신은 반도체 FDC 이상감지 조사 에이전트입니다. 지금까지의 관찰을 보고 "
        "다음에 실행할 조사 행동 하나를 고르세요. 선택지는 "
        "get_fdc_summary(후보 token), "
        "get_chamber_parameter_history(현재·형제 이력 token), "
        "get_metrology_result(계측 token), "
        "search_documents(스펙·운전 기준 문서 검색어), "
        "get_equipment_context(설비·공정 연쇄 컨텍스트, 1회), "
        "stop(근거가 충분하면 중단) 입니다. "
        "조치 결정이나 전송은 당신의 역할이 아니며 선택지에도 없습니다. "
        "rationale_summary는 관찰에서 다음 행동으로 이어지는 이유 한 문장을 "
        "120자 이내 한국어로 적습니다. 내부 추론 과정이나 원문을 적지 않습니다. "
        "arguments는 사용하지 않는 키를 null로 둡니다. 반드시 JSON 스키마만 출력합니다."
    )
    if context.structure_retry:
        system += (
            " 이전 응답은 스키마에 맞지 않았습니다. 이번에는 필수 키와 enum을 "
            "정확히 지키세요."
        )
    user = json.dumps(
        {
            "incident": {
                "representative_alarm_source": (
                    context.representative_alarm.source.value
                ),
                "member_alarm_count": context.member_alarm_count,
                "r03_present": context.r03_present,
            },
            "candidates": {
                "fdc": [
                    {
                        "id": item.candidate_id,
                        "relation": item.relation,
                        "wafer_ordinal": item.wafer_ordinal,
                        "observed": (
                            item.candidate_id in context.fetched_fdc_candidate_ids
                        ),
                    }
                    for item in context.candidates.fdc
                ],
                "history": [
                    {
                        "id": item.candidate_id,
                        "scope": item.scope,
                        "parameter_id": item.parameter_id,
                        "step_no": item.step_no,
                    }
                    for item in context.candidates.history
                ],
                "metrology": [
                    {
                        "id": item.candidate_id,
                        "relation": item.relation,
                    }
                    for item in context.candidates.metrology
                ],
            },
            "observations": {
                "fdc": list(context.fdc_observations),
                "equipment": context.equipment_observation,
                "history": list(context.history_observations),
                "metrology": list(context.metrology_observations),
                "documents": list(context.document_observations),
                "recent_tools": list(context.recent_tool_events[-4:]),
            },
            "budget": {
                "remaining_tool_calls": context.remaining_tool_calls,
                "remaining_steps": context.remaining_steps,
                "guard_rejections": context.guard_rejections,
            },
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _completion_usage(completion: llm.ChatCompletion) -> LlmUsage:
    return LlmUsage(
        model=completion.model,
        prompt_version=REACT_PROMPT_VERSION,
        input_tokens=completion.prompt_tokens,
        output_tokens=completion.completion_tokens,
    )


def select_next_step(
    context: ReactContext,
    *,
    seed: int | None = None,
) -> ReactSelectionOutcome:
    """LLM 1회 호출로 다음 행동을 고른다. 구조 위반은 fail-closed."""

    messages = build_react_select_messages(context)
    try:
        completion = llm.chat_with_usage(
            messages,
            json_schema=REACT_SELECT_SCHEMA,
            **({} if seed is None else {"seed": seed}),
        )
    except llm.LlmTimeoutError as exc:
        raise ReactSelectionError("LLM_TIMEOUT") from exc
    except llm.LlmDependencyError as exc:
        response_usage = exc.usage_or_none
        usage = (
            None
            if response_usage is None
            else LlmUsage(
                model=response_usage.model,
                prompt_version=REACT_PROMPT_VERSION,
                input_tokens=response_usage.prompt_tokens,
                output_tokens=response_usage.completion_tokens,
            )
        )
        raise ReactSelectionError("LLM_DEPENDENCY", usage=usage) from exc
    except llm.LlmNotReadyError as exc:
        raise ReactSelectionError("LLM_DEPENDENCY") from exc
    usage = _completion_usage(completion)
    try:
        payload = json.loads(completion.content)
        selection = ReactSelection.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        logger.warning("react selection rejected: structure invalid")
        raise ReactSelectionError("REACT_STRUCTURE_INVALID", usage=usage) from exc
    return ReactSelectionOutcome(selection=selection, llm_usage=usage)


ReactSelectPort = Callable[[ReactContext], ReactSelectionOutcome]


def production_port() -> ReactSelectPort:
    return select_next_step


def build_context(
    *,
    run_id: str,
    lot_id: str,
    chamber_id: str,
    representative_alarm: AlarmRef,
    member_alarms: Sequence[AlarmRef],
    route: ResolvedIncidentRoute,
    candidates: ReactCandidates | Mapping[str, Any],
    fdc_results: Sequence[FdcSummaryToolResult | None],
    equipment: EquipmentContextToolResult | None,
    documents: Sequence[DocumentSearchToolResult | None],
    history_results: Sequence[ChamberParameterHistoryToolResult | None] = (),
    metrology_results: Sequence[MetrologyResultToolResult | None] = (),
    remaining_tool_calls: int,
    remaining_steps: int,
    guard_rejections: int,
    react_trace: Sequence[Mapping[str, Any]] = (),
) -> ReactContext:
    del route  # route 원문은 token 발급 때만 사용하고 selector에는 주지 않는다.
    resolved_candidates = ReactCandidates.model_validate(candidates)
    if resolved_candidates.run_id != run_id:
        raise ValueError("REACT_CANDIDATE_RUN_MISMATCH")
    candidate_by_lot_hist = {
        item.lot_hist_id: item.candidate_id for item in resolved_candidates.fdc
    }
    fetched = tuple(
        candidate_by_lot_hist[item.wafer.lot_hist_id]
        for item in fdc_results
        if item is not None
        and item.ok
        and item.wafer is not None
        and item.wafer.lot_hist_id in candidate_by_lot_hist
    )
    observed_parameter_keys = tuple(
        sorted(
            {
                (parameter.parameter_id, parameter.recipe_step_no)
                for item in fdc_results
                if item is not None and item.ok
                for parameter in item.parameters
            }
        )
    )
    return ReactContext(
        lot_id=lot_id,
        chamber_id=chamber_id,
        representative_alarm=representative_alarm,
        member_alarm_count=len(member_alarms),
        r03_present=any(alarm.source.value == "R03" for alarm in member_alarms),
        candidates=resolved_candidates,
        fetched_fdc_candidate_ids=tuple(dict.fromkeys(fetched)),
        observed_parameter_keys=observed_parameter_keys,
        fdc_observations=tuple(
            f"{candidate_by_lot_hist.get(item.wafer.lot_hist_id, '관찰')}: {summary}"
            for item in fdc_results
            if item is not None
            and item.ok
            and item.wafer is not None
            and (summary := summarize_fdc(item))
        ),
        equipment_observation=summarize_equipment(equipment),
        sibling_chamber_ids=(
            tuple(str(item) for item in equipment.sibling_chamber_ids)
            if equipment is not None and equipment.ok
            else ()
        ),
        history_observations=tuple(
            summary
            for summary in (summarize_history(item) for item in history_results)
            if summary
        ),
        metrology_observations=tuple(
            summary
            for summary in (summarize_metrology(item) for item in metrology_results)
            if summary
        ),
        document_observations=tuple(
            summary
            for summary in (summarize_documents(item) for item in documents)
            if summary
        ),
        remaining_tool_calls=max(0, remaining_tool_calls),
        remaining_steps=max(0, remaining_steps),
        guard_rejections=guard_rejections,
        recent_tool_events=tuple(
            _clip(
                f"{step.get('tool') or 'unknown'}: "
                f"{step.get('observation_summary')}",
                _OBSERVATION_MAX,
            )
            for step in react_trace
            if step.get("phase") == "OBSERVED"
            and isinstance(step.get("observation_summary"), str)
        ),
    )


def trace_entry(
    *,
    seq: int,
    selection: ReactSelection,
    usage: LlmUsage,
    phase: ReactPhase,
    observation_summary: str | None = None,
    guard_code: str | None = None,
    canonical_arguments: Mapping[str, Any] | None = None,
    argument_summary: str | None = None,
    degraded: bool = False,
) -> ReactStep:
    return ReactStep(
        seq=seq,
        phase=phase,
        rationale_summary=_clip(selection.rationale_summary, _RATIONALE_MAX),
        tool=selection.next,
        argument_digest=(
            None
            if selection.next == "stop" or canonical_arguments is None
            else arguments_digest(canonical_arguments)
        ),
        argument_summary=argument_summary,
        observation_summary=(
            None
            if observation_summary is None
            else _clip(observation_summary, _OBSERVATION_MAX)
        ),
        guard_code=guard_code,
        react_prompt_version=usage.prompt_version,
        llm_model=usage.model,
        selector_tokens=SelectorTokens(
            input=usage.input_tokens,
            output=usage.output_tokens,
        ),
        stop_reason="LLM_STOP" if phase == "STOPPED" else None,
        degraded=degraded,
    )


def system_stop_entry(
    *,
    seq: int,
    reason: ReactStopReason,
    usage: LlmUsage | None = None,
) -> ReactStep:
    return ReactStep(
        seq=seq,
        phase="STOPPED",
        react_prompt_version=None if usage is None else usage.prompt_version,
        llm_model=None if usage is None else usage.model,
        selector_tokens=SelectorTokens(
            input=0 if usage is None else usage.input_tokens,
            output=0 if usage is None else usage.output_tokens,
        ),
        stop_reason=reason,
        degraded=True,
    )


def structure_rejection_entry(*, seq: int, usage: LlmUsage | None) -> ReactStep:
    return ReactStep(
        seq=seq,
        phase="REJECTED",
        guard_code="REACT_SCHEMA_INVALID",
        react_prompt_version=None if usage is None else usage.prompt_version,
        llm_model=None if usage is None else usage.model,
        selector_tokens=SelectorTokens(
            input=0 if usage is None else usage.input_tokens,
            output=0 if usage is None else usage.output_tokens,
        ),
    )


def trace_payload(steps: Sequence[ReactStep]) -> list[dict[str, Any]]:
    return [step.model_dump(mode="json") for step in steps]


def trace_from_payload(payload: object) -> tuple[ReactStep, ...]:
    if not isinstance(payload, list | tuple):
        return ()
    return tuple(
        ReactStep.model_validate(item) for item in payload if isinstance(item, Mapping)
    )
