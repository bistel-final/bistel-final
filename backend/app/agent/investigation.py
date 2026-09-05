"""V5-C-7.1 Level 3 전용 read Tool 구현.

selector에는 run-local candidate token만 보인다. 이 모듈은 Graph가 복원한 canonical
입력과 incident 문맥으로 읽기 전용 SQL을 실행하며 Fault label을 읽지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from statistics import mean, stdev
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import text

from app.agent.state import StateModel
from app.common.config import TOOL_DB_TIMEOUT_SEC
from app.common.db import get_readonly_engine
from app.common.tool_contracts import (
    ChamberParameterHistoryToolInput,
    ChamberParameterHistoryToolResult,
    HistoryBaseline,
    LotAggregate,
    MetrologyResultItem,
    MetrologyResultToolInput,
    MetrologyResultToolResult,
    fail,
)
from app.common.tool_timeouts import (
    DependencyTimeoutError,
    apply_postgres_statement_timeout,
    postgres_timeout_error,
)

logger = logging.getLogger(__name__)

METROLOGY_DISCLAIMER = "계측 PASS/FAIL은 제품 품질 근거이며 Fault Mode 정답이 아니다"


class HistoryLookupContext(StateModel):
    """selector가 정하지 못하는 incident-derived T2 문맥."""

    current_lot_id: str = Field(min_length=1, max_length=20)
    incident_step_id: str = Field(min_length=1, max_length=20)
    scope: Literal["CURRENT", "SIBLING"]


_HISTORY = text(
    """
    WITH prior_lots AS (
        SELECT l.lot_id, max(l.track_in_at) AS latest_track_in
        FROM lot_history AS l
        WHERE :scope = 'CURRENT'
          AND l.chamber_id = :chamber_id
          AND l.step_id = :incident_step_id
          AND l.track_in_at IS NOT NULL
        GROUP BY l.lot_id
        HAVING max(l.track_in_at) < :before
           AND l.lot_id <> :current_lot_id
        ORDER BY latest_track_in DESC, l.lot_id DESC
        LIMIT :n_lots
    ),
    selected_lots AS (
        SELECT CAST(:current_lot_id AS text) AS lot_id, 0::integer AS ordinal,
               'CURRENT'::text AS row_kind
        UNION ALL
        SELECT p.lot_id,
               row_number() OVER (
                   ORDER BY p.latest_track_in DESC, p.lot_id DESC
               )::integer AS ordinal,
               'PRIOR'::text AS row_kind
        FROM prior_lots AS p
    )
    SELECT
        selected.row_kind,
        selected.ordinal,
        selected.lot_id,
        avg(summary.value_mean)::double precision AS lot_mean,
        stddev_samp(summary.value_mean)::double precision AS lot_std,
        min(summary.value_min)::double precision AS lot_min,
        max(summary.value_max)::double precision AS lot_max,
        count(DISTINCT summary.lot_hist_id)::integer AS wafer_count,
        count(DISTINCT summary.lot_hist_id) FILTER (
            WHERE evaluation.ooc_point_cnt > 0
        )::integer AS ooc_wafers,
        count(DISTINCT summary.lot_hist_id) FILTER (
            WHERE evaluation.oos_point_cnt > 0
        )::integer AS oos_wafers,
        count(*) FILTER (
            WHERE evaluation.lot_hist_id IS NULL
        )::integer AS evaluation_missing,
        min(history.track_in_at) AS track_in_from,
        max(history.track_in_at) AS track_in_to
    FROM selected_lots AS selected
    JOIN lot_history AS history
      ON history.lot_id = selected.lot_id
     AND history.chamber_id = :chamber_id
     AND history.step_id = :incident_step_id
    JOIN summary_data AS summary
      ON summary.lot_hist_id = history.lot_hist_id
     AND summary.parameter = :parameter_id
     AND summary.step_no = :step_no
     AND summary.value_mean IS NOT NULL
    LEFT JOIN evaluation
      ON evaluation.lot_hist_id = summary.lot_hist_id
     AND evaluation.parameter = summary.parameter
     AND evaluation.step_no = summary.step_no
    GROUP BY selected.row_kind, selected.ordinal, selected.lot_id
    ORDER BY selected.ordinal ASC, selected.lot_id DESC
    """
)


_METROLOGY = text(
    """
    SELECT wafer_id, measure_type, measured_value, spec_lower, spec_upper,
           alarm_result, measured_at
    FROM metrology
    WHERE lot_id = :lot_id AND step_id = :step_id
    ORDER BY measured_at ASC, wafer_id ASC, measure_type ASC, metrology_id ASC
    """
)


def classify_history_trend(
    current_mean: float,
    prior_means_newest_first: Sequence[float],
) -> tuple[str, float | None, float | None]:
    """승인된 5분기 truth table로 trend와 baseline을 계산한다."""

    prior = tuple(float(value) for value in prior_means_newest_first)
    mean_hist = mean(prior) if prior else None
    sd_hist = stdev(prior) if len(prior) >= 2 else None
    if len(prior) < 2 or sd_hist is None or mean_hist is None:
        return "INSUFFICIENT", mean_hist, sd_hist
    if sd_hist == 0:
        return (
            "STABLE" if current_mean == mean_hist else "SUDDEN",
            mean_hist,
            sd_hist,
        )
    deviation = current_mean - mean_hist
    if abs(deviation) <= 2 * sd_hist:
        return "STABLE", mean_hist, sd_hist

    chronological = tuple(reversed(prior))
    pairs = tuple(zip(chronological, chronological[1:], strict=False))
    increasing = all(a < b for a, b in pairs)
    decreasing = all(a > b for a, b in pairs)
    if deviation > 0 and increasing:
        return "DRIFT_UP", mean_hist, sd_hist
    if deviation < 0 and decreasing:
        return "DRIFT_DOWN", mean_hist, sd_hist
    return "SUDDEN", mean_hist, sd_hist


def _lot_aggregate(row: Mapping[str, Any]) -> LotAggregate:
    return LotAggregate(
        lot_id=str(row["lot_id"]),
        lot_mean=None if row["lot_mean"] is None else float(row["lot_mean"]),
        lot_std=None if row["lot_std"] is None else float(row["lot_std"]),
        lot_min=None if row["lot_min"] is None else float(row["lot_min"]),
        lot_max=None if row["lot_max"] is None else float(row["lot_max"]),
        wafer_count=int(row["wafer_count"]),
        ooc_wafers=int(row["ooc_wafers"]),
        oos_wafers=int(row["oos_wafers"]),
        evaluation_missing=int(row["evaluation_missing"]),
        track_in_from=row["track_in_from"],
        track_in_to=row["track_in_to"],
    )


def _load_history(
    request: ChamberParameterHistoryToolInput,
    context: HistoryLookupContext,
) -> ChamberParameterHistoryToolResult:
    with get_readonly_engine().connect() as connection:
        apply_postgres_statement_timeout(
            connection,
            timeout_seconds=TOOL_DB_TIMEOUT_SEC,
        )
        rows = connection.execute(
            _HISTORY,
            {
                **request.model_dump(mode="python"),
                "current_lot_id": context.current_lot_id,
                "incident_step_id": context.incident_step_id,
                "scope": context.scope,
            },
        ).mappings()
        aggregates = [(_lot_aggregate(row), row["row_kind"]) for row in rows]

    current = next((item for item, kind in aggregates if kind == "CURRENT"), None)
    if current is None or current.lot_mean is None:
        return fail(ChamberParameterHistoryToolResult, "NOT_FOUND: current lot")
    prior = [item for item, kind in aggregates if kind == "PRIOR"]
    trend, mean_hist, sd_hist = classify_history_trend(
        current.lot_mean,
        [item.lot_mean for item in prior if item.lot_mean is not None],
    )
    return ChamberParameterHistoryToolResult(
        ok=True,
        scope=context.scope,
        chamber_id=request.chamber_id,
        parameter_id=request.parameter_id,
        step_no=request.step_no,
        current=current,
        prior=prior,
        baseline=HistoryBaseline(
            mean_hist=mean_hist,
            sd_hist=sd_hist,
            prior_lot_count=len(prior),
        ),
        trend=trend,
        comparison=context.scope,
        sample_count=current.wafer_count,
    )


def get_chamber_parameter_history(
    payload: dict[str, Any],
) -> ChamberParameterHistoryToolResult:
    """Graph가 복원한 T2 payload를 readonly DB 결과로 바꾼다."""

    try:
        context = HistoryLookupContext.model_validate(payload.get("_context"))
        request = ChamberParameterHistoryToolInput.model_validate(
            {key: value for key, value in payload.items() if key != "_context"}
        )
        return _load_history(request, context)
    except DependencyTimeoutError as exc:
        return fail(ChamberParameterHistoryToolResult, f"TIMEOUT: {exc.reason_code}")
    except TimeoutError:
        return fail(ChamberParameterHistoryToolResult, "TIMEOUT: dependency")
    except Exception as exc:
        if timeout := postgres_timeout_error(exc):
            return fail(
                ChamberParameterHistoryToolResult,
                f"TIMEOUT: {timeout.reason_code}",
            )
        logger.error("CHAMBER_PARAMETER_HISTORY_DEPENDENCY_ERROR")
        return fail(
            ChamberParameterHistoryToolResult,
            "DEPENDENCY_ERROR: chamber parameter history",
        )


def _load_metrology(
    request: MetrologyResultToolInput,
) -> MetrologyResultToolResult:
    with get_readonly_engine().connect() as connection:
        apply_postgres_statement_timeout(
            connection,
            timeout_seconds=TOOL_DB_TIMEOUT_SEC,
        )
        rows = connection.execute(
            _METROLOGY,
            request.model_dump(mode="python"),
        ).mappings()
        results = [
            MetrologyResultItem(
                wafer_id=str(row["wafer_id"]),
                measure_type=str(row["measure_type"]),
                measured_value=(
                    None
                    if row["measured_value"] is None
                    else float(row["measured_value"])
                ),
                spec_lower=(
                    None if row["spec_lower"] is None else float(row["spec_lower"])
                ),
                spec_upper=(
                    None if row["spec_upper"] is None else float(row["spec_upper"])
                ),
                alarm_result=str(row["alarm_result"]),
                measured_at=row["measured_at"],
            )
            for row in rows
        ]
    if not results:
        return fail(MetrologyResultToolResult, "NOT_FOUND: metrology")
    return MetrologyResultToolResult(
        ok=True,
        lot_id=request.lot_id,
        step_id=request.step_id,
        results=results,
        fail_count=sum(item.alarm_result == "FAIL" for item in results),
        disclaimer=METROLOGY_DISCLAIMER,
    )


def get_metrology_result(payload: dict[str, Any]) -> MetrologyResultToolResult:
    """lot·step 표본 계측을 반환하되 Fault 정답으로 해석하지 않는다."""

    try:
        request = MetrologyResultToolInput.model_validate(payload)
        return _load_metrology(request)
    except DependencyTimeoutError as exc:
        return fail(MetrologyResultToolResult, f"TIMEOUT: {exc.reason_code}")
    except TimeoutError:
        return fail(MetrologyResultToolResult, "TIMEOUT: dependency")
    except Exception as exc:
        if timeout := postgres_timeout_error(exc):
            return fail(MetrologyResultToolResult, f"TIMEOUT: {timeout.reason_code}")
        logger.error("METROLOGY_RESULT_DEPENDENCY_ERROR")
        return fail(MetrologyResultToolResult, "DEPENDENCY_ERROR: metrology")


__all__ = [
    "HistoryLookupContext",
    "METROLOGY_DISCLAIMER",
    "classify_history_trend",
    "get_chamber_parameter_history",
    "get_metrology_result",
]
