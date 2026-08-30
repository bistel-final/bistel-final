"""Detection core public read model.

API v3의 canonical DTO만 이 경계에서 조립한다. 물리 CSV 호환 이름과 공개
alias는 SQL이나 Router에서 따로 재구성하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.common.enums import AlarmSource
from app.detection.public_schemas import AlarmItem, ParameterItem, TracePoint

KST = ZoneInfo("Asia/Seoul")


class DetectionReadUnavailable(RuntimeError):
    """공개 조회 결과를 안전하게 만들 수 없거나 DB 조회가 실패했다."""


class DetectionQueryError(ValueError):
    """서로 의존하는 query 값이 API 계약을 위반했다."""


def _kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _canonical_area(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized == "photo":
        return "Photo"
    if normalized == "etch":
        return "Etch"
    raise ValueError("알 수 없는 area 값입니다")


def _float(value: Decimal | int | float | None) -> float | None:
    return None if value is None else float(value)


def _optional_filter(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def list_alarms(
    connection: Connection,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    area: str | None = None,
    equipment: str | None = None,
    chamber: str | None = None,
    parameter: str | None = None,
    source: AlarmSource | None = None,
    include_derived: bool = False,
) -> list[AlarmItem]:
    """저장 알람을 API v3 안정 순서로 조회한다."""

    if (date_from is None) != (date_to is None):
        raise DetectionQueryError("date_from과 date_to는 함께 사용해야 합니다")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise DetectionQueryError("date_from은 date_to보다 늦을 수 없습니다")

    clauses: list[str] = []
    params: dict[str, object] = {}
    if source is not None:
        clauses.append("v.source = :source")
        params["source"] = source.value
    elif not include_derived:
        clauses.append("v.source IN ('TRACE', 'SUMMARY')")

    if date_from is not None and date_to is not None:
        clauses.extend(
            ["v.occurred_at >= :date_from", "v.occurred_at < :date_to_exclusive"]
        )
        params.update(
            {
                "date_from": datetime.combine(date_from, datetime.min.time()),
                "date_to_exclusive": datetime.combine(
                    date_to + timedelta(days=1), datetime.min.time()
                ),
            }
        )

    normalized_area = None if area in {None, "ALL"} else area
    filters = (
        ("area", normalized_area),
        ("equipment_id", _optional_filter(equipment)),
        ("chamber_id", _optional_filter(chamber)),
        ("parameter_id", _optional_filter(parameter)),
    )
    for column, value in filters:
        if value is None:
            continue
        if column == "area":
            clauses.append("lower(v.area) = lower(:area)")
        else:
            clauses.append(f"v.{column} = :{column}")
        params[column] = value

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    statement = text(
        f"""
        SELECT
            v.source, v.alarm_id, v.occurred_at, v.area,
            v.equipment_id, v.chamber_id, v.parameter_id, v.recipe_id,
            v.lot_id, v.wafer_id, v.recipe_step_no, v.seq_no,
            v.value, v.alarm_type, v.rule_code,
            s.statistic_type, s.cl, s.ucl, s.lcl
        FROM public.v_alarm_event AS v
        LEFT JOIN public.summary_alarm_history AS s
          ON v.source = 'SUMMARY' AND s.alarm_id = v.alarm_id
        {where}
        ORDER BY v.occurred_at DESC, v.source ASC, v.alarm_id DESC
        """
    )

    try:
        rows = connection.execute(statement, params).mappings().all()
        # TODO(V5-A-3.1): C-5.2의 화면 조립용 scaffold는 Runtime join을
        # 소유하지 않는다. A가 agent_prediction·action·delivery를 연결해
        # predicted_fault_code/fault, action_code, notify_status/notify,
        # mes_status/mes를 최종 projection으로 승계할 때까지 명시적
        # "연결 안 됨"으로 둔다. 이 route의 존재는 A-3.1 완료가 아니다.
        return [
            AlarmItem(
                source=row["source"],
                alarm_id=row["alarm_id"],
                occurred_at=_kst(row["occurred_at"]),
                area=_canonical_area(row["area"]),
                equipment_id=row["equipment_id"],
                equipment=row["equipment_id"],
                chamber_id=row["chamber_id"],
                chamber=row["chamber_id"],
                recipe_id=row["recipe_id"],
                recipe=row["recipe_id"],
                lot_id=row["lot_id"],
                lot=row["lot_id"],
                wafer_id=row["wafer_id"],
                wafer=row["wafer_id"],
                parameter_id=row["parameter_id"],
                parameter=row["parameter_id"],
                recipe_step_no=row["recipe_step_no"],
                step_no=row["recipe_step_no"],
                seq_no=row["seq_no"],
                alarm_type=row["alarm_type"],
                value=_float(row["value"]),
                rule_code=row["rule_code"],
                predicted_fault_code=None,
                fault=None,
                action_code=None,
                notify_status=None,
                notify=False,
                mes_status=None,
                mes="",
                statistic_type=row["statistic_type"],
                cl=_float(row["cl"]),
                ucl=_float(row["ucl"]),
                lcl=_float(row["lcl"]),
            )
            for row in rows
        ]
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc


def list_trace_points(
    connection: Connection,
    *,
    lot: str,
    wafer: str,
    chamber: str,
    parameter: str,
) -> list[TracePoint]:
    """한 WAFER·parameter의 raw Trace를 시간·step·seq 안정 순서로 조회한다."""

    statement = text(
        """
        SELECT t.recipe_step_no, t.seq_no, t.measured_at, t.value
        FROM public.fdc_trace AS t
        JOIN public.lot_history AS h ON h.lot_hist_id = t.lot_hist_id
        WHERE h.lot_id = :lot
          AND h.wafer_id = :wafer
          AND h.chamber_id = :chamber
          AND t.parameter_id = :parameter
        ORDER BY t.measured_at ASC, t.recipe_step_no ASC, t.seq_no ASC
        """
    )
    try:
        rows = (
            connection.execute(
                statement,
                {
                    "lot": lot,
                    "wafer": wafer,
                    "chamber": chamber,
                    "parameter": parameter,
                },
            )
            .mappings()
            .all()
        )
        return [
            TracePoint(
                recipe_step_no=row["recipe_step_no"],
                seq_no=row["seq_no"],
                measured_at=_kst(row["measured_at"]),
                value=float(row["value"]),
            )
            for row in rows
        ]
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc


def list_parameters(connection: Connection) -> list[ParameterItem]:
    """8개 parameter 기준정보를 canonical area·alias와 함께 반환한다."""

    statement = text(
        """
        SELECT parameter_id, parameter_name, unit, area,
               target_value, spec_lower, ctrl_lower, ctrl_upper, spec_upper,
               upper_only
        FROM public.dim_parameter
        ORDER BY lower(area) ASC, parameter_id ASC
        """
    )
    try:
        rows = connection.execute(statement).mappings().all()
        return [
            ParameterItem(
                parameter_id=row["parameter_id"],
                parameter_name=row["parameter_name"],
                name=row["parameter_name"],
                area=_canonical_area(row["area"]),
                unit=row["unit"],
                spec_lower=_float(row["spec_lower"]),
                LSL=_float(row["spec_lower"]),
                ctrl_lower=_float(row["ctrl_lower"]),
                LCL=_float(row["ctrl_lower"]),
                target_value=float(row["target_value"]),
                TARGET=float(row["target_value"]),
                ctrl_upper=float(row["ctrl_upper"]),
                UCL=float(row["ctrl_upper"]),
                spec_upper=float(row["spec_upper"]),
                USL=float(row["spec_upper"]),
                upper_only=bool(row["upper_only"]),
            )
            for row in rows
        ]
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc
