from __future__ import annotations

import ast
import importlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import URL

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
REPOSITORY_ROOT = SCRIPTS_ROOT.parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rebuild_runner as runner  # noqa: E402
import schema_lock  # noqa: E402
from db_target import host_fingerprint  # noqa: E402


def _json_line(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return payload


def _rehearsal_env(
    *,
    database: str = "fdc_rehearsal_runtime",
    host: str = "127.0.0.1",
    port: str = "55432",
) -> dict[str, str]:
    return {
        "POSTGRES_REHEARSAL_HOST": host,
        "POSTGRES_REHEARSAL_PORT": port,
        "POSTGRES_REHEARSAL_DATABASE": database,
        "POSTGRES_REHEARSAL_USER": "rehearsal_user",
        "POSTGRES_REHEARSAL_PASSWORD": "do-not-print-this-password",
    }


def _public_env(*, host: str = "db.example.internal") -> dict[str, str]:
    port = 5432
    return {
        "POSTGRES_BOOTSTRAP_HOST": host,
        "POSTGRES_BOOTSTRAP_PORT": str(port),
        "POSTGRES_BOOTSTRAP_USER": "bootstrap_user",
        "POSTGRES_BOOTSTRAP_PASSWORD": "do-not-print-public-password",
        "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(host, port),
    }


@pytest.fixture
def artifact_paths(tmp_path: Path) -> runner.ArtifactPaths:
    mapping = {
        "epoch": runner.DATASET_EPOCH_PATH,
        "source_manifest": runner.SOURCE_MANIFEST_PATH,
        "intake": runner.INTAKE_PATH,
    }
    copied: dict[str, Path] = {}
    for key, source in mapping.items():
        destination = tmp_path / source.name
        shutil.copyfile(source, destination)
        copied[key] = destination
    return runner.ArtifactPaths(**copied)


def _rewrite_json(path: Path, update: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    update(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _args(*values: str) -> Any:
    return runner._parser().parse_args(list(values))


def _plan(
    policy: runner.CommitPolicy,
    *,
    lock: Any | None = None,
) -> runner.ExecutionPlan:
    target = runner.RehearsalTarget(
        host="127.0.0.1",
        port=55432,
        username="user",
        password="password",
        database="fdc_rehearsal_runtime",
        profile="runtime",
    )
    return runner.ExecutionPlan(
        mode={
            runner.CommitPolicy.READ_ONLY: runner.RunMode.PREFLIGHT,
            runner.CommitPolicy.ROLLBACK_ALWAYS: runner.RunMode.REHEARSE,
            runner.CommitPolicy.COMMIT: runner.RunMode.APPLY,
        }[policy],
        profile="runtime",
        logical_targets=("kosa_agent", "kosa_agent_e2e"),
        target=target,
        commit_policy=policy,
        acquire_lock=lock or (lambda *_: None),
    )


class FakeTransaction:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.is_active = True

    def commit(self) -> None:
        self.events.append("commit")
        self.is_active = False

    def rollback(self) -> None:
        self.events.append("rollback")
        self.is_active = False


class FakeConnection:
    def __init__(self, events: list[Any], *, begin_error: bool = False) -> None:
        self.events = events
        self.begin_error = begin_error

    def __enter__(self) -> FakeConnection:
        self.events.append("connection_enter")
        return self

    def __exit__(self, *args: Any) -> None:
        self.events.append("connection_exit")

    def begin(self) -> FakeTransaction:
        self.events.append("begin")
        if self.begin_error:
            raise RuntimeError("begin failed")
        return FakeTransaction(self.events)


class FakeEngine:
    def __init__(self, events: list[Any], *, begin_error: bool = False) -> None:
        self.events = events
        self.connection = FakeConnection(events, begin_error=begin_error)

    def connect(self) -> FakeConnection:
        self.events.append("connect")
        return self.connection

    def dispose(self) -> None:
        self.events.append("dispose")


def _engine_factory(events: list[Any], *, begin_error: bool = False) -> Any:
    def factory(target: Any) -> FakeEngine:
        assert target.database == "fdc_rehearsal_runtime"
        events.append("engine_factory")
        return FakeEngine(events, begin_error=begin_error)

    return factory


def _prepare_spy(events: list[Any]) -> Any:
    def prepare(
        connection: Any,
        target: Any,
        *,
        readonly: bool,
        isolation_level: str = "READ COMMITTED",
        acquire_lock: Any,
    ) -> None:
        assert isolation_level == "READ COMMITTED"
        events.append(("prepare", readonly))
        acquire_lock(connection, target.database)

    return prepare


def test_repository_artifacts_match_final_epoch() -> None:
    runner.validate_artifacts()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra="unexpected"),
        lambda value: value.update(format_version="2"),
        lambda value: value.update(artifact_type="other"),
    ],
)
def test_epoch_format_errors_are_artifact_invalid(
    artifact_paths: runner.ArtifactPaths, mutation: Any
) -> None:
    _rewrite_json(artifact_paths.epoch, mutation)
    with pytest.raises(runner.RunnerError) as caught:
        runner.validate_artifacts(artifact_paths)
    assert (caught.value.reason_code, caught.value.exit_code) == (
        "ARTIFACT_INVALID",
        2,
    )


