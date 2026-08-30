"""V5-C-6.1 12·5/4/3 snapshot의 격리 PostgreSQL 회귀."""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.golden_flow import (  # noqa: E402
    DATASET_EPOCH,
    ArtifactKind,
    EvidenceArtifact,
    EvidenceBundle,
    ExecutionScope,
    GateKind,
    GoldenPhase,
    PhaseStatus,
    evaluate_golden_flow,
    load_expected_oracle,
)
from app.agent.golden_flow_repository import (  # noqa: E402
    read_golden_flow_snapshot,
)
from scripts import run_pending_incidents as runner  # noqa: E402
from tests.unit import test_agent_graph_container as graph_fixture  # noqa: E402
from tests.unit.test_run_pending_incidents_container import (  # noqa: E402
    _grant_app_role,
)

pytestmark = pytest.mark.container

ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload["r03_incidents"] = [
        {"lot_id": lot_id, "chamber_id": chamber_id}
        for lot_id, chamber_id in snapshot.r03_incidents
    ]
    return json.loads(json.dumps(payload))


def _seed_batch_inputs(engine: Any) -> dict[tuple[str, str], tuple[int, Any]]:
    oracle = load_expected_oracle(json.loads(ORACLE_PATH.read_text()))
    by_alarm: dict[tuple[str, str], tuple[int, Any]] = {}
    with engine.begin() as connection:
        for table in (
            "agent_run",
            "action_history",
            "audit_log",
            "r03_alarm_history",
            "trace_alarm_history",
            "summary_alarm_history",
            "lot_history",
        ):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))
        connection.execute(
            text(
                "INSERT INTO dim_parameter (parameter_id, parameter_name) "
                "VALUES ('PARAM01', 'Fixture') ON CONFLICT (parameter_id) DO NOTHING"
            )
        )
        for index, incident in enumerate(oracle.incidents, start=1):
            lot_hist_id = f"GLH-{index:04d}"
            wafer_id = f"GW-{index:04d}"
            equipment_id = incident.chamber_id.split("-", 1)[0]
            connection.execute(
                text(
                    "INSERT INTO lot_history "
                    "(lot_hist_id, lot_id, wafer_no, wafer_id, equipment_id, "
                    " chamber_id, chamber_wafer_cum) VALUES "
                    "(:lh, :lot, 1, :wafer, :equipment, :chamber, :cum)"
                ),
                {
                    "lh": lot_hist_id,
                    "lot": incident.lot_id,
                    "wafer": wafer_id,
                    "equipment": equipment_id,
                    "chamber": incident.chamber_id,
                    "cum": index,
                },
            )
            if incident.expected_action == "MONITORING":
                source = "SUMMARY"
                alarm_id = f"SAL-GOLD-{index:04d}"
                connection.execute(
                    text(
                        "INSERT INTO summary_alarm_history "
                        "(alarm_id, occurred_at, area, equipment, chamber, parameter, "
                        " recipe, lot, wafer, step_no, statistic_type, stat_value, "
                        " cl, ucl, lcl, limit_type) VALUES "
                        "(:alarm, clock_timestamp(), 'etch', :equipment, :chamber, "
                        " 'PARAM01', 'RECIPE01', :lot, :wafer, 1, 'mean', 11, "
                        " 10, 10.5, 9.5, 'UCL')"
                    ),
                    {
                        "alarm": alarm_id,
                        "equipment": equipment_id,
                        "chamber": incident.chamber_id,
                        "lot": incident.lot_id,
                        "wafer": wafer_id,
                    },
                )
            elif incident.expected_action == "WARNING":
                source = "TRACE"
                alarm_id = f"TAL-GOLD-{index:04d}"
                connection.execute(
                    text(
                        "INSERT INTO trace_alarm_history "
                        "(alarm_id, occurred_at, area, equipment, chamber, parameter, "
                        " recipe, lot, wafer, step_no, seq_no, value, limit_type, "
                        " limit_value) VALUES "
                        "(:alarm, clock_timestamp(), 'etch', :equipment, :chamber, "
                        " 'PARAM01', 'RECIPE01', :lot, :wafer, 1, 1, 13, 'USL', 12)"
                    ),
                    {
                        "alarm": alarm_id,
                        "equipment": equipment_id,
                        "chamber": incident.chamber_id,
                        "lot": incident.lot_id,
                        "wafer": wafer_id,
                    },
                )
            else:
                source = "R03"
                alarm_id = f"R03-{index:020x}"
                connection.execute(
                    text(
                        "INSERT INTO r03_alarm_history "
                        "(alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id, "
                        " chamber_id, parameter_id, recipe_step_no, trigger_wafer_no, "
                        " member_wafer_refs, member_alarm_refs, policy_version) VALUES "
                        "(:alarm, clock_timestamp(), :lh, :lot, :equipment, :chamber, "
                        " 'PARAM01', 1, 1, CAST(:wafers AS jsonb), '[]'::jsonb, "
                        " 'R03_CONSEC_V1')"
                    ),
                    {
                        "alarm": alarm_id,
                        "lh": lot_hist_id,
                        "lot": incident.lot_id,
                        "equipment": equipment_id,
                        "chamber": incident.chamber_id,
                        "wafers": json.dumps(
                            [{"lot_hist_id": lot_hist_id, "wafer_id": wafer_id}] * 3
                        ),
                    },
                )
            by_alarm[(source, alarm_id)] = (index, incident)
    return by_alarm


