from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from app.agent import experiment


def test_experiment_factory_does_not_import_production_composition() -> None:
    tree = ast.parse(inspect.getsource(experiment))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "app.agent.runtime_composition" not in imports


@pytest.mark.parametrize("level", [0, 4])
def test_experiment_rejects_invalid_level_before_dependencies(level: int) -> None:
    with pytest.raises(ValueError, match="EXPERIMENT_LEVEL_INVALID"):
        experiment.build_level_graph(
            level,
            selector_port=None,
            hypothesis_port=None,
            clock=None,
            tools=None,  # type: ignore[arg-type]
            transactions=None,  # type: ignore[arg-type]
            routing_graph=None,  # type: ignore[arg-type]
            configured_llm_model="fixture",
        )


def test_experiment_selector_is_level_three_only() -> None:
    with pytest.raises(ValueError, match="EXPERIMENT_SELECTOR_FORBIDDEN"):
        experiment.build_level_graph(
            2,
            selector_port=lambda _context: None,
            hypothesis_port=None,
            clock=None,
            tools=None,  # type: ignore[arg-type]
            transactions=None,  # type: ignore[arg-type]
            routing_graph=None,  # type: ignore[arg-type]
            configured_llm_model="fixture",
        )
    with pytest.raises(ValueError, match="EXPERIMENT_SELECTOR_REQUIRED"):
        experiment.build_level_graph(
            3,
            selector_port=None,
            hypothesis_port=None,
            clock=None,
            tools=None,  # type: ignore[arg-type]
            transactions=None,  # type: ignore[arg-type]
            routing_graph=None,  # type: ignore[arg-type]
            configured_llm_model="fixture",
        )


@pytest.mark.parametrize(
    "port_name",
    [
        "persist_action",
        "notify_email",
        "approval_email",
        "hitl_interrupt",
        "publish_mes",
        "writeback_result",
        "cancel_mes",
    ],
)
def test_experiment_external_effect_ports_are_unreachable(port_name: str) -> None:
    ports = experiment._ExperimentPorts(  # type: ignore[attr-defined]
        generate_hypothesis=lambda *_args: None,
        react_select=None,
    )
    with pytest.raises(RuntimeError, match="EXPERIMENT_EXTERNAL_EFFECT_FORBIDDEN"):
        getattr(ports, port_name)()


def test_level_experiment_graph_rejects_runtime_level_override() -> None:
    inner = SimpleNamespace(invoke=lambda *_args, **_kwargs: pytest.fail())
    graph = experiment.LevelExperimentGraph(inner, 3)

    with pytest.raises(ValueError, match="EXPERIMENT_LEVEL_MISMATCH"):
        graph.invoke({"autonomy_level": 2})
