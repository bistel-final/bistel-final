"""`V5-C-3.1` versioned 순수 조치 규칙 회귀."""

from __future__ import annotations

import ast
import inspect
from collections import Counter
from typing import get_type_hints

import pytest

from app.agent import decision as subject
from app.agent.incident import ResolvedIncident
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute
from app.agent.state import ActionDecision
from app.common.enums import ActionCode, AlarmSource, Severity
from app.common.schemas import AlarmRef


def _alarm(source: AlarmSource, suffix: str) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=f"{source.value}-{suffix}")


def _route(
    sources: tuple[AlarmSource, ...],
    *,
    suffix: str = "FIXTURE",
    requested: AlarmRef | None = None,
    representative: AlarmRef | None = None,
    lot_id: str = "LOT-FIXTURE",
    chamber_id: str = "EQP-FIXTURE-PM1",
    graph_evidence: tuple[GraphRouteEvidence, ...] = (),
    route_consistency: bool = True,
) -> ResolvedIncidentRoute:
    members = tuple(
        _alarm(source, f"{suffix}-{index}") for index, source in enumerate(sources)
    )
    fallback = _alarm(AlarmSource.SUMMARY, f"{suffix}-REQUEST")
    return ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id=lot_id,
            chamber_id=chamber_id,
            requested_alarm=requested or (members[0] if members else fallback),
            representative_alarm=representative
            or (members[-1] if members else fallback),
            member_alarms=members,
        ),
        wafer_routes=(),
        graph_evidence=graph_evidence,
        route_consistency=route_consistency,
        mismatches=(),
    )


@pytest.mark.parametrize(
    ("sources", "rule", "action", "severity", "approval"),
    [
        ((), "NO_ALARM", None, None, False),
        (
            (AlarmSource.SUMMARY,),
            "SUMMARY_OOC_ONLY",
            ActionCode.MONITORING,
            Severity.LOW,
            False,
        ),
        (
            (AlarmSource.TRACE,),
            "TRACE_OOS",
            ActionCode.WARNING,
            Severity.MEDIUM,
            False,
        ),
        (
            (AlarmSource.TRACE, AlarmSource.SUMMARY),
            "TRACE_OOS",
            ActionCode.WARNING,
            Severity.MEDIUM,
            False,
        ),
        (
            (AlarmSource.R03,),
            "R03_PRESENT",
            ActionCode.EQP_HOLD,
            Severity.HIGH,
            True,
        ),
        (
            (AlarmSource.R03, AlarmSource.SUMMARY),
            "R03_PRESENT",
            ActionCode.EQP_HOLD,
            Severity.HIGH,
            True,
        ),
        (
            (AlarmSource.R03, AlarmSource.TRACE),
            "R03_PRESENT",
            ActionCode.EQP_HOLD,
            Severity.HIGH,
            True,
        ),
        (
            (AlarmSource.R03, AlarmSource.TRACE, AlarmSource.SUMMARY),
            "R03_PRESENT",
            ActionCode.EQP_HOLD,
            Severity.HIGH,
            True,
        ),
    ],
)
def test_all_source_combinations_follow_the_exact_priority(
    sources: tuple[AlarmSource, ...],
    rule: str,
    action: ActionCode | None,
    severity: Severity | None,
    approval: bool,
) -> None:
    decision = subject.decide_action(_route(sources))

    assert decision.matched_rule == rule
    assert decision.action is action
    assert decision.severity is severity
    assert decision.requires_approval is approval
    assert decision.policy_version == subject.POLICY_VERSION


def test_requested_and_representative_sources_never_override_members() -> None:
    route = _route(
        (AlarmSource.SUMMARY,),
        requested=_alarm(AlarmSource.R03, "REQUESTED"),
        representative=_alarm(AlarmSource.TRACE, "REPRESENTATIVE"),
    )

    assert subject.decide_action(route).matched_rule == "SUMMARY_OOC_ONLY"


