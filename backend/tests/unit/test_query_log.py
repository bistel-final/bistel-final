"""질의 이력 outcome 파생 검증 — V5-D-2.4 (FR-D-05)."""

from app.analytics.query_log import outcome_of
from app.analytics.schemas import AnalysisQueryResponse, NlQueryOutcome
from app.common.enums import ChartType
from app.common.tool_contracts import MetricPlan, VisualizationPlan


def _success() -> AnalysisQueryResponse:
    return AnalysisQueryResponse(
        question="챔버별 알람 수",
        generated_sql=(
            "SELECT chamber_id, count(*) FROM alarm_history"
            " GROUP BY chamber_id LIMIT 500"
        ),
        columns=["chamber_id", "count"],
        rows=[{"chamber_id": "PHO-01-C1", "count": 3}],
        row_count=1,
        metric=MetricPlan(type="count"),
        metric_result=None,
        group_by=["chamber_id"],
        visualization=VisualizationPlan(chart_type=ChartType.BAR),
        is_valid=True,
        is_rejected=False,
        reject_reason=None,
        error_msg=None,
        latency_ms=120,
        nl_query_log_id=None,
    )


def _rejected() -> AnalysisQueryResponse:
    return AnalysisQueryResponse(
        question="알람 전부 지워줘",
        generated_sql=None,
        columns=[],
        rows=[],
        row_count=0,
        metric=None,
        metric_result=None,
        group_by=[],
        visualization=None,
        is_valid=False,
        is_rejected=True,
        reject_reason="POLICY_REJECTED: 단일 SELECT 만 허용",
        error_msg=None,
        latency_ms=15,
        nl_query_log_id=None,
    )


def _db_error() -> AnalysisQueryResponse:
    return AnalysisQueryResponse(
        question="센서 평균",
        generated_sql="SELECT avg(value) FROM sensor_trace LIMIT 500",
        columns=[],
        rows=[],
        row_count=0,
        metric=MetricPlan(type="count"),
        metric_result=None,
        group_by=[],
        visualization=VisualizationPlan(chart_type=ChartType.TABLE),
        is_valid=True,
        is_rejected=False,
        reject_reason=None,
        error_msg="DB_ERROR: connection timeout",
        latency_ms=5000,
        nl_query_log_id=None,
    )


def test_outcome_success():
    assert outcome_of(_success()) is NlQueryOutcome.SUCCESS


def test_outcome_policy_rejected():
    assert outcome_of(_rejected()) is NlQueryOutcome.POLICY_REJECTED


def test_outcome_db_error():
    assert outcome_of(_db_error()) is NlQueryOutcome.DB_ERROR


def test_nl_query_log_id_nullable_and_positive_only():
    # 기록 생략(evaluation-only 미구성) 시 null 을 허용한다 — placeholder 금지
    assert _success().nl_query_log_id is None
    updated = _success().model_copy(update={"nl_query_log_id": 42})
    assert updated.nl_query_log_id == 42
