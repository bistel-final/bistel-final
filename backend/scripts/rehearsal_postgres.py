"""Cross-platform lifecycle for an isolated, one-off PostgreSQL container."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

EXIT_USAGE = 2
POSTGRES_IMAGE = (
    "postgres@sha256:"
    "cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
)
POSTGRES_USER = "postgres"
LABEL_KEY = "fdc.rehearsal.run"
COMMAND_TIMEOUT_SECONDS = 20.0
# `docker run`은 image가 없으면 암묵적으로 pull한다. 411MB image의 최초 내려받기는
# 20초를 넘기므로 사전 pull을 별도 timeout으로 분리한다(구현리뷰 부록 B).
PULL_TIMEOUT_SECONDS = 600.0
READY_TIMEOUT_SECONDS = 60.0
READY_INTERVAL_SECONDS = 0.5
PORT_PATTERN = re.compile(r"(?:127\.0\.0\.1|0\.0\.0\.0|::):(?P<port>[0-9]+)$")

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
HostProbe = Callable[[str, int, str, str, str], bool]


class RehearsalError(RuntimeError):
    """Sanitized lifecycle failure safe to expose through the wrapper."""

    def __init__(
        self,
        reason_code: str,
        exit_code: int = EXIT_USAGE,
        *,
        primary_reason_code: str | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.primary_reason_code = primary_reason_code


@dataclass(frozen=True)
class RehearsalEndpoint:
    host: str = field(repr=False)
    port: int = field(repr=False)
    database: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    container_id: str = field(repr=False)


def _default_host_probe(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> bool:
    """Import psycopg lazily so Windows contract tests need only pytest."""

    try:
        import psycopg

        with psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=username,
            password=password,
            connect_timeout=2,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except Exception:
        return False


def _command(
    argv: Sequence[str],
    *,
    runner: CommandRunner,
    child_env: Mapping[str, str] | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(argv),
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=None if child_env is None else dict(child_env),
        )
    except FileNotFoundError as exc:
        raise RehearsalError("DOCKER_UNAVAILABLE") from exc
    except subprocess.TimeoutExpired as exc:
        raise RehearsalError("DOCKER_TIMEOUT") from exc


def _pull_image(*, runner: CommandRunner) -> None:
    """`docker run` 전에 image를 확보한다. 이미 있으면 즉시 반환한다."""

    try:
        completed = _command(
            ("docker", "pull", "--quiet", POSTGRES_IMAGE),
            runner=runner,
            timeout=PULL_TIMEOUT_SECONDS,
        )
    except RehearsalError as exc:
        if exc.reason_code == "DOCKER_TIMEOUT":
            raise RehearsalError("DOCKER_IMAGE_UNAVAILABLE") from exc
        raise
    if completed.returncode != 0:
        raise RehearsalError(_pull_failure_reason(completed))


def _pull_failure_reason(completed: subprocess.CompletedProcess[str]) -> str:
    detail = f"{completed.stdout}\n{completed.stderr}".lower()
    if "cannot connect" in detail or "daemon" in detail:
        return "DOCKER_DAEMON_DOWN"
    return "DOCKER_IMAGE_UNAVAILABLE"


def _run_failure_reason(completed: subprocess.CompletedProcess[str]) -> str:
    detail = f"{completed.stdout}\n{completed.stderr}".lower()
    if "cannot connect" in detail or "daemon" in detail:
        return "DOCKER_DAEMON_DOWN"
    if "port is already allocated" in detail or "bind" in detail:
        return "DOCKER_PORT_UNAVAILABLE"
    if "pull" in detail or "manifest" in detail or "image" in detail:
        return "DOCKER_IMAGE_UNAVAILABLE"
    return "DOCKER_DAEMON_DOWN"


def _ids_for_token(token: str, *, runner: CommandRunner) -> tuple[str, ...]:
    completed = _command(
        (
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label={LABEL_KEY}={token}",
        ),
        runner=runner,
    )
    if completed.returncode != 0:
        raise RehearsalError("REHEARSAL_CLEANUP_FAILED")
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _volume_ids(container_id: str, *, runner: CommandRunner) -> tuple[str, ...]:
    completed = _command(
        (
            "docker",
            "inspect",
            "--format",
            '{{range .Mounts}}{{if eq .Type "volume"}}{{println .Name}}{{end}}{{end}}',
            container_id,
        ),
        runner=runner,
    )
    if completed.returncode != 0:
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _remove_owned_resources(
    token: str,
    *,
    container_id: str | None,
    volume_ids: Sequence[str],
    runner: CommandRunner,
    sleep: Callable[[float], None],
) -> None:
    known_containers = {container_id} if container_id else set()
    for attempt in range(3):
        failed = False
        try:
            known_containers.update(_ids_for_token(token, runner=runner))
        except RehearsalError:
            failed = True
        for owned_id in tuple(value for value in known_containers if value):
            try:
                completed = _command(
                    ("docker", "rm", "-f", "-v", owned_id), runner=runner
                )
            except RehearsalError:
                failed = True
            else:
                if completed.returncode != 0 and "no such container" not in (
                    f"{completed.stdout}\n{completed.stderr}".lower()
                ):
                    failed = True
        for volume_id in volume_ids:
            try:
                completed = _command(
                    ("docker", "volume", "rm", "-f", volume_id), runner=runner
                )
            except RehearsalError:
                failed = True
            else:
                if completed.returncode != 0 and "no such volume" not in (
                    f"{completed.stdout}\n{completed.stderr}".lower()
                ):
                    failed = True
        try:
            remaining = _ids_for_token(token, runner=runner)
        except RehearsalError:
            failed = True
            remaining = ("unknown",)
        if not remaining and not failed:
            return
        if attempt < 2:
            sleep(0.25 * (2**attempt))
    raise RehearsalError("REHEARSAL_CLEANUP_FAILED")


def _published_port(container_id: str, *, runner: CommandRunner) -> int:
    completed = _command(("docker", "port", container_id, "5432/tcp"), runner=runner)
    if completed.returncode != 0:
        raise RehearsalError("DOCKER_PORT_UNAVAILABLE")
    for line in completed.stdout.splitlines():
        match = PORT_PATTERN.search(line.strip())
        if match:
            port = int(match.group("port"))
            if 1 <= port <= 65535:
                return port
    raise RehearsalError("DOCKER_PORT_UNAVAILABLE")


def _wait_ready(
    endpoint: RehearsalEndpoint,
    *,
    runner: CommandRunner,
    host_probe: HostProbe,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    ready_timeout: float,
) -> None:
    deadline = monotonic() + ready_timeout
    while monotonic() < deadline:
        completed = _command(
            (
                "docker",
                "exec",
                endpoint.container_id,
                "pg_isready",
                "-U",
                endpoint.username,
                "-d",
                endpoint.database,
            ),
            runner=runner,
        )
        if completed.returncode == 0 and host_probe(
            endpoint.host,
            endpoint.port,
            endpoint.database,
            endpoint.username,
            endpoint.password,
        ):
            return
        sleep(READY_INTERVAL_SECONDS)
    raise RehearsalError("REHEARSAL_NOT_READY")


@contextmanager
def one_off_postgres(
    *,
    database: str,
    command_runner: CommandRunner = subprocess.run,
    host_probe: HostProbe = _default_host_probe,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ready_timeout: float = READY_TIMEOUT_SECONDS,
) -> Iterator[RehearsalEndpoint]:
    """Start, verify, yield, and fully remove an owned PostgreSQL container."""

    token = f"fdc-rehearsal-{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    child_env = dict(os.environ)
    child_env["POSTGRES_PASSWORD"] = password
    container_id: str | None = None
    volumes: tuple[str, ...] = ()
    primary: BaseException | None = None
    cleanup_required = False
    try:
        _pull_image(runner=command_runner)
        try:
            completed = _command(
                (
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    token,
                    "--label",
                    f"{LABEL_KEY}={token}",
                    "-e",
                    "POSTGRES_PASSWORD",
                    "-e",
                    f"POSTGRES_DB={database}",
                    "-p",
                    "127.0.0.1::5432",
                    POSTGRES_IMAGE,
                ),
                runner=command_runner,
                child_env=child_env,
            )
        except RehearsalError as exc:
            cleanup_required = exc.reason_code == "DOCKER_TIMEOUT"
            raise
        if completed.returncode != 0:
            raise RehearsalError(_run_failure_reason(completed))
        cleanup_required = True
        candidate = completed.stdout.strip()
        if not candidate:
            raise RehearsalError("DOCKER_TIMEOUT")
        container_id = candidate
        volumes = _volume_ids(container_id, runner=command_runner)
        endpoint = RehearsalEndpoint(
            host="127.0.0.1",
            port=_published_port(container_id, runner=command_runner),
            database=database,
            username=POSTGRES_USER,
            password=password,
            container_id=container_id,
        )
        _wait_ready(
            endpoint,
            runner=command_runner,
            host_probe=host_probe,
            monotonic=monotonic,
            sleep=sleep,
            ready_timeout=ready_timeout,
        )
        yield endpoint
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if cleanup_required:
            try:
                _remove_owned_resources(
                    token,
                    container_id=container_id,
                    volume_ids=volumes,
                    runner=command_runner,
                    sleep=sleep,
                )
            except RehearsalError as cleanup_error:
                primary_reason = None
                if primary is not None:
                    primary_reason = (
                        primary.reason_code
                        if isinstance(primary, RehearsalError)
                        else "INTERNAL_ERROR"
                    )
                raise RehearsalError(
                    "REHEARSAL_CLEANUP_FAILED",
                    EXIT_USAGE,
                    primary_reason_code=primary_reason,
                ) from cleanup_error
