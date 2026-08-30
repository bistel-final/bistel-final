"""Cross-platform lifecycle for an isolated, one-off Neo4j container."""

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
NEO4J_IMAGE = (
    "neo4j:5.26.28-community@sha256:"
    "bc27160e06cbd0b33c8ef557384c73449542f0ba706f0f406b58960c202c91ad"
)
NEO4J_USER = "neo4j"
NEO4J_DATABASE = "neo4j"
LABEL_KEY = "fdc.neo4j-timeout.run"
COMMAND_TIMEOUT_SECONDS = 20.0
PULL_TIMEOUT_SECONDS = 600.0
READY_TIMEOUT_SECONDS = 90.0
READY_INTERVAL_SECONDS = 0.5
PORT_PATTERN = re.compile(r"(?:127\.0\.0\.1|0\.0\.0\.0|::):(?P<port>[0-9]+)$")

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
HostProbe = Callable[[str, int, str, str, str], bool]


class Neo4jRehearsalError(RuntimeError):
    """Sanitized lifecycle failure safe to expose through test output."""

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
class Neo4jRehearsalEndpoint:
    host: str = field(repr=False)
    port: int = field(repr=False)
    database: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    container_id: str = field(repr=False)

    @property
    def uri(self) -> str:
        return f"bolt://{self.host}:{self.port}"


def _default_host_probe(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> bool:
    """Import the driver lazily so lifecycle unit tests need only pytest."""

    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            f"bolt://{host}:{port}",
            auth=(username, password),
            connection_timeout=2.0,
        )
        try:
            driver.verify_connectivity()
            with driver.session(database=database) as session:
                return session.run("RETURN 1 AS value").single()["value"] == 1
        finally:
            driver.close()
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
        raise Neo4jRehearsalError("DOCKER_UNAVAILABLE") from exc
    except subprocess.TimeoutExpired as exc:
        raise Neo4jRehearsalError("DOCKER_TIMEOUT") from exc


def _failure_reason(completed: subprocess.CompletedProcess[str]) -> str:
    detail = f"{completed.stdout}\n{completed.stderr}".lower()
    if "cannot connect" in detail or "daemon" in detail:
        return "DOCKER_DAEMON_DOWN"
    if "port is already allocated" in detail or "bind" in detail:
        return "DOCKER_PORT_UNAVAILABLE"
    if "pull" in detail or "manifest" in detail or "image" in detail:
        return "DOCKER_IMAGE_UNAVAILABLE"
    return "DOCKER_DAEMON_DOWN"


def _pull_image(*, runner: CommandRunner) -> None:
    try:
        completed = _command(
            ("docker", "pull", "--quiet", NEO4J_IMAGE),
            runner=runner,
            timeout=PULL_TIMEOUT_SECONDS,
        )
    except Neo4jRehearsalError as exc:
        if exc.reason_code == "DOCKER_TIMEOUT":
            raise Neo4jRehearsalError("DOCKER_IMAGE_UNAVAILABLE") from exc
        raise
    if completed.returncode != 0:
        raise Neo4jRehearsalError(_failure_reason(completed))


def _ids_for_token(token: str, *, runner: CommandRunner) -> tuple[str, ...]:
    completed = _command(
        ("docker", "ps", "-aq", "--filter", f"label={LABEL_KEY}={token}"),
        runner=runner,
    )
    if completed.returncode != 0:
        raise Neo4jRehearsalError("REHEARSAL_CLEANUP_FAILED")
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _remove_owned_resources(
    token: str,
    *,
    container_id: str | None,
    runner: CommandRunner,
    sleep: Callable[[float], None],
) -> None:
    known_ids = {container_id} if container_id else set()
    for attempt in range(3):
        failed = False
        try:
            known_ids.update(_ids_for_token(token, runner=runner))
        except Neo4jRehearsalError:
            failed = True
        for owned_id in tuple(value for value in known_ids if value):
            try:
                completed = _command(
                    ("docker", "rm", "-f", "-v", owned_id),
                    runner=runner,
                )
            except Neo4jRehearsalError:
                failed = True
            else:
                detail = f"{completed.stdout}\n{completed.stderr}".lower()
                if completed.returncode != 0 and "no such container" not in detail:
                    failed = True
        try:
            remaining = _ids_for_token(token, runner=runner)
        except Neo4jRehearsalError:
            failed = True
            remaining = ("unknown",)
        if not remaining and not failed:
            return
        if attempt < 2:
            sleep(0.25 * (2**attempt))
    raise Neo4jRehearsalError("REHEARSAL_CLEANUP_FAILED")


