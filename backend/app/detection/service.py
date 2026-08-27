"""Detection Service (V5-A-1.1~V5-A-1.5).

시스템설계서 v2.1 1.3 계층 규칙: Service는 트랜잭션 경계·업무 흐름을 담당하고
문자열 SQL을 직접 조립하지 않는다. 여기서는 `repository.py`가 읽은 데이터를
`summarize.py`·`rules.py`(Rules, 순수 함수)에 넘기고, 그 결과를 reference
테이블과 대조하거나(V5-A-1.3) 실제로 적재해(V5-A-1.4) 완료 기준을 판정한다.

완료 기준 근거 (시스템설계서 v2.1 4.1~4.3·3.2, docs/ai-context/tasks/A-detection.md):
- V5-A-1.1 Summary 재계산: `summary_data` 4,800건과 key·point_cnt가 완전히 같고
  수치는 0.001 이내.
- V5-A-1.2 evaluation 재현: IN 4,538 / OOC 216 / OOS 46, TRACE alarm(OOS point 합)
  138건.
- V5-A-1.3 TRACE·SUMMARY 알람 재현: TRACE 138·SUMMARY 51, 저장 알람 합계 189,
  occurred_at NULL 0건.
- V5-A-1.4 R03 파생·적재: 연속 3 OOS로 R03 3건을 만들어 `r03_alarm_history`에
  멱등 적재한다. 각 R03는 member wafer 3개·TRACE AlarmRef 9개를 가진다.

이 파일 아래쪽의 함수는 두 그룹으로 나뉜다.
  - V5-A-1.1~1.3 절: 전부 읽기 전용이다(base 9 table은 bootstrap이 적재한
    reference라 A가 덮어쓰지 않는다). 재계산·대조 결과만 반환한다.
  - V5-A-1.4 절: `persist_r03_alarms`만 쓰기 함수다. `r03_alarm_history`는
    base 9 table이 아니라 A가 직접 채우는 reference extension이기 때문이다
    (`repository.insert_r03_alarms` docstring 참고).

[팀원용 요약]
이 파일은 "채점기"라고 생각하면 된다. 순서는 항상 같다.
  1) repository.py로 원본(fdc_trace, dim_parameter, lot_history, ...)을 읽는다.
  2) summarize.py·rules.py(순수 함수)에 넣어서 우리가 직접 계산한 값을 만든다.
  3) repository.py로 "정답"(summary_data, evaluation, trace_alarm_history 등)을 읽는다.
  4) 계산값과 정답을 key별로 짝지어서 하나하나 비교한다.
  5) 다른 게 있으면 "무엇이 어떻게 다른지"를 리스트(mismatches)에 쌓아서 반환한다.
verify_*() 함수들은 절대 예외를 던지며 중간에 멈추지 않는다 — 불일치가 있어도
끝까지 다 비교한 뒤, 결과 객체의 mismatches/ok 필드로 알려준다. 그래야 "몇 개가
왜 틀렸는지" 한 번에 확인할 수 있다. R03만 예외로, "저장할 값을 만드는" 단계
(`derive_r03_alarm_records`)에서 데이터 정합성이 깨지면(예: track_in_at 없음)
즉시 예외를 던진다 — 잘못된 R03을 "일단 저장"하면 되돌리기가 더 어렵다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from sqlalchemy.engine import Connection

from app.common.enums import AlarmType
from app.common.tool_contracts import (
    AnomalySignal,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)
from app.detection import model as anomaly_model
from app.detection import repository, rules
from app.detection.model_artifact import LoadedModel, load_latest_model
from app.detection.summarize import (
    GroupKey,
    ParameterLimit,
    evaluate_groups,
    summarize_groups,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SummaryMismatch",
    "SummaryVerificationResult",
    "EvaluationMismatch",
    "EvaluationVerificationResult",
    "verify_summary_recalculation",
    "verify_evaluation_recalculation",
    "TraceAlarmVerificationResult",
    "SummaryAlarmVerificationResult",
    "AlarmReproductionResult",
    "verify_trace_alarm_reproduction",
    "verify_summary_alarm_reproduction",
    "verify_alarm_reproduction",
    "R03DerivationResult",
    "R03PersistResult",
    "derive_r03_events",
    "derive_r03_alarm_records",
    "persist_r03_alarms",
    "IncidentReferenceMismatch",
    "IncidentVerificationResult",
    "verify_incident_aggregation",
    "FdcSummaryService",
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


# =====================================================================
# V5-A-1.3 — TRACE·SUMMARY 알람 재현 대조
#
# trace_alarm_history·summary_alarm_history는 summary_data·evaluation과 같은
# "이미 저장된 정답"이다. 그래서 이 절도 A-1.1·A-1.2와 같은 패턴을 따른다 —
# fdc_trace에서 다시 계산 -> reference 테이블과 건수 대조. 다른 점은 TRACE는
# A-1.2가 이미 계산해 둔 값(oos_point_cnt 합)을 재사용하면 되고, SUMMARY는
# rules.py의 동적 관리한계 계산이 새로 필요하다는 것뿐이다.
# =====================================================================
@dataclass(frozen=True, slots=True)
class TraceAlarmVerificationResult:
    """TRACE 알람(raw 규격 이탈) 재현 대조 결과."""

    # V5-A-1.2의 EvaluationVerificationResult.recomputed_trace_alarm_count와
    # 계산식이 완전히 같다(OOS point 총합). 여기서 다시 계산하는 이유는 이
    # 결과가 "V5-A-1.3의 완료 기준"이라는 걸 이름과 타입으로 분명히 하기
    # 위해서다 — A-1.2 결과 객체를 그대로 재사용하면 "TRACE 알람 재현"이라는
    # 별도 완료 기준이 있다는 사실이 코드에 드러나지 않는다.
    recomputed_count: int
    reference_count: int  # trace_alarm_history 실제 저장 건수
    reference_occurred_at_null_count: int

    @property
    def ok(self) -> bool:
        """recomputed(=OOS point 합)와 reference 저장 건수가 같고 NULL이 0건인가."""

        return (
            self.recomputed_count == self.reference_count
            and self.reference_occurred_at_null_count == 0
        )

    @property
    def matches_acceptance_value(self) -> bool:
        """수용값(TRACE 138)과 일치하는지. `ok`와 분리하는 이유는 evaluation과 같다."""

        return self.reference_count == 138


@dataclass(frozen=True, slots=True)
class SummaryAlarmVerificationResult:
    """SUMMARY 알람(동적 관리한계 이탈) 재현 대조 결과."""

    recomputed_count: int  # rules.build_summary_alarm_flags 결과 길이
    reference_count: int  # summary_alarm_history 실제 저장 건수
    reference_occurred_at_null_count: int

    @property
    def ok(self) -> bool:
        return (
            self.recomputed_count == self.reference_count
            and self.reference_occurred_at_null_count == 0
        )

    @property
    def matches_acceptance_value(self) -> bool:
        """수용값(SUMMARY 51)과 일치하는지."""

        return self.reference_count == 51


@dataclass(frozen=True, slots=True)
class AlarmReproductionResult:
    """V5-A-1.3 완료 기준 전체(TRACE+SUMMARY) 판정."""

    trace: TraceAlarmVerificationResult
    summary: SummaryAlarmVerificationResult

    @property
    def total_stored_alarms(self) -> int:
        """설계서 4.1: "TRACE와 SUMMARY의 base alarm은 189건이다." 수용값 189."""

        return self.trace.reference_count + self.summary.reference_count

    @property
    def ok(self) -> bool:
        return self.trace.ok and self.summary.ok

    @property
    def matches_acceptance_values(self) -> bool:
        return (
            self.trace.matches_acceptance_value
            and self.summary.matches_acceptance_value
            and self.total_stored_alarms == 189
        )


def verify_trace_alarm_reproduction(
    connection: Connection,
) -> TraceAlarmVerificationResult:
    """`fdc_trace`+`dim_parameter`로 TRACE 알람 건수를 재현해 reference와 대조한다.

    설계서 4.2-6: "OOS raw point마다 TRACE alarm 한 건을 만든다." 이미 V5-A-1.2가
    이 값을 계산해뒀으므로(evaluate_groups의 oos_point_cnt 총합) 여기서는 그
    계산을 그대로 재사용하고, `trace_alarm_history` 저장 건수·occurred_at NULL
    건수만 새로 읽어서 대조한다.
    """

    points = repository.fetch_trace_points(connection)
    limits = repository.fetch_parameter_limits(connection)
    evaluations = evaluate_groups(points, limits)
    recomputed_count = sum(evaluation.oos_point_cnt for evaluation in evaluations)

    reference_stats = repository.fetch_trace_alarm_reference_stats(connection)

    return TraceAlarmVerificationResult(
        recomputed_count=recomputed_count,
        reference_count=reference_stats.count,
        reference_occurred_at_null_count=reference_stats.occurred_at_null_count,
    )


def verify_summary_alarm_reproduction(
    connection: Connection,
) -> SummaryAlarmVerificationResult:
    """`summary_data`+`evaluation`으로 SUMMARY 알람(동적 CL±3σ)을 재현해 대조한다.

    설계서 4.3 규칙(`rules.compute_summary_control_limits`·`judge_summary_alarm`이
    이미 구현하고 있다)을 그대로 적용한다. 이 함수는 입력을 모아 넘기고 대조만
    한다.
    """

    # 1) summary_data 통계값(chamber_id 포함)을 읽는다.
    stat_rows = repository.fetch_summary_statistics(connection)

    # 2) baseline 후보 여부(evaluation != OOS)를 알아야 하므로, evaluation도
    #    fdc_trace에서 재계산한다 — reference `evaluation` 테이블을 다시 읽지
    #    않는 이유는, A-1.2가 이미 "재계산 결과 == reference"를 증명했으므로
    #    재계산 값을 그대로 믿고 써도 되기 때문이다(중복 조회를 줄인다).
    points = repository.fetch_trace_points(connection)
    limits = repository.fetch_parameter_limits(connection)
    evaluations_by_key = {
        evaluation.key: evaluation for evaluation in evaluate_groups(points, limits)
    }

    # 3) SummaryStatRow(순수 DB 조회 결과) + evaluation 등급 -> rules.SummaryStatPoint
    #    (판정 입력)로 조립한다. 이 조립은 "무슨 규칙을 적용할지"를 결정하지
    #    않고 입력 모양만 맞추는 일이라 service.py가 한다(rules.py는 DB를 모른다).
    summary_points: list[rules.SummaryStatPoint] = []
    for row in stat_rows:
        evaluation = evaluations_by_key.get(row.key)
        if evaluation is None:
            # A-1.1·A-1.2가 이미 summary_data·evaluation·fdc_trace가 같은
            # 4,800개 그룹을 가리킨다는 걸 증명했으므로 이 경로는 정상 데이터에서
            # 발생하지 않는다. 그래도 조용히 건너뛰지 않고 바로 알 수 있게 한다.
            raise ValueError(
                f"evaluation 재계산 결과에 없는 summary_data 그룹입니다: {row.key}"
            )
        summary_points.append(
            rules.SummaryStatPoint(
                key=row.key,
                chamber_id=row.chamber_id,
                value_mean=row.value_mean,
                baseline_eligible=evaluation.alarm_type is not AlarmType.OOS,
            )
        )

    # 4) 순수 규칙 함수 호출. 알람이 발생한 그룹만 dict로 돌아온다.
    recomputed_flags = rules.build_summary_alarm_flags(summary_points)

    reference_stats = repository.fetch_summary_alarm_reference_stats(connection)

    return SummaryAlarmVerificationResult(
        recomputed_count=len(recomputed_flags),
        reference_count=reference_stats.count,
        reference_occurred_at_null_count=reference_stats.occurred_at_null_count,
    )


def verify_alarm_reproduction(connection: Connection) -> AlarmReproductionResult:
    """V5-A-1.3 완료 기준(TRACE 138·SUMMARY 51·합계 189·NULL 0건)을 한 번에 판정한다."""

    return AlarmReproductionResult(
        trace=verify_trace_alarm_reproduction(connection),
        summary=verify_summary_alarm_reproduction(connection),
    )


# =====================================================================
# V5-A-1.4 — R03 파생·적재
#
# 이 절만 세 단계로 나뉜다(A-1.1~1.3의 "계산 -> 대조"와 다르다):
#   1) derive_r03_events        : 순수 계산. 어디서 연속 3 OOS가 나오는지 찾는다.
#   2) derive_r03_alarm_records : derive_r03_events 결과 + trace_alarm_history
#                                 조회로, DB에 넣을 완성된 행(alarm_id 포함)을 만든다.
#   3) persist_r03_alarms       : 2)의 결과를 실제로 저장한다(유일한 쓰기 함수).
# 1)·2)는 readonly Connection으로 충분하고, 3)만 쓰기 Connection이 필요하다.
# =====================================================================
@dataclass(frozen=True, slots=True)
class R03DerivationResult:
    """연속 3 OOS 탐색 결과(아직 저장 전, alarm_id 없음)."""

    events: list[rules.R03Event]

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def matches_acceptance_value(self) -> bool:
        """수용값(R03 3건)과 일치하는지."""

        return self.count == 3


@dataclass(frozen=True, slots=True)
class R03PersistResult:
    """`r03_alarm_history` 적재 결과."""

    derived_count: int  # 이번 계산으로 찾아낸 R03 event 수
    inserted_count: int  # 이번 호출로 실제 새로 INSERT된 행 수(재실행이면 0)
    total_count_after: int  # INSERT 이후 테이블 전체 행 수

    @property
    def matches_acceptance_value(self) -> bool:
        """수용값(R03 3건)이 테이블에 실제로 있는지."""

        return self.total_count_after == 3


def derive_r03_events(connection: Connection) -> R03DerivationResult:
    """`fdc_trace`+`dim_parameter`+`lot_history`로 연속 3 OOS(R03)를 찾는다.

    읽기 전용이다. `evaluate_groups`(V5-A-1.2)가 만드는 그룹별 등급을
    lot_history의 chamber_id·chamber_wafer_cum과 엮어서, rules.py가 정의한
    "(chamber_id, parameter_id, recipe_step_no) 그룹 안에서 chamber_wafer_cum
    오름차순 연속 3"을 찾는 입력으로 변환하는 것까지가 이 함수의 일이다.
    실제 연속 판정 로직은 `rules.derive_r03_events`(순수 함수)가 담당한다.
    """

    points = repository.fetch_trace_points(connection)
    limits = repository.fetch_parameter_limits(connection)
    evaluations = evaluate_groups(points, limits)
    lot_history_by_id = repository.fetch_lot_history_by_id(connection)

    # 1) evaluation 결과(lot_hist_id 단위)를 R03 그룹 기준
    #    (chamber_id, parameter_id, recipe_step_no)으로 다시 묶는다.
    #    이 재묶음이 필요한 이유: evaluate_groups는 fdc_trace만 보고 계산해서
    #    chamber_id를 모른다 — chamber_id는 lot_history에만 있다.
    members_by_group: dict[tuple[str, str, int], list[rules.R03GroupMember]] = {}
    for evaluation in evaluations:
        lot_history_row = lot_history_by_id.get(evaluation.key.lot_hist_id)
        if lot_history_row is None:
            # fdc_trace.lot_hist_id는 lot_history를 FK로 참조하므로(스키마
            # 제약) 정상 데이터에서는 발생하지 않는다. 그래도 조용히 건너뛰지
            # 않고 데이터 정합성 문제로 바로 알린다.
            raise ValueError(
                f"lot_history에 없는 lot_hist_id입니다: {evaluation.key.lot_hist_id}"
            )
        # chamber_id·chamber_wafer_cum(1차 정렬 기준)뿐 아니라 track_in_at
        # (동점 시 2차 정렬 기준이자 R03Event.occurred_at의 원천)도 여기서
        # 같이 검사한다. 예전에는 track_in_at만 빠져 있어서, owner가 아닌
        # member의 track_in_at이 NULL이면 rules._member_sort_key가 조용히
        # datetime.min으로 대체해버리고 아무 예외도 나지 않았다 — owner일
        # 때만 build_r03_alarm_record가 뒤늦게 걸러냈다. 세 컬럼을 같은
        # 자리에서 같은 방식으로 검사해 이 비일관성을 없앤다.
        missing_order_columns = (
            lot_history_row.chamber_id is None
            or lot_history_row.chamber_wafer_cum is None
            or lot_history_row.track_in_at is None
        )
        if missing_order_columns:
            raise ValueError(
                "R03 정렬에 필요한 chamber_id·chamber_wafer_cum·track_in_at이 "
                f"없습니다: lot_hist_id={evaluation.key.lot_hist_id}"
            )

        group_key = (
            lot_history_row.chamber_id,
            evaluation.key.parameter_id,
            evaluation.key.recipe_step_no,
        )
        members_by_group.setdefault(group_key, []).append(
            rules.R03GroupMember(
                lot_hist_id=evaluation.key.lot_hist_id,
                lot_id=lot_history_row.lot_id,
                wafer_no=lot_history_row.wafer_no,
                wafer_id=lot_history_row.wafer_id,
                chamber_wafer_cum=lot_history_row.chamber_wafer_cum,
                track_in_at=lot_history_row.track_in_at,
                alarm_type=evaluation.alarm_type,
            )
        )

    # 2) 그룹마다 순수 함수(rules.derive_r03_events)를 불러 연속 3 OOS를 찾는다.
    events: list[rules.R03Event] = []
    for (chamber_id, parameter_id, recipe_step_no), members in members_by_group.items():
        equipment_id = lot_history_by_id[members[0].lot_hist_id].equipment_id
        if equipment_id is None:
            raise ValueError(
                f"lot_history에 equipment_id가 없습니다: chamber_id={chamber_id}"
            )
        events.extend(
            rules.derive_r03_events(
                members,
                chamber_id=chamber_id,
                parameter_id=parameter_id,
                recipe_step_no=recipe_step_no,
                equipment_id=equipment_id,
            )
        )

    # 3) 그룹을 순회한 순서는 dict 삽입 순서에 불과해 재현성이 없다. 결과를
    #    보는 사람이 항상 같은 순서를 보도록 명시적으로 정렬한다.
    events.sort(
        key=lambda event: (event.chamber_id, event.parameter_id, event.recipe_step_no)
    )

    return R03DerivationResult(events=events)


def derive_r03_alarm_records(connection: Connection) -> list[rules.R03AlarmRecord]:
    """`derive_r03_events` 결과에 TRACE alarm_id를 채워 저장 가능한 행으로 완성한다.

    읽기 전용이다(trace_alarm_history·lot_history 조회만 한다). 실제 INSERT는
    `persist_r03_alarms`가 한다 — 이렇게 나눠두면 "무엇을 저장할지 미리 보고
    검토"하는 dry-run 용도로 이 함수만 따로 부를 수 있다.
    """

    derivation = derive_r03_events(connection)
    trace_refs_by_group = repository.fetch_trace_alarm_refs_by_group(connection)

    records: list[rules.R03AlarmRecord] = []
    for event in derivation.events:
        member_alarm_ids: list[str] = []
        for member in event.members:
            key = GroupKey(
                lot_hist_id=member.lot_hist_id,
                parameter_id=event.parameter_id,
                recipe_step_no=event.recipe_step_no,
            )
            refs = trace_refs_by_group.get(key, [])
            if not refs:
                # 이 member는 evaluation.alarm_type==OOS인 wafer라서 raw OOS
                # TRACE alarm이 trace_alarm_history에 최소 1건 있어야 한다.
                # 없다면 두 데이터(evaluation 재계산 vs trace_alarm_history)가
                # 서로 어긋난다는 뜻이므로 조용히 넘어가지 않는다.
                raise ValueError(
                    "R03 member에 대응하는 raw OOS TRACE alarm이 "
                    f"trace_alarm_history에 없습니다: {key}"
                )
            # TraceAlarmRefRow는 이미 seq_no ASC, alarm_id ASC로 정렬돼 있다
            # (repository.fetch_trace_alarm_refs_by_group 문서 참고).
            member_alarm_ids.extend(ref.alarm_id for ref in refs)

        records.append(
            rules.build_r03_alarm_record(event, member_alarm_ids=member_alarm_ids)
        )

    return records


def persist_r03_alarms(
    readonly_connection: Connection,
    writer_connection: Connection,
) -> R03PersistResult:
    """R03을 계산하고 `r03_alarm_history`에 멱등 적재한다(이 파일의 유일한 쓰기 함수).

    두 Connection을 따로 받는 이유는 실제 최소권한 role 구조를 그대로 반영하기
    위해서다 — 계산에 필요한 조회(`readonly_connection`, `get_readonly_connection()`
    으로 얻은 것)와 실제 저장(`writer_connection`, `get_db_connection()`으로 얻은
    것)은 서로 다른 DB role을 쓴다. 같은 Connection을 두 자리에 넘겨도 동작은
    하지만(로컬 개발 등 role 구분이 없을 때), 운영 환경에서는 readonly role로
    이 함수를 통째로 부르면 INSERT 단계에서 권한 오류가 난다.

    커밋은 호출자 책임이다 — 이 함수는 `writer_connection.commit()`을 부르지
    않는다(트랜잭션을 더 큰 단위로 묶어야 하는 호출자를 위해).
    """

    records = derive_r03_alarm_records(readonly_connection)
    inserted_count = repository.insert_r03_alarms(writer_connection, records)
    total_count_after = repository.fetch_r03_alarm_count(writer_connection)

    return R03PersistResult(
        derived_count=len(records),
        inserted_count=inserted_count,
        total_count_after=total_count_after,
    )


# =====================================================================
# V5-A-1.5 — incident 집계
#
# "incident"은 실제 알람(TRACE·SUMMARY·R03)이 있는 (lot_id, chamber_id) 조합이다.
# `repository.fetch_incident_alarm_counts`가 이미 `v_alarm_event`에서 source별로
# 집계해서 돌려주므로, 여기서는 그 결과를 참고 action fixture(12건, V5-A-1.4까지와
# 달리 새로 계산하는 값은 없다 — 순수 집계 결과 대조)와 짝짓기만 한다.
#
# 완료 기준(WBS v5 V5-A-1.5·docs/ai-context/tasks/A-detection.md):
#   - 알람이 있는 (lot_id, chamber_id) 12개
#   - 참고 action fixture 12건과 1:1
#   - R03를 포함한 alarm 합계 192건
# =====================================================================
@dataclass(frozen=True, slots=True)
class IncidentReferenceMismatch:
    """산출한 incident 목록과 참고 action fixture가 어긋난
    (lot_id, chamber_id) 한 건."""

    lot_id: str
    chamber_id: str
    # "missing_in_incidents": action fixture엔 있는데 실제 알람이 있는
    #   incident엔 없다(=참고 action이 가리키는 조합에 알람이 재현되지 않았다).
    # "missing_in_reference": incident엔 있는데 action fixture엔 없다(=참고
    #   action이 커버하지 못하는 조합에서 알람이 나왔다).
    reason: str


@dataclass(frozen=True, slots=True)
class IncidentVerificationResult:
    """V5-A-1.5 완료 기준(incident 12·alarm 합계 189/192, action fixture가
    있는 DB에서는 action 12·1:1까지) 판정 결과.

    action fixture 1:1 대조와 alarm 합계 192는 DB 상태(evaluation profile
    여부·`--persist-r03` 여부)에 따라 애초에 성립할 수 없는 경우가 있다 — 그
    경우를 "불일치"로 잘못 보고하지 않으려고 `reference_action_available`·
    `r03_persisted`로 상태를 먼저 구분한다(코드 리뷰 지적: 상시 실패하는
    검사를 fail-closed로 오인하면 사람이 곧 무시하게 되고, 진짜 실패도 같이
    묻힌다).
    """

    incidents: list[repository.IncidentAlarmCountRow]
    reference_actions: list[repository.ReferenceActionRow]
    mismatches: list[IncidentReferenceMismatch]

    @property
    def incident_count(self) -> int:
        return len(self.incidents)

    @property
    def reference_action_count(self) -> int:
        return len(self.reference_actions)

    @property
    def total_alarm_count(self) -> int:
        """R03를 포함한 alarm 합계(R03 저장 전 189 / 저장 후 192)."""

        return sum(incident.total_count for incident in self.incidents)

    @property
    def r03_alarm_count(self) -> int:
        """incident에 포함된 R03 alarm 합계. `--persist-r03` 없이(dry-run)
        부르면 r03_alarm_history가 비어 있어 0이고, 저장 후에는 3이어야
        한다."""

        return sum(incident.r03_count for incident in self.incidents)

    @property
    def r03_persisted(self) -> bool:
        """R03 alarm이 r03_alarm_history에 이미 저장돼 v_alarm_event에
        반영됐는지. False면 아직 dry-run 상태(총합 189가 정상), True면 저장된
        상태(총합 192가 정상)다."""

        return self.r03_alarm_count > 0

    @property
    def matches_incident_count(self) -> bool:
        return self.incident_count == 12

    @property
    def matches_reference_action_count(self) -> bool:
        return self.reference_action_count == 12

    @property
    def matches_total_alarm_count(self) -> bool:
        """TRACE+SUMMARY 189건은 R03 저장 여부와 무관하게 항상 성립해야 하고,
        R03는 저장 전 0건·저장 후 3건 둘 중 하나여야 한다(그 사이 값은
        비정상). 고정값 192 하나만 기준으로 삼으면, `--persist-r03` 없이 돌린
        기본 실행에서는 정상 상태(189)도 항상 FAIL로 나온다(코드 리뷰 지적) —
        R03 저장 여부에 따라 기대값을 189/192로 갈라 판정한다."""

        trace_and_summary_total = sum(
            incident.trace_count + incident.summary_count for incident in self.incidents
        )
        if trace_and_summary_total != 189:
            return False
        if self.r03_alarm_count not in (0, 3):
            return False
        expected_total = 192 if self.r03_persisted else 189
        return self.total_alarm_count == expected_total

    @property
    def reference_action_available(self) -> bool:
        """참고 action fixture가 이 DB에 존재하는지. evaluation profile
        (`kosa_text2sql`)에만 12건이 있고 runtime 2 DB는 항상 0건이다
        (`repository.fetch_reference_actions` docstring 참고) — 0건이면 1:1
        대조는 "불일치"가 아니라 "이 DB로는 판정 불가"다."""

        return self.reference_action_count > 0

    @property
    def ok(self) -> bool:
        """incident-action fixture 1:1 대응 여부. 참고 action fixture가 없는
        DB(reference_action_available=False)에서는 애초에 대조하지 않았으므로
        True를 반환한다 — "판정 불가"를 "일치"로 위장하지 않되, 이 속성 하나만
        보고는 그 차이가 드러나지 않으므로 반드시 reference_action_available과
        함께 확인해야 한다."""

        if not self.reference_action_available:
            return True
        return len(self.mismatches) == 0

    @property
    def matches_acceptance_values(self) -> bool:
        """수용값(incident 12·alarm 189/192) 전체 일치 여부.

        참고 action fixture가 없는 DB(reference_action_available=False)에서는
        action 12건·1:1 기준을 판정에서 제외한다 — 그 DB에는 애초에 대조
        대상이 없으므로 "불일치"가 아니라 "대조 불가"이기 때문이다(코드 리뷰
        지적). fixture가 있는 DB에서는 지금까지처럼 action 12건·1:1까지 모두
        확인한다.
        """

        reference_ok = not self.reference_action_available or (
            self.matches_reference_action_count and self.ok
        )
        return (
            self.matches_incident_count
            and self.matches_total_alarm_count
            and reference_ok
        )


def verify_incident_aggregation(connection: Connection) -> IncidentVerificationResult:
    """V5-A-1.5: incident 집계와 참고 action fixture 1:1 대조.

    읽기 전용이다(아무것도 저장하지 않는다). `action_history`는 evaluation
    profile(`kosa_text2sql`)에만 12건이 있으므로(`repository.fetch_reference_actions`
    docstring 참고) runtime DB(`kosa_agent`·`kosa_agent_e2e`)로 이 함수를 부르면
    reference_actions가 0건으로 나온다 — `IncidentVerificationResult.
    reference_action_available`이 이 경우를 "불일치"가 아니라 "판정 불가"로
    구분하니(코드 결함이 아니라 profile 특성), action fixture 대조까지
    확인하려면 evaluation profile이나 9개 CSV를 전부 올려둔 로컬 dev DB에
    대고 불러야 한다.
    """

    incidents = repository.fetch_incident_alarm_counts(connection)
    reference_actions = repository.fetch_reference_actions(connection)

    mismatches: list[IncidentReferenceMismatch] = []
    if reference_actions:
        # reference_actions가 비어 있으면(runtime DB) 대조할 fixture 자체가
        # 없다 — 이 경우 아래 두 집합 차 계산을 그대로 두면 incident 12개
        # 전부가 "missing_in_reference"로 잡혀, 판정 불가 상황을 대량의 가짜
        # 불일치로 보고하게 된다. 그래서 reference_actions가 있을 때만
        # 대조한다.
        incident_keys = {(row.lot_id, row.chamber_id) for row in incidents}
        reference_keys = {(row.lot_id, row.chamber_id) for row in reference_actions}

        for lot_id, chamber_id in sorted(reference_keys - incident_keys):
            mismatches.append(
                IncidentReferenceMismatch(
                    lot_id=lot_id,
                    chamber_id=chamber_id,
                    reason="missing_in_incidents",
                )
            )
        for lot_id, chamber_id in sorted(incident_keys - reference_keys):
            mismatches.append(
                IncidentReferenceMismatch(
                    lot_id=lot_id,
                    chamber_id=chamber_id,
                    reason="missing_in_reference",
                )
            )

    return IncidentVerificationResult(
        incidents=incidents,
        reference_actions=reference_actions,
        mismatches=mismatches,
    )


# ---------------------------------------------------------------------
# get_fdc_summary(lot_hist_id) Tool 서비스 (V5-A-3.2-1)
#
# 위쪽 절들(V5-A-1.1~1.5)은 전부 "batch 재계산·대조·집계" 흐름이다. 이 절은
# "Agent Tool이 단건으로 부르는" 별도 흐름이라 아래에 분리해 둔다. repository.py
# 쪽에서도 이 절 전용 함수(fetch_wafer_lot_context·fetch_wafer_parameter_rows)를
# 같은 이유로 파일 맨 끝에 분리해 뒀다.
# ---------------------------------------------------------------------
def _build_anomaly_signal(
    loaded: LoadedModel,
    lot_hist_id: str,
    lot_id: str,
    parameter_rows: list[repository.WaferParameterRow],
) -> AnomalySignal | None:
    """단일 wafer의 parameter row들을 학습 때와 같은 채점 파이프라인으로 채점한다.

    `model.py`의 학습 함수(`build_group_feature` -> `aggregate_wafer_features`
    -> `apply_normalizer` -> `raw_anomaly_scores` -> `scale_scores` ->
    `to_anomaly_signal`)를 "wafer 1장짜리 채점"에 그대로 재사용한다 — 학습과
    운영 채점이 서로 다른 계산을 쓰면 "재현 가능한 score"라는 완료 기준이
    깨진다(model.py 모듈 docstring 원칙과 동일).

    `value_mean`이 없는(NULL) parameter 행은 건너뛴다 — `build_group_feature`가
    `value_mean: float`(nullable 아님)을 받기 때문이다. 그렇게 걸러진 뒤
    group_features가 하나도 안 남으면(이 wafer의 모든 parameter가 결측이면)
    채점을 포기하고 None을 반환한다 — 참여 신호가 전혀 없는 벡터를 억지로
    채점해봐야 의미가 없다.

    이 함수는 예외를 흡수하지 않는다 — 호출자(`FdcSummaryService.
    _try_build_anomaly_signal`)가 모든 실패를 `anomaly=None`으로 바꾼다.
    """

    expected_point_counts = {
        (parameter_id, step): cnt
        for parameter_id, step, cnt in loaded.manifest.expected_point_counts
    }

    group_features = []
    for row in parameter_rows:
        if row.value_mean is None:
            continue

        key = GroupKey(
            lot_hist_id=lot_hist_id,
            parameter_id=row.parameter_id,
            recipe_step_no=row.recipe_step_no,
        )
        limit = ParameterLimit(
            parameter_id=row.parameter_id,
            spec_lower=row.spec_lower,
            ctrl_lower=row.ctrl_lower,
            ctrl_upper=row.ctrl_upper,
            spec_upper=row.spec_upper,
            upper_only=row.upper_only,
            # 리뷰 V5-A-3.2-1 필수 2: parameters[].target(응답에 노출되는 값)과
            # anomaly.score의 계산 근거가 서로 다른 중심을 쓰던 불일치를 없앤다.
            target=row.target,
        )
        group_features.append(
            anomaly_model.build_group_feature(
                key=key,
                lot_id=lot_id,
                value_mean=row.value_mean,
                value_std=row.value_std,
                point_cnt=row.point_cnt,
                ooc_point_cnt=row.ooc_point_cnt,
                oos_point_cnt=row.oos_point_cnt,
                limit=limit,
                expected_point_cnt=expected_point_counts.get(
                    (row.parameter_id, row.recipe_step_no), 0
                ),
            )
        )

    if not group_features:
        return None

    vectors = anomaly_model.aggregate_wafer_features(
        group_features, loaded.manifest.feature_names
    )
    vector = next((v for v in vectors if v.lot_hist_id == lot_hist_id), None)
    if vector is None:
        return None

    matrix = np.array([vector.values])
    normalized = anomaly_model.apply_normalizer(matrix, loaded.manifest.normalizer)
    raw_scores = anomaly_model.raw_anomaly_scores(loaded.forest, normalized)
    scaled_scores = anomaly_model.scale_scores(raw_scores, loaded.manifest.scaling)
    return anomaly_model.to_anomaly_signal(float(scaled_scores[0]), loaded.manifest)


class FdcSummaryService:
    """`get_fdc_summary(lot_hist_id)` Tool 전용 서비스 (V5-A-3.2-1).

    `GET /trace`(V5-A-3.2, 화면용 boundary projection)와는 반환 모양(bare
    list 화면 응답 vs 단건 `ok`·`reason` Tool 계약)과 실패 처리(HTTP 404 vs
    `NOT_FOUND:` reason)가 달라 같은 서비스를 공유하지 않는다.

    Fault 정답(`lot_history.fault_code`)과 조치 권고(`action_history`)는 이
    클래스가 호출하는 두 repository 함수 어디에서도 SELECT하지 않는다 — 이
    클래스는 그 두 함수가 돌려준 값을 조립만 한다(NFR-19, 설계서 v2.1 8절).
    """

    def __init__(
        self,
        connection: Connection,
        *,
        model_loader: Callable[[], LoadedModel | None] = load_latest_model,
    ) -> None:
        self._connection = connection
        self._model_loader = model_loader

    def get_fdc_summary(self, lot_hist_id: str) -> FdcSummaryToolResult | None:
        """단일 `lot_hist_id`의 summary·evaluation·5선과 선택적 anomaly evidence.

        wafer 자체가 없거나(lot_history에 없음) parameter 행이 0건이면(그
        `lot_hist_id`가 summary_data·evaluation에 없음) None을 반환한다 —
        호출자(tools.py)가 이를 `NOT_FOUND:`로 번역한다. 이 Tool이 반환해야
        하는 필수 payload(`wafer`·`parameters`)를 완성할 수 없다는 점에서 두
        경우를 구분할 필요가 없다.
        """

        lot_row = repository.fetch_wafer_lot_context(self._connection, lot_hist_id)
        if lot_row is None:
            return None

        parameter_rows = repository.fetch_wafer_parameter_rows(
            self._connection, lot_hist_id
        )
        if not parameter_rows:
            return None

        wafer = WaferContext(
            lot_hist_id=lot_row.lot_hist_id,
            lot_id=lot_row.lot_id,
            wafer_no=lot_row.wafer_no,
            chamber_id=lot_row.chamber_id,
            equipment_id=lot_row.equipment_id,
            step_id=lot_row.step_id,
            recipe_id=lot_row.recipe_id,
        )
        parameters = [
            ParameterSummaryItem(
                parameter_id=row.parameter_id,
                parameter_name=row.parameter_name,
                unit=row.unit,
                recipe_step_no=row.recipe_step_no,
                # 물리 schema에 step 이름 컬럼이 없다(WaferParameterRow docstring).
                recipe_step_name=None,
                value_mean=row.value_mean,
                value_std=row.value_std,
                value_min=row.value_min,
                value_max=row.value_max,
                point_cnt=row.point_cnt,
                ooc_point_cnt=row.ooc_point_cnt,
                oos_point_cnt=row.oos_point_cnt,
                spec_lower=row.spec_lower,
                ctrl_lower=row.ctrl_lower,
                target=row.target,
                ctrl_upper=row.ctrl_upper,
                spec_upper=row.spec_upper,
                alarm_type=row.alarm_type,
            )
            for row in parameter_rows
        ]

        anomaly = self._try_build_anomaly_signal(lot_row, parameter_rows)

        return FdcSummaryToolResult(
            ok=True,
            wafer=wafer,
            parameters=parameters,
            anomaly=anomaly,
        )

    def _try_build_anomaly_signal(
        self,
        lot_row: repository.WaferLotContextRow,
        parameter_rows: list[repository.WaferParameterRow],
    ) -> AnomalySignal | None:
        """model artifact 로딩·채점 전체를 감싸 실패를 `None`으로 흡수한다.

        artifact가 없는 것(`model_loader`가 None)뿐 아니라, 있어도 채점
        도중 어떤 이유로든 실패하면 Tool 전체를 실패시키지 않고 조용히
        `anomaly=None`으로 넘어간다 — summary·evaluation 조회는 이미
        성공했으므로 그 값만으로도 이 Tool의 완료 기준(fault label·조치
        권고를 제외한 summary 제공)은 충족된다.
        """

        try:
            loaded = self._model_loader()
            if loaded is None:
                return None
            return _build_anomaly_signal(
                loaded, lot_row.lot_hist_id, lot_row.lot_id, parameter_rows
            )
        except Exception:
            logger.exception(
                "get_fdc_summary anomaly scoring 실패 — score 없이 계속한다 "
                "(lot_hist_id=%s)",
                lot_row.lot_hist_id,
            )
            return None
