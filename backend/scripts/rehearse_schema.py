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
from types import MappingProxyType
from typing import Any, NoReturn

import rebuild_runner
import rehearsal_profile_loader
import rehearsal_profile_verifier
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
        # V5-CM-2.5 재실행·복구. 이 집합이 recovery wrapper의 유일한 정본이며
        # 별도 allowlist를 만들지 않는다(계획 §9).
        "RECOVERY_REQUIRED",
        "RECOVERY_NOT_ALLOWED",
        "ARTIFACT_MISMATCH",
        "ARTIFACT_WRITE_FAILED",
    }
)

LifecycleFactory = Callable[..., Iterator[RehearsalEndpoint]]

_MODE_FLAG = {
    rebuild_runner.RunMode.REHEARSE: "--rehearse",
    rebuild_runner.RunMode.APPLY: "--apply",
}


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


@dataclass(frozen=True)
class VerifiedTable:
    """검증이 끝난 table 하나. 값이 전부 immutable이다."""

    name: str
    columns: tuple[str, ...]
    body: bytes


@dataclass(frozen=True)
class VerifiedArchiveSnapshot:
    """archive를 **한 번만** 읽어 만든 불변 snapshot.

    schema DDL과 profile CSV를 모두 이 snapshot에서 꺼낸다. 두 번 읽기 사이에 파일이
    교체돼 서로 다른 조합이 섞이는 것을 구조적으로 막는다(계획 §3.2).

    nested 값까지 immutable이라 검증 이후 COPY payload나 column 계약을 바꿀 수 없다
    (구현리뷰 1차 필수 2).

    `verified_tables`는 **이번 profile이 실제로 COPY하는** 8 또는 9종이고,
    `acceptances`는 두 profile 모두 **물리 table 9종 전부**다. runtime에서
    두 컬렉션의 길이가 다른 것은 의도다(`V5-CM-2.4` 계획 §3.2).
    """

    schema_bytes: bytes
    verified_tables: tuple[VerifiedTable, ...]
    acceptances: tuple[rehearsal_profile_verifier.TableAcceptance, ...]

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.verified_tables)

    @property
    def csv_bodies(self) -> Mapping[str, bytes]:
        return MappingProxyType(
            {entry.name: entry.body for entry in self.verified_tables}
        )

    @property
    def columns_by_table(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType(
            {entry.name: entry.columns for entry in self.verified_tables}
        )


def _fail(reason_code: str, exit_code: int) -> RehearsalError:
    return RehearsalError(reason_code, exit_code)


def _verified_archive_snapshot(
    archive_path: Path,
    artifact_paths: rebuild_runner.ArtifactPaths,
    profile: str,
) -> VerifiedArchiveSnapshot:
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
    intake = _load_json(artifact_paths.intake)
    expected_archive = epoch.get("archive", {}).get("sha256")
    expected_member = manifest.get("schema_sha256")
    schema_artifact = manifest.get("artifacts", {}).get("schema_sql", {})
    member_name = schema_artifact.get("file_id")
    manifest_tables = manifest.get("tables")
    if (
        not isinstance(expected_archive, str)
        or not isinstance(expected_member, str)
        or not isinstance(member_name, str)
        or not isinstance(manifest_tables, Mapping)
        or schema_artifact.get("sha256") != expected_member
    ):
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE)
    if hashlib.sha256(archive_bytes).hexdigest() != expected_archive:
        raise RehearsalError("ARCHIVE_MISMATCH", EXIT_MISMATCH)

    manifest_tables = rehearsal_profile_loader.validate_manifest_tables(
        manifest_tables, _fail
    )
    intake_members = rehearsal_profile_loader.validate_intake_members(
        intake.get("selected_members"), _fail
    )
    tables = rehearsal_profile_loader.select_tables(manifest_tables, profile, _fail)
    acceptances = rehearsal_profile_verifier.build_acceptances(
        manifest, sorted(manifest_tables), _fail
    )
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            info = archive.getinfo(member_name)
            if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE)
            schema_bytes = archive.read(info)
            csv_bodies = rehearsal_profile_loader.verified_csv_bodies(
                archive, manifest_tables, intake_members, tables, _fail
            )
    except RehearsalError:
        raise
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise RehearsalError("ARCHIVE_INVALID", EXIT_USAGE) from exc
    if hashlib.sha256(schema_bytes).hexdigest() != expected_member:
        raise RehearsalError("ARCHIVE_MISMATCH", EXIT_MISMATCH)
    return VerifiedArchiveSnapshot(
        schema_bytes=schema_bytes,
        verified_tables=tuple(
            VerifiedTable(
                name=table,
                columns=tuple(manifest_tables[table]["columns"]),
                body=csv_bodies[table],
            )
            for table in tables
        ),
        acceptances=acceptances,
    )


