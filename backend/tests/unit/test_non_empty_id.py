import pytest
from pydantic import TypeAdapter, ValidationError

from app.common.enums import AlarmSource
from app.common.ids import (
    NonEmptyId,
    format_alarm_ref_token,
    parse_alarm_ref_token,
)

adapter = TypeAdapter(NonEmptyId)


class TestNonEmptyIdStandalone:
    """모델의 str_strip_whitespace 설정에 의존하지 않고 타입 자체가 동작해야 한다."""

    @pytest.mark.parametrize("value", ["", "   ", "\t", "\n", " \t\n "])
    def test_rejects_blank(self, value: str) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python(value)

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("  LH-00101  ", "LH-00101"),
            ("\tACT-0001\n", "ACT-0001"),
            ("PHO-01-C1", "PHO-01-C1"),
        ],
    )
    def test_strips_surrounding_whitespace(self, value: str, expected: str) -> None:
        assert adapter.validate_python(value) == expected

    def test_keeps_inner_characters(self) -> None:
        assert adapter.validate_python("ET-7500") == "ET-7500"


class TestAlarmRefToken:
    @pytest.mark.parametrize("source", list(AlarmSource))
    def test_round_trip_is_source_aware(self, source: AlarmSource) -> None:
        token = format_alarm_ref_token(source, "  ALM-0001  ")

        assert token == f"{source.value}:ALM-0001"
        assert parse_alarm_ref_token(token) == (source, "ALM-0001")

    def test_alarm_id_may_contain_colon_without_losing_information(self) -> None:
        token = format_alarm_ref_token(AlarmSource.R03, "policy:R03-0001")

        assert parse_alarm_ref_token(token) == (
            AlarmSource.R03,
            "policy:R03-0001",
        )

    @pytest.mark.parametrize("token", ["ALM-0001", "TRACE:", "UNKNOWN:ALM-1"])
    def test_invalid_token_is_rejected(self, token: str) -> None:
        with pytest.raises((ValueError, ValidationError)):
            parse_alarm_ref_token(token)
