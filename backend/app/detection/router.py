from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection

from app.common.db import get_db_connection
from app.common.enums import AlarmSource
from app.detection import public_api
from app.detection.public_api import (
    DetectionQueryError,
    DetectionReadUnavailable,
    list_alarms,
    list_parameters,
    list_trace_points,
)
from app.detection.public_schemas import (
    AlarmDetailResponse,
    AlarmItem,
    AlarmPageEnvelope,
    DashboardSummaryResponse,
    ParameterItem,
    TraceCatalogResponse,
    TracePoint,
    TraceSearchRequest,
    TraceSearchResponse,
)

router = APIRouter(tags=["Detection"])

AreaFilter = Literal["Photo", "Etch", "ALL"]
JudgementFilter = Literal["OOS", "OOC"]


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Detection 조회 서비스를 사용할 수 없습니다.",
    )


def _required_query(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail="필수 query는 빈 문자열일 수 없습니다.",
        )
    return normalized


@router.get("/alarms", response_model=list[AlarmItem])
def get_alarms(
    connection: Annotated[Connection, Depends(get_db_connection)],
    date_from: date | None = None,
    date_to: date | None = None,
    area: AreaFilter | None = None,
    equipment: str | None = None,
    chamber: str | None = None,
    parameter: str | None = None,
    source: AlarmSource | None = None,
    include_derived: bool = False,
) -> list[AlarmItem]:
    try:
        return list_alarms(
            connection,
            date_from=date_from,
            date_to=date_to,
            area=area,
            equipment=equipment,
            chamber=chamber,
            parameter=parameter,
            source=source,
            include_derived=include_derived,
        )
    except DetectionQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc


RequiredQuery = Annotated[str, Query(min_length=1)]


@router.get("/trace", response_model=list[TracePoint])
def get_trace(
    connection: Annotated[Connection, Depends(get_db_connection)],
    lot: RequiredQuery,
    wafer: RequiredQuery,
    chamber: RequiredQuery,
    parameter: RequiredQuery,
) -> list[TracePoint]:
    try:
        return list_trace_points(
            connection,
            lot=_required_query(lot),
            wafer=_required_query(wafer),
            chamber=_required_query(chamber),
            parameter=_required_query(parameter),
        )
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc


@router.get("/parameters", response_model=list[ParameterItem])
def get_parameters(
    connection: Annotated[Connection, Depends(get_db_connection)],
) -> list[ParameterItem]:
    try:
        return list_parameters(connection)
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc


# ---------------------------------------------------------------------
# 선택 확장 API (API v3 §5.2, A 담당 5개) — 화면 1(알람 대시보드)·
# 화면 2(알람 히스토리) 전용. compat 필수 3개(위 절)와 DTO를 공유하지 않는다
# (public_schemas.py 모듈 docstring 참고).
# ---------------------------------------------------------------------


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    connection: Annotated[Connection, Depends(get_db_connection)],
    date: date | None = None,
    area: AreaFilter | None = None,
    equipment_id: str | None = None,
    chamber_id: str | None = None,
) -> DashboardSummaryResponse:
    try:
        return public_api.get_dashboard_summary(
            connection,
            date_=date,
            area=area,
            equipment_id=equipment_id,
            chamber_id=chamber_id,
        )
    except DetectionQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc


@router.get("/alarms/paged", response_model=AlarmPageEnvelope)
def get_alarms_paged(
    connection: Annotated[Connection, Depends(get_db_connection)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    date: date | None = None,
    area: AreaFilter | None = None,
    equipment_id: str | None = None,
    chamber_id: str | None = None,
    sensor_id: str | None = None,
    judgement: JudgementFilter | None = None,
) -> AlarmPageEnvelope:
    try:
        return public_api.get_alarms_page(
            connection,
            page=page,
            size=size,
            date_=date,
            area=area,
            equipment_id=equipment_id,
            chamber_id=chamber_id,
            sensor_id=sensor_id,
            judgement=judgement,
        )
    except DetectionQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc


@router.get("/alarms/{source}/{alarm_id}", response_model=AlarmDetailResponse)
def get_alarm_detail(
    connection: Annotated[Connection, Depends(get_db_connection)],
    source: AlarmSource,
    alarm_id: str,
) -> AlarmDetailResponse:
    normalized = _required_query(alarm_id)
    try:
        result = public_api.get_alarm_detail(
            connection, source=source, alarm_id=normalized
        )
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"알람을 찾을 수 없습니다: {source.value}:{normalized}",
        )
    return result


@router.get("/traces/catalog", response_model=TraceCatalogResponse)
def get_traces_catalog(
    connection: Annotated[Connection, Depends(get_db_connection)],
) -> TraceCatalogResponse:
    try:
        return public_api.get_trace_catalog(connection)
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc


@router.post("/traces/search", response_model=TraceSearchResponse)
def post_traces_search(
    connection: Annotated[Connection, Depends(get_db_connection)],
    body: TraceSearchRequest,
) -> TraceSearchResponse:
    try:
        return public_api.search_traces(connection, body)
    except DetectionReadUnavailable as exc:
        raise _unavailable() from exc
