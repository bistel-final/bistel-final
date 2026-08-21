"""Final-epoch PostgreSQL rebuild runner의 fail-closed 실행 골격.

V5-CM-2.1은 실제 schema/data handler를 배선하지 않는다.  후속 Task가 이
모듈의 MODE_HANDLERS/POSTCHECKS를 채우기 전까지 모든 DB mode는 연결 전에
MODE_NOT_WIRED로 차단된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn, Protocol

import mutation_runtime
import schema_lock
import sqlalchemy
from db_target import (
    ALLOWED_DATABASES,
    BOOTSTRAP_DRIVER,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    validate_url_components,
)
from manifest_v3 import ManifestSchemaError, scan_for_sensitive_values
from sqlalchemy.engine import URL

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
DATASET_EPOCH_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"
SOURCE_MANIFEST_PATH = BOOTSTRAP_ROOT / "source-manifest-v4.json"
INTAKE_PATH = BOOTSTRAP_ROOT / "final-zip-intake.json"

DATASET_EPOCH = "fdc_final_20260818"
REHEARSAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
REHEARSAL_DATABASE_PROFILE = {
    "fdc_rehearsal_runtime": "runtime",
    "fdc_rehearsal_evaluation": "evaluation",
}
REHEARSAL_LOGICAL_TARGETS = {
    "runtime": ("kosa_agent", "kosa_agent_e2e"),
    "evaluation": ("kosa_text2sql",),
}
REHEARSAL_LOCK_ID = {
    "fdc_rehearsal_runtime": 11,
    "fdc_rehearsal_evaluation": 12,
}
REHEARSAL_ENV_KEYS = (
    "POSTGRES_REHEARSAL_HOST",
    "POSTGRES_REHEARSAL_PORT",
    "POSTGRES_REHEARSAL_DATABASE",
    "POSTGRES_REHEARSAL_USER",
    "POSTGRES_REHEARSAL_PASSWORD",
)
CHANGE_REF_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

EPOCH_KEYS = frozenset(
    {
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "received_date",
        "archive",
        "inventory_scope",
        "intake_artifact",
        "supersedes",
    }
)
SOURCE_MANIFEST_KEYS = frozenset(
    {
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "source_archive_sha256",
        "selected_entry_manifest_sha256",
        "schema_sha256",
        "generator_sha256",
        "canonicalization_version",
        "hash_algorithm",
        "value_normalization_version",
        "derived_from",
        "tables",
        "artifacts",
        "origin_package",
        "generator_reproduction",
    }
)
INTAKE_REQUIRED_KEYS = frozenset(
    {
        "format_version",
        "artifact_type",
        "declared_target_epoch",
        "archive",
    }
)

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3


class RunMode(StrEnum):
    PREFLIGHT = "preflight"
    REHEARSE = "rehearse"
    APPLY = "apply"
    REGISTER_MANIFESTS = "register_manifests"


class CommitPolicy(StrEnum):
    READ_ONLY = "read_only"
    ROLLBACK_ALWAYS = "rollback_always"
    COMMIT = "commit"


class RunnerError(RuntimeError):
    """Sanitized CLI 실패 계약."""

    def __init__(self, reason_code: str, exit_code: int = EXIT_USAGE) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


class ModeContractError(RunnerError):
    def __init__(self) -> None:
        super().__init__("MODE_CONTRACT_ERROR")


class TargetLike(Protocol):
    host: str
    port: int
    username: str
    password: str
    database: str
    profile: str

    def create_url(self) -> URL: ...


LockHandler = Callable[[Any, str], None]
ModeHandler = Callable[[Any, "ExecutionPlan"], None]
PostCheck = Callable[[Any, "ExecutionPlan"], None]
ManifestHandler = Callable[["ExecutionPlan"], None]
# commit이 실제로 끝난 뒤에만 불린다. marker-last 계약의 유일한 삽입점이다
# (`V5-CM-2.5` 계획 §7.1). 성공 반환은 `None`뿐이다.
PostCommitHook = Callable[[Any, "ExecutionPlan"], None]
EngineFactory = Callable[[TargetLike], Any]


@dataclass(frozen=True)
class RehearsalTarget:
    host: str = field(repr=False)
    port: int = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    database: str
    profile: str

    def create_url(self) -> URL:
        return URL.create(
            drivername=BOOTSTRAP_DRIVER,
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


@dataclass(frozen=True)
class ArtifactPaths:
    epoch: Path = DATASET_EPOCH_PATH
    source_manifest: Path = SOURCE_MANIFEST_PATH
    intake: Path = INTAKE_PATH


DEFAULT_ARTIFACT_PATHS = ArtifactPaths()


@dataclass(frozen=True)
class ExecutionPlan:
    mode: RunMode
    profile: str
    logical_targets: tuple[str, ...]
    target: TargetLike | None = field(default=None, repr=False)
    commit_policy: CommitPolicy | None = None
    change_ref: str | None = None
    confirmed: bool = False
    recover_artifact: bool = False
    acquire_lock: LockHandler | None = field(default=None, repr=False)


# 후속 Task만 이 registry를 채운다. V5-CM-2.1에서는 의도적으로 비어 있다.
MODE_HANDLERS: dict[RunMode, ModeHandler] = {}
POSTCHECKS: dict[RunMode, PostCheck] = {}
MANIFEST_HANDLERS: dict[str, ManifestHandler] = {}
POST_COMMIT_HOOKS: dict[RunMode, PostCommitHook] = {}


def _emit(payload: Mapping[str, Any], *, exit_code: int) -> int:
    print(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return exit_code


class _JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _emit(
            {"reason_code": "ARG_INVALID", "status": "FAILED"},
            exit_code=EXIT_USAGE,
        )
        raise SystemExit(EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonParser(description=__doc__, add_help=True)
    parser.add_argument("--target")
    parser.add_argument("--profile")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--register-manifests", action="store_true")
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--recover-artifact", action="store_true")
    return parser


def _select_mode(args: argparse.Namespace) -> RunMode:
    selected = [
        mode
        for mode, enabled in (
            (RunMode.PREFLIGHT, args.preflight),
            (RunMode.REHEARSE, args.rehearse),
            (RunMode.APPLY, args.apply),
            (RunMode.REGISTER_MANIFESTS, args.register_manifests),
        )
        if enabled
    ]
    if len(selected) != 1:
        raise RunnerError("MODE_CONFLICT")
    return selected[0]


def _require_profile(value: str | None) -> str:
    if value not in REHEARSAL_LOGICAL_TARGETS:
        raise RunnerError("PROFILE_MISMATCH")
    return value


def _required_env(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise RunnerError("TARGET_ENV_INVALID")
    return value


def load_rehearsal_target(
    *, profile: str, environ: Mapping[str, str]
) -> RehearsalTarget:
    values = {key: _required_env(environ, key) for key in REHEARSAL_ENV_KEYS}
    host = values["POSTGRES_REHEARSAL_HOST"].lower()
    database = values["POSTGRES_REHEARSAL_DATABASE"]
    if host not in REHEARSAL_HOSTS:
        raise RunnerError("TARGET_ENV_INVALID")
    if database in ALLOWED_DATABASES or database not in REHEARSAL_DATABASE_PROFILE:
        raise RunnerError("TARGET_ENV_INVALID")
    if REHEARSAL_DATABASE_PROFILE[database] != profile:
        raise RunnerError("PROFILE_MISMATCH")
    try:
        port = int(values["POSTGRES_REHEARSAL_PORT"])
    except ValueError as exc:
        raise RunnerError("TARGET_ENV_INVALID") from exc
    if not 1 <= port <= 65535:
        raise RunnerError("TARGET_ENV_INVALID")
    return RehearsalTarget(
        host=host,
        port=port,
        username=values["POSTGRES_REHEARSAL_USER"],
        password=values["POSTGRES_REHEARSAL_PASSWORD"],
        database=database,
        profile=profile,
    )


def _validate_rehearsal_url(url: URL, target: RehearsalTarget) -> None:
    if (
        url.drivername != BOOTSTRAP_DRIVER
        or url.host != target.host
        or url.port != target.port
        or url.username != target.username
        or url.password != target.password
        or url.database != target.database
    ):
        raise RunnerError("TARGET_ENV_INVALID")


def _load_public_target(
    database: str, *, environ: Mapping[str, str]
) -> BootstrapTarget:
    try:
        return load_bootstrap_target(database, environ=environ)
    except TargetValidationError as exc:
        raise RunnerError("TARGET_ENV_INVALID") from exc


def _commit_policy(mode: RunMode) -> CommitPolicy:
    return {
        RunMode.PREFLIGHT: CommitPolicy.READ_ONLY,
        RunMode.REHEARSE: CommitPolicy.ROLLBACK_ALWAYS,
        RunMode.APPLY: CommitPolicy.COMMIT,
    }[mode]


def _validate_recover_artifact(args: argparse.Namespace, mode: RunMode) -> bool:
    """`--recover-artifact`는 `--apply`와만 조합된다(계획 §7.1).

    engine을 만들기 전에 판정하므로 잘못된 조합은 DB 연결 0회로 끝난다.
    """

    if not args.recover_artifact:
        return False
    if mode is not RunMode.APPLY:
        raise RunnerError("ARG_INVALID")
    return True


def _validate_db_options(
    args: argparse.Namespace,
    *,
    mode: RunMode,
    target: TargetLike,
    public: bool,
) -> str | None:
    if args.confirm:
        raise RunnerError("ARG_INVALID")
    if mode is RunMode.PREFLIGHT:
        if args.confirm_target or args.change_ref:
            raise RunnerError("ARG_INVALID")
        return None
    if args.confirm_target != target.database:
        raise RunnerError("CONFIRM_REQUIRED", EXIT_CONFIRM_REQUIRED)
    if mode is RunMode.REHEARSE:
        if args.change_ref:
            raise RunnerError("ARG_INVALID")
        return None
    if public:
        if not isinstance(args.change_ref, str) or not CHANGE_REF_PATTERN.fullmatch(
            args.change_ref
        ):
            raise RunnerError("ARG_INVALID")
        return args.change_ref
    if args.change_ref:
        raise RunnerError("ARG_INVALID")
    return None


def build_execution_plan(
    args: argparse.Namespace, *, environ: Mapping[str, str]
) -> ExecutionPlan:
    mode = _select_mode(args)
    recover_artifact = _validate_recover_artifact(args, mode)
    if mode is RunMode.REGISTER_MANIFESTS:
        if args.target or args.confirm_target or args.change_ref:
            raise RunnerError("ARG_INVALID")
        profile = _require_profile(args.profile)
        return ExecutionPlan(
            mode=mode,
            profile=profile,
            logical_targets=REHEARSAL_LOGICAL_TARGETS[profile],
            confirmed=bool(args.confirm),
        )

    if args.target not in {"rehearsal", *ALLOWED_DATABASES}:
        raise RunnerError("TARGET_NOT_ALLOWED")
    if args.target == "rehearsal":
        profile = _require_profile(args.profile)
        target: TargetLike = load_rehearsal_target(profile=profile, environ=environ)
        public = False
    else:
        if args.profile is not None:
            raise RunnerError("PROFILE_MISMATCH")
        target = _load_public_target(args.target, environ=environ)
        profile = target.profile
        public = True

    change_ref = _validate_db_options(args, mode=mode, target=target, public=public)
    return ExecutionPlan(
        mode=mode,
        target=target,
        profile=profile,
        logical_targets=REHEARSAL_LOGICAL_TARGETS[profile],
        commit_policy=_commit_policy(mode),
        change_ref=change_ref,
        recover_artifact=recover_artifact,
        acquire_lock=acquire_advisory_lock,
    )


def _read_json_object(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file():
            raise RunnerError("ARTIFACT_INVALID")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except RunnerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("ARTIFACT_INVALID") from exc
    if not isinstance(payload, dict):
        raise RunnerError("ARTIFACT_INVALID")
    return payload, raw


def _require_sha256(value: Any) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise RunnerError("ARTIFACT_INVALID")
    return value


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RunnerError("ARTIFACT_INVALID")
    return value


def validate_artifacts(paths: ArtifactPaths = DEFAULT_ARTIFACT_PATHS) -> None:
    epoch, _ = _read_json_object(paths.epoch)
    manifest, _ = _read_json_object(paths.source_manifest)
    intake, intake_raw = _read_json_object(paths.intake)
    try:
        scan_for_sensitive_values(epoch)
        scan_for_sensitive_values(manifest)
        scan_for_sensitive_values(intake)
    except ManifestSchemaError as exc:
        raise RunnerError("ARTIFACT_INVALID") from exc

    if set(epoch) != EPOCH_KEYS:
        raise RunnerError("ARTIFACT_INVALID")
    if (
        epoch.get("format_version") != 2
        or epoch.get("artifact_type") != "dataset_epoch_registration"
    ):
        raise RunnerError("ARTIFACT_INVALID")
    if not isinstance(epoch.get("dataset_epoch"), str):
        raise RunnerError("ARTIFACT_INVALID")
    epoch_archive = _require_mapping(epoch.get("archive"))
    epoch_sha = _require_sha256(epoch_archive.get("sha256"))

    if set(manifest) != SOURCE_MANIFEST_KEYS:
        raise RunnerError("ARTIFACT_INVALID")
    if (
        manifest.get("format_version") != 4
        or manifest.get("artifact_type") != "source_files"
    ):
        raise RunnerError("ARTIFACT_INVALID")
    if not isinstance(manifest.get("dataset_epoch"), str):
        raise RunnerError("ARTIFACT_INVALID")
    manifest_sha = _require_sha256(manifest.get("source_archive_sha256"))
    intake_file_sha = _require_sha256(manifest.get("selected_entry_manifest_sha256"))

    if not INTAKE_REQUIRED_KEYS.issubset(intake):
        raise RunnerError("ARTIFACT_INVALID")
    if (
        intake.get("format_version") != 1
        or intake.get("artifact_type") != "final_zip_intake"
    ):
        raise RunnerError("ARTIFACT_INVALID")
    if not isinstance(intake.get("declared_target_epoch"), str):
        raise RunnerError("ARTIFACT_INVALID")
    intake_archive = _require_mapping(intake.get("archive"))
    intake_sha = _require_sha256(intake_archive.get("sha256"))
    actual_intake_file_sha = hashlib.sha256(intake_raw).hexdigest()

    if (
        epoch["dataset_epoch"] != DATASET_EPOCH
        or manifest.get("dataset_epoch") != epoch["dataset_epoch"]
        or intake.get("declared_target_epoch") != epoch["dataset_epoch"]
        or manifest_sha != epoch_sha
        or intake_sha != epoch_sha
        or actual_intake_file_sha != intake_file_sha
    ):
        raise RunnerError("EPOCH_MISMATCH", EXIT_MISMATCH)


def advisory_lock_key(database: str) -> tuple[int, int]:
    if database in ALLOWED_DATABASES:
        return schema_lock.advisory_lock_key(database)
    try:
        lock_id = REHEARSAL_LOCK_ID[database]
    except KeyError as exc:
        raise RunnerError("TARGET_NOT_ALLOWED") from exc
    return schema_lock.POSTGRES_SCHEMA_MUTATION_LOCK_NAMESPACE, lock_id


def _one_mapping(result: Any) -> Mapping[str, Any]:
    try:
        return result.mappings().one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise RunnerError("LOCK_BUSY", EXIT_MISMATCH) from exc


def acquire_advisory_lock(connection: Any, database: str) -> None:
    namespace, lock_id = advisory_lock_key(database)
    result = connection.exec_driver_sql(
        "SELECT pg_try_advisory_xact_lock(%s, %s) AS acquired",
        (namespace, lock_id),
    )
    if _one_mapping(result).get("acquired") is not True:
        raise RunnerError("LOCK_BUSY", EXIT_MISMATCH)


def _default_engine_factory(target: TargetLike) -> Any:
    url = target.create_url()
    if isinstance(target, BootstrapTarget):
        try:
            validate_url_components(url, target)
        except TargetValidationError as exc:
            raise RunnerError("TARGET_ENV_INVALID") from exc
        if url.username != target.username or url.password != target.password:
            raise RunnerError("TARGET_ENV_INVALID")
    else:
        _validate_rehearsal_url(url, target)
    return sqlalchemy.create_engine(url, hide_parameters=True, pool_pre_ping=True)


def execute_transactional(
    plan: ExecutionPlan,
    engine_factory: EngineFactory,
    mode_handler: ModeHandler | None,
    postcheck: PostCheck | None,
    post_commit: PostCommitHook | None = None,
) -> int:
    """handler와 postcheck를 한 transaction에서 실행한다.

    `post_commit`은 `CommitPolicy.COMMIT`의 commit이 **실제로 끝난 뒤에만** 불린다.
    rollback·rehearse·handler 실패·postcheck 실패 경로에서는 호출 0회다
    (`V5-CM-2.5` 계획 §7.1-7·8).
    """

    if mode_handler is None or postcheck is None:
        return _emit(
            {
                "next_task": "V5-CM-2.2",
                "reason_code": "MODE_NOT_WIRED",
                "status": "BLOCKED",
            },
            exit_code=EXIT_USAGE,
        )
    if plan.target is None or plan.commit_policy is None or plan.acquire_lock is None:
        raise ModeContractError

    engine = engine_factory(plan.target)
    try:
        with engine.connect() as connection:
            transaction = None
            try:
                transaction = connection.begin()
                mutation_runtime.prepare_transaction(
                    connection,
                    plan.target,
                    readonly=plan.commit_policy is CommitPolicy.READ_ONLY,
                    acquire_lock=plan.acquire_lock,
                )
                outcome = mode_handler(connection, plan)
                if outcome is not None:
                    raise ModeContractError
                postcheck_outcome = postcheck(connection, plan)
                if postcheck_outcome is not None:
                    raise ModeContractError
                if plan.commit_policy is CommitPolicy.COMMIT:
                    transaction.commit()
                    if post_commit is not None:
                        # commit 뒤 transaction.is_active는 False다. 여기서 예외가
                        # 나도 아래 except의 rollback은 실행되지 않으므로 DB는
                        # commit된 상태로 남는다(계획 §7.2).
                        if post_commit(connection, plan) is not None:
                            raise ModeContractError
                else:
                    transaction.rollback()
                return EXIT_OK
            except BaseException:
                if transaction is not None and transaction.is_active:
                    transaction.rollback()
                raise
    finally:
        dispose = getattr(engine, "dispose", None)
        if callable(dispose):
            dispose()


def _run_artifact_mode(
    plan: ExecutionPlan, handlers: Mapping[str, ManifestHandler]
) -> int:
    if not plan.confirmed:
        return _emit(
            {
                "logical_targets": list(plan.logical_targets),
                "profile": plan.profile,
                "reason_code": "CONFIRM_REQUIRED",
                "status": "PREVIEW",
            },
            exit_code=EXIT_CONFIRM_REQUIRED,
        )
    handler = handlers.get(plan.profile)
    if handler is None:
        return _emit(
            {
                "next_task": "V5-CM-2.4",
                "reason_code": "MODE_NOT_WIRED",
                "status": "BLOCKED",
            },
            exit_code=EXIT_USAGE,
        )
    if handler(plan) is not None:
        raise ModeContractError
    return EXIT_OK


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    artifact_paths: ArtifactPaths = DEFAULT_ARTIFACT_PATHS,
    engine_factory: EngineFactory = _default_engine_factory,
    mode_handlers: Mapping[RunMode, ModeHandler] | None = None,
    postchecks: Mapping[RunMode, PostCheck] | None = None,
    manifest_handlers: Mapping[str, ManifestHandler] | None = None,
    post_commits: Mapping[RunMode, PostCommitHook] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        mode = _select_mode(args)
        validate_artifacts(artifact_paths)
        source_environ = os.environ if environ is None else environ
        plan = build_execution_plan(args, environ=source_environ)
        if mode is RunMode.REGISTER_MANIFESTS:
            return _run_artifact_mode(
                plan,
                MANIFEST_HANDLERS if manifest_handlers is None else manifest_handlers,
            )
        handlers = MODE_HANDLERS if mode_handlers is None else mode_handlers
        checks = POSTCHECKS if postchecks is None else postchecks
        hooks = POST_COMMIT_HOOKS if post_commits is None else post_commits
        return execute_transactional(
            plan,
            engine_factory,
            handlers.get(mode),
            checks.get(mode),
            hooks.get(mode),
        )
    except RunnerError as exc:
        return _emit(
            {"reason_code": exc.reason_code, "status": "FAILED"},
            exit_code=exc.exit_code,
        )
    except Exception:
        return _emit(
            {"reason_code": "INTERNAL_ERROR", "status": "FAILED"},
            exit_code=EXIT_USAGE,
        )


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
