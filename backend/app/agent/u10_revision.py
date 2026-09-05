"""Local Git identity checks for U10; no fetch, checkout, image or artifact IO.

These are point-in-time observations, not proof of remote merge/CI approval or
an exclusive workspace lock. Live callers must keep the tree immutable while
running and bind image labels/receipts separately.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from app.agent.release_artifacts import EvidenceError, EvidenceModel

TREE_PATHS = ("backend", "frontend", "deploy")


class RevisionTrees(EvidenceModel):
    backend: str
    frontend: str
    deploy: str


class RevisionIdentity(EvidenceModel):
    repository_root: str
    evaluated_revision: str
    git_object_format: Literal["sha1", "sha256"]
    evaluated_tree_oid: RevisionTrees


def _git(repository: Path, *arguments: str) -> str:
    # Do not inherit GIT_DIR/WORK_TREE/INDEX_FILE/CONFIG overrides that could
    # silently inspect a different repository. Never echo environment or stderr.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_NO_LAZY_FETCH="1",
        LC_ALL="C",
    )
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repository),
                *arguments,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise EvidenceError("U10_GIT_READ_FAILED") from None
    if result.returncode:
        raise EvidenceError("U10_GIT_READ_FAILED")
    return result.stdout.strip()


def _root(repository: Path) -> Path:
    try:
        root = repository.resolve(strict=True)
        actual = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    except OSError:
        raise EvidenceError("U10_REPOSITORY_INVALID") from None
    if root != actual:
        raise EvidenceError("U10_REPOSITORY_ROOT_MISMATCH")
    return root


def read_revision_identity(repository: Path, revision: str) -> RevisionIdentity:
    """Read an explicit full commit and its three trees, never implicit HEAD.

    This low-level reader accepts full SHA-1/SHA-256 object IDs. The execution
    guard below retains the current U10 artifact's 40-character revision contract.
    """
    if type(revision) is not str or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", revision
    ):
        raise EvidenceError("U10_REVISION_INVALID")
    root = _root(repository)
    object_format = _git(root, "rev-parse", "--show-object-format")
    size = {"sha1": 40, "sha256": 64}.get(object_format)
    if size is None or len(revision) != size:
        raise EvidenceError("U10_GIT_OBJECT_FORMAT_INVALID")
    if _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}") != revision:
        raise EvidenceError("U10_REVISION_COMMIT_MISMATCH")
    trees = {}
    for path in TREE_PATHS:
        reference = f"{revision}:{path}"
        if _git(root, "cat-file", "-t", reference) != "tree":
            raise EvidenceError("U10_REVISION_TREE_INVALID")
        oid = _git(root, "rev-parse", "--verify", reference)
        if not re.fullmatch(rf"[0-9a-f]{{{size}}}", oid):
            raise EvidenceError("U10_REVISION_TREE_INVALID")
        trees[path] = oid
    return RevisionIdentity(
        repository_root=str(root),
        evaluated_revision=revision,
        git_object_format=object_format,
        evaluated_tree_oid=RevisionTrees(**trees),
    )


def _execution_state(root: Path, expected_revision: str) -> None:
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise EvidenceError("U10_MAIN_REQUIRED")
    if _git(root, "rev-parse", "--verify", "HEAD^{commit}") != expected_revision:
        raise EvidenceError("U10_EXECUTION_REVISION_MISMATCH")
    if _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ):
        raise EvidenceError("U10_WORKTREE_NOT_CLEAN")


def verify_execution_revision(
    repository: Path, expected_revision: str
) -> RevisionIdentity:
    """Require local clean main at pinned R before and after reading tree OIDs.

    Does not pull main or confirm remote CI/merge. Status uses Git's ordinary
    ignored-file rules; ignored provider caches are not attestations of runtime
    bytecode. Recheck at use and provide isolation in the live runner.
    """
    if type(expected_revision) is not str or not re.fullmatch(
        r"[0-9a-f]{40}", expected_revision
    ):
        raise EvidenceError("U10_REVISION_INVALID")
    root = _root(repository)
    _execution_state(root, expected_revision)
    identity = read_revision_identity(root, expected_revision)
    _execution_state(root, expected_revision)
    return identity
