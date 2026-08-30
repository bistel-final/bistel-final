"""저장된 delivery plan만 실행하는 ``send_action(action_id)`` Tool.

조치·채널을 다시 결정하지 않는다. 짧은 read transaction에서 action·approval·delivery
snapshot을 검증한 뒤 실행 가능한 adapter 하나만 호출하고, DB 정본 전체를 Tool DTO로
projection한다. 외부 I/O 동안 transaction을 보유하지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Final

import httpx
from pydantic import ValidationError

from app.agent.email_delivery import (
    EmailDeliveryContractError,
    EmailDeliveryOutcome,
    EmailDeliveryResult,
    EmailDeliveryService,
)
from app.agent.email_delivery import (
    production_ports as email_production_ports,
)
from app.agent.mes_delivery import (
    MesDeliveryContractError,
    MesDeliveryOutcome,
    MesDeliveryResult,
    MesDeliveryService,
)
from app.agent.mes_delivery import (
    production_ports as mes_production_ports,
)
from app.agent.repository import (
    ActionBundle,
    ActionDeliveryRow,
    AgentRepositoryError,
    RepositoryConflict,
    RepositoryNotFound,
    get_action_bundle,
    list_action_deliveries,
)
from app.agent.tools import TransactionFactory
from app.common.enums import (
    ActionCode,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from app.common.tool_contracts import (
    ChannelDeliveryResult,
    SendActionToolInput,
    SendActionToolResult,
)

_EMAIL_PLAN: Final = (DeliveryChannel.EMAIL,)
_HOLD_PLAN: Final = (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK)
_EMAIL_NO_CALL: Final = frozenset(
    {
        DeliveryStatus.SENDING,
        DeliveryStatus.SENT,
        DeliveryStatus.FAILED,
        DeliveryStatus.UNKNOWN,
    }
)
_EMAIL_TERMINAL: Final = frozenset(
    {
        DeliveryStatus.SENT,
        DeliveryStatus.FAILED,
        DeliveryStatus.UNKNOWN,
    }
)
_EMAIL_REJECTED: Final = frozenset({DeliveryStatus.WAITING}) | _EMAIL_NO_CALL
_MES_NO_CALL: Final = _EMAIL_NO_CALL
_IDEMPOTENCY_CONFLICTS: Final = frozenset(
    {
        "DELIVERY_REQUEST_HASH_MISMATCH",
        "DELIVERY_TERMINAL_STATUS_CHANGED",
        "DELIVERY_STATE_CHANGED",
    }
)


@dataclass(frozen=True, slots=True)
class _ExecutionPlan:
    bundle: ActionBundle
    deliveries: tuple[ActionDeliveryRow, ...]
    channel: DeliveryChannel | None


def _failure(prefix: str, code: str) -> SendActionToolResult:
    return SendActionToolResult(ok=False, reason=f"{prefix}: {code}")


def _not_found() -> SendActionToolResult:
    return _failure("NOT_FOUND", "ACTION_DELIVERY_PLAN_NOT_FOUND")


def _policy_rejected() -> SendActionToolResult:
    return _failure("POLICY_REJECTED", "STORED_DELIVERY_PLAN_INVALID")


def _dependency_error() -> SendActionToolResult:
    return _failure("DEPENDENCY_ERROR", "SEND_ACTION_DEPENDENCY_FAILED")


def _status_by_channel(
    deliveries: Sequence[ActionDeliveryRow],
) -> dict[DeliveryChannel, DeliveryStatus]:
    return {item.channel: item.status for item in deliveries}


def _validated_plan(
    bundle: ActionBundle,
    deliveries: Sequence[ActionDeliveryRow],
) -> _ExecutionPlan | None:
    rows = tuple(deliveries)
    channels = tuple(item.channel for item in rows)
    if channels != bundle.delivery_channels or len(channels) != len(set(channels)):
        return None
    statuses = _status_by_channel(rows)

    if bundle.action_code is ActionCode.MONITORING:
        return None

    if bundle.action_code is ActionCode.WARNING:
        if (
            channels != _EMAIL_PLAN
            or bundle.approval_id is not None
            or bundle.approval_status is not None
            or bundle.approval_agent_run_id is not None
        ):
            return None
        status = statuses[DeliveryChannel.EMAIL]
        if status is DeliveryStatus.WAITING:
            return _ExecutionPlan(bundle, rows, DeliveryChannel.EMAIL)
        if status in _EMAIL_NO_CALL:
            return _ExecutionPlan(bundle, rows, None)
        return None

    if bundle.action_code is not ActionCode.EQP_HOLD:
        return None
    if (
        channels != _HOLD_PLAN
        or bundle.approval_id is None
        or bundle.approval_agent_run_id is None
        or bundle.approval_status is None
    ):
        return None

    email_status = statuses[DeliveryChannel.EMAIL]
    mes_status = statuses[DeliveryChannel.MES_MOCK]
    if bundle.approval_status is ApprovalStatus.PENDING:
        if mes_status is not DeliveryStatus.BLOCKED:
            return None
        if email_status is DeliveryStatus.WAITING:
            return _ExecutionPlan(bundle, rows, DeliveryChannel.EMAIL)
        if email_status in _EMAIL_NO_CALL:
            return _ExecutionPlan(bundle, rows, None)
        return None

    if bundle.approval_status is ApprovalStatus.APPROVED:
        if email_status not in _EMAIL_TERMINAL:
            return None
        if mes_status is DeliveryStatus.WAITING:
            return _ExecutionPlan(bundle, rows, DeliveryChannel.MES_MOCK)
        if mes_status in _MES_NO_CALL:
            return _ExecutionPlan(bundle, rows, None)
        return None

    if bundle.approval_status is ApprovalStatus.REJECTED:
        if email_status in _EMAIL_REJECTED and mes_status is DeliveryStatus.CANCELED:
            return _ExecutionPlan(bundle, rows, None)
        return None
    return None


def _projection(
    action_id: str,
    deliveries: Sequence[ActionDeliveryRow],
    *,
    previous: Sequence[ActionDeliveryRow],
    invoked_channel: DeliveryChannel | None,
    outcome: EmailDeliveryOutcome | MesDeliveryOutcome | None,
) -> SendActionToolResult:
    previous_status = _status_by_channel(previous)
    items: list[ChannelDeliveryResult] = []
    for row in deliveries:
        sent = row.channel is invoked_channel and outcome in {
            EmailDeliveryOutcome.SENT,
            MesDeliveryOutcome.SENT,
        }
        duplicate = row.status is DeliveryStatus.SENT and (
            previous_status.get(row.channel) is DeliveryStatus.SENT
            or (
                row.channel is invoked_channel
                and outcome in {EmailDeliveryOutcome.NOOP, MesDeliveryOutcome.NOOP}
            )
        )
        items.append(
            ChannelDeliveryResult(
                channel=row.channel,
                status=row.status,
                sent=sent,
                duplicate=duplicate,
            )
        )
    effect_attempted = invoked_channel is not None and outcome not in {
        None,
        EmailDeliveryOutcome.NOOP,
        MesDeliveryOutcome.NOOP,
    }
    return SendActionToolResult(
        ok=True,
        action_id=action_id,
        deliveries=items,
        effect_attempted=effect_attempted,
        effect_channel=invoked_channel if effect_attempted else None,
    )


def _outcome_failure(
    outcome: EmailDeliveryResult | MesDeliveryResult,
) -> SendActionToolResult | None:
    if outcome.outcome not in {
        EmailDeliveryOutcome.UNCERTAIN,
        MesDeliveryOutcome.UNCERTAIN,
    }:
        return None
    if outcome.reason_code == "WEBHOOK_TIMEOUT":
        return _failure("TIMEOUT", "SEND_ACTION_WEBHOOK_TIMEOUT")
    return _dependency_error()


@dataclass(frozen=True, slots=True)
class SendActionService:
    transactions: TransactionFactory
    email: EmailDeliveryService
    mes: MesDeliveryService

    def invoke(self, payload: dict[str, Any]) -> SendActionToolResult:
        try:
            request = SendActionToolInput.model_validate(payload)
        except ValidationError:
            return _policy_rejected()

        try:
            with self.transactions() as connection:
                bundle = get_action_bundle(connection, request.action_id)
                deliveries = tuple(
                    list_action_deliveries(connection, request.action_id)
                )
        except RepositoryNotFound:
            return _not_found()
        except AgentRepositoryError:
            return _dependency_error()

        if not deliveries:
            return _not_found()
        plan = _validated_plan(bundle, deliveries)
        if plan is None:
            return _policy_rejected()
        if plan.channel is None:
            return _projection(
                request.action_id,
                deliveries,
                previous=deliveries,
                invoked_channel=None,
                outcome=None,
            )

        outcome: EmailDeliveryResult | MesDeliveryResult
        try:
            if plan.channel is DeliveryChannel.EMAIL:
                if bundle.action_code is ActionCode.WARNING:
                    outcome = self.email.send_warning(request.action_id)
                else:
                    if bundle.approval_id is None:  # pre-I/O 검증의 방어적 이중화
                        return _policy_rejected()
                    outcome = self.email.send_approval(
                        request.action_id,
                        bundle.approval_id,
                    )
            else:
                outcome = self.mes.publish(request.action_id)
        except RepositoryConflict as exc:
            if exc.code in _IDEMPOTENCY_CONFLICTS:
                return _failure("IDEMPOTENCY_CONFLICT", exc.code)
            return _dependency_error()
        except (EmailDeliveryContractError, MesDeliveryContractError):
            return _policy_rejected()
        except AgentRepositoryError:
            return _dependency_error()

        failed = _outcome_failure(outcome)
        if failed is not None:
            return failed
        try:
            with self.transactions() as connection:
                current = tuple(list_action_deliveries(connection, request.action_id))
        except AgentRepositoryError:
            return _dependency_error()
        if not current:
            return _dependency_error()
        return _projection(
            request.action_id,
            current,
            previous=deliveries,
            invoked_channel=plan.channel,
            outcome=outcome.outcome,
        )


def build_send_action_tool(
    settings: ModuleType | Any,
    transactions: TransactionFactory,
    *,
    http_post: Callable[..., Any] = httpx.post,
    clock: Callable[[], float] = time.time,
) -> Callable[[dict[str, Any]], SendActionToolResult]:
    """EMAIL·MES adapter를 같은 runtime UoW owner로 지연 조립한다."""

    email = email_production_ports(
        settings,
        transactions,
        http_post=http_post,
        clock=clock,
    ).service
    mes = mes_production_ports(
        settings,
        transactions,
        http_post=http_post,
        clock=clock,
    ).service
    return SendActionService(
        transactions=transactions,
        email=email,
        mes=mes,
    ).invoke


__all__ = ["SendActionService", "build_send_action_tool"]
