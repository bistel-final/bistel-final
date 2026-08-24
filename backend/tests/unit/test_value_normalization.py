from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest

from scripts.value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    ValueNormalizationError,
    logical_type,
    normalize_csv_row,
    normalize_db_row,
    normalize_value,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("22.6810"), "22.681"),
        ("22.681", "22.681"),
        ("45.0000", "45"),
        ("45.0", "45"),
        ("45", "45"),
        ("-0", "0"),
        ("-0.0000", "0"),
        ("1E+2", "100"),
    ],
)
def test_numeric_forms_have_one_canonical_value(value: object, expected: str) -> None:
    assert normalize_db_row({"value": value}, {"value": "numeric"}) == {
        "value": expected
    }


def test_null_and_empty_string_remain_distinct() -> None:
    assert normalize_db_row({"value": None}, {"value": "text"}) == {"value": None}
    assert normalize_db_row({"value": ""}, {"value": "text"}) == {"value": ""}
    assert normalize_csv_row({"value": ""}, {"value": "text"}) == {"value": None}


def test_boolean_and_timestamp_preserve_logical_type_and_precision() -> None:
    row = normalize_db_row(
        {
            "flag": True,
            "measured_at": datetime(2026, 8, 1, 1, 2, 3, 456789),
        },
        {"flag": "boolean", "measured_at": "timestamp"},
    )
    assert row == {
        "flag": True,
        "measured_at": "2026-08-01 01:02:03.456789",
    }
    assert normalize_csv_row(
        {"flag": "false", "measured_at": "2026-08-01T01:02:03.400"},
        {"flag": "boolean", "measured_at": "timestamp"},
    ) == {
        "flag": False,
        "measured_at": "2026-08-01 01:02:03.400",
    }


@pytest.mark.parametrize(
    ("data_type", "expected"),
    [
        ("numeric(12,4)", "numeric"),
        ("smallint", "numeric"),
        ("timestamp without time zone", "timestamp"),
        ("boolean", "boolean"),
        ("character varying(20)", "text"),
        ("jsonb", "json"),
        ("vector(1024)", "vector"),
    ],
)
def test_postgresql_type_mapping(data_type: str, expected: str) -> None:
    assert logical_type(data_type) == expected


def test_unknown_type_and_row_contract_are_rejected() -> None:
    with pytest.raises(ValueNormalizationError, match="지원하지 않는 PostgreSQL type"):
        logical_type("money")
    with pytest.raises(ValueNormalizationError, match="registry"):
        normalize_db_row({"a": 1}, {"b": "numeric"})


def test_normalization_version_is_explicit() -> None:
    assert VALUE_NORMALIZATION_VERSION == "db-value-v1"


class TestNestedJsonStrings:
    """**중첩 문자열을 JSON으로 파싱하지 않는다**(`V5-CM-1.8`).

    재귀가 `json.loads`를 다시 부르면 `{"embedding_model": "BAAI/bge-m3"}` 같은
    평범한 object가 전부 거부된다. `document_chunk.metadata_json` 25행이 그 형태이고,
    `r03_alarm_history`의 `member_wafer_refs`·`member_alarm_refs`도 채워지면 같은 값을
    담는다.
    """

    @pytest.mark.parametrize(
        "value",
        [
            {"embedding_model": "BAAI/bge-m3"},
            {"corrected_sha256": "7af0" * 16},
            ["W01", "W02"],
            [{"chunk": "cs1"}],
            {"nested": {"deep": ["a", "b"]}},
            {"mixed": ["a", 1, True, None]},
        ],
    )
    def test_nested_strings_are_preserved(self, value: object) -> None:
        assert normalize_value(value, "json") == value

    @pytest.mark.parametrize("value", ['{"a": 1}', "[1, 2]", '"quoted"'])
    def test_a_top_level_string_is_still_parsed(self, value: str) -> None:
        """driver가 원문 문자열을 돌려주는 경우는 여전히 파싱한다."""

        assert normalize_value(value, "json") == json.loads(value)

    def test_a_malformed_top_level_string_is_refused(self) -> None:
        with pytest.raises(ValueNormalizationError):
            normalize_value("not json", "json")

    def test_nfc_is_applied_to_keys_and_values(self) -> None:
        composed = normalize_value({"e\u0301": "e\u0301"}, "json")

        assert list(composed) == ["é"]
        assert composed["é"] == "é"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_non_finite_numbers_are_still_refused(self, bad: float) -> None:
        with pytest.raises(ValueNormalizationError):
            normalize_value({"v": bad}, "json")

    def test_the_real_metadata_shape_normalizes(self) -> None:
        """실제 `document_chunk.metadata_json` 형상."""

        metadata = {
            "chunk_contract_sha256": "22ef" * 15,
            "chunk_schema_version": "cs1",
            "corrected_sha256": "7af0" * 16,
            "embedding_dimension": 1024,
            "embedding_model": "BAAI/bge-m3",
            "embedding_model_revision": "5617a9f6" * 5,
        }

        assert normalize_value(metadata, "json") == metadata
