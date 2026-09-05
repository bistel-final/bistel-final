"""No real Docker/DB operations; subprocess and readback are injected."""

import copy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.agent import u10_runtime as subject
from app.agent.release_artifacts import EvidenceError, canonical_json

ATTEMPT = "20260905T010203Z-123456abcdef"
PROFILES = {
    "production_level2": ("kosa_agent", 2, False),
    "e2e_level3": ("kosa_agent_e2e", 3, True),
    "production_level3": ("kosa_agent", 3, True),
}


def payload(profile="e2e_level3"):
    database, level, enabled = PROFILES[profile]
    return {
        "schema_version": "agent-runtime-readback-v1",
        "status": "PASS",
        "profile": profile,
        "database": database,
        "database_user": "kosa_app",
        "autonomy_level": level,
        "level3_enabled": enabled,
        "demo_ack": ATTEMPT if profile == "production_level3" else None,
        "ack_matches_receipt": profile == "production_level3",
        "budget_policy": {
            "level12_total": 8,
            "level3_total": 10,
            "send": 2,
            "same_tool_attempts": 4,
            "selector_steps": 10,
        },
    }


def inputs(profile="e2e_level3"):
    ids = {"backend": "1" * 64}
    if profile == "e2e_level3":
        ids["runner"] = "2" * 64
    values = {identifier: payload(profile) for identifier in ids.values()}
    calls = []

    def read(identifier, selected):
        calls.append((identifier, selected))
        return copy.deepcopy(values[identifier])

    return (
        {
            "profile": profile,
            "container_ids": ids,
            "read": read,
            "expected_attempt_id": ATTEMPT if profile == "production_level3" else None,
        },
        values,
        calls,
    )


@pytest.mark.parametrize("profile", PROFILES)
def test_each_profile_reads_exact_roles_and_no_enable_claim(profile):
    args, _, calls = inputs(profile)
    result = subject.verify_runtime_readbacks(**args).model_dump(mode="json")
    assert calls == [
        (identifier, profile) for identifier in args["container_ids"].values()
    ]
    assert result == {
        "profile": profile,
        "container_ids": args["container_ids"],
        "readbacks": {role: payload(profile) for role in args["container_ids"]},
    }


@pytest.mark.parametrize("role", ["backend", "runner"])
@pytest.mark.parametrize(
    "key,value",
    [
        ("profile", "production_level3"),
        ("database", "kosa_agent"),
        ("database_user", "kosa_admin"),
        ("autonomy_level", 2),
        ("level3_enabled", False),
        ("autonomy_level", 3.0),
        ("level3_enabled", 1),
        ("status", "FAIL"),
        ("schema_version", "unknown"),
        ("secret", "private"),
        ("ack_matches_receipt", 0),
    ],
)
def test_each_role_drift_or_noncanonical_payload_fails(role, key, value):
    args, values, calls = inputs()
    values[args["container_ids"][role]][key] = value
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READBACK_INVALID$"):
        subject.verify_runtime_readbacks(**args)
    assert len(calls) == (1 if role == "backend" else 2)


@pytest.mark.parametrize("key", payload()["budget_policy"])
@pytest.mark.parametrize("kind", ["drift", "float"])
def test_budget_is_code_owned_and_strict(key, kind):
    args, values, _ = inputs()
    budget = values["1" * 64]["budget_policy"]
    budget[key] = budget[key] + 1 if kind == "drift" else float(budget[key])
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READBACK_INVALID$"):
        subject.verify_runtime_readbacks(**args)


@pytest.mark.parametrize(
    "ack,matched",
    [(ATTEMPT, False), ("20260905T010204Z-123456abcdef", True), (None, True)],
)
def test_production_ack_must_match_independent_attempt_and_container_receipt(
    ack, matched
):
    args, values, _ = inputs("production_level3")
    values["1" * 64].update(demo_ack=ack, ack_matches_receipt=matched)
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READBACK_INVALID$"):
        subject.verify_runtime_readbacks(**args)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda a: a.update(profile="unknown"), "PROFILE"),
        (lambda a: a.update(profile=[]), "PROFILE"),
        (lambda a: a["container_ids"].pop("runner"), "ROLES"),
        (lambda a: a["container_ids"].update(frontend="3" * 64), "ROLES"),
        (lambda a: a["container_ids"].update(runner="1" * 64), "CONTAINER"),
        (lambda a: a["container_ids"].update(backend="backend-name"), "CONTAINER"),
        (lambda a: a.update(expected_attempt_id=ATTEMPT), "ATTEMPT"),
        (
            lambda a: a.update(
                profile="production_level3", container_ids={"backend": "1" * 64}
            ),
            "ATTEMPT",
        ),
    ],
)
def test_bad_inputs_fail_before_read(change, code):
    args, _, calls = inputs()
    change(args)
    with pytest.raises(EvidenceError, match=f"^U10_RUNTIME_{code}_INVALID$"):
        subject.verify_runtime_readbacks(**args)
    assert calls == []


