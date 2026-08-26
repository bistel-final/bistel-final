"""'~별' 집계 질문의 GROUP BY 누락 lint 검증 — Q08 패턴 (FR-D-01·02 범위)."""

from app.analytics.service import _has_group_by, _has_group_signal, _needs_group_by_hint

_SQL_NO_GROUP = "SELECT AVG(value_mean) AS avg_mean FROM summary_data LIMIT 500"
_SQL_GROUPED = (
    "SELECT parameter, AVG(value_mean) AS avg_mean"
    " FROM summary_data GROUP BY parameter LIMIT 500"
)


def test_group_signal_positive_tokens():
    assert _has_group_signal("파라미터별 평균을 구해줘")
    assert _has_group_signal("챔버별로 알람 수 알려줘")
    assert _has_group_signal("equipment별 건수")
    # 신호 3종 확장 — '각 ~마다' · '~ 단위로'
    assert _has_group_signal("각 챔버마다 평균 값을 구해줘")
    assert _has_group_signal("설비 단위로 알람 수를 집계해줘")


def test_group_signal_blacklist_negative():
    # 그룹 의미가 아닌 '별' 단어는 오탐하지 않는다
    assert not _has_group_signal("특별한 알람만 보여줘")
    assert not _has_group_signal("각별히 주의할 값")
    assert not _has_group_signal("이별 노래 가사")
    assert not _has_group_signal("별다른 조건 없이 전체 조회")


def test_has_group_by_detection():
    assert _has_group_by(_SQL_GROUPED)
    assert not _has_group_by(_SQL_NO_GROUP)
    # 파싱 불가 SQL 은 lint 를 걸지 않는다 (validator 의 몫)
    assert _has_group_by("this is not sql")


def test_needs_hint_q08_pattern():
    # Q08: '~별' + 집계 어휘 + GROUP BY 없음 → 힌트
    assert _needs_group_by_hint(
        "파라미터별 summary 평균값의 평균을 구해줘", _SQL_NO_GROUP
    )


def test_no_hint_when_grouped_or_no_signal():
    # 이미 GROUP BY 가 있으면 힌트 없음
    assert not _needs_group_by_hint("파라미터별 평균을 구해줘", _SQL_GROUPED)
    # 그룹 신호가 없으면 힌트 없음
    assert not _needs_group_by_hint("전체 평균을 구해줘", _SQL_NO_GROUP)


def test_no_hint_for_listing_questions():
    # 그룹 신호가 있어도 집계 어휘가 없으면(목록 조회) 재생성하지 않는다
    listing_sql = "SELECT alarm_id, chamber FROM trace_alarm_history LIMIT 500"
    assert not _needs_group_by_hint("챔버별 알람을 나열해줘", listing_sql)
    assert not _needs_group_by_hint("챔버별 알람 목록 보여줘", listing_sql)
