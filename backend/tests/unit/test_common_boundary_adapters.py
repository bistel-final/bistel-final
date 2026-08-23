"""공개 ↔ 내부 계약 변환 (`V5-CM-4.1` 묶음 1).

값 하나를 옮기는 테스트가 아니라 **mapping이 양쪽 Enum 전체를 빠짐없이 덮는지**를 본다.
새 값이 생기면 mapping을 고치기 전에 여기서 먼저 깨진다.
"""

from __future__ import annotations

import pytest

from app.common import boundary_adapters as adapters
from app.common.enums import (
    ApprovalStatus,
    Decision,
    DeliveryChannel,
    PublicApprovalDecision,
    PublicApprovalStatus,
    PublicDeliveryChannel,
)


def test_public_and_internal_decision_map_totally() -> None:
    """공개 명령 2종과 내부 명령 2종이 서로 빠짐없이 대응한다."""

    assert {adapters.to_internal_decision(d) for d in PublicApprovalDecision} == set(
        Decision
    )
    assert {adapters.to_public_decision(d) for d in Decision} == set(
        PublicApprovalDecision
    )


@pytest.mark.parametrize("public", list(PublicApprovalDecision))
def test_decision_round_trips(public: PublicApprovalDecision) -> None:
    assert adapters.to_public_decision(adapters.to_internal_decision(public)) is public


def test_approval_status_partitions_the_internal_set() -> None:
    """**내부 5종이 공개 3종과 내부 전용 2종으로 정확히 나뉜다.**

    한쪽만 늘면 여기서 깨진다. `AUTO`·`EXPIRED`가 조용히 공개되는 것을 막는 계약이다.
    """

    public_capable = {
        status
        for status in ApprovalStatus
        if status not in adapters.INTERNAL_ONLY_APPROVAL_STATUSES
    }
    assert public_capable | adapters.INTERNAL_ONLY_APPROVAL_STATUSES == set(
        ApprovalStatus
    )
    assert not (public_capable & adapters.INTERNAL_ONLY_APPROVAL_STATUSES)
    assert {adapters.to_public_approval_status(s) for s in public_capable} == set(
        PublicApprovalStatus
    )


@pytest.mark.parametrize("status", sorted(adapters.INTERNAL_ONLY_APPROVAL_STATUSES))
def test_an_internal_only_status_never_becomes_public(status: ApprovalStatus) -> None:
    with pytest.raises(adapters.BoundaryConversionError) as caught:
        adapters.to_public_approval_status(status)
    assert status.value in str(caught.value)


def test_delivery_channels_map_totally() -> None:
    assert {adapters.to_public_channel(c) for c in DeliveryChannel} == set(
        PublicDeliveryChannel
    )
    assert {adapters.to_internal_channel(c) for c in PublicDeliveryChannel} == set(
        DeliveryChannel
    )


@pytest.mark.parametrize("internal", list(DeliveryChannel))
def test_channel_round_trips(internal: DeliveryChannel) -> None:
    public = adapters.to_public_channel(internal)
    assert adapters.to_internal_channel(public) is internal


def test_mes_mock_is_not_converted_by_stripping_a_suffix() -> None:
    """**암묵 변환을 쓰지 않는다.** 값이 늘어도 조용히 틀리지 않게 한다."""

    assert adapters.to_public_channel(DeliveryChannel.MES_MOCK) is (
        PublicDeliveryChannel.MES
    )
    # suffix 제거였다면 EMAIL도 다른 값이 됐을 것이다.
    assert adapters.to_public_channel(DeliveryChannel.EMAIL) is (
        PublicDeliveryChannel.EMAIL
    )
    assert "MES_MOCK" not in {c.value for c in PublicDeliveryChannel}


def test_public_and_internal_enums_do_not_share_members() -> None:
    """같은 이름의 Enum이 우연히 섞이지 않는다."""

    assert {d.value for d in PublicApprovalDecision} == {"APPROVED", "REJECTED"}
    assert {d.value for d in Decision} == {"APPROVE", "REJECT"}
    assert not ({d.value for d in PublicApprovalDecision} & {d.value for d in Decision})
