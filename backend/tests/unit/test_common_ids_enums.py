import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.common import ids
from app.common.audit import (
    EVENT_ENTITY_TYPE,
    AuditEvent,
    AuditRecord,
    append_audit_log,
)
from app.common.enums import (
    ActionCode,
    ActionLinkRole,
    AlarmSource,
    AlarmType,
    ApprovalStatus,
    ChartType,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    IncidentModelSignalStatus,
    RunStatus,
    Severity,
    ThresholdValidationStatus,
    requires_approval,
    resolve_delivery_channels,
    resolve_severity,
)


class TestIds:
    @pytest.mark.parametrize(
        "factory, prefix, max_length",
        [
            (ids.new_agent_run_id, "RUN-", ids.AGENT_RUN_ID_MAX_LENGTH),
            (ids.new_action_id, "ACT-", ids.ACTION_ID_MAX_LENGTH),
            (ids.new_approval_id, "APR-", ids.APPROVAL_ID_MAX_LENGTH),
            (ids.new_tool_call_id, "TOOL-", ids.TOOL_CALL_ID_MAX_LENGTH),
        ],
    )
    def test_prefix_and_length(
        self,
        factory,
        prefix: str,
        max_length: int,
    ) -> None:
        value = factory()

        assert value.startswith(prefix)
        assert len(value) == max_length
        assert int(value[len(prefix) :], 16) >= 0

    def test_thread_id_is_uuid(self) -> None:
        value = ids.new_thread_id()

        assert len(value) == ids.THREAD_ID_MAX_LENGTH
        assert str(uuid.UUID(value)) == value

    def test_ids_are_unique(self) -> None:
        generated = {ids.new_action_id() for _ in range(500)}

        assert len(generated) == 500

    def test_deployed_fixture_format_does_not_collide(self) -> None:
        # evaluation profile의 제공 Mock ACT-0001과 신규 Runtime ID는 공존한다.
        assert ids.new_action_id() != "ACT-0001"
        assert len("ACT-0001") <= ids.ACTION_ID_MAX_LENGTH


class TestEnumValues:
    def test_string_serialization(self) -> None:
        assert AlarmSource.TRACE == "TRACE"
        assert AlarmType.IN == "IN"
        assert ChartType.TABLE == "table"
        assert f"{Severity.HIGH}" == "HIGH"

    def test_chart_type_is_lowercase(self) -> None:
        assert [c.value for c in ChartType] == ["table", "bar", "line", "histogram"]

    @pytest.mark.parametrize(
        "enum_type, expected",
        [
            (AlarmSource, ["TRACE", "SUMMARY", "R03"]),
            (AlarmType, ["IN", "OOC", "OOS"]),
            (ActionCode, ["MONITORING", "WARNING", "EQP_HOLD"]),
            (FaultHypothesis, ["FOC", "RFM", "MFD", "TMD", "OTH"]),
            (
                RunStatus,
                ["RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED"],
            ),
            (
                ApprovalStatus,
                ["AUTO", "PENDING", "APPROVED", "REJECTED", "EXPIRED"],
            ),
            (DeliveryChannel, ["EMAIL", "MES_MOCK"]),
            (
                DeliveryStatus,
                [
                    "BLOCKED",
                    "WAITING",
                    "SENDING",
                    "SENT",
                    "FAILED",
                    "CANCELED",
                    "UNKNOWN",
                ],
            ),
            (ThresholdValidationStatus, ["VERIFIED", "UNVERIFIED"]),
            (
                IncidentModelSignalStatus,
                ["READY", "DISABLED", "UNAVAILABLE"],
            ),
        ],
    )
    def test_v2_enum_members(self, enum_type: type, expected: list[str]) -> None:
        assert [member.value for member in enum_type] == expected

    def test_legacy_action_values_are_not_active(self) -> None:
        assert {"MONITOR", "NOTIFY", "LOT_HOLD"}.isdisjoint(ActionCode)
        assert "MES" not in DeliveryChannel


class TestMigrationBackedEnums:
    """DB CHECK가 값을 강제하는 Enum. **갈리면 insert가 실패한다.**

    DTO·단위 테스트는 통과하고 DB에서만 죽으므로 여기서 exact하게 고정한다
    (구현리뷰 1차 필수 1).
    """

    def test_action_link_role_matches_the_migration_check(self) -> None:
        """`002_agent_runtime_clean.sql:93`
        `CHECK (link_role IN ('CREATED', 'REUSED'))`."""

        assert {role.value for role in ActionLinkRole} == {"CREATED", "REUSED"}
        assert ActionLinkRole.CREATED == "CREATED"
        assert ActionLinkRole.REUSED == "REUSED"

    def test_action_link_role_is_read_from_the_migration_file(self) -> None:
        """상수를 손으로 적으면 migration과 또 갈린다 — **파일에서 읽어 대조한다.**"""

        import re
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"link_role\s+varchar\(\d+\)\s+NOT NULL\s+CHECK\s*\("
            r"\s*link_role IN \(([^)]*)\)",
            migration,
        )
        assert match is not None, "migration에서 link_role CHECK를 찾지 못했다"
        allowed = {value.strip().strip("'") for value in match.group(1).split(",")}
        assert allowed == {role.value for role in ActionLinkRole}

    def test_run_status_matches_the_migration_check(self) -> None:
        assert {status.value for status in RunStatus} == {
            "RUNNING",
            "WAITING_APPROVAL",
            "COMPLETED",
            "FAILED",
        }


