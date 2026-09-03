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

from pydantic import Field, ValidationError

from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import LlmUsage, StateModel
from app.common import llm
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
_THOUGHT_MAX: Final = 200
_OBSERVATION_MAX: Final = 300
_QUERY_MAX: Final = 200
_FORBIDDEN_QUERY_CHARS: Final = frozenset("<>{}[]|\\`")

ReactNext = Literal[
    "get_fdc_summary", "search_documents", "get_equipment_context", "stop"
]

REACT_SELECT_SCHEMA: Final[dict[str, object]] = {
    "name": "agent_react_select",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "thought": {"type": "string"},
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
        "required": ["thought", "next", "arguments", "stop_reason"],
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

    thought: str = Field(min_length=1, max_length=2000)
    next: ReactNext
    arguments: ReactArguments = Field(default_factory=ReactArguments)
    stop_reason: str | None = Field(default=None, max_length=500)


class ReactSelectionOutcome(StateModel):
    selection: ReactSelection
    llm_usage: LlmUsage


class ReactStep(StateModel):
    """`agent_run.evidence.react_trace` 한 항목. prompt 원문·credential 없음."""

    seq: int = Field(ge=1)
    thought: str = Field(min_length=1, max_length=_THOUGHT_MAX)
    tool: ReactNext
    arguments_digest: str | None = Field(default=None, min_length=64, max_length=64)
    arguments_summary: str | None = Field(default=None, max_length=200)
    observation: str | None = Field(default=None, max_length=_OBSERVATION_MAX)
    guard: str | None = Field(default=None, max_length=64)
    llm_input_tokens: int = Field(ge=0)
    llm_output_tokens: int = Field(ge=0)


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


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_fdc(result: FdcSummaryToolResult | None) -> str | None:
    if result is None or not result.ok or result.wafer is None:
        return None
    flagged = [
        f"{item.parameter_id}(ooc={item.ooc_point_cnt},oos={item.oos_point_cnt})"
        for item in result.parameters
        if item.ooc_point_cnt or item.oos_point_cnt
    ]
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
) -> str | None:
    """코드가 강제하는 안전 가드. 위반 코드를 돌려주고 None이면 허용."""

    if selection.next == "stop":
        return None
    if context.remaining_tool_calls <= 0:
        return "REACT_GUARD_BUDGET_EXHAUSTED"
    if selection.next == "get_fdc_summary":
        target = selection.arguments.lot_hist_id
        if target is None or target not in context.allowed_lot_hist_ids:
            return "REACT_GUARD_TARGET_NOT_ALLOWED"
        if target in context.fetched_lot_hist_ids:
            return "REACT_GUARD_TARGET_REPEATED"
        return None
    if selection.next == "search_documents":
        query = selection.arguments.query
        if not query or not query.strip():
            return "REACT_GUARD_QUERY_EMPTY"
        if len(query) > _QUERY_MAX or any(ch in _FORBIDDEN_QUERY_CHARS for ch in query):
            return "REACT_GUARD_QUERY_INVALID"
        return None
    if selection.next == "get_equipment_context":
        if equipment_fetched:
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
        "thought는 200자 이내 한국어로 왜 그 행동이 필요한지 적습니다. "
        "arguments는 사용하지 않는 키를 null로 둡니다. 반드시 JSON 스키마만 출력합니다."
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
        input_tokens=completion.usage.input_tokens,
        output_tokens=completion.usage.output_tokens,
    )


def select_next_step(context: ReactContext) -> ReactSelectionOutcome:
    """LLM 1회 호출로 다음 행동을 고른다. 구조 위반은 fail-closed."""

    messages = build_react_select_messages(context)
    try:
        completion = llm.chat_with_usage(messages, json_schema=REACT_SELECT_SCHEMA)
    except llm.LlmTimeoutError as exc:
        raise ReactSelectionError("LLM_TIMEOUT") from exc
    except (llm.LlmNotReadyError, llm.LlmDependencyError) as exc:
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
    )


def trace_entry(
    *,
    seq: int,
    selection: ReactSelection,
    usage: LlmUsage,
    observation: str | None,
    guard: str | None,
) -> ReactStep:
    summary: str | None = None
    if selection.next == "get_fdc_summary":
        summary = f"lot_hist_id={selection.arguments.lot_hist_id}"
    elif selection.next == "search_documents":
        summary = _clip(f"query={selection.arguments.query or ''}", 200)
    return ReactStep(
        seq=seq,
        thought=_clip(selection.thought, _THOUGHT_MAX),
        tool=selection.next,
        arguments_digest=(
            None if selection.next == "stop" else arguments_digest(selection.arguments)
        ),
        arguments_summary=summary,
        observation=(
            None if observation is None else _clip(observation, _OBSERVATION_MAX)
        ),
        guard=guard,
        llm_input_tokens=usage.input_tokens,
        llm_output_tokens=usage.output_tokens,
    )


def trace_payload(steps: Sequence[ReactStep]) -> list[dict[str, Any]]:
    return [step.model_dump(mode="json") for step in steps]


def trace_from_payload(payload: object) -> tuple[ReactStep, ...]:
    if not isinstance(payload, list):
        return ()
    return tuple(
        ReactStep.model_validate(item) for item in payload if isinstance(item, Mapping)
    )