def _composite(
    snapshot: VerifiedArchiveSnapshot,
    profile: str,
    *,
    reference: rehearsal_profile_verifier.AcceptanceReference = (
        rehearsal_profile_verifier.FINAL_REFERENCE
    ),
) -> tuple[
    rehearsal_profile_loader.Handler,
    rehearsal_profile_loader.PostCheck,
]:
    """schema → loader → acceptance. 각 단계 실패 시 이후 단계를 부르지 않는다.

    handler는 schema 1회 → loader 1회, postcheck는 schema → loader 최소 →
    full acceptance 순서다(`V5-CM-2.3` §3.5 · `V5-CM-2.4` §3.8).

    `reference`는 축소 fixture 전용 keyword-only 주입점이다. `_run()`·CLI는 절대
    넘기지 않으므로 production 경로는 항상 최종 epoch 상수를 쓴다(계획 §3.8).
    """

    schema_handler, schema_postcheck = rehearsal_schema.make_handlers(
        snapshot.schema_bytes, rebuild_runner.RunnerError
    )
    load_handler, load_postcheck = rehearsal_profile_loader.make_load_handlers(
        snapshot.csv_bodies,
        snapshot.columns_by_table,
        snapshot.tables,
        profile,
        rebuild_runner.RunnerError,
    )
    acceptance_postcheck = rehearsal_profile_verifier.make_acceptance_postcheck(
        snapshot.acceptances,
        snapshot.tables,
        profile,
        rebuild_runner.RunnerError,
        reference=reference,
    )

    def handler(connection: Any, plan: Any) -> None:
        schema_handler(connection, plan)
        load_handler(connection, plan)

    def postcheck(connection: Any, plan: Any) -> None:
        schema_postcheck(connection, plan)
        load_postcheck(connection, plan)
        acceptance_postcheck(connection, plan)

    return handler, postcheck


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
    mode: rebuild_runner.RunMode = rebuild_runner.RunMode.REHEARSE,
    post_commit: Any = None,
    recover_artifact: bool = False,
) -> RunnerOutcome:
    """runner를 한 번 호출한다.

    기본값은 `V5-CM-2.2` 이후와 같은 rollback rehearsal이다. `V5-CM-2.5`가
    apply·복구를 실증할 때만 `mode`·`post_commit`·`recover_artifact`를 넘긴다.
    """

    environment = {
        "POSTGRES_REHEARSAL_HOST": endpoint.host,
        "POSTGRES_REHEARSAL_PORT": str(endpoint.port),
        "POSTGRES_REHEARSAL_DATABASE": endpoint.database,
        "POSTGRES_REHEARSAL_USER": endpoint.username,
        "POSTGRES_REHEARSAL_PASSWORD": endpoint.password,
    }
    argv = [
        "--target",
        "rehearsal",
        "--profile",
        profile,
        _MODE_FLAG[mode],
        "--confirm-target",
        endpoint.database,
    ]
    if recover_artifact:
        argv.append("--recover-artifact")
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        exit_code = rebuild_runner.run(
            argv,
            environ=environment,
            artifact_paths=artifact_paths,
            mode_handlers={mode: handler},
            postchecks={mode: postcheck},
            post_commits={} if post_commit is None else {mode: post_commit},
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
    snapshot = _verified_archive_snapshot(
        Path(args.archive), artifact_paths, args.profile
    )
    rebuild_runner.validate_artifacts(artifact_paths)
    handler, postcheck = _composite(snapshot, args.profile)
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
