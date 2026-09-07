"""V5-C-7.1 Stage2's low-level, pinned three-service preparation adapter.

NOT a Stage2 controller or authorization gate: no team down/restore, reset,
preflight, grant, workload selection, receipt or lifecycle claim is owned here.
The Stage2 owner must hold its lifecycle lock and arrange cleanup before calling
create/start, and validate prepared/grant before calling exec_runner. Plain
legacy full/hold calls remain blocked for Level 3; only Stage2 owns admission.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, ValidationError

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    parse_json,
    validate_report_root,
)
from app.agent.release_prepared import (
    ImageId,
    Revision,
    RuntimeContainers,
    RuntimeImages,
)
from app.agent.release_production import read_env
from app.agent.u10_images import docker_inspect
from app.agent.u10_readiness import ProbeResponse, fetch_gateway

SERVICES = {"backend": "backend", "frontend": "frontend", "runner": "e2e-runner"}
PROJECT = "bistel-team-e2e"
IDLE_COMMAND = ["python", "scripts/e2e_runner_idle.py"]
WORKDIR = "/workspace/backend"
OPTIONAL_SECRET_MOUNTS = {
    "/run/secrets/kafka_client_user",
    "/run/secrets/kafka_client_password",
}
_ZERO_START = "0001-01-01T00:00:00Z"
_HEALTH_FORMAT = "{{json .State.Health.Status}}"
START_TIMEOUT_SECONDS = 120
START_POLL_SECONDS = 2

# Do not expose Config.Env, other labels, secret source paths or raw inspect.
# Only the runner's command is projected (to check that it is inert).
_FORMAT = (
    '{"container_id":{{json .Id}},"image_id":{{json .Image}},'
    '"running":{{json .State.Running}},"status":{{json .State.Status}},'
    '"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},'
    '"started_at":{{json .State.StartedAt}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"oneoff":{{json (index .Config.Labels "com.docker.compose.oneoff")}},'
    '"runner":{{if eq (index .Config.Labels "com.docker.compose.service") '
    '"e2e-runner"}}'
    '{"user":{{json .Config.User}},"workdir":{{json .Config.WorkingDir}},'
    '"command":{{json .Config.Cmd}},"entrypoint":{{json .Config.Entrypoint}},'
    '"init":{{json .HostConfig.Init}},'
    '"restart":{{json .HostConfig.RestartPolicy.Name}}}'
    '{{else}}null{{end}},"mounts":[{{$sep := ""}}{{range .Mounts}}'
    '{{if or (eq .Destination "/reports") '
    '(eq .Destination "/run/secrets/kafka_client_user") '
    '(eq .Destination "/run/secrets/kafka_client_password")}}'
    '{{$sep}}{"destination":{{json .Destination}},"type":{{json .Type}},'
    '"rw":{{json .RW}},"source":{{if eq .Destination "/reports"}}'
    '{{json .Source}}{{else}}null{{end}}}{{$sep = ","}}{{end}}{{end}}]}'
)


def command(argv: list[str], *, env: dict[str, str], timeout: int) -> bytes:
    """No shell; never include subprocess stderr/argv in raised errors."""
    try:
        result = subprocess.run(
            argv, env=env, capture_output=True, timeout=timeout, check=False
        )
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise EvidenceError("LEVEL3_RUNTIME_COMMAND_FAILED")
        return result.stdout
    except (OSError, subprocess.TimeoutExpired, EvidenceError):
        raise EvidenceError("LEVEL3_RUNTIME_COMMAND_FAILED") from None


class ImagePins(EvidenceModel):
    backend: ImageId
    frontend: ImageId
    runner: ImageId


class Mount(EvidenceModel):
    destination: str
    type: Literal["bind"]
    rw: bool
    source: str | None


class RunnerConfig(EvidenceModel):
    user: str
    workdir: Literal["/workspace/backend"]
    command: list[str]
    entrypoint: list[str] | None
    init: Literal[True]
    restart: Literal["no"]


class Container(EvidenceModel):
    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_id: ImageId
    running: bool
    status: Literal["created", "running"]
    paused: Literal[False]
    restarting: Literal[False]
    started_at: str
    project: Literal["bistel-team-e2e"]
    service: Literal["backend", "frontend", "e2e-runner"]
    oneoff: Literal["False"]
    runner: RunnerConfig | None
    mounts: list[Mount] = Field(max_length=3)


class RuntimeSnapshot(EvidenceModel):
    """Internal observation, not a prepared artifact, preflight or SMTP grant."""

    revision: Revision
    phase: Literal["created", "running"]
    images: RuntimeImages
    containers: dict[str, Container]

    def prepared_containers(self) -> RuntimeContainers:
        if self.phase != "running":
            raise EvidenceError("LEVEL3_RUNTIME_NOT_RUNNING")
        return RuntimeContainers.model_validate(
            {
                role: {"container_id": c.container_id, "started_at": c.started_at}
                for role, c in self.containers.items()
            }
        )


class ComposeRuntime:
    """Only Compose creates containers; start/exec use inspected immutable IDs.

    Compose inheritance supplies the existing env/secrets/networks/mounts. A
    separate checked-in override pins images and the inert runner. Runtime env,
    DB identity and readiness still require A's e2e_level3 preflight afterwards.
    No fallback to tags, auto pull/build/recreate, retry or automatic cleanup.
    """

    def __init__(
        self,
        *,
        compose_directory: Path,
        env_file: Path,
        revision: str,
        image_ids: dict[str, str],
        report_root: Path,
        uid: int,
        gid: int,
        run=command,
        inspect_image=docker_inspect,
        action_policy="ACTION-POLICY-V1",
        trail_run_id=None,
        fetch=fetch_gateway,
        sleep=time.sleep,
        monotonic=time.monotonic,
        start_timeout=START_TIMEOUT_SECONDS,
        start_poll=START_POLL_SECONDS,
    ):
        # Validate every argument before any Docker I/O. Stage2 must perform
        # its own pre-team-down gate too; we also check actual mount sources.
        try:
            if action_policy not in {"ACTION-POLICY-V1", "MOCK-NOTIFY-V1"}:
                raise ValueError
            if trail_run_id is not None and (
                type(trail_run_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", trail_run_id) is None
            ):
                raise ValueError
            pins = ImagePins.model_validate(image_ids)
            RuntimeImages.model_validate(
                {
                    role: {"image_id": value, "label_revision": revision}
                    for role, value in pins.model_dump().items()
                }
            )
            if any(type(v) is not int or v < 0 for v in (uid, gid)):
                raise ValueError
            if (
                not callable(fetch)
                or not callable(sleep)
                or not callable(monotonic)
                or type(start_timeout) is not int
                or not 1 <= start_timeout <= START_TIMEOUT_SECONDS
                or type(start_poll) is not int
                or not 1 <= start_poll <= start_timeout
            ):
                raise ValueError
            if any(
                not isinstance(p, Path) or not p.is_absolute()
                for p in (compose_directory, env_file, report_root)
            ):
                raise ValueError
            directory = compose_directory.resolve(strict=True)
            if directory != Path(__file__).resolve().parents[3] / "deploy" / "compose":
                raise ValueError
            self.report_root = str(
                validate_report_root(report_root, report_root, directory.parent.parent)
            )
            if not env_file.is_file() or any(
                not (directory / name).is_file()
                for name in (
                    "docker-compose.team.yml",
                    "docker-compose.e2e-backend.yml",
                    "docker-compose.e2e-level3.yml",
                )
            ):
                raise ValueError
        except (OSError, ValueError, ValidationError):
            raise EvidenceError("LEVEL3_RUNTIME_ARGUMENT_INVALID") from None
        self.images = pins.model_dump()
        self.revision = revision
        self.user = f"{uid}:{gid}"
        self.run = run
        self.inspect_image = inspect_image
        self.fetch = fetch
        self.sleep = sleep
        self.monotonic = monotonic
        self.start_timeout = start_timeout
        self.start_poll = start_poll
        # Compose gives exported host values precedence over --env-file. Pin the
        # private file's bytes and remove its keys from the child environment;
        # never allow the host's DB/recipient/credential values to shadow it.
        self.env_file = env_file
        self.env_bytes = read_env(env_file)
        values = dotenv_values(
            stream=io.StringIO(self.env_bytes.decode()), interpolate=False
        )
        if any(v is not None and "${" in v for v in values.values()):
            raise EvidenceError("RELEASE_ENV_INTERPOLATION_UNSUPPORTED")
        self.env = {
            **{
                k: v
                for k, v in os.environ.items()
                if k not in values
                and not k.startswith(("CM52_PIN_", "AGENT_", "COMPOSE_"))
                and k not in {"SOURCE_REVISION", "TEAM_IMAGE_TAG"}
            },
            **{f"CM52_PIN_{r.upper()}_IMAGE": v for r, v in self.images.items()},
            "CM52_RUNNER_UID": str(uid),
            "CM52_RUNNER_GID": str(gid),
            "SOURCE_REVISION": revision,
            "AGENT_EVAL_REPORTS_DIR": self.report_root,
            "AGENT_FAULT_EVAL_ARTIFACT_PATH": "",
            "AGENT_GOLDEN_FLOW_SUMMARY_PATH": "",
            "CM52_PIN_ACTION_POLICY": action_policy,
        }
        self.compose = ["docker", "compose", "-p", PROJECT]
        if trail_run_id is not None:
            self.env.update(
                DELIVERY_CALLBACK_TRAIL_DIR="/var/lib/bistel/delivery-trail",
                DELIVERY_CALLBACK_TRAIL_RUN_ID=trail_run_id,
            )
        for name in (
            "docker-compose.team.yml",
            "docker-compose.e2e-backend.yml",
            "docker-compose.e2e-level3.yml",
        ):
            self.compose += ["-f", str(directory / name)]
        self.compose += ["--env-file", str(env_file), "--profile", "e2e-runner"]

    def _command(self, argv: list[str], timeout=30) -> bytes:
        self._check_env()
        try:
            value = self.run(argv, env=dict(self.env), timeout=timeout)
            if type(value) is not bytes or len(value) > 4 * 1024 * 1024:
                raise ValueError
            return value
        except Exception:
            raise EvidenceError("LEVEL3_RUNTIME_COMMAND_FAILED") from None

    def _check_env(self) -> None:
        if read_env(self.env_file) != self.env_bytes:
            raise EvidenceError("RELEASE_ENV_DRIFT")

    def _ids(self, *, empty=False) -> dict[str, str]:
        result = {}
        for role, service in SERVICES.items():
            raw = self._command(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    f"label=com.docker.compose.project={PROJECT}",
                    "--filter",
                    f"label=com.docker.compose.service={service}",
                ]
            )
            ids = raw.splitlines()
            if empty:
                if ids:
                    raise EvidenceError("LEVEL3_RUNTIME_ALREADY_EXISTS")
            elif len(ids) != 1 or not re.fullmatch(rb"[0-9a-f]{64}", ids[0]):
                raise EvidenceError("LEVEL3_RUNTIME_POPULATION_INVALID")
            else:
                result[role] = ids[0].decode("ascii")
        if not empty and len(set(result.values())) != 3:
            raise EvidenceError("LEVEL3_RUNTIME_CONTAINER_REUSED")
        return result

    def _images(self) -> RuntimeImages:
        self._check_env()
        try:
            images = RuntimeImages.model_validate(
                {r: self.inspect_image("image", v) for r, v in self.images.items()}
            )
            if any(
                p["image_id"] != self.images[r] or p["label_revision"] != self.revision
                for r, p in images.model_dump().items()
            ):
                raise ValueError
            return images
        except Exception:
            raise EvidenceError("LEVEL3_RUNTIME_IMAGE_MISMATCH") from None

    def _observe(self, ids: dict[str, str], phase: str) -> RuntimeSnapshot:
        containers = {}
        for role, cid in ids.items():
            try:
                c = Container.model_validate(
                    parse_json(
                        self._command(
                            ["docker", "container", "inspect", "--format", _FORMAT, cid]
                        )
                    )
                )
                if (
                    c.container_id != cid
                    or c.image_id != self.images[role]
                    or c.service != SERVICES[role]
                    or c.status != phase
                    or c.running != (phase == "running")
                    or (phase == "created" and c.started_at != _ZERO_START)
                    or (phase == "running" and c.started_at == _ZERO_START)
                ):
                    raise ValueError
                mounts = {m.destination: m for m in c.mounts}
                # Compose environment-sourced secrets may be materialized as
                # container files without appearing in Docker's bind-mount
                # projection. /reports is the only required inspect mount;
                # tolerate explicitly projected read-only secret mounts too.
                expected = {"/reports"} if role != "frontend" else set()
                optional = OPTIONAL_SECRET_MOUNTS if role != "frontend" else set()
                if (
                    not expected.issubset(mounts)
                    or not set(mounts).issubset(expected | optional)
                    or len(mounts) != len(c.mounts)
                ):
                    raise ValueError
                for dest, mount in mounts.items():
                    if dest == "/reports":
                        if mount.source != self.report_root or mount.rw != (
                            role == "runner"
                        ):
                            raise ValueError
                    elif mount.source is not None or mount.rw:
                        raise ValueError
                if role == "runner":
                    if (
                        c.runner is None
                        or c.runner.user != self.user
                        or c.runner.command != IDLE_COMMAND
                        or c.runner.entrypoint not in (None, [])
                    ):
                        raise ValueError
                elif c.runner is not None:
                    raise ValueError
                containers[role] = c
            except Exception:
                raise EvidenceError("LEVEL3_RUNTIME_CONTAINER_INVALID") from None
        snapshot = RuntimeSnapshot(
            revision=self.revision,
            phase=phase,
            images=self._images(),
            containers=containers,
        )
        if phase == "running":
            try:
                snapshot.prepared_containers()
            except Exception:
                raise EvidenceError("LEVEL3_RUNTIME_CONTAINER_INVALID") from None
        return snapshot

    def _stable(self, ids: dict[str, str], phase: str) -> RuntimeSnapshot:
        before = self._observe(ids, phase)
        after = self._observe(ids, phase)
        if before != after or self._ids() != ids:
            raise EvidenceError("LEVEL3_RUNTIME_DRIFT")
        # Two observations detect drift; they are not an atomic Docker lock.
        return after

    def create(self) -> RuntimeSnapshot:
        self._images()  # Missing image/incorrect label fails before Compose.
        self._ids(empty=True)
        self._command(
            self.compose
            + [
                "create",
                "--no-build",
                "--pull",
                "never",
                "--no-recreate",
                *SERVICES.values(),
            ],
            timeout=120,
        )
        return self._stable(self._ids(), "created")

    def _recheck(self, snapshot: RuntimeSnapshot, phase: str) -> dict[str, str]:
        try:
            snapshot = RuntimeSnapshot.model_validate(snapshot.model_dump()).model_copy(
                deep=True
            )
            if (
                snapshot.phase != phase
                or snapshot.revision != self.revision
                or set(snapshot.containers) != set(SERVICES)
                or snapshot.images != self._images()
            ):
                raise ValueError
            ids = {r: c.container_id for r, c in snapshot.containers.items()}
            if self._ids() != ids or self._stable(ids, phase) != snapshot:
                raise ValueError
        except Exception:
            raise EvidenceError("LEVEL3_RUNTIME_DRIFT") from None
        return ids

    def start(self, created: RuntimeSnapshot) -> RuntimeSnapshot:
        ids = self._recheck(created, "created")
        # Start immutable IDs, never resolve a mutable tag/service to a new
        # container here. Kafka/MES must already be healthy (prepare orders it).
        self._command(["docker", "start", *[ids[r] for r in SERVICES]], timeout=120)
        deadline = self.monotonic() + self.start_timeout
        while True:
            ready = False
            try:
                health = parse_json(
                    self._command(
                        [
                            "docker",
                            "container",
                            "inspect",
                            "--format",
                            _HEALTH_FORMAT,
                            ids["backend"],
                        ]
                    )
                )
                if health not in {"starting", "healthy", "unhealthy"}:
                    raise EvidenceError("LEVEL3_RUNTIME_CONTAINER_INVALID")
                if health == "healthy":
                    responses = [
                        self.fetch(path) for path in ("/", "/api/health/ready")
                    ]
                    ready = all(
                        type(response) is ProbeResponse
                        and response.status_code == 200
                        and type(response.body) is bytes
                        for response in responses
                    )
            except EvidenceError as error:
                # A single local HTTP miss/status is the expected startup race.
                # Docker command/contract failures are not readiness retries.
                if str(error) not in {
                    "U10_READINESS_HTTP_FAILED",
                    "U10_READINESS_HTTP_STATUS_INVALID",
                }:
                    raise
            if ready:
                break
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise EvidenceError("LEVEL3_RUNTIME_START_TIMEOUT")
            self.sleep(min(self.start_poll, remaining))
        return self._stable(ids, "running")

    def verify_running(self, runtime: RuntimeSnapshot) -> None:
        """Read-only pin/drift check for the preparation capture window."""
        self._recheck(runtime, "running")

    def exec_runner(
        self, runtime: RuntimeSnapshot, argv: list[str], *, timeout=60
    ) -> bytes:
        if (
            type(argv) is not list
            or not argv
            or any(type(a) is not str or not a or "\0" in a for a in argv)
            or type(timeout) is not int
            or not 1 <= timeout <= 3600
        ):
            raise EvidenceError("LEVEL3_RUNTIME_ARGUMENT_INVALID")
        ids = self._recheck(runtime, "running")
        return self._command(
            [
                "docker",
                "exec",
                "--user",
                self.user,
                "--workdir",
                WORKDIR,
                ids["runner"],
                *argv,
            ],
            timeout=timeout,
        )
