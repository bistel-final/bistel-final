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


#: 값이 아니라 **스키마만** 검증하는 logical type.
#:
#: `logical_type()`은 이 값을 돌려주지만 `normalize_value()`는 받지 않는다. 그 경계를
#: 상수로 드러내 두 함수의 계약이 한눈에 맞물리게 한다(구현리뷰 4차 권장 2).
SCHEMA_ONLY_LOGICAL_TYPES = frozenset({"bytes"})


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
    if normalized == "bytea":
        # **`V5-CM-3.4` checkpoint 저장소가 쓰는 유일한 이진 타입이다.**
        #
        # `text`로 접으면 checkpoint blob 컬럼이 문자열 계약과 같아 보여, 타입이
        # 바뀌어도 full verifier가 통과한다. 별도 logical type으로 둔다.
        #
        # 값 정규화 대상은 아니다 — checkpoint 4 table은 `SCHEMA_ONLY_TABLES`라
        # content hash를 계산하지 않는다.
        return "bytes"
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
    """DB의 json/jsonb 값을 canonical 구조로 바꾼다.

    **문자열 파싱은 최상위에서 한 번만 한다.** driver가 원문 문자열을 돌려줄 때만
    필요하기 때문이다. 예전에는 재귀가 중첩 문자열까지 `json.loads`에 넣어서,
    `{"embedding_model": "BAAI/bge-m3"}` 같은 평범한 object가 전부 거부됐다 —
    `document_chunk.metadata_json` 25행이 그 형태이고, `r03_alarm_history`의
    `member_wafer_refs`·`member_alarm_refs`도 채워지면 같은 값을 담는다.
    """

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueNormalizationError("JSON 값 형식이 잘못됐습니다") from exc
    return _canonical_json_node(value)


def _canonical_json_node(value: Any) -> Any:
    """이미 파싱된 JSON 값. JSON type을 보존하되 key/cell 문자열은 NFC로 고정한다."""

    if isinstance(value, Mapping):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_json_node(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_canonical_json_node(child) for child in value]
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
    if value_type in SCHEMA_ONLY_LOGICAL_TYPES:
        # **의도적으로 값 정규화를 하지 않는다.**
        #
        # `logical_type()`이 돌려주는 값을 같은 모듈의 normalize가 거부하면 비대칭이
        # 남는다. 그 비대칭을 "지원하지 않음"이 아니라 **schema-only 계약**으로
        # 명시한다 — 임의 이진 blob에는 우리가 검증한 정본 표현이 없고, 이 type을 쓰는
        # table은 전부 `SCHEMA_ONLY_TABLES`다(구현리뷰 4차 권장 2).
        raise ValueNormalizationError(
            f"schema-only 전용 logical type은 값 정규화 대상이 아닙니다: {value_type}"
        )
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
