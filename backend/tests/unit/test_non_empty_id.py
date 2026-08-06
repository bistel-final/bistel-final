import pytest
from pydantic import TypeAdapter, ValidationError

from app.common.ids import NonEmptyId

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
