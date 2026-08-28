"""`V5-C-3.2` action 생성 transaction의 단위 계약."""

from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import action_store as subject
from app.agent.repository import (
    ActionBundle,
    RepositoryConflict,
    RepositoryContractError,
)
from app.agent.state import ActionDecision
from app.common.enums import (
    ActionCode,
    ActionLinkRole,
    AlarmSource,
    ApprovalStatus,
    DeliveryChannel,
    RunStatus,
    Severity,
    resolve_delivery_channels,
)
from app.common.schemas import AlarmRef

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")


def _decision(action: ActionCode | None) -> ActionDecision:
    values = {
        ActionCode.MONITORING: (Severity.LOW, False, "SUMMARY_OOC_ONLY"),
        ActionCode.WARNING: (Severity.MEDIUM, False, "TRACE_OOS"),
        ActionCode.EQP_HOLD: (Severity.HIGH, True, "R03_PRESENT"),
        None: (None, False, "NO_ALARM"),
    }
    severity, approval, rule = values[action]
    return ActionDecision(
        action=action,
        severity=severity,
        requires_approval=approval,
        matched_rule=rule,
    )


def _bundle(action_id: str, action: ActionCode) -> ActionBundle:
    return ActionBundle(
        action_id=action_id,
        action_code=action,
        approval_id="APR-existing" if action is ActionCode.EQP_HOLD else None,
        approval_status=(
            ApprovalStatus.PENDING if action is ActionCode.EQP_HOLD else None
        ),
        approval_agent_run_id="RUN-1" if action is ActionCode.EQP_HOLD else None,
        delivery_channels=resolve_delivery_channels(action),
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: RunStatus = RunStatus.RUNNING,
    action: ActionCode | None = None,
    current: Any | None = None,
    existing: Any | None = None,
    bundle: ActionBundle | None = None,
) -> tuple[Any, SimpleNamespace]:
    calls: list[tuple[str, Any]] = []
    writes: list[tuple[str, Any]] = []
    run = SimpleNamespace(
        agent_run_id="RUN-1",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
        representative_alarm=ALARM,
        status=status,
        action=action,
    )
    state = SimpleNamespace(
        calls=calls,
        writes=writes,
        transaction_count=0,
        run=run,
        current=current,
        existing=existing,
        bundle=bundle,
        hashes=[],
    )

    @contextmanager
    def transactions() -> Any:
        state.transaction_count += 1
        calls.append(("begin", None))
        yield object()
        calls.append(("commit", None))

    monkeypatch.setattr(subject, "get_agent_run", lambda *_a: run)

    def lock_incident(_connection: Any, *, lot_id: str, chamber_id: str) -> None:
        calls.append(("lock_incident", (lot_id, chamber_id)))

    def lock_run(_connection: Any, run_id: str) -> Any:
        calls.append(("lock_agent_run", run_id))
        return run

    monkeypatch.setattr(subject, "lock_incident", lock_incident)
    monkeypatch.setattr(subject, "lock_agent_run", lock_run)
    monkeypatch.setattr(
        subject, "find_created_action", lambda *_a, **_k: state.existing
    )
    monkeypatch.setattr(subject, "find_run_action", lambda *_a: state.current)
    monkeypatch.setattr(subject, "list_run_alarms", lambda *_a: [ALARM])
    monkeypatch.setattr(subject, "get_action_bundle", lambda *_a: state.bundle)
    monkeypatch.setattr(subject, "new_action_id", lambda: "ACT-0000000000000001")

    def insert_history(_connection: Any, **kwargs: Any) -> Any:
        writes.append(("action", kwargs))
        return SimpleNamespace(**kwargs)

    def link(_connection: Any, **kwargs: Any) -> Any:
        writes.append(("link", kwargs))
        row = SimpleNamespace(**kwargs)
        state.current = row
        if kwargs["link_role"] is ActionLinkRole.CREATED:
            state.existing = row
        return row

    def approval(_connection: Any, **kwargs: Any) -> Any:
        writes.append(("approval", kwargs))
        return SimpleNamespace(approval_id="APR-0000000000000001")

    def delivery(_connection: Any, **kwargs: Any) -> Any:
        writes.append(("delivery", kwargs))
        state.hashes.append(kwargs["request_hash"])
        return SimpleNamespace(**kwargs)

    def set_action(_connection: Any, _run_id: str, value: ActionCode) -> Any:
        writes.append(("set_run_action", value))
        run.action = value
        return run

    def merge(_connection: Any, _run_id: str, **kwargs: Any) -> Any:
        writes.append(("provenance", kwargs))
        return run

    monkeypatch.setattr(subject, "insert_action_history", insert_history)
    monkeypatch.setattr(subject, "link_run_action", link)
    monkeypatch.setattr(subject, "create_approval_request", approval)
    monkeypatch.setattr(subject, "insert_action_delivery", delivery)
    monkeypatch.setattr(subject, "set_run_action", set_action)
    monkeypatch.setattr(subject, "merge_run_action_provenance", merge)
    return subject.production_port(transactions), state


