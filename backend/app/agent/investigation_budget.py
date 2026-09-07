"""Code-owned, versioned V5-C-7.1 investigation budgets.

These are not runtime environment overrides or public API inputs. Experimental
graphs require a matching isolated tool ledger; their results belong to a
development envelope, never the existing U10 or public run artifact schemas.
New Level 3 production runs bind PRODUCTION_WIDE_V1 once in agent_run.evidence.
Absent persisted metadata remains legacy; no environment value upgrades old runs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

RUN_PROFILE_KEY: Final = "investigation_budget_profile"
ProductionProfileId = Literal["PRODUCTION_WIDE_V1"]


@dataclass(frozen=True, slots=True)
class InvestigationBudget:
    """Named immutable limits, shared by the development harness and graph."""

    profile_id: Literal["STANDARD", "DEVELOPMENT_WIDE", "PRODUCTION_WIDE_V1"]
    read_cap: int
    selector_cap: int
    same_tool_cap: int
    guard_rejection_cap: int = 2
    send_budget: int = 2

    def __post_init__(self) -> None:
        values = (
            self.read_cap,
            self.selector_cap,
            self.same_tool_cap,
            self.guard_rejection_cap,
            self.send_budget,
        )
        expected = {
            "STANDARD": (8, 10, 4, 2, 2),
            "DEVELOPMENT_WIDE": (24, 28, 8, 2, 2),
            "PRODUCTION_WIDE_V1": (24, 28, 8, 2, 2),
        }
        if (
            any(type(value) is not int for value in values)
            or expected.get(self.profile_id) != values
        ):
            raise ValueError("INVESTIGATION_BUDGET_PROFILE_INVALID")


STANDARD: Final = InvestigationBudget("STANDARD", 8, 10, 4)
DEVELOPMENT_WIDE: Final = InvestigationBudget("DEVELOPMENT_WIDE", 24, 28, 8)
PRODUCTION_WIDE_V1: Final = InvestigationBudget("PRODUCTION_WIDE_V1", 24, 28, 8)


def profile_for_new_run(autonomy_level: int) -> ProductionProfileId | None:
    """Code-owned default for NEW production runs; never rewrites an existing run."""
    if type(autonomy_level) is not int or autonomy_level not in (1, 2, 3):
        raise ValueError("AUTONOMY_LEVEL_INVALID")
    return "PRODUCTION_WIDE_V1" if autonomy_level == 3 else None


def resolve_run_budget(
    autonomy_level: int, profile_id: str | None
) -> InvestigationBudget | None:
    """Resolve immutable persisted identity, not mutable env/checkpoint numbers."""
    # Validate the level without selecting the policy for a new run.
    profile_for_new_run(autonomy_level)
    if profile_id is None:
        return STANDARD if autonomy_level == 3 else None
    if autonomy_level != 3 or profile_id != "PRODUCTION_WIDE_V1":
        raise ValueError("INVESTIGATION_BUDGET_PROFILE_INVALID")
    return PRODUCTION_WIDE_V1


def persisted_profile(
    autonomy_level: int, evidence: Mapping[str, Any] | None
) -> ProductionProfileId | None:
    """Only absent metadata denotes legacy; null/unknown metadata is corruption."""
    if evidence is not None and not isinstance(evidence, Mapping):
        raise ValueError("INVESTIGATION_BUDGET_PROFILE_INVALID")
    if evidence is None or RUN_PROFILE_KEY not in evidence:
        resolve_run_budget(autonomy_level, None)
        return None
    profile = evidence[RUN_PROFILE_KEY]
    if not isinstance(profile, str):
        raise ValueError("INVESTIGATION_BUDGET_PROFILE_INVALID")
    resolve_run_budget(autonomy_level, profile)
    return profile
