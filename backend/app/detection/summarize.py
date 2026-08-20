"""Trace 요약 재계산 순수 함수 (V5-A-1.1·V5-A-1.2).

시스템설계서 v2.1 4.1~4.2, 요구사항정의서 v2.1 FR-A-01·FR-A-02 근거.
기준 원천: 멘토 최종 project.zip(2026-08-18) epoch `fdc_final_20260818`.

이 모듈은 Rules/Model 계층이다(시스템설계서 1.3). DB 조회·쓰기, HTTP, LLM 호출을
하지 않는 순수 계산만 담당한다. `fdc_trace` raw point를 입력받아
`(lot_hist_id, parameter_id, recipe_step_no)` 단위로 집계하고(V5-A-1.1),
`dim_parameter` 5선으로 point 단위 규격을 판정한다(V5-A-1.2).

DB 조회는 `repository.py`, 트랜잭션·오류 계약은 `service.py`가 담당하며 이 모듈의
함수를 그대로 호출한다. batch/단건 요청 모두 같은 함수를 재사용해 결과가
재현되도록 한다(V5-A-1.1·V5-A-4.1 완료 기준).

[팀원용 요약]
이 파일이 하는 일은 딱 두 갈래다.
  1) 집계: raw point들을 그룹으로 묶어서 평균/표준편차/최소/최대/개수를 구한다.
     group_points -> summarize_group -> summarize_groups
  2) 판정: raw point 하나하나가 정상(IN)/관리이탈(OOC)/규격이탈(OOS)인지 판단하고,
     그룹 단위로 최종 등급을 매긴다.
     group_points -> judge_point -> evaluate_group -> evaluate_groups
DB나 외부 시스템을 전혀 건드리지 않는 "순수 함수"만 모아뒀기 때문에, 입력이 같으면
언제 실행하든 항상 같은 출력이 나온다(재현성). 이 특성 덕분에 단위 테스트가 쉽고,
batch 처리와 단건 처리가 같은 함수를 그대로 재사용할 수 있다.
"""

from __future__ import annotations

# 파이썬 3.10 미만 버전에서도 `float | None` 같은 최신 타입 힌트 문법을
# 문제없이 쓸 수 있게 해주는 선언. (실제 실행 시 타입은 평가되지 않고 문자열로 취급됨)
from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Mapping, Sequence

from app.common.enums import AlarmType  # IN / OOC / OOS 등 판정 등급을 담은 enum

# 이 모듈에서 외부(다른 파일)로 공개(export)하는 이름 목록.
# 여기 없는 이름(예: _group_key)은 "이 모듈 내부에서만 쓰는 것"이라는 의미.
__all__ = [
    "TracePoint",
    "ParameterLimit",
    "GroupKey",
    "GroupSummary",
    "GroupEvaluation",
    "group_points",
    "summarize_group",
    "summarize_groups",
    "judge_point",
    "evaluate_group",
    "evaluate_groups",
]


# ---------------------------------------------------------------------
# 입력·출력 값 객체
#
# 아래 클래스들은 모두 @dataclass(frozen=True, slots=True)로 선언되어 있다.
#   - frozen=True : 인스턴스 생성 후 필드 값을 변경할 수 없다(불변 객체).
#                    "계산 도중 값이 몰래 바뀌는" 부작용을 원천 차단하기 위함.
#   - slots=True  : 인스턴스가 고정된 슬롯만 갖도록 해서 메모리를 아끼고
#                    속도를 조금 높인다(대량의 point를 다루므로 최적화 목적).
# frozen=True인 덕분에 이 객체들은 dict의 key로도 쓸 수 있다
# (파이썬은 불변 객체만 dict key로 허용한다). 실제로 GroupKey가 그렇게 쓰인다.
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TracePoint:
    """`fdc_trace` 원본 한 점. corrected `seq_no`(0~5)로 보정된 값을 입력받는다."""

    lot_hist_id: str       # 어떤 lot(작업 단위)에서 나온 값인지
    parameter_id: str      # 어떤 측정 파라미터인지 (예: ET_REFL)
    recipe_step_no: int    # 레시피의 몇 번째 스텝에서 측정됐는지
    seq_no: int             # 같은 그룹 안에서의 측정 순서(보정된 0~5 값)
    value: float            # 실제 측정값


