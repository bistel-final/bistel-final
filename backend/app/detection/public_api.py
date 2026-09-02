"""Detection 공개 read model — 호환 필수 3종 + 화면 확장 5종.

Router가 바로 반환할 수 있는 완성된 DTO를 이 경계에서 조립하고, SQL도 이 파일이
직접 낸다(`repository.py`는 V5-A-1.x 배치 재계산·검증 전용이라 HTTP read path가
쓰지 않는다). 물리 CSV 호환 이름과 공개 alias는 SQL이나 Router에서 따로
재구성하지 않는다.

담는 endpoint는 두 묶음이다.

1. **호환 필수** `GET /alarms`·`/trace`·`/parameters` (V5-A-3.1·V5-A-3.2)
   멘토 기준 public 필수 계약이다. API v3 §2.7이 고정한 deprecated React alias를
   canonical field와 함께 내보낸다.

2. **선택 확장** `GET /dashboard/summary`·`/alarms/paged`·
   `/alarms/{source}/{alarm_id}`·`/traces/catalog`·`POST /traces/search`
   (V5-A-3.3·V5-A-3.4·V5-A-3.5). API 명세 v3 §5.2다 — 화면 1(알람 대시보드)·
   화면 2(알람 히스토리)가 실제로 그리는 조치·승인·Agent run 연결 정보까지 담는다.

조치·승인·예측은 C가 소유한 `action_history`·`agent_run`·`agent_prediction`·
`action_delivery` runtime table을 **읽기만** 한다(쓰기는 C의 서비스만 한다).
해당 incident·알람이 아직 Agent runtime을 안 거쳤으면 관련 필드는 전부 None이다
— 정상 상태이지 에러가 아니다.

Runtime join 규칙 (API v3 §2.7 파생 규칙 표):
- `predicted_fault_code`/`fault`는 **연결된 Agent 예측이 있을 때만** 그 값이다.
  알람→run 연결은 `agent_run_alarm`(알람 단위 명시 링크)을 쓰고, 한 알람이 여러
  번 분석됐으면 가장 최근 run(`agent_run.started_at DESC`)을 쓴다. §2.7이
  `lot_history.fault_code`나 parameter→Fault 고정표에서 만드는 것을 명시적으로
  금지하므로 그 경로는 쓰지 않는다.
- `action_code`·`notify_status`·`mes_status`는 조치 단위다. 스키마 주석이
  "알람은 WAFER 단위, 조치는 (lot,chamber) incident 단위"로 못박았으므로
  `(lot_id, chamber_id)` incident의 최신 `action_history` 행에서 가져온다.
- 통지·MES 상태는 runtime `action_delivery`(EMAIL·MES_MOCK)를 먼저 보고, 없으면
  멘토 base table `action_history`의 같은 이름 컬럼으로 떨어진다. 설계 §7.1이
  EMAIL을 `WAITING|SENDING|SENT|FAILED|UNKNOWN`으로, MES_MOCK을 `BLOCKED`까지
  포함한 전체 상태로 고정했고 이는 공개 계약의 두 enum과 정확히 일치한다.
  범위를 벗어난 값은 만들어 덮지 않고 DTO 검증에서 fail-closed로 막는다.
- 저장 값이 빈 문자열이면(멘토 CSV의 `''` 관행) "연결 안 됨"인 None으로
  정규화한다. 값을 창작하지 않는다(NFR-19).
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

from app.common.enums import (
    ActionCode,
    AlarmSource,
    DeliveryStatus,
    resolve_severity,
)
from app.detection.public_schemas import (
    RULE_ID_BY_SOURCE,
    AlarmDetailResponse,
    AlarmItem,
    AlarmPageEnvelope,
    ChamberAlarmCount,
    DailyTrendItem,
    DashboardSummaryResponse,
    EquipmentCountItem,
    HierarchyNode,
    Judgement,
    ParameterItem,
    PendingApprovalItem,
    RuleId,
    ScreenAlarmItem,
    TopSensorItem,
    TraceCatalogArea,
    TraceCatalogEquipment,
    TraceCatalogLot,
    TraceCatalogRecipe,
    TraceCatalogResponse,
    TraceCatalogSensor,
    TracePoint,
    TraceSearchPoint,
    TraceSearchRequest,
    TraceSearchResponse,
    TraceSearchWafer,
)

KST = ZoneInfo("Asia/Seoul")

__all__ = [
    "DetectionQueryError",
    "DetectionReadUnavailable",
    "list_alarms",
    "list_trace_points",
    "list_parameters",
    "get_dashboard_summary",
    "get_alarms_page",
    "get_alarm_detail",
    "get_trace_catalog",
    "search_traces",
]


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


def _optional(value: Any) -> str | None:
    """빈 문자열·공백뿐인 값을 "연결 안 됨"(None)으로 정규화한다.

    query filter와 저장 값 양쪽에 쓴다. 멘토 CSV는 "해당 없음"을 NULL이 아니라
    빈 문자열로 적는 자리가 있고(`action_history.notify_status`의 `SENT|''`),
    공개 계약의 enum에는 빈 문자열이 없다. 임의 기본값으로 채우지 않고 None으로
    떨어뜨리는 것이 §2.7 파생 규칙("없으면 null")과 같은 처리다.
    """

    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


# ---------------------------------------------------------------------
# 1. 호환 필수 API — GET /alarms · /trace · /parameters
# ---------------------------------------------------------------------


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
    """저장 알람을 API v3 안정 순서로 조회한다.

    Runtime projection(`predicted_fault_code`/`fault`, `action_code`,
    `notify_status`/`notify`, `mes_status`/`mes`)은 모듈 docstring의 join 규칙을
    따른다. 연결된 Agent run·조치가 없으면 전부 null이며 이는 정상 상태다.
    """

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
        ("equipment_id", _optional(equipment)),
        ("chamber_id", _optional(chamber)),
        ("parameter_id", _optional(parameter)),
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
        WITH alarm_run AS (
            SELECT DISTINCT ON (ra.alarm_source, ra.alarm_id)
                ra.alarm_source, ra.alarm_id, ra.agent_run_id
            FROM public.agent_run_alarm AS ra
            JOIN public.agent_run AS r ON r.agent_run_id = ra.agent_run_id
            ORDER BY ra.alarm_source, ra.alarm_id,
                     r.started_at DESC, ra.agent_run_id DESC
        ),
        incident_action AS (
            SELECT DISTINCT ON (ah.lot_id, ah.chamber_id)
                ah.lot_id, ah.chamber_id, ah.action_id, ah.action_code,
                ah.notify_status, ah.mes_status
            FROM public.action_history AS ah
            ORDER BY ah.lot_id, ah.chamber_id,
                     ah.created_at DESC NULLS LAST, ah.action_id DESC
        )
        SELECT
            v.source, v.alarm_id, v.occurred_at, v.area,
            v.equipment_id, v.chamber_id, v.parameter_id, v.recipe_id,
            v.lot_id, v.wafer_id, v.recipe_step_no, v.seq_no,
            v.value, v.alarm_type, v.rule_code,
            s.statistic_type, s.cl, s.ucl, s.lcl,
            p.predicted_fault_code,
            ia.action_code,
            COALESCE(email_delivery.status, ia.notify_status) AS notify_status,
            COALESCE(mes_delivery.status, ia.mes_status) AS mes_status
        FROM public.v_alarm_event AS v
        LEFT JOIN public.summary_alarm_history AS s
          ON v.source = 'SUMMARY' AND s.alarm_id = v.alarm_id
        LEFT JOIN alarm_run AS ar
          ON ar.alarm_source = v.source AND ar.alarm_id = v.alarm_id
        LEFT JOIN public.agent_prediction AS p
          ON p.agent_run_id = ar.agent_run_id
        LEFT JOIN incident_action AS ia
          ON ia.lot_id = v.lot_id AND ia.chamber_id = v.chamber_id
        LEFT JOIN public.action_delivery AS email_delivery
          ON email_delivery.action_id = ia.action_id
         AND email_delivery.channel = 'EMAIL'
        LEFT JOIN public.action_delivery AS mes_delivery
          ON mes_delivery.action_id = ia.action_id
         AND mes_delivery.channel = 'MES_MOCK'
        {where}
        ORDER BY v.occurred_at DESC, v.source ASC, v.alarm_id DESC
        """
    )

    try:
        rows = connection.execute(statement, params).mappings().all()
        return [_row_to_public_alarm(row) for row in rows]
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc


def _row_to_public_alarm(row: Any) -> AlarmItem:
    """`v_alarm_event` + Runtime join 한 행을 compat `AlarmItem`으로 옮긴다."""

    predicted = _optional(row["predicted_fault_code"])
    notify_status = _optional(row["notify_status"])
    mes_status = _optional(row["mes_status"])
    return AlarmItem(
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
        predicted_fault_code=predicted,
        fault=predicted,
        action_code=_optional(row["action_code"]),
        notify_status=notify_status,
        notify=notify_status == DeliveryStatus.SENT.value,
        mes_status=mes_status,
        mes=mes_status or "",
        statistic_type=row["statistic_type"],
        cl=_float(row["cl"]),
        ucl=_float(row["ucl"]),
        lcl=_float(row["lcl"]),
    )


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


# ---------------------------------------------------------------------
# 2. 선택 확장 API — 화면 1·2 전용 (API v3 §5.2)
# ---------------------------------------------------------------------


def _step_label(step_no: int) -> str:
    """물리 스키마에 없는 recipe step 이름 대신 쓰는 순서 라벨(측정값 아님)."""

    return f"STEP{step_no}"


def _judgement_of(alarm_type: str) -> Judgement:
    if alarm_type not in ("OOS", "OOC"):
        raise ValueError(f"알 수 없는 alarm_type 값입니다: {alarm_type}")
    return alarm_type  # type: ignore[return-value]