def test_well_formed_epoch_value_mismatch_is_exit_one(
    artifact_paths: runner.ArtifactPaths,
) -> None:
    _rewrite_json(
        artifact_paths.epoch,
        lambda value: value.update(dataset_epoch="different_epoch"),
    )
    with pytest.raises(runner.RunnerError) as caught:
        runner.validate_artifacts(artifact_paths)
    assert (caught.value.reason_code, caught.value.exit_code) == (
        "EPOCH_MISMATCH",
        1,
    )


@pytest.mark.parametrize("kind", ["missing", "broken", "non_object", "symlink"])
def test_unreadable_or_unsafe_artifact_is_exit_two(
    artifact_paths: runner.ArtifactPaths, kind: str, tmp_path: Path
) -> None:
    target = artifact_paths.intake
    if kind == "missing":
        target.unlink()
    elif kind == "broken":
        target.write_text("{", encoding="utf-8")
    elif kind == "non_object":
        target.write_text("[]\n", encoding="utf-8")
    else:
        target.unlink()
        target.symlink_to(tmp_path / "does-not-exist.json")
    with pytest.raises(runner.RunnerError) as caught:
        runner.validate_artifacts(artifact_paths)
    assert (caught.value.reason_code, caught.value.exit_code) == (
        "ARTIFACT_INVALID",
        2,
    )


def test_intake_byte_hash_mismatch_is_exit_one(
    artifact_paths: runner.ArtifactPaths,
) -> None:
    with artifact_paths.intake.open("a", encoding="utf-8") as stream:
        stream.write(" \n")
    with pytest.raises(runner.RunnerError) as caught:
        runner.validate_artifacts(artifact_paths)
    assert (caught.value.reason_code, caught.value.exit_code) == (
        "EPOCH_MISMATCH",
        1,
    )


def test_sensitive_value_is_rejected_without_echo(
    artifact_paths: runner.ArtifactPaths,
) -> None:
    secret = "never-echo-this"
    _rewrite_json(
        artifact_paths.intake,
        lambda value: value.update(reference_basis={"password": secret}),
    )
    with pytest.raises(runner.RunnerError) as caught:
        runner.validate_artifacts(artifact_paths)
    assert caught.value.reason_code == "ARTIFACT_INVALID"
    assert secret not in str(caught.value)


def test_rehearsal_target_is_secret_safe_and_profile_bound() -> None:
    target = runner.load_rehearsal_target(profile="runtime", environ=_rehearsal_env())
    assert target.database == "fdc_rehearsal_runtime"
    assert target.profile == "runtime"
    assert "password" not in repr(target)
    assert "rehearsal_user" not in repr(target)
    assert (
        target.create_url()
        .render_as_string(hide_password=True)
        .endswith("@127.0.0.1:55432/fdc_rehearsal_runtime")
    )


