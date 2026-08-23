import pytest
from pydantic import ValidationError

from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef


class TestAlarmRef:
    @pytest.mark.parametrize("source", list(AlarmSource))
    def test_strict_source_aware_identity(self, source: AlarmSource) -> None:
        ref = AlarmRef(source=source, alarm_id="  ALM-001  ")

        assert ref.alarm_id == "ALM-001"
        assert AlarmRef.from_token(ref.to_token()) == ref

    @pytest.mark.parametrize("source", ["FDС", "trace", ""])
    def test_unknown_source_is_rejected(self, source: str) -> None:
        with pytest.raises(ValidationError):
            AlarmRef(source=source, alarm_id="ALM-001")

    @pytest.mark.parametrize("alarm_id", ["", "   ", "\t\n"])
    def test_blank_alarm_id_is_rejected(self, alarm_id: str) -> None:
        with pytest.raises(ValidationError):
            AlarmRef(source="TRACE", alarm_id=alarm_id)

    def test_source_is_required(self) -> None:
        """**source 없는 body를 받지 않는다.**

        `AlarmRef`는 서로 다른 알람 ID 공간을 구분하는 식별자다. source가 optional이면
        `alarm_id` 단독 body가 통과하고, 같은 ID를 쓰는 TRACE·SUMMARY·R03가 섞인다
        (API v3 §4.2 "legacy `/agent/run`과 `alarm_id` 단독 금지").
        """

        with pytest.raises(ValidationError):
            AlarmRef(alarm_id="TAL-0001")
        with pytest.raises(ValidationError):
            AlarmRef(source=None, alarm_id="TAL-0001")
        assert AlarmRef.model_fields["source"].is_required()

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AlarmRef(source="TRACE", alarm_id="TAL-001", lot_id="LOT-001")

    def test_same_id_from_different_sources_never_collides(self) -> None:
        trace = AlarmRef(source="TRACE", alarm_id="ALM-001")
        summary = AlarmRef(source="SUMMARY", alarm_id="ALM-001")

        assert trace != summary
        assert trace.to_token() != summary.to_token()
