"""Synthetic Docker command boundary; never contacts a daemon or starts services."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.agent import release_runtime as m
from app.agent.release_artifacts import EvidenceError

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "deploy/compose"
REV = "a" * 40
IMAGES = {r: "sha256:" + str(i) * 64 for i, r in enumerate(m.SERVICES, 1)}
IDS = {r: str(i) * 64 for i, r in enumerate(m.SERVICES, 4)}
AT = "2026-09-06T01:00:00.123456789Z"
SECRET = "private-driver-secret-do-not-log"


class Docker:
    """Explicit fake command process, including created/running state changes."""

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.payloads = {}
        self.id_lists = {r: [] for r in m.SERVICES}
        self.labels = {r: REV for r in m.SERVICES}
        self.secret_mount_roles = set()
        self.missing_secret = None
        self.health = ["healthy"]
        self.hook = lambda argv: None

    def image(self, kind, image):
        self.calls.append((kind, image))
        role = next(r for r, value in IMAGES.items() if value == image)
        return {"image_id": image, "label_revision": self.labels[role]}

    def fill(self):
        for r, s in m.SERVICES.items():
            self.id_lists[r] = [IDS[r]]
            self.payloads[IDS[r]] = {
                "container_id": IDS[r],
                "image_id": IMAGES[r],
                "running": False,
                "status": "created",
                "paused": False,
                "restarting": False,
                "started_at": m._ZERO_START,
                "project": m.PROJECT,
                "service": s,
                "oneoff": "False",
                "runner": {
                    "user": "501:20",
                    "workdir": m.WORKDIR,
                    "command": list(m.IDLE_COMMAND),
                    "entrypoint": None,
                    "init": True,
                    "restart": "no",
                }
                if r == "runner"
                else None,
                "mounts": []
                if r == "frontend"
                else [
                    {
                        "destination": "/reports",
                        "type": "bind",
                        "rw": r == "runner",
                        "source": str(self.root),
                    },
                    *(
                        [
                            {
                                "destination": destination,
                                "type": "bind",
                                "rw": False,
                                "source": None,
                            }
                            for destination in sorted(m.OPTIONAL_SECRET_MOUNTS)
                        ]
                        if r in self.secret_mount_roles
                        else []
                    ),
                ],
            }

    def run(self, argv, *, env, timeout):
        self.calls.append((list(argv), dict(env), timeout))
        self.hook(argv)
        if argv[1] == "ps":
            service = argv[-1].split("=")[-1]
            role = next(r for r, s in m.SERVICES.items() if s == service)
            return "\n".join(self.id_lists[role]).encode()
        if argv[1] == "compose":
            if "create" in argv:
                self.fill()
                return b""
            if "start" in argv:
                assert argv[-4:] == ["start", *m.SERVICES.values()]
                for cid in IDS.values():
                    self.payloads[cid].update(
                        running=True, status="running", started_at=AT
                    )
                return b""
            if "exec" in argv:
                service = argv[argv.index("-T") + 1]
                role = next(r for r, s in m.SERVICES.items() if s == service)
                assert argv[-3:-1] == ["test", "-f"]
                if self.missing_secret == (role, argv[-1]):
                    raise RuntimeError("missing secret")
                return b""
            raise AssertionError(argv)
        if argv[1] == "container":
            if argv[-2] == m._HEALTH_FORMAT:
                value = self.health[0]
                if len(self.health) > 1:
                    self.health.pop(0)
                return json.dumps(value).encode()
            return json.dumps(self.payloads[argv[-1]]).encode()
        if argv[1] == "exec":
            return b"synthetic-output\n"
        raise AssertionError(argv)

    def actions(self, verb):
        return [c for c in self.calls if isinstance(c[0], list) and c[0][1] == verb]

    def compose_actions(self, verb):
        return [c for c in self.actions("compose") if verb in c[0]]


@pytest.fixture
def rig(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(mode=0o700)
    env = tmp_path / ".env.team"
    env.write_text("APP_DB_PASSWORD=" + SECRET)
    env.chmod(0o600)
    fake = Docker(reports)
    args = dict(
        compose_directory=COMPOSE,
        env_file=env,
        revision=REV,
        image_ids=IMAGES,
        report_root=reports,
        uid=501,
        gid=20,
        run=fake.run,
        inspect_image=fake.image,
        fetch=lambda path: m.ProbeResponse(200),
    )
    return m.ComposeRuntime(**args), fake, args


def test_create_inspect_start_same_persistent_runner(rig):
    runtime, fake, _ = rig
    created = runtime.create()
    assert created.phase == "created"
    assert not fake.actions("start") and not fake.actions("exec")
    running = runtime.start(created)
    assert running.prepared_containers().runner.container_id == IDS["runner"]
    assert not fake.actions("start")
    assert fake.compose_actions("start")[0][0][-4:] == [
        "start",
        *m.SERVICES.values(),
    ]
    for _ in range(2):
        assert (
            runtime.exec_runner(running, ["python", "scripts/synthetic.py"])
            == b"synthetic-output\n"
        )
    assert len(fake.compose_actions("create")) == 1
    assert len(fake.compose_actions("start")) == 1
    assert len(fake.compose_actions("exec")) == 4
    assert len(fake.actions("exec")) == 2
    for call in fake.actions("exec"):
        assert call[0] == [
            "docker",
            "exec",
            "--user",
            "501:20",
            "--workdir",
            m.WORKDIR,
            IDS["runner"],
            "python",
            "scripts/synthetic.py",
        ]
    argv, env, _ = fake.actions("compose")[0]
    assert argv[argv.index("create") :] == [
        "create",
        "--no-build",
        "--pull",
        "never",
        "--no-recreate",
        *m.SERVICES.values(),
    ]
    assert argv.count("-f") == 3
    assert "--rm" not in argv and "up" not in argv and "run" not in argv
    assert env["SOURCE_REVISION"] == REV
    assert env["CM52_PIN_RUNNER_IMAGE"] == IMAGES["runner"]
    assert (
        env["AGENT_FAULT_EVAL_ARTIFACT_PATH"]
        == env["AGENT_GOLDEN_FLOW_SUMMARY_PATH"]
        == ""
    )
    assert SECRET not in repr(fake.calls)


@pytest.mark.parametrize("role", m.SERVICES)
def test_bad_image_label_before_create(rig, role):
    runtime, fake, _ = rig
    fake.labels[role] = "b" * 40
    with pytest.raises(EvidenceError, match="IMAGE_MISMATCH"):
        runtime.create()
    assert not fake.actions("compose")


def test_team_env_wins_over_host_exports(rig, monkeypatch):
    _, fake, args = rig
    args["env_file"].write_text(
        "APP_DB_PASSWORD=file-secret\nPOSTGRES_DB=kosa_agent\n"
        "AGENT_EMAIL_RECIPIENTS=Team@example.invalid\n"
    )
    for key in (
        "APP_DB_PASSWORD",
        "POSTGRES_DB",
        "AGENT_EMAIL_RECIPIENTS",
        "AGENT_LEVEL3_DEMO_ACK",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "TEAM_IMAGE_TAG",
        "CM52_PIN_BACKEND_IMAGE",
    ):
        monkeypatch.setenv(key, SECRET)
    runtime = m.ComposeRuntime(**args)
    runtime.create()
    env = fake.actions("compose")[0][1]
    assert SECRET not in repr(env)
    for key in (
        "APP_DB_PASSWORD",
        "POSTGRES_DB",
        "AGENT_EMAIL_RECIPIENTS",
        "AGENT_LEVEL3_DEMO_ACK",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "TEAM_IMAGE_TAG",
    ):
        assert key not in env
    assert env["CM52_PIN_BACKEND_IMAGE"] == IMAGES["backend"]


@pytest.mark.parametrize("operation", ["create", "start", "exec"])
@pytest.mark.parametrize("fault", ["bytes", "permissions", "symlink", "hardlink"])
def test_env_drift_stops_before_next_docker_command(rig, operation, fault):
    runtime, fake, args = rig
    snapshot = None
    if operation != "create":
        snapshot = runtime.create()
        if operation == "exec":
            snapshot = runtime.start(snapshot)
    env = args["env_file"]
    if fault == "bytes":
        env.write_text("APP_DB_PASSWORD=changed\n")
    elif fault == "permissions":
        env.chmod(0o644)
    elif fault == "symlink":
        moved = env.with_suffix(".moved")
        env.rename(moved)
        env.symlink_to(moved)
    else:
        os.link(env, env.with_suffix(".linked"))
    count = len(fake.calls)
    with pytest.raises((EvidenceError, OSError)):
        if operation == "create":
            runtime.create()
        elif operation == "start":
            runtime.start(snapshot)
        else:
            runtime.exec_runner(snapshot, ["python", "scripts/synthetic.py"])
    assert len(fake.calls) == count


def test_env_indirection_is_not_resolved_from_host(rig):
    _, fake, args = rig
    args["env_file"].write_text("POSTGRES_DB=${SHADOW_DB}\n")
    with pytest.raises(EvidenceError, match="INTERPOLATION_UNSUPPORTED"):
        m.ComposeRuntime(**args)
    assert fake.calls == []


@pytest.mark.parametrize("role", m.SERVICES)
def test_existing_container_or_oneoff_never_recreated(rig, role):
    runtime, fake, _ = rig
    fake.id_lists[role] = [IDS[role]]
    with pytest.raises(EvidenceError, match="ALREADY_EXISTS"):
        runtime.create()
    assert not fake.actions("compose")


@pytest.mark.parametrize(
    "key,value",
    [
        ("revision", "main"),
        ("revision", True),
        ("image_ids", {"backend": IMAGES["backend"]}),
        ("image_ids", {**IMAGES, "runner": "bistel-backend:latest"}),
        ("uid", True),
        ("uid", -1),
        ("gid", "20"),
        ("report_root", Path("relative")),
        ("compose_directory", Path("relative")),
        ("env_file", Path("missing")),
    ],
)
def test_arguments_fail_before_io(rig, key, value):
    _, fake, args = rig
    with pytest.raises(EvidenceError, match="ARGUMENT_INVALID"):
        m.ComposeRuntime(**{**args, key: value})
    assert fake.calls == []


@pytest.mark.parametrize("role", m.SERVICES)
def test_created_image_mismatch_prevents_start(rig, role):
    runtime, fake, _ = rig
    created = runtime.create()
    fake.payloads[IDS[role]]["image_id"] = "sha256:" + "f" * 64
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert not fake.actions("start")


@pytest.mark.parametrize(
    "case",
    [
        "backend_image",
        "frontend_image",
        "runner_image",
        "command",
        "missing_reports",
        "reports_source",
        "runner_user",
        "init",
        "oneoff",
    ],
)
def test_bad_initial_creation_rejected_not_just_later_drift(rig, case):
    runtime, fake, _ = rig

    def invalid(argv):
        if argv[1] != "container":
            return
        runner = fake.payloads[IDS["runner"]]
        if case.endswith("_image"):
            role = case.removesuffix("_image")
            fake.payloads[IDS[role]]["image_id"] = "sha256:" + "e" * 64
        elif case == "command":
            runner["runner"]["command"] = ["uvicorn", "app.main:app"]
        elif case == "missing_reports":
            runner["mounts"] = []
        elif case == "reports_source":
            runner["mounts"][0]["source"] = "/other/reports"
        elif case == "runner_user":
            runner["runner"]["user"] = "0:0"
        elif case == "init":
            runner["runner"]["init"] = False
        else:
            runner["oneoff"] = "True"

    fake.hook = invalid
    with pytest.raises(EvidenceError, match="CONTAINER_INVALID"):
        runtime.create()
    assert len(fake.actions("compose")) == 1
    assert not fake.actions("start") and not fake.actions("exec")


@pytest.mark.parametrize(
    "key,value",
    [
        ("project", "bistel-team"),
        ("oneoff", "True"),
        ("oneoff", False),
        ("paused", True),
        ("paused", 0),
        ("restarting", True),
        ("running", 1),
        ("status", "exited"),
        ("service", "backend"),
        ("container_id", "9" * 64),
        ("started_at", AT),
        ("runner", None),
    ],
)
def test_created_container_contract(rig, key, value):
    runtime, fake, _ = rig
    created = runtime.create()
    fake.payloads[IDS["runner"]][key] = value
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert not fake.actions("start")


@pytest.mark.parametrize(
    "key,value",
    [
        ("user", "0:0"),
        ("workdir", "/tmp"),
        ("command", ["uvicorn", "app.main:app"]),
        ("entrypoint", ["sh", "-c"]),
        ("init", False),
        ("init", 1),
        ("restart", "always"),
    ],
)
def test_runner_must_be_inert_with_same_user_and_workdir(rig, key, value):
    runtime, fake, _ = rig
    created = runtime.create()
    fake.payloads[IDS["runner"]]["runner"][key] = value
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert not fake.actions("start")


@pytest.mark.parametrize(
    "change",
    [
        "missing_reports",
        "duplicate",
        "wrong_source",
        "reports_readonly",
        "volume",
    ],
)
def test_runner_reports_mount_contract(rig, change):
    runtime, fake, _ = rig
    created = runtime.create()
    mounts = fake.payloads[IDS["runner"]]["mounts"]
    if change == "missing_reports":
        mounts.pop(0)
    elif change == "duplicate":
        mounts.append(dict(mounts[0]))
    elif change == "wrong_source":
        mounts[0]["source"] = "/wrong/reports"
    elif change == "reports_readonly":
        mounts[0]["rw"] = False
    else:
        mounts[0]["type"] = "volume"
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert not fake.actions("start")


@pytest.mark.parametrize("role", ["backend", "runner"])
def test_environment_sourced_secrets_need_no_docker_mount(rig, role):
    runtime, fake, _ = rig
    created = runtime.create()
    assert [mount["destination"] for mount in fake.payloads[IDS[role]]["mounts"]] == [
        "/reports"
    ]
    assert runtime.start(created).phase == "running"


@pytest.mark.parametrize("role", m.SECRET_SERVICES)
@pytest.mark.parametrize("secret", sorted(m.OPTIONAL_SECRET_MOUNTS))
def test_start_requires_materialized_kafka_secret_files(rig, role, secret):
    runtime, fake, _ = rig
    created = runtime.create()
    fake.missing_secret = (role, secret)
    with pytest.raises(EvidenceError, match="COMMAND_FAILED"):
        runtime.start(created)
    assert len(fake.compose_actions("start")) == 1
    assert not fake.actions("start")


def test_compose_start_must_preserve_all_created_container_ids(rig):
    runtime, fake, _ = rig
    created = runtime.create()
    started = False

    def replace_after_start(argv):
        nonlocal started
        if argv[1] == "compose" and "start" in argv:
            started = True
        elif started and argv[1] == "ps":
            role = next(r for r, s in m.SERVICES.items() if s in argv[-1])
            if role == "frontend":
                fake.id_lists[role] = ["f" * 64]

    fake.hook = replace_after_start
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert len(fake.compose_actions("start")) == 1
    assert not fake.compose_actions("exec")


@pytest.mark.parametrize("role", ["backend", "runner"])
def test_optional_readonly_secret_mounts_are_harmless(rig, role):
    runtime, fake, _ = rig
    fake.secret_mount_roles.add(role)
    assert runtime.start(runtime.create()).phase == "running"


@pytest.mark.parametrize("role", ["backend", "runner"])
def test_optional_secret_mount_can_never_be_writable(rig, role):
    runtime, fake, _ = rig
    fake.secret_mount_roles.add(role)
    created = runtime.create()
    secret = next(
        mount
        for mount in fake.payloads[IDS[role]]["mounts"]
        if mount["destination"] in m.OPTIONAL_SECRET_MOUNTS
    )
    secret["rw"] = True
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert not fake.actions("start")


def test_start_waits_for_backend_health_and_gateway_before_stable_observation(rig):
    _, fake, args = rig
    fake.health = ["starting", "healthy", "healthy"]
    statuses = [503, 200, 200, 502, 200, 200]
    calls = []

    def fetch(path):
        calls.append(path)
        return m.ProbeResponse(statuses.pop(0))

    runtime = m.ComposeRuntime(
        **{
            **args,
            "fetch": fetch,
            "sleep": lambda _: None,
            "monotonic": lambda: 0.0,
        }
    )
    assert runtime.start(runtime.create()).phase == "running"
    assert calls == ["/", "/api/health/ready"] * 3
    start_index = next(
        index
        for index, call in enumerate(fake.calls)
        if isinstance(call[0], list) and call[0][1] == "compose" and "start" in call[0]
    )
    first_observe = next(
        index
        for index, call in enumerate(fake.calls)
        if index > start_index
        if isinstance(call[0], list)
        and call[0][1] == "container"
        and call[0][-2] != m._HEALTH_FORMAT
    )
    last_health = max(
        index
        for index, call in enumerate(fake.calls)
        if isinstance(call[0], list)
        and call[0][1] == "container"
        and call[0][-2] == m._HEALTH_FORMAT
    )
    assert last_health < first_observe


def test_start_health_timeout_has_bounded_reason_code(rig):
    _, fake, args = rig
    fake.health = ["starting"]
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    runtime = m.ComposeRuntime(
        **{
            **args,
            "fetch": lambda path: m.ProbeResponse(200),
            "sleep": sleep,
            "monotonic": lambda: now[0],
            "start_timeout": 3,
            "start_poll": 2,
        }
    )
    with pytest.raises(EvidenceError, match="^LEVEL3_RUNTIME_START_TIMEOUT$"):
        runtime.start(runtime.create())
    assert now[0] == 3


@pytest.mark.parametrize("role", ["backend", "runner"])
@pytest.mark.parametrize("change", ["wrong_source", "wrong_access"])
def test_reports_mount_source_and_access_mode_are_pinned(rig, role, change):
    runtime, fake, _ = rig
    created = runtime.create()
    reports = fake.payloads[IDS[role]]["mounts"][0]
    if change == "wrong_source":
        reports["source"] = "/wrong/reports"
    else:
        reports["rw"] = not reports["rw"]
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert not fake.actions("start")


@pytest.mark.parametrize(
    "ids", [[], ["short"], [IDS["runner"], "f" * 64], [IDS["backend"]]]
)
def test_replaced_missing_scaled_or_reused_runner_prevents_start(rig, ids):
    runtime, fake, _ = rig
    created = runtime.create()
    fake.id_lists["runner"] = ids
    with pytest.raises(EvidenceError):
        runtime.start(created)
    assert not fake.actions("start")


def test_start_twice_is_rejected_without_second_start(rig):
    runtime, fake, _ = rig
    created = runtime.create()
    runtime.start(created)
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert len(fake.compose_actions("start")) == 1
    assert not fake.actions("start")


@pytest.mark.parametrize("role", m.SERVICES)
def test_restart_of_any_service_blocks_runner_exec(rig, role):
    runtime, fake, _ = rig
    running = runtime.start(runtime.create())
    fake.payloads[IDS[role]]["started_at"] = "2026-09-06T01:00:01Z"
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.exec_runner(running, ["python", "scripts/synthetic.py"])
    assert not fake.actions("exec")


def test_drift_between_two_inspections_is_rejected(rig):
    runtime, fake, _ = rig
    created = runtime.create()
    n = 0

    def drift(argv):
        nonlocal n
        if argv[1] == "container" and argv[-1] == IDS["runner"]:
            n += 1
            if n == 2:
                fake.payloads[IDS["runner"]]["runner"]["user"] = "0:0"

    fake.hook = drift
    with pytest.raises(EvidenceError, match="DRIFT"):
        runtime.start(created)
    assert n == 2 and not fake.actions("start")


@pytest.mark.parametrize(
    "argv,timeout",
    [
        ([], 60),
        ([""], 60),
        (["x\0y"], 60),
        (["true"], 0),
        (["true"], True),
        (("true",), 60),
    ],
)
def test_exec_arguments_validated_before_readback(rig, argv, timeout):
    runtime, fake, _ = rig
    running = runtime.start(runtime.create())
    fake.calls.clear()
    with pytest.raises(EvidenceError, match="ARGUMENT_INVALID"):
        runtime.exec_runner(running, argv, timeout=timeout)
    assert fake.calls == []


@pytest.mark.parametrize(
    "error", [OSError(SECRET), subprocess.TimeoutExpired([SECRET], 10)]
)
def test_driver_error_redacted(monkeypatch, error):
    def fail(*a, **kw):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(EvidenceError) as exc:
        m.command(["docker", "start", IDS["runner"]], env={}, timeout=10)
    assert str(exc.value) == "LEVEL3_RUNTIME_COMMAND_FAILED"
    assert exc.value.__suppress_context__
    assert SECRET not in str(exc.value)


def test_default_command_uses_argv_no_shell_and_suppresses_stderr(monkeypatch):
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, b"", SECRET.encode())

    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(EvidenceError, match="COMMAND_FAILED"):
        m.command(["docker", "ps"], env={"PIN": "value"}, timeout=7)
    assert calls == [
        (
            ["docker", "ps"],
            {
                "env": {"PIN": "value"},
                "capture_output": True,
                "timeout": 7,
                "check": False,
            },
        )
    ]


def test_checked_in_override_is_opt_in_and_preserves_inheritance():
    override = yaml.safe_load((COMPOSE / "docker-compose.e2e-level3.yml").read_text())
    assert set(override["services"]) == set(m.SERVICES.values())
    for role, service in m.SERVICES.items():
        config = override["services"][service]
        assert f"CM52_PIN_{role.upper()}_IMAGE" in config["image"]
        assert not set(config) & {"build", "volumes", "secrets", "networks", "extends"}
    runner = override["services"]["e2e-runner"]
    assert runner["command"] == m.IDLE_COMMAND and runner["entrypoint"] == []
    assert runner["init"] is True and runner["working_dir"] == m.WORKDIR
    for s in ("backend", "e2e-runner"):
        assert override["services"][s]["environment"]["AGENT_AUTONOMY_LEVEL"] == "3"
        assert override["services"][s]["environment"]["AGENT_LEVEL3_ENABLED"] == "true"
    # Do not silently activate unfinished prepared/grant flow in legacy Stage2.
    assert (
        "docker-compose.e2e-level3.yml" not in (COMPOSE / "cm52_common.sh").read_text()
    )
    assert "SMTP_SEND_GRANT_REQUIRED" in (COMPOSE / "cm52_stage2.sh").read_text()


def test_idle_entrypoint_import_has_no_effect_and_waits_until_signal(monkeypatch):
    path = REPO / "backend/scripts/e2e_runner_idle.py"
    spec = importlib.util.spec_from_file_location("idle_test", path)
    module = importlib.util.module_from_spec(spec)
    calls = []
    monkeypatch.setattr(signal, "signal", lambda *args: calls.append(args))

    def pause():
        calls.append("pause")
        raise SystemExit(0)

    monkeypatch.setattr(signal, "pause", pause)
    spec.loader.exec_module(module)
    assert calls == []
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 0
    assert [c[0] for c in calls[:-1]] == [signal.SIGTERM, signal.SIGINT]
    assert calls[-1] == "pause"


def test_import_has_no_docker_network_or_file_write():
    script = """
