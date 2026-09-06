"""Real local flock/FD/subprocess tests; no service, SMTP or project Git writes."""

import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys

import pytest

from app.agent import release_lifecycle as m
from app.agent.release_artifacts import EvidenceError
from app.agent.release_prepare import issue_prepared
from tests.unit import test_agent_release_prepare as prepare_tests

bundle = prepare_tests.bundle
cli_command = prepare_tests.cli_command


def contender(root):
    code = f"""
from pathlib import Path
from app.agent.release_lifecycle import lifecycle_lock
with lifecycle_lock(Path({str(root)!r})): pass
"""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


def test_nested_borrow_closes_only_duplicate_and_preserves_owner(root):
    with m.lifecycle_lock(root) as owner:
        assert not os.get_inheritable(owner)
        with m.lifecycle_lock(root, inherited_fd=owner) as borrowed:
            assert borrowed != owner and not os.get_inheritable(borrowed)
            m.verify_lifecycle_lock(root, borrowed)
            assert contender(root).returncode != 0
        with pytest.raises(OSError):
            os.fstat(borrowed)
        m.verify_lifecycle_lock(root, owner)
        assert "LIFECYCLE_LOCK_BUSY" in contender(root).stderr
    assert contender(root).returncode == 0


def test_child_explicit_inheritance_keeps_lock_after_success_and_exception(root):
    for fail in (False, True):
        with m.lifecycle_lock(root) as owner:
            code = f"""
from pathlib import Path
from app.agent.release_lifecycle import lifecycle_lock
with lifecycle_lock(Path({str(root)!r}), inherited_fd={owner}) as fd:
    assert fd != {owner}
    if {fail!r}: raise ValueError('synthetic')
"""
            child = subprocess.run(
                [sys.executable, "-c", code],
                pass_fds=(owner,),
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert child.returncode == (1 if fail else 0), child.stderr
            m.verify_lifecycle_lock(root, owner)
            assert "LIFECYCLE_LOCK_BUSY" in contender(root).stderr
        assert contender(root).returncode == 0


def test_terminated_borrower_does_not_unlock_owner(root):
    with m.lifecycle_lock(root) as owner:
        code = f"""
import os
from pathlib import Path
from app.agent.release_lifecycle import lifecycle_lock
with lifecycle_lock(Path({str(root)!r}), inherited_fd={owner}):
    os._exit(23)
"""
        child = subprocess.run(
            [sys.executable, "-c", code],
            pass_fds=(owner,),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert child.returncode == 23
        m.verify_lifecycle_lock(root, owner)
        assert contender(root).returncode != 0


def test_abrupt_owner_exit_keeps_inherited_child_lock_until_child_exit(root):
    child_code = """
import sys
from pathlib import Path
from app.agent.release_lifecycle import lifecycle_lock
with lifecycle_lock(Path(sys.argv[1]), inherited_fd=int(sys.argv[2])):
    print('READY', flush=True)
    assert sys.stdin.readline().strip() == 'DONE'
"""
    owner_code = f"""
import os, subprocess, sys
from pathlib import Path
from app.agent.release_lifecycle import lifecycle_lock
with lifecycle_lock(Path({str(root)!r})) as fd:
    subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(root)!r}, str(fd)],
                     pass_fds=(fd,))
    os._exit(0)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    complete = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(owner.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "child readback timed out"
        assert owner.stdout.readline().strip() == "READY"
        assert owner.wait(timeout=10) == 0
        assert "LIFECYCLE_LOCK_BUSY" in contender(root).stderr
        owner.stdin.write("DONE\n")
        owner.stdin.flush()
        _, err = owner.communicate(timeout=10)
        assert not err
        assert contender(root).returncode == 0
        complete = True
    finally:
        # Only the isolated process group created by this test, never a service.
        if not complete:
            try:
                os.killpg(owner.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        owner.communicate(timeout=10)


def test_repeated_verification_does_not_leak_probe_descriptors(root):
    with m.lifecycle_lock(root) as owner:
        expected = os.dup(owner)
        os.close(expected)
        for _ in range(20):
            m.verify_lifecycle_lock(root, owner)
        observed = os.dup(owner)
        try:
            assert observed == expected
        finally:
            os.close(observed)


@pytest.mark.parametrize("value", [-1, 0, 1, 2, True, "3", 999999])
def test_bad_descriptor_cannot_create_or_bypass_lock(root, value):
    with pytest.raises(EvidenceError, match="^LIFECYCLE_LOCK_DESCRIPTOR_INVALID$"):
        with m.lifecycle_lock(root, inherited_fd=value):
            pytest.fail("entered")
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("mode", ["unlocked", "shared", "readonly"])
def test_unlocked_shared_or_readonly_fd_not_accepted(root, mode):
    path = root / ".lifecycle.lock"
    fd = os.open(
        path,
        os.O_CREAT | os.O_EXCL | (os.O_RDONLY if mode == "readonly" else os.O_RDWR),
        0o600,
    )
    try:
        if mode == "shared":
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        elif mode == "readonly":
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EvidenceError, match="^LIFECYCLE_LOCK_DESCRIPTOR_INVALID$"):
            with m.lifecycle_lock(root, inherited_fd=fd):
                pytest.fail("entered")
        if mode == "unlocked":
            assert contender(root).returncode == 0
        if mode == "shared":
            # Refusal must not upgrade or unlock the original shared lock.
            probe = os.open(path, os.O_RDWR)
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
            finally:
                os.close(probe)
            assert contender(root).returncode != 0
    finally:
        os.close(fd)


def test_same_inode_independent_open_does_not_prove_ownership(root):
    with m.lifecycle_lock(root) as owner:
        independent = os.open(root / ".lifecycle.lock", os.O_RDWR)
        try:
            assert os.fstat(independent).st_ino == os.fstat(owner).st_ino
            with pytest.raises(
                EvidenceError, match="^LIFECYCLE_LOCK_DESCRIPTOR_INVALID$"
            ):
                with m.lifecycle_lock(root, inherited_fd=independent):
                    pytest.fail("entered")
            m.verify_lifecycle_lock(root, owner)
        finally:
            os.close(independent)


def test_other_attempt_lock_cannot_authorize_this_attempt(root, tmp_path):
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    with m.lifecycle_lock(root) as owner, m.lifecycle_lock(other):
        with pytest.raises(EvidenceError, match="^LIFECYCLE_LOCK_DESCRIPTOR_INVALID$"):
            with m.lifecycle_lock(other, inherited_fd=owner):
                pytest.fail("entered")
        m.verify_lifecycle_lock(root, owner)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "permissions", "replacement", "missing"]
)
def test_lock_path_metadata_or_inode_change_rejected(root, tmp_path, kind):
    path = root / ".lifecycle.lock"
    with m.lifecycle_lock(root) as owner:
        if kind == "permissions":
            path.chmod(0o644)
        elif kind == "hardlink":
            os.link(path, tmp_path / "link")
        else:
            moved = tmp_path / "original"
            path.rename(moved)
            if kind == "symlink":
                path.symlink_to(moved)
            elif kind == "replacement":
                path.touch(mode=0o600)
        with pytest.raises(EvidenceError, match="^LIFECYCLE_LOCK_DESCRIPTOR_INVALID$"):
            m.verify_lifecycle_lock(root, owner)


def test_intermediate_symlink_refused_without_new_lock(root, tmp_path):
    (root / "attempt").mkdir(mode=0o700)
    relative = "attempt/robustness"
    (root / relative).mkdir(mode=0o700)
    with m.lifecycle_lock(root, relative_directory=relative) as owner:
        moved = tmp_path / "moved"
        (root / "attempt").rename(moved)
        (root / "attempt").symlink_to(moved, target_is_directory=True)
        with pytest.raises(EvidenceError):
            with m.lifecycle_lock(
                root, relative_directory=relative, inherited_fd=owner
            ):
                pytest.fail("entered")
        assert set(p.name for p in (moved / "robustness").iterdir()) == {
            ".lifecycle.lock"
        }


def test_prepared_writer_can_borrow_in_process_without_releasing_owner(bundle):
    args, _, root, _ = bundle
    relative = f"cm-5.2/{args['attempt_id']}/robustness"
    with m.lifecycle_lock(args["report_root"], relative_directory=relative) as fd:
        assert issue_prepared(**args, lifecycle_lock_fd=fd)["status"] == "PREPARED"
        m.verify_lifecycle_lock(root, fd)
        assert contender(root).returncode != 0
    assert contender(root).returncode == 0


@pytest.mark.parametrize("pass_fd", [True, False])
def test_actual_prepared_cli_requires_real_inheritance(bundle, pass_fd):
    args, _, root, _ = bundle
    with m.lifecycle_lock(root) as fd:
        command = cli_command(args) + ["--lifecycle-lock-fd", str(fd)]
        child = subprocess.run(
            command,
            pass_fds=(fd,) if pass_fd else (),
            capture_output=True,
            text=True,
            timeout=15,
        )
        payload = json.loads(child.stdout)
        assert child.returncode == (0 if pass_fd else 1), child.stderr
        assert (root / "prepared-attempt.json").exists() is pass_fd
        assert (
            not payload["smtp_send_authorized"] and not payload["deployment_authorized"]
        )
        if not pass_fd:
            assert payload["code"] == "LIFECYCLE_LOCK_DESCRIPTOR_INVALID"
        assert "Team@example.invalid" not in child.stdout + child.stderr
        m.verify_lifecycle_lock(root, fd)
        assert contender(root).returncode != 0


def test_writer_rechecks_lock_inode_immediately_before_publication(bundle):
    args, attempt, root, _ = bundle
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / ".lifecycle.lock").rename(attempt / "old-lock")
            (root / ".lifecycle.lock").touch(mode=0o600)
        return "2026-09-05T01:03:00Z"

    with m.lifecycle_lock(root) as fd:
        args["clock"] = clock
        with pytest.raises(EvidenceError, match="^LIFECYCLE_LOCK_DESCRIPTOR_INVALID$"):
            issue_prepared(**args, lifecycle_lock_fd=fd)
        assert not (root / "prepared-attempt.json").exists()


def test_writer_failure_does_not_unlock_owner(bundle):
    args, _, root, _ = bundle
    args["capture_sha256"] = "f" * 64
    with m.lifecycle_lock(root) as fd:
        with pytest.raises(EvidenceError, match="^PREPARATION_INPUT_PIN_MISMATCH$"):
            issue_prepared(**args, lifecycle_lock_fd=fd)
        m.verify_lifecycle_lock(root, fd)
        assert contender(root).returncode != 0
        assert not (root / "prepared-attempt.json").exists()