class _BatchFixtureRuntime:
    """C-5.3 orchestration 뒤 실제 link·delivery DML을 남기는 결정론적 fake."""

    def __init__(
        self,
        engine: Any,
        by_alarm: dict[tuple[str, str], tuple[int, Any]],
    ) -> None:
        self._engine = engine
        self._by_alarm = by_alarm
        self.closed = False

    def start_run(self, alarm: Any) -> Any:
        index, incident = self._by_alarm[(alarm.source.value, alarm.alarm_id)]
        run_id = f"RUN-{index:016x}"
        action_id = f"ACT-GOLD-{index:04d}"
        status = (
            "WAITING_APPROVAL"
            if incident.expected_action == "EQP_HOLD"
            else "COMPLETED"
        )
        severity = {
            "MONITORING": "LOW",
            "WARNING": "MEDIUM",
            "EQP_HOLD": "HIGH",
        }[incident.expected_action]
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_run "
                    "(agent_run_id, thread_id, lot_id, chamber_id, "
                    " requested_alarm_source, requested_alarm_id, "
                    " representative_alarm_source, representative_alarm_id, status, "
                    " autonomy_level, action, severity, latency_ms) VALUES "
                    "(:run, :thread, :lot, :chamber, :source, :alarm, :source, :alarm, "
                    " :status, 2, :action, :severity, 10)"
                ),
                {
                    "run": run_id,
                    "thread": f"THREAD-GOLD-{index:04d}",
                    "lot": incident.lot_id,
                    "chamber": incident.chamber_id,
                    "source": alarm.source.value,
                    "alarm": alarm.alarm_id,
                    "status": status,
                    "action": incident.expected_action,
                    "severity": severity,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO agent_run_alarm "
                    "(agent_run_id, alarm_source, alarm_id, is_representative) "
                    "VALUES (:run, :source, :alarm, true)"
                ),
                {
                    "run": run_id,
                    "source": alarm.source.value,
                    "alarm": alarm.alarm_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO action_history "
                    "(action_id, lot_id, chamber_id, action_code, reason, "
                    " approval_required, approval_status, created_at) VALUES "
                    "(:action_id, :lot, :chamber, :action, 'batch fixture', "
                    " :required, :approval, clock_timestamp())"
                ),
                {
                    "action_id": action_id,
                    "lot": incident.lot_id,
                    "chamber": incident.chamber_id,
                    "action": incident.expected_action,
                    "required": "Y" if incident.expected_action == "EQP_HOLD" else "N",
                    "approval": (
                        "PENDING" if incident.expected_action == "EQP_HOLD" else "AUTO"
                    ),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO agent_run_action "
                    "(agent_run_id, action_id, link_role, lot_id, chamber_id, "
                    " trigger_alarm_source, trigger_alarm_id) VALUES "
                    "(:run, :action_id, 'CREATED', :lot, :chamber, :source, :alarm)"
                ),
                {
                    "run": run_id,
                    "action_id": action_id,
                    "lot": incident.lot_id,
                    "chamber": incident.chamber_id,
                    "source": alarm.source.value,
                    "alarm": alarm.alarm_id,
                },
            )
            if incident.expected_action == "EQP_HOLD":
                connection.execute(
                    text(
                        "INSERT INTO approval_request "
                        "(approval_id, action_id, agent_run_id, status) "
                        "VALUES (:approval, :action_id, :run, 'PENDING')"
                    ),
                    {
                        "approval": f"APR-{index:016x}",
                        "action_id": action_id,
                        "run": run_id,
                    },
                )
            channels = {
                "MONITORING": (),
                "WARNING": ("EMAIL",),
                "EQP_HOLD": ("EMAIL", "MES_MOCK"),
            }[incident.expected_action]
            for channel in channels:
                connection.execute(
                    text(
                        "INSERT INTO action_delivery "
                        "(action_id, channel, status, request_hash, attempt_count) "
                        "VALUES (:action_id, :channel, :status, :hash, 0)"
                    ),
                    {
                        "action_id": action_id,
                        "channel": channel,
                        "status": "BLOCKED" if channel == "MES_MOCK" else "WAITING",
                        "hash": f"{index:064x}",
                    },
                )
        return SimpleNamespace(
            agent_run_id=run_id,
            thread_id=f"THREAD-GOLD-{index:04d}",
        )

    def continue_run(self, _thread_id: str, _run_id: str) -> None:
        return None

    def fail_registered_run(self, _run_id: str) -> None:
        raise AssertionError("successful fixture run must not be compensated")

    def close(self) -> None:
        self.closed = True


