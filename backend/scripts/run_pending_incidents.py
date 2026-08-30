"""Runtime 이력이 없는 incident를 한 번씩 실행하는 확인형 관리 명령."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn, Protocol

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.batch import (  # noqa: E402
    PendingBatchPlan,
    PendingIncident,
    build_pending_batch_plan,
)
from app.agent.batch_repository import (  # noqa: E402
    BatchObservation,
    fetch_incident_observation,
)
from app.agent.repository import get_agent_run  # noqa: E402
from app.common.enums import RunStatus  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_TARGET = 3
ALLOWED_DATABASES = ("kosa_agent", "kosa_agent_e2e")


class RuntimePort(Protocol):
    def start_run(self, alarm: AlarmRef) -> Any: ...

    def continue_run(self, thread_id: str, run_id: str) -> None: ...

    def close(self) -> None: ...


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _UsageError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__, add_help=False)
    parser.add_argument("--database", required=True, choices=ALLOWED_DATABASES)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--confirm-database")
    return parser


def _stderr(reason: str) -> None:
    print(reason, file=sys.stderr)


def _emit(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _target_matches(connection: Any, database: str) -> bool:
    row = connection.execute(
        text("SELECT current_database() AS database_name, " "current_user AS role_name")
    ).one()
    return row.database_name == database and row.role_name == "kosa_app"


def _plan_payload(plan: PendingBatchPlan, database: str) -> dict[str, Any]:
    return {
        "type": "plan",
        "database": database,
        "selected": [
            {
                "lot_id": item.lot_id,
                "chamber_id": item.chamber_id,
                "member_count": item.member_count,
                "representative": {
                    "source": item.representative.source.value,
                    "alarm_id": item.representative.alarm_id,
                },
            }
            for item in plan.selected
        ],
        "rejected": [
            {
                "lot_id": item.lot_id,
                "chamber_id": item.chamber_id,
                "reason": item.reason,
            }
            for item in plan.rejected
        ],
        "excluded": {"canonical_null_rows": plan.canonical_null_rows},
    }


def _incident_payload(
    incident: PendingIncident,
    outcome: str,
    agent_run_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "incident",
        "lot_id": incident.lot_id,
        "chamber_id": incident.chamber_id,
        "outcome": outcome,
    }
    if agent_run_id is not None:
        payload["agent_run_id"] = agent_run_id
    return payload


def _observe(engine: Any, incident: PendingIncident) -> BatchObservation:
    with engine.connect() as connection:
        return fetch_incident_observation(
            connection,
            lot_id=incident.lot_id,
            chamber_id=incident.chamber_id,
        )


def _status(engine: Any, run_id: str) -> RunStatus:
    with engine.connect() as connection:
        return get_agent_run(connection, run_id).status


def _outcome_for_status(status: RunStatus) -> tuple[str, bool]:
    if status is RunStatus.COMPLETED:
        return "STARTED_COMPLETED", True
    if status is RunStatus.WAITING_APPROVAL:
        return "STARTED_WAITING_APPROVAL", True
    if status is RunStatus.FAILED:
        return "FAILED", False
    return "CONTRACT_FAILURE", False


def _new_failed_run(
    engine: Any,
    before: BatchObservation,
    after: BatchObservation,
) -> str | None:
    new_ids = sorted(after.run_ids - before.run_ids)
    if len(new_ids) != 1:
        return None
    try:
        return new_ids[0] if _status(engine, new_ids[0]) is RunStatus.FAILED else None
    except Exception:  # noqa: BLE001 - best-effort 판정에도 원문을 출력하지 않는다.
        return None


def _observation_delta(
    before: BatchObservation,
    after: BatchObservation,
) -> BatchObservation:
    """incident 실행 전부터 있던 identity를 신규 관측치에서 제외한다."""

    return BatchObservation(
        run_ids=after.run_ids - before.run_ids,
        created_action_ids=after.created_action_ids - before.created_action_ids,
        delivery_keys=after.delivery_keys - before.delivery_keys,
    )


def _run_once(engine: Any, runtime: RuntimePort, plan: PendingBatchPlan) -> int:
    attempted = succeeded = failed = skipped = 0
    observed_runs: set[str] = set()
    observed_actions: set[str] = set()
    observed_deliveries: set[tuple[str, str]] = set()

    # resolver 단계 실패는 runtime을 부르지 않지만 final failed에는 포함한다.
    for rejected in plan.rejected:
        failed += 1
        _emit(
            {
                "type": "incident",
                "lot_id": rejected.lot_id,
                "chamber_id": rejected.chamber_id,
                "outcome": rejected.reason,
            }
        )

    for incident in plan.selected:
        try:
            before = _observe(engine, incident)
        except Exception:  # noqa: BLE001 - driver/config detail은 출력하지 않는다.
            failed += 1
            _emit(_incident_payload(incident, "CONTRACT_FAILURE"))
            continue
        if before.run_ids:
            skipped += 1
            _emit(_incident_payload(incident, "SKIPPED_RACE"))
            continue

        attempted += 1
        started_id: str | None = None
        outcome = "CONTRACT_FAILURE"
        success = False
        try:
            started = runtime.start_run(incident.representative)
            started_id = started.agent_run_id
            runtime.continue_run(started.thread_id, started.agent_run_id)
            outcome, success = _outcome_for_status(
                _status(engine, started.agent_run_id)
            )
        except Exception:  # noqa: BLE001 - incident별 격리·sanitized outcome.
            outcome = "CONTRACT_FAILURE"

        try:
            after = _observe(engine, incident)
        except Exception:  # noqa: BLE001
            after = before
            outcome = "CONTRACT_FAILURE"
            success = False

        delta = _observation_delta(before, after)
        observed_runs.update(delta.run_ids)
        observed_actions.update(delta.created_action_ids)
        observed_deliveries.update(delta.delivery_keys)

        if outcome == "CONTRACT_FAILURE":
            failed_run = _new_failed_run(engine, before, after)
            if failed_run is not None:
                outcome = "FAILED"
                started_id = started_id or failed_run

        if success:
            succeeded += 1
        else:
            failed += 1
        _emit(_incident_payload(incident, outcome, started_id))

    _emit(
        {
            "type": "final",
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "new_runs_observed": len(observed_runs),
            "new_actions_observed": len(observed_actions),
            "new_deliveries_observed": len(observed_deliveries),
        }
    )
    return EXIT_FAILED if failed else EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: Callable[[], Any] | None = None,
    runtime_factory: Callable[[], RuntimePort] | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except _UsageError:
        _stderr("USAGE_INVALID")
        return EXIT_USAGE

    if args.once and (
        (args.database == "kosa_agent" and args.confirm_database != args.database)
        or (
            args.confirm_database is not None and args.confirm_database != args.database
        )
    ):
        _stderr("CONFIRM_REQUIRED")
        return EXIT_TARGET

    try:
        if engine_factory is None:
            # usage·confirm 오류에서는 config module조차 import하지 않는다.
            from app.common.db import get_app_engine

            engine_factory = get_app_engine
        engine = engine_factory()
        with engine.connect() as connection:
            if not _target_matches(connection, args.database):
                _stderr("TARGET_MISMATCH")
                return EXIT_TARGET
            plan = build_pending_batch_plan(connection)
    except Exception:  # noqa: BLE001 - DSN·driver·SQL 원문 비출력.
        _stderr("BATCH_COMMAND_FAILED")
        return EXIT_FAILED

    if not args.once:
        _emit(_plan_payload(plan, args.database))
        return EXIT_OK

    try:
        if runtime_factory is None:
            # dry-run은 production Runtime module과 factory 모두 건드리지 않는다.
            from app.agent.runtime_composition import get_agent_runtime

            runtime_factory = get_agent_runtime
        runtime = runtime_factory()
    except Exception:  # noqa: BLE001
        _stderr("BATCH_COMMAND_FAILED")
        return EXIT_FAILED

    close_failed = False
    try:
        try:
            result = _run_once(engine, runtime, plan)
        except Exception:  # noqa: BLE001 - 최상위에서도 원문을 내보내지 않는다.
            _stderr("BATCH_COMMAND_FAILED")
            result = EXIT_FAILED
    finally:
        try:
            runtime.close()
        except Exception:  # noqa: BLE001
            close_failed = True
    if close_failed:
        _stderr("BATCH_RUNTIME_CLOSE_FAILED")
        return EXIT_FAILED
    return result


if __name__ == "__main__":
    raise SystemExit(main())