def test_same_member_sources_ignore_every_other_route_field() -> None:
    graph_evidence = (
        GraphRouteEvidence(
            chamber_id="EQP-OTHER-PM9",
            equipment_id="EQP-OTHER",
            model_code="MODEL-OTHER",
            process_step_id="CT-OTHER",
            upstream_process_step_ids=("CT-UP",),
            downstream_process_step_ids=("CT-DOWN",),
            relation_ids=("REL-OTHER",),
            graph_revision="REV-OTHER",
        ),
    )
    first = _route(
        (AlarmSource.TRACE,),
        suffix="FIRST",
        requested=_alarm(AlarmSource.R03, "FIRST-REQUEST"),
        representative=_alarm(AlarmSource.SUMMARY, "FIRST-REP"),
        route_consistency=True,
    )
    second = _route(
        (AlarmSource.TRACE,),
        suffix="SECOND",
        requested=_alarm(AlarmSource.SUMMARY, "SECOND-REQUEST"),
        representative=_alarm(AlarmSource.R03, "SECOND-REP"),
        lot_id="LOT-OTHER",
        chamber_id="EQP-OTHER-PM9",
        graph_evidence=graph_evidence,
        route_consistency=False,
    )

    assert subject.decide_action(first) == subject.decide_action(second)


def test_policy_snapshot_and_json_round_trip_are_exact() -> None:
    decision = subject.decide_action(_route((AlarmSource.TRACE,)))
    restored = ActionDecision.model_validate_json(decision.model_dump_json())

    assert subject.POLICY_VERSION == "ACTION-POLICY-V1"
    assert subject.ACTION_POLICY == {
        "version": "ACTION-POLICY-V1",
        "priority": (
            "R03_PRESENT",
            "TRACE_OOS",
            "SUMMARY_OOC_ONLY",
            "NO_ALARM",
        ),
    }
    assert restored.model_dump(mode="json") == decision.model_dump(mode="json")


def test_production_port_is_the_pure_decision_function() -> None:
    assert subject.production_port() is subject.decide_action


def _decision_function() -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(subject))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "decide_action"
    )


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return (current.id, *reversed(parts))


def test_signature_has_only_the_reviewed_route_contract() -> None:
    signature = inspect.signature(subject.decide_action)
    parameters = list(signature.parameters.values())
    hints = get_type_hints(subject.decide_action)

    assert len(parameters) == 1
    assert parameters[0].name == "route"
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[0].default is inspect.Parameter.empty
    assert hints == {
        "route": ResolvedIncidentRoute,
        "return": ActionDecision,
    }


def test_decision_ast_contains_no_forbidden_signal_symbol() -> None:
    forbidden = {"hypothesis", "score", "metrology", "confidence"}
    function = _decision_function()
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    attributes = {
        node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)
    }

    assert not forbidden & (names | attributes)