def _published_port(container_id: str, *, runner: CommandRunner) -> int:
    completed = _command(("docker", "port", container_id, "7687/tcp"), runner=runner)
    if completed.returncode != 0:
        raise Neo4jRehearsalError("DOCKER_PORT_UNAVAILABLE")
    for line in completed.stdout.splitlines():
        match = PORT_PATTERN.search(line.strip())
        if match:
            port = int(match.group("port"))
            if 1 <= port <= 65535:
                return port
    raise Neo4jRehearsalError("DOCKER_PORT_UNAVAILABLE")


def _wait_ready(
    endpoint: Neo4jRehearsalEndpoint,
    *,
    host_probe: HostProbe,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    ready_timeout: float,
) -> None:
    deadline = monotonic() + ready_timeout
    while monotonic() < deadline:
        if host_probe(
            endpoint.host,
            endpoint.port,
            endpoint.database,
            endpoint.username,
            endpoint.password,
        ):
            return
        sleep(READY_INTERVAL_SECONDS)
    raise Neo4jRehearsalError("REHEARSAL_NOT_READY")


@contextmanager
def one_off_neo4j(
    *,
    command_runner: CommandRunner = subprocess.run,
    host_probe: HostProbe = _default_host_probe,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ready_timeout: float = READY_TIMEOUT_SECONDS,
) -> Iterator[Neo4jRehearsalEndpoint]:
    """Start, verify, yield, and fully remove one label-owned Neo4j."""

    token = f"fdc-neo4j-timeout-{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    child_env = dict(os.environ)
    child_env["NEO4J_AUTH"] = f"{NEO4J_USER}/{password}"
    container_id: str | None = None
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
                    "NEO4J_AUTH",
                    "-e",
                    "NEO4J_server_memory_heap_initial__size=128m",
                    "-e",
                    "NEO4J_server_memory_heap_max__size=256m",
                    "-e",
                    "NEO4J_server_memory_pagecache_size=128m",
                    "-p",
                    "127.0.0.1::7687",
                    NEO4J_IMAGE,
                ),
                runner=command_runner,
                child_env=child_env,
            )
        except Neo4jRehearsalError as exc:
            cleanup_required = exc.reason_code == "DOCKER_TIMEOUT"
            raise
        if completed.returncode != 0:
            raise Neo4jRehearsalError(_failure_reason(completed))
        cleanup_required = True
        container_id = completed.stdout.strip()
        if not container_id:
            raise Neo4jRehearsalError("DOCKER_TIMEOUT")
        endpoint = Neo4jRehearsalEndpoint(
            host="127.0.0.1",
            port=_published_port(container_id, runner=command_runner),
            database=NEO4J_DATABASE,
            username=NEO4J_USER,
            password=password,
            container_id=container_id,
        )
        _wait_ready(
            endpoint,
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
                    runner=command_runner,
                    sleep=sleep,
                )
            except Neo4jRehearsalError as cleanup_error:
                primary_reason = None
                if primary is not None:
                    primary_reason = (
                        primary.reason_code
                        if isinstance(primary, Neo4jRehearsalError)
                        else "INTERNAL_ERROR"
                    )
                raise Neo4jRehearsalError(
                    "REHEARSAL_CLEANUP_FAILED",
                    primary_reason_code=primary_reason,
                ) from cleanup_error
