"""`V5-C-5.3` batch plan·CLI 단위 회귀."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import batch  # noqa: E402
from app.agent.batch import (  # noqa: E402
    ALARM_OCCURRED_AT_MISSING,
    RESOLVER_REJECTED,
    PendingBatchPlan,
    PendingIncident,
    RejectedIncident,
)
from app.agent.batch_repository import (  # noqa: E402
    BatchObservation,
    PendingIncidentMemberRow,
)
from app.common.enums import AlarmSource, RunStatus  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from scripts import run_pending_incidents as runner  # noqa: E402

T0 = datetime(2026, 8, 1, 10, 0, 0)
EMPTY = BatchObservation(frozenset(), frozenset(), frozenset())


def _row(
    lot: str | None,
    chamber: str | None,
    alarm_id: str,
    *,
    source: AlarmSource = AlarmSource.TRACE,
    occurred_at: datetime | None = T0,
    raw_lot: str | None = None,
    raw_chamber: str | None = None,
    pending: bool = True,
) -> PendingIncidentMemberRow:
    return PendingIncidentMemberRow(
        alarm=AlarmRef(source=source, alarm_id=alarm_id),
        occurred_at=occurred_at,
        lot_hist_id=None if lot is None else f"LH-{alarm_id}",
        raw_lot_id=lot if raw_lot is None else raw_lot,
        raw_chamber_id=chamber if raw_chamber is None else raw_chamber,
        canonical_lot_id=lot,
        canonical_chamber_id=chamber,
        is_pending=pending,
    )


def _candidate(
    lot: str = "LOT001",
    chamber: str = "CH01",
    alarm_id: str = "TA-01",
) -> PendingIncident:
    return PendingIncident(
        lot_id=lot,
        chamber_id=chamber,
        member_count=1,
        representative=AlarmRef(source=AlarmSource.TRACE, alarm_id=alarm_id),
    )


def _plan(*items: PendingIncident) -> PendingBatchPlan:
    return PendingBatchPlan(tuple(items), (), 0)


class _Connection:
    def __init__(self, *, database: str = "kosa_agent_e2e", role: str = "kosa_app"):
        self.database = database
        self.role = role

    def execute(self, statement: Any, params: Any = None) -> Any:
        del statement, params
        return SimpleNamespace(
            one=lambda: SimpleNamespace(
                database_name=self.database,
                role_name=self.role,
            )
        )


class _Engine:
    def __init__(self, connection: _Connection | None = None) -> None:
        self.connection = connection or _Connection()

    @contextmanager
    def connect(self) -> Any:
        yield self.connection


class _Runtime:
    def __init__(self, statuses: list[RunStatus] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.starts: list[str] = []
        self.continues: list[tuple[str, str]] = []
        self.close_calls = 0

    def start_run(self, alarm: AlarmRef) -> Any:
        run_id = f"RUN-{len(self.starts) + 1}"
        self.starts.append(alarm.alarm_id)
        return SimpleNamespace(
            agent_run_id=run_id,
            thread_id=f"THREAD-{run_id}",
        )

    def continue_run(self, thread_id: str, run_id: str) -> None:
        self.continues.append((thread_id, run_id))

    def close(self) -> None:
        self.close_calls += 1


def _json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line]


def test_plan_uses_the_shared_sort_key_and_stable_incident_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        _row("LOT-B", "CH02", "TA-LATE", occurred_at=T0 + timedelta(hours=1)),
        _row(
            "LOT-A",
            "CH01",
            "SA-OLD",
            source=AlarmSource.SUMMARY,
            occurred_at=T0,
        ),
        _row(
            "LOT-A",
            "CH01",
            "RA-SAME-TIME",
            source=AlarmSource.R03,
            occurred_at=T0,
        ),
    )
    monkeypatch.setattr(batch, "fetch_pending_incident_rows", lambda _connection: rows)

    result = batch.build_pending_batch_plan(SimpleNamespace())

    assert [(item.lot_id, item.chamber_id) for item in result.selected] == [
        ("LOT-A", "CH01"),
        ("LOT-B", "CH02"),
    ]
    assert result.selected[0].representative == AlarmRef(
        source=AlarmSource.R03,
        alarm_id="RA-SAME-TIME",
    )
    assert result.selected[0].member_count == 2


def test_plan_separates_missing_time_resolver_rejection_and_null_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        _row("LOT-A", "CH01", "TA-NULL", occurred_at=None),
        _row(
            "LOT-B",
            "CH02",
            "TA-DRIFT",
            raw_chamber="CH99",
        ),
        _row(None, None, "TA-ORPHAN", raw_lot="LOT-C", raw_chamber="CH03"),
    )
    monkeypatch.setattr(batch, "fetch_pending_incident_rows", lambda _connection: rows)

    result = batch.build_pending_batch_plan(SimpleNamespace())

    assert result.selected == ()
    assert {(item.lot_id, item.reason) for item in result.rejected} == {
        ("LOT-A", ALARM_OCCURRED_AT_MISSING),
        ("LOT-B", RESOLVER_REJECTED),
    }
    assert result.canonical_null_rows == 1


def test_duplicate_alarm_identity_is_never_picked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        _row("LOT-A", "CH01", "TA-DUP"),
        _row("LOT-A", "CH01", "TA-DUP"),
    )
    monkeypatch.setattr(batch, "fetch_pending_incident_rows", lambda _connection: rows)
    result = batch.build_pending_batch_plan(SimpleNamespace())
    assert result.selected == ()
    assert result.rejected == (RejectedIncident("LOT-A", "CH01", RESOLVER_REJECTED),)


def test_alarm_identity_in_a_nonpending_group_rejects_the_pending_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        _row("LOT-A", "CH01", "TA-DUP", pending=True),
        _row("LOT-B", "CH02", "TA-DUP", pending=False),
    )
    monkeypatch.setattr(batch, "fetch_pending_incident_rows", lambda _connection: rows)
    result = batch.build_pending_batch_plan(SimpleNamespace())
    assert result.selected == ()
    assert result.rejected == (RejectedIncident("LOT-A", "CH01", RESOLVER_REJECTED),)


def test_repository_query_reuses_resolved_projection_and_excludes_any_history() -> None:
    from app.agent import batch_repository, incident_repository

    # 이 문자열 검사는 shared SQL seam의 보조 경보다. FAILED run만 있고 action link가
    # 없는 실제 DB 상태는 container의 의미 기반 음성 회귀가 정본으로 고정한다.
    pending_sql = str(batch_repository._PENDING_MEMBERS)
    snapshot_sql = str(incident_repository._SNAPSHOT)
    shared = incident_repository.RESOLVED_ALARM_SELECT_SQL.strip()
    assert shared in pending_sql
    assert shared in snapshot_sql
    assert "NOT EXISTS" in pending_sql
    assert "existing.lot_id = r.canonical_lot_id" in pending_sql
    assert "existing.status" not in pending_sql


def test_observation_delta_excludes_every_preexisting_identity() -> None:
    before = BatchObservation(
        run_ids=frozenset({"RUN-OLD"}),
        created_action_ids=frozenset({"ACT-OLD"}),
        delivery_keys=frozenset({("ACT-OLD", "EMAIL")}),
    )
    after = BatchObservation(
        run_ids=frozenset({"RUN-OLD", "RUN-NEW"}),
        created_action_ids=frozenset({"ACT-OLD", "ACT-NEW"}),
        delivery_keys=frozenset(
            {
                ("ACT-OLD", "EMAIL"),
                ("ACT-NEW", "MES_MOCK"),
            }
        ),
    )

    assert runner._observation_delta(before, after) == BatchObservation(
        run_ids=frozenset({"RUN-NEW"}),
        created_action_ids=frozenset({"ACT-NEW"}),
        delivery_keys=frozenset({("ACT-NEW", "MES_MOCK")}),
    )


def test_runtime_target_path_never_imports_the_bootstrap_owner_helpers() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "db_target" not in source
    assert "load_bootstrap_target" not in source
    assert "validate_connected_identity" not in source
    assert "current_user AS role_name" in source
    assert 'row.role_name == "kosa_app"' in source


def test_dry_run_emits_exact_plan_and_never_builds_runtime(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = PendingBatchPlan(
        selected=(_candidate(),),
        rejected=(RejectedIncident("LOT-X", "CH-X", RESOLVER_REJECTED),),
        canonical_null_rows=2,
    )
    monkeypatch.setattr(runner, "build_pending_batch_plan", lambda _connection: plan)

    def forbidden_runtime() -> Any:
        pytest.fail("dry-run must not create runtime")

    code = runner.main(
        ["--database", "kosa_agent_e2e"],
        engine_factory=lambda: _Engine(),
        runtime_factory=forbidden_runtime,
    )
    captured = capsys.readouterr()
    assert code == runner.EXIT_OK
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "plan",
            "database": "kosa_agent_e2e",
            "selected": [
                {
                    "lot_id": "LOT001",
                    "chamber_id": "CH01",
                    "member_count": 1,
                    "representative": {"source": "TRACE", "alarm_id": "TA-01"},
                }
            ],
            "rejected": [
                {
                    "lot_id": "LOT-X",
                    "chamber_id": "CH-X",
                    "reason": RESOLVER_REJECTED,
                }
            ],
            "excluded": {"canonical_null_rows": 2},
        }
    ]


@pytest.mark.parametrize(
    ("argv", "code", "reason"),
    [
        ([], runner.EXIT_USAGE, "USAGE_INVALID"),
        (
            ["--database", "kosa_agent", "--once"],
            runner.EXIT_TARGET,
            "CONFIRM_REQUIRED",
        ),
        (
            [
                "--database",
                "kosa_agent",
                "--once",
                "--confirm-database",
                "kosa_agent_e2e",
            ],
            runner.EXIT_TARGET,
            "CONFIRM_REQUIRED",
        ),
    ],
)
def test_usage_and_confirmation_fail_without_connecting(
    argv: list[str],
    code: int,
    reason: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_engine() -> Any:
        pytest.fail("invalid command must not connect")

    assert runner.main(argv, engine_factory=forbidden_engine) == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{reason}\n"


def test_target_identity_requires_both_database_and_kosa_app(
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _Engine(_Connection(role="postgres"))
    assert (
        runner.main(
            ["--database", "kosa_agent_e2e"],
            engine_factory=lambda: engine,
        )
        == runner.EXIT_TARGET
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "TARGET_MISMATCH\n"


def test_once_rechecks_status_continues_after_failed_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    incidents = (
        _candidate("LOT-1", "CH-1", "TA-1"),
        _candidate("LOT-2", "CH-2", "TA-2"),
        _candidate("LOT-3", "CH-3", "TA-3"),
    )
    plan = _plan(*incidents)
    runtime = _Runtime()
    statuses = {
        "RUN-1": RunStatus.COMPLETED,
        "RUN-2": RunStatus.FAILED,
        "RUN-3": RunStatus.WAITING_APPROVAL,
    }
    observations: dict[str, int] = {}

    def observe(_engine: Any, incident: PendingIncident) -> BatchObservation:
        call = observations.get(incident.lot_id, 0)
        observations[incident.lot_id] = call + 1
        if call == 0:
            return EMPTY
        index = incident.lot_id[-1]
        return BatchObservation(
            frozenset({f"RUN-{index}"}),
            frozenset({f"ACT-{index}"}),
            frozenset({(f"ACT-{index}", "EMAIL")}),
        )

    monkeypatch.setattr(runner, "build_pending_batch_plan", lambda _connection: plan)
    monkeypatch.setattr(runner, "_observe", observe)
    monkeypatch.setattr(runner, "_status", lambda _engine, run_id: statuses[run_id])

    code = runner.main(
        ["--database", "kosa_agent_e2e", "--once"],
        engine_factory=lambda: _Engine(),
        runtime_factory=lambda: runtime,
    )
    output = _json_lines(capsys.readouterr().out)

    assert code == runner.EXIT_FAILED
    assert runtime.starts == ["TA-1", "TA-2", "TA-3"]
    assert len(runtime.continues) == 3
    assert runtime.close_calls == 1
    assert [item["outcome"] for item in output[:-1]] == [
        "STARTED_COMPLETED",
        "FAILED",
        "STARTED_WAITING_APPROVAL",
    ]
    assert output[-1] == {
        "type": "final",
        "attempted": 3,
        "succeeded": 2,
        "failed": 1,
        "skipped": 0,
        "new_runs_observed": 3,
        "new_actions_observed": 3,
        "new_deliveries_observed": 3,
    }


def test_start_exception_still_observes_the_compensated_failed_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    incident = _candidate()

    class _CompensatingRuntime(_Runtime):
        def start_run(self, alarm: AlarmRef) -> Any:
            self.starts.append(alarm.alarm_id)
            raise RuntimeError("provider-sentinel")

    runtime = _CompensatingRuntime()
    observations = iter(
        (
            EMPTY,
            BatchObservation(
                frozenset({"RUN-FAILED"}),
                frozenset(),
                frozenset(),
            ),
        )
    )
    monkeypatch.setattr(
        runner,
        "build_pending_batch_plan",
        lambda _connection: _plan(incident),
    )
    monkeypatch.setattr(runner, "_observe", lambda *_args: next(observations))
    monkeypatch.setattr(
        runner,
        "_status",
        lambda _engine, run_id: (
            RunStatus.FAILED
            if run_id == "RUN-FAILED"
            else pytest.fail("unexpected run")
        ),
    )
    code = runner.main(
        ["--database", "kosa_agent_e2e", "--once"],
        engine_factory=lambda: _Engine(),
        runtime_factory=lambda: runtime,
    )
    output = _json_lines(capsys.readouterr().out)
    assert code == runner.EXIT_FAILED
    assert runtime.close_calls == 1
    assert output[0]["outcome"] == "FAILED"
    assert output[0]["agent_run_id"] == "RUN-FAILED"
    assert output[-1]["attempted"] == 1
    assert output[-1]["new_runs_observed"] == 1


def test_race_is_skipped_without_runtime_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        runner,
        "build_pending_batch_plan",
        lambda _connection: _plan(_candidate()),
    )
    monkeypatch.setattr(
        runner,
        "_observe",
        lambda _engine, _incident: BatchObservation(
            frozenset({"RUN-OTHER"}), frozenset(), frozenset()
        ),
    )
    code = runner.main(
        ["--database", "kosa_agent_e2e", "--once"],
        engine_factory=lambda: _Engine(),
        runtime_factory=lambda: runtime,
    )
    output = _json_lines(capsys.readouterr().out)
    assert code == runner.EXIT_OK
    assert runtime.starts == []
    assert runtime.close_calls == 1
    assert output[0]["outcome"] == "SKIPPED_RACE"
    assert output[-1]["skipped"] == 1
    assert output[-1]["attempted"] == 0


def test_rejected_incident_is_failed_without_runtime_start(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        runner,
        "build_pending_batch_plan",
        lambda _connection: PendingBatchPlan(
            (),
            (RejectedIncident("LOT-X", "CH-X", ALARM_OCCURRED_AT_MISSING),),
            0,
        ),
    )
    code = runner.main(
        ["--database", "kosa_agent_e2e", "--once"],
        engine_factory=lambda: _Engine(),
        runtime_factory=lambda: runtime,
    )
    output = _json_lines(capsys.readouterr().out)
    assert code == runner.EXIT_FAILED
    assert runtime.starts == []
    assert runtime.close_calls == 1
    assert output[0]["outcome"] == ALARM_OCCURRED_AT_MISSING
    assert output[-1]["failed"] == 1
    assert output[-1]["attempted"] == 0


def test_unexpected_failures_and_secrets_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "postgresql://kosa_app:sentinel-password@sentinel-host/db"

    def broken_engine() -> Any:
        raise RuntimeError(secret)

    code = runner.main(
        ["--database", "kosa_agent_e2e"],
        engine_factory=broken_engine,
    )
    captured = capsys.readouterr()
    assert code == runner.EXIT_FAILED
    assert captured.out == ""
    assert captured.err == "BATCH_COMMAND_FAILED\n"
    assert secret not in captured.err

    monkeypatch.setattr(
        runner,
        "build_pending_batch_plan",
        lambda _connection: PendingBatchPlan((), (), 0),
    )

    def broken_runtime() -> Any:
        raise RuntimeError(secret)

    code = runner.main(
        ["--database", "kosa_agent_e2e", "--once"],
        engine_factory=lambda: _Engine(),
        runtime_factory=broken_runtime,
    )
    captured = capsys.readouterr()
    assert code == runner.EXIT_FAILED
    assert captured.out == ""
    assert captured.err == "BATCH_COMMAND_FAILED\n"
    assert secret not in captured.err


def test_runtime_is_closed_when_command_boundary_raises(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        runner,
        "build_pending_batch_plan",
        lambda _connection: PendingBatchPlan((), (), 0),
    )
    monkeypatch.setattr(
        runner,
        "_run_once",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("sentinel-secret")),
    )
    code = runner.main(
        ["--database", "kosa_agent_e2e", "--once"],
        engine_factory=lambda: _Engine(),
        runtime_factory=lambda: runtime,
    )
    captured = capsys.readouterr()
    assert code == runner.EXIT_FAILED
    assert runtime.close_calls == 1
    assert captured.out == ""
    assert captured.err == "BATCH_COMMAND_FAILED\n"
