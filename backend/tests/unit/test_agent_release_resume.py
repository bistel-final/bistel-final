"""Actual repeated read validators; no real Docker, SMTP or workload."""

from copy import deepcopy

import pytest

from app.agent import release_resume as subject
from app.agent.release_artifacts import EvidenceError
from app.agent.release_prepared import RUNTIME_BINDING_FIELDS, config_digest
from tests.unit.test_agent_release_capture import rig  # noqa: F401


def arguments(test_rig):
    args = test_rig[0]
    return {
        k: args[k]
        for k in (
            "runtime_adapter",
            "running",
            "repository",
            "read_smtp_config",
            "read_context",
            "read",
        )
    }


def test_observation_is_live_and_does_not_run_a_second_preflight(rig):  # noqa: F811
    args = arguments(rig)
    observed, sha = subject.observe_resume(**args)
    assert set(observed) == set(RUNTIME_BINDING_FIELDS)
    assert sha == config_digest(rig[3])
    assert len(rig[5]) == 4 and len(rig[6]) == 2
    assert not any("readiness" in str(e) for e in rig[7])
    assert all("start" not in call and "create" not in call for call in rig[1].calls)


@pytest.mark.parametrize(
    "fault", ["config", "recipient", "context", "env", "container"]
)
def test_resume_rereads_fail_closed_on_drift(rig, fault):  # noqa: F811
    args = arguments(rig)
    original = args["read_smtp_config"]
    calls = 0

    def smtp():
        nonlocal calls
        calls += 1
        value = deepcopy(original())
        if fault == "config" and calls == 2:
            value["smtp_host"] = "changed.invalid"
        if fault == "recipient":
            value["recipient_allowlist"] = ["different@example.invalid"]
        if fault == "context":
            rig[4]["runner"] = rig[4]["runner"].model_copy(
                update={"recipients": ["other@example.invalid"]}
            )
        if fault == "env":
            args["read"] = lambda *_: {}
        if fault == "container":
            cid = args["running"].containers["backend"].container_id
            rig[1].payloads[cid]["started_at"] = "2026-09-05T03:00:00Z"
        return value

    if fault == "env":
        original_read = args["read"]
        args["read"] = lambda *a: original_read(*a) if calls == 0 else {}
    args["read_smtp_config"] = smtp
    with pytest.raises(EvidenceError, match="^PREPARED_RUNTIME_DRIFT$"):
        subject.observe_resume(**args)
