"""공용 PostgreSQL 3 DB의 최종 profile을 한 번에 검증한다 (`V5-CM-4.3`).

기본 mode는 기존 full verifier·role matrix·marker owner를 조립하는 read-only
orchestrator다. ``--promote-profile-markers``는 CM-2.6 외부 증적을 검증한 뒤 profile
marker 3종만 byte-identical하게 저장소로 승격하는 일회성 file-only mode다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

SCRIPTS_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPTS_ROOT.parent
REPOSITORY_ROOT = BACKEND_ROOT.parent
for import_root in (SCRIPTS_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import apply_agent_runtime as agent_runtime  # noqa: E402
import apply_postgres_role_matrix as role_runner  # noqa: E402
import apply_severity_pair_guard as severity_guard  # noqa: E402
import db_target  # noqa: E402
import manifest_v3  # noqa: E402
import postgres_backup  # noqa: E402
import postgres_role_matrix as role_matrix  # noqa: E402
import postgres_transition  # noqa: E402
import setup_checkpoint  # noqa: E402
import transition_public_postgres as public_transition  # noqa: E402
import verify_bootstrap_state as bootstrap_verifier  # noqa: E402

from app.common import rag_readiness  # noqa: E402

TASK_ID: Final = "V5-CM-4.3"
DATASET_EPOCH: Final = manifest_v3.DATASET_EPOCH
TARGETS: Final = ("kosa_agent", "kosa_agent_e2e", "kosa_text2sql")
MARKER_ROOT: Final = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
REPORT_ROOT: Final = REPOSITORY_ROOT / "infra" / "bootstrap" / "reports"

EXIT_OK: Final = 0
EXIT_MISMATCH: Final = 1
EXIT_USAGE: Final = 2
EXIT_UNVERIFIABLE: Final = 7

PASSED: Final = "PASSED"
FAILED: Final = "FAILED"
UNVERIFIABLE: Final = "UNVERIFIABLE"
NOT_RUN: Final = "NOT_RUN"

EXPECTED_STAGE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "kosa_agent": "runtime_checkpointed",
        "kosa_agent_e2e": "runtime_checkpointed",
        "kosa_text2sql": "evaluation_reference",
    }
)
EXPECTED_MARKERS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        database: frozenset(
            {
                f"postgres_profile.{database}.json",
                f"rag_load.{database}.json",
                f"role_matrix_core.{database}.json",
                *(
                    {
                        f"agent_runtime_final.{database}.json",
                        f"agent_severity_guard_final.{database}.json",
                        f"checkpoint_setup_final.{database}.json",
                        f"role_matrix_checkpoint.{database}.json",
                    }
                    if database != "kosa_text2sql"
                    else set()
                ),
            }
        )
        for database in TARGETS
    }
)

# Production에서 호출할 수 있는 owner 함수의 allowlist. apply/recover/producer는 없다.
VERIFY_REGISTRY: Final[Mapping[str, Callable[..., Any]]] = MappingProxyType(
    {
        "full_database": bootstrap_verifier.verify_database,
        "role_contract": role_matrix.build_contract,
        "role_snapshot": role_runner.read_snapshot,
        "role_inspection": role_runner.inspect_snapshot,
        "rag_live": rag_readiness.verify_live_state,
    }
)
VERIFY_REGISTRY_KEYS: Final = frozenset(VERIFY_REGISTRY)


class PublicProfileError(RuntimeError):
    """CLI에 안전하게 노출할 reason code만 보관한다."""

    def __init__(
        self,
        reason_code: str,
        *,
        exit_code: int = EXIT_MISMATCH,
        status: str = FAILED,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.status = status


@dataclass(frozen=True, slots=True)
class TargetReport:
    database: str
    profile: str
    expected_stage: str
    status: str
    checks: Mapping[str, Mapping[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "database": self.database,
            "profile": self.profile,
            "expected_stage": self.expected_stage,
            "status": self.status,
            "checks": {name: dict(value) for name, value in self.checks.items()},
        }
        manifest_v3.scan_for_sensitive_values(payload)
        return payload


def _check(status: str, reason_code: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status}
    if reason_code is not None:
        value["reason_code"] = reason_code
    return value


def _reason(exc: BaseException) -> str:
    value = getattr(exc, "reason_code", getattr(exc, "code", None))
    if isinstance(value, str) and value and len(value) <= 80:
        return value
    return type(exc).__name__.upper()


def _is_unverifiable(exc: BaseException) -> bool:
    return (
        isinstance(exc, SQLAlchemyError | db_target.TargetValidationError)
        or getattr(exc, "exit_code", None) == EXIT_UNVERIFIABLE
    )


def assert_verify_registry_safe(
    registry: Mapping[str, Callable[..., Any]] = VERIFY_REGISTRY,
) -> None:
    if frozenset(registry) != VERIFY_REGISTRY_KEYS:
        raise PublicProfileError("VERIFY_REGISTRY_INVALID", exit_code=EXIT_USAGE)
    forbidden = ("apply", "recover", "save", "record", "write", "mutat")
    for name, function in registry.items():
        function_name = getattr(function, "__name__", "").lower()
        if not callable(function) or any(token in function_name for token in forbidden):
            raise PublicProfileError(
                f"VERIFY_REGISTRY_INVALID_{name.upper()}", exit_code=EXIT_USAGE
            )


def _engine_for(target: db_target.BootstrapTarget) -> Engine:
    url = target.create_url()
    db_target.validate_url_components(url, target)
    return create_engine(url, poolclass=NullPool)


def _read_marker(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PublicProfileError("MARKER_INVALID")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicProfileError("MARKER_INVALID") from exc
    if not isinstance(payload, dict):
        raise PublicProfileError("MARKER_INVALID")
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _role_contract(database: str, *, checkpoint: bool = False) -> Any:
    if database == "kosa_text2sql":
        return VERIFY_REGISTRY["role_contract"](
            database,
            role_matrix.MatrixStage.CORE,
            role_matrix.SchemaStage.EVALUATION_REFERENCE,
        )
    return VERIFY_REGISTRY["role_contract"](
        database,
        (
            role_matrix.MatrixStage.CHECKPOINTED
            if checkpoint
            else role_matrix.MatrixStage.CORE
        ),
        role_matrix.SchemaStage.RUNTIME_CHECKPOINTED,
    )


def _validate_profile_marker(payload: Mapping[str, Any], database: str) -> None:
    try:
        postgres_transition.validate_marker_schema(payload)
    except Exception as exc:
        raise PublicProfileError("PROFILE_MARKER_INVALID") from exc
    if (
        payload.get("database") != database
        or payload.get("profile") != db_target.DATABASE_PROFILE[database]
        or payload.get("base_state") != "FINAL_ADOPTED"
    ):
        raise PublicProfileError("PROFILE_MARKER_INVALID")


def validate_marker_matrix(
    database: str,
    target: db_target.BootstrapTarget,
    *,
    marker_root: Path = MARKER_ROOT,
) -> dict[str, Any]:
    """target marker exact 집합과 artifact별 owner 계약을 검증한다."""

    expected = EXPECTED_MARKERS[database]
    found_paths = tuple(marker_root.glob(f"*.{database}.json"))
    found = {path.name for path in found_paths}
    if found != expected or len(found_paths) != len(found):
        raise PublicProfileError("MARKER_SET_MISMATCH")
    payloads = {name: _read_marker(marker_root / name) for name in sorted(expected)}

    _validate_profile_marker(payloads[f"postgres_profile.{database}.json"], database)
    rag_readiness.validate_marker(
        payloads[f"rag_load.{database}.json"], database=database
    )

    core = _role_contract(database)
    role_runner.validate_marker(
        payloads[f"role_matrix_core.{database}.json"],
        core,
        target,
        marker_root,
    )
    if database != "kosa_text2sql":
        runtime_sql, _ = agent_runtime.load_and_validate_sql()
        agent_runtime.validate_marker(
            payloads[f"agent_runtime_final.{database}.json"],
            target,
            migration_sha=agent_runtime.migration_sha256(runtime_sql),
        )
        guard_sql, _ = severity_guard.load_and_validate_sql()
        severity_guard.validate_marker(
            payloads[f"agent_severity_guard_final.{database}.json"],
            target,
            migration_sha=severity_guard.migration_sha256(guard_sql),
        )
        setup_checkpoint.validate_marker(
            payloads[f"checkpoint_setup_final.{database}.json"], database
        )
        checkpoint_role = _role_contract(database, checkpoint=True)
        role_runner.validate_marker(
            payloads[f"role_matrix_checkpoint.{database}.json"],
            checkpoint_role,
            target,
            marker_root,
        )
    return payloads[f"rag_load.{database}.json"]


def _verify_full_database(
    database: str,
    *,
    environ: Mapping[str, str],
    full_verifier: Callable[..., Any],
) -> dict[str, dict[str, Any]]:
    axes = {
        "epoch": _check(NOT_RUN),
        "stage": _check(NOT_RUN),
        "table_rows_hash": _check(NOT_RUN),
    }
    try:
        result = full_verifier(
            database,
            EXPECTED_STAGE[database],
            environ=environ,
        )
        if getattr(result, "exit_code", EXIT_MISMATCH) != EXIT_OK:
            raise PublicProfileError("FULL_VERIFIER_FAILED")
    except Exception as exc:
        status = UNVERIFIABLE if _is_unverifiable(exc) else FAILED
        reason = _reason(exc)
        if status == UNVERIFIABLE:
            return {name: _check(status, reason) for name in axes}
        details = getattr(exc, "details", {})
        mismatches = (
            details.get("mismatches", ()) if isinstance(details, Mapping) else ()
        )
        kinds = {
            str(item.get("mismatch_kind"))
            for item in mismatches
            if isinstance(item, Mapping) and item.get("mismatch_kind")
        }
        if isinstance(details, Mapping) and details.get("mismatch_kind"):
            kinds.add(str(details["mismatch_kind"]))
        if kinds:
            axes["epoch"] = _check(PASSED)
            table_prefixes = ("TABLE", "COLUMN", "ROW", "CONTENT")
            table_kinds = {kind for kind in kinds if kind.startswith(table_prefixes)}
            stage_kinds = kinds - table_kinds
            axes["table_rows_hash"] = (
                _check(FAILED, ",".join(sorted(table_kinds)))
                if table_kinds
                else _check(PASSED)
            )
            axes["stage"] = (
                _check(FAILED, ",".join(sorted(stage_kinds)))
                if stage_kinds
                else _check(PASSED)
            )
            return axes
        return {name: _check(FAILED, reason) for name in axes}
    return {name: _check(PASSED) for name in axes}


def _verify_role_and_rag(
    database: str,
    target: db_target.BootstrapTarget,
    rag_marker: Mapping[str, Any],
    *,
    engine_factory: Callable[[db_target.BootstrapTarget], Engine],
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = _role_contract(database, checkpoint=database != "kosa_text2sql")
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            # 모든 live catalog/RAG 조회 transaction의 첫 문장이다.
            connection.exec_driver_sql(bootstrap_verifier.READ_ONLY_TRANSACTION_SQL)
            connection.exec_driver_sql("SET LOCAL search_path = public")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            snapshot = VERIFY_REGISTRY["role_snapshot"](connection)
            inspection = VERIFY_REGISTRY["role_inspection"](snapshot, contract)
            permission = (
                _check(PASSED)
                if inspection.state == "READY"
                else _check(FAILED, "ROLE_MATRIX_DRIFT")
            )
            try:
                VERIFY_REGISTRY["rag_live"](connection, rag_marker)
                rag_live = _check(PASSED)
            except Exception as exc:
                rag_live = _check(FAILED, _reason(exc))
            return permission, rag_live
    except Exception as exc:
        status = UNVERIFIABLE if _is_unverifiable(exc) else FAILED
        reason = _reason(exc)
        return _check(status, reason), _check(status, reason)
    finally:
        engine.dispose()


def _overall_status(checks: Mapping[str, Mapping[str, Any]]) -> str:
    statuses = {str(value["status"]) for value in checks.values()}
    if FAILED in statuses:
        return FAILED
    if UNVERIFIABLE in statuses or NOT_RUN in statuses:
        return UNVERIFIABLE
    return PASSED


def verify_target(
    database: str,
    *,
    environ: Mapping[str, str],
    marker_root: Path = MARKER_ROOT,
    full_verifier: Callable[..., Any] | None = None,
    engine_factory: Callable[[db_target.BootstrapTarget], Engine] = _engine_for,
) -> TargetReport:
    if database not in TARGETS:
        raise PublicProfileError("TARGET_NOT_ALLOWED", exit_code=EXIT_USAGE)
    assert_verify_registry_safe()
    checks = _verify_full_database(
        database,
        environ=environ,
        full_verifier=full_verifier or VERIFY_REGISTRY["full_database"],
    )
    try:
        target = db_target.load_bootstrap_target(database, environ=environ)
    except db_target.TargetValidationError as exc:
        reason = _reason(exc)
        checks["permissions"] = _check(UNVERIFIABLE, reason)
        checks["markers"] = _check(UNVERIFIABLE, reason)
    else:
        try:
            rag_marker = validate_marker_matrix(
                database, target, marker_root=marker_root
            )
            checks["markers"] = _check(PASSED)
        except Exception as exc:
            checks["markers"] = _check(FAILED, _reason(exc))
            rag_marker = None
        if rag_marker is None:
            checks["permissions"], _ = _verify_role_and_rag(
                database,
                target,
                {},
                engine_factory=engine_factory,
            )
        else:
            checks["permissions"], rag_live = _verify_role_and_rag(
                database,
                target,
                rag_marker,
                engine_factory=engine_factory,
            )
            if rag_live["status"] != PASSED:
                checks["markers"] = rag_live
    return TargetReport(
        database=database,
        profile=db_target.DATABASE_PROFILE[database],
        expected_stage=EXPECTED_STAGE[database],
        status=_overall_status(checks),
        checks=checks,
    )


def _report_payload(reports: Sequence[TargetReport]) -> dict[str, Any]:
    statuses = {report.status for report in reports}
    overall = (
        FAILED
        if FAILED in statuses
        else (UNVERIFIABLE if UNVERIFIABLE in statuses else PASSED)
    )
    payload = {
        "artifact_type": "public_profile_verification_report",
        "format_version": 1,
        "task_id": TASK_ID,
        "dataset_epoch": DATASET_EPOCH,
        "status": overall,
        "verified_at": datetime.now(UTC).isoformat(),
        "targets": [report.as_dict() for report in reports],
    }
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def verify_profiles(
    databases: Sequence[str],
    *,
    environ: Mapping[str, str],
    report_path: Path,
    marker_root: Path = MARKER_ROOT,
) -> tuple[dict[str, Any], int]:
    reports: list[TargetReport] = []
    for database in databases:
        try:
            reports.append(
                verify_target(database, environ=environ, marker_root=marker_root)
            )
        except Exception as exc:
            status = UNVERIFIABLE if _is_unverifiable(exc) else FAILED
            reports.append(
                TargetReport(
                    database=database,
                    profile=db_target.DATABASE_PROFILE.get(database, "unknown"),
                    expected_stage=EXPECTED_STAGE.get(database, "unknown"),
                    status=status,
                    checks={
                        "orchestration": _check(status, _reason(exc)),
                    },
                )
            )
    payload = _report_payload(reports)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_v3.atomic_save_json(report_path, payload)
    exit_code = (
        EXIT_OK
        if payload["status"] == PASSED
        else EXIT_UNVERIFIABLE
        if payload["status"] == UNVERIFIABLE
        else EXIT_MISMATCH
    )
    return payload, exit_code


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _closure_matches(
    closure: Mapping[str, Any],
    *,
    change_ref: str,
    approval_sha256: str,
    root_trust: str,
    digests: Mapping[str, Mapping[str, str]],
) -> bool:
    if (
        closure.get("change_ref") != change_ref
        or closure.get("approval_sha256") != approval_sha256
        or closure.get("backup_root_mode") != root_trust
    ):
        return False
    mapping = {
        "archive_sha256_by_target": "archive",
        "view_sidecar_sha256_by_target": "view_sidecar",
        "receipt_sha256_by_target": "receipt",
        "completion_sha256_by_target": "completion",
        "committed_marker_sha256_by_target": "committed_marker",
    }
    return all(
        closure.get(key)
        == {database: digests[database][artifact] for database in TARGETS}
        for key, artifact in mapping.items()
    )


def _load_promotion_bundle(
    evidence_root: Path,
    approval_path: Path,
    change_ref: str,
) -> dict[str, bytes]:
    if not postgres_transition.CHANGE_REF.fullmatch(change_ref):
        raise PublicProfileError("CHANGE_REF_INVALID", exit_code=EXIT_USAGE)
    if evidence_root.is_symlink():
        raise PublicProfileError("EVIDENCE_ROOT_INVALID", exit_code=EXIT_USAGE)
    try:
        root = postgres_backup.validate_backup_root(
            evidence_root, repository_root=REPOSITORY_ROOT
        ).resolve(strict=True)
    except Exception as exc:
        raise PublicProfileError("EVIDENCE_ROOT_INVALID", exit_code=EXIT_USAGE) from exc
    if (
        approval_path.is_symlink()
        or approval_path.name != f"approval.{change_ref}.json"
    ):
        raise PublicProfileError("APPROVAL_IDENTITY_MISMATCH", exit_code=EXIT_USAGE)
    try:
        approval_resolved = approval_path.resolve(strict=True)
    except OSError as exc:
        raise PublicProfileError("APPROVAL_INVALID", exit_code=EXIT_USAGE) from exc
    if approval_resolved.parent != root:
        raise PublicProfileError("APPROVAL_IDENTITY_MISMATCH", exit_code=EXIT_USAGE)
    approval_candidates = tuple(root.glob("approval.*.json"))
    if (
        len(approval_candidates) != 1
        or approval_candidates[0].is_symlink()
        or approval_candidates[0].resolve(strict=True) != approval_resolved
    ):
        raise PublicProfileError("PROMOTION_BUNDLE_AMBIGUOUS", exit_code=EXIT_USAGE)
    approval, approval_sha256 = public_transition.read_approval(approval_resolved)
    if approval.get("change_ref") != change_ref:
        raise PublicProfileError("APPROVAL_IDENTITY_MISMATCH", exit_code=EXIT_USAGE)
    root_trust, rejection = postgres_backup.backup_root_trust(
        root, change_ref=change_ref
    )
    if rejection is not None:
        raise PublicProfileError(rejection[0], exit_code=rejection[1])
    try:
        digests = public_transition.collect_closure_digests(
            root,
            change_ref,
            approval=approval,
            root_trust=root_trust,
            approval_sha256=approval_sha256,
        )
        closure_path = root / postgres_transition.closure_name(change_ref)
        closure = _read_marker(closure_path)
        postgres_transition.validate_closure_schema(closure)
    except Exception as exc:
        raise PublicProfileError("CLOSURE_BLOCKED") from exc
    if not _closure_matches(
        closure,
        change_ref=change_ref,
        approval_sha256=approval_sha256,
        root_trust=root_trust,
        digests=digests,
    ):
        raise PublicProfileError("CLOSURE_BLOCKED")

    bundle: dict[str, bytes] = {}
    common_ref: set[str] = set()
    for database in TARGETS:
        path = root / postgres_transition.marker_name(database)
        if path.is_symlink() or not path.is_file():
            raise PublicProfileError("PROFILE_MARKER_INVALID")
        raw = path.read_bytes()
        if _sha256(raw) != closure["committed_marker_sha256_by_target"][database]:
            raise PublicProfileError("PROFILE_MARKER_INVALID")
        try:
            payload = json.loads(raw.decode("utf-8"))
            postgres_transition.validate_marker_schema(payload)
        except Exception as exc:
            raise PublicProfileError("PROFILE_MARKER_INVALID") from exc
        if (
            payload.get("database") != database
            or payload.get("profile") != db_target.DATABASE_PROFILE[database]
            or payload.get("approval_sha256") != approval_sha256
            or payload.get("preflight_bundle_sha256")
            != approval.get("preflight_bundle_sha256")
        ):
            raise PublicProfileError("PROFILE_MARKER_INVALID")
        common_ref.add(str(payload.get("change_ref")))
        manifest_v3.scan_for_sensitive_values(payload)
        bundle[path.name] = raw
    if common_ref != {change_ref}:
        raise PublicProfileError("PROFILE_MARKER_IDENTITY_SPLIT")
    return bundle


def _promote_bytes(bundle: Mapping[str, bytes], marker_root: Path) -> tuple[int, int]:
    marker_root.mkdir(parents=True, exist_ok=True)
    destinations = {name: marker_root / name for name in bundle}
    unchanged: set[str] = set()
    for name, path in destinations.items():
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PublicProfileError("PROMOTION_DESTINATION_INVALID")
        if path.is_file():
            if path.read_bytes() != bundle[name]:
                raise PublicProfileError("PROMOTION_DESTINATION_MISMATCH")
            unchanged.add(name)

    lock_path = marker_root / ".profile-marker-promotion.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PublicProfileError("PROMOTION_LOCKED") from exc
    staged: dict[str, Path] = {}
    created: list[Path] = []
    try:
        for name, raw in bundle.items():
            if name in unchanged:
                continue
            temporary = marker_root / f".{name}.{uuid.uuid4().hex}.tmp"
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            staged[name] = temporary
        for name, temporary in staged.items():
            destination = destinations[name]
            temporary.replace(destination)
            created.append(destination)
        return len(created), len(unchanged)
    except OSError as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise PublicProfileError("PROMOTION_WRITE_FAILED") from exc
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


def promote_profile_markers(
    *,
    evidence_root: Path,
    approval_path: Path,
    change_ref: str,
    marker_root: Path = MARKER_ROOT,
) -> dict[str, Any]:
    """DB 연결 없이 검증된 CM-2.6 profile marker를 byte-identical 승격한다."""

    bundle = _load_promotion_bundle(evidence_root, approval_path, change_ref)
    promoted, unchanged = _promote_bytes(bundle, marker_root)
    payload = {
        "artifact_type": "postgres_profile_marker_promotion",
        "format_version": 1,
        "task_id": TASK_ID,
        "dataset_epoch": DATASET_EPOCH,
        "change_ref": change_ref,
        "status": "PROMOTED" if promoted else "UNCHANGED",
        "promoted_count": promoted,
        "unchanged_count": unchanged,
    }
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promote-profile-markers", action="store_true")
    parser.add_argument("--database", action="append", choices=TARGETS)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--change-ref")
    return parser


def _validate_cli(args: argparse.Namespace) -> None:
    promotion_values = (args.evidence_root, args.approval, args.change_ref)
    if args.promote_profile_markers:
        if any(value is None for value in promotion_values):
            raise PublicProfileError(
                "PROMOTION_ARGUMENT_REQUIRED", exit_code=EXIT_USAGE
            )
        if args.database or args.report:
            raise PublicProfileError("MODE_CONFLICT", exit_code=EXIT_USAGE)
    elif any(value is not None for value in promotion_values):
        raise PublicProfileError("MODE_CONFLICT", exit_code=EXIT_USAGE)
    if args.database and len(args.database) != len(set(args.database)):
        raise PublicProfileError("DUPLICATE_TARGET", exit_code=EXIT_USAGE)


def _default_report_path() -> Path:
    return REPORT_ROOT / datetime.now(UTC).strftime(
        "public-profiles-%Y%m%dT%H%M%SZ.json"
    )


def _print(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    try:
        args = _parser().parse_args(argv)
        _validate_cli(args)
        if args.promote_profile_markers:
            payload = promote_profile_markers(
                evidence_root=args.evidence_root,
                approval_path=args.approval,
                change_ref=args.change_ref,
            )
            _print(payload)
            return EXIT_OK
        payload, exit_code = verify_profiles(
            tuple(args.database or TARGETS),
            environ=dict(os.environ),
            report_path=args.report or _default_report_path(),
        )
        _print(payload)
        return exit_code
    except PublicProfileError as exc:
        _print(
            {
                "artifact_type": "public_profile_command_result",
                "format_version": 1,
                "status": exc.status,
                "reason_code": exc.reason_code,
            }
        )
        return exc.exit_code
    except Exception:
        _print(
            {
                "artifact_type": "public_profile_command_result",
                "format_version": 1,
                "status": UNVERIFIABLE,
                "reason_code": "INTERNAL_ERROR",
            }
        )
        return EXIT_UNVERIFIABLE


if __name__ == "__main__":
    raise SystemExit(main())
