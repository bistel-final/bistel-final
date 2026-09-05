"""Test-only inspect projections: no Docker daemon, deployment or real images."""

import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent import u10_images as subject
from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.u10_revision import RevisionIdentity, RevisionTrees

R = "a" * 40
START = "2026-09-05T01:02:03.123456789Z"


def inputs(monkeypatch, profile="e2e_level3"):
    roles = (
        ["backend", "frontend", "runner"]
        if profile == "e2e_level3"
        else ["backend", "frontend"]
    )
    image_ids = {
        role: "sha256:" + ("1" if role != "frontend" else "2") * 64 for role in roles
    }
    container_ids = {role: str(n) * 64 for n, role in enumerate(roles, 4)}
    trees = RevisionTrees(backend="b" * 40, frontend="c" * 40, deploy="d" * 40)
    git_calls, inspect_calls = [], []

    def revision(repository, value):
        git_calls.append((repository, value))
        return RevisionIdentity(
            repository_root="/explicit/repository",
            evaluated_revision=value,
            git_object_format="sha1",
            evaluated_tree_oid=trees,
        )

    monkeypatch.setattr(subject, "read_revision_identity", revision)
    containers = {
        container_ids[role]: dict(
            container_id=container_ids[role],
            image_id=image_ids[role],
            running=True,
            status="running",
            paused=False,
            restarting=False,
            started_at=START,
            project="bistel-team-e2e" if profile == "e2e_level3" else "bistel-team",
            service="e2e-runner" if role == "runner" else role,
        )
        for role in roles
    }
    images = {
        value: dict(image_id=value, label_revision=R) for value in image_ids.values()
    }

    def inspect(kind, identifier):
        inspect_calls.append((kind, identifier))
        return deepcopy((containers if kind == "container" else images)[identifier])

    return (
        dict(
            repository="/explicit/repository",
            evaluated_revision=R,
            expected_trees=trees,
            profile=profile,
            expected_image_ids=image_ids,
            container_ids=container_ids,
            inspect=inspect,
        ),
        containers,
        images,
        git_calls,
        inspect_calls,
    )


@pytest.mark.parametrize(
    "profile", ["production_level2", "production_level3", "e2e_level3"]
)
def test_all_roles_bind_to_pinned_images_and_explicit_label_trees(monkeypatch, profile):
    params, containers, images, git_calls, calls = inputs(monkeypatch, profile)
    result = subject.verify_image_bindings(**params)
    roles = list(params["container_ids"])
    assert list(result.images) == list(result.containers) == roles
    assert (
        result.repository_root == params["repository"]
        and result.evaluated_revision == R
    )
    assert all(v == R for _, v in git_calls) and len(git_calls) == len(roles) + 1
    assert calls == [
        pair
        for role in roles
        for pair in [
            ("container", params["container_ids"][role]),
            ("image", params["expected_image_ids"][role]),
        ]
    ] + [("container", params["container_ids"][role]) for role in roles]
    if "runner" in roles:
        assert result.images["runner"].image_id == result.images["backend"].image_id
        assert (
            result.containers["runner"].container_id
            != result.containers["backend"].container_id
        )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("profile", "custom", "U10_IMAGE_PROFILE_INVALID"),
        ("evaluated_revision", "short", "U10_REVISION_INVALID"),
        ("container_ids", {}, "U10_IMAGE_ROLES_INVALID"),
        ("expected_image_ids", {}, "U10_IMAGE_ROLES_INVALID"),
    ],
)
def test_invalid_inputs_fail_before_git_or_docker(monkeypatch, field, value, code):
    params, _, _, git_calls, calls = inputs(monkeypatch)
    params[field] = value
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        subject.verify_image_bindings(**params)
    assert git_calls == calls == []


@pytest.mark.parametrize(
    "field,value",
    [("expected_image_ids", "bistel-backend:latest"), ("container_ids", "backend")],
)
def test_mutable_names_are_not_immutable_pins(monkeypatch, field, value):
    params, _, _, git_calls, calls = inputs(monkeypatch)
    params[field]["backend"] = value
    with pytest.raises(EvidenceError, match="^U10_DOCKER_ID_INVALID$"):
        subject.verify_image_bindings(**params)
    assert git_calls == calls == []


def test_runner_cannot_reuse_backend_container(monkeypatch):
    params, _, _, git_calls, calls = inputs(monkeypatch)
    params["container_ids"]["runner"] = params["container_ids"]["backend"]
    with pytest.raises(EvidenceError, match="^U10_CONTAINER_REUSED$"):
        subject.verify_image_bindings(**params)
    assert git_calls == calls == []


def test_receipt_tree_mismatch_prevents_docker_inspection(monkeypatch):
    params, _, _, _, calls = inputs(monkeypatch)
    params["expected_trees"] = params["expected_trees"].model_copy(
        update={"deploy": "0" * 40}
    )
    with pytest.raises(EvidenceError, match="^U10_EVALUATED_TREE_MISMATCH$"):
        subject.verify_image_bindings(**params)
    assert calls == []