def _rule_id_of(source: AlarmSource) -> RuleId:
    return RULE_ID_BY_SOURCE[source]


def _build_detail(
    *,
    judgement: Judgement,
    hit_cnt: int | None,
    step_no: int,
    mean: float | None,
    vmin: float | None,
    vmax: float | None,
) -> str:
    """실측 집계(evaluation·summary_data)만으로 사람이 읽는 요약을 만든다.

    구할 수 없는 값은 만들지 않는다 — 문장에서 그냥 뺀다(NFR-19와 같은 원칙,
    `TraceModel.jsx` 상단 "값 창작 금지" 주석 참고). `detailNumbers()`가
    `mean\\s+(-?[0-9.]+)` 형태로 역파싱하므로 라벨과 숫자 사이는 반드시
    공백 하나다.
    """

    count = f"{hit_cnt} points" if hit_cnt is not None else "points"
    head = f"{judgement} {count} at {_step_label(step_no)}"
    parts: list[str] = []
    if mean is not None:
        parts.append(f"mean {mean:.3f}")
    if judgement == "OOS" and vmin is not None:
        parts.append(f"min {vmin:.3f}")
    if judgement == "OOS" and vmax is not None:
        parts.append(f"max {vmax:.3f}")
    if not parts:
        return head
    return f"{head} ({', '.join(parts)})"


def _r03_detail(step_no: int) -> str:
    return f"OOS for 3 consecutive WAFER at {_step_label(step_no)}"


def _row_to_alarm(row: Any) -> ScreenAlarmItem:
    source = AlarmSource(row["source"])
    judgement = _judgement_of(row["alarm_type"])
    step_no = int(row["recipe_step_no"])
    if source is AlarmSource.R03:
        hit_cnt = 3
        detail = _r03_detail(step_no)
    else:
        hit_cnt = (
            int(row["oos_point_cnt"])
            if judgement == "OOS" and row["oos_point_cnt"] is not None
            else int(row["ooc_point_cnt"])
            if judgement == "OOC" and row["ooc_point_cnt"] is not None
            else None
        )
        detail = _build_detail(
            judgement=judgement,
            hit_cnt=hit_cnt,
            step_no=step_no,
            mean=_float(row["value_mean"]),
            vmin=_float(row["value_min"]),
            vmax=_float(row["value_max"]),
        )
    lot_id = row["lot_id"]
    chamber_id = row["chamber_id"]
    return ScreenAlarmItem(
        alarm_id=row["alarm_id"],
        source=source,
        occurred_at=_kst(row["occurred_at"]),
        area=_canonical_area(row["area"]),
        equipment_id=row["equipment_id"],
        chamber_id=chamber_id,
        sensor_id=row["parameter_id"],
        lot_hist_id=row["lot_hist_id"],
        lot_id=lot_id,
        wafer_no=row["wafer_no"],
        recipe_step_no=step_no,
        recipe_step_name=_step_label(step_no),
        rule_id=_rule_id_of(source),
        judgement=judgement,
        hit_cnt=hit_cnt,
        detail=detail,
        incident={"lot_id": lot_id, "chamber_id": chamber_id},
        action_id=row["action_id"],
        action_code=row["action_code"],
        approval_status=row["approval_status"],
        latest_agent_run_id=row["latest_agent_run_id"],
        agent_run_status=row["agent_run_status"],
    )


