from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection

from app.common.db import get_db_connection
from app.common.enums import AlarmSource
from app.detection.public_api import (
    DetectionQueryError,
    DetectionReadUnavailable,
    list_alarms,
    list_parameters,
    list_trace_points,
)
from app.detection.public_schemas import AlarmItem, ParameterItem, TracePoint

router = APIRouter(tags=["Detection"])


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
    area: Literal["Photo", "Etch", "ALL"] | None = None,
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
