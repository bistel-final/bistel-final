"""Real local Git checks on disposable repositories; no project commits/network."""

import os
import subprocess
import sys

import pytest

from app.agent import u10_revision as subject
from app.agent.release_artifacts import EvidenceError


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=U10 Test",
            "-c",
            "user.email=u10@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repo),
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def make_repo(repo, *, object_format="sha1"):
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main", f"--object-format={object_format}")
    for name in ("backend", "frontend", "deploy"):
        (repo / name).mkdir()
        (repo / name / "source.txt").write_text(name)
    (repo / ".gitignore").write_text("ignored.cache\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture baseline")
    return repo, git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    return make_repo(tmp_path / "저장소 with spaces")


def test_clean_main_returns_explicit_root_full_revision_and_real_tree_oids(repository):
    repo, revision = repository
    identity = subject.verify_execution_revision(repo, revision)
    assert identity.repository_root == str(repo.resolve())
    assert identity.evaluated_revision == revision
    assert identity.git_object_format == "sha1"
    assert identity.evaluated_tree_oid.model_dump() == {
        name: git(repo, "rev-parse", f"{revision}:{name}")
        for name in ("backend", "frontend", "deploy")
    }
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == revision


def test_low_level_reader_uses_requested_commit_not_head(repository):
    repo, revision = repository
    before = subject.read_revision_identity(repo, revision)
    (repo / "backend/source.txt").write_text("new source")
    git(repo, "commit", "-qam", "fixture successor")
    assert subject.read_revision_identity(repo, revision) == before
    with pytest.raises(EvidenceError, match="^U10_EXECUTION_REVISION_MISMATCH$"):
        subject.verify_execution_revision(repo, revision)


@pytest.mark.parametrize("state", ["unstaged", "staged", "untracked", "deleted"])
def test_dirty_tree_is_rejected_without_cleaning(repository, state):
    repo, revision = repository
    source = repo / "backend/source.txt"
    if state == "deleted":
        source.unlink()
    elif state == "untracked":
        (repo / "new.py").write_text("untracked")
    else:
        source.write_text("modified")
        if state == "staged":
            git(repo, "add", "backend/source.txt")
    before = git(repo, "status", "--porcelain")
    with pytest.raises(EvidenceError, match="^U10_WORKTREE_NOT_CLEAN$"):
        subject.verify_execution_revision(repo, revision)
    assert git(repo, "status", "--porcelain") == before


@pytest.mark.parametrize("branch", ["feature", "detached"])
def test_feature_or_detached_checkout_cannot_be_execution_main(repository, branch):
    repo, revision = repository
    if branch == "detached":
        git(repo, "checkout", "--detach", "-q", revision)
    else:
        git(repo, "checkout", "-qb", "feature")
    with pytest.raises(EvidenceError, match="^U10_MAIN_REQUIRED$"):
        subject.verify_execution_revision(repo, revision)


@pytest.mark.parametrize(
    "revision", [None, "main", "a" * 39, "A" * 40, "a" * 64, "--help"]
)
def test_execution_requires_full_lowercase_40_hex_before_git(monkeypatch, revision):
    monkeypatch.setattr(
        subject, "_git", lambda *_: pytest.fail("Git before input validation")
    )
    with pytest.raises(EvidenceError, match="^U10_REVISION_INVALID$"):
        subject.verify_execution_revision(None, revision)


def test_nested_path_is_not_implicitly_treated_as_repository_root(repository):
    repo, revision = repository
    with pytest.raises(EvidenceError, match="^U10_REPOSITORY_ROOT_MISMATCH$"):
        subject.verify_execution_revision(repo / "backend", revision)


def test_blob_at_required_tree_path_is_rejected(repository):
    repo, _ = repository
    (repo / "deploy/source.txt").unlink()
    (repo / "deploy").rmdir()
    (repo / "deploy").write_text("not a tree")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixture invalid tree")
    with pytest.raises(EvidenceError, match="^U10_REVISION_TREE_INVALID$"):
        subject.verify_execution_revision(repo, git(repo, "rev-parse", "HEAD"))


def test_missing_tree_is_not_a_successful_empty_tree(repository):
    repo, _ = repository
    (repo / "deploy/source.txt").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixture missing tree")
    with pytest.raises(EvidenceError, match="^U10_GIT_READ_FAILED$"):
        subject.verify_execution_revision(repo, git(repo, "rev-parse", "HEAD"))


def test_environment_cannot_redirect_repository_or_write_trace(
    repository, tmp_path, monkeypatch
):
    repo, revision = repository
    other, _ = make_repo(tmp_path / "other")
    trace = tmp_path / "forbidden-trace"
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_TRACE", str(trace))
    result = subject.verify_execution_revision(repo, revision)
    assert result.repository_root == str(repo.resolve()) and not trace.exists()


def test_replace_refs_do_not_rewrite_observed_commit_trees(repository):
    repo, revision = repository
    expected = subject.read_revision_identity(repo, revision)
    (repo / "backend/source.txt").write_text("replacement")
    git(repo, "commit", "-qam", "fixture replacement")
    git(repo, "replace", revision, git(repo, "rev-parse", "HEAD"))
    assert subject.read_revision_identity(repo, revision) == expected


@pytest.mark.parametrize("change", ["dirty", "head", "branch"])
def test_state_is_rechecked_after_tree_observation(repository, monkeypatch, change):
    repo, revision = repository
    original = subject.read_revision_identity

    def changed(*args):
        result = original(*args)
        if change in {"dirty", "head"}:
            (repo / "backend/source.txt").write_text("changed during observation")
            if change == "head":
                git(repo, "commit", "-qam", "fixture concurrent commit")
        else:
            git(repo, "checkout", "-qb", "feature")
        return result

    monkeypatch.setattr(subject, "read_revision_identity", changed)
    code = {
        "dirty": "U10_WORKTREE_NOT_CLEAN",
        "head": "U10_EXECUTION_REVISION_MISMATCH",
        "branch": "U10_MAIN_REQUIRED",
    }[change]
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        subject.verify_execution_revision(repo, revision)


def test_ignored_cache_is_not_misrepresented_as_tracked_source(repository):
    repo, revision = repository
    (repo / "ignored.cache").write_text("cache")
    assert (
        subject.verify_execution_revision(repo, revision).evaluated_revision == revision
    )


@pytest.mark.parametrize(
    "error", [OSError("private path"), subprocess.TimeoutExpired("private command", 10)]
)
def test_git_process_errors_are_sanitized(repository, monkeypatch, error):
    repo, revision = repository

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(subject.subprocess, "run", fail)
    with pytest.raises(EvidenceError, match="^U10_GIT_READ_FAILED$"):
        subject.read_revision_identity(repo, revision)


def test_sha256_identity_reader_does_not_confuse_git_oids_with_file_sha(tmp_path):
    repo, revision = make_repo(tmp_path / "sha256", object_format="sha256")
    identity = subject.read_revision_identity(repo, revision)
    assert identity.git_object_format == "sha256" and len(revision) == 64
    assert all(len(v) == 64 for v in identity.evaluated_tree_oid.model_dump().values())
    # Current U10 artifact schema still requires a 40-character execution R.
    with pytest.raises(EvidenceError, match="^U10_REVISION_INVALID$"):
        subject.verify_execution_revision(repo, revision)


def test_import_has_no_provider_or_git_side_effects():
    code = """
import sys, subprocess
def forbidden(*args, **kwargs):
    raise AssertionError('Git on import')
subprocess.run = forbidden
import app.agent.u10_revision
assert 'app.agent.graph' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
