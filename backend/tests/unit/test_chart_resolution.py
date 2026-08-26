"""실행 결과 기반 차트 확정 규칙 검증 — FR-D-04 (charts.py)."""

from datetime import UTC, date, datetime

from app.analytics.charts import resolve_visualization
from app.common.enums import ChartType
from app.common.tool_contracts import VisualizationPlan

_PLANNED_TABLE = VisualizationPlan(chart_type=ChartType.TABLE)
_PLANNED_BAR = VisualizationPlan(chart_type=ChartType.BAR)


def test_empty_rows_is_table():
    viz = resolve_visualization("아무거나", [], [], _PLANNED_BAR)
    assert viz.chart_type is ChartType.TABLE


def test_scalar_is_table():
    viz = resolve_visualization(
        "TRACE 알람은 전부 몇 건이야?", ["cnt"], [{"cnt": 138}], _PLANNED_TABLE
    )
    assert viz.chart_type is ChartType.TABLE


def test_categorical_plus_numeric_is_bar_with_axes():
    rows = [{"chamber": "PHO-01-C1", "cnt": 30}, {"chamber": "ETC-01-C1", "cnt": 12}]
    viz = resolve_visualization(
        "챔버별 알람 수", ["chamber", "cnt"], rows, _PLANNED_TABLE
    )
    assert viz.chart_type is ChartType.BAR
    assert viz.x == "chamber"
    assert viz.y == "cnt"


def test_bar_over_30_categories_demotes_to_table():
    rows = [{"k": f"C{i}", "cnt": i} for i in range(31)]
    viz = resolve_visualization("항목별 건수", ["k", "cnt"], rows, _PLANNED_BAR)
    assert viz.chart_type is ChartType.TABLE


def test_temporal_axis_becomes_line_without_keyword():
    rows = [{"day": datetime(2026, 6, d, tzinfo=UTC), "cnt": d} for d in (1, 2, 3, 4)]
    viz = resolve_visualization("일자별 알람", ["day", "cnt"], rows, _PLANNED_BAR)
    assert viz.chart_type is ChartType.LINE
    assert viz.x == "day"
    assert viz.y == "cnt"


def test_iso_string_and_date_count_as_temporal():
    rows = [
        {"day": "2026-06-01", "cnt": 1},
        {"day": "2026-06-02", "cnt": 2},
        {"day": "2026-06-03", "cnt": 3},
    ]
    assert (
        resolve_visualization("q", ["day", "cnt"], rows, None).chart_type
        is ChartType.LINE
    )
    rows2 = [{"day": date(2026, 6, d), "cnt": d} for d in (1, 2, 3)]
    assert (
        resolve_visualization("q", ["day", "cnt"], rows2, None).chart_type
        is ChartType.LINE
    )


def test_line_keyword_overrides_and_needs_two_rows():
    rows = [{"chamber": "A", "cnt": 1}, {"chamber": "B", "cnt": 2}]
    viz = resolve_visualization("챔버 추이 보여줘", ["chamber", "cnt"], rows, None)
    assert viz.chart_type is ChartType.LINE
    # 1행이면 추이가 성립하지 않는다 — 강등
    one = resolve_visualization("추이", ["chamber", "cnt"], rows[:1], None)
    assert one.chart_type is ChartType.TABLE


def test_single_numeric_column_many_rows_is_histogram_raw():
    rows = [{"value": float(i)} for i in range(25)]
    viz = resolve_visualization("value 조회", ["value"], rows, _PLANNED_TABLE)
    assert viz.chart_type is ChartType.HISTOGRAM
    assert viz.x == "value"
    assert viz.y is None


def test_histogram_keyword_with_prebinned_rows_sets_axes():
    rows = [{"bin": "0~10", "frequency": 4}, {"bin": "10~20", "frequency": 9}]
    viz = resolve_visualization(
        "분포를 히스토그램으로", ["bin", "frequency"], rows, None
    )
    assert viz.chart_type is ChartType.HISTOGRAM
    assert viz.x == "bin"
    assert viz.y == "frequency"


def test_histogram_without_numeric_demotes_to_table():
    rows = [{"name": "a"}, {"name": "b"}]
    viz = resolve_visualization("히스토그램으로", ["name"], rows, None)
    assert viz.chart_type is ChartType.TABLE


def test_bunpo_word_alone_is_not_histogram_keyword():
    # "분포"는 범주별 집계 의미로도 쓰인다 — 데이터 모양이 범주+숫자면 BAR 로 간다
    rows = [{"alarm_type": "OOC", "cnt": 52}]
    viz = resolve_visualization("규칙별 알람 분포", ["alarm_type", "cnt"], rows, None)
    assert viz.chart_type is ChartType.BAR


def test_few_numeric_rows_without_category_stays_table():
    rows = [{"value": 1.0}, {"value": 2.0}]  # 표본 20 미만 — 분포로 보지 않는다
    viz = resolve_visualization("값 조회", ["value"], rows, _PLANNED_TABLE)
    assert viz.chart_type is ChartType.TABLE
