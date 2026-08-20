"""Detection Service (V5-A-1.1·V5-A-1.2).

시스템설계서 v2.1 1.3 계층 규칙: Service는 트랜잭션 경계·업무 흐름을 담당하고
문자열 SQL을 직접 조립하지 않는다. 여기서는 `repository.py`가 읽은 데이터를
`summarize.py`(Rules, 순수 함수)에 넘기고, 그 결과를 reference 테이블과 대조해
완료 기준을 판정한다.

완료 기준 근거 (시스템설계서 v2.1 4.1~4.2, docs/ai-context/tasks/A-detection.md):
- V5-A-1.1 Summary 재계산: `summary_data` 4,800건과 key·point_cnt가 완전히 같고
  수치는 0.001 이내.
- V5-A-1.2 evaluation 재현: IN 4,538 / OOC 216 / OOS 46, TRACE alarm(OOS point 합)
  138건.

이 모듈은 DB에 쓰지 않는다(base 9 table은 bootstrap이 적재한 reference라 A가
덮어쓰지 않는다). 재계산·대조 결과만 반환한다.

[팀원용 요약]
이 파일은 "채점기"라고 생각하면 된다. 순서는 항상 같다.
  1) repository.py로 원본(fdc_trace, dim_parameter)을 읽는다.
  2) summarize.py(순수 함수)에 넣어서 우리가 직접 계산한 값을 만든다.
  3) repository.py로 "정답"(summary_data, evaluation)을 읽는다.
  4) 계산값과 정답을 key(=GroupKey, 즉 lot_hist_id+parameter_id+recipe_step_no)
     별로 짝지어서 하나하나 비교한다.
  5) 다른 게 있으면 "무엇이 어떻게 다른지"를 리스트(mismatches)에 쌓아서 반환한다.
verify_*() 함수들은 절대 예외를 던지며 중간에 멈추지 않는다 — 불일치가 있어도
끝까지 다 비교한 뒤, 결과 객체의 mismatches/ok 필드로 알려준다. 그래야 "몇 개가
왜 틀렸는지" 한 번에 확인할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Connection

from app.common.enums import AlarmType
from app.detection import repository
from app.detection.summarize import (
    GroupKey,
    evaluate_groups,
    summarize_groups,
)

__all__ = [
    "SummaryMismatch",
    "SummaryVerificationResult",
    "EvaluationMismatch",
    "EvaluationVerificationResult",
    "verify_summary_recalculation",
    "verify_evaluation_recalculation",
]


def _key_sort(key: GroupKey) -> tuple[str, str, int]:
    # sorted(..., key=_key_sort)에 넘길 정렬 기준. GroupKey 자체에는 순서 비교가
    # 정의돼 있지 않으므로(그냥 값 3개를 담은 불변 객체일 뿐), "lot_hist_id ->
    # parameter_id -> recipe_step_no" 순서로 비교 가능한 튜플로 바꿔준다.
    # 이렇게 하면 결과를 출력하거나 로그로 남길 때 항상 같은 순서가 나와서
    # (재현성) 사람이 diff를 보기 쉽다.
    return (key.lot_hist_id, key.parameter_id, key.recipe_step_no)


# ---------------------------------------------------------------------
# V5-A-1.1 — Summary 재계산 대조
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SummaryMismatch:
    """`summary_data` reference와 재계산 결과가 어긋난 필드 하나."""

    # 한 번의 불일치를 "어떤 그룹(key)의 어떤 필드(field)가 얼마(recomputed) 나왔는데
    # 정답은 얼마(reference)였고 그 차이(diff)는 얼마인가"로 기록한다.
    # field는 "point_cnt" / "value_mean" / "value_min" / "value_max" / "value_std"
    # 중 하나의 문자열이 들어간다.
    key: GroupKey
    field: str
    recomputed: float | None
    reference: float | None
    diff: float | None


@dataclass(frozen=True, slots=True)
class SummaryVerificationResult:
    recomputed_count: int
    reference_count: int
    missing_keys: list[GroupKey]  # reference에는 있는데 재계산에는 없는 key
    unexpected_keys: list[GroupKey]  # 재계산에는 있는데 reference에는 없는 key
    mismatches: list[SummaryMismatch]

    @property
    def ok(self) -> bool:
        """V5-A-1.1 완료 기준: key·point_cnt 완전 일치 + 수치 0.001 이내."""

        # @property라서 result.ok()가 아니라 result.ok 처럼 괄호 없이 접근한다.
        # 아래 4개 조건이 "전부" 참이어야 진짜로 통과다: 그룹 개수가 같고,
        # 빠진 key도 없고, 엉뚱하게 더 생긴 key도 없고, 값 차이(mismatches)도
        # 하나도 없어야 한다. 이 중 하나라도 어긋나면 False.
        return (
            self.recomputed_count == self.reference_count
            and not self.missing_keys
            and not self.unexpected_keys
            and not self.mismatches
        )


def verify_summary_recalculation(
    connection: Connection,
    *,
    tolerance: float = 0.001,
) -> SummaryVerificationResult:
    """`fdc_trace`에서 Summary를 재계산해 `summary_data` reference와 대조한다.

    시스템설계서 v2.1 4.1: "최종 summary_data 4,800행과 key·point count가
    완전히 같고 수치는 0.001 이내여야 한다." `tolerance` 기본값이 그 값이다.
    """

    # 1) 원본을 읽는다 (repository.py 담당).
    points = repository.fetch_trace_points(connection)
    # 2) summarize.py 순수 함수로 우리가 직접 계산한다. summarize_groups는
    #    GroupSummary 리스트를 주는데, 이후 key로 빠르게 찾아 쓰기 위해
    #    "{key: 그 요약값}" 형태의 dict로 다시 감싼다. (summary.key는
    #    GroupSummary 안에 이미 들어있는 GroupKey 필드다.)
    recomputed = {summary.key: summary for summary in summarize_groups(points)}
    # 3) "정답"을 읽는다. fetch_reference_summary가 이미 {key: 값} dict를 준다.
    reference = repository.fetch_reference_summary(connection)

    # 4) 두 dict의 key 집합(set)을 비교한다.
    #    - missing_keys: 정답(reference)에는 있는데 우리가 계산한 결과에는 없는
    #      그룹 -> "우리가 놓친 그룹"
    #    - unexpected_keys: 반대로 우리 계산에만 있고 정답에는 없는 그룹 ->
    #      "우리가 잘못 만들어낸 그룹"
    #    두 집합 다 비어 있어야 정상이다.
    recomputed_keys = set(recomputed)
    reference_keys = set(reference)
    missing_keys = sorted(reference_keys - recomputed_keys, key=_key_sort)
    unexpected_keys = sorted(recomputed_keys - reference_keys, key=_key_sort)

    mismatches: list[SummaryMismatch] = []
    # 5) 양쪽에 공통으로 있는 key(교집합, &)만 실제 값 비교를 한다. 정렬해서
    #    도는 이유는 mismatches 리스트가 매번 같은 순서로 나오게 하기 위함이다
    #    (재현성 — 같은 입력이면 언제 실행해도 같은 출력).
    for key in sorted(recomputed_keys & reference_keys, key=_key_sort):
        actual = recomputed[key]  # 우리가 방금 계산한 값 (GroupSummary)
        expected = reference[key]  # DB에 이미 있던 정답 (ReferenceSummaryRow)

        # point_cnt는 정수라서 오차 허용 없이 완전히 같아야 한다("0.001 이내"는
        # 소수점이 있는 통계값에만 적용되는 얘기다).
        if actual.point_cnt != expected.point_cnt:
            mismatches.append(
                SummaryMismatch(
                    key=key,
                    field="point_cnt",
                    recomputed=float(actual.point_cnt),
                    reference=float(expected.point_cnt),
                    diff=float(abs(actual.point_cnt - expected.point_cnt)),
                )
            )

        # value_mean·value_min·value_max는 부동소수점이라 완전히 똑같지 않을 수
        # 있으므로(계산 순서 등으로 아주 작은 오차가 생길 수 있음), 차이가
        # tolerance(기본 0.001)를 "넘을 때만" 불일치로 기록한다. getattr로
        # 필드 이름 문자열을 그대로 써서 세 필드를 반복문 하나로 처리한다.
        for field in ("value_mean", "value_min", "value_max"):
            actual_value: float = getattr(actual, field)
            expected_value: float = getattr(expected, field)
            diff = abs(actual_value - expected_value)
            if diff > tolerance:
                mismatches.append(
                    SummaryMismatch(
                        key=key,
                        field=field,
                        recomputed=actual_value,
                        reference=expected_value,
                        diff=diff,
                    )
                )

        # value_std: point_cnt < 2 면 None(표본표준편차 미정의). 한쪽만 None이면
        # 불일치, 둘 다 None이면 통과, 둘 다 값이 있으면 tolerance로 비교한다.
        # (None != None은 파이썬에서 False이므로 "둘 다 None"이면 아래 첫 번째
        # if는 실행되지 않고 그냥 통과한다 — 의도한 동작이다.)
        if (actual.value_std is None) != (expected.value_std is None):
            # 한쪽만 None인 경우: 예를 들어 우리는 point가 1개라 std=None인데
            # reference는 값이 있다면, 애초에 point_cnt부터 달랐을 가능성이 높다
            # (위에서 이미 point_cnt mismatch로도 잡혔을 것이다). 그래도 std
            # 자체도 명시적으로 기록해서 "무엇이 얼마나" 다른지 숨기지 않는다.
            mismatches.append(
                SummaryMismatch(
                    key=key,
                    field="value_std",
                    recomputed=actual.value_std,
                    reference=expected.value_std,
                    diff=None,
                )
            )
        elif actual.value_std is not None and expected.value_std is not None:
            std_diff = abs(actual.value_std - expected.value_std)
            if std_diff > tolerance:
                mismatches.append(
                    SummaryMismatch(
                        key=key,
                        field="value_std",
                        recomputed=actual.value_std,
                        reference=expected.value_std,
                        diff=std_diff,
                    )
                )

    # 6) 지금까지 모은 것들을 한 결과 객체로 묶어서 반환한다. 호출자는
    #    result.ok 하나만 봐도 되고, 실패했다면 result.mismatches를 순회해서
    #    구체적으로 뭐가 틀렸는지 확인할 수 있다.
    return SummaryVerificationResult(
        recomputed_count=len(recomputed),
        reference_count=len(reference),
        missing_keys=missing_keys,
        unexpected_keys=unexpected_keys,
        mismatches=mismatches,
    )


# ---------------------------------------------------------------------
# V5-A-1.2 — evaluation 재현 대조
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EvaluationMismatch:
    """`evaluation` reference와 재계산 결과가 어긋난 필드 하나."""

    # SummaryMismatch와 구조는 같은데, evaluation 쪽 필드는 전부 정수이거나
    # AlarmType(IN/OOC/OOS)이라서 diff(오차량) 개념이 없다 — 그래서 diff 필드가
    # 없고, 대신 recomputed/reference 값 자체를 그대로 보여준다("다르다/같다"만
    # 판단하면 되는 값이므로).
    key: GroupKey
    field: str
    recomputed: int | AlarmType
    reference: int | AlarmType


@dataclass(frozen=True, slots=True)
class EvaluationVerificationResult:
    recomputed_count: int
    reference_count: int
    missing_keys: list[GroupKey]
    unexpected_keys: list[GroupKey]
    mismatches: list[EvaluationMismatch]
    # {AlarmType.IN: n건, AlarmType.OOC: n건, AlarmType.OOS: n건} — 우리가
    # 재계산한 결과의 등급별 그룹 개수. 이걸로 "IN 4,538 / OOC 216 / OOS 46"
    # 수용값과 한눈에 비교할 수 있다.
    recomputed_alarm_type_counts: dict[AlarmType, int]
    # OOS point 합 = TRACE alarm 발행 건수(설계서 v2.1 4.2 "OOS raw point마다
    # TRACE alarm 한 건을 만든다"). 실제 trace_alarm_history 저장은 V5-A-1.3 범위다.
    recomputed_trace_alarm_count: int

    @property
    def ok(self) -> bool:
        """V5-A-1.2 완료 기준: key 완전 일치 + point_cnt/ooc/oos/alarm_type 일치."""

        return (
            self.recomputed_count == self.reference_count
            and not self.missing_keys
            and not self.unexpected_keys
            and not self.mismatches
        )

    @property
    def matches_acceptance_values(self) -> bool:
        """수용값(IN 4,538 / OOC 216 / OOS 46, TRACE alarm 138)과 일치하는지.

        `ok`와 별개로 둔 이유: 최종 데이터가 아닌 DB에 연결됐을 때(예: 검증되지
        않은 `fdc_final`) `ok`는 참이어도 이 값은 거짓일 수 있다 — 그 자체가
        "확정 전 데이터로 개발 중"이라는 신호가 된다.
        """

        # ok는 "우리 계산과 DB의 evaluation 테이블이 서로 일치하느냐"만 본다.
        # 하지만 DB의 evaluation 테이블 자체가 최종 확정 데이터가 아니라면(예:
        # 검증 전 fdc_final), ok=True이면서도 숫자 자체는 프로젝트가 못박은
        # 수용값(4538/216/46/138)과 다를 수 있다. 그래서 이 프로퍼티를 따로 둬서
        # "내부적으로 앞뒤가 맞다"와 "프로젝트가 정한 최종 숫자와 맞다"를 구분한다.
        counts = self.recomputed_alarm_type_counts
        return (
            counts.get(AlarmType.IN, 0) == 4538
            and counts.get(AlarmType.OOC, 0) == 216
            and counts.get(AlarmType.OOS, 0) == 46
            and self.recomputed_trace_alarm_count == 138
        )


def verify_evaluation_recalculation(connection: Connection) -> EvaluationVerificationResult:
    """`fdc_trace`+`dim_parameter`로 evaluation을 재현해 `evaluation` reference와 대조한다.

    시스템설계서 v2.1 4.2 규칙(OOS→OOC→IN 우선순위, upper_only 하한 미판정)은
    `summarize.judge_point`·`summarize.evaluate_group`이 이미 구현하고 있으므로
    이 함수는 조회·대조만 한다.
    """

    # verify_summary_recalculation과 흐름은 똑같다: 원본 읽기 -> 계산 ->
    # 정답 읽기 -> key 집합 비교 -> 값 비교. 다른 점은 evaluate_groups가
    # dim_parameter(limits)도 함께 필요하다는 것뿐이다(OOS/OOC 판정 기준선).
    points = repository.fetch_trace_points(connection)
    limits = repository.fetch_parameter_limits(connection)
    recomputed = {
        evaluation.key: evaluation for evaluation in evaluate_groups(points, limits)
    }
    reference = repository.fetch_reference_evaluation(connection)

    recomputed_keys = set(recomputed)
    reference_keys = set(reference)
    missing_keys = sorted(reference_keys - recomputed_keys, key=_key_sort)
    unexpected_keys = sorted(recomputed_keys - reference_keys, key=_key_sort)

    mismatches: list[EvaluationMismatch] = []
    for key in sorted(recomputed_keys & reference_keys, key=_key_sort):
        actual = recomputed[key]
        expected = reference[key]

        # 4개 필드(point_cnt·ooc_point_cnt·oos_point_cnt·alarm_type)를 하나씩
        # 비교한다. 전부 정수 또는 enum이라 "같다/다르다"만 확인하면 되고,
        # Summary처럼 오차 허용(tolerance)은 필요 없다.
        if actual.point_cnt != expected.point_cnt:
            mismatches.append(
                EvaluationMismatch(key, "point_cnt", actual.point_cnt, expected.point_cnt)
            )
        if actual.ooc_point_cnt != expected.ooc_point_cnt:
            mismatches.append(
                EvaluationMismatch(
                    key, "ooc_point_cnt", actual.ooc_point_cnt, expected.ooc_point_cnt
                )
            )
        if actual.oos_point_cnt != expected.oos_point_cnt:
            mismatches.append(
                EvaluationMismatch(
                    key, "oos_point_cnt", actual.oos_point_cnt, expected.oos_point_cnt
                )
            )
        if actual.alarm_type != expected.alarm_type:
            mismatches.append(
                EvaluationMismatch(key, "alarm_type", actual.alarm_type, expected.alarm_type)
            )

    # 등급별 집계용 카운터를 미리 0으로 초기화해둔다(IN/OOC/OOS 셋 다 0건이어도
    # dict에 키가 존재하게 하려는 목적 — 나중에 counts.get(AlarmType.IN, 0)
    # 대신 counts[AlarmType.IN]으로 접근해도 KeyError가 안 난다).
    alarm_type_counts: dict[AlarmType, int] = {
        AlarmType.IN: 0,
        AlarmType.OOC: 0,
        AlarmType.OOS: 0,
    }
    trace_alarm_count = 0
    # 우리가 재계산한 4,800개 그룹을 전부 훑으면서, 그룹의 최종 등급(alarm_type)
    # 개수를 세고, OOS point 개수를 전부 더한다. oos_point_cnt를 더하는 이유는
    # "OOS raw point 하나당 TRACE alarm 하나"라서, 이 합계가 곧 TRACE alarm
    # 총 건수(수용값 138)와 같아야 하기 때문이다.
    for evaluation in recomputed.values():
        alarm_type_counts[evaluation.alarm_type] += 1
        trace_alarm_count += evaluation.oos_point_cnt

    return EvaluationVerificationResult(
        recomputed_count=len(recomputed),
        reference_count=len(reference),
        missing_keys=missing_keys,
        unexpected_keys=unexpected_keys,
        mismatches=mismatches,
        recomputed_alarm_type_counts=alarm_type_counts,
        recomputed_trace_alarm_count=trace_alarm_count,
    )
