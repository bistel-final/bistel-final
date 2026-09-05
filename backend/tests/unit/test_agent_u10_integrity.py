"""Real private files/Git/validators; only Docker and HTTP leaves are synthetic."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from app.agent import u10_images
from app.agent import u10_integrity as subject
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_revision import read_revision_identity
from tests.unit.test_agent_u10_deployment import setup as deployment_setup
from tests.unit.test_agent_u10_evaluation import bundle
from tests.unit.test_agent_u10_revision import git, make_repo


def setup(tmp_path, monkeypatch, profile="e2e_level3", negative=False):
    repo, revision = make_repo(tmp_path / "repo")
    trees = read_revision_identity(repo, revision).evaluated_tree_oid
    args, containers, images, events = deployment_setup(monkeypatch, profile)
    monkeypatch.setattr(u10_images, "read_revision_identity", read_revision_identity)
    for image in images.values():
        image["label_revision"] = revision

    def actual_revision(a, b, r):
        a["evaluated_revision"] = revision
        r.update(evaluated_revision=revision, evaluated_tree_oid=trees.model_dump())
        r["artifact_sha256"] = digest(canonical_json(a) + b"\n")

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    files, _, _ = bundle(private, negative=negative, change=actual_revision)
    args.pop("evaluated_revision")
    args.pop("expected_trees")
    args.update(files, repository=repo)
    start = datetime(2026, 9, 5, 1, 2, 3, 123456, tzinfo=UTC)
    times = iter([start, start + timedelta(seconds=1), start + timedelta(seconds=2)])
    args["clock"] = lambda: next(times)
    return args, containers, events, revision


@pytest.mark.parametrize(
    "profile", ["production_level2", "e2e_level3", "production_level3"]
)
@pytest.mark.parametrize("negative", [False, True])
def test_all_eight_checks_bind_receipt_revision_and_preserve_verdict(
    tmp_path, monkeypatch, profile, negative
):
    args, _, _, revision = setup(tmp_path, monkeypatch, profile, negative)
    result = subject.verify_preflight_integrity(**args)
    assert result.integrity == "PASS" and result.head == revision
    assert result.repository_root == str(args["repository"].resolve())
    assert result.checked_at == "2026-09-05T01:02:05Z"
    assert (
        result.evaluation.receipt.evaluated_revision
        == result.deployment.image_bindings.evaluated_revision
        == revision
    )
    assert (
        result.evaluation.receipt.evaluated_tree_oid
        == result.deployment.image_bindings.evaluated_tree_oid
    )
    assert result.evaluation.verdict_reason == (
        "HARD_GATE_FAIL:COMPLETION" if negative else None
    )
    assert set(result.model_dump()) == {
        "integrity",
        "repository_root",
        "head",
        "profile",
        "phase",
        "checked_at",
        "evaluation",
        "deployment",
    }


def test_head_can_differ_from_receipt_revision(tmp_path, monkeypatch):
    args, _, _, revision = setup(tmp_path, monkeypatch)
    git(args["repository"], "commit", "--allow-empty", "-qm", "later checkout")
    later = git(args["repository"], "rev-parse", "HEAD")
    result = subject.verify_preflight_integrity(**args)
    assert result.head == later and later != revision
    assert result.deployment.image_bindings.evaluated_revision == revision


def test_bad_evaluation_prevents_all_runtime_io(tmp_path, monkeypatch):
    args, _, events, _ = setup(tmp_path, monkeypatch)
    args["artifact"].chmod(0o644)
    with pytest.raises(EvidenceError):
        subject.verify_preflight_integrity(**args)
    assert events == []


def test_receipt_tree_is_not_replaced_with_current_tree(tmp_path, monkeypatch):
    args, _, events, _ = setup(tmp_path, monkeypatch)
    receipt = json.loads(args["evaluation_receipt"].read_bytes())
    receipt["evaluated_tree_oid"]["backend"] = "f" * 40
    args["evaluation_receipt"].write_bytes(canonical_json(receipt))
    with pytest.raises(EvidenceError, match="^U10_EVALUATED_TREE_MISMATCH$"):
        subject.verify_preflight_integrity(**args)
    assert events == []


def test_receipt_object_format_is_checked_before_runtime(tmp_path, monkeypatch):
    args, _, events, _ = setup(tmp_path, monkeypatch)
    receipt = json.loads(args["evaluation_receipt"].read_bytes())
    receipt.update(
        git_object_format="sha256",
        evaluated_tree_oid={k: "a" * 64 for k in receipt["evaluated_tree_oid"]},
    )
    args["evaluation_receipt"].write_bytes(canonical_json(receipt))
    with pytest.raises(EvidenceError, match="^U10_RECEIPT_OBJECT_FORMAT_MISMATCH$"):
        subject.verify_preflight_integrity(**args)
    assert events == []


@pytest.mark.parametrize("file", ["artifact", "benchmark", "evaluation_receipt"])
def test_file_changes_during_runtime_are_rejected(tmp_path, monkeypatch, file):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    fetch = args["fetch"]

    def mutate(path):
        if path == "/api/health":
            args[file].write_bytes(args[file].read_bytes() + b" ")
        return fetch(path)

    args["fetch"] = mutate
    code = {
        "artifact": "U10_RECEIPT_ARTIFACT_SHA_MISMATCH",
        "benchmark": "PINNED_BENCHMARK_SHA_MISMATCH",
        "evaluation_receipt": "U10_EVALUATION_DRIFT",
    }[file]
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        subject.verify_preflight_integrity(**args)


def test_head_change_during_http_is_rejected(tmp_path, monkeypatch):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    fetch = args["fetch"]

    def mutate(path):
        if path == "/api/health":
            git(
                args["repository"],
                "commit",
                "--allow-empty",
                "-qm",
                "concurrent checkout",
            )
        return fetch(path)

    args["fetch"] = mutate
    with pytest.raises(EvidenceError, match="^U10_REPOSITORY_HEAD_DRIFT$"):
        subject.verify_preflight_integrity(**args)


def test_last_clock_backwards_within_same_second_rejected(tmp_path, monkeypatch):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    time = datetime(2026, 9, 5, microsecond=123456, tzinfo=UTC)
    times = iter([time, time, time - timedelta(microseconds=1)])
    args["clock"] = lambda: next(times)
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_TIME_INVALID$"):
        subject.verify_preflight_integrity(**args)


def test_repeated_checks_create_no_state_or_files(tmp_path, monkeypatch):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    args["clock"] = lambda: datetime(2026, 9, 5, tzinfo=UTC)
    files = {p.name: p.read_bytes() for p in args["artifact"].parent.iterdir()}
    first = subject.verify_preflight_integrity(**args)
    assert subject.verify_preflight_integrity(**args) == first
    assert {p.name: p.read_bytes() for p in args["artifact"].parent.iterdir()} == files
    assert git(args["repository"], "status", "--porcelain") == ""


def test_import_has_no_io_or_provider_loading():
    code = """
import sys, subprocess, httpx
def fail(*args, **kwargs): raise AssertionError('IO on import')
subprocess.run = fail
httpx.Client = fail
import app.agent.u10_integrity
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