import sys
def audit(event, args):
    if event.startswith(('socket.', 'subprocess.')):
        raise RuntimeError('unexpected I/O: ' + event)
    if event == 'open' and args[2] & 0x03:
        raise RuntimeError('unexpected file write')
sys.addaudithook(audit)
import app.agent.release_runtime
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=REPO / "backend",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_level3_rendered_compose_model_preserves_runner_contract(tmp_path, monkeypatch):
    # Compose config only: real merge/interpolation, no daemon/lifecycle action.
    if shutil.which("docker") is None:
        if os.environ.get("CM52_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker CLI required for rendered-model gate")
        pytest.skip("Docker CLI unavailable")
    from tests.unit.test_team_compose import _valid_env, _write_env

    values = _valid_env(tmp_path)
    env_file = tmp_path / ".env.team"
    _write_env(env_file, values)
    env_file.chmod(0o600)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    runtime = m.ComposeRuntime(
        compose_directory=COMPOSE,
        env_file=env_file,
        revision=REV,
        image_ids=IMAGES,
        report_root=Path(values["AGENT_EVAL_REPORTS_DIR"]),
        uid=501,
        gid=20,
    )
    result = subprocess.run(
        runtime.compose + ["config", "--format", "json"],
        env=runtime.env,
        capture_output=True,
        check=False,
    )
    assert (
        result.returncode == 0
    ), "Level 3 Compose render failed (private stderr suppressed)"
    services = json.loads(result.stdout)["services"]
    for role, service in m.SERVICES.items():
        assert services[service]["image"] == IMAGES[role]
    runner = services["e2e-runner"]
    assert runner["command"] == m.IDLE_COMMAND
    assert runner["entrypoint"] == []
    assert runner["init"] is True
    assert runner["user"] == "501:20" and runner["working_dir"] == m.WORKDIR
    assert runner["restart"] == "no" and runner["healthcheck"]["disable"] is True
    assert not runner.get("ports")
    assert runner["networks"] == services["backend"]["networks"]
    assert {s["source"] for s in runner["secrets"]} == {
        "kafka_client_user",
        "kafka_client_password",
    }
    for name in ("backend", "e2e-runner"):
        env = services[name]["environment"]
        assert (
            env["AGENT_AUTONOMY_LEVEL"] == "3" and env["AGENT_LEVEL3_ENABLED"] == "true"
        )
        assert env["POSTGRES_DB"] == "kosa_agent_e2e"
        assert (
            env["AGENT_FAULT_EVAL_ARTIFACT_PATH"]
            == env["AGENT_GOLDEN_FLOW_SUMMARY_PATH"]
            == ""
        )
        assert env["AGENT_LEVEL3_DEMO_ACK"] == ""
        assert env["LLM_API_KEY"] == values["LLM_API_KEY"]
        reports = next(
            v for v in services[name]["volumes"] if v["target"] == "/reports"
        )
        assert reports["source"] == values["AGENT_EVAL_REPORTS_DIR"]
        assert reports.get("read_only", False) is (name == "backend")
    assert runner["environment"]["GITHUB_SHA"] == REV
    assert runner["environment"]["EVALUATION_DB_USER"] == "kosa_evaluation"
    assert "/kosa_agent_e2e" in runner["environment"]["TEXT2SQL_DATABASE_URL"]


