"""Concrete fixed-image production ports; called only by the operator CLI.

Local env updates preserve unrelated bytes; no tags, build/pull, reset, grants,
application writes or batch execution. Subprocess output stays private/bounded.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import uuid
from contextlib import redirect_stdout
from pathlib import Path

from dotenv import dotenv_values

from app.agent.release_aggregate import verify_aggregate
from app.agent.release_artifacts import EvidenceError, parse_json, validate_report_root
from app.agent.u10_images import docker_inspect

KEYS = (
    "AGENT_AUTONOMY_LEVEL",
    "AGENT_LEVEL3_ENABLED",
    "AGENT_LEVEL3_DEMO_ACK",
    "AGENT_ACTION_POLICY",
)


def read_env(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size > 1024 * 1024
        ):
            raise EvidenceError("RELEASE_ENV_INVALID")
        return f.read(1024 * 1024 + 1)


def update_env(
    path,
    expected,
    *,
    level,
    attempt,
    artifact_paths=None,
    action_policy="ACTION-POLICY-V1",
):
    if type(level) is not int or level not in (2, 3):
        raise EvidenceError("RELEASE_ENV_INVALID")
    if action_policy not in {"ACTION-POLICY-V1", "MOCK-NOTIFY-V1"}:
        raise EvidenceError("RELEASE_ENV_INVALID")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", attempt):
        raise EvidenceError("RELEASE_ENV_INVALID")
    if read_env(path) != expected:
        raise EvidenceError("RELEASE_ENV_DRIFT")
    values = dict(
        zip(
            KEYS,
            (
                str(level),
                "true" if level == 3 else "false",
                attempt if level == 3 else "",
                action_policy if level == 3 else "ACTION-POLICY-V1",
            ),
            strict=True,
        )
    )
    if artifact_paths is not None:
        # Stage2 restores the PREPARED previous pair, never arbitrary env keys.
        if (
            type(artifact_paths) is not tuple
            or len(artifact_paths) != 2
            or any(type(v) is not str for v in artifact_paths)
        ):
            raise EvidenceError("RELEASE_ENV_INVALID")
        fault, golden = artifact_paths
        match = re.fullmatch(
            r"/reports/cm-5\.2/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12})/fault-5class\.json",
            fault,
        )
        if (fault or golden) and (
            match is None or golden != f"/reports/cm-5.2/{match[1]}/golden-flow.json"
        ):
            raise EvidenceError("RELEASE_ENV_INVALID")
        values.update(
            AGENT_FAULT_EVAL_ARTIFACT_PATH=fault,
            AGENT_GOLDEN_FLOW_SUMMARY_PATH=golden,
        )
    seen, lines = set(), []
    for line in expected.splitlines(keepends=True):
        match = re.match(rb"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", line)
        key = match[1].decode() if match else None
        if key in values:
            if key in seen:
                raise EvidenceError("RELEASE_ENV_DUPLICATE")
            seen.add(key)
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
            lines.append(f"{key}={values[key]}".encode() + ending)
        else:
            lines.append(line)
    if lines and not lines[-1].endswith(b"\n"):
        lines[-1] += b"\n"
    lines.extend(
        f"{key}={value}\n".encode() for key, value in values.items() if key not in seen
    )
    output = b"".join(lines)
    temporary = path.parent / f".{path.name}.release-{uuid.uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(output)
            f.flush()
            os.fsync(f.fileno())
        if read_env(path) != expected:
            raise EvidenceError("RELEASE_ENV_DRIFT")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


class ProductionPorts:
    def __init__(
        self,
        *,
        repository,
        env_file,
        report_root,
        artifact,
        published_root,
        revision,
        attempt_id,
        image_ids,
        preflight_arguments,
        action_policy="ACTION-POLICY-V1",
        run=subprocess.run,
    ):
        self.repository, self.env_file, self.report_root = (
            repository,
            env_file,
            report_root,
        )
        self.artifact, self.published_root = artifact, published_root
        self.revision, self.attempt = revision, attempt_id
        self.images, self.preflight_arguments = (
            dict(image_ids),
            list(preflight_arguments),
        )
        self.run = run
        self.release_policy = action_policy
        self.env_bytes = read_env(env_file)
        values = dotenv_values(
            stream=io.StringIO(self.env_bytes.decode()), interpolate=False
        )
        # Compose does interpolation itself; reject variable indirection in this
        # operational input rather than substitute a host environment default.
        if any(v is not None and "${" in v for v in values.values()):
            raise EvidenceError("RELEASE_ENV_INTERPOLATION_UNSUPPORTED")
        self.values = values
        self.process_env = {
            k: v
            for k, v in os.environ.items()
            if k not in values
            and not k.startswith(("CM52_PIN_", "AGENT_", "COMPOSE_"))
            and k not in {"SOURCE_REVISION", "TEAM_IMAGE_TAG"}
        }
        self.process_env.update(
            {
                f"CM52_PIN_{role.upper()}_IMAGE": identifier
                for role, identifier in self.images.items()
            }
        )
        directory = repository / "deploy/compose"
        self.compose = [
            "docker",
            "compose",
            "-p",
            "bistel-team",
            "-f",
            str(directory / "docker-compose.team.yml"),
            "-f",
            str(directory / "docker-compose.production-level3.yml"),
            "--env-file",
            str(env_file),
        ]

    def command(self, argv, *, timeout=30):
        if read_env(self.env_file) != self.env_bytes:
            raise EvidenceError("RELEASE_ENV_DRIFT")
        try:
            r = self.run(
                argv,
                env=self.process_env.copy(),
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            if r.returncode or len(r.stdout) > 1024 * 1024:
                raise ValueError
            return r.stdout
        except Exception:
            raise EvidenceError("RELEASE_COMMAND_FAILED") from None

    def containers(self, roles=("backend", "frontend")):
        result = {}
        if not roles or any(role not in {"backend", "frontend"} for role in roles):
            raise EvidenceError("RELEASE_CONTAINER_INVALID")
        for role in roles:
            rows = self.command(
                [
                    "docker",
                    "ps",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    "label=com.docker.compose.project=bistel-team",
                    "--filter",
                    f"label=com.docker.compose.service={role}",
                ]
            ).splitlines()
            if len(rows) != 1 or not re.fullmatch(rb"[0-9a-f]{64}", rows[0]):
                raise EvidenceError("RELEASE_CONTAINER_INVALID")
            result[role] = rows[0].decode()
        return result

    def validate_inputs(self, *, restore_only=False):
        if self.repository.resolve() != Path(__file__).resolve().parents[3]:
            raise EvidenceError("RELEASE_REPOSITORY_INVALID")
        validate_report_root(self.report_root, self.report_root, self.repository)
        if set(self.images) != {"backend", "frontend"}:
            raise EvidenceError("RELEASE_IMAGE_ROLES_INVALID")
        for identifier in self.images.values():
            observed = docker_inspect("image", identifier)
            if observed != {"image_id": identifier, "label_revision": self.revision}:
                raise EvidenceError("RELEASE_IMAGE_BINDING_MISMATCH")
        if (
            self.values.get("POSTGRES_DB") != "kosa_agent"
            or self.values.get("APP_DB_USER") != "kosa_app"
            or self.values.get("SOURCE_REVISION") != self.revision
            or Path(self.values.get("AGENT_EVAL_REPORTS_DIR") or "").resolve()
            != self.report_root.resolve()
        ):
            raise EvidenceError("RELEASE_ENV_BINDING_MISMATCH")
        ids = self.containers(("backend",) if restore_only else ("backend", "frontend"))
        mounts = parse_json(
            self.command(
                [
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{json .Mounts}}",
                    ids["backend"],
                ]
            )
        )
        reports = [m for m in mounts if m.get("Destination") == "/reports"]
        if (
            len(reports) != 1
            or reports[0].get("RW") is not False
            or reports[0].get("Type") != "bind"
            or Path(reports[0].get("Source", "")).resolve()
            != self.report_root.resolve()
        ):
            raise EvidenceError("RELEASE_FENCE_MOUNT_MISMATCH")
        if not restore_only:
            result = verify_aggregate(
                self.artifact,
                published_root=self.published_root,
                expected_revision=self.revision,
                expected_attempt_id=self.attempt,
            )
            if (
                result.robustness_verdict != "PASS"
                or result.delivery_integrity != "PASS"
            ):
                raise EvidenceError("RELEASE_THREE_GATE_DENIED")

    def active_runs(self):
        result = parse_json(
            self.command(
                [
                    "docker",
                    "exec",
                    self.containers(("backend",))["backend"],
                    "python",
                    "-B",
                    "/workspace/backend/scripts/read_release_quiescence.py",
                ]
            )
        )
        if (
            set(result) != {"schema_version", "active_runs"}
            or result["schema_version"] != "level3-quiescence-v1"
            or type(result["active_runs"]) is not int
            or result["active_runs"] < 0
        ):
            raise EvidenceError("RELEASE_QUIESCENCE_UNAVAILABLE")
        return result["active_runs"]

    def configure(self, level):
        self.env_bytes = update_env(
            self.env_file,
            self.env_bytes,
            level=level,
            attempt=self.attempt,
            action_policy=self.release_policy,
        )

    def qualify(self, *, fence):
        from datetime import UTC, datetime

        from app.agent.investigation_budget import profile_for_new_run
        from app.agent.release_qualification import issue_release_grant

        args = dict(
            zip(
                self.preflight_arguments[::2],
                self.preflight_arguments[1::2],
                strict=True,
            )
        )
        return issue_release_grant(
            fence=fence,
            reports_root=self.report_root,
            repository=self.repository,
            root=self.artifact.parent,
            published_root=self.published_root,
            expected_revision=self.revision,
            expected_attempt_id=self.attempt,
            expected_investigation_budget_profile=profile_for_new_run(3),
            image_ids=self.images,
            artifact=Path(args["--artifact"]),
            evaluation_receipt=Path(args["--evaluation-receipt"]),
            benchmark=Path(args["--benchmark"]),
            pinned_benchmark_sha256=args["--benchmark-sha256"],
            now=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def recreate(self):
        self.command(
            self.compose
            + [
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
            ],
            timeout=180,
        )

    def preflight(self, level):
        from scripts.u10_preflight import main

        args = self.preflight_arguments + [
            "--profile",
            f"production_level{level}",
            "--phase",
            "post_start_pre_enable",
        ]
        for role, identifier in self.images.items():
            args += ["--image-id", f"{role}={identifier}"]
        for role, identifier in self.containers().items():
            args += ["--container-id", f"{role}={identifier}"]
        if level == 3:
            args += [
                "--expected-attempt-id",
                self.attempt,
                "--robustness-artifact",
                str(self.artifact),
                "--robustness-published-root",
                str(self.published_root),
            ]
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(args)
        report = json.loads(output.getvalue())
        if code != 0:
            raise EvidenceError("RELEASE_PREFLIGHT_FAILED")
        return report