def _seed_golden_snapshot(engine: Any) -> None:
    oracle = load_expected_oracle(json.loads(ORACLE_PATH.read_text()))
    with engine.begin() as connection:
        for table in ("agent_run", "action_history", "audit_log", "r03_alarm_history"):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))
        connection.execute(
            text(
                "INSERT INTO dim_parameter (parameter_id, parameter_name) "
                "VALUES ('PARAM01', 'Fixture') ON CONFLICT (parameter_id) DO NOTHING"
            )
        )
        for index, incident in enumerate(oracle.incidents, start=1):
            run_id = f"RUN-{index:016x}"
            action_id = f"ACT-GOLD-{index:04d}"
            lot_hist_id = f"GLH-{index:04d}"
            connection.execute(
                text(
                    "INSERT INTO lot_history "
                    "(lot_hist_id, lot_id, wafer_no, wafer_id, equipment_id, "
                    " chamber_id, chamber_wafer_cum) VALUES "
                    "(:lh, :lot, 1, :wafer, 'EQP01', :chamber, :cum) "
                    "ON CONFLICT (lot_hist_id) DO NOTHING"
                ),
                {
                    "lh": lot_hist_id,
                    "lot": incident.lot_id,
                    "wafer": f"{incident.lot_id}W001",
                    "chamber": incident.chamber_id,
                    "cum": index,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO agent_run "
                    "(agent_run_id, thread_id, lot_id, chamber_id, "
                    " requested_alarm_source, requested_alarm_id, "
                    " representative_alarm_source, representative_alarm_id, "
                    " status, autonomy_level, action, severity, latency_ms) "
                    "VALUES (:run, :thread, :lot, :chamber, 'SUMMARY', :alarm, "
                    " 'SUMMARY', :alarm, :status, 2, :action, :severity, 10)"
                ),
                {
                    "run": run_id,
                    "thread": f"THREAD-{index}",
                    "lot": incident.lot_id,
                    "chamber": incident.chamber_id,
                    "alarm": f"SAL-GOLD-{index:04d}",
                    "status": (
                        "WAITING_APPROVAL"
                        if incident.expected_action == "EQP_HOLD"
                        else "COMPLETED"
                    ),
                    "action": incident.expected_action,
                    "severity": {
                        "MONITORING": "LOW",
                        "WARNING": "MEDIUM",
                        "EQP_HOLD": "HIGH",
                    }[incident.expected_action],
                },
            )
            connection.execute(
                text(
                    "INSERT INTO action_history "
                    "(action_id, lot_id, chamber_id, action_code, reason, "
                    " approval_required, approval_status, created_at) "
                    "VALUES (:action_id, :lot, :chamber, :action, 'fixture', "
                    " :required, :approval, clock_timestamp())"
                ),
                {
                    "action_id": action_id,
                    "lot": incident.lot_id,
                    "chamber": incident.chamber_id,
                    "action": incident.expected_action,
                    "required": (
                        "Y" if incident.expected_action == "EQP_HOLD" else "N"
                    ),
                    "approval": (
                        "PENDING" if incident.expected_action == "EQP_HOLD" else "AUTO"
                    ),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO agent_run_action "
                    "(agent_run_id, action_id, link_role, lot_id, chamber_id, "
                    " trigger_alarm_source, trigger_alarm_id) "
                    "VALUES (:run, :action_id, 'CREATED', :lot, :chamber, "
                    " 'SUMMARY', :alarm)"
                ),
                {
                    "run": run_id,
                    "action_id": action_id,
                    "lot": incident.lot_id,
                    "chamber": incident.chamber_id,
                    "alarm": f"SAL-GOLD-{index:04d}",
                },
            )
            channels = {
                "MONITORING": (),
                "WARNING": ("EMAIL",),
                "EQP_HOLD": ("EMAIL", "MES_MOCK"),
            }[incident.expected_action]
            for channel in channels:
                connection.execute(
                    text(
                        "INSERT INTO action_delivery "
                        "(action_id, channel, status, request_hash, attempt_count) "
                        "VALUES (:action_id, :channel, :status, :hash, 0)"
                    ),
                    {
                        "action_id": action_id,
                        "channel": channel,
                        "status": "BLOCKED" if channel == "MES_MOCK" else "WAITING",
                        "hash": f"{index:064x}",
                    },
                )
            if "R03" in incident.alarm_sources:
                connection.execute(
                    text(
                        "INSERT INTO r03_alarm_history VALUES "
                        "(:alarm, clock_timestamp(), :lh, :lot, 'EQP01', :chamber, "
                        " 'PARAM01', 1, 1, CAST(:wafers AS jsonb), "
                        " CAST(:alarms AS jsonb), 'R03_CONSEC_V1')"
                    ),
                    {
                        "alarm": f"R03-{index:020x}",
                        "lh": lot_hist_id,
                        "lot": incident.lot_id,
                        "chamber": incident.chamber_id,
                        "wafers": json.dumps(
                            [{"lot_hist_id": lot_hist_id, "wafer_id": "W"}] * 3
                        ),
                        "alarms": "[]",
                    },
                )


