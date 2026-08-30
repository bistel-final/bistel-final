"""``kosa_agent_e2e`` Runtime 13-table reset guard (`V5-CM-4.7`).

Public 성공은 이 파일이 아니라 ``orchestrate_e2e_reset_evidence.py``가 소유한다.
이 CLI의 apply exit 0은 target reset과 postcheck가 끝난 ``RESET_APPLIED``일 뿐,
다른 두 DB의 변경 0 증명은 아니다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apply_agent_runtime as agent_runtime
import apply_postgres_role_matrix as role_runner
import apply_severity_pair_guard as severity_guard
import checkpoint_contract
import db_target
import e2e_reset_evidence as evidence
import postgres_role_matrix as role_matrix
import verify_public_profiles
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

TARGET_DATABASE = "kosa_agent_e2e"
TARGET_PROFILE = "runtime"
CONFIRMATION = "reset-runtime kosa_agent_e2e"
TASK_ID = "V5-CM-4.7"

TARGET_TABLES: tuple[str, ...] = (
    *agent_runtime.RUNTIME_TABLES,
    "action_history",
    *checkpoint_contract.OPERATIONAL_TABLES,
)
TARGET_SEQUENCES: tuple[str, ...] = tuple(sorted(agent_runtime.EXPECTED_SEQUENCE_NAMES))

if len(TARGET_TABLES) != 13 or len(set(TARGET_TABLES)) != 13:
    raise RuntimeError("CM-4.7 reset table allowlist가 13종이 아닙니다")

TRUNCATE_SQL = (
    "TRUNCATE TABLE "
    + ", ".join(f'public."{name}"' for name in TARGET_TABLES)
    + " RESTART IDENTITY"
)
if "CASCADE" in TRUNCATE_SQL.upper():  # pragma: no cover - import-time invariant
    raise RuntimeError("CM-4.7 reset은 CASCADE를 허용하지 않습니다")

ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(54047, 20260818) AS acquired"
SESSION_COUNT_SQL = """
SELECT count(*) AS row_count
FROM pg_stat_activity
WHERE datname = current_database()
  AND backend_type = 'client backend'
  AND pid <> pg_backend_pid()
"""
ACTION_ID_PATTERN = r"^ACT-[0-9a-f]{16}$"

PROVENANCE_SQL = f"""
WITH action_ids AS (
  SELECT action_id, lot_id, chamber_id FROM public.action_history
), created AS (
  SELECT action_id, lot_id, chamber_id, count(*) OVER (PARTITION BY action_id) AS n
  FROM public.agent_run_action WHERE link_role = 'CREATED'
)
SELECT
  (SELECT count(*) FROM action_ids
   WHERE action_id !~ '{ACTION_ID_PATTERN}') AS invalid_id,
  (SELECT count(*) FROM action_ids a
   LEFT JOIN created c ON c.action_id = a.action_id
   WHERE c.action_id IS NULL OR c.n <> 1) AS missing_created,
  (SELECT count(*) FROM created c
   LEFT JOIN action_ids a ON a.action_id = c.action_id
   WHERE a.action_id IS NULL) AS orphan_created,
  (SELECT count(*) FROM action_ids a JOIN created c USING (action_id)
   WHERE a.lot_id IS DISTINCT FROM c.lot_id
      OR a.chamber_id IS DISTINCT FROM c.chamber_id) AS identity_mismatch,
  (SELECT count(*) FROM public.approval_request p
   LEFT JOIN action_ids a ON a.action_id = p.action_id
   WHERE a.action_id IS NULL) AS orphan_approval,
  (SELECT count(*) FROM public.action_delivery d
   LEFT JOIN action_ids a ON a.action_id = d.action_id
   WHERE a.action_id IS NULL) AS orphan_delivery
