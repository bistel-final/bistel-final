"""Synthetic PG identity/transaction boundary; never connects to a database."""

import json
import subprocess
import sys
import traceback
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent import release_context as m
from app.agent.release_artifacts import EvidenceError

SECRET = "private-password-or-address-must-not-escape"
CID = "4" * 64


class Engine:
    def __init__(self):
        self.url = SimpleNamespace(
            database="kosa_agent_e2e", username="kosa_app", host="postgres"
        )
        self.identity = dict(
            database_name="kosa_agent_e2e",
            role_name="kosa_app",
            read_only="on",
            isolation="repeatable read",
            system_identifier="12345",
        )
        self.events = []

    def connect(self):
        self.events.append("CONNECT")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.events.append("ROLLBACK_CLOSE")

    def exec_driver_sql(self, sql):
        self.events.append(sql)

    def execute(self, statement):
        assert statement is m.IDENTITY_SQL
        self.events.append(str(statement))
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(one=lambda: deepcopy(self.identity))
        )


def settings():
    return SimpleNamespace(
        AGENT_AUTONOMY_LEVEL=3,
        AGENT_LEVEL3_ENABLED=True,
        AGENT_LEVEL3_DEMO_ACK=None,
        AGENT_EMAIL_RECIPIENTS=" Team@EXAMPLE.invalid , Owner@example.invalid ",
    )


def projection():
    return m.read_context(Engine(), settings())


def test_actual_projection_only_identity_sql_and_rollback():
    engine = Engine()
    result = m.read_context(engine, settings())
    assert result.identity.system_identifier == "12345"
    assert result.recipients == ["Owner@example.invalid", "Team@example.invalid"]
    assert engine.events == [
        "CONNECT",
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SET LOCAL statement_timeout = '10s'",
        "SET LOCAL lock_timeout = '2s'",
        str(m.IDENTITY_SQL),
        "ROLLBACK_CLOSE",
    ]
    assert set(result.model_dump()) == {"schema_version", "identity", "recipients"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("database", "kosa_agent"),
        ("username", "postgres"),
        ("host", "postgres/secret"),
        ("host", None),
    ],
)
def test_target_rejected_before_connect(field, value):
    engine = Engine()
    setattr(engine.url, field, value)
    with pytest.raises(EvidenceError, match="^PREPARATION_CONTEXT_TARGET_INVALID$"):
        m.read_context(engine, settings())
    assert engine.events == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("AGENT_AUTONOMY_LEVEL", 2),
        ("AGENT_AUTONOMY_LEVEL", True),
        ("AGENT_LEVEL3_ENABLED", 1),
        ("AGENT_LEVEL3_ENABLED", False),
        ("AGENT_LEVEL3_DEMO_ACK", "old-attempt"),
    ],
)
def test_non_prepare_env_rejected_before_connect(field, value):
    engine, config = Engine(), settings()
    setattr(config, field, value)
    with pytest.raises(EvidenceError, match="^PREPARATION_CONTEXT_ENV_INVALID$"):
        m.read_context(engine, config)
    assert engine.events == []


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "bad",
        "a@b",
        "a@b.c,",
        "a@b.c\nb@c.d",
        "Team@a.b,team@a.b",
        "a@B.c,a@b.c",
        ",".join(f"a{i}@b.c" for i in range(11)),
    ],
)
def test_invalid_or_effectively_deduplicated_recipient_rejected(raw):
    engine, config = Engine(), settings()
    config.AGENT_EMAIL_RECIPIENTS = raw
    with pytest.raises(EvidenceError):
        m.read_context(engine, config)
    assert engine.events == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_name", "kosa_agent"),
        ("role_name", "postgres"),
        ("read_only", "off"),
        ("isolation", "read committed"),
        ("system_identifier", 12345),
        ("system_identifier", ""),
        ("extra", SECRET),
    ],
)
def test_actual_identity_fail_closed_and_rollback(field, value):
    engine = Engine()
    engine.identity[field] = value
    with pytest.raises(EvidenceError):
        m.read_context(engine, settings())
    assert engine.events[-1] == "ROLLBACK_CLOSE"


def test_missing_pg_control_permission_has_no_privileged_fallback():
    engine = Engine()

    def denied(_):
        raise RuntimeError(SECRET)

    engine.execute = denied
    with pytest.raises(EvidenceError) as caught:
        m.read_context(engine, settings())
    assert engine.events.count("CONNECT") == 1
    assert engine.events[-1] == "ROLLBACK_CLOSE"
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_docker_read_fixed_id_command_and_no_override(monkeypatch):
    calls = []
    expected = projection()

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0, stdout=expected.model_dump_json().encode(), stderr=SECRET
        )

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m.docker_context(CID) == expected
    assert calls == [
        (
            [
                "docker",
                "exec",
                CID,
                "python",
                "-B",
                "/workspace/backend/scripts/read_preparation_context.py",
            ],
            dict(capture_output=True, timeout=30, check=False),
        )
    ]


@pytest.mark.parametrize("cid", ["backend", "-evil", "", "a" * 63, None])
def test_invalid_container_no_subprocess(monkeypatch, cid):
    monkeypatch.setattr(
        m.subprocess, "run", lambda *a, **k: pytest.fail("unexpected subprocess")
    )
    with pytest.raises(EvidenceError, match="^PREPARATION_CONTEXT_CONTAINER_INVALID$"):
        m.docker_context(cid)


@pytest.mark.parametrize(
    "kind", ["exit", "oversize", "json", "schema", "timeout", "os"]
)
def test_subprocess_boundary_sanitizes_failures(monkeypatch, kind):
    payload = projection().model_dump()

    def run(*a, **k):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(SECRET, 30, stderr=SECRET)
        if kind == "os":
            raise OSError(SECRET)
        if kind == "schema":
            payload["password"] = SECRET
        raw = (
            b"x" * 16385
            if kind == "oversize"
            else SECRET.encode()
            if kind == "json"
            else json.dumps(payload).encode()
        )
        return SimpleNamespace(
            returncode=1 if kind == "exit" else 0, stdout=raw, stderr=SECRET
        )

    monkeypatch.setattr(m.subprocess, "run", run)
    with pytest.raises(
        EvidenceError, match="^PREPARATION_CONTEXT_READ_FAILED$"
    ) as caught:
        m.docker_context(CID)
    assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("mode", ["success", "failure", "argument"])
def test_actual_script_process_private_output_or_safe_failure(mode):
    payload = projection().model_dump_json()
    code = f"""
import json
from app.agent import release_context as m
def collect():
    if {mode!r} == 'failure': raise RuntimeError({SECRET!r})
    if {mode!r} == 'argument': raise AssertionError('must not read')
    return m.PreparationContext.model_validate_json({payload!r})
m.collect_current_context = collect
from scripts.read_preparation_context import main
raise SystemExit(main(['--override=' + {SECRET!r}] if {mode!r} == 'argument' else []))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert (
        result.returncode == {"success": 0, "failure": 1, "argument": 2}[mode]
    ), result.stderr
    output = json.loads(result.stdout)
    assert SECRET not in result.stdout + result.stderr
    if mode == "success":
        assert output == json.loads(payload)
    else:
        assert set(output) == {"status", "code"} and output["status"] == "FAIL"


def test_import_does_not_load_runtime_or_do_io():
    code = """
import subprocess, httpx, sys
def fail(*a, **k): raise AssertionError('unexpected IO')
subprocess.run = fail
httpx.Client = fail
import app.agent.release_context
import app.agent.release_capture
import scripts.read_preparation_context
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.common.llm' not in sys.modules
assert 'app.agent.email_delivery' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
