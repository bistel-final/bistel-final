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
from typing import Any, Final, Literal

from pydantic import Field, ValidationError, model_validator

from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import LlmUsage, StateModel
from app.common import llm
from app.common.config import AGENT_MAX_RETRY
from app.common.enums import ToolCallStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

logger = logging.getLogger(__name__)

REACT_PROMPT_VERSION: Final = "agent-react-v1-ko1"
REACT_MAX_STEPS: Final = 6
REACT_MAX_GUARD_REJECTIONS: Final = 2
REACT_TOOLS: Final = ("get_fdc_summary", "search_documents", "get_equipment_context")
_RATIONALE_MAX: Final = 120
_OBSERVATION_MAX: Final = 160
_QUERY_MAX: Final = 200
_FORBIDDEN_QUERY_CHARS: Final = frozenset("<>{}[]|\\`")

ReactNext = Literal[
    "get_fdc_summary", "search_documents", "get_equipment_context", "stop"
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
                    "lot_hist_id": {"type": ["string", "null"]},
                    "query": {"type": ["string", "null"]},
                },
                "required": ["lot_hist_id", "query"],
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
    lot_hist_id: str | None = Field(default=None, max_length=64)
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
                        self.tool not in REACT_TOOLS
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
    allowed_lot_hist_ids: tuple[str, ...]
    fetched_lot_hist_ids: tuple[str, ...]
    fdc_observations: tuple[str, ...]
    equipment_observation: str | None
    document_observations: tuple[str, ...]
    remaining_tool_calls: int = Field(ge=0)
    remaining_steps: int = Field(ge=0)
    guard_rejections: int = Field(ge=0)
    structure_retry: bool = False
    recent_tool_events: tuple[str, ...] = ()


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        f"lot_hist={result.wafer.lot_hist_id} wafer={result.wafer.wafer_no} "
        f"step={result.wafer.step_id} params={len(result.parameters)} "
        f"flagged=[{', '.join(flagged) or '-'}]" + anomaly,
        _OBSERVATION_MAX,
    )


def summarize_equipment(result: EquipmentContextToolResult | None) -> str | None:
    if result is None or not result.ok:
        return None
    return _clip(
        f"equipment={result.equipment_id} model={result.model_code} "
        f"step={result.process_step_id} "
        f"upstream={list(result.upstream_process_step_ids)} "
        f"downstream={list(result.downstream_process_step_ids)} "
        f"parameters={len(result.parameter_ids)}",
        _OBSERVATION_MAX,
    )


def summarize_documents(result: DocumentSearchToolResult | None) -> str | None:
    if result is None or not result.ok:
        return None
    titles = [f"{hit.title}#{hit.section or '-'}" for hit in result.hits[:4]]
    joined = "; ".join(titles) or "-"
    return _clip(f"hits={len(result.hits)} [{joined}]", _OBSERVATION_MAX)


def arguments_digest(arguments: ReactArguments) -> str:
    raw = json.dumps(
        arguments.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def guard_selection(
    selection: ReactSelection,
    context: ReactContext,
    *,
    equipment_fetched: bool,
    tool_history: Sequence[Any] = (),
    document_model_code: str | None = None,
) -> str | None:
    """코드가 강제하는 안전 가드. 위반 코드를 돌려주고 None이면 허용."""

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
    )
    if selection.next == "get_fdc_summary":
        target = selection.arguments.lot_hist_id
        if target is None or target not in context.allowed_lot_hist_ids:
            return "REACT_GUARD_TARGET_NOT_ALLOWED"
        if target in context.fetched_lot_hist_ids:
            return "REACT_GUARD_TARGET_REPEATED"
        if any(
            isinstance(value, Mapping) and value.get("lot_hist_id") == target
            for value in successful_inputs
        ):
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
        "다음에 실행할 조사 행동 하나를 고르세요. 선택지는 get_fdc_summary(허용된 "
        "lot_hist_id 중 하나), search_documents(스펙·운전 기준 문서 검색어), "
        "get_equipment_context(설비·공정 연쇄 컨텍스트, 1회), "
        "stop(충분히 조사함) 입니다. "
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
                "lot_id": context.lot_id,
                "chamber_id": context.chamber_id,
                "representative_alarm": context.representative_alarm.model_dump(
                    mode="json"
                ),
                "member_alarm_count": context.member_alarm_count,
                "r03_present": context.r03_present,
            },
            "allowed_lot_hist_ids": list(context.allowed_lot_hist_ids),
            "fetched_lot_hist_ids": list(context.fetched_lot_hist_ids),
            "observations": {
                "fdc": list(context.fdc_observations),
                "equipment": context.equipment_observation,
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
    lot_id: str,
    chamber_id: str,
    representative_alarm: AlarmRef,
    member_alarms: Sequence[AlarmRef],
    route: ResolvedIncidentRoute,
    allowed_lot_hist_ids: Sequence[str],
    fdc_results: Sequence[FdcSummaryToolResult | None],
    equipment: EquipmentContextToolResult | None,
    documents: Sequence[DocumentSearchToolResult | None],
    remaining_tool_calls: int,
    remaining_steps: int,
    guard_rejections: int,
    react_trace: Sequence[Mapping[str, Any]] = (),
) -> ReactContext:
    del route  # route 요약은 alarm·allowlist로 충분하다. 원문 노출을 늘리지 않는다.
    fetched = tuple(
        item.wafer.lot_hist_id
        for item in fdc_results
        if item is not None and item.ok and item.wafer is not None
    )
    return ReactContext(
        lot_id=lot_id,
        chamber_id=chamber_id,
        representative_alarm=representative_alarm,
        member_alarm_count=len(member_alarms),
        r03_present=any(alarm.source.value == "R03" for alarm in member_alarms),
        allowed_lot_hist_ids=tuple(allowed_lot_hist_ids),
        fetched_lot_hist_ids=fetched,
        fdc_observations=tuple(
            summary
            for summary in (summarize_fdc(item) for item in fdc_results)
            if summary
        ),
        equipment_observation=summarize_equipment(equipment),
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
    candidate_ordinal: int | None = None,
    degraded: bool = False,
) -> ReactStep:
    summary: str | None = None
    if selection.next == "get_fdc_summary":
        summary = (
            "후보 wafer"
            if candidate_ordinal is None
            else f"후보 wafer {candidate_ordinal}"
        )
    elif selection.next == "search_documents":
        summary = "문서 검색"
    elif selection.next == "get_equipment_context":
        summary = "설비 컨텍스트"
    return ReactStep(
        seq=seq,
        phase=phase,
        rationale_summary=_clip(selection.rationale_summary, _RATIONALE_MAX),
        tool=selection.next,
        argument_digest=(
            None if selection.next == "stop" else arguments_digest(selection.arguments)
        ),
        argument_summary=summary,
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
