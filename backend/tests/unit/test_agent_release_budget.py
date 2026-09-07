"""Versioned production release limits; no live calls or legacy reinterpretation."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.release_budget import BudgetBoundEvidence, budget_policy, profile_fields
from app.agent.release_prepared import EffectiveEnv
from app.agent.release_round import (
    BUDGET,
    RoundEvidence,
    assess_round,
    budget_policy_sha256,
)
from app.agent.release_run_capture import capture_run
from app.agent.u10_runtime import verify_runtime_readbacks
from tests.unit.test_agent_release import prepared_payload
from tests.unit.test_agent_release_round import evidence, template  # noqa: F401
from tests.unit.test_agent_release_run_capture import capture_args, model  # noqa: F401
from tests.unit.test_agent_u10_runtime import inputs

WIDE = "PRODUCTION_WIDE_V1"
KEY = "investigation_budget_profile"


def bind_round(raw, profile=WIDE):
    raw.update(
        **profile_fields(profile), budget_policy_sha256=budget_policy_sha256(profile)
    )
    for run in raw["runs"]:
        run.update(profile_fields(profile))
    return raw


def repeat_reads(run, extra):
    stop = run["react_trace"].pop()
    for _ in range(extra):
        read = deepcopy(run["reads"][1])
        event = deepcopy(run["react_trace"][0])
        read["seq"] = len(run["reads"]) + 1
        read["selector_seq"] = event["seq"] = len(run["react_trace"]) + 1
        run["reads"].append(read)
        run["react_trace"].append(event)
    stop["seq"] = len(run["react_trace"]) + 1
    run["react_trace"].append(stop)


def test_legacy_bytes_and_policy_digest_are_exactly_preserved(evidence):  # noqa: F811
    parsed = RoundEvidence.model_validate(evidence)
    assert canonical_json(parsed) == canonical_json(evidence)
    assert budget_policy_sha256() == digest(canonical_json(BUDGET))
    assert (
        budget_policy_sha256()
        == "7297f0bea5e5c1201aa2c028d5d170d590d96894f77670268c605a1c7bf3ebe1"
    )
    assert canonical_json(BudgetBoundEvidence()) == b"{}"


@pytest.mark.parametrize("bad", [None, "STANDARD", "DEVELOPMENT_WIDE", "wide", 3])
def test_artifact_profile_requires_known_non_null_identity(bad):
    with pytest.raises(ValidationError):
        BudgetBoundEvidence.model_validate({KEY: bad})


def test_profile_changes_only_explicit_bound_round_and_never_legacy_caps(evidence):  # noqa: F811
    repeat_reads(evidence["runs"][0], 3)
    old, _ = assess_round(RoundEvidence.model_validate(evidence))
    assert "READ_BUDGET_EXCEEDED" in old.failed_checks
    new, _ = assess_round(RoundEvidence.model_validate(bind_round(evidence)))
    assert new.robustness_verdict == "PASS"
    assert "READ_BUDGET_EXCEEDED" not in new.failed_checks
    assert budget_policy(WIDE) == dict(
        level12_total=8,
        level3_total=26,
        send=2,
        same_tool_attempts=8,
        selector_steps=28,
    )


@pytest.mark.parametrize("corruption", ["round_only", "one_legacy_run", "old_digest"])
def test_round_rejects_profile_mix_even_with_valid_dtos(evidence, corruption):  # noqa: F811
    bind_round(evidence)
    if corruption == "round_only":
        for run in evidence["runs"]:
            run.pop(KEY)
    elif corruption == "one_legacy_run":
        evidence["runs"][0].pop(KEY)
    else:
        evidence["budget_policy_sha256"] = budget_policy_sha256()
    with pytest.raises(EvidenceError, match="ROUND_BUDGET_POLICY_MISMATCH"):
        assess_round(RoundEvidence.model_validate(evidence))


def test_wide_same_tool_limit_still_enforced(evidence):  # noqa: F811
    bind_round(evidence)
    repeat_reads(evidence["runs"][0], 8)
    summary, _ = assess_round(RoundEvidence.model_validate(evidence))
    assert "READ_BUDGET_EXCEEDED" in summary.failed_checks


def test_prepared_limits_require_profile_and_exact_numbers():
    old = prepared_payload()["effective_env"]
    assert canonical_json(EffectiveEnv.model_validate(old)) == canonical_json(old)
    wide = {**old, **profile_fields(WIDE), **budget_policy(WIDE)}
    assert EffectiveEnv.model_validate(wide).level3_total == 26
    for key in budget_policy(WIDE):
        bad = deepcopy(wide)
        bad[key] += 1
        with pytest.raises(ValidationError):
            EffectiveEnv.model_validate(bad)
    wide.pop(KEY)
    with pytest.raises(ValidationError):
        EffectiveEnv.model_validate(wide)


@pytest.mark.parametrize("mixed", [False, True])
def test_private_readback_propagates_profile_and_rejects_backend_runner_mix(mixed):
    args, payloads, _ = inputs()
    for index, value in enumerate(payloads.values()):
        if index == 0 or not mixed:
            value.update(**profile_fields(WIDE), budget_policy=budget_policy(WIDE))
    if mixed:
        with pytest.raises(EvidenceError, match="U10_RUNTIME_POLICY_MISMATCH"):
            verify_runtime_readbacks(**args)
    else:
        result = verify_runtime_readbacks(**args)
        assert all(
            row.investigation_budget_profile == WIDE
            for row in result.readbacks.values()
        )


@pytest.mark.parametrize("mismatch", [False, True])
def test_run_capture_binds_actual_db_metadata_not_checkpoint_claim(
    template,  # noqa: F811
    model,  # noqa: F811
    mismatch,
):
    raw = deepcopy(template["runs"][0])
    raw.update(profile_fields(WIDE))
    args = capture_args(raw, model)
    if mismatch:
        args["run"].evidence = {}
        with pytest.raises(EvidenceError, match="ROUND_RUN_CAPTURE_INVALID"):
            capture_run(**args)
    else:
        result = capture_run(**args)
        assert result.investigation_budget_profile == WIDE
        assert result.model_dump(mode="json") == raw