_ALARM_SELECT = """
    WITH incident_action AS (
        SELECT DISTINCT ON (ah.lot_id, ah.chamber_id)
            ah.lot_id, ah.chamber_id, ah.action_id, ah.action_code,
            ah.approval_status
        FROM action_history AS ah
        ORDER BY ah.lot_id, ah.chamber_id, ah.created_at DESC NULLS LAST,
                 ah.action_id DESC
    ),
    incident_run AS (
        SELECT DISTINCT ON (r.lot_id, r.chamber_id)
            r.lot_id, r.chamber_id, r.agent_run_id, r.status
        FROM agent_run AS r
        ORDER BY r.lot_id, r.chamber_id, r.started_at DESC, r.agent_run_id DESC
    )
    SELECT
        v.source, v.alarm_id, v.occurred_at, v.area, v.equipment_id,
        v.chamber_id, v.parameter_id, v.lot_hist_id, v.lot_id, v.wafer_no,
        v.recipe_step_no, v.alarm_type,
        ev.oos_point_cnt, ev.ooc_point_cnt,
        sd.value_mean, sd.value_min, sd.value_max,
        ia.action_id, ia.action_code, ia.approval_status,
        ir.agent_run_id AS latest_agent_run_id, ir.status AS agent_run_status
    FROM v_alarm_event AS v
    LEFT JOIN evaluation AS ev
      ON ev.lot_hist_id = v.lot_hist_id
     AND ev.parameter = v.parameter_id
     AND ev.step_no = v.recipe_step_no
    LEFT JOIN summary_data AS sd
      ON sd.lot_hist_id = v.lot_hist_id
     AND sd.parameter = v.parameter_id
     AND sd.step_no = v.recipe_step_no
    LEFT JOIN incident_action AS ia
      ON ia.lot_id = v.lot_id AND ia.chamber_id = v.chamber_id
    LEFT JOIN incident_run AS ir
      ON ir.lot_id = v.lot_id AND ir.chamber_id = v.chamber_id
"""


def _alarm_filters(
    *,
    date_: date | None,
    area: str | None,
    equipment_id: str | None,
    chamber_id: str | None,
    sensor_id: str | None,
    judgement: str | None,
    source: AlarmSource | None,
) -> tuple[list[str], dict[str, object]]:
    clauses: list[str] = []
    params: dict[str, object] = {}
    if date_ is not None:
        clauses.append("v.occurred_at >= :date_from AND v.occurred_at < :date_to")
        day_start = datetime.combine(date_, datetime.min.time())
        params["date_from"] = day_start
        params["date_to"] = day_start + timedelta(days=1)
    normalized_area = None if area in {None, "ALL"} else area
    if normalized_area is not None:
        clauses.append("lower(v.area) = lower(:area)")
        params["area"] = normalized_area
    if _optional(equipment_id):
        clauses.append("v.equipment_id = :equipment_id")
        params["equipment_id"] = equipment_id
    if _optional(chamber_id):
        clauses.append("v.chamber_id = :chamber_id")
        params["chamber_id"] = chamber_id
    if _optional(sensor_id):
        clauses.append("v.parameter_id = :sensor_id")
        params["sensor_id"] = sensor_id
    if judgement is not None:
        if judgement not in ("OOS", "OOC"):
            raise DetectionQueryError("judgement는 OOS 또는 OOC여야 합니다")
        clauses.append("v.alarm_type = :judgement")
        params["judgement"] = judgement
    if source is not None:
        clauses.append("v.source = :source")
        params["source"] = source.value
    return clauses, params


def _fetch_hierarchy_rows(connection: Connection) -> list[tuple[str, str, str]]:
    query = text(
        """
        SELECT DISTINCT area_id, equipment_id, chamber_id
        FROM lot_history
        WHERE area_id IS NOT NULL
          AND equipment_id IS NOT NULL
          AND chamber_id IS NOT NULL
        ORDER BY area_id, equipment_id, chamber_id
        """
    )
    rows = connection.execute(query).mappings().all()
    return [
        (_canonical_area(row["area_id"]), row["equipment_id"], row["chamber_id"])
        for row in rows
    ]


