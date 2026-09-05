"""Run real validation composition with fake Git/Docker/HTTP leaves only."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from app.agent import u10_deployment as subject
from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.u10_readiness import ProbeResponse
from tests.unit.test_agent_u10_images import inputs as image_inputs
from tests.unit.test_agent_u10_readiness import ready
from tests.unit.test_agent_u10_runtime import ATTEMPT, payload


def setup(monkeypatch, profile="e2e_level3"):
    args, containers, images, _, _ = image_inputs(monkeypatch, profile)
    events = []
    inspect = args["inspect"]

    def tracked_inspect(kind, identifier):
        events.append((kind, identifier))
        return inspect(kind, identifier)

    def read(identifier, selected):
        events.append(("runtime", identifier, selected))
        return payload(selected)

    def fetch(path):
        events.append(("http", path))
        return ProbeResponse(200, canonical_json(ready()))

    times = iter(
        [datetime(2026, 9, 5, 1, tzinfo=UTC), datetime(2026, 9, 5, 1, 1, tzinfo=UTC)]
    )
    args.update(
        phase="pre_u9",
        inspect=tracked_inspect,
        read=read,
        fetch=fetch,
        expected_attempt_id=ATTEMPT if profile == "production_level3" else None,
        clock=lambda: next(times),
    )
    return args, containers, images, events


@pytest.mark.parametrize(
    "profile", ["production_level2", "production_level3", "e2e_level3"]
)
@pytest.mark.parametrize("phase", ["pre_u9", "post_start_pre_enable"])
def test_same_ids_and_exact_order_with_real_validators(monkeypatch, profile, phase):
    args, _, _, events = setup(monkeypatch, profile)
    args["phase"] = phase
    result = subject.observe_deployment(**args)
    ids = args["container_ids"]
    round_events = [
        event
        for role in ids
        for event in [
            ("container", ids[role]),
            ("image", args["expected_image_ids"][role]),
        ]
    ] + [("container", value) for value in ids.values()]
    roles = ["backend", "runner"] if profile == "e2e_level3" else ["backend"]
    assert events == (
        round_events
        + [("runtime", ids[role], profile) for role in roles]
        + [("http", path) for path in ("/api/health/ready", "/", "/api/health")]
        + round_events
    )
    assert result.runtime.container_ids == {role: ids[role] for role in roles}
    assert result.profile == profile and result.phase == phase
    assert result.started_at == "2026-09-05T01:00:00Z"
    assert result.checked_at == "2026-09-05T01:01:00Z"
    assert set(result.model_dump()) == {
        "profile",
        "phase",
        "started_at",
        "checked_at",
        "image_bindings",
        "runtime",
        "readiness",
    }


@pytest.mark.parametrize("stage", ["runtime", "http"])
@pytest.mark.parametrize("role", ["backend", "frontend", "runner"])
def test_restart_between_image_rounds_is_rejected(monkeypatch, stage, role):
    args, containers, _, _ = setup(monkeypatch)
    key = "read" if stage == "runtime" else "fetch"
    original = args[key]

    def restart(*values):
        containers[args["container_ids"][role]]["started_at"] = "2026-09-05T01:00:30Z"
        return original(*values)

    args[key] = restart
    with pytest.raises(EvidenceError, match="^U10_DEPLOYMENT_DRIFT$"):
        subject.observe_deployment(**args)


@pytest.mark.parametrize("role", ["backend", "frontend", "runner"])
def test_post_http_stopped_container_is_rejected(monkeypatch, role):
    args, containers, _, _ = setup(monkeypatch)
    fetch = args["fetch"]

    def stop(path):
        containers[args["container_ids"][role]]["running"] = False
        return fetch(path)

    args["fetch"] = stop
    with pytest.raises(EvidenceError):
        subject.observe_deployment(**args)


def test_failed_first_image_check_prevents_runtime_and_http(monkeypatch):
    args, containers, _, events = setup(monkeypatch)
    containers[args["container_ids"]["backend"]]["image_id"] = "sha256:" + "9" * 64
    with pytest.raises(EvidenceError):
        subject.observe_deployment(**args)
    assert not any(e[0] in ("runtime", "http") for e in events)


def test_runtime_failure_prevents_http_and_second_image_round(monkeypatch):
    args, _, _, events = setup(monkeypatch)

    def wrong_database(identifier, profile):
        events.append(("runtime", identifier))
        value = payload(profile)
        value["database"] = "kosa_agent"
        return value

    args["read"] = wrong_database
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READBACK_INVALID$"):
        subject.observe_deployment(**args)
    assert events[-1][0] == "runtime" and not any(e[0] == "http" for e in events)
    assert sum(e[0] == "image" for e in events) == 3


def test_readiness_failure_prevents_second_image_round(monkeypatch):
    args, _, _, events = setup(monkeypatch)
    args["fetch"] = lambda _: ProbeResponse(503)
    with pytest.raises(EvidenceError, match="^U10_READINESS_HTTP_STATUS_INVALID$"):
        subject.observe_deployment(**args)
    assert sum(e[0] == "image" for e in events) == 3


def test_caller_map_mutation_does_not_retarget_later_reads(monkeypatch):
    args, _, _, events = setup(monkeypatch)
    original = args["read"]
    pinned = args["container_ids"].copy()

    def mutate(*values):
        args["container_ids"]["runner"] = "9" * 64
        args["expected_image_ids"]["frontend"] = "sha256:" + "9" * 64
        return original(*values)

    args["read"] = mutate
    result = subject.observe_deployment(**args)
    assert result.runtime.container_ids["runner"] == pinned["runner"]
    assert all("9" * 64 not in str(event) for event in events)


@pytest.mark.parametrize("phase", ["round2_pre_run", None, []])
def test_invalid_phase_prevents_io(monkeypatch, phase):
    args, _, _, events = setup(monkeypatch)
    args["phase"] = phase
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_PHASE_INVALID$"):
        subject.observe_deployment(**args)
    assert events == []


@pytest.mark.parametrize("time", [None, "2026-09-05", datetime(2026, 9, 5)])
def test_invalid_start_time_prevents_io(monkeypatch, time):
    args, _, _, events = setup(monkeypatch)
    args["clock"] = lambda: time
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_TIME_INVALID$"):
        subject.observe_deployment(**args)
    assert events == []


def test_backwards_clock_does_not_issue_observation(monkeypatch):
    args, _, _, _ = setup(monkeypatch)
    start = datetime(2026, 9, 5, tzinfo=UTC)
    times = iter([start, start - timedelta(seconds=1)])
    args["clock"] = lambda: next(times)
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_TIME_INVALID$"):
        subject.observe_deployment(**args)


def test_import_has_no_io_or_provider_loading():
    code = """
import sys, subprocess, httpx
def fail(*args, **kwargs): raise AssertionError('IO on import')
subprocess.run = fail
httpx.Client = fail
import app.agent.u10_deployment
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