@dataclass(frozen=True, slots=True)
class ParameterLimit:
    """`dim_parameter` 고정 5선. Summary 동적 CL±3σ(V5-A-1.3)와는 다른 값이다.

    `upper_only=true`(예: ET_REFL)는 하한(spec_lower·ctrl_lower)을 판정하지
    않는다. null 여부로 upper_only를 추정하지 않고 이 플래그를 그대로 따른다.
    """

    parameter_id: str
    spec_lower: float | None  # LSL (규격 하한, Lower Spec Limit)
    ctrl_lower: float | None  # LCL (관리 하한, Lower Control Limit)
    ctrl_upper: float | None  # UCL (관리 상한, Upper Control Limit)
    spec_upper: float | None  # USL (규격 상한, Upper Spec Limit)
    upper_only: bool          # True면 하한선(LSL/LCL)은 아예 검사 대상에서 제외


@dataclass(frozen=True, slots=True)
class GroupKey:
    """(lot_hist_id, parameter_id, recipe_step_no) 3개 값의 조합이
    "그룹" 하나를 유일하게 식별한다. 아래 group_points에서 이 키로 point들을 묶는다."""

    lot_hist_id: str
    parameter_id: str
    recipe_step_no: int


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """V5-A-1.1 — `summary_data` reference(4,800건)와 비교하는 집계 결과."""

    key: GroupKey
    value_mean: float
    value_std: float | None  # point_cnt < 2 면 표본표준편차가 정의되지 않아 None
    value_min: float
    value_max: float
    point_cnt: int


@dataclass(frozen=True, slots=True)
class GroupEvaluation:
    """V5-A-1.2 — point 단위 판정을 그룹으로 집계한 결과.

    `evaluation` reference(4,800건, IN 4,538/OOC 216/OOS 46)와 비교하는 값을
    만들지만, evaluation reference와의 최종 비교·TRACE 알람 발행은
    V5-A-1.2·V5-A-1.3 범위다(repository.py·service.py가 담당). 이 모듈은
    계산만 제공한다.
    """

    key: GroupKey
    point_cnt: int          # 그룹 안 전체 point 개수
    ooc_point_cnt: int       # 그중 OOC(관리이탈)로 판정된 point 개수
    oos_point_cnt: int       # 그중 OOS(규격이탈)로 판정된 point 개수
    alarm_type: AlarmType    # 그룹 전체의 최종 등급 (IN/OOC/OOS 중 하나)


def _group_key(point: TracePoint) -> GroupKey:
    """TracePoint 하나에서 그룹을 식별하는 3개 필드만 뽑아 GroupKey로 만든다.
    이름이 밑줄(_)로 시작하므로 이 모듈 내부(group_points)에서만 쓰는 헬퍼 함수다."""
    return GroupKey(
        lot_hist_id=point.lot_hist_id,
        parameter_id=point.parameter_id,
        recipe_step_no=point.recipe_step_no,
    )


def group_points(points: Iterable[TracePoint]) -> dict[GroupKey, list[TracePoint]]:
    """raw point를 `(lot_hist_id, parameter_id, recipe_step_no)`로 묶는다.

    입력 순서에 의존하지 않도록 각 그룹 내부를 seq_no 오름차순으로 정렬해
    반환한다. 그룹 자체의 정렬(결정론 재현)은 `summarize_groups`·
    `evaluate_groups`가 책임진다.
    """

    grouped: dict[GroupKey, list[TracePoint]] = {}
    for point in points:
        # setdefault(key, []): grouped에 key가 없으면 빈 리스트를 새로 넣고,
        # 있으면 기존 리스트를 그대로 가져온다. 그 리스트에 point를 추가.
        # -> 결과적으로 같은 GroupKey를 가진 point들이 하나의 리스트로 모인다.
        grouped.setdefault(_group_key(point), []).append(point)

    # 그룹별로 내부 리스트를 seq_no 기준 오름차순 정렬.
    # (point가 원래 입력에 어떤 순서로 들어오든 항상 seq_no 순으로 나오게 함)
    for key, group in grouped.items():
        grouped[key] = sorted(group, key=lambda p: p.seq_no)

    return grouped


