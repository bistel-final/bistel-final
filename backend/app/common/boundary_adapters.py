"""공개 계약 ↔ 내부 Runtime 계약 변환. **explicit dictionary만 쓴다.**

`V5-CM-4.1`. 공개 API가 내부 Enum을 그대로 노출하거나, 공개 입력이 내부 저장 값으로
그대로 흘러가는 것을 막는다.

문자열 suffix 제거(`MES_MOCK` → `MES`) 같은 암묵 변환을 쓰지 않는다. channel이나 상태가
늘어날 때 조용히 잘못된 값을 만들어 내기 때문이다. mapping이 양쪽 Enum 전체를 덮는지는
totality test가 고정한다.

이 모듈은 값 변환만 한다. 승인 상태 전이, 조회 정책, 전송 실행은 각각
`V5-C-3.3`·`V5-C-5.1`·`V5-C-4.x`가 소유한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.common.enums import (
    ApprovalStatus,
    Decision,
    DeliveryChannel,
    PublicApprovalDecision,
    PublicApprovalStatus,
    PublicDeliveryChannel,
)


class BoundaryConversionError(ValueError):
    """공개 경계를 넘을 수 없는 값. 내부 전용 값이 공개로 새는 것을 막는다."""


#: 공개 승인 명령 → 내부 명령.
_PUBLIC_TO_INTERNAL_DECISION: Mapping[PublicApprovalDecision, Decision] = (
    MappingProxyType(
        {
            PublicApprovalDecision.APPROVED: Decision.APPROVE,
            PublicApprovalDecision.REJECTED: Decision.REJECT,
        }
    )
)

#: 내부 명령 → 공개 승인 명령. 위 mapping의 역이며 totality test가 왕복을 고정한다.
_INTERNAL_TO_PUBLIC_DECISION: Mapping[Decision, PublicApprovalDecision] = (
    MappingProxyType(
        {internal: public for public, internal in _PUBLIC_TO_INTERNAL_DECISION.items()}
    )
)

#: 내부 승인 저장 상태 → 공개 목록 상태. **내부 5종이 빠짐없이 분할된다.**
_INTERNAL_TO_PUBLIC_APPROVAL_STATUS: Mapping[ApprovalStatus, PublicApprovalStatus] = (
    MappingProxyType(
        {
            ApprovalStatus.PENDING: PublicApprovalStatus.PENDING,
            ApprovalStatus.APPROVED: PublicApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED: PublicApprovalStatus.REJECTED,
        }
    )
)

#: 공개하지 않는 내부 전용 승인 상태. 위 mapping과 합쳐 내부 전체를 덮는다.
INTERNAL_ONLY_APPROVAL_STATUSES: frozenset[ApprovalStatus] = frozenset(
    {ApprovalStatus.AUTO, ApprovalStatus.EXPIRED}
)

#: 내부 delivery channel → 공개 channel.
_INTERNAL_TO_PUBLIC_CHANNEL: Mapping[DeliveryChannel, PublicDeliveryChannel] = (
    MappingProxyType(
        {
            DeliveryChannel.EMAIL: PublicDeliveryChannel.EMAIL,
            DeliveryChannel.MES_MOCK: PublicDeliveryChannel.MES,
        }
    )
)

#: 공개 channel → 내부 channel. 위 mapping의 역이다.
_PUBLIC_TO_INTERNAL_CHANNEL: Mapping[PublicDeliveryChannel, DeliveryChannel] = (
    MappingProxyType(
        {public: internal for internal, public in _INTERNAL_TO_PUBLIC_CHANNEL.items()}
    )
)


def to_internal_decision(decision: PublicApprovalDecision) -> Decision:
    """공개 승인 요청을 내부 명령으로 바꾼다."""

    try:
        return _PUBLIC_TO_INTERNAL_DECISION[decision]
    except KeyError as exc:  # pragma: no cover - Enum이 먼저 거부한다
        raise BoundaryConversionError(f"공개 승인 명령이 아닙니다: {decision}") from exc


def to_public_decision(decision: Decision) -> PublicApprovalDecision:
    """내부 명령을 공개 값으로 바꾼다."""

    try:
        return _INTERNAL_TO_PUBLIC_DECISION[decision]
    except KeyError as exc:  # pragma: no cover - Enum이 먼저 거부한다
        raise BoundaryConversionError(f"내부 승인 명령이 아닙니다: {decision}") from exc


def to_public_approval_status(status: ApprovalStatus) -> PublicApprovalStatus:
    """내부 저장 상태를 공개 목록 상태로 projection한다.

    `AUTO`·`EXPIRED`는 **공개하지 않는다.** 조회에서 제외할지 다르게 표시할지는
    `V5-C-5.1`이 정하며, 그 정책이 정해지기 전까지 공개 DTO로 새지 않게 막는다.
    """

    try:
        return _INTERNAL_TO_PUBLIC_APPROVAL_STATUS[status]
    except KeyError as exc:
        raise BoundaryConversionError(
            f"공개할 수 없는 내부 전용 승인 상태입니다: {status}"
        ) from exc


def to_public_channel(channel: DeliveryChannel) -> PublicDeliveryChannel:
    """내부 channel을 공개 값으로 바꾼다. `MES_MOCK` → `MES`."""

    try:
        return _INTERNAL_TO_PUBLIC_CHANNEL[channel]
    except KeyError as exc:  # pragma: no cover - Enum이 먼저 거부한다
        raise BoundaryConversionError(f"내부 channel이 아닙니다: {channel}") from exc


def to_internal_channel(channel: PublicDeliveryChannel) -> DeliveryChannel:
    """공개 channel을 내부 값으로 바꾼다. `MES` → `MES_MOCK`."""

    try:
        return _PUBLIC_TO_INTERNAL_CHANNEL[channel]
    except KeyError as exc:  # pragma: no cover - Enum이 먼저 거부한다
        raise BoundaryConversionError(f"공개 channel이 아닙니다: {channel}") from exc
