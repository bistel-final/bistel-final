"""Table-driven service call order under a real persistent admission fence."""

import pytest

from app.agent.release_artifacts import EvidenceError
from app.agent.release_fence import ProductionFence, admit_new_run
from app.agent.release_transition import transition
from tests.unit.test_agent_release import ATTEMPT, REV
from tests.unit.test_agent_release_grant import layout  # noqa: F401


class Ports:
    def __init__(self, root, faults=(), verdict="NOT_ESTABLISHED_V21"):
        self.root, self.faults, self.verdict = root, set(faults), verdict
        self.calls = []
        self.counts = {}
        self.level = 2

    def call(self, name):
        self.calls.append(name)
        self.counts[name] = self.counts.get(name, 0) + 1
        if name != "validate":
            with pytest.raises(EvidenceError):
                with admit_new_run(root=self.root):
                    pytest.fail("new run admitted")
        if (name, self.counts[name]) in self.faults:
            raise EvidenceError("TEST_FAILURE")

    def validate_inputs(self, **_):
        self.call("validate")

    def active_runs(self):
        self.call("quiescence")
        return 1 if ("busy", 1) in self.faults else 0

    def configure(self, level):
        self.call(f"configure{level}")
        self.level = level

    def recreate(self):
        self.call(f"recreate{self.level}")

    def preflight(self, level):
        self.call(f"preflight{level}")
        return {
            "profile": f"production_level{level}",
            "evaluated_revision": REV,
            "integrity": "PASS",
            "agent_verdict": self.verdict,
            "robustness": "PASS",
            "delivery_integrity": "PASS",
            "reset_attempt_id": ATTEMPT,
            "allowed_actions": {"production_level3": ("deny", 1) not in self.faults},
        }


def setup(tmp_path, faults=(), verdict="NOT_ESTABLISHED_V21"):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    ports = Ports(root, faults, verdict)
    return ports, dict(
        ports=ports,
        fence=ProductionFence(root, REV, ATTEMPT),
        revision=REV,
        attempt_id=ATTEMPT,
    )


@pytest.mark.parametrize("verdict", ["ESTABLISHED_V21", "NOT_ESTABLISHED_V21"])
def test_success_includes_rollback_rehearsal_and_ends_l3(tmp_path, verdict):
    ports, args = setup(tmp_path, verdict=verdict)
    result = transition(**args)
    assert result["status"] == "PASS" and result["level"] == 3
    assert ports.calls == [
        "validate",
        "quiescence",
        "preflight2",
        "configure3",
        "recreate3",
        "preflight3",
        "configure2",
        "recreate2",
        "preflight2",
        "configure3",
        "recreate3",
        "preflight3",
    ]
    with admit_new_run(root=ports.root):
        pass


@pytest.mark.parametrize(
    "fault",
    [
        ("configure3", 1),
        ("recreate3", 1),
        ("preflight3", 1),
        ("configure2", 1),
        ("recreate2", 1),
        ("preflight2", 2),
        ("configure3", 2),
        ("preflight3", 2),
        ("deny", 1),
    ],
)
def test_every_partial_failure_restores_once_and_verifies_before_admission(
    tmp_path, fault
):
    ports, args = setup(tmp_path, [fault])
    result = transition(**args)
    assert result["status"] == "FAIL" and result["restored_level2"] is True
    assert ports.calls[-3:] == ["configure2", "recreate2", "preflight2"]
    with admit_new_run(root=ports.root):
        pass


def test_failed_restore_keeps_durable_closed_fence(tmp_path):
    ports, args = setup(tmp_path, [("preflight3", 1), ("recreate2", 1)])
    result = transition(**args)
    assert result["restored_level2"] is False and result["new_runs_blocked"] is True
    assert ports.counts["configure2"] == ports.counts["recreate2"] == 1
    with pytest.raises(EvidenceError):
        with admit_new_run(root=ports.root):
            pytest.fail("run admitted")


