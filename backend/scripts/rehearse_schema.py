"""Run the pinned final schema in a disposable PostgreSQL transaction."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import rebuild_runner
import rehearsal_schema
from rehearsal_postgres import RehearsalEndpoint, RehearsalError, one_off_postgres

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2

REASON_ALLOWLIST = frozenset(
    {
        "ARG_INVALID",
        "ARCHIVE_INVALID",
        "ARCHIVE_MISMATCH",
        "DOCKER_UNAVAILABLE",
        "DOCKER_DAEMON_DOWN",
        "DOCKER_IMAGE_UNAVAILABLE",
        "DOCKER_PORT_UNAVAILABLE",
        "DOCKER_TIMEOUT",
        "REHEARSAL_NOT_READY",
        "REHEARSAL_CLEANUP_FAILED",
        "INTERRUPTED",
        "INTERNAL_ERROR",
        "MODE_CONFLICT",
        "TARGET_NOT_ALLOWED",
        "TARGET_ENV_INVALID",
        "PROFILE_MISMATCH",
        "MODE_NOT_WIRED",
        "EPOCH_MISMATCH",
        "ARTIFACT_INVALID",
        "CONFIRM_REQUIRED",
        "MODE_CONTRACT_ERROR",
        "LOCK_BUSY",
        "TARGET_NOT_FRESH",
        "SCHEMA_FORBIDDEN_STATEMENT",
    }
)

LifecycleFactory = Callable[..., Iterator[RehearsalEndpoint]]


@dataclass(frozen=True)
class RunnerOutcome:
    exit_code: int
    reason_code: str | None


class _JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise RehearsalError("ARG_INVALID", EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--profile", required=True, choices=("runtime", "evaluation"))
    return parser


def _checked(reason_code: str) -> str:
    return reason_code if reason_code in REASON_ALLOWLIST else "INTERNAL_ERROR"


def _emit_error(error: RehearsalError) -> int:
    reason_code = _checked(error.reason_code)
    payload = {"reason_code": reason_code, "status": "FAILED"}
    if error.primary_reason_code is not None:
        payload["primary_reason_code"] = _checked(error.primary_reason_code)
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
    )
    return error.exit_code if reason_code == error.reason_code else EXIT_USAGE


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE) from exc
    if not isinstance(value, dict):
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE)
    return value


def _verified_schema_bytes(
    archive_path: Path, artifact_paths: rebuild_runner.ArtifactPaths
) -> bytes:
    try:
        if archive_path.is_symlink() or not archive_path.is_file():
            raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE)
        archive_bytes = archive_path.read_bytes()
    except RehearsalError:
        raise
    except OSError as exc:
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE) from exc

    epoch = _load_json(artifact_paths.epoch)
    manifest = _load_json(artifact_paths.source_manifest)
    expected_archive = epoch.get("archive", {}).get("sha256")
    expected_member = manifest.get("schema_sha256")
    schema_artifact = manifest.get("artifacts", {}).get("schema_sql", {})
    member_name = schema_artifact.get("file_id")
    if (
        not isinstance(expected_archive, str)
        or not isinstance(expected_member, str)
        or not isinstance(member_name, str)
        or schema_artifact.get("sha256") != expected_member
    ):
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE)
    if hashlib.sha256(archive_bytes).hexdigest() != expected_archive:
        raise RehearsalError("ARCHIVE_MISMATCH", EXIT_MISMATCH)
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            info = archive.getinfo(member_name)
            if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE)
            schema_bytes = archive.read(info)
    except RehearsalError:
        raise
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE) from exc
    if hashlib.sha256(schema_bytes).hexdigest() != expected_member:
        raise RehearsalError("ARCHIVE_MISMATCH", EXIT_MISMATCH)
    return schema_bytes


def _parse_runner_output(text: str, exit_code: int) -> RunnerOutcome:
    lines = [line for line in text.splitlines() if line.strip()]
    if exit_code == EXIT_OK:
        return (
            RunnerOutcome(EXIT_OK, None)
            if not lines
            else RunnerOutcome(EXIT_USAGE, "INTERNAL_ERROR")
        )
    if len(lines) != 1:
        return RunnerOutcome(EXIT_USAGE, "INTERNAL_ERROR")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError:
        return RunnerOutcome(EXIT_USAGE, "INTERNAL_ERROR")
    if not isinstance(payload, dict):
        return RunnerOutcome(EXIT_USAGE, "INTERNAL_ERROR")
    reason_code = payload.get("reason_code")
    if not isinstance(reason_code, str) or reason_code not in REASON_ALLOWLIST:
        return RunnerOutcome(EXIT_USAGE, "INTERNAL_ERROR")
    return RunnerOutcome(exit_code, reason_code)


def _call_runner(
    endpoint: RehearsalEndpoint,
    *,
    profile: str,
    artifact_paths: rebuild_runner.ArtifactPaths,
    handler: Any,
    postcheck: Any,
) -> RunnerOutcome:
    environment = {
        "POSTGRES_REHEARSAL_HOST": endpoint.host,
        "POSTGRES_REHEARSAL_PORT": str(endpoint.port),
        "POSTGRES_REHEARSAL_DATABASE": endpoint.database,
        "POSTGRES_REHEARSAL_USER": endpoint.username,
        "POSTGRES_REHEARSAL_PASSWORD": endpoint.password,
    }
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        exit_code = rebuild_runner.run(
            [
                "--target",
                "rehearsal",
                "--profile",
                profile,
                "--rehearse",
                "--confirm-target",
                endpoint.database,
            ],
            environ=environment,
            artifact_paths=artifact_paths,
            mode_handlers={rebuild_runner.RunMode.REHEARSE: handler},
            postchecks={rebuild_runner.RunMode.REHEARSE: postcheck},
        )
    return _parse_runner_output(stderr.getvalue(), exit_code)


def _database_for_profile(profile: str) -> str:
    return {
        "runtime": "fdc_rehearsal_runtime",
        "evaluation": "fdc_rehearsal_evaluation",
    }[profile]


def _run(
    argv: Sequence[str] | None,
    *,
    artifact_paths: rebuild_runner.ArtifactPaths = (
        rebuild_runner.DEFAULT_ARTIFACT_PATHS
    ),
    lifecycle: LifecycleFactory = one_off_postgres,
) -> int:
    args = _parser().parse_args(argv)
    schema_bytes = _verified_schema_bytes(Path(args.archive), artifact_paths)
    rebuild_runner.validate_artifacts(artifact_paths)
    handler, postcheck = rehearsal_schema.make_handlers(
        schema_bytes, rebuild_runner.RunnerError
    )
    outcome: RunnerOutcome | None = None
    try:
        with lifecycle(database=_database_for_profile(args.profile)) as endpoint:
            outcome = _call_runner(
                endpoint,
                profile=args.profile,
                artifact_paths=artifact_paths,
                handler=handler,
                postcheck=postcheck,
            )
    except RehearsalError as exc:
        if (
            exc.reason_code == "REHEARSAL_CLEANUP_FAILED"
            and exc.primary_reason_code is None
            and outcome is not None
            and outcome.reason_code is not None
        ):
            raise RehearsalError(
                exc.reason_code,
                exc.exit_code,
                primary_reason_code=outcome.reason_code,
            ) from exc
        raise
    if outcome is None:
        raise RehearsalError("INTERNAL_ERROR", EXIT_USAGE)
    if outcome.reason_code is not None:
        raise RehearsalError(outcome.reason_code, outcome.exit_code)
    return outcome.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(argv)
    except RehearsalError as exc:
        return _emit_error(exc)
    except rebuild_runner.RunnerError as exc:
        return _emit_error(RehearsalError(exc.reason_code, exc.exit_code))
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        return _emit_error(RehearsalError("INTERRUPTED", EXIT_USAGE))
    except Exception:
        return _emit_error(RehearsalError("INTERNAL_ERROR", EXIT_USAGE))


if __name__ == "__main__":
    sys.exit(main())