@pytest.mark.parametrize(
    ("profile", "environment", "reason"),
    [
        ("runtime", _rehearsal_env(host="remote.example"), "TARGET_ENV_INVALID"),
        ("runtime", _rehearsal_env(host="0.0.0.0"), "TARGET_ENV_INVALID"),
        (
            "runtime",
            _rehearsal_env(database="kosa_agent"),
            "TARGET_ENV_INVALID",
        ),
        (
            "evaluation",
            _rehearsal_env(database="fdc_rehearsal_runtime"),
            "PROFILE_MISMATCH",
        ),
        ("runtime", _rehearsal_env(port="0"), "TARGET_ENV_INVALID"),
    ],
)
def test_rehearsal_target_rejects_cross_contamination(
    profile: str, environment: Mapping[str, str], reason: str
) -> None:
    with pytest.raises(runner.RunnerError, match=reason):
        runner.load_rehearsal_target(profile=profile, environ=environment)


def test_rehearsal_url_component_drift_is_rejected() -> None:
    target = runner.load_rehearsal_target(profile="runtime", environ=_rehearsal_env())
    bad = URL.create(
        runner.BOOTSTRAP_DRIVER,
        username=target.username,
        password=target.password,
        host=target.host,
        port=target.port,
        database="other",
    )
    with pytest.raises(runner.RunnerError, match="TARGET_ENV_INVALID"):
        runner._validate_rehearsal_url(bad, target)


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ((), "MODE_CONFLICT"),
        (("--preflight", "--rehearse"), "MODE_CONFLICT"),
        (("--preflight", "--target", "unknown"), "TARGET_NOT_ALLOWED"),
        (("--register-manifests",), "PROFILE_MISMATCH"),
        (
            ("--register-manifests", "--profile", "unknown"),
            "PROFILE_MISMATCH",
        ),
    ],
)
def test_mode_target_and_profile_reason_codes(
    arguments: tuple[str, ...], reason: str
) -> None:
    args = _args(*arguments)
    with pytest.raises(runner.RunnerError, match=reason):
        runner.build_execution_plan(args, environ={})


def test_artifact_mode_is_db_free_and_maps_logical_targets() -> None:
    class ExplodingEnvironment(Mapping[str, str]):
        def __getitem__(self, key: str) -> str:
            raise AssertionError(key)

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("environment must not be iterated")

        def __len__(self) -> int:
            raise AssertionError("environment must not be sized")

        def get(self, key: str, default: Any = None) -> Any:
            raise AssertionError(key)

    plan = runner.build_execution_plan(
        _args("--register-manifests", "--profile", "runtime"),
        environ=ExplodingEnvironment(),
    )
    assert plan.target is None
    assert plan.logical_targets == ("kosa_agent", "kosa_agent_e2e")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--register-manifests", "--profile", "runtime", "--target", "rehearsal"),
        (
            "--register-manifests",
            "--profile",
            "runtime",
            "--confirm-target",
            "x",
        ),
        (
            "--register-manifests",
            "--profile",
            "runtime",
            "--change-ref",
            "CM-1",
        ),
    ],
)
def test_artifact_mode_rejects_db_options(arguments: tuple[str, ...]) -> None:
    with pytest.raises(runner.RunnerError, match="ARG_INVALID"):
        runner.build_execution_plan(_args(*arguments), environ={})


def test_rehearsal_plan_and_confirmation_matrix() -> None:
    env = _rehearsal_env()
    preflight = runner.build_execution_plan(
        _args("--target", "rehearsal", "--profile", "runtime", "--preflight"),
        environ=env,
    )
    assert preflight.commit_policy is runner.CommitPolicy.READ_ONLY

    with pytest.raises(runner.RunnerError) as caught:
        runner.build_execution_plan(
            _args("--target", "rehearsal", "--profile", "runtime", "--rehearse"),
            environ=env,
        )
    assert (caught.value.reason_code, caught.value.exit_code) == (
        "CONFIRM_REQUIRED",
        3,
    )

    rehearse = runner.build_execution_plan(
        _args(
            "--target",
            "rehearsal",
            "--profile",
            "runtime",
            "--rehearse",
            "--confirm-target",
            "fdc_rehearsal_runtime",
        ),
        environ=env,
    )
    assert rehearse.commit_policy is runner.CommitPolicy.ROLLBACK_ALWAYS


