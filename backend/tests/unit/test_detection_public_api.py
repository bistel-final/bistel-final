"""Detection 공개 API(`public_api.py`) 단위 테스트 — 호환 필수 3종 + 화면 확장 5종.

가짜 `Connection`(SQL 문자열은 기록만 하고 미리 준비한 행을 돌려준다)을 쓴다.
실제 PostgreSQL 없이 "SQL 조합 → DTO 조립" 경계만 검증한다 — join 결과 자체가
맞는지는 이 테스트의 책임이 아니다(실 DB 대조는 배포 환경 통합 테스트 몫이다).
다만 Runtime join이 **어느 테이블을 보는지**는 SQL 문자열로 고정한다: 그 join이
조용히 빠지면 `fault`·`action_code`·`notify`·`mes`가 영구히 null이 되는데,
그것이 V5-A-3.1 이전 scaffold 상태의 회귀이기 때문이다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.common.enums import AlarmSource
from app.detection import public_api
from app.detection.public_api import (
    DetectionQueryError,
    DetectionReadUnavailable,
    list_alarms,
    list_parameters,
    list_trace_points,
)
from app.detection.public_schemas import TraceSearchRequest


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _ScalarRow(_Rows):
    def scalar_one(self) -> object:
        return self._rows[0]


class _Connection:
    """`execute()`가 몇 번 불리든 같은 행 목록을 돌려주는 단순 fake."""

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


class _SequenceConnection:
    """`execute()`를 부를 때마다 미리 준비한 결과를 순서대로 하나씩 돌려준다.

    `scalar` 태그가 붙은 항목은 `COUNT(*)` 같은 단일 스칼라 조회용이다.
    """

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.statements: list[str] = []

    def execute(
        self, statement: object, params: dict[str, object] | None = None
    ) -> _Rows:
        self.statements.append(str(statement))
        item = self._script.pop(0)
        if isinstance(item, tuple) and item and item[0] == "scalar":
            return _ScalarRow(item[1])
        return _Rows(item)  # type: ignore[arg-type]


# ---------------------------------------------------------------------
# 1. 호환 필수 API — GET /alarms · /trace · /parameters
# ---------------------------------------------------------------------


def _summary_alarm_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
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
        # Runtime join (V5-A-3.1) — 분석 전에는 전부 비어 있다.
        "predicted_fault_code": None,
        "action_code": None,
        "notify_status": None,
        "mes_status": None,
    }
    row.update(overrides)
    return row


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


def test_alarm_query_reads_agent_runtime_join_sources() -> None:
    """V5-A-3.1 회귀 방지 — Runtime join이 조용히 빠지면 안 된다.

    C-5.2 scaffold는 이 네 테이블을 안 보고 예측·조치 필드를 전부 하드코딩된
    null로 두었다. join source가 사라지면 화면이 영구히 "분석 전"으로 보인다.
    """

    connection = _Connection([])
    list_alarms(connection)

    statement = connection.statement
    assert "public.agent_run_alarm" in statement
    assert "public.agent_prediction" in statement
    assert "public.action_history" in statement
    assert "email_delivery.channel = 'EMAIL'" in statement
    assert "mes_delivery.channel = 'MES_MOCK'" in statement


def test_alarm_runtime_projection_is_null_before_agent_analysis() -> None:
    """연결된 run·조치가 없으면 전부 null이다 — 에러가 아니라 정상 상태다."""

    alarms = list_alarms(_Connection([_summary_alarm_row()]))

    alarm = alarms[0]
    assert alarm.predicted_fault_code is None and alarm.fault is None
    assert alarm.action_code is None
    assert alarm.notify_status is None and alarm.notify is False
    assert alarm.mes_status is None and alarm.mes == ""


def test_alarm_runtime_projection_copies_linked_prediction_and_delivery() -> None:
    alarms = list_alarms(
        _Connection(
            [
                _summary_alarm_row(
                    predicted_fault_code="FOC",
                    action_code="EQP_HOLD",
                    notify_status="SENT",
                    mes_status="WAITING",
                )
            ]
        )
    )

    alarm = alarms[0]
    assert alarm.predicted_fault_code == "FOC"
    assert alarm.fault == alarm.predicted_fault_code
    assert alarm.action_code == "EQP_HOLD"
    assert alarm.notify_status == "SENT"
    assert alarm.notify is True
    assert alarm.mes_status == "WAITING"
    assert alarm.mes == "WAITING"


def test_alarm_runtime_projection_normalizes_blank_stored_values() -> None:
    """멘토 CSV는 "해당 없음"을 NULL이 아니라 빈 문자열로 적는 자리가 있다."""

    alarms = list_alarms(
        _Connection(
            [
                _summary_alarm_row(
                    predicted_fault_code="",
                    action_code="   ",
                    notify_status="",
                    mes_status="",
                )
            ]
        )
    )

    alarm = alarms[0]
    assert alarm.predicted_fault_code is None and alarm.fault is None
    assert alarm.action_code is None
    assert alarm.notify_status is None and alarm.notify is False
    assert alarm.mes_status is None and alarm.mes == ""


def test_alarm_projection_fails_closed_on_out_of_contract_delivery_status() -> None:
    """설계 §7.1에 없는 `EMAIL=BLOCKED` 조합은 값을 지어내지 않고 막는다."""

    with pytest.raises(DetectionReadUnavailable):
        list_alarms(_Connection([_summary_alarm_row(notify_status="BLOCKED")]))


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


# ---------------------------------------------------------------------
# 2. 선택 확장 API — 화면 1·2 (API v3 §5.2)
# ---------------------------------------------------------------------


def _trace_alarm_row(**overrides: object) -> dict[str, object]:
    row = {
        "source": "TRACE",
        "alarm_id": "TAL-0001",
        "occurred_at": datetime(2026, 8, 5, 7, 6, 42),
        "area": "etch",
        "equipment_id": "EQP05",
        "chamber_id": "EQP05-PM2",
        "parameter_id": "ET_CF4",
        "lot_hist_id": "LH-0001",
        "lot_id": "LOT002",
        "wafer_no": 3,
        "recipe_step_no": 1,
        "alarm_type": "OOS",
        "oos_point_cnt": 3,
        "ooc_point_cnt": 0,
        "value_mean": Decimal("74.500"),
        "value_min": Decimal("73.500"),
        "value_max": Decimal("75.900"),
        "action_id": "ACT-0001",
        "action_code": "WARNING",
        "approval_status": None,
        "latest_agent_run_id": "RUN-0001",
        "agent_run_status": "COMPLETED",
    }
    row.update(overrides)
    return row


def test_row_to_alarm_trace_builds_real_detail_and_rule_id() -> None:
    item = public_api._row_to_alarm(_trace_alarm_row())

    assert item.source.value == "TRACE"
    assert item.rule_id == "R01_OOS"
    assert item.judgement == "OOS"
    assert item.hit_cnt == 3
    # detail의 mean/min/max는 TraceModel.jsx의 detailNumbers() 정규식
    # (`mean\s+(-?[0-9.]+)`)이 그대로 역파싱할 수 있는 형태여야 한다.
    assert "mean 74.500" in item.detail
    assert "min 73.500" in item.detail
    assert "max 75.900" in item.detail
    assert item.incident.lot_id == item.lot_id == "LOT002"
    assert item.incident.chamber_id == item.chamber_id == "EQP05-PM2"
    assert item.area == "Etch"
    assert item.occurred_at.isoformat().endswith("+09:00")


def test_row_to_alarm_summary_omits_min_max() -> None:
    row = _trace_alarm_row(
        source="SUMMARY",
        alarm_id="SAL-0044",
        alarm_type="OOC",
        oos_point_cnt=None,
        ooc_point_cnt=2,
        value_min=None,
        value_max=None,
    )
    item = public_api._row_to_alarm(row)

    assert item.rule_id == "R02_OOC"
    assert item.judgement == "OOC"
    assert item.hit_cnt == 2
    assert "mean 74.500" in item.detail
    assert "min" not in item.detail
    assert "max" not in item.detail


def test_row_to_alarm_r03_has_fixed_hit_count_and_no_leaked_join() -> None:
    row = _trace_alarm_row(
        source="R03",
        alarm_id="R03-0001",
        oos_point_cnt=None,
        ooc_point_cnt=None,
        value_mean=None,
        value_min=None,
        value_max=None,
    )
    item = public_api._row_to_alarm(row)

    assert item.rule_id == "R03_CONSEC"
    assert item.judgement == "OOS"
    assert item.hit_cnt == 3
    assert item.detail == "OOS for 3 consecutive WAFER at STEP1"


def test_row_to_alarm_without_agent_linkage_is_all_none() -> None:
    """아직 Agent runtime을 안 거친 incident는 action·approval·run이 전부
    None이어야 한다 — 에러가 아니라 정상 상태다(public_api.py 모듈 docstring)."""

    row = _trace_alarm_row(
        action_id=None,
        action_code=None,
        approval_status=None,
        latest_agent_run_id=None,
        agent_run_status=None,
    )
    item = public_api._row_to_alarm(row)

    assert item.action_id is None
    assert item.action_code is None
    assert item.latest_agent_run_id is None
    assert item.agent_run_status is None


def test_get_alarms_page_returns_total_and_items() -> None:
    connection = _SequenceConnection([("scalar", [3]), [_trace_alarm_row()]])

    page = public_api.get_alarms_page(connection, page=1, size=20)

    assert page.total == 3
    assert page.page == 1
    assert page.size == 20
    assert len(page.items) == 1
    assert "LIMIT :limit OFFSET :offset" in connection.statements[-1]


@pytest.mark.parametrize(("page", "size"), [(0, 20), (1, 0), (1, 101)])
def test_get_alarms_page_rejects_out_of_range_paging(page: int, size: int) -> None:
    connection = _SequenceConnection([])

    with pytest.raises(DetectionQueryError):
        public_api.get_alarms_page(connection, page=page, size=size)


def test_get_alarms_page_rejects_invalid_judgement() -> None:
    connection = _SequenceConnection([])

    with pytest.raises(DetectionQueryError):
        public_api.get_alarms_page(connection, page=1, size=20, judgement="IN")


def test_get_alarm_detail_returns_none_when_not_found() -> None:
    connection = _SequenceConnection([[]])

    result = public_api.get_alarm_detail(
        connection, source=public_api.AlarmSource.TRACE, alarm_id="TAL-9999"
    )

    assert result is None


def test_get_alarm_detail_found_matches_list_projection() -> None:
    connection = _SequenceConnection([[_trace_alarm_row()]])

    result = public_api.get_alarm_detail(
        connection, source=public_api.AlarmSource.TRACE, alarm_id="TAL-0001"
    )

    assert result is not None
    assert result.alarm_id == "TAL-0001"
    assert result.rule_id == "R01_OOS"


def test_get_dashboard_summary_assembles_hierarchy_kpi_and_trend() -> None:
    hierarchy_rows = [
        {"area_id": "etch", "equipment_id": "EQP05", "chamber_id": "EQP05-PM2"},
        {"area_id": "etch", "equipment_id": "EQP05", "chamber_id": "EQP05-PM1"},
    ]
    sensor_rows = [
        {
            "parameter_id": "ET_CF4",
            "parameter_name": "CF4 flow",
            "unit": "sccm",
            "target_value": Decimal("74"),
            "spec_lower": Decimal("70"),
            "ctrl_lower": Decimal("72"),
            "ctrl_upper": Decimal("76"),
            "spec_upper": Decimal("78"),
            "upper_only": False,
        }
    ]
    connection = _SequenceConnection(
        [[_trace_alarm_row()], hierarchy_rows, sensor_rows, []]
    )

    summary = public_api.get_dashboard_summary(connection)

    assert summary.alarm_count == 1
    assert summary.oos_count == 1
    assert summary.ooc_count == 0
    assert len(summary.hierarchy) == 1
    assert set(summary.hierarchy[0].chambers) == {"EQP05-PM2", "EQP05-PM1"}
    assert summary.daily_trend[0].oos_count == 1
    assert summary.top_sensors[0].sensor_id == "ET_CF4"
    # 알람이 없는 챔버(EQP05-PM1)도 0건으로 hierarchy에서 나와야 한다.
    equipment = summary.equipment_counts[0]
    chamber_counts = {c.chamber_id: c.alarm_count for c in equipment.chambers}
    assert chamber_counts == {"EQP05-PM2": 1, "EQP05-PM1": 0}
    assert summary.pending_approvals == []
    assert len(summary.recent_alarms) == 1
    pending_approval_query = connection.statements[3]
    assert "COALESCE(" in pending_approval_query
    assert "incident_equipment.equipment_id" in pending_approval_query
    assert "history.lot_id = ah.lot_id" in pending_approval_query
    assert "history.chamber_id = ah.chamber_id" in pending_approval_query


def test_get_dashboard_summary_wraps_db_failure() -> None:
    from sqlalchemy.exc import SQLAlchemyError

    class _BoomConnection:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise SQLAlchemyError("boom")

    with pytest.raises(DetectionReadUnavailable):
        public_api.get_dashboard_summary(_BoomConnection())


def test_get_trace_catalog_groups_equipment_and_lots() -> None:
    hierarchy_rows = [
        {"area_id": "photo", "equipment_id": "EQP01", "chamber_id": "EQP01-PM1"},
    ]
    sensor_rows = [
        {
            "parameter_id": "PH_FOCUS",
            "parameter_name": "Focus",
            "unit": "nm",
            "target_value": Decimal("50"),
            "spec_lower": Decimal("40"),
            "ctrl_lower": Decimal("45"),
            "ctrl_upper": Decimal("55"),
            "spec_upper": Decimal("60"),
            "upper_only": False,
        }
    ]
    recipe_rows = [{"area_id": "photo", "recipe_id": "RECIPE01"}]
    lot_rows = [{"lot_id": "LOT001", "wafer_nos": [1, 2, 3]}]
    connection = _SequenceConnection(
        [hierarchy_rows, sensor_rows, recipe_rows, lot_rows]
    )

    catalog = public_api.get_trace_catalog(connection)

    assert catalog.areas[0].area_id == "Photo"
    assert catalog.equipments[0].chambers == ["EQP01-PM1"]
    assert catalog.sensors[0].sensor_id == "PH_FOCUS"
    assert catalog.recipes[0].recipe_id == "RECIPE01"
    assert catalog.lots[0].wafer_nos == [1, 2, 3]


def test_search_traces_groups_points_by_wafer_and_sensor() -> None:
    lot_rows = [
        {
            "lot_hist_id": "LH-0001",
            "lot_id": "LOT002",
            "wafer_no": 3,
            "area_id": "etch",
            "equipment_id": "EQP05",
            "chamber_id": "EQP05-PM2",
            "recipe_id": "RECIPE03",
            "track_in_at": datetime(2026, 8, 5, 7, 0, 0),
        }
    ]
    sensor_rows = [
        {
            "parameter_id": "ET_CF4",
            "parameter_name": "CF4 flow",
            "unit": "sccm",
            "target_value": Decimal("74"),
            "spec_lower": Decimal("70"),
            "ctrl_lower": Decimal("72"),
            "ctrl_upper": Decimal("76"),
            "spec_upper": Decimal("78"),
            "upper_only": False,
        }
    ]
    trace_rows = [
        {
            "lot_hist_id": "LH-0001",
            "parameter_id": "ET_CF4",
            "recipe_step_no": 1,
            "seq_no": 0,
            "measured_at": datetime(2026, 8, 5, 7, 0, 5),
            "value": Decimal("74.1"),
        },
        {
            "lot_hist_id": "LH-0001",
            "parameter_id": "ET_CF4",
            "recipe_step_no": 1,
            "seq_no": 1,
            "measured_at": datetime(2026, 8, 5, 7, 0, 10),
            "value": Decimal("74.3"),
        },
    ]
    connection = _SequenceConnection([lot_rows, sensor_rows, trace_rows])

    result = public_api.search_traces(
        connection, TraceSearchRequest(chamber_id="EQP05-PM2")
    )

    assert result.total == 1
    wafer = result.wafers[0]
    assert wafer.lot_id == "LOT002"
    assert wafer.wafer_no == 3
    assert len(wafer.points) == 2
    assert "ET_CF4" in result.limits


def test_search_traces_returns_empty_when_no_lot_matches() -> None:
    connection = _SequenceConnection([[], [
        {
            "parameter_id": "ET_CF4",
            "parameter_name": "CF4 flow",
            "unit": "sccm",
            "target_value": Decimal("74"),
            "spec_lower": Decimal("70"),
            "ctrl_lower": Decimal("72"),
            "ctrl_upper": Decimal("76"),
            "spec_upper": Decimal("78"),
            "upper_only": False,
        }
    ]])

    result = public_api.search_traces(
        connection, TraceSearchRequest(lot_id="LOT-NOPE")
    )

    assert result.total == 0
    assert result.wafers == []


def test_trace_search_request_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        TraceSearchRequest(
            **{"from": datetime(2026, 8, 5), "to": datetime(2026, 8, 1)}
        )
