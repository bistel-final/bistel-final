from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from scripts.value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    ValueNormalizationError,
    logical_type,
    normalize_csv_row,
    normalize_db_row,
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
