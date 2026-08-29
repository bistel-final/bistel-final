"""LangGraph Level 1·2 실행 골격 (`V5-C-2.1`, 담당 방대혁 C).

이 Task는 위상과 안전 경계를 소유한다. 가설·정책·영속화·delivery 구현은 9개 port로
남기며, 미배선 production port는 성공처럼 통과하지 않고 ``PORT_NOT_WIRED``로 끝난다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent.approval_store import (
    EmailTransportError,
    HitlDeliveryError,
    HitlResumeError,
)
from app.agent.checkpoint import AgentCheckpointError, normalize_thread_id
from app.agent.hypothesis import HypothesisGenerationError
from app.agent.mes_delivery import MesDeliveryError
from app.agent.prompts import PROMPT_VERSION
from app.agent.rehydration import RehydrationSeed
from app.agent.repository import (
    AgentRepositoryError,
    PredictionRow,
    RepositoryConflict,
    RepositoryContractError,
    active_run_latency_ms,
    finish_agent_run,
    get_agent_run,
    get_prediction_or_none,
    insert_prediction,
    lock_agent_run,
    merge_run_action_provenance,
    record_run_llm_usage,
)
from app.agent.routing import (
    GraphBoundary,
    combine_route,
    read_route_snapshot,
)
from app.agent.run_guard import start_incident_run
from app.agent.state import (
    ActionDecision,
    AgentError,
    AgentGraphInput,
    AgentGraphState,
    AgentNodePorts,
    CompletedAgentState,
    DeliveryPlan,
    Hypothesis,
    HypothesisOutcome,
    PersistResult,
    ToolBudget,
)
from app.agent.tools import (
    AuditedToolExecutor,
    ToolBoundaryError,
    ToolBudgetBlocked,
    TransactionFactory,
)
from app.common.enums import ActionCode, AlarmSource, Decision, RunStatus
from app.common.exceptions import AppError
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    DocumentSearchToolInput,
    EquipmentContextToolInput,
    FdcSummaryToolInput,
    SendActionToolInput,
    SendActionToolResult,
    ToolResult,
)

CANONICAL_NODES: Final[tuple[str, ...]] = (
    "load_incident",
    "collect_fdc",
    "collect_equipment",
    "collect_documents",
    "generate_hypothesis",
    "decide_action",
    "persist_action",
    "notify_email",
    "approval_email",
    "hitl_interrupt",
    "publish_mes",
    "writeback_result",
    "cancel_mes",
    "finalize",
)
INTERNAL_NODES: Final[tuple[str, ...]] = ("fail_run",)
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PortNotWiredError(RuntimeError):
    """후속 Task port가 아직 연결되지 않은 정상적인 fail-closed 상태."""

    code = "PORT_NOT_WIRED"


class AgentGraphInputError(ValueError):
    """run 생성 전에 거부하는 sanitized 입력 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _UnwiredPorts:
    """production 기본값. 어떤 downstream 부작용도 흉내 내지 않는다."""

    @staticmethod
    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise PortNotWiredError

    generate_hypothesis = _fail
    decide_action = _fail
    persist_action = _fail
    notify_email = _fail
    approval_email = _fail
    hitl_interrupt = _fail
    publish_mes = _fail
    writeback_result = _fail
    cancel_mes = _fail


@dataclass(frozen=True, slots=True)
class AgentGraphDependencies:
    """그래프가 소유하지 않는 DB·Tool·routing·후속 node 경계."""

    transactions: TransactionFactory
    tools: AuditedToolExecutor
    routing_graph: GraphBoundary
    ports: AgentNodePorts | None = None
    configured_llm_model: str | None = None
    require_bound_thread: bool = False
    now: Callable[[], datetime] = field(default_factory=lambda: _utc_now)


