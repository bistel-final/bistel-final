"""V5-C-7.1 격리 비교 전용 graph factory.

Production ``AgentRuntime``·환경 opt-in을 우회하는 기능이 아니라, 외부 효과 직전의
``decide_action`` seam에서 반드시 멈추는 주입형 실험 조립이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.agent import decision
from app.agent.graph import AgentGraphDependencies, build_agent_graph
from app.agent.routing import GraphBoundary
from app.agent.state import AgentNodePorts
from app.agent.tools import AuditedToolExecutor, TransactionFactory


def _unreachable(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("EXPERIMENT_EXTERNAL_EFFECT_FORBIDDEN")


@dataclass(frozen=True, slots=True)
class _ExperimentPorts:
    generate_hypothesis: Any
    react_select: Any
    decide_action: Any = decision.decide_action
    persist_action: Any = _unreachable
    notify_email: Any = _unreachable
    approval_email: Any = _unreachable
    hitl_interrupt: Any = _unreachable
    publish_mes: Any = _unreachable
    writeback_result: Any = _unreachable
    cancel_mes: Any = _unreachable


class LevelExperimentGraph:
    """호출자가 factory에서 고정한 level을 실행 중 바꾸지 못하게 한다."""

    def __init__(self, graph: Any, level: int) -> None:
        self._graph = graph
        self.level = level

    def invoke(self, values: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        requested = values.get("autonomy_level", self.level)
        if requested != self.level:
            raise ValueError("EXPERIMENT_LEVEL_MISMATCH")
        return self._graph.invoke(
            {**values, "autonomy_level": self.level}, *args, **kwargs
        )

    def get_state(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph.get_state(*args, **kwargs)


def build_level_graph(
    level: int,
    *,
    selector_port: Any,
    hypothesis_port: Any,
    clock: Any,
    tools: AuditedToolExecutor,
    transactions: TransactionFactory,
    routing_graph: GraphBoundary,
    configured_llm_model: str,
    checkpointer: Any | None = None,
) -> LevelExperimentGraph:
    """동일 주입 경계로 L1·L2·L3을 만들고 조치 영속화 전에 중단한다."""

    if level not in (1, 2, 3):
        raise ValueError("EXPERIMENT_LEVEL_INVALID")
    if level == 3 and selector_port is None:
        raise ValueError("EXPERIMENT_SELECTOR_REQUIRED")
    if level != 3 and selector_port is not None:
        raise ValueError("EXPERIMENT_SELECTOR_FORBIDDEN")
    if not isinstance(configured_llm_model, str) or not configured_llm_model.strip():
        raise ValueError("EXPERIMENT_MODEL_INVALID")
    if not callable(clock):
        raise ValueError("EXPERIMENT_CLOCK_INVALID")

    ports: AgentNodePorts = _ExperimentPorts(  # type: ignore[assignment]
        generate_hypothesis=hypothesis_port,
        react_select=selector_port,
    )
    graph = build_agent_graph(
        AgentGraphDependencies(
            transactions=transactions,
            tools=tools,
            routing_graph=routing_graph,
            ports=ports,
            configured_llm_model=configured_llm_model,
            now=clock,
        ),
        checkpointer=checkpointer or MemorySaver(),
        interrupt_after=("decide_action",),
    )
    return LevelExperimentGraph(graph, level)


__all__ = ["LevelExperimentGraph", "build_level_graph"]
