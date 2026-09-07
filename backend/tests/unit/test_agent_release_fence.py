"""Real file locks and persistent crash state, no service/DB mutation."""

import os
import subprocess
import sys

import pytest

from app.agent import release_fence as subject
from app.agent.release_artifacts import EvidenceError, parse_json
from tests.unit.test_agent_release import ATTEMPT, REV


@pytest.fixture
def root(tmp_path):
    result = tmp_path / "reports"
    result.mkdir(mode=0o700)
    return result


def fence(root):
    return subject.ProductionFence(root, REV, ATTEMPT)


def test_legacy_no_file_preserves_admission(root):
    with subject.admit_new_run(root=root):
        assert list(root.iterdir()) == []


def test_closed_and_exclusive_lock_deny_new_runs(root):
    with fence(root) as owned:
        with pytest.raises(EvidenceError):
            with subject.admit_new_run(root=root):
                pytest.fail("admitted")
        owned.reopen(level=3)
        # OPEN bytes alone cannot bypass a transition that still owns the lock.
        with pytest.raises(EvidenceError):
            with subject.admit_new_run(root=root):
                pytest.fail("admitted")
    with subject.admit_new_run(root=root):
        pass


def test_run_registration_shared_lock_prevents_transition(root):
    with fence(root) as owned:
        owned.reopen(level=2)
    before = (root / subject.FENCE_NAME).read_bytes()
    with subject.admit_new_run(root=root):
        with pytest.raises(EvidenceError):
            with fence(root):
                pytest.fail("transition raced")
    assert (root / subject.FENCE_NAME).read_bytes() == before


def test_exception_after_reopen_leaves_closed(root):
    with pytest.raises(RuntimeError):
        with fence(root) as owned:
            owned.reopen(level=3)
            raise RuntimeError("fault after reopening")
    with pytest.raises(EvidenceError):
        with subject.admit_new_run(root=root):
            pytest.fail("admitted")


def test_process_exit_cannot_reopen_runs(root):
    code = """
import os,sys
from pathlib import Path
from app.agent.release_fence import ProductionFence
f = ProductionFence(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
f.__enter__()
os._exit(19)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), REV, ATTEMPT],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 19
    assert parse_json((root / subject.FENCE_NAME).read_bytes())["state"] == "CLOSED"
    with pytest.raises(EvidenceError):
        with subject.admit_new_run(root=root):
            pytest.fail("admitted")
    # The OS lock was released but reopening still requires an explicit operator.
    with fence(root) as owned:
        owned.reopen(level=2)
    with subject.admit_new_run(root=root):
        pass


@pytest.mark.parametrize("fault", ["empty", "mode", "symlink", "hardlink", "schema"])
def test_invalid_fence_never_admits(root, fault):
    with fence(root) as owned:
        owned.reopen(level=3)
    p = root / subject.FENCE_NAME
    if fault in {"empty", "schema"}:
        p.write_bytes(b"" if fault == "empty" else b"{}")
    if fault == "mode":
        p.chmod(0o644)
    if fault == "symlink":
        p.rename(root / "saved")
        p.symlink_to(root / "saved")
    if fault == "hardlink":
        os.link(p, root / "saved")
    with pytest.raises((EvidenceError, OSError)):
        with subject.admit_new_run(root=root):
            pytest.fail("admitted")


def test_api_start_is_inside_admission_fence(root, monkeypatch):
    from functools import partial

    from app.agent import runtime_composition as runtime
    from app.common.exceptions import DependencyNotReadyError

    monkeypatch.setattr(
        subject, "admit_new_run", partial(subject.admit_new_run, root=root)
    )
    runtime_type = runtime.AgentRuntime
    obj = object.__new__(runtime_type)
    obj._autonomy_level, obj._database_name = 2, "kosa_agent"
    calls = []
    monkeypatch.setattr(
        runtime_type, "_start_admitted_run", lambda *_: calls.append("register")
    )
    with fence(root):
        with pytest.raises(DependencyNotReadyError):
            obj.start_run(None)
    assert calls == []
    with fence(root) as owned:
        owned.reopen(level=2)
    obj.start_run(None)
    assert calls == ["register"]


@pytest.mark.parametrize("fault", ["missing", "level", "revision", "attempt"])
def test_production_level3_requires_exact_open_binding(root, fault):
    if fault != "missing":
        with fence(root) as owned:
            owned.reopen(level=2 if fault == "level" else 3)
    revision = "b" * 40 if fault == "revision" else REV
    attempt = "20260905T020000Z-aaaaaaaaaaaa" if fault == "attempt" else ATTEMPT
    with pytest.raises(EvidenceError):
        with subject.admit_new_run(root=root, required_binding=(revision, attempt)):
            pytest.fail("unbound L3 admission")


def test_production_level3_accepts_exact_open_binding(root):
    with fence(root) as owned:
        owned.reopen(level=3)
    with subject.admit_new_run(root=root, required_binding=(REV, ATTEMPT)):
        pass
