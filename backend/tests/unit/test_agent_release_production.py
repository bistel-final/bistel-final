"""Concrete adapter contracts without running Docker or mutating shared env."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.agent import release_production as subject
from app.agent.release_artifacts import EvidenceError
from app.agent.release_quiescence import read_quiescence
from scripts.enable_production_level3 import main
from tests.unit.test_agent_release import ATTEMPT, REV


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env.team"
    p.write_bytes(
        b"# private comment\r\nSECRET=do-not-output\r\nAGENT_AUTONOMY_LEVEL=2\r\n"
        b"AGENT_LEVEL3_ENABLED=false\r\nAGENT_LEVEL3_DEMO_ACK=\r\nOTHER=keep-exact"
    )
    p.chmod(0o600)
    return p


def test_three_key_update_preserves_unrelated_lines_and_recovery(env_file):
    before = env_file.read_bytes()
    after = subject.update_env(env_file, before, level=3, attempt=ATTEMPT)
    assert b"SECRET=do-not-output\r\n" in after and b"OTHER=keep-exact\n" in after
    assert b"AGENT_AUTONOMY_LEVEL=3\r\n" in after
    assert b"AGENT_LEVEL3_ENABLED=true\r\n" in after
    assert f"AGENT_LEVEL3_DEMO_ACK={ATTEMPT}\r\n".encode() in after
    restored = subject.update_env(env_file, after, level=2, attempt=ATTEMPT)
    assert restored == before + b"\nAGENT_ACTION_POLICY=ACTION-POLICY-V1\n"


@pytest.mark.parametrize(
    "fault", ["drift", "duplicate", "mode", "symlink", "invalid_level", "bad_attempt"]
)
def test_invalid_env_input_never_replaces_user_bytes(env_file, fault):
    before = env_file.read_bytes()
    expected = before
    if fault == "drift":
        expected = b"stale"
    if fault == "duplicate":
        env_file.write_bytes(before + b"\nexport AGENT_LEVEL3_ENABLED=true\n")
        expected = env_file.read_bytes()
    if fault == "mode":
        env_file.chmod(0o644)
    if fault == "symlink":
        other = env_file.with_suffix(".saved")
        env_file.rename(other)
        env_file.symlink_to(other)
    existing = env_file.read_bytes()
    with pytest.raises((EvidenceError, OSError)):
        subject.update_env(
            env_file,
            expected,
            level=True if fault == "invalid_level" else 3,
            attempt="bad" if fault == "bad_attempt" else ATTEMPT,
        )
    assert env_file.read_bytes() == existing


def test_compose_recreation_is_exact_fixed_scope(env_file, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LEVEL3_ENABLED", "host-must-not-win")
    monkeypatch.setenv("CM52_PIN_BACKEND_IMAGE", "host-tag")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"")

    ports = subject.ProductionPorts(
        repository=tmp_path,
        env_file=env_file,
        report_root=tmp_path,
        artifact=tmp_path / "aggregate.json",
        published_root=tmp_path,
        revision=REV,
        attempt_id=ATTEMPT,
        image_ids={"backend": "sha256:" + "a" * 64, "frontend": "sha256:" + "b" * 64},
        preflight_arguments=[],
        run=run,
    )
    ports.recreate()
    argv, kwargs = calls[0]
    assert (
        argv[-10:]
        == [
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "backend",
            "frontend",
        ][-11:]
    )
    assert "kafka" not in argv and "down" not in argv and "build" not in argv
    assert kwargs["env"]["CM52_PIN_BACKEND_IMAGE"] == "sha256:" + "a" * 64
    assert "AGENT_LEVEL3_ENABLED" not in kwargs["env"]


@pytest.mark.parametrize(
    "identity,count,ok",
    [
        (("kosa_agent", "kosa_app"), 0, True),
        (("kosa_agent", "kosa_app"), 2, True),
        (("kosa_agent_e2e", "kosa_app"), 0, False),
        (("kosa_agent", "owner"), 0, False),
        (("kosa_agent", "kosa_app"), True, False),
    ],
)
def test_quiescence_uses_read_only_app_transaction(identity, count, ok):
    calls = []

    class Connection:
        def exec_driver_sql(self, sql):
            calls.append(sql)

        def execute(self, sql):
            calls.append(str(sql))
            return SimpleNamespace(one=lambda: identity, scalar_one=lambda: count)

    @contextmanager
    def connect():
        yield Connection()

    engine = SimpleNamespace(
        url=SimpleNamespace(database="kosa_agent", username="kosa_app"), connect=connect
    )
    if ok:
        assert read_quiescence(engine)["active_runs"] == count
    else:
        with pytest.raises(EvidenceError):
            read_quiescence(engine)
    assert calls[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert not any(word in " ".join(calls) for word in ("UPDATE", "DELETE", "INSERT"))


@pytest.mark.parametrize("argv", [[], ["--secret", "do-not-print"]])
def test_cli_usage_has_no_side_effect_or_secret_output(argv, capsys):
    assert main(argv, ports_factory=lambda **_: pytest.fail("no side effect")) == 1
    report = json.loads(capsys.readouterr().out)
    assert report == {"status": "FAIL", "code": "U10_CLI_ARGUMENT_INVALID"}
