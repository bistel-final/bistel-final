"""User-approved MOCK-NOTIFY-V1: email + Mock dispatch, no human gate/tracking."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent import action_store, decision, email_delivery, mes_delivery, repository
from app.agent.public_schemas import AgentRunActionItem
from app.agent.state import ActionDecision, DeliveryPlan, PersistResult
from app.common.enums import (
    ActionCode,
    AlarmSource,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
    RunStatus,
)
from tests.unit import test_agent_action_store as store_fixture
from tests.unit import test_agent_decision as decision_fixture
from tests.unit import test_agent_graph as graph_fixture
from tests.unit import test_email_delivery as email_fixture
from tests.unit import test_mes_delivery as mes_fixture
from tests.unit import test_send_action_tool as send_fixture
from tests.unit.test_n8n_workflows import _load_workflows, _result_json, _run_code

POLICY = "MOCK-NOTIFY-V1"


def _decision(action):
    values = store_fixture._decision(action).model_dump()
    return ActionDecision(
        **{**values, "policy_version": POLICY, "requires_approval": False}
    )


def _bundle(action=ActionCode.EQP_HOLD):
    return replace(
        send_fixture._bundle(action),
        delivery_policy=POLICY,
        approval_id=None,
        approval_status=None,
        approval_agent_run_id=None,
    )


@pytest.mark.parametrize(
    "source,action",
    [
        (AlarmSource.SUMMARY, ActionCode.MONITORING),
        (AlarmSource.TRACE, ActionCode.WARNING),
        (AlarmSource.R03, ActionCode.EQP_HOLD),
    ],
)
def test_rule_selection_preserved_but_no_approval(source, action):
    route = decision_fixture._route((source,))
    legacy = decision.decide_action(route)
    result = decision.production_port(POLICY)(route)
    assert result.action is legacy.action is action
    assert result.severity is legacy.severity
    assert result.matched_rule == legacy.matched_rule
    assert result.policy_version == POLICY
    assert result.requires_approval is False


def test_notification_policy_cannot_claim_human_approval():
    with pytest.raises(ValidationError):
        ActionDecision(
            **{**_decision(ActionCode.EQP_HOLD).model_dump(), "requires_approval": True}
        )
    with pytest.raises(ValueError, match="ACTION_POLICY_INVALID"):
        decision.production_port("AUTO_APPROVE")


@pytest.mark.parametrize(
    "action,count",
    [(ActionCode.MONITORING, 0), (ActionCode.WARNING, 1), (ActionCode.EQP_HOLD, 2)],
)
def test_real_persist_port_has_no_approval_write_or_wait(monkeypatch, action, count):
    persist, observed = store_fixture._wire(monkeypatch)
    result = persist("RUN-1", _decision(action))
    assert result.approval_id is None
    assert len(result.deliveries) == count
    assert all(d.status is DeliveryStatus.WAITING for d in result.deliveries)
    assert observed.run.status is RunStatus.RUNNING
    writes = dict((k, v) for k, v in observed.writes if k != "delivery")
    assert "approval" not in writes and "begin_approval_wait" not in writes
    assert writes["action"]["policy_version"] == POLICY
    assert writes["provenance"]["action_policy_version"] == POLICY
    assert writes["provenance"]["rehydration_snapshot"] is None
    assert observed.transaction_count == 1


def test_existing_approval_bundle_is_not_converted():
    with pytest.raises(repository.RepositoryConflict, match="ACTION_DECISION_MISMATCH"):
        action_store._validate_bundle(
            store_fixture._bundle("ACT-old", ActionCode.EQP_HOLD),
            agent_run_id="RUN-1",
            decision=_decision(ActionCode.EQP_HOLD),
        )


def test_legacy_warning_cannot_be_reused_by_notification_policy():
    # WARNING has no approval fields under either policy: only policy binding
    # can catch this cross-policy reuse (Claude 26 N10).
    with pytest.raises(repository.RepositoryConflict, match="ACTION_DECISION_MISMATCH"):
        action_store._validate_bundle(
            store_fixture._bundle("ACT-old", ActionCode.WARNING),
            agent_run_id="RUN-1",
            decision=_decision(ActionCode.WARNING),
        )


def test_notification_email_refuses_legacy_bundle_without_http(monkeypatch):
    tx = email_fixture._Transactions()
    # Preserve every other valid automatic WARNING field to isolate N11.
    record = replace(
        email_fixture._action(ActionCode.WARNING),
        approval_required=False,
        approval_status=ApprovalStatus.AUTO,
    )
    email_fixture._wire(monkeypatch, transactions=tx, action=record)
    monkeypatch.setattr(
        email_delivery,
        "get_action_bundle",
        lambda *_: store_fixture._bundle(record.action_id, ActionCode.WARNING),
    )
    sender = email_delivery.production_ports(
        email_fixture._settings(),
        tx,
        http_post=lambda *_a, **_k: pytest.fail("HTTP must not be attempted"),
    ).service
    with pytest.raises(
        email_delivery.EmailDeliveryContractError, match="NOTIFICATION_POLICY_MISMATCH"
    ):
        sender.send_notification(record.action_id)


def test_notification_mes_rejects_injected_human_approval():
    claim = replace(
        mes_fixture._claim(),
        delivery_policy=POLICY,
        action=replace(
            mes_fixture._action(),
            approval_required=False,
            approval_status=ApprovalStatus.AUTO,
        ),
    )
    with pytest.raises(
        mes_delivery.MesDeliveryContractError, match="MES_NOTIFICATION_POLICY_MISMATCH"
    ):
        mes_delivery.raw_mes_payload(claim)


def test_public_repository_rejects_notification_approval_before_dto():
    row = SimpleNamespace(
        agent_run_id="RUN-1",
        action_code="EQP_HOLD",
        approval_status="APPROVED",
        delivery_policy=POLICY,
    )
    with pytest.raises(
        repository.RepositoryContractError, match="PUBLIC_ACTION_APPROVAL_UNEXPECTED"
    ):
        repository._public_action_record(row)


def test_config_import_rejects_invalid_policy_in_subprocess():
    import os
    import subprocess
    import sys

    environment = {
        **os.environ,
        "AGENT_ACTION_POLICY": "INVALID_POLICY",
        "AGENT_AUTONOMY_LEVEL": "2",
        "AGENT_LEVEL3_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import app.common.config"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "RuntimeError: ACTION_POLICY_INVALID" in result.stderr


def test_send_tool_dispatches_both_once_without_decision_or_confirmation(monkeypatch):
    rows = [
        send_fixture._row(c, DeliveryStatus.WAITING)
        for c in (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK)
    ]
    service, email, mes, _ = send_fixture._service(monkeypatch, _bundle(), rows)
    email.send_notification = lambda action_id: email._send("notification", None)
    assert service.invoke({"action_id": "ACT-1"}).ok
    assert email.calls == [("notification", None)] and mes.calls == []
    assert service.invoke({"action_id": "ACT-1"}).ok
    assert mes.calls == ["ACT-1"]
    assert service.invoke({"action_id": "ACT-1"}).effect_attempted is False
    assert email.calls == [("notification", None)] and mes.calls == ["ACT-1"]


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.SENT,
        DeliveryStatus.SENDING,
        DeliveryStatus.FAILED,
        DeliveryStatus.UNKNOWN,
    ],
)
def test_email_outcome_does_not_require_human_gate_for_mes(monkeypatch, status):
    rows = [
        send_fixture._row(DeliveryChannel.EMAIL, status),
        send_fixture._row(DeliveryChannel.MES_MOCK, DeliveryStatus.WAITING),
    ]
    service, email, mes, _ = send_fixture._service(monkeypatch, _bundle(), rows)
    assert service.invoke({"action_id": "ACT-1"}).ok
    assert email.calls == [] and mes.calls == ["ACT-1"]


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.SENDING,
        DeliveryStatus.SENT,
        DeliveryStatus.FAILED,
        DeliveryStatus.UNKNOWN,
    ],
)
def test_mes_terminal_or_uncertain_state_never_republishes(monkeypatch, status):
    rows = [
        send_fixture._row(DeliveryChannel.EMAIL, DeliveryStatus.SENT),
        send_fixture._row(DeliveryChannel.MES_MOCK, status),
    ]
    service, email, mes, _ = send_fixture._service(monkeypatch, _bundle(), rows)
    assert service.invoke({"action_id": "ACT-1"}).effect_attempted is False
    assert email.calls == [] and mes.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"approval_id": "APR-old"},
        {"approval_status": ApprovalStatus.APPROVED},
        {"approval_agent_run_id": "RUN-old"},
        {"delivery_policy": "unknown"},
    ],
)
def test_mixed_or_unknown_policy_cannot_dispatch(monkeypatch, changes):
    rows = [
        send_fixture._row(c, DeliveryStatus.WAITING)
        for c in (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK)
    ]
    service, email, mes, _ = send_fixture._service(
        monkeypatch, replace(_bundle(), **changes), rows
    )
    assert service.invoke({"action_id": "ACT-1"}).ok is False
    assert email.calls == [] and mes.calls == []


class _NotificationPorts(graph_fixture._Ports):
    def decide_action(self, route):
        self.calls.append("decide_action")
        return _decision(self.action)

    def persist_action(self, run_id, decision, seed):
        self.calls.append("persist_action")
        return PersistResult(
            action_id="ACT-1",
            deliveries=tuple(
                DeliveryPlan(channel=c, status=DeliveryStatus.WAITING)
                for c in _bundle(self.action).delivery_channels
            ),
        )


@pytest.mark.parametrize("level", [1, 2])
def test_real_graph_reaches_completion_without_hitl_or_approval_checkpoint(
    monkeypatch, level
):
    ports = _NotificationPorts(action=ActionCode.EQP_HOLD)
    graph, tools, _, finishes, _ = graph_fixture._build(
        monkeypatch,
        ports=ports,
        interrupt_after=("approval_email",),
        durable_interrupt=True,
    )
    state = graph_fixture._invoke(graph, level)
    assert state["approval_id"] is None
    assert state["action_decision"].policy_version == POLICY
    assert tools.send_count == 2
    assert [status for status, _ in finishes] == ["COMPLETED"]
    assert not {"approval_email", "hitl_interrupt", "cancel_mes"}.intersection(
        ports.calls
    )


@pytest.mark.parametrize("action", [ActionCode.WARNING, ActionCode.EQP_HOLD])
def test_real_email_adapter_and_wf2_accept_link_free_notification(monkeypatch, action):
    tx = email_fixture._Transactions()
    record = replace(
        email_fixture._action(action),
        approval_required=False,
        approval_status=ApprovalStatus.AUTO,
    )
    observed = email_fixture._wire(monkeypatch, transactions=tx, action=record)
    monkeypatch.setattr(email_delivery, "get_action_bundle", lambda *_: _bundle(action))

    def post(url, **kwargs):
        assert tx.open == 0
        observed.update(kwargs)
        return SimpleNamespace(status_code=200)

    sender = email_delivery.production_ports(
        email_fixture._settings(), tx, http_post=post
    ).service
    sender.send_notification(record.action_id)
    payload = json.loads(observed["content"])
    assert payload["email_kind"] == "ACTION_NOTIFY" and payload["approval_id"] is None
    assert ("Kafka·MES Mock" in payload["summary"]) is (action is ActionCode.EQP_HOLD)
    assert all(
        word not in payload["summary"]
        for word in ("승인 요청", "확인 완료", "알림 확인:", "http://", "https://")
    )
    workflow = _load_workflows()["WF2-notify-email.json"]
    checked = _result_json(
        _run_code(
            workflow,
            "Validate Email Payload",
            input_item={"json": {"body_ok": True, "body": payload}},
        )
    )
    assert checked["schema_ok"] is True
    payload["approval_id"] = "APR-forged"
    checked = _result_json(
        _run_code(
            workflow,
            "Validate Email Payload",
            input_item={"json": {"body_ok": True, "body": payload}},
        )
    )
    assert checked["schema_ok"] is False


def test_mock_mes_payload_is_automatic_policy_not_fabricated_human_approval():
    claim = replace(
        mes_fixture._claim(),
        approval=None,
        delivery_policy=POLICY,
        action=replace(
            mes_fixture._action(),
            approval_required=False,
            approval_status=ApprovalStatus.AUTO,
        ),
    )
    payload = json.loads(mes_delivery.raw_mes_payload(claim))
    assert payload["decided_by"] == "policy:MOCK-NOTIFY-V1"
    checked = _result_json(
        _run_code(
            _load_workflows()["WF3-mes-hold.json"],
            "Validate MES Payload",
            input_item={"json": {"body_ok": True, "body": payload}},
        )
    )
    assert checked["schema_ok"] is True
    command = mes_fixture.mock.parse_command(
        SimpleNamespace(
            value=lambda: json.dumps(payload).encode(), key=lambda: payload["action_id"]
        )
    )
    assert mes_fixture.mock.successful_hold(command).status == "SENT"


def test_public_contract_distinguishes_new_policy_from_legacy():
    value = dict(
        action_id="ACT-1",
        agent_run_id="RUN-1",
        action_code="EQP_HOLD",
        reason="R03",
        approval_status=None,
        deliveries=[
            dict(channel="EMAIL", status="SENT", started_at=None, completed_at=None),
            dict(channel="MES", status="SENT", started_at=None, completed_at=None),
        ],
    )
    assert AgentRunActionItem(**value, delivery_policy=POLICY).approval_status is None
    with pytest.raises(ValidationError):
        AgentRunActionItem(**value)
    with pytest.raises(ValidationError):
        AgentRunActionItem(
            **{**value, "approval_status": "APPROVED"}, delivery_policy=POLICY
        )


def test_no_confirmation_api_migration_or_client_was_left_behind():
    root = Path(__file__).resolve().parents[3]
    for relative in (
        "backend/app/agent/notification_ack.py",
        "backend/app/agent/notification_router.py",
        "backend/migrations/v5/003_action_notification.sql",
        "frontend/src/shared/api/notification.js",
        "frontend/src/features/agent/pages/NotificationConfirmPage.jsx",
    ):
        assert not (root / relative).exists()


def _automatic_claim_fixture(monkeypatch):
    action = replace(
        mes_fixture._action(),
        approval_required=False,
        approval_status=ApprovalStatus.AUTO,
    )
    observed = SimpleNamespace(
        bundle=_bundle(),
        equipment="EQP01",
        cas=True,
        hash=mes_fixture.HASH,
        writes=0,
        status=RunStatus.RUNNING,
        provenance={"action_policy_version": POLICY},
        lot_id=action.lot_id,
        role=repository.ActionLinkRole.CREATED,
    )
    monkeypatch.setattr(repository, "get_action_bundle", lambda *_: observed.bundle)
    monkeypatch.setattr(
        repository,
        "get_run_action",
        lambda *_: SimpleNamespace(
            action_id=action.action_id,
            link_role=observed.role,
            lot_id=observed.lot_id,
            chamber_id=action.chamber_id,
        ),
    )
    monkeypatch.setattr(
        repository,
        "get_agent_run",
        lambda *_: SimpleNamespace(
            evidence={repository.ACTION_PROVENANCE_KEY: observed.provenance},
            action=ActionCode.EQP_HOLD,
            status=observed.status,
            lot_id=action.lot_id,
            chamber_id=action.chamber_id,
        ),
    )
    # Exercise the real row decoder and CAS boundary with a DB-shaped mapping.
    from dataclasses import asdict

    class Connection:
        def execute(self, statement, values):
            if statement is repository._SELECT_INCIDENT_EQUIPMENT:
                return SimpleNamespace(
                    all=lambda: [
                        SimpleNamespace(
                            equipment_id=observed.equipment,
                        )
                    ]
                )
            if statement is repository._BEGIN_MES_DELIVERY:
                observed.writes += 1
                row = SimpleNamespace(
                    **asdict(
                        replace(
                            mes_fixture._delivery(),
                            request_hash=observed.hash,
                        )
                    )
                )
                return SimpleNamespace(
                    one_or_none=lambda: row if observed.cas else None
                )
            assert "link_role = 'CREATED'" in str(statement)
            return SimpleNamespace(one=lambda: SimpleNamespace(agent_run_id="RUN-1"))

    return action, Connection(), observed


def test_repository_claim_validates_automatic_creator_and_transitions_once(monkeypatch):
    action, connection, observed = _automatic_claim_fixture(monkeypatch)
    result = repository._begin_notification_mes(
        connection,
        action,
        mes_fixture._delivery(DeliveryStatus.WAITING),
    )
    assert result.delivery_policy == POLICY and result.approval is None
    assert result.delivery.status is DeliveryStatus.SENDING
    assert result.equipment_id == "EQP01" and observed.writes == 1


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("provenance", None, "MES_NOTIFICATION_IDENTITY_MISMATCH"),
        (
            "provenance",
            {"action_policy_version": "ACTION-POLICY-V1"},
            "MES_NOTIFICATION_IDENTITY_MISMATCH",
        ),
        ("lot_id", "LOT-other", "MES_NOTIFICATION_IDENTITY_MISMATCH"),
        ("status", RunStatus.WAITING_APPROVAL, "MES_NOTIFICATION_IDENTITY_MISMATCH"),
        (
            "role",
            repository.ActionLinkRole.REUSED,
            "MES_NOTIFICATION_IDENTITY_MISMATCH",
        ),
        ("equipment", " ", "MES_EQUIPMENT_NOT_UNIQUE"),
        (
            "bundle",
            replace(_bundle(), approval_id="APR-forged"),
            "MES_NOTIFICATION_POLICY_MISMATCH",
        ),
    ],
)
def test_repository_denies_invalid_identity_before_cas(monkeypatch, field, value, code):
    action, connection, observed = _automatic_claim_fixture(monkeypatch)
    setattr(observed, field, value)
    with pytest.raises(
        (repository.RepositoryConflict, repository.RepositoryContractError), match=code
    ):
        repository._begin_notification_mes(connection, action, mes_fixture._delivery())
    assert observed.writes == 0


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("cas", False, "DELIVERY_STATE_CHANGED"),
        ("hash", "b" * 64, "DELIVERY_REQUEST_HASH_MISMATCH"),
    ],
)
def test_repository_lost_claim_or_hash_mismatch_never_returns_send_grant(
    monkeypatch, field, value, code
):
    action, connection, observed = _automatic_claim_fixture(monkeypatch)
    setattr(observed, field, value)
    with pytest.raises(repository.RepositoryConflict, match=code):
        repository._begin_notification_mes(connection, action, mes_fixture._delivery())


def test_new_release_readback_requires_database_identity(monkeypatch):
    from app.agent import runtime_readback
    from app.common import config, db

    monkeypatch.setattr(config, "AGENT_ACTION_POLICY", POLICY)

    def unavailable():
        raise RuntimeError("synthetic unavailable DB")

    monkeypatch.setattr(db, "get_app_engine", unavailable)
    with pytest.raises(ValueError, match="RUNTIME_IDENTITY_UNAVAILABLE"):
        runtime_readback.collect_readback()


def test_old_level3_release_receipt_cannot_enable_new_policy(monkeypatch):
    from app.agent import runtime_composition

    monkeypatch.setattr(runtime_composition.settings, "AGENT_ACTION_POLICY", POLICY)
    runtime = runtime_composition.AgentRuntime(
        autonomy_level=3,
        level3_enabled=True,
        database_name="kosa_agent",
        demo_ack="old-receipt",
        demo_receipt_validator=lambda _: True,
    )
    with pytest.raises(
        runtime_composition.AgentRuntimeError,
        match="AUTONOMY_LEVEL_NOT_READY",
    ):
        runtime._require_autonomy_ready()


def test_same_policy_reentry_does_not_create_action_or_approval(monkeypatch):
    current = SimpleNamespace(
        action_id="ACT-1",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
        link_role=repository.ActionLinkRole.CREATED,
    )
    port, observed = store_fixture._wire(
        monkeypatch,
        current=current,
        existing=current,
        bundle=_bundle(),
    )
    observed.run.action = ActionCode.EQP_HOLD
    first = port("RUN-1", _decision(ActionCode.EQP_HOLD))
    second = port("RUN-1", _decision(ActionCode.EQP_HOLD))
    assert first == second and first.approval_id is None
    assert observed.writes == []


def test_public_repository_maps_new_hold_without_human_approval():
    row = SimpleNamespace(
        action_id="ACT-1",
        agent_run_id="RUN-1",
        delivery_policy=POLICY,
        action_code="EQP_HOLD",
        approval_status=None,
        lot_id="LOT-1",
        equipment_id="EQP01",
        chamber_id="EQP01-PM1",
        reason="R03",
        created_at=mes_fixture.NOW,
        deliveries=[
            dict(
                channel=c,
                status="SENT",
                started_at=mes_fixture.NOW,
                completed_at=mes_fixture.NOW,
            )
            for c in ("EMAIL", "MES_MOCK")
        ],
    )
    result = repository._public_action_record(row)
    assert result.delivery_policy == POLICY and result.approval_status is None
    row.delivery_policy = "ACTION-POLICY-V1"
    with pytest.raises(
        repository.RepositoryContractError, match="PUBLIC_ACTION_APPROVAL_MISSING"
    ):
        repository._public_action_record(row)


@pytest.mark.parametrize("policy", ["MOCK-NOTIFY-V1", "UNKNOWN"])
def test_legacy_stage2_denies_policy_before_initialization(tmp_path, policy):
    import os
    import subprocess

    root = Path(__file__).resolve().parents[3]
    env_file = tmp_path / "team.env"
    env_file.write_text(f"AGENT_ACTION_POLICY={policy}\nAPP_DB_PASSWORD=do-not-print\n")
    environment = dict(os.environ)
    for key in (
        "AGENT_ACTION_POLICY",
        "SOURCE_REVISION",
        "TEAM_IMAGE_TAG",
        "AGENT_FAULT_EVAL_ARTIFACT_PATH",
        "AGENT_GOLDEN_FLOW_SUMMARY_PATH",
    ):
        environment.pop(key, None)
    environment["CM52_ENV_FILE"] = str(env_file)
    result = subprocess.run(
        [
            "bash",
            str(root / "deploy/compose/cm52_stage2.sh"),
            "--attempt-id",
            "20260906T010203Z-0123456789ab",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "MOCK_NOTIFY_RELEASE_EVIDENCE_REQUIRED" in result.stdout
    assert "do-not-print" not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == [env_file]