def test_public_plan_uses_existing_guard_and_change_ref() -> None:
    plan = runner.build_execution_plan(
        _args(
            "--target",
            "kosa_agent",
            "--apply",
            "--confirm-target",
            "kosa_agent",
            "--change-ref",
            "CM-21",
        ),
        environ=_public_env(),
    )
    assert plan.commit_policy is runner.CommitPolicy.COMMIT
    assert plan.profile == "runtime"
    assert plan.change_ref == "CM-21"
    assert plan.logical_targets == ("kosa_agent", "kosa_agent_e2e")


def test_public_target_still_rejects_local_host() -> None:
    with pytest.raises(runner.RunnerError, match="TARGET_ENV_INVALID"):
        runner.build_execution_plan(
            _args("--target", "kosa_agent", "--preflight"),
            environ=_public_env(host="127.0.0.1"),
        )


def test_public_profile_flag_is_rejected_before_env_read() -> None:
    with pytest.raises(runner.RunnerError, match="PROFILE_MISMATCH"):
        runner.build_execution_plan(
            _args(
                "--target",
                "kosa_agent",
                "--profile",
                "runtime",
                "--preflight",
            ),
            environ={},
        )


def test_lock_keys_preserve_public_values_and_separate_rehearsal() -> None:
    assert runner.advisory_lock_key("kosa_agent") == schema_lock.advisory_lock_key(
        "kosa_agent"
    )
    public_ids = set(schema_lock.DATABASE_LOCK_ID.values())
    rehearsal_ids = {
        runner.advisory_lock_key(database)[1]
        for database in runner.REHEARSAL_DATABASE_PROFILE
    }
    assert public_ids.isdisjoint(rehearsal_ids)


class LockResult:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired

    def mappings(self) -> LockResult:
        return self

    def one(self) -> dict[str, bool]:
        return {"acquired": self.acquired}


def test_advisory_lock_failure_is_state_mismatch() -> None:
    class Connection:
        def exec_driver_sql(self, statement: str, parameters: Any) -> LockResult:
            assert "pg_try_advisory_xact_lock" in statement
            assert parameters == runner.advisory_lock_key("fdc_rehearsal_runtime")
            return LockResult(False)

    with pytest.raises(runner.RunnerError) as caught:
        runner.acquire_advisory_lock(Connection(), "fdc_rehearsal_runtime")
    assert (caught.value.reason_code, caught.value.exit_code) == ("LOCK_BUSY", 1)


@pytest.mark.parametrize(
    ("policy", "terminal"),
    [
        (runner.CommitPolicy.READ_ONLY, "rollback"),
        (runner.CommitPolicy.ROLLBACK_ALWAYS, "rollback"),
        (runner.CommitPolicy.COMMIT, "commit"),
    ],
)
def test_transaction_policy_and_order(
    monkeypatch: pytest.MonkeyPatch,
    policy: runner.CommitPolicy,
    terminal: str,
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy(events)
    )
    plan = _plan(policy, lock=lambda *_: events.append("lock"))

    result = runner.execute_transactional(
        plan,
        _engine_factory(events),
        lambda *_: events.append("handler"),
        lambda *_: events.append("postcheck"),
    )

    assert result == 0
    assert ("prepare", policy is runner.CommitPolicy.READ_ONLY) in events
    assert events.index("lock") < events.index("handler") < events.index("postcheck")
    assert terminal in events
    assert ("commit" in events) is (terminal == "commit")
    assert events[-2:] == ["connection_exit", "dispose"]


