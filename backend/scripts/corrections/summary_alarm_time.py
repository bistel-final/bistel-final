"""Summary 알람의 빈 occurred_at을 대응 lot의 track-in으로 보정한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from manifest_v3 import SOURCE_EXPECTED_COLUMNS, ManifestSchemaError

STAGE_ID = "summary_alarm_time"
STAGE_VERSION = "1"


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


def _lot_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return row["lot_id"], row["wafer_no"], row["chamber_id"]


def _summary_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return row["lot"], row["wafer"], row["chamber"]


def _key_label(key: tuple[str, str, str]) -> str:
    lot_id, wafer_no, chamber_id = key
    return f"lot={lot_id[:32]}, wafer={wafer_no[:16]}, chamber={chamber_id[:32]}"


def _build_lot_index(lot_history: Any) -> dict[tuple[str, str, str], dict[str, str]]:
    index: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in lot_history.rows:
        key = _lot_key(row)
        if any(not value.strip() for value in key):
            raise ManifestSchemaError(
                f"{STAGE_ID}: lot_history 매칭 key가 비어 있습니다"
            )
        if key in index:
            raise ManifestSchemaError(
                f"{STAGE_ID}: lot_history 매칭 key가 중복됐습니다: {_key_label(key)}"
            )
        index[key] = row
    return index


def apply(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """빈 시각만 채우며 기존 비어 있지 않은 충돌값은 덮어쓰지 않는다."""

    summary = _require_source_table(dataset, "summary_alarm_history")
    lot_history = _require_source_table(dataset, "lot_history")
    lot_index = _build_lot_index(lot_history)

    changed = False
    corrected_rows = []
    for row in summary.rows:
        key = _summary_key(row)
        if any(not value.strip() for value in key):
            raise ManifestSchemaError(f"{STAGE_ID}: Summary 매칭 key가 비어 있습니다")
        lot = lot_index.get(key)
        if lot is None:
            raise ManifestSchemaError(
                f"{STAGE_ID}: Summary와 매칭되는 lot_history가 없습니다: "
                f"{_key_label(key)}"
            )
        if row["area"] != lot["area_id"]:
            raise ManifestSchemaError(
                f"{STAGE_ID}: Summary area가 lot_history와 다릅니다: {_key_label(key)}"
            )
        if row["equipment"] != lot["equipment_id"]:
            raise ManifestSchemaError(
                f"{STAGE_ID}: Summary equipment가 lot_history와 다릅니다: "
                f"{_key_label(key)}"
            )

        expected = lot["track_in_at"]
        if not expected.strip():
            raise ManifestSchemaError(
                f"{STAGE_ID}: lot_history track_in_at이 비어 있습니다: "
                f"{_key_label(key)}"
            )
        occurred_at = row["occurred_at"]
        if occurred_at.strip():
            if occurred_at != expected:
                raise ManifestSchemaError(
                    f"{STAGE_ID}: 기존 occurred_at이 track_in_at과 다릅니다: "
                    f"{_key_label(key)}"
                )
            corrected_rows.append(row)
            continue

        corrected = dict(row)
        corrected["occurred_at"] = expected
        corrected_rows.append(corrected)
        changed = True

    if not changed:
        return {}
    return {"summary_alarm_history": replace(summary, rows=tuple(corrected_rows))}