def test_created_phase_does_not_masquerade_as_prepared_running_containers(rig):
    runtime, _, _ = rig
    with pytest.raises(EvidenceError, match="NOT_RUNNING"):
        runtime.create().prepared_containers()


@pytest.mark.parametrize(
    "case", ["repo", "symlink_repo", "public_mode", "other_compose"]
)
def test_report_root_and_checked_in_compose_before_docker_io(rig, case, tmp_path):
    _, fake, args = rig
    args = dict(args)
    if case == "repo":
        args["report_root"] = REPO
    elif case == "symlink_repo":
        link = tmp_path / "repo-alias"
        link.symlink_to(REPO, target_is_directory=True)
        args["report_root"] = link
    elif case == "public_mode":
        args["report_root"].chmod(0o755)
    else:
        args["compose_directory"] = tmp_path
    with pytest.raises(EvidenceError, match="ARGUMENT_INVALID"):
        m.ComposeRuntime(**args)
    assert fake.calls == []


@pytest.mark.parametrize("verb", ["compose", "start", "exec"])
def test_failure_does_not_retry_or_implicitly_restore_or_issue_receipt(rig, verb):
    runtime, fake, args = rig
    snapshot = None
    if verb != "compose":
        snapshot = runtime.create()
    if verb == "exec":
        snapshot = runtime.start(snapshot)

    def fail(argv):
        if (verb == "start" and argv[1] == "compose" and "start" in argv) or (
            verb != "start" and argv[1] == verb
        ):
            raise RuntimeError(SECRET)

    fake.hook = fail
    before = set(args["report_root"].iterdir())
    with pytest.raises(EvidenceError) as exc:
        if verb == "compose":
            runtime.create()
        elif verb == "start":
            runtime.start(snapshot)
        else:
            runtime.exec_runner(snapshot, ["python", "scripts/synthetic.py"])
    assert SECRET not in str(exc.value)
    actions = fake.compose_actions("start") if verb == "start" else fake.actions(verb)
    assert len(actions) == 1
    assert set(args["report_root"].iterdir()) == before
    assert all(
        c[0][1] in {"ps", "container", "compose", "start", "exec"}
        for c in fake.calls
        if isinstance(c[0], list)
    )