@pytest.mark.parametrize("role", ["backend", "frontend", "runner"])
@pytest.mark.parametrize(
    "field,value,code",
    [
        ("project", "other-project", "U10_CONTAINER_BINDING_MISMATCH"),
        ("service", "wrong-service", "U10_CONTAINER_BINDING_MISMATCH"),
        ("image_id", "sha256:" + "9" * 64, "U10_CONTAINER_BINDING_MISMATCH"),
        ("container_id", "9" * 64, "U10_CONTAINER_BINDING_MISMATCH"),
        ("running", False, "U10_CONTAINER_NOT_RUNNING"),
        ("running", 1, "U10_CONTAINER_NOT_RUNNING"),
        ("status", "exited", "U10_CONTAINER_NOT_RUNNING"),
        ("paused", True, "U10_CONTAINER_NOT_RUNNING"),
        ("restarting", True, "U10_CONTAINER_NOT_RUNNING"),
        ("started_at", "bad", "U10_CONTAINER_INSPECT_INVALID"),
    ],
)
def test_each_role_is_checked_even_when_image_is_shared(
    monkeypatch, role, field, value, code
):
    params, containers, _, _, _ = inputs(monkeypatch)
    containers[params["container_ids"][role]][field] = value
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        subject.verify_image_bindings(**params)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("image_id", "sha256:" + "9" * 64, "U10_IMAGE_BINDING_MISMATCH"),
        ("label_revision", "b" * 40, "U10_IMAGE_BINDING_MISMATCH"),
        ("label_revision", None, "U10_IMAGE_INSPECT_INVALID"),
    ],
)
def test_frontend_image_is_not_covered_by_backend_validation(
    monkeypatch, field, value, code
):
    params, _, images, _, _ = inputs(monkeypatch)
    images[params["expected_image_ids"]["frontend"]][field] = value
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        subject.verify_image_bindings(**params)


def test_image_label_tree_is_rechecked_against_receipt(monkeypatch):
    params, _, _, _, _ = inputs(monkeypatch)
    actual = subject.read_revision_identity
    count = 0

    def changed(*args):
        nonlocal count
        count += 1
        identity = actual(*args)
        return (
            identity
            if count == 1
            else identity.model_copy(
                update={
                    "evaluated_tree_oid": identity.evaluated_tree_oid.model_copy(
                        update={"backend": "0" * 40}
                    )
                }
            )
        )

    monkeypatch.setattr(subject, "read_revision_identity", changed)
    with pytest.raises(EvidenceError, match="^U10_IMAGE_TREE_MISMATCH$"):
        subject.verify_image_bindings(**params)


def test_restart_between_inspections_invalidates_observation(monkeypatch):
    params, _, _, _, _ = inputs(monkeypatch)
    actual = params["inspect"]
    counts = {}

    def restarted(kind, identifier):
        row = actual(kind, identifier)
        counts[kind, identifier] = counts.get((kind, identifier), 0) + 1
        if kind == "container" and counts[kind, identifier] == 2:
            row["started_at"] = "2026-09-05T02:00:00Z"
        return row

    params["inspect"] = restarted
    with pytest.raises(EvidenceError, match="^U10_CONTAINER_DRIFT$"):
        subject.verify_image_bindings(**params)


def test_raw_env_is_not_accepted_in_container_projection(monkeypatch):
    params, containers, _, _, _ = inputs(monkeypatch)
    containers[params["container_ids"]["backend"]]["Env"] = ["SECRET=test-only"]
    with pytest.raises(EvidenceError, match="^U10_CONTAINER_INSPECT_INVALID$"):
        subject.verify_image_bindings(**params)


@pytest.mark.parametrize("kind", ["image", "container"])
def test_docker_command_is_read_only_and_projects_no_secrets(monkeypatch, kind):
    captured = []

    def run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(
            returncode=0, stdout=canonical_json({"test": "projection"}), stderr=b""
        )

    monkeypatch.setattr(subject.subprocess, "run", run)
    identifier = "1" * 64 if kind == "container" else "sha256:" + "1" * 64
    assert subject.docker_inspect(kind, identifier) == {"test": "projection"}
    command = captured[0]
    assert (
        command[:4] == ["docker", kind, "inspect", "--format"]
        and command[-1] == identifier
    )
    assert "Config.Env" not in command[4] and "Mounts" not in command[4]
    assert (
        "{{json .}}" not in command[4] and "{{json .Config.Labels}}" not in command[4]
    )


@pytest.mark.parametrize(
    "stdout,returncode",
    [
        (b"[]", 0),
        (b"not json", 0),
        (b'{"x":1,"x":2}', 0),
        (b"x" * 16385, 0),
        (b"private-output", 1),
    ],
)
def test_bad_inspect_output_is_sanitized(monkeypatch, stdout, returncode):
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=stdout, stderr=b"private-stderr", returncode=returncode
        ),
    )
    with pytest.raises(EvidenceError, match="^U10_DOCKER_INSPECT_INVALID$"):
        subject.docker_inspect("container", "1" * 64)


@pytest.mark.parametrize(
    "error", [OSError("private-path"), subprocess.TimeoutExpired("private-command", 10)]
)
def test_docker_process_error_is_sanitized(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(subject.subprocess, "run", fail)
    with pytest.raises(EvidenceError, match="^U10_DOCKER_INSPECT_INVALID$"):
        subject.docker_inspect("image", "sha256:" + "1" * 64)


def test_import_does_not_call_docker_git_or_load_providers():
    code = """
import sys, subprocess
def fail(*args, **kwargs): raise AssertionError('process on import')
subprocess.run = fail
import app.agent.u10_images
assert 'app.agent.graph' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