def test_active_runs_prevent_configuration_and_service_changes(tmp_path):
    ports, args = setup(tmp_path, [("busy", 1)])
    result = transition(**args)
    assert result["status"] == "FAIL"
    assert not any(c.startswith(("configure", "recreate")) for c in ports.calls)


def test_explicit_recovery_only_restores_level2(tmp_path):
    ports, args = setup(tmp_path)
    result = transition(**args, restore_only=True)
    assert result["status"] == "PASS" and result["level"] == 2
    assert ports.calls == [
        "validate",
        "quiescence",
        "configure2",
        "recreate2",
        "preflight2",
    ]


def test_interruption_after_partial_transition_restores_before_admission(tmp_path):
    ports, args = setup(tmp_path)
    original = ports.preflight

    def preflight(level):
        if level == 3:
            raise KeyboardInterrupt
        return original(level)

    ports.preflight = preflight
    result = transition(**args)
    assert result["status"] == "FAIL" and result["code"] == "RELEASE_INTERRUPTED"
    assert result["restored_level2"] is True
    assert ports.calls[-3:] == ["configure2", "recreate2", "preflight2"]


def test_operator_signal_handlers_are_restored():
    import signal

    from app.agent.release_transition import operator_signals

    prior = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    with pytest.raises(KeyboardInterrupt):
        with operator_signals():
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    assert {s: signal.getsignal(s) for s in prior} == prior


@pytest.fixture
def mock_transition(tmp_path, layout):  # noqa: F811
    from app.agent.release_grant import read_release_grant

    ports, args = setup(tmp_path)
    ports.release_policy = "MOCK-NOTIFY-V1"
    grant = read_release_grant(**layout).model_copy(
        update={"attempt_id": ATTEMPT, "R": REV}
    )

    def qualify(*, fence):
        assert fence is args["fence"] and fence.fd is not None
        ports.call("qualify")
        return grant

    ports.qualify = qualify
    return ports, args, grant


def test_mock_transition_qualifies_before_configuration_and_rehearsal(mock_transition):
    ports, args, _ = mock_transition
    result = transition(**args)
    assert result["status"] == "PASS" and result["level"] == 3
    assert ports.calls == [
        "validate",
        "quiescence",
        "preflight2",
        "qualify",
        "configure3",
        "recreate3",
        "preflight3",
        "configure2",
        "recreate2",
        "preflight2",
        "configure3",
        "recreate3",
        "preflight3",
    ]
    with admit_new_run(root=ports.root, required_binding=(REV, ATTEMPT)):
        pass


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_id", "20260906T000000Z-ffffffffffff"),
        ("R", "f" * 40),
        ("action_policy_version", "ACTION-POLICY-V1"),
        ("type", None),
    ],
)
def test_mock_transition_rejects_misbound_grant_before_configure(
    mock_transition, field, value
):
    ports, args, grant = mock_transition

    def qualify(**kwargs):
        ports.call("qualify")
        # Simulate a broken issuer boundary, including an invalid policy object.
        return None if field == "type" else grant.model_copy(update={field: value})

    ports.qualify = qualify
    result = transition(**args)
    assert result["status"] == "FAIL" and result["code"] == "RELEASE_GRANT_MISMATCH"
    assert result["restored_level2"] is True
    assert ports.calls == [
        "validate",
        "quiescence",
        "preflight2",
        "qualify",
        "preflight2",
    ]
    with admit_new_run(root=ports.root):
        pass


def test_mock_qualification_exception_rechecks_l2_without_reconfiguration(
    mock_transition,
):
    ports, args, _ = mock_transition
    ports.faults.add(("qualify", 1))
    result = transition(**args)
    assert result["code"] == "TEST_FAILURE" and result["restored_level2"] is True
    assert ports.calls == [
        "validate",
        "quiescence",
        "preflight2",
        "qualify",
        "preflight2",
    ]
    with admit_new_run(root=ports.root):
        pass