"""


class ResetError(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


class NoMutationBlocked(ResetError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code, 3)


class DependencyFailure(ResetError):
    def __init__(self, reason_code: str = "RESET_FAILED") -> None:
        super().__init__(reason_code, 1)


@dataclass(frozen=True)
class ResetResult:
    before_rows: Mapping[str, int]
    preserved_before_sha256: str
    preserved_after_sha256: str


@dataclass(frozen=True)
class PostResetResult:
    row_counts: Mapping[str, int]
    sequence_state: Mapping[str, Mapping[str, Any]]
    other_client_backends: int


def _rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in result.mappings().all()]
    except AttributeError:
        return [dict(row) for row in result]


def _one(result: Any) -> dict[str, Any]:
    rows = _rows(result)
    if len(rows) != 1:
        raise DependencyFailure()
    return rows[0]


def _engine_for(target: db_target.BootstrapTarget) -> Engine:
    url = target.create_url()
    db_target.validate_url_components(url, target)
    return create_engine(url, pool_pre_ping=True, future=True)


def _postcheck_engine_for(target: db_target.BootstrapTarget) -> Engine:
    """write transaction의 pooled backend와 독립인 일회성 postcheck 연결."""

    url = target.create_url()
    db_target.validate_url_components(url, target)
    return create_engine(url, poolclass=NullPool, future=True)


def _prepare_transaction(
    connection: Any,
    target: db_target.BootstrapTarget,
    *,
    readonly: bool,
) -> None:
    if target.database != TARGET_DATABASE or target.profile != TARGET_PROFILE:
        raise NoMutationBlocked("TARGET_NOT_ALLOWED")
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    if readonly:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    connection.exec_driver_sql("SET LOCAL search_path = public")
    connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
    try:
        db_target.validate_connected_identity(connection, target)
        db_target.set_and_validate_public_search_path(connection)
    except db_target.TargetValidationError as exc:
        raise NoMutationBlocked("TARGET_DB_MISMATCH") from exc


def acquire_reset_lock(connection: Any) -> None:
    row = _one(connection.exec_driver_sql(ADVISORY_LOCK_SQL))
    if row.get("acquired") is not True:
        raise NoMutationBlocked("RESET_IN_PROGRESS")


def other_client_backend_count(connection: Any) -> int:
    try:
        return int(_one(connection.exec_driver_sql(SESSION_COUNT_SQL))["row_count"])
    except ResetError:
        raise
    except Exception as exc:
        raise NoMutationBlocked("E2E_WRITER_ACTIVE") from exc


def assert_no_other_clients(connection: Any) -> None:
    if other_client_backend_count(connection) != 0:
        raise NoMutationBlocked("E2E_WRITER_ACTIVE")


def target_row_counts(connection: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TARGET_TABLES:
        row = _one(
            connection.exec_driver_sql(
                f'SELECT count(*) AS row_count FROM public."{table}"'
            )
        )
        counts[table] = int(row["row_count"])
    return counts


def target_sequence_state(connection: Any) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for sequence in TARGET_SEQUENCES:
        row = _one(
            connection.exec_driver_sql(
                f"""
                SELECT state.last_value,
                       state.is_called,
                       catalog.seqstart AS start_value
                FROM public."{sequence}" AS state
                JOIN pg_catalog.pg_class relation
                  ON relation.relname = %s
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                 AND namespace.nspname = 'public'
                JOIN pg_catalog.pg_sequence catalog
                  ON catalog.seqrelid = relation.oid
                """,
                (sequence,),
            )
        )
        state[sequence] = {
            "last_value": int(row["last_value"]),
            "is_called": row["is_called"] is True,
            "start_value": int(row["start_value"]),
        }
    return state


def is_reset_sequence_state(item: Mapping[str, Any]) -> bool:
    return (
        type(item.get("last_value")) is int
        and type(item.get("start_value")) is int
        and item.get("last_value") == item.get("start_value")
        and item.get("is_called") is False
    )


def assert_target_zero(connection: Any) -> PostResetResult:
    counts = target_row_counts(connection)
    if any(counts.values()):
        raise DependencyFailure("RESET_APPLIED_WRITER_REENTRY")
    sequences = target_sequence_state(connection)
    if any(not is_reset_sequence_state(item) for item in sequences.values()):
        raise DependencyFailure("RESET_APPLIED_WRITER_REENTRY")
    clients = other_client_backend_count(connection)
    if clients:
        raise DependencyFailure("RESET_APPLIED_WRITER_REENTRY")
    return PostResetResult(counts, sequences, clients)


def assert_action_provenance(connection: Any) -> None:
    try:
        row = _one(connection.exec_driver_sql(PROVENANCE_SQL))
    except ResetError:
        raise
    except Exception as exc:
        raise NoMutationBlocked("ACTION_PROVENANCE_MISMATCH") from exc
    if any(int(value) != 0 for value in row.values()):
        raise NoMutationBlocked("ACTION_PROVENANCE_MISMATCH")


def _checkpoint_catalog(connection: Any) -> dict[str, Any]:
    tables = list(checkpoint_contract.CHECKPOINT_TABLES)
    indexes = list(checkpoint_contract.CHECKPOINT_INDEXES)
    expected_owner = str(
        _one(connection.exec_driver_sql("SELECT current_user AS value"))["value"]
    )
    return checkpoint_contract.inspect_catalog(
        {
            "columns": _rows(
                connection.exec_driver_sql(checkpoint_contract.CATALOG_SQL, (tables,))
            ),
            "indexes": _rows(
                connection.exec_driver_sql(checkpoint_contract.INDEX_SQL, (indexes,))
            ),
            "primary_keys": _rows(
                connection.exec_driver_sql(
                    checkpoint_contract.PRIMARY_KEY_SQL, (tables,)
                )
            ),
            "versions": _rows(
                connection.exec_driver_sql(
                    "SELECT v FROM public.checkpoint_migrations ORDER BY v"
                )
            ),
            "acl": _rows(
                connection.exec_driver_sql(
                    checkpoint_contract.CHECKPOINT_ACL_SQL, (tables,)
                )
            ),
            "expected_owner": expected_owner,
        }
    )


def assert_steady_state(connection: Any) -> None:
    """final marker와 Runtime/severity/checkpoint/role owner를 조립한다."""

    try:
        inspection = agent_runtime.inspect_database(
            connection, expected_constraints=severity_guard.GUARDED_CONSTRAINTS
        )
        guard = severity_guard.inspect_guard(connection)
        if inspection.state != "PRESENT" or guard.state != "GUARDED_UNMARKED":
            raise NoMutationBlocked("TARGET_STATE_MISMATCH")
        catalog = _checkpoint_catalog(connection)
        checkpoint_contract.assert_ready(catalog)
        checkpoint_contract.assert_checkpoint_acl(catalog)
        contract = role_matrix.build_contract(
            TARGET_DATABASE,
            role_matrix.MatrixStage.CHECKPOINTED,
            role_matrix.SchemaStage.RUNTIME_CHECKPOINTED,
        )
        role_inspection = role_runner.inspect_snapshot(
            role_runner.read_snapshot(connection), contract
        )
        if role_inspection.state != "READY":
            raise NoMutationBlocked("TARGET_STATE_MISMATCH")
    except ResetError:
        raise
    except (
        agent_runtime.AgentRuntimeError,
        severity_guard.SeverityGuardError,
        checkpoint_contract.CheckpointContractError,
        checkpoint_contract.CheckpointStateError,
        role_matrix.ContractError,
        role_runner.RoleMatrixError,
    ) as exc:
        raise NoMutationBlocked("TARGET_STATE_MISMATCH") from exc
    except Exception as exc:
        raise DependencyFailure("RESET_FAILED") from exc


def preserved_snapshot(connection: Any) -> dict[str, Any]:
    return evidence.snapshot_database_fingerprint(
        connection,
        mutable_tables=TARGET_TABLES,
        mutable_sequences=TARGET_SEQUENCES,
    )


def reset_runtime_data(
    connection: Any,
    *,
    preflight: Callable[[Any], None] = assert_steady_state,
    snapshotter: Callable[[Any], Mapping[str, Any]] = preserved_snapshot,
    fault_hook: Callable[[str], None] | None = None,
) -> ResetResult:
    """열린 write transaction 안에서만 13종을 truncate한다.

    commit은 호출자가 소유한다. 그래야 commit 성공 **뒤** applied receipt를 쓰는
    상태 전이를 한 곳에서 강제할 수 있다.
    """

    acquire_reset_lock(connection)
    assert_no_other_clients(connection)
    if fault_hook:
        fault_hook("after_session_scan")
    preflight(connection)
    assert_action_provenance(connection)
    before_rows = target_row_counts(connection)
    before = dict(snapshotter(connection))
    try:
        connection.exec_driver_sql(TRUNCATE_SQL)
        zero = target_row_counts(connection)
        sequences = target_sequence_state(connection)
    except ResetError:
        raise
    except Exception as exc:
        raise DependencyFailure("RESET_FAILED") from exc
    if any(zero.values()) or any(
        not is_reset_sequence_state(item) for item in sequences.values()
    ):
        raise DependencyFailure("RESET_FAILED")
    after = dict(snapshotter(connection))
    if before.get("sha256") != after.get("sha256"):
        raise NoMutationBlocked("PRESERVED_STATE_CHANGED")
    if fault_hook:
        fault_hook("before_commit")
    return ResetResult(
        before_rows=before_rows,
        preserved_before_sha256=evidence.assert_sha256(before.get("sha256")),
        preserved_after_sha256=evidence.assert_sha256(after.get("sha256")),
    )


def validate_marker_preflight(
    target: db_target.BootstrapTarget,
    *,
    marker_root: Path = verify_public_profiles.MARKER_ROOT,
) -> None:
    try:
        verify_public_profiles.validate_marker_matrix(
            TARGET_DATABASE, target, marker_root=marker_root
        )
    except Exception as exc:
        raise NoMutationBlocked("TARGET_STATE_MISMATCH") from exc


def _pre_receipt(
    path: Path,
    *,
    run_id: str,
    target: db_target.BootstrapTarget,
) -> tuple[dict[str, Any], str]:
    try:
        payload, digest = evidence.load_receipt(
            path, artifact_type="e2e_reset_pre", run_id=run_id
        )
    except evidence.EvidenceError as exc:
        raise NoMutationBlocked("PRE_RECEIPT_INVALID") from exc
    if payload.get("target_database") != TARGET_DATABASE or payload.get(
        "target_host_fingerprint_sha256"
    ) != db_target.host_fingerprint(target.host, target.port):
        raise NoMutationBlocked("PRE_RECEIPT_INVALID")
    return payload, digest


def apply_reset(
    target: db_target.BootstrapTarget,
    *,
    run_id: str,
    pre_receipt_path: Path,
    report_root: Path = evidence.DEFAULT_REPORT_ROOT,
    engine_factory: Callable[[db_target.BootstrapTarget], Engine] = _engine_for,
    postcheck_engine_factory: Callable[
        [db_target.BootstrapTarget], Engine
    ] = _postcheck_engine_for,
    marker_root: Path = verify_public_profiles.MARKER_ROOT,
    fault_hook: Callable[[str], None] | None = None,
    preflight: Callable[[Any], None] = assert_steady_state,
    snapshotter: Callable[[Any], Mapping[str, Any]] = preserved_snapshot,
) -> tuple[ResetResult, PostResetResult]:
    """commit→applied receipt→별도 postcheck 순서를 고정한다."""

    validate_marker_preflight(target, marker_root=marker_root)
    _pre, pre_sha = _pre_receipt(pre_receipt_path, run_id=run_id, target=target)
    engine = engine_factory(target)
    reset_result: ResetResult
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _prepare_transaction(connection, target, readonly=False)
                reset_result = reset_runtime_data(
                    connection,
                    preflight=preflight,
                    snapshotter=snapshotter,
                    fault_hook=fault_hook,
                )
                transaction.commit()
            except BaseException:
                if transaction.is_active:
                    transaction.rollback()
                raise
        if fault_hook:
            fault_hook("after_commit_before_receipt")

        applied = evidence.base_receipt("e2e_reset_applied", run_id)
        applied.update(
            {
                "status": "RESET_APPLIED",
                "database": TARGET_DATABASE,
                "pre_receipt_sha256": pre_sha,
                "preserved_before_sha256": reset_result.preserved_before_sha256,
                "preserved_after_sha256": reset_result.preserved_after_sha256,
                "tables": sorted(TARGET_TABLES),
            }
        )
        try:
            evidence.write_atomic_receipt(
                evidence.receipt_path(report_root, run_id, "applied"), applied
            )
        except evidence.EvidenceError as exc:
            raise DependencyFailure("RESET_APPLIED_EVIDENCE_BLOCKED") from exc
        if fault_hook:
            fault_hook("after_applied_receipt")

        # write Engine의 pool에 남은 idle backend가 postcheck에서 "다른 client"로
        # 오인되지 않게 먼저 닫고, NullPool 일회성 연결에서 검증한다.
        engine.dispose()
        postcheck_engine = postcheck_engine_factory(target)
        try:
            with postcheck_engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=True)
                post = assert_target_zero(connection)
        finally:
            postcheck_engine.dispose()
        post_receipt = evidence.base_receipt("e2e_reset_post", run_id)
        post_receipt.update(
            {
                "status": "POSTCHECK_PASSED",
                "database": TARGET_DATABASE,
                "pre_receipt_sha256": pre_sha,
                "row_counts": dict(post.row_counts),
                "sequence_state": dict(post.sequence_state),
                "other_client_backends": post.other_client_backends,
            }
        )
        try:
            evidence.write_atomic_receipt(
                evidence.receipt_path(report_root, run_id, "post"), post_receipt
            )
        except evidence.EvidenceError as exc:
            raise DependencyFailure("RESET_APPLIED_EVIDENCE_BLOCKED") from exc
        return reset_result, post
    finally:
        engine.dispose()


def dry_run(
    target: db_target.BootstrapTarget,
    *,
    engine_factory: Callable[[db_target.BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = verify_public_profiles.MARKER_ROOT,
    preflight: Callable[[Any], None] = assert_steady_state,
) -> Mapping[str, int]:
    validate_marker_preflight(target, marker_root=marker_root)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            acquire_reset_lock(connection)
            assert_no_other_clients(connection)
            preflight(connection)
            assert_action_provenance(connection)
            return target_row_counts(connection)
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5-CM-4.7 E2E Runtime reset")
    parser.add_argument("--target", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--run-id")
    parser.add_argument("--pre-receipt", type=Path)
    parser.add_argument(
        "--report-root", type=Path, default=evidence.DEFAULT_REPORT_ROOT
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.target != TARGET_DATABASE:
        raise ResetError("TARGET_NOT_ALLOWED", 2)
    if args.yes:
        if args.confirm != CONFIRMATION:
            raise NoMutationBlocked("CONFIRMATION_MISMATCH")
        if (
            not isinstance(args.run_id, str)
            or not evidence.RUN_ID_RE.fullmatch(args.run_id)
            or args.pre_receipt is None
        ):
            raise ResetError("ARG_INVALID", 2)
    elif any((args.confirm, args.run_id, args.pre_receipt)):
        raise ResetError("ARG_INVALID", 2)


def _emit(status: str, reason: str, *, tables: Mapping[str, int] | None = None) -> None:
    print(
        json.dumps(
            {"status": status, "reason": reason, "tables": dict(tables or {})},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _validate_args(args)
        load_dotenv()
        target = db_target.load_bootstrap_target(TARGET_DATABASE)
        if not args.yes:
            counts = dry_run(target)
            _emit("DRY_RUN", "DRY_RUN_READY", tables=counts)
            return 0
        result, _post = apply_reset(
            target,
            run_id=args.run_id,
            pre_receipt_path=args.pre_receipt,
            report_root=args.report_root,
        )
        _emit("APPLIED", "RESET_APPLIED", tables=result.before_rows)
        return 0
    except ResetError as exc:
        _emit("BLOCKED", exc.reason_code)
        return exc.exit_code
    except (db_target.TargetValidationError, evidence.EvidenceError):
        _emit("BLOCKED", "RESET_FAILED")
        return 1
    except Exception:
        _emit("BLOCKED", "RESET_FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