@pytest.mark.parametrize(
    ("action", "expected_writes", "channels"),
    [
        (
            ActionCode.MONITORING,
            ["action", "link", "set_run_action", "provenance"],
            (),
        ),
        (
            ActionCode.WARNING,
            ["action", "link", "delivery", "set_run_action", "provenance"],
            (DeliveryChannel.EMAIL,),
        ),
        (
            ActionCode.EQP_HOLD,
            [
                "action",
                "link",
                "approval",
                "delivery",
                "delivery",
                "set_run_action",
                "provenance",
            ],
            (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK),
        ),
    ],
)
def test_new_action_builds_the_exact_policy_bundle(
    monkeypatch: pytest.MonkeyPatch,
    action: ActionCode,
    expected_writes: list[str],
    channels: tuple[DeliveryChannel, ...],
) -> None:
    port, state = _wire(monkeypatch)

    result = port("RUN-1", _decision(action))

    assert [name for name, _ in state.writes] == expected_writes
    assert tuple(item.channel for item in result.deliveries) == channels
    assert (result.approval_id is not None) == (action is ActionCode.EQP_HOLD)
    action_payload = state.writes[0][1]
    assert action_payload["reason"] == subject.REASONS[_decision(action).matched_rule]
    assert "{" not in action_payload["reason"]
    assert "ACTION-POLICY" not in action_payload["reason"]
    assert state.transaction_count == 1


def test_no_alarm_is_rejected_before_transaction_and_id_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, state = _wire(monkeypatch)
    monkeypatch.setattr(
        subject,
        "new_action_id",
        lambda: pytest.fail("NO_ALARM에서 ID를 발급했습니다"),
    )

    with pytest.raises(RepositoryContractError) as exc:
        port("RUN-1", _decision(None))

    assert exc.value.code == "ACTION_REQUIRED"
    assert state.transaction_count == 0
    assert state.calls == []
    assert state.writes == []


def test_incident_lock_always_precedes_the_run_row_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, state = _wire(monkeypatch)
    port("RUN-1", _decision(ActionCode.MONITORING))
    names = [name for name, _ in state.calls]
    assert names.index("lock_incident") < names.index("lock_agent_run")


def test_incident_change_while_waiting_for_the_lock_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, state = _wire(monkeypatch)
    snapshot = SimpleNamespace(lot_id="LOT-before", chamber_id="CH-before")
    monkeypatch.setattr(subject, "get_agent_run", lambda *_a: snapshot)

    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.MONITORING))

    assert exc.value.code == "RUN_INCIDENT_CHANGED"
    assert state.writes == []
    assert ("lock_incident", ("LOT-before", "CH-before")) in state.calls


@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.WAITING_APPROVAL])
def test_same_run_replay_is_read_only_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
) -> None:
    link = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    port, state = _wire(
        monkeypatch,
        status=status,
        action=ActionCode.WARNING,
        current=link,
        existing=link,
        bundle=_bundle("ACT-existing", ActionCode.WARNING),
    )

    first = port("RUN-1", _decision(ActionCode.WARNING))
    second = port("RUN-1", _decision(ActionCode.WARNING))

    assert first == second
    assert first.action_id == "ACT-existing"
    assert state.writes == []
    assert state.transaction_count == 2


def test_failed_retry_links_the_existing_action_without_new_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    port, state = _wire(
        monkeypatch,
        existing=created,
        bundle=_bundle("ACT-existing", ActionCode.WARNING),
    )

    result = port("RUN-1", _decision(ActionCode.WARNING))

    assert result.action_id == "ACT-existing"
    assert [name for name, _ in state.writes] == [
        "link",
        "set_run_action",
        "provenance",
    ]
    assert state.writes[0][1]["link_role"] is ActionLinkRole.REUSED


def test_reuse_refuses_a_different_action_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    port, state = _wire(
        monkeypatch,
        existing=created,
        bundle=_bundle("ACT-existing", ActionCode.WARNING),
    )

    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.EQP_HOLD))

    assert exc.value.code == "ACTION_DECISION_MISMATCH"
    assert state.writes == []


@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.FAILED])
def test_terminal_run_cannot_persist_action(
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
) -> None:
    port, state = _wire(monkeypatch, status=status)
    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.MONITORING))
    assert exc.value.code == "RUN_NOT_ACTIVE"
    assert state.writes == []


