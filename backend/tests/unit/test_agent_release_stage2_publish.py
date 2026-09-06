"""Real golden recount/publish wiring, fake process/DB/API boundaries."""

import pytest

from app.agent import release_stage2_publish as m
from app.agent import release_stage2_recovery as recovery
from app.agent.release_artifacts import (
    EvidenceError,
    parse_json,
    read_private,
    write_private,
    write_private_bytes,
)
from app.agent.release_prepared import parse_prepared
from app.agent.release_runtime import RuntimeSnapshot
from tests.unit.test_agent_release import AT, ATTEMPT, REV
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_bundle_v2 import mock_bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery  # noqa: F401
from tests.unit.test_agent_release_golden_v2 import golden  # noqa: F401
from tests.unit.test_agent_release_mock import evidence as mock_evidence  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401
from tests.unit.test_agent_release_runtime import Docker


@pytest.fixture
def publication(golden, tmp_path, monkeypatch):  # noqa: F811
    args, manifest = golden
    a = tmp_path / ATTEMPT
    args["root"].rename(a)
    root = a / "robustness"
    prepared = parse_prepared(parse_json(read_private(root, "prepared-attempt.json")))
    second_name = manifest["snapshots"]["SECOND_BATCH"]["relative_path"]
    second = read_private(a, second_name)
    second_output = read_private(a, "evidence/second-batch.jsonl")
    fault = read_private(a, "fault-5class.json")
    for name in (
        "evidence/mock-notify-evidence.json",
        "golden-flow.json",
        "fault-5class.json",
        "evidence/second-batch.jsonl",
        second_name,
    ):
        (a / name).unlink()
    (a / "evidence/artifacts/NO_DECISIONS/db-snapshot.json").rename(
        a / "evidence/artifacts/NO_DECISIONS/db.json"
    )
    fake = Docker(tmp_path)
    fake.fill()
    containers = {}
    for role, c in zip(
        ("backend", "frontend", "runner"), fake.payloads.values(), strict=True
    ):
        c.update(
            container_id=getattr(prepared.containers, role).container_id,
            started_at=AT,
            status="running",
            running=True,
        )
        containers[role] = c
    running = RuntimeSnapshot(
        revision=REV, phase="running", images=prepared.images, containers=containers
    )
    new_value = running.model_dump()
    new_value["containers"]["backend"]["container_id"] = "9" * 64
    new = RuntimeSnapshot.model_validate(new_value)
    write_private(a, "prepare-running.json", running)
    events = []

    class Runtime:
        env = {}
        compose = ["docker", "compose", "-p", "bistel-team-e2e"]

        def verify_running(self, expected):
            assert expected == running

        def _ids(self):
            return {}

        def _stable(self, ids, state):
            assert state == "running"
            return new

        def _command(self, argv, **kwargs):
            events.append(argv)
            return b""

        def exec_runner(self, expected, argv, **kwargs):
            assert expected == running
            events.append(argv)
            if "scripts/run_pending_incidents.py" in argv:
                value = parse_json(
                    read_private(a, "evidence/artifacts/PREFLIGHT/pending.json")
                )
                value["selected"] = []
                return __import__("json").dumps(value).encode()
            if "scripts/evaluate_fault_5class.py" in argv:
                write_private_bytes(a, "fault-5class.json", fault)
                return b""
            raise AssertionError(argv)

    runtime = Runtime()

    def once(runtime, expected, *, root, name, arguments, **kwargs):
        assert expected == running
        events.append(arguments)
        return write_private_bytes(root, name, second_output)

    monkeypatch.setattr(
        m, "current_runtime", lambda **kwargs: (a, prepared, running, runtime)
    )
    monkeypatch.setattr(m, "capture_runner", once)
    monkeypatch.setattr(
        m, "snapshot", lambda *args: write_private_bytes(a, second_name, second)
    )
    monkeypatch.setattr(m, "source", lambda *args: [])
    monkeypatch.setattr(
        recovery, "verify_evaluation_api", lambda state: events.append(["api", state])
    )
    monkeypatch.setattr(
        recovery, "docker_command", lambda argv, **kw: events.append(argv)
    )
    from pathlib import Path

    inputs = dict(
        repository=Path(__file__).resolve().parents[3],
        report_root=tmp_path,
        env_file=tmp_path / ".env.team",
        attempt_id=ATTEMPT,
    )
    return inputs, a, events, runtime, prepared, new


def test_actual_publish_recounts_before_backend_only_recreation(publication):
    args, a, events, _, prepared, new = publication
    before = read_private(a / "robustness", "prepared-attempt.json")
    result = m.publish_evidence(**args)
    assert result == dict(status="PUBLICATIONS_VERIFIED", post_freeze_callbacks=0)
    assert sum("--once" in argv for argv in events) == 1
    recreates = [argv for argv in events if "--force-recreate" in argv]
    assert len(recreates) == 1 and recreates[0][-1] == "backend"
    assert "--no-build" in recreates[0] and "--no-deps" in recreates[0]
    assert "--pull" in recreates[0] and "never" in recreates[0]
    assert ["api", "bound"] in events
    assert any("--publications-only" in argv for argv in events)
    assert (
        parse_json(read_private(a, "post-freeze-callbacks.json"))["original_run_id"]
        == ATTEMPT
    )
    assert read_private(a / "robustness", "prepared-attempt.json") == before
    assert (
        m.cleanup_context(a, allow_published=True).containers
        == new.prepared_containers()
    )
    assert m.cleanup_context(a, allow_published=False).containers == prepared.containers
    count = len(events)
    with pytest.raises(EvidenceError, match="PUBLISH_ALREADY_STARTED"):
        m.publish_evidence(**args)
    assert len(events) == count


def test_ambiguous_recreation_never_authorizes_mutable_id_cleanup(publication):
    args, a, _, runtime, _, _ = publication

    def fail(*args, **kwargs):
        raise EvidenceError("LEVEL3_RUNTIME_COMMAND_FAILED")

    runtime._command = fail
    with pytest.raises(EvidenceError, match="LEVEL3_RUNTIME_COMMAND_FAILED"):
        m.publish_evidence(**args)
    assert (a / "publish-recreate-intent.json").exists()
    assert not (a / "publish-running.json").exists()
    with pytest.raises(EvidenceError):
        m.cleanup_context(a, allow_published=True)


@pytest.mark.parametrize("drift", ["frontend", "runner", "image"])
def test_publish_runtime_drift_is_rejected_before_recording_new_pins(
    publication, drift
):
    args, a, events, runtime, _, new = publication
    changed = new.model_dump()
    if drift == "image":
        changed["images"]["backend"]["image_id"] = "sha256:" + "8" * 64
    else:
        changed["containers"][drift]["container_id"] = "8" * 64
    observed = RuntimeSnapshot.model_validate(changed)
    runtime._stable = lambda *args: observed
    before = read_private(a / "robustness", "prepared-attempt.json")
    with pytest.raises(EvidenceError, match="^PUBLISH_RUNTIME_DRIFT$"):
        m.publish_evidence(**args)
    assert (a / "publish-recreate-intent.json").exists()
    assert not (a / "publish-running.json").exists()
    assert not (a / "post-freeze-callbacks.json").exists()
    assert ["api", "bound"] not in events
    assert sum("--force-recreate" in argv for argv in events) == 1
    assert read_private(a / "robustness", "prepared-attempt.json") == before
