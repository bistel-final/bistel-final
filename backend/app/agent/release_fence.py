"""Persistent production admission fence shared by API and deployment wrapper.

Readers hold a shared lock through initial run registration. The operator holds
the exclusive lock across quiescence, recreation, validation and rollback. A
crash releases the OS lock but leaves CLOSED bytes, so it never reopens runs.
No database mutation or service action occurs in this module.
"""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    canonical_json,
    parse_json,
)
from app.agent.release_prepared import Attempt, Revision

FENCE_NAME = "level3-run-fence.json"


class FenceState(EvidenceModel):
    schema_version: Literal["level3-run-fence-v1"]
    state: Literal["CLOSED", "OPEN"]
    revision: Revision
    attempt_id: Attempt
    level: Literal[2, 3]


def _read(fd):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size > 4096
    ):
        raise EvidenceError("RELEASE_FENCE_INVALID")
    os.lseek(fd, 0, os.SEEK_SET)
    return FenceState.model_validate(parse_json(os.read(fd, 4097)))


@contextmanager
def admit_new_run(*, root=Path("/reports"), required_binding=None):
    """A missing fence preserves the pre-wrapper legacy Level 2 workflow.

    Production L3 supplies (revision, attempt) and requires an OPEN L3 fence
    with that exact binding. Legacy L1/L2 and isolated E2E may omit the binding;
    an existing CLOSED fence still denies all new registrations.
    """
    fd = None
    try:
        try:
            fd = os.open(root / FENCE_NAME, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if required_binding is not None:
                raise EvidenceError("RELEASE_NEW_RUNS_BLOCKED") from None
            yield
            return
        except OSError:
            raise EvidenceError("RELEASE_NEW_RUNS_BLOCKED") from None
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            value = _read(fd)
            if value.state != "OPEN" or (
                required_binding is not None
                and (
                    value.level != 3
                    or (value.revision, value.attempt_id) != required_binding
                )
            ):
                raise EvidenceError("RELEASE_NEW_RUNS_BLOCKED")
        except Exception:
            raise EvidenceError("RELEASE_NEW_RUNS_BLOCKED") from None
        yield
    finally:
        if fd is not None:
            os.close(fd)


class ProductionFence:
    """Host-owned mutable operational state, NOT a qualification artifact."""

    def __init__(self, root: Path, revision: str, attempt_id: str):
        self.root = root
        self.closed = FenceState(
            schema_version="level3-run-fence-v1",
            state="CLOSED",
            revision=revision,
            attempt_id=attempt_id,
            level=2,
        )
        self.fd = None

    def _write(self, value):
        if self.fd is None:
            raise EvidenceError("RELEASE_FENCE_NOT_LOCKED")
        payload = canonical_json(value) + b"\n"
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        written = 0
        while written < len(payload):
            size = os.write(self.fd, payload[written:])
            if size <= 0:
                raise EvidenceError("RELEASE_FENCE_WRITE_FAILED")
            written += size
        os.fsync(self.fd)

    def __enter__(self):
        fd = None
        try:
            info = self.root.stat()
            if (
                self.root.is_symlink()
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.getuid()
            ):
                raise EvidenceError("RELEASE_FENCE_ROOT_INVALID")
            fd = os.open(
                self.root / FENCE_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
            info = os.fstat(fd)
            if (
                info.st_uid != os.getuid()
                or info.st_nlink != 1
                or not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise EvidenceError("RELEASE_FENCE_INVALID")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = fd
            self._write(self.closed)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return self
        except Exception:
            if fd is not None:
                os.close(fd)
            self.fd = None
            raise EvidenceError("RELEASE_FENCE_UNAVAILABLE") from None

    def reopen(self, *, level: int):
        self._write(
            FenceState.model_validate(
                {**self.closed.model_dump(), "state": "OPEN", "level": level}
            )
        )

    def __exit__(self, exc_type, *_):
        if self.fd is not None:
            try:
                if exc_type is not None:
                    self._write(self.closed)
            finally:
                os.close(self.fd)
                self.fd = None