def test_waiting_run_without_a_link_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, state = _wire(monkeypatch, status=RunStatus.WAITING_APPROVAL)
    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.EQP_HOLD))
    assert exc.value.code == "RUN_STATE_INVALID"
    assert state.writes == []


def test_request_hash_is_raw_deterministic_lowercase_hex() -> None:
    kwargs = {
        "action_id": "ACT-0000000000000001",
        "channel": DeliveryChannel.EMAIL,
        "action_code": ActionCode.WARNING,
        "lot_id": "LOT-1",
        "chamber_id": "EQP01-PM1",
        "trigger_alarm": ALARM,
    }
    first = subject._request_hash(**kwargs)
    second = subject._request_hash(**kwargs)
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert subject._request_hash(**(kwargs | {"lot_id": "LOT-2"})) != first


@pytest.mark.parametrize(
    "bundle",
    [
        ActionBundle(
            action_id="ACT-existing",
            action_code=ActionCode.WARNING,
            approval_id="APR-wrong",
            approval_status=ApprovalStatus.PENDING,
            approval_agent_run_id="RUN-1",
            delivery_channels=(DeliveryChannel.EMAIL,),
        ),
        ActionBundle(
            action_id="ACT-existing",
            action_code=ActionCode.WARNING,
            approval_id=None,
            approval_status=None,
            approval_agent_run_id=None,
            delivery_channels=(),
        ),
    ],
)
def test_reuse_requires_an_exact_approval_and_delivery_bundle(
    monkeypatch: pytest.MonkeyPatch,
    bundle: ActionBundle,
) -> None:
    created = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    port, state = _wire(monkeypatch, existing=created, bundle=bundle)
    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.WARNING))
    assert exc.value.code == "ACTION_DECISION_MISMATCH"
    assert state.writes == []


def test_reuse_compares_delivery_channels_as_an_order_independent_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    bundle = ActionBundle(
        action_id="ACT-existing",
        action_code=ActionCode.EQP_HOLD,
        approval_id="APR-existing",
        approval_status=ApprovalStatus.PENDING,
        approval_agent_run_id="RUN-1",
        delivery_channels=(DeliveryChannel.MES_MOCK, DeliveryChannel.EMAIL),
    )
    port, state = _wire(
        monkeypatch,
        status=RunStatus.WAITING_APPROVAL,
        action=ActionCode.EQP_HOLD,
        current=created,
        existing=created,
        bundle=bundle,
    )

    result = port("RUN-1", _decision(ActionCode.EQP_HOLD))

    assert tuple(item.channel for item in result.deliveries) == (
        DeliveryChannel.EMAIL,
        DeliveryChannel.MES_MOCK,
    )
    assert state.writes == []


@pytest.mark.parametrize(
    "approval_status",
    [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED],
)
def test_retry_refuses_a_terminal_approval(
    monkeypatch: pytest.MonkeyPatch,
    approval_status: ApprovalStatus,
) -> None:
    created = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    bundle = ActionBundle(
        action_id="ACT-existing",
        action_code=ActionCode.EQP_HOLD,
        approval_id="APR-existing",
        approval_status=approval_status,
        approval_agent_run_id="RUN-1",
        delivery_channels=(DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK),
    )
    port, state = _wire(monkeypatch, existing=created, bundle=bundle)

    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.EQP_HOLD))

    assert exc.value.code == "ACTION_APPROVAL_NOT_PENDING"
    assert state.writes == []


def test_retry_refuses_a_pending_approval_owned_by_the_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = SimpleNamespace(
        action_id="ACT-existing",
        lot_id="LOT-1",
        chamber_id="EQP01-PM1",
    )
    bundle = ActionBundle(
        action_id="ACT-existing",
        action_code=ActionCode.EQP_HOLD,
        approval_id="APR-existing",
        approval_status=ApprovalStatus.PENDING,
        approval_agent_run_id="RUN-failed",
        delivery_channels=(DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK),
    )
    port, state = _wire(monkeypatch, existing=created, bundle=bundle)

    with pytest.raises(RepositoryConflict) as exc:
        port("RUN-1", _decision(ActionCode.EQP_HOLD))

    assert exc.value.code == "ACTION_APPROVAL_RUN_MISMATCH"
    assert state.writes == []


def test_missing_reason_is_a_sanitized_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, state = _wire(monkeypatch)
    monkeypatch.setattr(subject, "REASONS", {})

    with pytest.raises(RepositoryContractError) as exc:
        port("RUN-1", _decision(ActionCode.WARNING))

    assert exc.value.code == "ACTION_REASON_NOT_FOUND"
    assert state.writes == []