def test_non_success_handler_return_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy(events)
    )
    with pytest.raises(runner.ModeContractError):
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events),
            lambda *_: 2,  # type: ignore[return-value]
            lambda *_: None,
        )
    assert events.count("rollback") == 1
    assert "commit" not in events


def test_non_success_postcheck_return_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy(events)
    )
    with pytest.raises(runner.ModeContractError):
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events),
            lambda *_: None,
            lambda *_: {"status": "FAILED"},  # type: ignore[return-value]
        )
    assert events.count("rollback") == 1
    assert "commit" not in events


def test_handler_and_postcheck_failures_roll_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner.mutation_runtime,
        "prepare_transaction",
        lambda *args, **kwargs: None,
    )

    for failing_step in ("handler", "postcheck"):
        events: list[Any] = []

        def handler(*_: Any, step: str = failing_step) -> None:
            if step == "handler":
                raise RuntimeError("handler failed")

        def postcheck(*_: Any, step: str = failing_step) -> None:
            if step == "postcheck":
                raise RuntimeError("postcheck failed")

        with pytest.raises(RuntimeError, match="failed"):
            runner.execute_transactional(
                _plan(runner.CommitPolicy.COMMIT),
                _engine_factory(events),
                handler,
                postcheck,
            )
        assert events.count("rollback") == 1
        assert "commit" not in events


def test_begin_failure_closes_connection_without_rollback() -> None:
    events: list[Any] = []
    with pytest.raises(RuntimeError, match="begin failed"):
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events, begin_error=True),
            lambda *_: None,
            lambda *_: None,
        )
    assert "rollback" not in events
    assert events[-2:] == ["connection_exit", "dispose"]


