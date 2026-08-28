"""규칙 조치를 원자적으로 저장하는 production port (`V5-C-3.2`).

Repository 함수는 caller의 ``Connection``만 소비한다. 이 모듈이 transaction scope와
incident→run lock 순서, 신규 생성·재사용 정책을 소유한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final

from app.agent.repository import (
    ActionBundle,
    RepositoryConflict,
    RepositoryContractError,
    create_approval_request,
    find_created_action,
    find_run_action,
    get_action_bundle,
    get_agent_run,
    insert_action_delivery,
    insert_action_history,
    link_run_action,
    list_run_alarms,
    lock_agent_run,
    merge_run_action_provenance,
    set_run_action,
)
from app.agent.run_guard_repository import lock_incident
from app.agent.state import (
    INITIAL_STATUS,
    ActionDecision,
    DeliveryPlan,
    PersistResult,
)
from app.agent.tools import TransactionFactory
from app.common.enums import (
    ActionCode,
    ActionLinkRole,
    ApprovalStatus,
    DeliveryChannel,
    RunStatus,
    requires_approval,
    resolve_delivery_channels,
)
from app.common.ids import NonEmptyId, new_action_id
from app.common.schemas import AlarmRef

DELIVERY_REQUEST_SCHEMA: Final = "delivery-request-v1"

REASONS: Final[Mapping[str, str]] = {
    "R03_PRESENT": "R03 알람이 존재해 설비 보류 조치를 생성했습니다.",
    "TRACE_OOS": "TRACE OOS 알람이 존재해 경고 조치를 생성했습니다.",
    "SUMMARY_OOC_ONLY": "SUMMARY OOC 알람만 존재해 모니터링 조치를 생성했습니다.",
}


def _request_hash(
    *,
    action_id: str,
    channel: DeliveryChannel,
    action_code: ActionCode,
    lot_id: str,
    chamber_id: str,
    trigger_alarm: AlarmRef,
) -> str:
    payload = {
        "schema": DELIVERY_REQUEST_SCHEMA,
        "action_id": action_id,
        "channel": channel.value,
        "action_code": action_code.value,
        "lot_id": lot_id,
        "chamber_id": chamber_id,
        "trigger_alarm": trigger_alarm.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _result(
    *,
    action_id: str,
    approval_id: str | None,
    channels: tuple[DeliveryChannel, ...],
) -> PersistResult:
    return PersistResult(
        action_id=action_id,
        approval_id=approval_id,
        deliveries=tuple(
            DeliveryPlan(channel=channel, status=INITIAL_STATUS[channel])
            for channel in channels
        ),
    )


def _validate_bundle(
    bundle: ActionBundle,
    *,
    agent_run_id: str,
    decision: ActionDecision,
) -> PersistResult:
    action = decision.action
    if action is None:
        raise RepositoryContractError("ACTION_REQUIRED")
    expected_channels = resolve_delivery_channels(action)
    approval_required = requires_approval(action)
    if (
        bundle.action_code is not action
        or (bundle.approval_id is not None) != approval_required
        or (bundle.approval_status is not None) != approval_required
        or (bundle.approval_agent_run_id is not None) != approval_required
        or len(bundle.delivery_channels) != len(set(bundle.delivery_channels))
        or set(bundle.delivery_channels) != set(expected_channels)
    ):
        raise RepositoryConflict("ACTION_DECISION_MISMATCH")
    if approval_required and bundle.approval_status is not ApprovalStatus.PENDING:
        raise RepositoryConflict("ACTION_APPROVAL_NOT_PENDING")
    if approval_required and bundle.approval_agent_run_id != agent_run_id:
        raise RepositoryConflict("ACTION_APPROVAL_RUN_MISMATCH")
    result = _result(
        action_id=bundle.action_id,
        approval_id=bundle.approval_id,
        channels=expected_channels,
    )
    result.assert_matches(decision)
    return result


def _require_active_shape(
    *,
    status: RunStatus,
    has_current_link: bool,
) -> None:
    if status in {RunStatus.COMPLETED, RunStatus.FAILED}:
        raise RepositoryConflict("RUN_NOT_ACTIVE")
    if status is RunStatus.WAITING_APPROVAL and not has_current_link:
        raise RepositoryConflict("RUN_STATE_INVALID")


def _reason_for(decision: ActionDecision) -> str:
    reason = REASONS.get(decision.matched_rule)
    if reason is None:
        raise RepositoryContractError("ACTION_REASON_NOT_FOUND")
    return reason


def production_port(
    transactions: TransactionFactory,
) -> Callable[[NonEmptyId, ActionDecision], PersistResult]:
    """매 호출마다 짧은 transaction 하나로 action bundle을 생성·재사용한다."""

    def persist_action(
        agent_run_id: NonEmptyId,
        decision: ActionDecision,
    ) -> PersistResult:
        resolved = ActionDecision.model_validate(decision)
        action = resolved.action
        if action is None:
            # public callable 경계다. DB transaction과 ID 발급 전에 거부한다.
            raise RepositoryContractError("ACTION_REQUIRED")

        with transactions() as connection:
            snapshot = get_agent_run(connection, agent_run_id)
            lock_incident(
                connection,
                lot_id=snapshot.lot_id,
                chamber_id=snapshot.chamber_id,
            )
            locked = lock_agent_run(connection, agent_run_id)
            # 현재 application writer는 incident key를 바꾸지 않지만 DB column 자체는
            # UPDATE 가능하다. 외부 writer나 후속 기능이 lock 대기 중 바꾸면 새 key를
            # 잠그지 않은 채 계속하지 않고 transaction을 중단한다.
            if (locked.lot_id, locked.chamber_id) != (
                snapshot.lot_id,
                snapshot.chamber_id,
            ):
                raise RepositoryConflict("RUN_INCIDENT_CHANGED")

            existing = find_created_action(
                connection,
                lot_id=locked.lot_id,
                chamber_id=locked.chamber_id,
            )
            current = find_run_action(connection, agent_run_id)
            _require_active_shape(
                status=locked.status,
                has_current_link=current is not None,
            )

            if current is not None:
                if (
                    current.lot_id != locked.lot_id
                    or current.chamber_id != locked.chamber_id
                    or existing is None
                    or current.action_id != existing.action_id
                ):
                    raise RepositoryConflict("ACTION_BUNDLE_INVALID")
                if locked.action is not action:
                    raise RepositoryConflict("ACTION_DECISION_MISMATCH")
                return _validate_bundle(
                    get_action_bundle(connection, current.action_id),
                    agent_run_id=agent_run_id,
                    decision=resolved,
                )

            member_alarms = tuple(list_run_alarms(connection, agent_run_id))
            if not member_alarms:
                raise RepositoryContractError("EMPTY_ACTION_MEMBER_ALARMS")

            if existing is not None:
                result = _validate_bundle(
                    get_action_bundle(connection, existing.action_id),
                    agent_run_id=agent_run_id,
                    decision=resolved,
                )
                link_run_action(
                    connection,
                    agent_run_id=agent_run_id,
                    action_id=existing.action_id,
                    link_role=ActionLinkRole.REUSED,
                    lot_id=locked.lot_id,
                    chamber_id=locked.chamber_id,
                    trigger_alarm=locked.representative_alarm,
                )
                set_run_action(connection, agent_run_id, action)
                merge_run_action_provenance(
                    connection,
                    agent_run_id,
                    action_policy_version=resolved.policy_version,
                    member_alarms=member_alarms,
                )
                return result

            reason = _reason_for(resolved)
            action_id = new_action_id()
            created_at = datetime.now(UTC)
            insert_action_history(
                connection,
                action_id=action_id,
                lot_id=locked.lot_id,
                chamber_id=locked.chamber_id,
                action_code=action,
                reason=reason,
                created_at=created_at,
            )
            link_run_action(
                connection,
                agent_run_id=agent_run_id,
                action_id=action_id,
                link_role=ActionLinkRole.CREATED,
                lot_id=locked.lot_id,
                chamber_id=locked.chamber_id,
                trigger_alarm=locked.representative_alarm,
            )

            approval_id = None
            if resolved.requires_approval:
                approval_id = create_approval_request(
                    connection,
                    action_id=action_id,
                    agent_run_id=agent_run_id,
                ).approval_id

            channels = resolve_delivery_channels(action)
            for channel in channels:
                insert_action_delivery(
                    connection,
                    action_id=action_id,
                    channel=channel,
                    status=INITIAL_STATUS[channel],
                    request_hash=_request_hash(
                        action_id=action_id,
                        channel=channel,
                        action_code=action,
                        lot_id=locked.lot_id,
                        chamber_id=locked.chamber_id,
                        trigger_alarm=locked.representative_alarm,
                    ),
                )

            set_run_action(connection, agent_run_id, action)
            merge_run_action_provenance(
                connection,
                agent_run_id,
                action_policy_version=resolved.policy_version,
                member_alarms=member_alarms,
            )
            result = _result(
                action_id=action_id,
                approval_id=approval_id,
                channels=channels,
            )
            result.assert_matches(resolved)
            return result

    return persist_action


__all__ = ["DELIVERY_REQUEST_SCHEMA", "production_port"]