class TestActionDerivedValues:
    @pytest.mark.parametrize(
        "action, severity, channels, approval",
        [
            (ActionCode.MONITORING, Severity.LOW, (), False),
            (
                ActionCode.WARNING,
                Severity.MEDIUM,
                (DeliveryChannel.EMAIL,),
                False,
            ),
            (
                ActionCode.EQP_HOLD,
                Severity.HIGH,
                (DeliveryChannel.EMAIL, DeliveryChannel.MES_MOCK),
                True,
            ),
        ],
    )
    def test_decision_table(
        self,
        action: ActionCode,
        severity: Severity,
        channels: tuple[DeliveryChannel, ...],
        approval: bool,
    ) -> None:
        assert resolve_severity(action) is severity
        assert resolve_delivery_channels(action) == channels
        assert requires_approval(action) is approval

    def test_only_eqp_hold_requires_approval(self) -> None:
        approved = [a for a in ActionCode if requires_approval(a)]

        assert approved == [ActionCode.EQP_HOLD]


class TestAuditEvents:
    def test_exactly_nine_events(self) -> None:
        assert [event.value for event in AuditEvent] == [
            "DETECTION_COMPLETED",
            "AGENT_RUN_STARTED",
            "HYPOTHESIS_GENERATED",
            "APPROVAL_REQUESTED",
            "APPROVAL_DECIDED",
            "ACTION_SENT",
            "ACTION_SEND_FAILED",
            "AGENT_RUN_COMPLETED",
            "AGENT_RUN_FAILED",
        ]

    def test_legacy_classification_event_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            AuditEvent("CLASSIFICATION_COMPLETED")

    def test_every_event_has_entity_type(self) -> None:
        assert set(EVENT_ENTITY_TYPE) == set(AuditEvent)

    def test_skipped_action_event_does_not_exist(self) -> None:
        assert "ACTION_SKIPPED" not in {event.value for event in AuditEvent}

    def test_entity_type_is_derived_from_event(self) -> None:
        record = AuditRecord(
            event_type=AuditEvent.APPROVAL_DECIDED,
            actor_type="HUMAN",
            entity_id="APR-0001",
        )

        assert record.entity_type.value == "APPROVAL"

    def test_entity_type_cannot_be_overridden(self) -> None:
        # 이벤트별 entity_type 이 고정이므로 입력으로 받지 않는다.
        with pytest.raises(ValidationError):
            AuditRecord(
                event_type=AuditEvent.APPROVAL_DECIDED,
                actor_type="HUMAN",
                entity_id="APR-0001",
                entity_type="ACTION",
            )

    @pytest.mark.parametrize("entity_id", ["", "   ", "\t\n"])
    def test_entity_id_rejects_blank(self, entity_id: str) -> None:
        with pytest.raises(ValidationError):
            AuditRecord(
                event_type=AuditEvent.ACTION_SENT,
                actor_type="AGENT",
                entity_id=entity_id,
            )

    @pytest.mark.parametrize("actor_id", ["", "   ", "\t"])
    def test_actor_id_rejects_blank(self, actor_id: str) -> None:
        with pytest.raises(ValidationError):
            AuditRecord(
                event_type=AuditEvent.APPROVAL_DECIDED,
                actor_type="HUMAN",
                entity_id="APR-0001",
                actor_id=actor_id,
            )

    def test_actor_id_allows_none_and_strips(self) -> None:
        assert (
            AuditRecord(
                event_type=AuditEvent.ACTION_SENT,
                actor_type="AGENT",
                entity_id="ACT-0001",
            ).actor_id
            is None
        )

        record = AuditRecord(
            event_type=AuditEvent.APPROVAL_DECIDED,
            actor_type="HUMAN",
            entity_id="APR-0001",
            actor_id="  operator  ",
        )

        assert record.actor_id == "operator"

    def test_entity_id_is_stripped(self) -> None:
        record = AuditRecord(
            event_type=AuditEvent.ACTION_SENT,
            actor_type="AGENT",
            entity_id="  ACT-0001  ",
        )

        assert record.entity_id == "ACT-0001"


class TestAppendAuditLog:
    def test_does_not_commit_caller_transaction(self) -> None:
        # 호출자 트랜잭션을 그대로 쓴다. 업무 롤백 시 감사로그도 함께 롤백돼야 한다.
        connection = MagicMock()
        connection.execute.return_value.one.return_value = SimpleNamespace(audit_id=7)

        audit_id = append_audit_log(
            connection,
            AuditRecord(
                event_type=AuditEvent.ACTION_SENT,
                actor_type="AGENT",
                entity_id="ACT-0001",
                after={"channel": "MES_MOCK"},
            ),
        )

        assert audit_id == 7
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()
        connection.close.assert_not_called()

    def test_entity_type_written_from_mapping(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.one.return_value = SimpleNamespace(audit_id=1)

        append_audit_log(
            connection,
            AuditRecord(
                event_type=AuditEvent.APPROVAL_REQUESTED,
                actor_type="AGENT",
                entity_id="APR-0001",
            ),
        )

        params = connection.execute.call_args.args[1]

        assert params["entity_type"] == "APPROVAL"
        assert params["event_type"] == "APPROVAL_REQUESTED"