def test_unwired_mode_never_constructs_engine(
    artifact_paths: runner.ArtifactPaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def forbidden_factory(target: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError(target)

    result = runner.run(
        ["--target", "rehearsal", "--profile", "runtime", "--preflight"],
        environ=_rehearsal_env(),
        artifact_paths=artifact_paths,
        engine_factory=forbidden_factory,
        mode_handlers={},
        postchecks={},
    )
    captured = capsys.readouterr()
    assert result == 2
    assert calls == 0
    assert captured.out == ""
    assert _json_line(captured.err) == {
        "next_task": "V5-CM-2.2",
        "reason_code": "MODE_NOT_WIRED",
        "status": "BLOCKED",
    }


def test_unwired_cli_never_calls_real_connection_factories(
    artifact_paths: runner.ArtifactPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    psycopg = importlib.import_module("psycopg")
    monkeypatch.setattr(
        runner.sqlalchemy,
        "create_engine",
        lambda *args, **kwargs: calls.append("sqlalchemy.create_engine"),
    )
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *args, **kwargs: calls.append("psycopg.connect"),
    )

    result = runner.run(
        ["--target", "rehearsal", "--profile", "runtime", "--preflight"],
        environ=_rehearsal_env(),
        artifact_paths=artifact_paths,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert calls == []
    assert _json_line(captured.err)["reason_code"] == "MODE_NOT_WIRED"


def test_malformed_artifact_precedes_unwired_mode(
    artifact_paths: runner.ArtifactPaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact_paths.epoch.write_text("{", encoding="utf-8")
    result = runner.run(
        ["--target", "rehearsal", "--profile", "runtime", "--preflight"],
        environ=_rehearsal_env(),
        artifact_paths=artifact_paths,
        engine_factory=lambda _: pytest.fail("must not create engine"),
    )
    captured = capsys.readouterr()
    assert result == 2
    assert _json_line(captured.err)["reason_code"] == "ARTIFACT_INVALID"


def test_artifact_preview_requires_confirmation_and_no_db_env(
    artifact_paths: runner.ArtifactPaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = runner.run(
        ["--register-manifests", "--profile", "evaluation"],
        environ={},
        artifact_paths=artifact_paths,
    )
    captured = capsys.readouterr()
    assert result == 3
    assert captured.out == ""
    assert _json_line(captured.err) == {
        "logical_targets": ["kosa_text2sql"],
        "profile": "evaluation",
        "reason_code": "CONFIRM_REQUIRED",
        "status": "PREVIEW",
    }


def test_confirmed_artifact_mode_is_unwired_in_this_task(
    artifact_paths: runner.ArtifactPaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = runner.run(
        ["--register-manifests", "--profile", "runtime", "--confirm"],
        environ={},
        artifact_paths=artifact_paths,
    )
    captured = capsys.readouterr()
    assert result == 2
    assert _json_line(captured.err)["next_task"] == "V5-CM-2.4"


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (("--preflight", "--rehearse"), "MODE_CONFLICT"),
        (("--not-a-real-option", "sentinel-secret"), "ARG_INVALID"),
        (("--target",), "ARG_INVALID"),
        (("--preflight", "--target", "unknown"), "TARGET_NOT_ALLOWED"),
        (("--register-manifests", "--profile", "unknown"), "PROFILE_MISMATCH"),
    ],
)
def test_cli_failures_are_one_sanitized_json_line(
    arguments: tuple[str, ...], reason: str
) -> None:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS_ROOT / "rebuild_runner.py"), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    payload = _json_line(completed.stderr)
    assert payload["reason_code"] == reason
    assert "sentinel-secret" not in completed.stderr
    assert "usage:" not in completed.stderr
    assert "Traceback" not in completed.stderr


def test_runner_ast_forbids_shell_execution() -> None:
    source_path = SCRIPTS_ROOT / "rebuild_runner.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_imports = {"subprocess", "pty", "commands"}
    forbidden_calls = {"os.system", "os.popen"}
    imports: set[str] = set()
    calls: set[str] = set()
    os_aliases = {"os"}
    forbidden_name_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                imports.add(module)
                if module == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
            if node.module == "os":
                forbidden_name_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in {"system", "popen"}
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in os_aliases
            ):
                calls.add(f"{node.func.value.id}.{node.func.attr}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in forbidden_name_aliases:
                calls.add(node.func.id)
    assert imports.isdisjoint(forbidden_imports)
    assert not any(call.rsplit(".", 1)[-1] in {"system", "popen"} for call in calls)
    assert calls.isdisjoint(forbidden_calls)


# ---------------------------------------------------------------------------
# V5-CM-2.5 — --recover-artifact 옵션 행렬과 post-commit hook 계약
# ---------------------------------------------------------------------------


def _recover_argv(mode_flag: str) -> list[str]:
    argv = [
        "--target",
        "rehearsal",
        "--profile",
        "runtime",
        mode_flag,
        "--recover-artifact",
    ]
    if mode_flag != "--register-manifests":
        argv += ["--confirm-target", "fdc_rehearsal_runtime"]
    return argv


def test_recover_artifact_is_allowed_only_with_apply() -> None:
    args = runner._parser().parse_args(_recover_argv("--apply"))
    assert runner._validate_recover_artifact(args, runner.RunMode.APPLY) is True

    plan = runner.build_execution_plan(args, environ=_rehearsal_env())
    assert plan.recover_artifact is True
    assert plan.commit_policy is runner.CommitPolicy.COMMIT


def test_execution_plan_defaults_recover_artifact_to_false() -> None:
    args = runner._parser().parse_args(
        [
            "--target",
            "rehearsal",
            "--profile",
            "runtime",
            "--rehearse",
            "--confirm-target",
            "fdc_rehearsal_runtime",
        ]
    )
    plan = runner.build_execution_plan(args, environ=_rehearsal_env())
    assert plan.recover_artifact is False


@pytest.mark.parametrize(
    "mode_flag", ["--preflight", "--rehearse", "--register-manifests"]
)
def test_recover_artifact_with_other_modes_never_builds_engine(
    mode_flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """잘못된 조합은 연결 전에 `ARG_INVALID`다(계획 §7.1-5)."""

    calls: list[str] = []

    def factory(_target: Any) -> Any:  # pragma: no cover - 호출되면 실패다
        calls.append("engine")
        raise AssertionError("engine must not be built")

    exit_code = runner.run(
        _recover_argv(mode_flag),
        environ=_rehearsal_env(),
        engine_factory=factory,
        mode_handlers={},
        postchecks={},
    )
    assert exit_code == runner.EXIT_USAGE
    assert _json_line(capsys.readouterr().err)["reason_code"] == "ARG_INVALID"
    assert calls == []


def _hook_events() -> tuple[list[Any], Any, Any, Any]:
    events: list[Any] = []

    def handler(_c: Any, _p: Any) -> None:
        events.append("handler")

    def postcheck(_c: Any, _p: Any) -> None:
        events.append("postcheck")

    def hook(_c: Any, _p: Any) -> None:
        events.append("post_commit")

    return events, handler, postcheck, hook


def test_post_commit_hook_runs_once_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, handler, postcheck, hook = _hook_events()
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy([])
    )
    assert (
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events),
            handler,
            postcheck,
            hook,
        )
        == runner.EXIT_OK
    )
    assert events.index("commit") < events.index("post_commit")
    assert events.count("post_commit") == 1


@pytest.mark.parametrize(
    "policy", [runner.CommitPolicy.ROLLBACK_ALWAYS, runner.CommitPolicy.READ_ONLY]
)
def test_rollback_modes_never_call_post_commit(
    policy: runner.CommitPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    events, handler, postcheck, hook = _hook_events()
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy([])
    )
    runner.execute_transactional(
        _plan(policy), _engine_factory(events), handler, postcheck, hook
    )
    assert "rollback" in events
    assert "post_commit" not in events


@pytest.mark.parametrize("failing", ["handler", "postcheck"])
def test_failed_stage_never_calls_post_commit(
    failing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    events, handler, postcheck, hook = _hook_events()
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy([])
    )

    def boom(_c: Any, _p: Any) -> None:
        events.append(failing)
        raise runner.RunnerError("MODE_CONTRACT_ERROR", runner.EXIT_MISMATCH)

    with pytest.raises(runner.RunnerError):
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events),
            boom if failing == "handler" else handler,
            boom if failing == "postcheck" else postcheck,
            hook,
        )
    assert "rollback" in events
    assert "commit" not in events
    assert "post_commit" not in events