# ---------------------------------------------------------------------
# V5-A-1.1 — 그룹 집계 (mean·표본표준편차·min·max·count)
# ---------------------------------------------------------------------
def summarize_group(key: GroupKey, points: Sequence[TracePoint]) -> GroupSummary:
    """단일 그룹의 mean·표본표준편차(ddof=1)·min·max·count를 계산한다.

    설계서 4.1: value_std는 표본표준편차(ddof=1)다. point가 1개면 ddof=1
    분산이 정의되지 않으므로 0으로 대체하지 않고 None으로 둔다.
    """

    if not points:
        # 빈 그룹은 평균조차 정의할 수 없으므로 조용히 0을 반환하지 않고
        # 명시적으로 예외를 던진다. (호출자가 잘못된 입력을 바로 알아채도록)
        raise ValueError(f"빈 그룹은 집계할 수 없습니다: {key}")

    values = [p.value for p in points]  # point 리스트에서 value만 뽑아낸 리스트
    n = len(values)
    mean = sum(values) / n  # 단순 산술 평균

    std: float | None
    if n >= 2:
        # 표본표준편차(ddof=1, "n-1로 나누는" 방식) 계산.
        # 표본에서 뽑은 데이터의 분산을 추정할 때는 n이 아니라 n-1로 나눠야
        # 편향이 없다는 통계적 이유 때문에 이 방식을 쓴다.
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = sqrt(variance)
    else:
        # point가 1개뿐이면 n-1=0이 되어 나눗셈이 불가능(분산 정의 불가).
        # 0으로 대체하면 "편차가 없다"는 잘못된 의미가 되므로 None으로 둔다.
        std = None

    return GroupSummary(
        key=key,
        value_mean=mean,
        value_std=std,
        value_min=min(values),
        value_max=max(values),
        point_cnt=n,
    )


def summarize_groups(points: Iterable[TracePoint]) -> list[GroupSummary]:
    """전체 `fdc_trace`를 그룹별로 집계해 결정론적으로 정렬된 목록을 반환한다.

    정렬 키는 `(lot_hist_id, parameter_id, recipe_step_no)`다. 같은 입력에는
    항상 같은 순서·같은 값을 반환한다(V5-A-1.1·V5-A-4.1 재현성 요구).
    """

    grouped = group_points(points)  # 1) 전체 point를 그룹으로 묶고
    # 2) 그룹마다 summarize_group을 호출해 GroupSummary 리스트를 만든다.
    summaries = [summarize_group(key, group) for key, group in grouped.items()]

    # 3) (lot_hist_id, parameter_id, recipe_step_no) 튜플 기준으로 정렬.
    # dict는 순회 순서가 삽입 순서를 따르긴 하지만 "보장"이라 부르기엔 취약하므로,
    # 여기서 명시적으로 정렬해 항상 같은 출력 순서를 보장한다(재현성).
    return sorted(
        summaries,
        key=lambda s: (s.key.lot_hist_id, s.key.parameter_id, s.key.recipe_step_no),
    )


# ---------------------------------------------------------------------
# V5-A-1.2 — parameter 규격 판정 (point 단위)
# ---------------------------------------------------------------------
def judge_point(value: float, limit: ParameterLimit) -> AlarmType:
    """raw point 하나를 USL/LSL(OOS) → UCL/LCL(OOC) → IN 순서로 판정한다.

    한계선은 `dim_parameter`만 사용한다(설계서 4.2). Summary 동적 CL±3σ는
    이 함수의 대상이 아니다(V5-A-1.3에서 별도 계산).
    """

    # 아래 4개의 if는 "위에서부터 순서대로" 검사되고, 하나라도 걸리면 즉시 return한다.
    # 즉 검사 순서 자체가 우선순위다: 규격이탈(OOS)이 관리이탈(OOC)보다 더 심각하므로
    # OOS를 먼저 검사한다.

    # 1) USL(규격 상한) 초과 -> OOS
    #    spec_upper가 None(선이 없음)이면 이 조건은 항상 False라서 그냥 넘어간다.
    if limit.spec_upper is not None and value > limit.spec_upper:
        return AlarmType.OOS

    # 2) LSL(규격 하한) 미만 -> OOS
    #    단, upper_only=True인 파라미터는 하한을 아예 판정하지 않으므로
    #    `not limit.upper_only` 조건으로 이 검사 자체를 건너뛴다.
    if (
        not limit.upper_only
        and limit.spec_lower is not None
        and value < limit.spec_lower
    ):
        return AlarmType.OOS

    # 3) UCL(관리 상한) 초과 -> OOC
    if limit.ctrl_upper is not None and value > limit.ctrl_upper:
        return AlarmType.OOC

    # 4) LCL(관리 하한) 미만 -> OOC (역시 upper_only면 건너뜀)
    if (
        not limit.upper_only
        and limit.ctrl_lower is not None
        and value < limit.ctrl_lower
    ):
        return AlarmType.OOC

    # 위 4개 검사에 하나도 걸리지 않았다면 정상(IN)
    return AlarmType.IN