def _hierarchy_nodes(rows: list[tuple[str, str, str]]) -> list[HierarchyNode]:
    ordered: dict[tuple[str, str], list[str]] = {}
    for area_id, equipment_id, chamber_id in rows:
        key = (area_id, equipment_id)
        ordered.setdefault(key, [])
        if chamber_id not in ordered[key]:
            ordered[key].append(chamber_id)
    return [
        HierarchyNode(area_id=area_id, equipment_id=equipment_id, chambers=chambers)
        for (area_id, equipment_id), chambers in ordered.items()
    ]


def _fetch_sensor_catalog(connection: Connection) -> list[TraceCatalogSensor]:
    query = text(
        """
        SELECT parameter_id, parameter_name, unit, target_value, spec_lower,
               ctrl_lower, ctrl_upper, spec_upper, upper_only
        FROM dim_parameter
        ORDER BY parameter_id
        """
    )
    rows = connection.execute(query).mappings().all()
    return [
        TraceCatalogSensor(
            sensor_id=row["parameter_id"],
            sensor_name=row["parameter_name"],
            unit=row["unit"],
            spec_lower=_float(row["spec_lower"]),
            ctrl_lower=_float(row["ctrl_lower"]),
            target=_float(row["target_value"]),
            ctrl_upper=float(row["ctrl_upper"]),
            spec_upper=float(row["spec_upper"]),
            upper_only=bool(row["upper_only"]),
        )
        for row in rows
    ]