def test_decision_module_imports_only_the_reviewed_symbols() -> None:
    tree = ast.parse(inspect.getsource(subject))
    actual = {
        (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert actual == {
        ("__future__", "annotations"),
        ("collections.abc", "Callable"),
        ("types", "MappingProxyType"),
        ("typing", "Final"),
        ("app.agent.routing", "ResolvedIncidentRoute"),
        ("app.agent.state", "RULE_TO_ACTION"),
        ("app.agent.state", "ActionDecision"),
        ("app.agent.state", "ActionPolicyVersion"),
        ("app.agent.state", "MatchedRule"),
        ("app.common.enums", "AlarmSource"),
        ("app.common.enums", "requires_approval"),
        ("app.common.enums", "resolve_severity"),
    }
    assert not [node for node in tree.body if isinstance(node, ast.Import)]


def test_action_policy_snapshot_is_read_only() -> None:
    with pytest.raises(TypeError):
        subject.ACTION_POLICY["version"] = "ACTION-POLICY-V999"


def test_route_access_is_exactly_member_alarm_sources() -> None:
    function = _decision_function()
    route_paths = {
        path
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        if (path := _attribute_path(node)) is not None and path[0] == "route"
    }
    comprehensions = [
        node for node in ast.walk(function) if isinstance(node, ast.SetComp)
    ]

    assert route_paths == {
        ("route", "incident"),
        ("route", "incident", "member_alarms"),
    }
    assert len(comprehensions) == 1
    assert _attribute_path(comprehensions[0].elt) == ("alarm", "source")
    assert len(comprehensions[0].generators) == 1
    generator = comprehensions[0].generators[0]
    assert isinstance(generator.target, ast.Name) and generator.target.id == "alarm"
    assert _attribute_path(generator.iter) == (
        "route",
        "incident",
        "member_alarms",
    )
    assert generator.ifs == []


# Source provenance (REFERENCE_NOT_GOLD — 채점 정답 아님):
# project.zip sha256:
# e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3
# action_history.csv sha256:
# 174e8fd71fab0e716e3d8585057e997d17dc03bb9fbedec957df3a146ca213a1
# trace_alarm_history.csv sha256:
# aaa43f9e6af5d45d3cdc4c813f0a07691a426130a09dfdf93a3c2fe9edac6686
# summary_alarm_history.csv sha256:
# cf16301cb5f03f0213fdb816f4ad15b935c0c6c7e6ed6ef20f63eb30c8121d88
# R03 3건은 raw ZIP column이 아니라 strict R03 파생 결과다.
REFERENCE_CASES = (
    ("LOT002", "EQP05-PM2", (AlarmSource.SUMMARY,), ActionCode.MONITORING),
    ("LOT004", "EQP01-PM2", (AlarmSource.SUMMARY,), ActionCode.MONITORING),
    ("LOT007", "EQP04-PM1", (AlarmSource.SUMMARY,), ActionCode.MONITORING),
    ("LOT009", "EQP06-PM1", (AlarmSource.SUMMARY,), ActionCode.MONITORING),
    ("LOT010", "EQP01-PM1", (AlarmSource.SUMMARY,), ActionCode.MONITORING),
    (
        "LOT006",
        "EQP06-PM1",
        (AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.WARNING,
    ),
    (
        "LOT006",
        "EQP06-PM2",
        (AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.WARNING,
    ),
    (
        "LOT007",
        "EQP01-PM1",
        (AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.WARNING,
    ),
    (
        "LOT009",
        "EQP03-PM1",
        (AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.WARNING,
    ),
    (
        "LOT004",
        "EQP04-PM2",
        (AlarmSource.R03, AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.EQP_HOLD,
    ),
    (
        "LOT005",
        "EQP02-PM1",
        (AlarmSource.R03, AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.EQP_HOLD,
    ),
    (
        "LOT011",
        "EQP05-PM1",
        (AlarmSource.R03, AlarmSource.TRACE, AlarmSource.SUMMARY),
        ActionCode.EQP_HOLD,
    ),
)


def test_final_reference_cases_reproduce_the_five_four_three_distribution() -> None:
    keys = [(lot_id, chamber_id) for lot_id, chamber_id, *_ in REFERENCE_CASES]
    decisions = [
        subject.decide_action(
            _route(
                sources,
                suffix=f"REFERENCE-{index}",
                lot_id=lot_id,
                chamber_id=chamber_id,
            )
        ).action
        for index, (lot_id, chamber_id, sources, _expected) in enumerate(
            REFERENCE_CASES
        )
    ]
    expected = [case[3] for case in REFERENCE_CASES]

    assert len(keys) == len(set(keys)) == 12
    assert decisions == expected
    assert Counter(decisions) == {
        ActionCode.MONITORING: 5,
        ActionCode.WARNING: 4,
        ActionCode.EQP_HOLD: 3,
    }
