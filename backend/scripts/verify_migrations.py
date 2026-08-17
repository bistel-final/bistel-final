"""Verify the V4-CM-2.1 reference-extension migration without mutation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from apply_reference_extensions import (
    MARKER_ROOT,
    ReferenceExtensionError,
    ReferenceStateError,
    _engine_for,
    _migration_sha256,
    _prepare_transaction,
    action_history_count,
    inspect_database,
    load_and_validate_sql,
    load_marker,
    postcheck_database,
    public_privilege_violations,
)
from db_target import (
    ALLOWED_DATABASES,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
)
from dotenv import load_dotenv
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def verify_database(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> dict[str, Any]:
    sql, _ = load_and_validate_sql()
    migration_sha = _migration_sha256(sql)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            inspection = inspect_database(connection)
            if inspection.state != "PRESENT":
                raise ReferenceStateError(
                    "001 reference schema 상태가 PRESENT가 아닙니다: "
                    f"{inspection.state}",
                    reason_code={
                        "ABSENT": "MIGRATION_NOT_APPLIED",
                        "MISSING_BASE": "MISSING_BASE_SCHEMA",
                        "DRIFT": "SCHEMA_DRIFT",
                    }.get(inspection.state, "SCHEMA_STATE_INVALID"),
                )
            marker = load_marker(
                target, migration_sha256=migration_sha, root=marker_root
            )
            if marker is None:
                raise ReferenceStateError(
                    "001 success marker가 없습니다",
                    reason_code="MISSING_SUCCESS_MARKER",
                )
            current_action_rows = action_history_count(connection)
            postcheck = postcheck_database(
                connection, action_rows_before=current_action_rows
            )
            if marker["schema_signature_sha256"] != postcheck.schema_signature_sha256:
                raise ReferenceStateError(
                    "marker와 현재 schema signature가 다릅니다",
                    reason_code="SCHEMA_SIGNATURE_MISMATCH",
                )
            if marker["vector_extension_version"] != postcheck.vector_extension_version:
                raise ReferenceStateError(
                    "marker와 vector extension version이 다릅니다",
                    reason_code="VECTOR_VERSION_MISMATCH",
                )
            violations = public_privilege_violations(connection)
            if violations:
                rendered = ", ".join(
                    f"{object_name}:{privilege}"
                    for object_name, privilege in violations
                )
                raise ReferenceStateError(
                    f"PUBLIC 권한이 남아 있습니다: {rendered}",
                    reason_code="PUBLIC_PRIVILEGE_DETECTED",
                )
            return {
                "database": target.database,
                "profile": target.profile,
                "schema_signature_sha256": postcheck.schema_signature_sha256,
                "vector_extension_version": postcheck.vector_extension_version,
                "action_history_rows": current_action_rows,
                "alarm_event_rows": postcheck.alarm_event_rows,
                "role_matrix": "NOT_READY(V4-CM-2.6)",
            }
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    target.add_argument("--all", action="store_true")
    return parser


def _target_failure_reason(exc: TargetValidationError) -> str:
    """Map target validation failures without echoing target or credential values."""

    message = str(exc)
    if "설정이 비어" in message:
        return "MISSING_CONFIGURATION"
    if "public schema" in message or "search_path" in message:
        return "NO_SCHEMA_USAGE_OR_CREATE"
    if any(
        token in message
        for token in ("allowlist", "fingerprint", "database", "URL target", "로컬 기본")
    ):
        return "TARGET_IDENTITY_MISMATCH"
    return "TARGET_VALIDATION_FAILED"


def _failure_reason(exc: ReferenceExtensionError | TargetValidationError) -> str:
    if isinstance(exc, TargetValidationError):
        return _target_failure_reason(exc)
    return exc.reason_code


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    databases = sorted(ALLOWED_DATABASES) if args.all else [args.database]
    failed = False
    for database in databases:
        try:
            target = load_bootstrap_target(database)
            result = verify_database(target)
            print(
                "MIGRATION_OK "
                f"database={result['database']} "
                f"action_rows={result['action_history_rows']} "
                f"alarm_rows={result['alarm_event_rows']} "
                "role_matrix=NOT_READY(V4-CM-2.6)"
            )
        except (ReferenceExtensionError, TargetValidationError) as exc:
            reason = _failure_reason(exc)
            print(
                f"MIGRATION_FAIL database={database} reason={reason}",
                file=sys.stderr,
            )
            failed = True
        except SQLAlchemyError:
            print(
                f"MIGRATION_FAIL database={database} reason=CONNECT_OR_QUERY_FAILED",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
