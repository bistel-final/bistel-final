"""kosa_0813의 누락된 dim_parameter 8행을 결정론적으로 생성한다."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from manifest_v3 import SOURCE_EXPECTED_COLUMNS, ManifestSchemaError

STAGE_ID = "dim_parameter_seed"
STAGE_VERSION = "1"

DIM_PARAMETER_COLUMNS = (
    "parameter_id",
    "parameter_name",
    "unit",
    "area",
    "target_value",
    "spec_lower",
    "ctrl_lower",
    "ctrl_upper",
    "spec_upper",
    "upper_only",
)

PARAMETER_ORDER = (
    "PH_DOSE",
    "PH_FOCUS",
    "PH_PEB",
    "PH_DEV",
    "ET_PRES",
    "ET_REFL",
    "ET_CF4",
    "ET_ESC",
)

# 근거: kosa_0813 Generator SPEC의 한계선과 docs_rag SPEC 표의 이름·단위.
# PH_FOCUS 음수는 PostgreSQL numeric 입력이 가능한 ASCII '-'로 고정한다.
DIM_PARAMETER_ROWS = (
    {
        "parameter_id": "PH_DOSE",
        "parameter_name": "Exposure Dose",
        "unit": "mJ/cm2",
        "area": "photo",
        "target_value": "25.0",
        "spec_lower": "24.0",
        "ctrl_lower": "24.4",
        "ctrl_upper": "25.6",
        "spec_upper": "26.0",
        "upper_only": "false",
    },
    {
        "parameter_id": "PH_FOCUS",
        "parameter_name": "Focus Offset",
        "unit": "nm",
        "area": "photo",
        "target_value": "0.0",
        "spec_lower": "-60.0",
        "ctrl_lower": "-36.0",
        "ctrl_upper": "36.0",
        "spec_upper": "60.0",
        "upper_only": "false",
    },
    {
        "parameter_id": "PH_PEB",
        "parameter_name": "PEB Temperature",
        "unit": "degC",
        "area": "photo",
        "target_value": "115.0",
        "spec_lower": "113.0",
        "ctrl_lower": "113.8",
        "ctrl_upper": "116.2",
        "spec_upper": "117.0",
        "upper_only": "false",
    },
    {
        "parameter_id": "PH_DEV",
        "parameter_name": "Developer Temperature",
        "unit": "degC",
        "area": "photo",
        "target_value": "23.0",
        "spec_lower": "22.4",
        "ctrl_lower": "22.64",
        "ctrl_upper": "23.36",
        "spec_upper": "23.6",
        "upper_only": "false",
    },
    {
        "parameter_id": "ET_PRES",
        "parameter_name": "Chamber Pressure",
        "unit": "mTorr",
        "area": "etch",
        "target_value": "25.0",
        "spec_lower": "22.0",
        "ctrl_lower": "23.2",
        "ctrl_upper": "26.8",
        "spec_upper": "28.0",
        "upper_only": "false",
    },
    {
        "parameter_id": "ET_REFL",
        "parameter_name": "Reflected Power",
        "unit": "W",
        "area": "etch",
        "target_value": "8.0",
        "spec_lower": "0.0",
        "ctrl_lower": "0.0",
        "ctrl_upper": "21.0",
        "spec_upper": "30.0",
        "upper_only": "true",
    },
    {
        "parameter_id": "ET_CF4",
        "parameter_name": "CF4 Flow",
        "unit": "sccm",
        "area": "etch",
        "target_value": "80.0",
        "spec_lower": "74.0",
        "ctrl_lower": "76.4",
        "ctrl_upper": "83.6",
        "spec_upper": "86.0",
        "upper_only": "false",
    },
    {
        "parameter_id": "ET_ESC",
        "parameter_name": "ESC Temperature",
        "unit": "degC",
        "area": "etch",
        "target_value": "60.0",
        "spec_lower": "56.0",
        "ctrl_lower": "57.6",
        "ctrl_upper": "62.4",
        "spec_upper": "64.0",
        "upper_only": "false",
    },
)

_LIMIT_COLUMNS = (
    "spec_lower",
    "ctrl_lower",
    "target_value",
    "ctrl_upper",
    "spec_upper",
)


def _require_source_table(dataset: Mapping[str, Any], table_name: str) -> Any:
    try:
        table = dataset[table_name]
    except KeyError as exc:
        raise ManifestSchemaError(f"{STAGE_ID}: {table_name} table이 없습니다") from exc

    if tuple(getattr(table, "columns", ())) != SOURCE_EXPECTED_COLUMNS[table_name]:
        raise ManifestSchemaError(f"{STAGE_ID}: {table_name} column 계약이 다릅니다")
    if not isinstance(getattr(table, "rows", None), tuple):
        raise ManifestSchemaError(f"{STAGE_ID}: {table_name} row 계약이 다릅니다")
    return table


def _validate_seed_contract() -> None:
    if len(DIM_PARAMETER_ROWS) != 8:
        raise ManifestSchemaError(f"{STAGE_ID}: seed는 정확히 8행이어야 합니다")

    parameter_ids = tuple(row.get("parameter_id") for row in DIM_PARAMETER_ROWS)
    if parameter_ids != PARAMETER_ORDER:
        raise ManifestSchemaError(f"{STAGE_ID}: parameter 순서·집합이 다릅니다")

    for index, row in enumerate(DIM_PARAMETER_ROWS):
        if tuple(row) != DIM_PARAMETER_COLUMNS or any(
            not isinstance(value, str) for value in row.values()
        ):
            raise ManifestSchemaError(
                f"{STAGE_ID}: seed {index + 1}행 column·문자열 계약이 다릅니다"
            )
        if any(unicodedata.normalize("NFC", value) != value for value in row.values()):
            raise ManifestSchemaError(f"{STAGE_ID}: seed 문자열은 NFC여야 합니다")
        if any("\u2212" in value for value in row.values()):
            raise ManifestSchemaError(f"{STAGE_ID}: 숫자 음수는 ASCII '-'여야 합니다")
        try:
            limits = tuple(Decimal(row[column]) for column in _LIMIT_COLUMNS)
        except InvalidOperation as exc:
            raise ManifestSchemaError(
                f"{STAGE_ID}: {row['parameter_id']} 한계선이 숫자가 아닙니다"
            ) from exc
        if limits != tuple(sorted(limits)):
            raise ManifestSchemaError(
                f"{STAGE_ID}: {row['parameter_id']} 한계선 순서가 다릅니다"
            )

    areas = [row["area"] for row in DIM_PARAMETER_ROWS]
    if areas != ["photo"] * 4 + ["etch"] * 4:
        raise ManifestSchemaError(f"{STAGE_ID}: area 4/4 순서 계약이 다릅니다")
    upper_only = tuple(
        row["parameter_id"] for row in DIM_PARAMETER_ROWS if row["upper_only"] == "true"
    )
    if upper_only != ("ET_REFL",) or any(
        row["upper_only"] not in {"true", "false"} for row in DIM_PARAMETER_ROWS
    ):
        raise ManifestSchemaError(f"{STAGE_ID}: upper_only 계약이 다릅니다")


def _validate_trace_parameters(trace: Any) -> None:
    trace_parameters = {row["parameter_id"] for row in trace.rows}
    if "" in trace_parameters:
        raise ManifestSchemaError(f"{STAGE_ID}: Trace parameter_id가 비어 있습니다")
    seed_parameters = set(PARAMETER_ORDER)
    if trace_parameters == seed_parameters:
        return

    missing_seed = sorted(trace_parameters - seed_parameters)
    unused_seed = sorted(seed_parameters - trace_parameters)
    details = []
    if missing_seed:
        details.append(f"seed 누락={missing_seed}")
    if unused_seed:
        details.append(f"Trace 미사용 seed={unused_seed}")
    raise ManifestSchemaError(
        f"{STAGE_ID}: Trace FK 양방향 집합이 다릅니다: {'; '.join(details)}"
    )


def _validate_existing_table(table: Any) -> None:
    if tuple(getattr(table, "columns", ())) != DIM_PARAMETER_COLUMNS:
        raise ManifestSchemaError(f"{STAGE_ID}: 기존 dim_parameter column이 다릅니다")
    rows = getattr(table, "rows", None)
    if not isinstance(rows, tuple) or rows != DIM_PARAMETER_ROWS:
        raise ManifestSchemaError(f"{STAGE_ID}: 기존 dim_parameter 값·순서가 다릅니다")


def apply(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """고정 seed와 Trace FK가 정확할 때만 dim_parameter를 추가한다."""

    trace = _require_source_table(dataset, "fdc_trace")
    _validate_seed_contract()
    _validate_trace_parameters(trace)

    existing = dataset.get("dim_parameter")
    if existing is not None:
        _validate_existing_table(existing)
        return {}

    return {
        "dim_parameter": replace(
            trace,
            columns=DIM_PARAMETER_COLUMNS,
            rows=DIM_PARAMETER_ROWS,
        )
    }