def get_dashboard_summary(
    connection: Connection,
    *,
    date_: date | None = None,
    area: str | None = None,
    equipment_id: str | None = None,
    chamber_id: str | None = None,
) -> DashboardSummaryResponse:
    """`GET /dashboard/summary` 조립. 필터에 걸리는 알람으로 KPI·추이·상위
    parameter를 서버에서 집계한다."""

    try:
        clauses, params = _alarm_filters(
            date_=date_,
            area=area,
            equipment_id=equipment_id,
            chamber_id=chamber_id,
            sensor_id=None,
            judgement=None,
            source=None,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        statement = text(
            f"{_ALARM_SELECT}\n{where}\n"
            "ORDER BY v.occurred_at DESC, v.source ASC, v.alarm_id DESC"
        )
        rows = connection.execute(statement, params).mappings().all()
        alarms = [_row_to_alarm(row) for row in rows]

        hierarchy_rows = _fetch_hierarchy_rows(connection)
        hierarchy = _hierarchy_nodes(hierarchy_rows)
        sensor_catalog = [s.sensor_id for s in _fetch_sensor_catalog(connection)]

        oos_count = sum(1 for a in alarms if a.judgement == "OOS")
        ooc_count = len(alarms) - oos_count

        by_day: dict[date, dict[str, Any]] = {}
        for alarm in alarms:
            d = alarm.occurred_at.date()
            bucket = by_day.setdefault(
                d, {"oos": 0, "ooc": 0, "r03": False}
            )
            if alarm.judgement == "OOS":
                bucket["oos"] += 1
            else:
                bucket["ooc"] += 1
            if alarm.source is AlarmSource.R03:
                bucket["r03"] = True
        daily_trend = [
            DailyTrendItem(
                date=d,
                oos_count=v["oos"],
                ooc_count=v["ooc"],
                has_r03_consec=v["r03"],
            )
            for d, v in sorted(by_day.items())
        ]
        date_range = [d for d, _ in sorted(by_day.items())]
        if date_range:
            date_range = [date_range[0], date_range[-1]]

        by_sensor: dict[str, dict[str, Any]] = {}
        for alarm in alarms:
            bucket = by_sensor.setdefault(
                alarm.sensor_id, {"count": 0, "chambers": set()}
            )
            bucket["count"] += 1
            bucket["chambers"].add(alarm.chamber_id)
        top_sensors = [
            TopSensorItem(
                sensor_id=sensor_id,
                alarm_count=v["count"],
                chamber_ids=sorted(v["chambers"]),
            )
            for sensor_id, v in sorted(
                by_sensor.items(), key=lambda kv: (-kv[1]["count"], kv[0])
            )
        ]

        by_equipment: dict[str, dict[str, Any]] = {}
        for area_id, equipment, chamber in hierarchy_rows:
            slot = by_equipment.setdefault(
                equipment, {"area_id": area_id, "count": 0, "chambers": {}}
            )
            slot["chambers"].setdefault(chamber, 0)
        for alarm in alarms:
            slot = by_equipment.setdefault(
                alarm.equipment_id, {"area_id": alarm.area, "count": 0, "chambers": {}}
            )
            slot["count"] += 1
            slot["chambers"][alarm.chamber_id] = (
                slot["chambers"].get(alarm.chamber_id, 0) + 1
            )
        equipment_counts = [
            EquipmentCountItem(
                equipment_id=equipment_id,
                area_id=slot["area_id"],
                alarm_count=slot["count"],
                chambers=sorted(
                    (
                        ChamberAlarmCount(chamber_id=chamber_id, alarm_count=count)
                        for chamber_id, count in slot["chambers"].items()
                    ),
                    key=lambda c: (-c.alarm_count, c.chamber_id),
                ),
            )
            for equipment_id, slot in sorted(
                by_equipment.items(), key=lambda kv: (-kv[1]["count"], kv[0])
            )
        ]

        pending_rows = connection.execute(
            text(
                """
                SELECT ar.approval_id, ar.action_id, ar.agent_run_id,
                       ar.requested_at, ah.lot_id, ah.chamber_id,
                       COALESCE(
                           ah.equipment_id,
                           incident_equipment.equipment_id
                       ) AS equipment_id,
                       ah.action_code
                FROM approval_request AS ar
                JOIN action_history AS ah ON ah.action_id = ar.action_id
                LEFT JOIN LATERAL (
                    SELECT CASE
                             WHEN count(DISTINCT history.equipment_id) = 1
                             THEN min(history.equipment_id)
                             ELSE NULL
                           END AS equipment_id
                    FROM lot_history AS history
                    WHERE history.lot_id = ah.lot_id
                      AND history.chamber_id = ah.chamber_id
                      AND history.equipment_id IS NOT NULL
                ) AS incident_equipment ON TRUE
                WHERE ar.status = 'PENDING'
                ORDER BY ar.requested_at DESC, ar.approval_id DESC
                """
            )
        ).mappings().all()
        pending_approvals = [
            PendingApprovalItem(
                approval_id=row["approval_id"],
                action_id=row["action_id"],
                agent_run_id=row["agent_run_id"],
                incident={"lot_id": row["lot_id"], "chamber_id": row["chamber_id"]},
                equipment_id=row["equipment_id"],
                action_code=ActionCode(row["action_code"]),
                severity=resolve_severity(ActionCode(row["action_code"])),
                requested_at=_kst(row["requested_at"]),
            )
            for row in pending_rows
        ]

        recent_alarms = sorted(
            alarms, key=lambda a: (a.occurred_at, a.alarm_id), reverse=True
        )[:6]

        return DashboardSummaryResponse(
            reference_date=date_range[-1] if date_range else date.today(),
            date_range=date_range,
            area=_canonical_area(area) if area not in (None, "ALL") else None,
            hierarchy=hierarchy,
            sensor_catalog=sensor_catalog,
            alarm_count=len(alarms),
            oos_count=oos_count,
            ooc_count=ooc_count,
            daily_trend=daily_trend,
            top_sensors=top_sensors,
            equipment_counts=equipment_counts,
            pending_approvals=pending_approvals,
            recent_alarms=recent_alarms,
        )
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DetectionQueryError):
            raise
        raise DetectionReadUnavailable from exc


