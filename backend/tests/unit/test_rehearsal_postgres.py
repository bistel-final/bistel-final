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
        if operation[0] == "pull":
            return subprocess.CompletedProcess(argv, 0, "", "")
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


def test_image_is_pulled_before_run_with_its_own_timeout() -> None:
    """`docker run`의 암묵적 pull에 기대지 않는다 (구현리뷰 부록 B)."""

    docker = DockerFake()
    with postgres.one_off_postgres(
        database="fdc_rehearsal_runtime",
        command_runner=docker,
        host_probe=lambda *_: True,
    ):
        pass

    operations = [argv[1] for argv, _ in docker.calls]
    assert operations[0] == "pull", operations
    assert operations.index("pull") < operations.index("run")

    pull_call = next(call for call in docker.calls if call[0][1] == "pull")
    run_call = next(call for call in docker.calls if call[0][1] == "run")
    assert pull_call[1]["timeout"] == postgres.PULL_TIMEOUT_SECONDS
    assert run_call[1]["timeout"] == postgres.COMMAND_TIMEOUT_SECONDS
    assert pull_call[1]["timeout"] > run_call[1]["timeout"]
    assert postgres.POSTGRES_IMAGE in pull_call[0]


def test_pull_timeout_reports_image_unavailable_not_docker_timeout() -> None:
    """느린 최초 내려받기를 '명령이 느리다'로 뭉개지 않는다."""

    class SlowPull(DockerFake):
        def __call__(self, argv: list[str], **kwargs: Any):
            if argv[1] == "pull":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return super().__call__(argv, **kwargs)

    docker = SlowPull()
    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=docker,
            host_probe=lambda *_: True,
        ):
            pass

    assert raised.value.reason_code == "DOCKER_IMAGE_UNAVAILABLE"
    assert "run" not in [argv[1] for argv, _ in docker.calls]


def test_pull_failure_does_not_start_container() -> None:
    class FailingPull(DockerFake):
        def __call__(self, argv: list[str], **kwargs: Any):
            if argv[1] == "pull":
                return subprocess.CompletedProcess(
                    argv, 1, "", "manifest unknown: manifest unknown"
                )
            return super().__call__(argv, **kwargs)

    docker = FailingPull()
    with pytest.raises(postgres.RehearsalError) as raised:
        with postgres.one_off_postgres(
            database="fdc_rehearsal_runtime",
            command_runner=docker,
            host_probe=lambda *_: True,
        ):
            pass

    assert raised.value.reason_code == "DOCKER_IMAGE_UNAVAILABLE"
    assert docker.alive is False
    assert "run" not in [argv[1] for argv, _ in docker.calls]


# ---------------------------------------------------------------------------
# V5-CM-2.6 — backup client image seam
# ---------------------------------------------------------------------------

_BACKUP_IMAGE = "postgres@sha256:" + "b" * 64


def _lifecycle_argv(image: str | None = None) -> list[list[str]]:
    """기존 `DockerFake`로 lifecycle을 한 번 돌리고 docker argv 전부를 돌려준다."""

    docker = DockerFake()
    kwargs: dict[str, Any] = {
        "database": "fdc_rehearsal_runtime",
        "command_runner": docker,
        "host_probe": lambda *_: True,
        "sleep": lambda _: None,
    }
    if image is not None:
        kwargs["image"] = image
    with postgres.one_off_postgres(**kwargs):
        pass
    return [argv for argv, _ in docker.calls]


@pytest.mark.windows_contract
def test_default_image_argv_is_unchanged() -> None:
    """image를 넘기지 않은 기존 호출부의 argv는 그대로다."""

    seen = _lifecycle_argv()
    pulls = [a for a in seen if a[1] == "pull"]
    runs = [a for a in seen if a[1] == "run"]
    assert pulls and runs
    assert pulls[0][-1] == postgres.POSTGRES_IMAGE
    assert runs[0][-1] == postgres.POSTGRES_IMAGE


@pytest.mark.windows_contract
def test_explicit_image_applies_to_both_pull_and_run() -> None:
    """pull과 run이 반드시 같은 image를 쓴다.

    한쪽만 바꾸면 cold cache에서 `docker run`이 암묵적 pull을 시도해
    `V5-CM-2.2`가 겪은 timeout이 재발한다.
    """

    seen = _lifecycle_argv(_BACKUP_IMAGE)
    pulls = [a for a in seen if a[1] == "pull"]
    runs = [a for a in seen if a[1] == "run"]
    assert pulls[0][-1] == _BACKUP_IMAGE
    assert runs[0][-1] == _BACKUP_IMAGE
    assert postgres.POSTGRES_IMAGE not in {a[-1] for a in pulls + runs}


@pytest.mark.windows_contract
def test_image_seam_keeps_cleanup_owned_by_lifecycle() -> None:
    """다른 image를 써도 cleanup argv는 같은 lifecycle이 낸다."""

    seen = _lifecycle_argv(_BACKUP_IMAGE)
    assert "rm" in [a[1] for a in seen]
