from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.common.enums import AlarmSource
from app.detection.public_api import (
    DetectionQueryError,
    list_alarms,
    list_parameters,
    list_trace_points,
)


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.statement = ""
        self.params: dict[str, object] = {}

    def execute(
        self,
        statement: object,
        params: dict[str, object] | None = None,
    ) -> _Rows:
        self.statement = str(statement)
        self.params = params or {}
        return _Rows(self.rows)


def _summary_alarm_row() -> dict[str, object]:
    return {
        "source": "SUMMARY",
        "alarm_id": "SAL-0044",
        "occurred_at": datetime(2026, 8, 5, 7, 6, 42),
        "area": "etch",
        "equipment_id": "EQP05",
        "chamber_id": "EQP05-PM2",
        "parameter_id": "ET_CF4",
        "recipe_id": "RECIPE03",
        "lot_id": "LOT002",
        "wafer_id": "LOT002W010",
        "recipe_step_no": 2,
        "seq_no": None,
        "value": Decimal("77.3710"),
        "alarm_type": "OOC",
        "rule_code": "SUMMARY_OOC",
        "statistic_type": "mean",
        "cl": Decimal("70.0"),
        "ucl": Decimal("75.0"),
        "lcl": Decimal("65.0"),
    }


def test_alarm_projection_is_canonical_offset_aware_and_stably_queried() -> None:
    connection = _Connection([_summary_alarm_row()])

    alarms = list_alarms(connection, source=AlarmSource.SUMMARY)

    assert len(alarms) == 1
    alarm = alarms[0]
    assert alarm.alarm_id == "SAL-0044"
    assert alarm.occurred_at.isoformat().endswith("+09:00")
    assert alarm.area == "Etch"
    assert alarm.wafer == alarm.wafer_id == "LOT002W010"
    assert alarm.parameter == alarm.parameter_id == "ET_CF4"
    assert alarm.statistic_type == "mean"
    assert alarm.notify is False and alarm.mes == ""
    assert (
        "ORDER BY v.occurred_at DESC, v.source ASC, v.alarm_id DESC"
        in connection.statement
    )
    assert connection.params["source"] == "SUMMARY"


def test_alarm_default_excludes_derived_and_date_pair_is_atomic() -> None:
    connection = _Connection([])

    assert list_alarms(connection) == []
    assert "v.source IN ('TRACE', 'SUMMARY')" in connection.statement

    with pytest.raises(DetectionQueryError, match="함께"):
        list_alarms(connection, date_from=date(2026, 8, 1))


def test_trace_projection_preserves_core_shape_and_order_contract() -> None:
    connection = _Connection(
        [
            {
                "recipe_step_no": 1,
                "seq_no": 0,
                "measured_at": datetime(2026, 8, 5, 7, 6, 42),
                "value": Decimal("80.6970"),
            }
        ]
    )

    points = list_trace_points(
        connection,
        lot="LOT002",
        wafer="LOT002W010",
        chamber="EQP05-PM2",
        parameter="ET_CF4",
    )

    assert points[0].model_dump().keys() == {
        "seq_no",
        "recipe_step_no",
        "measured_at",
        "value",
    }
    assert points[0].measured_at.isoformat().endswith("+09:00")
    assert (
        "ORDER BY t.measured_at ASC, t.recipe_step_no ASC, t.seq_no ASC"
        in connection.statement
    )


def test_parameter_projection_builds_exact_compatibility_aliases() -> None:
    connection = _Connection(
        [
            {
                "parameter_id": "ET_CF4",
                "parameter_name": "CF4 Flow",
                "unit": "sccm",
                "area": "etch",
                "target_value": Decimal("80"),
                "spec_lower": Decimal("60"),
                "ctrl_lower": Decimal("68"),
                "ctrl_upper": Decimal("92"),
                "spec_upper": Decimal("100"),
                "upper_only": False,
            }
        ]
    )

    parameters = list_parameters(connection)

    assert parameters[0].name == parameters[0].parameter_name == "CF4 Flow"
    assert parameters[0].TARGET == parameters[0].target_value == 80.0
    assert parameters[0].UCL == parameters[0].ctrl_upper == 92.0
    assert "ORDER BY lower(area) ASC, parameter_id ASC" in connection.statement
