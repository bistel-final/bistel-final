"""조치 우선순위를 적용하는 versioned 순수 규칙 (`V5-C-3.1`)."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Final

from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import (
    RULE_TO_ACTION,
    ActionDecision,
    ActionPolicyVersion,
    MatchedRule,
)
from app.common.enums import AlarmSource, requires_approval, resolve_severity

POLICY_VERSION: Final[ActionPolicyVersion] = "ACTION-POLICY-V1"
_POLICY_RULES: Final[tuple[tuple[MatchedRule, AlarmSource | None], ...]] = (
    ("R03_PRESENT", AlarmSource.R03),
    ("TRACE_OOS", AlarmSource.TRACE),
    ("SUMMARY_OOC_ONLY", AlarmSource.SUMMARY),
    ("NO_ALARM", None),
)
ACTION_POLICY: Final = MappingProxyType(
    {
        "version": POLICY_VERSION,
        "priority": tuple(rule for rule, _source in _POLICY_RULES),
    }
)


def decide_action(route: ResolvedIncidentRoute) -> ActionDecision:
    """member Alarm source만으로 가장 높은 한 개의 조치를 결정한다."""

    sources = {alarm.source for alarm in route.incident.member_alarms}
    rule = next(
        candidate
        for candidate, source in _POLICY_RULES
        if source is None or source in sources
    )

    action = RULE_TO_ACTION[rule]
    return ActionDecision(
        action=action,
        severity=None if action is None else resolve_severity(action),
        requires_approval=False if action is None else requires_approval(action),
        matched_rule=rule,
        policy_version=POLICY_VERSION,
    )


def production_port() -> Callable[[ResolvedIncidentRoute], ActionDecision]:
    """AgentNodePorts에 주입할 production 결정 callable."""

    return decide_action


__all__ = ["ACTION_POLICY", "POLICY_VERSION", "decide_action", "production_port"]