def get_alarms_page(
    connection: Connection,
    *,
    page: int,
    size: int,
    date_: date | None = None,
    area: str | None = None,
    equipment_id: str | None = None,
    chamber_id: str | None = None,
    sensor_id: str | None = None,
    judgement: str | None = None,
) -> AlarmPageEnvelope:
    """`GET /alarms/paged` 조립 — 화면 1·2가 공유하는 목록 원본."""

    if page < 1:
        raise DetectionQueryError("page는 1 이상이어야 합니다")
    if size < 1 or size > 100:
        raise DetectionQueryError("size는 1..100 사이여야 합니다")

    try:
        clauses, params = _alarm_filters(
            date_=date_,
            area=area,
            equipment_id=equipment_id,
            chamber_id=chamber_id,
            sensor_id=sensor_id,
            judgement=judgement,
            source=None,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        count_statement = text(
            f"SELECT COUNT(*) FROM v_alarm_event AS v {where}"
        )
        total = connection.execute(count_statement, params).scalar_one()

        page_params = {**params, "limit": size, "offset": (page - 1) * size}
        statement = text(
            f"{_ALARM_SELECT}\n{where}\n"
            "ORDER BY v.occurred_at DESC, v.source ASC, v.alarm_id DESC\n"
            "LIMIT :limit OFFSET :offset"
        )
        rows = connection.execute(statement, page_params).mappings().all()
        items = [_row_to_alarm(row) for row in rows]
        return AlarmPageEnvelope(items=items, total=int(total), page=page, size=size)
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DetectionQueryError):
            raise
        raise DetectionReadUnavailable from exc


def get_alarm_detail(
    connection: Connection, *, source: AlarmSource, alarm_id: str
) -> AlarmDetailResponse | None:
    """`GET /alarms/{source}/{alarm_id}` 조립. 없으면 None(Router가 404로 번역)."""

    try:
        statement = text(
            f"{_ALARM_SELECT}\nWHERE v.source = :source AND v.alarm_id = :alarm_id"
        )
        row = connection.execute(
            statement, {"source": source.value, "alarm_id": alarm_id}
        ).mappings().first()
        if row is None:
            return None
        item = _row_to_alarm(row)
        return AlarmDetailResponse(**item.model_dump())
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc


def get_trace_catalog(connection: Connection) -> TraceCatalogResponse:
    """`GET /traces/catalog` 조립 — 화면 2 조회 선택지 + 센서별 한계선."""

    try:
        hierarchy_rows = _fetch_hierarchy_rows(connection)
        areas = sorted({area_id for area_id, _, _ in hierarchy_rows})
        equipments_map: dict[tuple[str, str], list[str]] = {}
        for area_id, equipment_id, chamber_id in hierarchy_rows:
            key = (area_id, equipment_id)
            equipments_map.setdefault(key, [])
            if chamber_id not in equipments_map[key]:
                equipments_map[key].append(chamber_id)
        equipments = [
            TraceCatalogEquipment(
                equipment_id=equipment_id, area_id=area_id, chambers=chambers
            )
            for (area_id, equipment_id), chambers in equipments_map.items()
        ]

        sensors = _fetch_sensor_catalog(connection)

        recipe_rows = connection.execute(
            text(
                """
                SELECT DISTINCT area_id, recipe_id
                FROM lot_history
                WHERE area_id IS NOT NULL AND recipe_id IS NOT NULL
                ORDER BY area_id, recipe_id
                """
            )
        ).mappings().all()
        recipes = [
            TraceCatalogRecipe(
                recipe_id=row["recipe_id"], area_id=_canonical_area(row["area_id"])
            )
            for row in recipe_rows
        ]

        lot_rows = connection.execute(
            text(
                """
                SELECT lot_id,
                       array_agg(DISTINCT wafer_no ORDER BY wafer_no) AS wafer_nos
                FROM lot_history
                WHERE lot_id IS NOT NULL AND wafer_no IS NOT NULL
                GROUP BY lot_id
                ORDER BY lot_id
                """
            )
        ).mappings().all()
        lots = [
            TraceCatalogLot(lot_id=row["lot_id"], wafer_nos=list(row["wafer_nos"]))
            for row in lot_rows
        ]

        return TraceCatalogResponse(
            areas=[TraceCatalogArea(area_id=a) for a in areas],
            equipments=equipments,
            sensors=sensors,
            recipes=recipes,
            lots=lots,
        )
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc


