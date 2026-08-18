"""DB와 corrected CSV를 같은 logical value로 정규화한다.

이 모듈은 DB 값 hash의 의미를 고정한다. 파일 전달 manifest의 byte/cell hash에는
사용하지 않으며, profile별 DB bootstrap manifest와 verifier에서만 사용한다.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

VALUE_NORMALIZATION_VERSION = "db-value-v1"


class ColumnLike(Protocol):
    name: str
    data_type: str
    nullable: bool


class ValueNormalizationError(ValueError):
    """값 또는 logical type이 계약과 다를 때 발생한다."""


def logical_type(data_type: str) -> str:
    """PostgreSQL format_type 값을 안정적인 logical type으로 축약한다."""

    normalized = " ".join(data_type.strip().lower().split())
    if normalized.startswith(("numeric", "decimal", "real", "double precision")):
        return "numeric"
    if normalized in {"smallint", "integer", "bigint"}:
        return "numeric"
    if normalized == "boolean":
        return "boolean"
    if normalized.startswith("timestamp") or normalized == "date":
        return "timestamp"
    if normalized in {"json", "jsonb"}:
        return "json"
    if normalized.startswith("vector"):
        return "vector"
    if normalized.startswith(("character varying", "character", "varchar", "char")):
        return "text"
    if normalized == "text":
        return "text"
    raise ValueNormalizationError(f"지원하지 않는 PostgreSQL type입니다: {data_type}")


def column_type_registry(columns: Sequence[ColumnLike]) -> dict[str, str]:
    """BASE_COLUMNS/Reference 계약을 공통 logical type registry로 변환한다."""

    registry = {column.name: logical_type(column.data_type) for column in columns}
    if not registry or len(registry) != len(columns):
        raise ValueNormalizationError("column type registry가 비었거나 중복됐습니다")
    return registry


def _canonical_decimal(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueNormalizationError("boolean을 numeric으로 정규화할 수 없습니다")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueNormalizationError("NaN/Infinity는 허용하지 않습니다")
        value = str(value)
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueNormalizationError("numeric 값 형식이 잘못됐습니다") from exc
    if not parsed.is_finite():
        raise ValueNormalizationError("NaN/Infinity는 허용하지 않습니다")
    if parsed == 0:
        return "0"
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _canonical_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "t", "1"}:
        return True
    if normalized in {"false", "f", "0"}:
        return False
    raise ValueNormalizationError("boolean 값 형식이 잘못됐습니다")


def _canonical_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    rendered = unicodedata.normalize("NFC", str(value).strip()).replace("T", " ", 1)
    if not rendered:
        raise ValueNormalizationError("timestamp 값이 비어 있습니다")
    try:
        # parsing은 유효성만 확인하고 입력 정밀도·timezone 표기는 보존한다.
        datetime.fromisoformat(rendered)
    except ValueError:
        try:
            date.fromisoformat(rendered)
        except ValueError as exc:
            raise ValueNormalizationError("timestamp 값 형식이 잘못됐습니다") from exc
    return rendered


def _canonical_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueNormalizationError("JSON 값 형식이 잘못됐습니다") from exc
    # JSON 자체 type을 보존하되 key/cell 문자열은 NFC로 고정한다.
    if isinstance(value, Mapping):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_json(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_canonical_json(child) for child in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueNormalizationError("JSON NaN/Infinity는 허용하지 않습니다")
    return value


def normalize_value(value: Any, value_type: str) -> Any:
    if value is None:
        return None
    if value_type == "numeric":
        return _canonical_decimal(value)
    if value_type == "boolean":
        return _canonical_boolean(value)
    if value_type == "timestamp":
        return _canonical_timestamp(value)
    if value_type == "json":
        return _canonical_json(value)
    if value_type in {"text", "vector"}:
        return unicodedata.normalize("NFC", str(value))
    raise ValueNormalizationError(f"지원하지 않는 logical type입니다: {value_type}")


def _validate_row_contract(
    row: Mapping[str, Any], column_types: Mapping[str, str]
) -> None:
    if set(row) != set(column_types):
        raise ValueNormalizationError("row와 column type registry가 다릅니다")


def normalize_db_row(
    row: Mapping[str, Any], column_types: Mapping[str, str]
) -> dict[str, Any]:
    """DBAPI row를 type 보존 canonical JSON row로 바꾼다."""

    _validate_row_contract(row, column_types)
    return {
        column: normalize_value(row[column], column_types[column])
        for column in column_types
    }


def normalize_csv_row(
    row: Mapping[str, str], column_types: Mapping[str, str]
) -> dict[str, Any]:
    """COPY 계약에 맞춰 빈 CSV cell을 NULL로 해석한 canonical row를 만든다."""

    _validate_row_contract(row, column_types)
    return {
        column: (
            None
            if row[column] == ""
            else normalize_value(row[column], column_types[column])
        )
        for column in column_types
    }