def evaluate_group(
    key: GroupKey,
    points: Sequence[TracePoint],
    limit: ParameterLimit,
) -> GroupEvaluation:
    """그룹 내 point별 판정을 집계해 evaluation reference와 비교 가능한 값을 만든다.

    evaluation 우선순위(설계서 4.2): OOS point가 하나라도 있으면 그룹
    alarm_type=OOS, 없고 OOC point가 있으면 OOC, 그 외 IN.
    """

    if not points:
        raise ValueError(f"빈 그룹은 판정할 수 없습니다: {key}")

    # 그룹 안의 point 하나하나에 judge_point를 적용해 판정 결과 리스트를 만든다.
    # 예: [IN, IN, OOC, IN, OOS] 처럼 point 개수만큼 AlarmType 값이 나온다.
    judgements = [judge_point(p.value, limit) for p in points]

    # sum(1 for j in judgements if j is AlarmType.OOC):
    # "judgements를 순회하면서 OOC인 것마다 1을 더한다" -> OOC 개수를 센다.
    # (`is` 비교는 enum 값을 비교할 때 관례적으로 쓰는 방식)
    ooc_point_cnt = sum(1 for j in judgements if j is AlarmType.OOC)
    oos_point_cnt = sum(1 for j in judgements if j is AlarmType.OOS)

    # 그룹 전체 등급 결정: "가장 나쁜 point 하나"가 그룹 등급을 좌우한다.
    if oos_point_cnt > 0:
        alarm_type = AlarmType.OOS
    elif ooc_point_cnt > 0:
        alarm_type = AlarmType.OOC
    else:
        alarm_type = AlarmType.IN

    return GroupEvaluation(
        key=key,
        point_cnt=len(points),
        ooc_point_cnt=ooc_point_cnt,
        oos_point_cnt=oos_point_cnt,
        alarm_type=alarm_type,
    )


def evaluate_groups(
    points: Iterable[TracePoint],
    limits: Mapping[str, ParameterLimit],
) -> list[GroupEvaluation]:
    """전체 `fdc_trace`를 그룹별로 판정해 결정론적으로 정렬된 목록을 반환한다.

    `limits`는 `parameter_id -> ParameterLimit` 매핑이며 호출자(Repository)가
    `dim_parameter` 조회 결과로 채운다. 이 함수는 DB에 접근하지 않는다.
    한계값이 없는 parameter_id가 나오면 데이터 오류로 간주해 예외를 낸다
    (추측해서 채우지 않는다).
    """

    grouped = group_points(points)
    evaluations = []

    for key, group in grouped.items():
        # 이 그룹의 parameter_id에 해당하는 한계값을 limits 딕셔너리에서 찾는다.
        limit = limits.get(key.parameter_id)
        if limit is None:
            # 한계값이 없는데 임의의 값으로 대체(추측)하면 잘못된 판정이 조용히
            # 만들어질 수 있으므로, 데이터 누락으로 간주하고 즉시 예외를 던진다.
            raise ValueError(
                f"parameter_id={key.parameter_id}의 한계값(dim_parameter)이 없습니다"
            )
        evaluations.append(evaluate_group(key, group, limit))

    # summarize_groups와 마찬가지로 재현성을 위해 정렬해서 반환한다.
    return sorted(
        evaluations,
        key=lambda e: (e.key.lot_hist_id, e.key.parameter_id, e.key.recipe_step_no),
    )
