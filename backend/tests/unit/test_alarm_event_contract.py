"""v_alarm_event 평면 row와 AlarmItem 중첩 DTO 사이 adapter 계약을 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.common.schemas import AlarmRef, IncidentRef
from app.detection.schemas import AlarmItem

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SQL = (
    REPOSITORY_ROOT / "backend" / "migrations" / "001_reference_extensions.sql"
).read_text(encoding="utf-8")


def _row(source: str) -> dict[str, Any]:
    return {
        "source": source,
        "alarm_id": f"{source}-001",
        "occurred_at": datetime(2026, 8, 1, tzinfo=UTC),
        "area": "photo",
        "equipment_id": "EQP01",
        "chamber_id": "EQP01-PM1",
        "parameter_id": "PH_DOSE",
        "recipe_id": "RECIPE01",
        "lot_id": "LOT001",
        "wafer_no": 1,
        "recipe_step_no": 1,
        "seq_no": 0 if source == "TRACE" else None,
        "value": 10.0 if source != "R03" else None,
        "alarm_type": "OOS" if source in {"TRACE", "R03"} else "OOC",
        "lot_hist_id": "LH-00001",
    }


def _adapt(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    source = payload.pop("source")
    alarm_id = payload.pop("alarm_id")
    seq_no = payload.pop("seq_no")
    payload["alarm"] = AlarmRef(source=source, alarm_id=alarm_id)
    payload["incident"] = IncidentRef(
        lot_id=payload["lot_id"], chamber_id=payload["chamber_id"]
    )
    payload["source_detail"] = {"seq_no": seq_no}
    return payload


@pytest.mark.parametrize("source", ["TRACE", "SUMMARY", "R03"])
def test_each_view_branch_validates_after_adapter(source: str) -> None:
    item = AlarmItem.model_validate(_adapt(_row(source)))

    assert item.alarm.source.value == source
    assert item.incident.lot_id == "LOT001"


def test_flat_row_is_not_mistaken_for_api_payload() -> None:
    with pytest.raises(ValidationError) as raised:
        AlarmItem.model_validate(_row("TRACE"))

    errors = {(error["loc"], error["type"]) for error in raised.value.errors()}
    assert (("alarm",), "missing") in errors
    assert (("incident",), "missing") in errors
    assert (("source",), "extra_forbidden") in errors
    assert (("alarm_id",), "extra_forbidden") in errors
    assert (("seq_no",), "extra_forbidden") in errors


def test_r03_is_source_and_oos_is_alarm_type() -> None:
    assert "'R03'::varchar(10) AS source" in SQL
    assert "'OOS'::varchar(10) AS alarm_type" in SQL
    assert "'R03'::varchar(10) AS alarm_type" not in SQL


def test_view_uses_union_all_and_left_join_for_stored_alarms() -> None:
    assert SQL.count("UNION ALL") == 2
    assert SQL.count("LEFT JOIN lot_history") == 2
