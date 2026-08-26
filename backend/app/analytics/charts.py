"""실행 결과 기반 차트 확정 — FR-D-04 (table·bar·line·histogram).

차트 결정권은 백엔드 계획에 있다("UI 임의 재판단 없음"). planner 는 실행
전이라 질문 키워드와 GROUP BY 만 보지만, 여기서는 실행된 rows 의 실제
모양으로 차트를 확정하고 x·y 축까지 채운다.

규칙 (우선순위 순)
    1. 사용자 명시 의도 — 질문에 히스토그램 → HISTOGRAM, 추이/시계열 → LINE
       ("분포"는 한국어에서 범주별 집계 의미로도 쓰이므로 강제하지 않고
       데이터 모양 판정에 맡긴다)
    2. 데이터 모양 자동 판정 (명시 없을 때)
       - 범주 1 + 숫자 ≥1, 범주가 시간형이고 3행 이상 → LINE
       - 숫자 단일 컬럼 20행 이상 → HISTOGRAM (분포 데이터)
       - 범주 1 + 숫자 ≥1 → BAR
    3. 타입·축 호환성 가드 — 지정 차트가 데이터와 맞지 않으면 TABLE 로
       강등한다 (BAR 범주 30 초과 포함). 애매하면 항상 TABLE.

LLM 을 쓰지 않는다 — 차트 선택은 규칙으로 결정 가능한 문제이고,
결정론이어야 평가·테스트로 고정된다.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.common.enums import ChartType
from app.common.tool_contracts import VisualizationPlan

_HISTOGRAM_WORDS: tuple[str, ...] = ("히스토그램", "histogram")
_LINE_WORDS: tuple[str, ...] = ("추이", "시계열", "trend")

#: BAR 가독성 상한 — 범주가 이보다 많으면 표가 낫다.
BAR_MAX_CATEGORIES = 30
#: 분포로 인정하는 최소 표본 수.
HISTOGRAM_MIN_ROWS = 20
#: 추이로 인정하는 최소 포인트 수.
LINE_MIN_ROWS = 3

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _is_temporal(value: Any) -> bool:
    if isinstance(value, datetime | date):
        return True
    return isinstance(value, str) and bool(_ISO_DATE_RE.match(value))


def _explicit_intent(question: str) -> ChartType | None:
    q = question.lower()
    if any(word in q for word in _HISTOGRAM_WORDS):
        return ChartType.HISTOGRAM
    if any(word in q for word in _LINE_WORDS):
        return ChartType.LINE
    return None


def resolve_visualization(
    question: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    planned: VisualizationPlan | None,
) -> VisualizationPlan:
    """실행 결과로 차트를 확정한다. 항상 유효한 계획을 돌려준다 (기본 TABLE)."""
    if not rows or not columns:
        return VisualizationPlan(chart_type=ChartType.TABLE)

    first = rows[0]
    numeric = [c for c in columns if _is_number(first.get(c))]
    categorical = [c for c in columns if c not in numeric]

    desired = _explicit_intent(question)
    if desired is None:
        planned_type = planned.chart_type if planned else ChartType.TABLE
        if (
            len(categorical) == 1
            and numeric
            and _is_temporal(first.get(categorical[0]))
            and len(rows) >= LINE_MIN_ROWS
        ):
            desired = ChartType.LINE
        elif len(columns) == 1 and numeric and len(rows) >= HISTOGRAM_MIN_ROWS:
            desired = ChartType.HISTOGRAM
        elif categorical and numeric:
            desired = ChartType.BAR
        else:
            desired = planned_type

    # ── 타입·축 호환성 가드 — 맞지 않으면 TABLE 강등 ────────────────────
    if desired is ChartType.LINE:
        if categorical and numeric and len(rows) >= 2:
            return VisualizationPlan(
                chart_type=ChartType.LINE, x=categorical[0], y=numeric[0]
            )
        return VisualizationPlan(chart_type=ChartType.TABLE)

    if desired is ChartType.BAR:
        if categorical and numeric and len(rows) <= BAR_MAX_CATEGORIES:
            return VisualizationPlan(
                chart_type=ChartType.BAR, x=categorical[0], y=numeric[0]
            )
        return VisualizationPlan(chart_type=ChartType.TABLE)

    if desired is ChartType.HISTOGRAM:
        if not numeric:
            return VisualizationPlan(chart_type=ChartType.TABLE)
        if categorical:
            # SQL 사전 비닝 형태 (bin 라벨 + 빈도)
            return VisualizationPlan(
                chart_type=ChartType.HISTOGRAM, x=categorical[0], y=numeric[0]
            )
        # raw 값 형태 — 비닝은 렌더링의 몫이다
        return VisualizationPlan(chart_type=ChartType.HISTOGRAM, x=numeric[0], y=None)

    return VisualizationPlan(chart_type=ChartType.TABLE)
