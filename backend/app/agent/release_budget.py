"""Versioned private budget bindings; missing fields retain legacy byte shape."""

from pydantic import model_serializer, model_validator

from app.agent.investigation_budget import RUN_PROFILE_KEY as PROFILE_KEY
from app.agent.investigation_budget import ProductionProfileId as ProductionProfile
from app.agent.investigation_budget import resolve_run_budget
from app.agent.release_artifacts import EvidenceModel


def budget_policy(profile_id: str | None = None) -> dict[str, int]:
    profile = resolve_run_budget(3, profile_id)
    return {
        "level12_total": 8,
        "level3_total": profile.read_cap + profile.send_budget,
        "send": profile.send_budget,
        "same_tool_attempts": profile.same_tool_cap,
        "selector_steps": profile.selector_cap,
    }


def profile_fields(profile_id: str | None) -> dict[str, str]:
    # Resolve even absent fields: this helper never admits a development profile.
    resolve_run_budget(3, profile_id)
    return {} if profile_id is None else {PROFILE_KEY: profile_id}


class BudgetBoundEvidence(EvidenceModel):
    investigation_budget_profile: ProductionProfile | None = None

    @model_validator(mode="before")
    @classmethod
    def omitted_is_legacy(cls, value):
        if (
            isinstance(value, dict)
            and PROFILE_KEY in value
            and value[PROFILE_KEY] is None
        ):
            raise ValueError("INVESTIGATION_BUDGET_PROFILE_INVALID")
        return value

    @model_serializer(mode="wrap")
    def preserve_legacy_budget_shape(self, handler):
        value = handler(self)
        if self.investigation_budget_profile is None:
            value.pop(PROFILE_KEY, None)
        return value
