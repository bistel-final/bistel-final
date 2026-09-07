"""A legacy grant does not qualify a new wider run; resume keeps its own cap."""

from types import SimpleNamespace

import pytest

from app.agent import release_grant, runtime_composition
from app.common import config

ATTEMPT = "20260903T010203Z-0123456789ab"


def _runtime(**kwargs):
    return runtime_composition.AgentRuntime(
        autonomy_level=3,
        level3_enabled=True,
        database_name="kosa_agent",
        demo_ack=ATTEMPT,
        demo_receipt_validator=lambda _: True,
        **kwargs,
    )


@pytest.mark.parametrize("grant_profile", [None, "PRODUCTION_WIDE_V1"])
def test_new_run_requires_wide_grant_while_resume_accepts_valid_old_grant(
    monkeypatch, grant_profile
):
    calls = []

    def validate(**kwargs):
        calls.append(kwargs)
        return (
            "expected_investigation_budget_profile" not in kwargs
            or kwargs["expected_investigation_budget_profile"] == grant_profile
        )

    monkeypatch.setattr(config, "AGENT_ACTION_POLICY", "MOCK-NOTIFY-V1")
    monkeypatch.setattr(release_grant, "release_grant_matches", validate)
    runtime = _runtime()
    if grant_profile is None:
        with pytest.raises(runtime_composition.AgentRuntimeError):
            runtime._require_autonomy_ready()
    else:
        runtime._require_autonomy_ready()
    assert calls[-1]["expected_investigation_budget_profile"] == "PRODUCTION_WIDE_V1"
    runtime._require_autonomy_ready(for_new_run=False)
    assert "expected_investigation_budget_profile" not in calls[-1]


def test_old_receipt_policy_cannot_admit_new_wide_run_but_can_resume(monkeypatch):
    monkeypatch.setattr(config, "AGENT_ACTION_POLICY", "ACTION-POLICY-V1")
    runtime = _runtime()
    with pytest.raises(runtime_composition.AgentRuntimeError):
        runtime._require_autonomy_ready()
    runtime._require_autonomy_ready(for_new_run=False)


def test_preflight_checks_new_admission_but_resources_checks_resume(monkeypatch):
    calls = []
    resources = SimpleNamespace(llm_model="fixture-model")
    runtime = _runtime(
        factory=lambda _: resources,
        model_config=lambda: "fixture-model",
        llm_preflight=lambda: "fixture-model",
    )
    monkeypatch.setattr(
        runtime,
        "_require_autonomy_ready",
        lambda **kwargs: calls.append(kwargs.get("for_new_run", True)),
    )
    assert runtime.preflight() is resources
    assert runtime.resources() is resources
    assert calls == [True, False]
