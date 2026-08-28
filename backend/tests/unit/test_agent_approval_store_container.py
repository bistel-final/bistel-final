"""`V5-C-3.3` 승인 원자성·동시성 PostgreSQL 16 회귀."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import rehearsal_postgres as postgres  # noqa: E402

from app.agent import action_store, approval_store  # noqa: E402
from app.agent import repository as repo  # noqa: E402
from app.agent.incident import ResolvedIncident  # noqa: E402
from app.agent.rehydration import RehydrationSeed  # noqa: E402
from app.agent.routing import ResolvedIncidentRoute  # noqa: E402
from app.agent.state import ActionDecision, ToolBudget  # noqa: E402
from app.common.audit import AuditContractError  # noqa: E402
from app.common.enums import (  # noqa: E402
    ActionCode,
    AlarmSource,
    Decision,
    DeliveryStatus,
    RunStatus,
    Severity,
)
from app.common.schemas import AlarmRef  # noqa: E402

pytestmark = pytest.mark.container

REPOSITORY_ROOT = BACKEND_ROOT.parent
TARGET_DATABASE = "kosa_agent_e2e"
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"
ALARM = AlarmRef(source=AlarmSource.R03, alarm_id="R03-HITL")


@pytest.fixture(scope="module")
def engine() -> Any:
    with postgres.one_off_postgres(database=TARGET_DATABASE) as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=TARGET_DATABASE,
            user=endpoint.username,
            password=endpoint.password,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(
                (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
            )
            cursor.execute(SQL_002.read_text(encoding="utf-8"))
            cursor.execute(SQL_003.read_text(encoding="utf-8"))
            raw.commit()
        value = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}"
        )
        try:
            yield value
        finally:
            value.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Any) -> None:
    with engine.begin() as connection:
        for table in ("agent_run", "action_history", "audit_log"):
            connection.execute(text(f"TRUNCATE {table} CASCADE"))


def _hold() -> ActionDecision:
    return ActionDecision(
        action=ActionCode.EQP_HOLD,
        severity=Severity.HIGH,
        requires_approval=True,
        matched_rule="R03_PRESENT",
    )


def _seed(lot_id: str, chamber_id: str) -> RehydrationSeed:
    return RehydrationSeed(
        route=ResolvedIncidentRoute(
            incident=ResolvedIncident(
                lot_id=lot_id,
                chamber_id=chamber_id,
                requested_alarm=ALARM,
                representative_alarm=ALARM,
                member_alarms=(ALARM,),
            ),
            wafer_routes=(),
            graph_evidence=(),
            route_consistency=True,
            mismatches=(),
        ),
        fdc_evidence=None,
        optional_anomaly_evidence=None,
        graph_evidence=None,
        document_evidence=None,
        errors=(),
        tool_budget=ToolBudget(used=0),
        fdc_lot_hist_id="LH-HITL",
    )


def _create_waiting_bundle(
    engine: Any,
    *,
    lot_id: str = "LOT-HITL",
    chamber_id: str = "EQP01-PM1",
) -> tuple[str, str, str]:
    with engine.begin() as connection:
        run = repo.create_agent_run(
            connection,
            repo.CreateAgentRunCommand(
                thread_id="11111111-2222-3333-4444-555555555555",
                lot_id=lot_id,
                chamber_id=chamber_id,
                autonomy_level=2,
                requested_alarm=ALARM,
                representative_alarm=ALARM,
                member_alarms=(ALARM,),
            ),
        )
    result = action_store.production_port(engine.begin)(
        run.agent_run_id,
        _hold(),
        _seed(lot_id, chamber_id),
    )
    assert result.approval_id is not None
    return run.agent_run_id, result.action_id, result.approval_id


def test_hold_bundle_and_waiting_status_commit_together(engine: Any) -> None:
    run_id, action_id, approval_id = _create_waiting_bundle(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT r.status, a.approval_status, p.status AS approval_status_2, "
                "d.status AS mes_status "
                "FROM agent_run r "
                "JOIN agent_run_action l ON l.agent_run_id = r.agent_run_id "
                "JOIN action_history a ON a.action_id = l.action_id "
                "JOIN approval_request p ON p.action_id = a.action_id "
                "JOIN action_delivery d ON d.action_id = a.action_id "
                "AND d.channel = 'MES_MOCK' "
                "WHERE r.agent_run_id = :run_id"
            ),
            {"run_id": run_id},
        ).one()
    assert action_id and approval_id
    assert row.status == RunStatus.WAITING_APPROVAL.value
    assert row.approval_status == "PENDING"
    assert row.approval_status_2 == "PENDING"
    assert row.mes_status == DeliveryStatus.BLOCKED.value


@pytest.mark.parametrize(
    ("decision", "approval_status", "mes_status"),
    [
        (Decision.APPROVE, "APPROVED", DeliveryStatus.WAITING.value),
        (Decision.REJECT, "REJECTED", DeliveryStatus.CANCELED.value),
    ],
)
def test_decision_updates_three_projections_and_one_audit_atomically(
    engine: Any,
    decision: Decision,
    approval_status: str,
    mes_status: str,
) -> None:
    run_id, action_id, approval_id = _create_waiting_bundle(engine)
    result = approval_store.decision_port(engine.begin)(
        approval_id,
        decision,
        "operator",
        "checked",
    )
    with engine.connect() as connection:
        approval = connection.execute(
            text("SELECT * FROM approval_request WHERE approval_id = :id"),
            {"id": approval_id},
        ).one()
        action = connection.execute(
            text("SELECT * FROM action_history WHERE action_id = :id"),
            {"id": action_id},
        ).one()
        deliveries = connection.execute(
            text(
                "SELECT channel, status FROM action_delivery "
                "WHERE action_id = :id ORDER BY channel"
            ),
            {"id": action_id},
        ).all()
        audits = connection.execute(
            text(
                "SELECT occurred_at, actor_type, actor_id, before_json, after_json "
                "FROM audit_log WHERE event_type = 'APPROVAL_DECIDED'"
            )
        ).all()

    assert result.approval.status.value == approval_status
    assert approval.status == approval_status
    assert approval.decided_by == "operator"
    assert approval.decision_comment == "checked"
    assert action.approval_status == approval_status
    assert [(row.channel, row.status) for row in deliveries] == [
        ("EMAIL", "WAITING"),
        ("MES_MOCK", mes_status),
    ]
    if decision is Decision.APPROVE:
        assert action.approved_by == "operator"
        assert action.approved_at.replace(tzinfo=UTC) == approval.decided_at
    else:
        assert action.approved_by is None and action.approved_at is None
    assert len(audits) == 1
    audit = audits[0]
    assert (audit.actor_type, audit.actor_id) == ("HUMAN", "operator")
    assert audit.before_json == {"status": "PENDING"}
    assert audit.after_json == {
        "status": approval_status,
        "approval_id": approval_id,
        "action_id": action_id,
        "agent_run_id": run_id,
        "decided_at": approval.decided_at.isoformat(),
    }
    assert audit.occurred_at >= approval.decided_at


def test_concurrent_decision_has_one_winner_and_one_conflict(engine: Any) -> None:
    _run_id, _action_id, approval_id = _create_waiting_bundle(engine)

    def decide(value: Decision) -> str:
        try:
            approval_store.decision_port(engine.begin)(
                approval_id, value, f"operator-{value.value}"
            )
        except repo.RepositoryConflict as exc:
            return exc.code
        return "DECIDED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(decide, (Decision.APPROVE, Decision.REJECT)))
    with engine.connect() as connection:
        audits = connection.execute(
            text(
                "SELECT count(*) FROM audit_log "
                "WHERE event_type = 'APPROVAL_DECIDED'"
            )
        ).scalar_one()
    assert outcomes == ["APPROVAL_NOT_PENDING", "DECIDED"]
    assert audits == 1


@pytest.mark.parametrize("run_status", [RunStatus.RUNNING, RunStatus.FAILED])
def test_decision_rejects_non_waiting_run_without_partial_writes(
    engine: Any,
    run_status: RunStatus,
) -> None:
    run_id, action_id, approval_id = _create_waiting_bundle(engine)
    with engine.begin() as connection:
        statement = text(
            "UPDATE agent_run SET status = :status, ended_at = clock_timestamp() "
            "WHERE agent_run_id = :run_id"
            if run_status is RunStatus.FAILED
            else "UPDATE agent_run SET status = :status, ended_at = NULL "
            "WHERE agent_run_id = :run_id"
        )
        connection.execute(
            statement,
            {"status": run_status.value, "run_id": run_id},
        )
        audit_before = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()

    with pytest.raises(repo.RepositoryConflict) as caught:
        approval_store.decision_port(engine.begin)(
            approval_id,
            Decision.APPROVE,
            "operator",
        )
    assert caught.value.code == "RUN_NOT_WAITING_APPROVAL"

    with engine.connect() as connection:
        approval = connection.execute(
            text(
                "SELECT status, decided_by, decided_at, decision_comment "
                "FROM approval_request WHERE approval_id = :id"
            ),
            {"id": approval_id},
        ).one()
        action = connection.execute(
            text(
                "SELECT approval_status, approved_by, approved_at "
                "FROM action_history WHERE action_id = :id"
            ),
            {"id": action_id},
        ).one()
        mes = connection.execute(
            text(
                "SELECT status FROM action_delivery "
                "WHERE action_id = :id AND channel = 'MES_MOCK'"
            ),
            {"id": action_id},
        ).scalar_one()
        audit_after = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
    assert tuple(approval) == ("PENDING", None, None, None)
    assert tuple(action) == ("PENDING", None, None)
    assert mes == "BLOCKED"
    assert audit_after == audit_before


def test_failure_after_three_business_writes_rolls_back_everything(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_id, action_id, approval_id = _create_waiting_bundle(engine)
    with engine.connect() as connection:
        audit_before = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise AuditContractError("fixture")

    monkeypatch.setattr(repo, "append_audit_log", fail_audit)
    with pytest.raises(repo.RepositoryContractError) as caught:
        approval_store.decision_port(engine.begin)(
            approval_id,
            Decision.APPROVE,
            "operator",
        )
    assert caught.value.code == "AUDIT_CONTRACT_VIOLATION"

    with engine.connect() as connection:
        approval = connection.execute(
            text("SELECT status, decided_at FROM approval_request")
        ).one()
        action = connection.execute(
            text(
                "SELECT approval_status, approved_by, approved_at "
                "FROM action_history WHERE action_id = :id"
            ),
            {"id": action_id},
        ).one()
        mes = connection.execute(
            text(
                "SELECT status FROM action_delivery "
                "WHERE action_id = :id AND channel = 'MES_MOCK'"
            ),
            {"id": action_id},
        ).scalar_one()
        audit_after = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
    assert (approval.status, approval.decided_at) == ("PENDING", None)
    assert (action.approval_status, action.approved_by, action.approved_at) == (
        "PENDING",
        None,
        None,
    )
    assert mes == "BLOCKED"
    assert audit_after == audit_before


def test_same_run_terminal_bundle_replays_but_another_run_cannot_reuse_it(
    engine: Any,
) -> None:
    run_id, action_id, approval_id = _create_waiting_bundle(engine)
    approval_store.decision_port(engine.begin)(
        approval_id,
        Decision.REJECT,
        "operator",
    )
    replay = action_store.production_port(engine.begin)(
        run_id,
        _hold(),
        _seed("LOT-HITL", "EQP01-PM1"),
    )
    assert replay.action_id == action_id

    with engine.begin() as connection:
        repo.finish_agent_run(connection, run_id, RunStatus.FAILED)
        retry = repo.create_agent_run(
            connection,
            repo.CreateAgentRunCommand(
                thread_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                lot_id="LOT-HITL",
                chamber_id="EQP01-PM1",
                autonomy_level=2,
                requested_alarm=ALARM,
                representative_alarm=ALARM,
                member_alarms=(ALARM,),
                retry_of_run_id=run_id,
            ),
        )
    with pytest.raises(repo.RepositoryConflict) as caught:
        action_store.production_port(engine.begin)(
            retry.agent_run_id,
            _hold(),
            _seed("LOT-HITL", "EQP01-PM1"),
        )
    assert caught.value.code == "ACTION_APPROVAL_NOT_PENDING"
