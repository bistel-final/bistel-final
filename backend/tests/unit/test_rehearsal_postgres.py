from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_postgres as postgres  # noqa: E402


class DockerFake:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.alive = False
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] > 0
        operation = argv[1:]
        if operation[0] == "run":
            self.alive = True
            return subprocess.CompletedProcess(argv, 0, "container-id\n", "")
        if operation[0] == "inspect":
            return subprocess.CompletedProcess(argv, 0, "volume-id\n", "")
        if operation[0] == "port":
            return subprocess.CompletedProcess(argv, 0, "127.0.0.1:55432\n", "")
        if operation[0] == "exec":
            return subprocess.CompletedProcess(argv, 0 if self.ready else 1, "", "")
        if operation[:2] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(
                argv, 0, "container-id\n" if self.alive else "", ""
            )
        if operation[:2] == ["rm", "-f"]:
            self.alive = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if operation[:2] == ["volume", "rm"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


def test_lifecycle_uses_list_argv_secret_env_and_cleans_owned_resources() -> None:
    docker = DockerFake()
    with postgres.one_off_postgres(
        database="fdc_rehearsal_runtime",
        command_runner=docker,
        host_probe=lambda *_: True,
        sleep=lambda _: None,
    ) as endpoint:
        assert endpoint.port == 55432
        assert docker.alive is True

    assert docker.alive is False
    run_argv, run_kwargs = next(call for call in docker.calls if call[0][1] == "run")
    secret = run_kwargs["env"]["POSTGRES_PASSWORD"]
    assert secret
    assert all(secret not in argument for argument in run_argv)
    assert "POSTGRES_PASSWORD" in run_argv
    assert all(isinstance(call[0], list) for call in docker.calls)


def test_body_failure_is_preserved_when_cleanup_succeeds() -> None:
    docker = DockerFake()
    with pytest.raises(ValueError, match="primary"):
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=docker,
            host_probe=lambda *_: True,
            sleep=lambda _: None,
        ):
            raise ValueError("primary")
    assert docker.alive is False


def test_ready_timeout_is_sanitized_and_cleanup_runs() -> None:
    docker = DockerFake(ready=False)
    ticks = iter((0.0, 2.0, 2.0))
    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=docker,
            host_probe=lambda *_: False,
            monotonic=lambda: next(ticks),
            sleep=lambda _: None,
            ready_timeout=1.0,
        ):
            pytest.fail("must not yield")
    assert raised.value.reason_code == "REHEARSAL_NOT_READY"
    assert docker.alive is False


def test_run_timeout_uses_label_fallback_cleanup() -> None:
    class LostResponse(DockerFake):
        def __call__(self, argv: list[str], **kwargs: Any) -> Any:
            if argv[1] == "run":
                self.calls.append((argv, kwargs))
                self.alive = True
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return super().__call__(argv, **kwargs)

    docker = LostResponse()
    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=docker,
            host_probe=lambda *_: True,
            sleep=lambda _: None,
        ):
            pytest.fail("must not yield")
    assert raised.value.reason_code == "DOCKER_TIMEOUT"
    assert docker.alive is False
    assert any("label=fdc.rehearsal.run=" in " ".join(call[0]) for call in docker.calls)


def test_missing_docker_is_not_misreported_as_cleanup_failure() -> None:
    def missing(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError

    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=missing,
            host_probe=lambda *_: True,
            sleep=lambda _: None,
        ):
            pytest.fail("must not yield")
    assert raised.value.reason_code == "DOCKER_UNAVAILABLE"
    assert raised.value.primary_reason_code is None


def test_cleanup_failure_is_final_and_keeps_primary_reason() -> None:
    class CleanupFailure(DockerFake):
        def __call__(self, argv: list[str], **kwargs: Any) -> Any:
            if argv[1:3] == ["rm", "-f"]:
                self.calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 1, "", "failure-token")
            return super().__call__(argv, **kwargs)

    docker = CleanupFailure()
    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=docker,
            host_probe=lambda *_: True,
            sleep=lambda _: None,
        ):
            raise postgres.RehearsalError("TARGET_NOT_FRESH", 1)
    assert raised.value.reason_code == "REHEARSAL_CLEANUP_FAILED"
    assert raised.value.primary_reason_code == "TARGET_NOT_FRESH"


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        ("Cannot connect to the Docker daemon", "DOCKER_DAEMON_DOWN"),
        ("port is already allocated", "DOCKER_PORT_UNAVAILABLE"),
        ("failed to pull image", "DOCKER_IMAGE_UNAVAILABLE"),
    ],
)
def test_run_failures_have_fixed_reason_codes(stderr: str, reason: str) -> None:
    class Failing(DockerFake):
        def __call__(self, argv: list[str], **kwargs: Any) -> Any:
            if argv[1] == "run":
                self.calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 1, "", stderr)
            return super().__call__(argv, **kwargs)

    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=Failing(),
            host_probe=lambda *_: True,
            sleep=lambda _: None,
        ):
            pytest.fail("must not yield")
    assert raised.value.reason_code == reason


def test_module_has_no_third_party_top_level_import_or_wrapper_cycle() -> None:
    tree = ast.parse(
        (SCRIPTS_ROOT / "rehearsal_postgres.py").read_text(encoding="utf-8")
    )
    imports = {
        node.names[0].name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
    }
    imports.update(
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert "psycopg" not in imports
    assert "sqlalchemy" not in imports
    assert "rehearse_schema" not in imports
    assert postgres.POSTGRES_IMAGE.endswith(
        "cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
    )
    source = (SCRIPTS_ROOT / "rehearsal_postgres.py").read_text(encoding="utf-8")
    assert all(command not in source for command in ("flock", "pkill", "trap"))