def test_post_commit_non_none_return_is_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, handler, postcheck, _hook = _hook_events()
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy([])
    )
    with pytest.raises(runner.ModeContractError):
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events),
            handler,
            postcheck,
            lambda _c, _p: "unexpected",
        )
    # commit은 이미 끝났다. 되돌리지 않는다.
    assert "commit" in events
    assert "rollback" not in events


def test_post_commit_failure_does_not_roll_back_committed_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker 저장 실패를 rollback으로 오판하지 않는다(계획 §7.2 · 계획리뷰 §5-10)."""

    events, handler, postcheck, _hook = _hook_events()
    monkeypatch.setattr(
        runner.mutation_runtime, "prepare_transaction", _prepare_spy([])
    )

    def failing_hook(_c: Any, _p: Any) -> None:
        events.append("post_commit")
        raise runner.RunnerError("ARTIFACT_WRITE_FAILED", runner.EXIT_USAGE)

    with pytest.raises(runner.RunnerError) as caught:
        runner.execute_transactional(
            _plan(runner.CommitPolicy.COMMIT),
            _engine_factory(events),
            handler,
            postcheck,
            failing_hook,
        )
    assert caught.value.reason_code == "ARTIFACT_WRITE_FAILED"
    assert "commit" in events
    assert "rollback" not in events
    assert "dispose" in events


def test_production_post_commit_registry_is_empty() -> None:
    assert runner.POST_COMMIT_HOOKS == {}
    assert runner.MODE_HANDLERS == {}
    assert runner.POSTCHECKS == {}
    assert runner.MANIFEST_HANDLERS == {}