@pytest.mark.parametrize("value", [None, [], {"status": "PASS"}])
def test_readback_missing_or_nonobject_is_rejected(value):
    args, _, _ = inputs()
    args["read"] = lambda *_: value
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READBACK_INVALID$"):
        subject.verify_runtime_readbacks(**args)


@pytest.mark.parametrize("profile", ["production_level2", "production_level3"])
def test_production_rejects_runner_before_read(profile):
    args, _, calls = inputs(profile)
    args["container_ids"]["runner"] = "2" * 64
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_ROLES_INVALID$"):
        subject.verify_runtime_readbacks(**args)
    assert calls == []


@pytest.mark.parametrize("attempt", ["invalid", ATTEMPT + "\n", True])
def test_expected_attempt_must_be_canonical_before_read(attempt):
    args, _, calls = inputs("production_level3")
    args["expected_attempt_id"] = attempt
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_ATTEMPT_INVALID$"):
        subject.verify_runtime_readbacks(**args)
    assert calls == []


@pytest.mark.parametrize(
    "error",
    [
        ValueError("private"),
        OSError("private"),
        subprocess.TimeoutExpired("private", 30),
    ],
)
def test_injected_read_errors_are_sanitized(error):
    args, _, _ = inputs()

    def read(*_):
        raise error

    args["read"] = read
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READBACK_INVALID$"):
        subject.verify_runtime_readbacks(**args)


def test_docker_exec_is_pinned_read_only_and_uses_existing_script(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0, stdout=canonical_json(payload()), stderr=b""
        )

    monkeypatch.setattr(subject.subprocess, "run", run)
    assert subject.docker_readback("1" * 64, "e2e_level3") == payload()
    assert calls == [
        (
            [
                "docker",
                "exec",
                "1" * 64,
                "python",
                "-B",
                "/workspace/backend/scripts/read_agent_runtime.py",
                "--profile",
                "e2e_level3",
            ],
            {"capture_output": True, "timeout": 30, "check": False},
        )
    ]


@pytest.mark.parametrize(
    "identifier,profile",
    [("backend", "e2e_level3"), (None, "e2e_level3"), ("1" * 64, "unknown")],
)
def test_public_docker_readback_validates_before_subprocess(
    monkeypatch, identifier, profile
):
    monkeypatch.setattr(
        subject.subprocess, "run", lambda *_a, **_k: pytest.fail("process called")
    )
    with pytest.raises(
        EvidenceError, match="^U10_RUNTIME_(CONTAINER|PROFILE)_INVALID$"
    ):
        subject.docker_readback(identifier, profile)


@pytest.mark.parametrize(
    "body,exit_code",
    [
        (b"private", 1),
        (b"[]", 0),
        (b"not-json", 0),
        (b'{"x":1,"x":2}', 0),
        (b'{"x":NaN}', 0),
        pytest.param(canonical_json({"x": "x" * 16384}), 0, id="valid-json-over-cap"),
    ],
)
def test_docker_output_errors_are_sanitized(monkeypatch, body, exit_code):
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            stdout=body,
            stderr=b"private-error",
            returncode=exit_code,
        ),
    )
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READ_FAILED$"):
        subject.docker_readback("1" * 64, "e2e_level3")


@pytest.mark.parametrize(
    "error", [OSError("private"), subprocess.TimeoutExpired("private", 30)]
)
def test_docker_process_errors_are_sanitized(monkeypatch, error):
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(subject.subprocess, "run", run)
    with pytest.raises(EvidenceError, match="^U10_RUNTIME_READ_FAILED$"):
        subject.docker_readback("1" * 64, "e2e_level3")


def test_import_is_lazy_without_config_db_or_processes():
    code = """
import sys, subprocess
def fail(*args, **kwargs): raise AssertionError('process on import')
subprocess.run = fail
import app.agent.u10_runtime
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.agent.react' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