def test_readonly_repeatable_snapshot_reads_exact_golden_distribution() -> None:
    with graph_fixture._runtime_context() as (_endpoint, super_engine):
        _seed_golden_snapshot(super_engine)
        app_engine = _grant_app_role(super_engine)
        try:
            with app_engine.connect() as connection:
                connection.exec_driver_sql(
                    "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                settings = connection.execute(
                    text(
                        "SELECT current_setting('transaction_isolation') AS isolation, "
                        "current_setting('transaction_read_only') AS read_only"
                    )
                ).one()
                assert (settings.isolation, settings.read_only) == (
                    "repeatable read",
                    "on",
                )
            snapshot = read_golden_flow_snapshot(app_engine, database="kosa_agent_e2e")
        finally:
            app_engine.dispose()

    created = [item for item in snapshot.actions if item.link_role == "CREATED"]
    assert len(snapshot.runs) == 12
    assert Counter(item.action_code for item in created) == Counter(
        {"MONITORING": 5, "WARNING": 4, "EQP_HOLD": 3}
    )
    assert Counter(item.channel for item in snapshot.deliveries) == Counter(
        {"EMAIL": 7, "MES_MOCK": 3}
    )
    assert len(snapshot.r03_incidents) == 3


def test_batch_output_round_trips_into_verifier_and_detects_delivery_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    oracle = load_expected_oracle(json.loads(ORACLE_PATH.read_text()))
    phase = GoldenPhase.BATCH_BASELINE

    with graph_fixture._runtime_context() as (_endpoint, super_engine):
        by_alarm = _seed_batch_inputs(super_engine)
        app_engine = _grant_app_role(super_engine)
        runtime = _BatchFixtureRuntime(app_engine, by_alarm)
        try:
            exit_code = runner.main(
                ["--database", graph_fixture.TARGET_DATABASE, "--once"],
                engine_factory=lambda: app_engine,
                runtime_factory=lambda: runtime,
            )
            batch_lines = [
                json.loads(line)
                for line in capsys.readouterr().out.splitlines()
                if line
            ]
            snapshot = read_golden_flow_snapshot(
                app_engine,
                database="kosa_agent_e2e",
            )
            snapshot_payload = _snapshot_payload(snapshot)
            evidence = EvidenceBundle(
                dataset_epoch=DATASET_EPOCH,
                gate_kind=GateKind.PUBLIC_GOLDEN_FLOW,
                level_rounds=(2,),
                scopes={phase: ExecutionScope.PUBLIC_E2E},
                artifacts=(
                    EvidenceArtifact(
                        "batch-db",
                        ArtifactKind.DB_SNAPSHOT,
                        phase,
                        2,
                        snapshot_payload,
                    ),
                    EvidenceArtifact(
                        "batch-output",
                        ArtifactKind.BATCH_NDJSON,
                        phase,
                        2,
                        batch_lines,
                    ),
                    EvidenceArtifact(
                        "batch-timing",
                        ArtifactKind.HTTP_RESULTS,
                        phase,
                        2,
                        {
                            "format_version": 1,
                            "results": [{"case": "BATCH_WALL_CLOCK", "duration_ms": 1}],
                        },
                    ),
                ),
            )
            passed = evaluate_golden_flow(
                snapshot,
                evidence,
                oracle,
                phase=phase,
            )

            with app_engine.begin() as connection:
                deleted = connection.execute(
                    text(
                        "DELETE FROM action_delivery "
                        "WHERE (action_id, channel) = ("
                        "  SELECT action_id, channel FROM action_delivery "
                        "  ORDER BY action_id, channel LIMIT 1"
                        ") RETURNING action_id"
                    )
                ).one()
                assert deleted.action_id
            drifted_snapshot = read_golden_flow_snapshot(
                app_engine,
                database="kosa_agent_e2e",
            )
            drifted_evidence = EvidenceBundle(
                dataset_epoch=DATASET_EPOCH,
                gate_kind=GateKind.PUBLIC_GOLDEN_FLOW,
                level_rounds=(2,),
                scopes={phase: ExecutionScope.PUBLIC_E2E},
                artifacts=(
                    EvidenceArtifact(
                        "drifted-db",
                        ArtifactKind.DB_SNAPSHOT,
                        phase,
                        2,
                        _snapshot_payload(drifted_snapshot),
                    ),
                    *evidence.artifacts[1:],
                ),
            )
            failed = evaluate_golden_flow(
                drifted_snapshot,
                drifted_evidence,
                oracle,
                phase=phase,
            )
        finally:
            app_engine.dispose()

    assert exit_code == runner.EXIT_OK
    assert runtime.closed is True
    assert batch_lines[-1] == {
        "type": "final",
        "attempted": 12,
        "succeeded": 12,
        "failed": 0,
        "skipped": 0,
        "new_runs_observed": 12,
        "new_actions_observed": 12,
        "new_deliveries_observed": 10,
    }
    assert passed.status is PhaseStatus.PASS
    assert failed.status is PhaseStatus.FAIL
    assert "ROUND_2_BASELINE_DELIVERY_PLAN_NOT_EXACT" in failed.phases[0].reasons