class CompiledAgentGraph:
    """성공 ``invoke`` 결과에서 실행 전용 channel을 제거하는 얇은 proxy.

    checkpoint 자체에는 내부 channel이 남아 재개에 쓰인다. 호출자에게 돌려주는 성공
    결과만 canonical 20개로 projection하며, 실패·향후 interrupt의 부분 State는 원형을
    유지한다. 완전성 판정은 여전히 ``finalize``의 명시 검증이 담당한다.
    """

    def __init__(self, compiled: Any, *, project_completed: bool) -> None:
        self._compiled = compiled
        self._project_completed = project_completed

    def _project(self, result: Any) -> Any:
        if not self._project_completed:
            # interrupt_after 구성은 내부 channel이 호출자의 재개 판단 근거다.
            return result
        if not isinstance(result, dict) or result.get("terminal_error") is not None:
            return result
        if not all(name in result for name in CompletedAgentState.model_fields):
            return result
        return {name: result[name] for name in CompletedAgentState.model_fields}

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self._project(self._compiled.invoke(*args, **kwargs))

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return self._project(await self._compiled.ainvoke(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._compiled, name)


def _classify_exception(exc: Exception) -> str:
    """예외 타입·sanitized 속성만 본다. ``str(exc)``는 읽지 않는다."""

    if isinstance(exc, AgentRepositoryError):
        return exc.code
    if isinstance(exc, HypothesisGenerationError):
        return exc.code
    if isinstance(exc, AgentCheckpointError):
        return exc.reason_code
    if isinstance(exc, ToolBoundaryError):
        return exc.code
    if isinstance(exc, PortNotWiredError):
        return exc.code
    if isinstance(exc, AgentGraphInputError):
        return exc.code
    if isinstance(exc, MesDeliveryError):
        return exc.code
    if isinstance(exc, ValidationError):
        return "STATE_CONTRACT_ERROR"
    if isinstance(exc, ValueError):
        return "NODE_CONTRACT_ERROR"
    return "NODE_EXECUTION_ERROR"


def _terminal(exc: Exception, node: str) -> AgentError:
    return AgentError(code=_classify_exception(exc), node=node, terminal=True)


def _append_nonterminal_error(
    state: AgentGraphState,
    *,
    result: ToolResult | None,
    node: str,
) -> tuple[AgentError, ...]:
    existing = state.get("errors", ())
    if result is not None and result.ok:
        return existing
    if result is None:
        code = "TOOL_CALL_FAILED"
    else:
        code = result.reason.partition(":")[0]
    return (*existing, AgentError(code=code, node=node, terminal=False))


def _collect_tool_result(
    state: AgentGraphState,
    *,
    node: str,
    invoke: Callable[[], ToolResult | None],
    budget: Callable[[], ToolBudget],
) -> tuple[ToolResult | None, ToolBudget, tuple[AgentError, ...]]:
    """예산 차단만 nonterminal exact code로 바꾸고 나머지는 상위 안전 경계에 둔다."""

    try:
        result = invoke()
    except ToolBudgetBlocked as exc:
        errors = (
            *state.get("errors", ()),
            AgentError(code=exc.code, node=node, terminal=False),
        )
        return None, exc.budget, errors
    return (
        result,
        budget(),
        _append_nonterminal_error(state, result=result, node=node),
    )


def _safe_node(
    name: str,
    fn: Callable[[AgentGraphState], dict[str, Any]],
) -> Callable[[AgentGraphState], dict[str, Any]]:
    def wrapped(state: AgentGraphState) -> dict[str, Any]:
        try:
            return fn(state)
        except ToolBudgetBlocked as exc:
            # 예산 차단은 호출 위치와 무관하게 Tool을 실행하지 않았다는 뜻이다.
            # 각 node의 공용 수집 helper가 놓친 미래 send_action 경로도 terminal로
            # 바꾸지 않는 최후의 안전 경계다.
            error = AgentError(code=exc.code, node=name, terminal=False)
            return {
                "tool_budget": exc.budget,
                "errors": (*state.get("errors", ()), error),
            }
        except Exception as exc:
            error = _terminal(exc, name)
            return {
                "terminal_error": error,
                "errors": (*state.get("errors", ()), error),
            }

    return wrapped


def _approval_email_node(
    fn: Callable[[AgentGraphState], dict[str, Any]],
) -> Callable[[AgentGraphState], dict[str, Any]]:
    """email transport만 nonterminal로 기록하고 나머지는 checkpoint 앞에 남긴다."""

    def wrapped(state: AgentGraphState) -> dict[str, Any]:
        try:
            return fn(state)
        except EmailTransportError as exc:
            error = AgentError(code=exc.code, node="approval_email", terminal=False)
            return {"errors": (*state.get("errors", ()), error)}
        except Exception as exc:
            # `_safe_node→fail_run→END`로 보내면 WAITING DB는 남아도 checkpoint가
            # terminal이 된다. 원문 없이 재상승해 직전 성공 checkpoint에서 재시도한다.
            raise HitlDeliveryError(_classify_exception(exc)) from None

    return wrapped


def _hitl_interrupt_node(
    fn: Callable[[AgentGraphState], dict[str, Any]],
) -> Callable[[AgentGraphState], dict[str, Any]]:
    """pending 오진입은 run을 죽이지 않고 같은 checkpoint에 남긴다."""

    def wrapped(state: AgentGraphState) -> dict[str, Any]:
        try:
            return fn(state)
        except RepositoryConflict as exc:
            if exc.code == "APPROVAL_STILL_PENDING":
                raise HitlResumeError(exc.code) from None
            error = _terminal(exc, "hitl_interrupt")
        except Exception as exc:
            error = _terminal(exc, "hitl_interrupt")
        return {
            "terminal_error": error,
            "errors": (*state.get("errors", ()), error),
        }

    return wrapped


def _entry_node(
    fn: Callable[[AgentGraphState, dict[str, Any] | None], dict[str, Any]],
) -> Callable[[AgentGraphState, dict[str, Any] | None], dict[str, Any]]:
    """입력 오류만 그대로 두고 run 생성 전 의존성 예외는 sanitize한다."""

    def wrapped(
        state: AgentGraphState, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            return fn(state, config)
        except (AgentGraphInputError, AppError):
            raise
        except Exception as exc:
            raise AgentGraphInputError(_classify_exception(exc)) from None

    return wrapped


def _route_or_fail(target: str) -> Callable[[AgentGraphState], str]:
    def route(state: AgentGraphState) -> str:
        return "fail_run" if state.get("terminal_error") is not None else target

    return route


def _canonical_payload(state: AgentGraphState) -> dict[str, Any]:
    # LangGraph checkpoint는 값이 None인 optional channel을 물리 State에서 생략할 수
    # 있다. nullable은 None으로 복원하고, 실제 필수 channel 누락은 아래 Pydantic
    # 완료 검증이 거부하게 한다.
    return {name: state.get(name) for name in CompletedAgentState.model_fields}


def _required_state_id(state: AgentGraphState, name: str) -> str:
    value = state.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name.upper()}_MISSING")
    return value


def _prediction_hypothesis(row: PredictionRow) -> Hypothesis:
    """DB prediction과 compact evidence를 canonical Hypothesis로 복원한다."""

    evidence = row.evidence
    if evidence.get("schema_version") != "agent-evidence-v1":
        raise RepositoryConflict("PREDICTION_CONFLICT")
    try:
        return Hypothesis(
            predicted_fault_code=row.predicted_fault_code,
            confidence=row.confidence,
            cause_summary=row.cause_summary,
            supporting_alarms=tuple(
                AlarmRef.model_validate(item)
                for item in evidence.get("supporting_alarms", ())
            ),
            supporting_chunk_ids=tuple(evidence.get("supporting_chunk_ids", ())),
            supporting_relation_ids=tuple(evidence.get("supporting_relation_ids", ())),
            uncertainty=evidence.get("uncertainty", ""),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepositoryConflict("PREDICTION_CONFLICT") from exc


def _prediction_evidence(hypothesis: Hypothesis) -> dict[str, Any]:
    return {
        "schema_version": "agent-evidence-v1",
        "supporting_alarms": [
            alarm.model_dump(mode="json") for alarm in hypothesis.supporting_alarms
        ],
        "supporting_chunk_ids": list(hypothesis.supporting_chunk_ids),
        "supporting_relation_ids": list(hypothesis.supporting_relation_ids),
        "uncertainty": hypothesis.uncertainty,
    }


def _assert_prediction_provenance(
    row: PredictionRow,
    *,
    run_model: str | None,
    run_prompt_version: str | None,
    expected_model: str | None = None,
) -> None:
    if row.prompt_version != PROMPT_VERSION or run_prompt_version != PROMPT_VERSION:
        raise RepositoryConflict("PREDICTION_CONFLICT")
    if run_model != row.llm_model:
        raise RepositoryConflict("PREDICTION_CONFLICT")
    if expected_model is not None and row.llm_model != expected_model:
        raise RepositoryConflict("PREDICTION_CONFLICT")


def _document_query(state: AgentGraphState) -> str:
    """가설·label 없이 이미 수집된 식별자만으로 검색문을 만든다."""

    representative = state["representative_alarm"]
    route = state["route"]
    step_ids = tuple(
        dict.fromkeys(
            item.process_step_id
            for item in route.graph_evidence
            if item.process_step_id is not None
        )
    )
    fdc = state.get("fdc_evidence")
    parameter_ids = ()
    if fdc is not None and fdc.ok:
        parameter_ids = tuple(item.parameter_id for item in fdc.parameters)
    fields = (
        representative.source.value,
        representative.alarm_id,
        state["chamber_id"],
        *step_ids,
        *parameter_ids,
    )
    return " ".join(dict.fromkeys(str(field) for field in fields))[:1000]


def build_agent_graph(
    dependencies: AgentGraphDependencies,
    *,
    checkpointer: Any | None = None,
    interrupt_after: tuple[str, ...] | None = None,
) -> Any:
    """canonical 14 node와 내부 ``fail_run`` 하나를 조립한다."""

    if interrupt_after and checkpointer is None:
        raise ValueError("HITL_CHECKPOINTER_REQUIRED")
    if interrupt_after and dependencies.ports is None:
        raise ValueError("HITL_PORTS_REQUIRED")

    ports: AgentNodePorts = (  # type: ignore[assignment]
        dependencies.ports if dependencies.ports is not None else _UnwiredPorts()
    )

    def run_latency_ms(connection: Any, run_id: str) -> int:
        """저장 subtotal과 현재 활성 구간에서 사람 대기를 제외해 계산한다."""

        return active_run_latency_ms(
            get_agent_run(connection, run_id),
            now=dependencies.now(),
        )

    def _finish_failed(
        connection: Any,
        run_id: str,
        *,
        error_code: str,
    ) -> None:
        """FAILED 두 경로가 action provenance를 같은 방식으로 보존하게 한다."""

        merged = merge_run_action_provenance(
            connection,
            run_id,
            terminal_evidence={"code": error_code},
        )
        latency_ms = run_latency_ms(connection, run_id)
        finish_agent_run(
            connection,
            run_id,
            RunStatus.FAILED,
            evidence=merged.evidence,
            latency_ms=latency_ms,
        )

    def load_incident(
        state: AgentGraphState,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """run·route snapshot을 한 UoW에서 확정하고 DB를 놓은 뒤 graph를 읽는다."""

        level = state.get("autonomy_level")
        if not isinstance(level, int) or isinstance(level, bool):
            raise AgentGraphInputError("AUTONOMY_LEVEL_INVALID")
        if level not in (1, 2):
            if level == 3:
                raise AgentGraphInputError("AUTONOMY_LEVEL_NOT_IMPLEMENTED")
            raise AgentGraphInputError("AUTONOMY_LEVEL_INVALID")
        try:
            requested = AlarmRef.model_validate(state.get("requested_alarm"))
        except ValidationError as exc:
            raise AgentGraphInputError("REQUESTED_ALARM_INVALID") from exc
        bound_thread: str | None = None
        raw_thread = state.get("thread_id")
        configurable = {} if config is None else config.get("configurable", {})
        config_thread = (
            configurable.get("thread_id") if isinstance(configurable, dict) else None
        )
        if dependencies.require_bound_thread:
            try:
                bound_thread = normalize_thread_id(raw_thread)
                canonical_config = normalize_thread_id(config_thread)
            except AgentCheckpointError as exc:
                raise AgentGraphInputError(exc.reason_code) from exc
            if bound_thread != canonical_config:
                raise AgentGraphInputError("THREAD_BINDING_MISMATCH")
            model = dependencies.configured_llm_model
            if (
                not isinstance(model, str)
                or not model.strip()
                or len(model.strip()) > 64
            ):
                raise AgentGraphInputError("LLM_MODEL_NOT_READY")
        with dependencies.transactions() as connection:
            started = start_incident_run(
                connection,
                requested,
                autonomy_level=level,
                llm_model=dependencies.configured_llm_model,
                prompt_version=PROMPT_VERSION,
                **(
                    {}
                    if bound_thread is None
                    else {"thread_id_factory": lambda: bound_thread}
                ),
            )
            bound = read_route_snapshot(connection, started.incident)
            representative = started.incident.representative_alarm
            fdc_lot_hist_id = bound.snapshot.lot_hist_id_of_member.get(
                (AlarmSource(representative.source), representative.alarm_id)
            )

        base: dict[str, Any] = {
            "run_id": started.run.agent_run_id,
            "thread_id": started.run.thread_id,
            "retry_of_run_id": started.run.retry_of_run_id,
            "requested_alarm": started.incident.requested_alarm,
            "representative_alarm": representative,
            "member_alarms": started.incident.member_alarms,
            "lot_id": started.incident.lot_id,
            "chamber_id": started.incident.chamber_id,
            "fdc_evidence": None,
            "optional_anomaly_evidence": None,
            "graph_evidence": None,
            "document_evidence": None,
            "hypothesis": None,
            "action_decision": None,
            "action_id": None,
            "approval_id": None,
            "deliveries": (),
            # 방금 생성한 run에는 예약 Tool row가 구조적으로 0건이다. 첫 호출부터는
            # AuditedToolExecutor가 실제 count_tool_calls() 값으로 교체한다.
            "tool_budget": ToolBudget(used=0),
            "errors": (),
            "autonomy_level": level,
            "terminal_error": None,
            "approval_decision": None,
            "pending_llm_usage": None,
        }
        if fdc_lot_hist_id is None:
            error = _terminal(
                RepositoryContractError("ROUTE_INCIDENT_MISMATCH"),
                "load_incident",
            )
            base["terminal_error"] = error
            base["errors"] = (error,)
            return base
        base["fdc_lot_hist_id"] = fdc_lot_hist_id
        try:
            base["route"] = combine_route(bound, graph=dependencies.routing_graph)
        except Exception as exc:
            error = _terminal(exc, "load_incident")
            base["terminal_error"] = error
            base["errors"] = (error,)
        return base

    def collect_fdc(state: AgentGraphState) -> dict[str, Any]:
        result, tool_budget, errors = _collect_tool_result(
            state,
            node="collect_fdc",
            invoke=lambda: dependencies.tools.fdc_summary(
                state["run_id"],
                FdcSummaryToolInput(lot_hist_id=state["fdc_lot_hist_id"]),
            ),
            budget=lambda: dependencies.tools.budget(state["run_id"]),
        )
        return {
            "fdc_evidence": result,
            "optional_anomaly_evidence": None if result is None else result.anomaly,
            "tool_budget": tool_budget,
            "errors": errors,
        }

    def collect_equipment(state: AgentGraphState) -> dict[str, Any]:
        result, tool_budget, errors = _collect_tool_result(
            state,
            node="collect_equipment",
            invoke=lambda: dependencies.tools.equipment_context(
                state["run_id"],
                EquipmentContextToolInput(chamber_id=state["chamber_id"]),
            ),
            budget=lambda: dependencies.tools.budget(state["run_id"]),
        )
        return {
            "graph_evidence": result,
            "tool_budget": tool_budget,
            "errors": errors,
        }

    def collect_documents(state: AgentGraphState) -> dict[str, Any]:
        graph = state.get("graph_evidence")
        if graph is not None and graph.ok:
            model_code = graph.model_code
        else:
            route_graph = next(
                (
                    item
                    for item in state["route"].graph_evidence
                    if item.chamber_id == state["chamber_id"]
                ),
                None,
            )
            model_code = None if route_graph is None else route_graph.model_code
        result, tool_budget, errors = _collect_tool_result(
            state,
            node="collect_documents",
            invoke=lambda: dependencies.tools.document_search(
                state["run_id"],
                DocumentSearchToolInput(
                    query=_document_query(state),
                    model_code=model_code,
                ),
            ),
            budget=lambda: dependencies.tools.budget(state["run_id"]),
        )
        return {
            "document_evidence": result,
            "tool_budget": tool_budget,
            "errors": errors,
        }

    def generate_hypothesis(state: AgentGraphState) -> dict[str, Any]:
        run_id = _required_state_id(state, "run_id")

        # checkpoint 재개 전 이미 commit된 prediction이 있으면 LLM을 다시
        # 호출하지 않는다. provenance 불일치는 재사용하지 않는다.
        with dependencies.transactions() as connection:
            stored = get_prediction_or_none(connection, run_id)
            if stored is not None:
                run = get_agent_run(connection, run_id)
                _assert_prediction_provenance(
                    stored,
                    run_model=run.llm_model,
                    run_prompt_version=run.prompt_version,
                )
                return {
                    "hypothesis": _prediction_hypothesis(stored),
                    "pending_llm_usage": None,
                }

        outcome: HypothesisOutcome | None = None
        try:
            outcome = HypothesisOutcome.model_validate(
                ports.generate_hypothesis(
                    state.get("fdc_evidence"),
                    state.get("graph_evidence"),
                    state.get("document_evidence"),
                    state["route"],
                )
            )
        except HypothesisGenerationError as exc:
            error = _terminal(exc, "generate_hypothesis")
            return {
                "terminal_error": error,
                "errors": (*state.get("errors", ()), error),
                "pending_llm_usage": exc.usage_or_none,
            }

        try:
            with dependencies.transactions() as connection:
                run = lock_agent_run(connection, run_id)
                stored = get_prediction_or_none(connection, run_id)
                if stored is None:
                    if run.prompt_version != PROMPT_VERSION:
                        raise RepositoryConflict("PREDICTION_CONFLICT")
                    if (
                        run.llm_model is not None
                        and run.llm_model != outcome.llm_usage.model
                    ):
                        raise RepositoryConflict("PREDICTION_CONFLICT")
                    stored = insert_prediction(
                        connection,
                        agent_run_id=run_id,
                        predicted_fault_code=outcome.hypothesis.predicted_fault_code,
                        confidence=outcome.hypothesis.confidence,
                        cause_summary=outcome.hypothesis.cause_summary,
                        evidence=_prediction_evidence(outcome.hypothesis),
                        llm_model=outcome.llm_usage.model,
                        prompt_version=outcome.llm_usage.prompt_version,
                    )
                else:
                    _assert_prediction_provenance(
                        stored,
                        run_model=run.llm_model,
                        run_prompt_version=run.prompt_version,
                        expected_model=outcome.llm_usage.model,
                    )
                record_run_llm_usage(
                    connection,
                    run_id,
                    llm_model=outcome.llm_usage.model,
                    prompt_version=outcome.llm_usage.prompt_version,
                    input_tokens=outcome.llm_usage.input_tokens,
                    output_tokens=outcome.llm_usage.output_tokens,
                )
                hypothesis = _prediction_hypothesis(stored)
        except AgentRepositoryError as exc:
            error = _terminal(exc, "generate_hypothesis")
            return {
                "terminal_error": error,
                "errors": (*state.get("errors", ()), error),
                "pending_llm_usage": outcome.llm_usage,
            }
        return {"hypothesis": hypothesis, "pending_llm_usage": None}

    def decide_action(state: AgentGraphState) -> dict[str, Any]:
        return {
            "action_decision": ActionDecision.model_validate(
                ports.decide_action(state["route"])
            )
        }

    def persist_action(state: AgentGraphState) -> dict[str, Any]:
        decision = state["action_decision"]
        if decision is None:
            raise ValueError("ACTION_DECISION_MISSING")
        result = PersistResult.model_validate(
            ports.persist_action(
                state["run_id"],
                decision,
                RehydrationSeed.from_state(state),
            )
        )
        result.assert_matches(decision)
        return {
            "action_id": result.action_id,
            "approval_id": result.approval_id,
            "deliveries": result.deliveries,
        }

    # 감사 증적의 node 이름은 채널이 아니다. 세 delivery node는 같은
    # send_stored_action을 부르고 채널은 DB 저장 plan이 결정하므로, HITL 복구
    # 재생에서는 approval_email node가 MES 발행을 수행할 수 있다. 실제 채널은
    # Tool output의 effect_channel로 판정한다.
    def send_stored_action(
        state: AgentGraphState,
        *,
        node: str,
    ) -> dict[str, Any]:
        result, tool_budget, errors = _collect_tool_result(
            state,
            node=node,
            invoke=lambda: dependencies.tools.send_action(
                _required_state_id(state, "run_id"),
                SendActionToolInput(action_id=_required_state_id(state, "action_id")),
            ),
            budget=lambda: dependencies.tools.budget(state["run_id"]),
        )
        update: dict[str, Any] = {
            "tool_budget": tool_budget,
            "errors": errors,
        }
        if isinstance(result, SendActionToolResult) and result.ok:
            update["deliveries"] = tuple(
                DeliveryPlan(channel=item.channel, status=item.status)
                for item in result.deliveries
            )
        return update

    def notify_email(state: AgentGraphState) -> dict[str, Any]:
        return send_stored_action(state, node="notify_email")

    def approval_email(state: AgentGraphState) -> dict[str, Any]:
        return send_stored_action(state, node="approval_email")

    def hitl_interrupt(state: AgentGraphState) -> dict[str, Any]:
        decision = Decision(
            ports.hitl_interrupt(_required_state_id(state, "approval_id"))
        )
        return {
            "approval_decision": decision,
            "errors": state.get("errors", ()),
        }

    def publish_mes(state: AgentGraphState) -> dict[str, Any]:
        return send_stored_action(state, node="publish_mes")

    def writeback_result(state: AgentGraphState) -> dict[str, Any]:
        return {
            "deliveries": tuple(
                DeliveryPlan.model_validate(item)
                for item in ports.writeback_result(
                    _required_state_id(state, "action_id")
                )
            )
        }

    def cancel_mes(state: AgentGraphState) -> dict[str, Any]:
        return {
            "deliveries": tuple(
                DeliveryPlan.model_validate(item)
                for item in ports.cancel_mes(_required_state_id(state, "action_id"))
            )
        }

    def finalize(state: AgentGraphState) -> dict[str, Any]:
        with dependencies.transactions() as connection:
            # 재개 뒤 Tool node가 없어도 완료 State는 checkpoint의 stale 표시값이 아니라
            # 같은 terminal transaction에서 읽은 DB 정본으로 확정한다.
            payload = _canonical_payload(state)
            payload["tool_budget"] = dependencies.tools.budget_from_connection(
                connection,
                state["run_id"],
            )
            # 검증이 COMMITTED 전이어야 한다.
            completed = CompletedAgentState.model_validate(payload)
            merged = merge_run_action_provenance(
                connection,
                completed.run_id,
                terminal_evidence={
                    "route_consistency": completed.route.route_consistency,
                    "error_codes": [error.code for error in completed.errors],
                },
            )
            latency_ms = run_latency_ms(connection, completed.run_id)
            finish_agent_run(
                connection,
                completed.run_id,
                RunStatus.COMPLETED,
                evidence=merged.evidence,
                latency_ms=latency_ms,
            )
        return {name: getattr(completed, name) for name in completed.model_fields}

    def fail_run(state: AgentGraphState) -> dict[str, Any]:
        error = state.get("terminal_error")
        if not isinstance(error, AgentError):
            error = AgentError(
                code="TERMINAL_ERROR_MISSING",
                node="fail_run",
                terminal=True,
            )
        errors = state.get("errors", ())
        if error not in errors:
            errors = (*errors, error)
        run_id = state.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            persistence_error = AgentError(
                code="FAILED_RUN_ID_MISSING",
                node="fail_run",
                terminal=True,
            )
            return {
                "terminal_error": error,
                "errors": (*errors, persistence_error),
            }
        usage_persistence_exc: Exception | None = None
        try:
            with dependencies.transactions() as connection:
                pending_usage = state.get("pending_llm_usage")
                if pending_usage is not None:
                    try:
                        record_run_llm_usage(
                            connection,
                            run_id,
                            llm_model=pending_usage.model,
                            prompt_version=pending_usage.prompt_version,
                            input_tokens=pending_usage.input_tokens,
                            output_tokens=pending_usage.output_tokens,
                        )
                    except Exception as exc:
                        # SQL 오류면 현재 transaction이 aborted됐을 수 있으므로 여기서
                        # FAILED 전이를 계속하지 않고 바깥의 새 UoW로 넘긴다.
                        usage_persistence_exc = exc
                        raise
                _finish_failed(connection, run_id, error_code=error.code)
        except Exception as exc:
            if usage_persistence_exc is not None:
                usage_error = _terminal(usage_persistence_exc, "fail_run")
                errors = (*errors, usage_error)
                logger.error(
                    "failed-run usage persistence failed (code=%s)",
                    usage_error.code,
                )
                try:
                    # usage UoW는 rollback시킨 뒤 fresh transaction에서 terminal
                    # 전이만 수행해 RUNNING 고착을 막는다.
                    with dependencies.transactions() as connection:
                        _finish_failed(connection, run_id, error_code=error.code)
                except Exception as finish_exc:
                    persistence_error = _terminal(finish_exc, "fail_run")
                    logger.error(
                        "failed-run terminal persistence failed (code=%s)",
                        persistence_error.code,
                    )
                    return {
                        "terminal_error": error,
                        "errors": (*errors, persistence_error),
                    }
                return {"terminal_error": error, "errors": errors}
            # fail_run은 자기 자신으로 재라우팅할 수 없다. 원문을 호출자에게 올리지
            # 않고 최초 terminal 원인은 보존하되, 영속화 실패를 별도 sanitized
            # error로 남긴다. stale RUNNING 회수는 설계 §12.3 복구 정책의 몫이다.
            persistence_error = _terminal(exc, "fail_run")
            logger.error(
                "failed-run terminal persistence failed (code=%s)",
                persistence_error.code,
            )
            return {
                "terminal_error": error,
                "errors": (*errors, persistence_error),
            }
        return {"terminal_error": error, "errors": errors}

    graph = StateGraph(AgentGraphState, input=AgentGraphInput)
    graph.add_node("load_incident", _entry_node(load_incident))
    implementations = {
        "collect_fdc": collect_fdc,
        "collect_equipment": collect_equipment,
        "collect_documents": collect_documents,
        "generate_hypothesis": generate_hypothesis,
        "decide_action": decide_action,
        "persist_action": persist_action,
        "notify_email": notify_email,
        "approval_email": approval_email,
        "hitl_interrupt": hitl_interrupt,
        "publish_mes": publish_mes,
        "writeback_result": writeback_result,
        "cancel_mes": cancel_mes,
        "finalize": finalize,
    }
    for name, implementation in implementations.items():
        if name == "approval_email":
            graph.add_node(name, _approval_email_node(implementation))
        elif name == "hitl_interrupt":
            graph.add_node(name, _hitl_interrupt_node(implementation))
        else:
            graph.add_node(name, _safe_node(name, implementation))
    graph.add_node("fail_run", fail_run)

    graph.add_edge(START, "load_incident")
    graph.add_conditional_edges(
        "load_incident",
        _route_or_fail("collect_fdc"),
        {"collect_fdc": "collect_fdc", "fail_run": "fail_run"},
    )

    def after_fdc(state: AgentGraphState) -> str:
        if state.get("terminal_error") is not None:
            return "fail_run"
        route = state["route"]
        covered = any(
            evidence.chamber_id == state["chamber_id"]
            for evidence in route.graph_evidence
        )
        if state["autonomy_level"] == 2 and route.route_consistency and covered:
            return "collect_documents"
        return "collect_equipment"

    graph.add_conditional_edges(
        "collect_fdc",
        after_fdc,
        {
            "collect_equipment": "collect_equipment",
            "collect_documents": "collect_documents",
            "fail_run": "fail_run",
        },
    )
    graph.add_conditional_edges(
        "collect_equipment",
        _route_or_fail("collect_documents"),
        {"collect_documents": "collect_documents", "fail_run": "fail_run"},
    )

    linear = (
        ("collect_documents", "generate_hypothesis"),
        ("generate_hypothesis", "decide_action"),
    )
    for source, target in linear:
        graph.add_conditional_edges(
            source,
            _route_or_fail(target),
            {target: target, "fail_run": "fail_run"},
        )

    def after_decision(state: AgentGraphState) -> str:
        if state.get("terminal_error") is not None:
            return "fail_run"
        decision = state.get("action_decision")
        if decision is None:
            return "fail_run"
        return "finalize" if decision.action is None else "persist_action"

    graph.add_conditional_edges(
        "decide_action",
        after_decision,
        {
            "persist_action": "persist_action",
            "finalize": "finalize",
            "fail_run": "fail_run",
        },
    )

    def after_persist(state: AgentGraphState) -> str:
        if state.get("terminal_error") is not None:
            return "fail_run"
        decision: ActionDecision | None = state.get("action_decision")
        if decision is None or decision.action is None:
            return "fail_run"
        return {
            ActionCode.MONITORING: "finalize",
            ActionCode.WARNING: "notify_email",
            ActionCode.EQP_HOLD: "approval_email",
        }[decision.action]

    graph.add_conditional_edges(
        "persist_action",
        after_persist,
        {
            "finalize": "finalize",
            "notify_email": "notify_email",
            "approval_email": "approval_email",
            "fail_run": "fail_run",
        },
    )
    graph.add_conditional_edges(
        "notify_email",
        _route_or_fail("finalize"),
        {"finalize": "finalize", "fail_run": "fail_run"},
    )
    graph.add_conditional_edges(
        "approval_email",
        _route_or_fail("hitl_interrupt"),
        {"hitl_interrupt": "hitl_interrupt", "fail_run": "fail_run"},
    )

    def after_hitl(state: AgentGraphState) -> str:
        if state.get("terminal_error") is not None:
            return "fail_run"
        value = state.get("approval_decision")
        try:
            decision = Decision(value)
        except (TypeError, ValueError):
            return "fail_run"
        if decision is Decision.APPROVE:
            return "publish_mes"
        if decision is Decision.REJECT:
            return "cancel_mes"
        return "fail_run"

    graph.add_conditional_edges(
        "hitl_interrupt",
        after_hitl,
        {
            "publish_mes": "publish_mes",
            "cancel_mes": "cancel_mes",
            "fail_run": "fail_run",
        },
    )
    graph.add_conditional_edges(
        "publish_mes",
        _route_or_fail("writeback_result"),
        {"writeback_result": "writeback_result", "fail_run": "fail_run"},
    )
    for source in ("writeback_result", "cancel_mes"):
        graph.add_conditional_edges(
            source,
            _route_or_fail("finalize"),
            {"finalize": "finalize", "fail_run": "fail_run"},
        )
    graph.add_conditional_edges(
        "finalize",
        _route_or_fail("__end__"),
        {"__end__": END, "fail_run": "fail_run"},
    )
    graph.add_edge("fail_run", END)
    return CompiledAgentGraph(
        graph.compile(
            checkpointer=checkpointer,
            interrupt_after=[] if interrupt_after is None else list(interrupt_after),
        ),
        project_completed=not bool(interrupt_after),
    )


__all__ = [
    "CANONICAL_NODES",
    "INTERNAL_NODES",
    "AgentGraphDependencies",
    "AgentGraphInputError",
    "CompiledAgentGraph",
    "PortNotWiredError",
    "build_agent_graph",
]