def search_traces(
    connection: Connection, request: TraceSearchRequest
) -> TraceSearchResponse:
    """`POST /traces/search` 조립 — 다중 웨이퍼·다중 센서 raw trace 조회."""

    try:
        lot_clauses: list[str] = []
        lot_params: dict[str, object] = {}
        if request.area is not None:
            lot_clauses.append("lower(area_id) = lower(:area)")
            lot_params["area"] = request.area
        if request.equipment_id is not None:
            lot_clauses.append("equipment_id = :equipment_id")
            lot_params["equipment_id"] = request.equipment_id
        if request.chamber_id is not None:
            lot_clauses.append("chamber_id = :chamber_id")
            lot_params["chamber_id"] = request.chamber_id
        if request.recipe_id is not None:
            lot_clauses.append("recipe_id = :recipe_id")
            lot_params["recipe_id"] = request.recipe_id
        if request.lot_id is not None:
            lot_clauses.append("lot_id = :lot_id")
            lot_params["lot_id"] = request.lot_id
        if request.wafer_nos:
            lot_clauses.append("wafer_no = ANY(:wafer_nos)")
            lot_params["wafer_nos"] = list(request.wafer_nos)
        if request.from_ is not None:
            lot_clauses.append("track_in_at >= :from_")
            lot_params["from_"] = request.from_
        if request.to is not None:
            lot_clauses.append("track_in_at <= :to")
            lot_params["to"] = request.to

        lot_where = f"WHERE {' AND '.join(lot_clauses)}" if lot_clauses else ""
        lot_rows = connection.execute(
            text(
                f"""
                SELECT lot_hist_id, lot_id, wafer_no, area_id, equipment_id,
                       chamber_id, recipe_id, track_in_at
                FROM lot_history
                {lot_where}
                ORDER BY track_in_at ASC NULLS LAST, lot_hist_id ASC
                """
            ),
            lot_params,
        ).mappings().all()

        sensors = _fetch_sensor_catalog(connection)
        limits = {s.sensor_id: s for s in sensors}

        lot_hist_ids = [row["lot_hist_id"] for row in lot_rows]
        if not lot_hist_ids:
            return TraceSearchResponse(
                wafers=[], limits=limits, measured_step_stats={}, total=0
            )

        trace_clauses = ["t.lot_hist_id = ANY(:lot_hist_ids)"]
        trace_params: dict[str, object] = {"lot_hist_ids": lot_hist_ids}
        if request.sensor_ids:
            trace_clauses.append("t.parameter_id = ANY(:sensor_ids)")
            trace_params["sensor_ids"] = list(request.sensor_ids)
        trace_rows = connection.execute(
            text(
                f"""
                SELECT t.lot_hist_id, t.parameter_id, t.recipe_step_no,
                       t.seq_no, t.measured_at, t.value
                FROM fdc_trace AS t
                WHERE {' AND '.join(trace_clauses)}
                ORDER BY t.lot_hist_id, t.parameter_id, t.measured_at ASC,
                         t.seq_no ASC
                """
            ),
            trace_params,
        ).mappings().all()

        lot_by_id = {row["lot_hist_id"]: row for row in lot_rows}
        grouped: dict[tuple[str, str], list[Any]] = {}
        for row in trace_rows:
            grouped.setdefault((row["lot_hist_id"], row["parameter_id"]), []).append(
                row
            )

        wafers: list[TraceSearchWafer] = []
        for (lot_hist_id, parameter_id), points in grouped.items():
            lot_row = lot_by_id.get(lot_hist_id)
            if lot_row is None:
                continue
            wafers.append(
                TraceSearchWafer(
                    lot_hist_id=lot_hist_id,
                    lot_id=lot_row["lot_id"],
                    wafer_no=int(lot_row["wafer_no"]),
                    chamber_id=lot_row["chamber_id"],
                    equipment_id=lot_row["equipment_id"],
                    sensor_id=parameter_id,
                    occurred_at=(
                        _kst(lot_row["track_in_at"])
                        if lot_row["track_in_at"] is not None
                        else None
                    ),
                    points=[
                        TraceSearchPoint(
                            seq_no=p["seq_no"],
                            recipe_step_no=p["recipe_step_no"],
                            recipe_step_name=_step_label(p["recipe_step_no"]),
                            measured_at=_kst(p["measured_at"]),
                            value=float(p["value"]),
                        )
                        for p in points
                    ],
                    missing_steps=[],
                )
            )
        wafers.sort(key=lambda w: (w.lot_id, w.wafer_no, w.sensor_id))

        return TraceSearchResponse(
            wafers=wafers,
            limits=limits,
            measured_step_stats={},
            total=len(wafers),
        )
    except (SQLAlchemyError, ValidationError, KeyError, TypeError, ValueError) as exc:
        raise DetectionReadUnavailable from exc
