"""Detection 알람 규칙 (V5-A-1.3·V5-A-1.4).

시스템설계서 v2.1 1.3 계층 규칙: 이 모듈도 `summarize.py`와 같은 Rules/Model
계층이다. DB 조회·쓰기, HTTP, LLM 호출을 하지 않는 순수 계산만 담당한다.
`repository.py`가 읽어온 값을 넘겨받아 "SUMMARY 알람인지", "R03 3연속에
도달했는지" 같은 업무 규칙만 적용하고, 그 결과를 다시 `repository.py`가 저장(R03)
하거나 `service.py`가 reference와 대조(TRACE·SUMMARY)한다.

이 파일이 다루는 두 규칙(설계서 v2.1 4.3, 3.2):

1) V5-A-1.3 SUMMARY 알람 — "동적 관리한계" 판정
   TRACE 알람(`trace_alarm_history`)과 달리 SUMMARY 알람은 `dim_parameter`의
   고정 5선이 아니라, **같은 (chamber, parameter, recipe step) 그룹 안에서
   정상(=evaluation이 OOS가 아닌) wafer들의 summary 평균**으로 매번 새로
   관리한계(CL·UCL·LCL)를 계산한 뒤, 그 한계를 벗어나는 wafer를 OOC로 판정한다.
   즉 "규격"이 아니라 "이 데이터 안에서의 통계적 정상 범위"를 기준으로 삼는다.

2) V5-A-1.4 R03 파생 — "연속 3회 OOS" 판정
   같은 (chamber, parameter, recipe step)에서 `chamber_wafer_cum`(챔버 누적
   처리 순번, LOT 경계를 넘어 오름차순) 순서로 wafer를 쭉 나열했을 때, OOS가
   "끊기지 않고" 3번 연속되는 지점을 찾는다. 정확히 3에 "처음" 도달한 바로 그
   행에서만 R03 alarm 1건을 발행하고(설계서: "run이 3에 최초 도달한 행에서
   1회"), 4번째·5번째로 계속 OOS가 이어져도 추가로 발행하지 않는다. 중간에
   OOS가 아닌 wafer가 하나라도 끼면 연속 횟수를 0부터 다시 센다.

[팀원용 요약]
이 파일에서 눈여겨볼 함수는 딱 두 개다.
  - build_summary_alarm_flags(...)  : SUMMARY 알람 51건을 만드는 계산
  - derive_r03_events(...)          : R03 3건을 찾아내는 계산
나머지(_compute_r03_alarm_id 등 밑줄로 시작하는 함수)는 내부 보조 함수다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import sqrt

from app.common.enums import AlarmSource, AlarmType
from app.detection.summarize import GroupKey

__all__ = [
    "SummaryStatPoint",
    "SummaryControlLimit",
    "compute_summary_control_limits",
    "judge_summary_alarm",
    "build_summary_alarm_flags",
    "R03_POLICY_VERSION",
    "R03GroupMember",
    "R03Event",
    "R03AlarmRecord",
    "derive_r03_events",
    "build_r03_alarm_record",
]


# =====================================================================
# V5-A-1.3 — SUMMARY 알람(동적 관리한계 CL±3σ)
# =====================================================================
@dataclass(frozen=True, slots=True)
class SummaryStatPoint:
    """`summary_data` 한 행을 SUMMARY 알람 판정 입력 모양으로 정리한 값.

    `summarize.GroupSummary`(V5-A-1.1이 fdc_trace에서 직접 재계산한 값)와는
    다르다 — 이건 이미 계산이 끝난 summary 통계 한 행에, 이 규칙이 필요로 하는
    두 가지(어느 chamber인지, baseline 후보인지)만 덧붙인 것이다.
    """

    key: GroupKey  # lot_hist_id, parameter_id, recipe_step_no
    chamber_id: str
    value_mean: float
    # 설계서 4.3-1: "evaluation이 OOS가 아닌 summary mean을 baseline 후보로
    # 사용한다." 이 wafer의 evaluation 등급이 OOS가 "아니면" True — IN뿐 아니라
    # OOC도 baseline 후보에 포함된다(제외 대상은 OOS뿐).
    baseline_eligible: bool


@dataclass(frozen=True, slots=True)
class SummaryControlLimit:
    """(chamber_id, parameter_id, recipe_step_no) 한 그룹의 동적 관리한계."""

    chamber_id: str
    parameter_id: str
    recipe_step_no: int
    center_line: float  # CL = baseline 후보들의 산술평균
    upper_control_limit: float  # UCL = CL + 3 × 표본표준편차
    lower_control_limit: float  # LCL = CL - 3 × 표본표준편차
    baseline_count: int  # CL·표준편차 계산에 실제로 쓰인 baseline 후보 개수


def _control_group_key(point: SummaryStatPoint) -> tuple[str, str, int]:
    # SummaryStatPoint에서 "어느 관리한계 그룹에 속하는가"만 뽑아 tuple로 만든다.
    # GroupKey는 (lot_hist_id, parameter_id, recipe_step_no)라 lot_hist_id가
    # 섞여 있는데, 관리한계 그룹은 lot_hist_id 대신 chamber_id를 쓴다(설계서
    # 4.3: "관리한계 group은 chamber_id, parameter_id, recipe_step_no다") —
    # 즉 여러 LOT의 wafer가 같은 chamber를 지나갔으면 전부 한 그룹으로 묶인다.
    return (point.chamber_id, point.key.parameter_id, point.key.recipe_step_no)


def compute_summary_control_limits(
    points: Iterable[SummaryStatPoint],
) -> dict[tuple[str, str, int], SummaryControlLimit]:
    """baseline(evaluation != OOS) summary mean만 모아 그룹별 CL·UCL·LCL을 계산한다.

    설계서 4.3-1~2. `judge_summary_alarm`이 이 결과를 받아 개별 wafer를 판정한다.
    이 함수 자체는 판정을 하지 않는다(그룹당 한계선 하나만 만든다).
    """

    # 1) 그룹별로 baseline 후보의 value_mean만 리스트로 모은다.
    baseline_means: dict[tuple[str, str, int], list[float]] = {}
    for point in points:
        if not point.baseline_eligible:
            continue  # OOS로 판정된 wafer는 애초에 "정상 기준선" 후보가 아니다.
        group_key = _control_group_key(point)
        baseline_means.setdefault(group_key, []).append(point.value_mean)

    # 2) 그룹마다 평균(CL)과 표본표준편차(ddof=1)를 구해 UCL·LCL을 만든다.
    #    표본표준편차 계산 방식은 summarize.summarize_group과 동일하게 맞춘다
    #    (프로젝트 전체에서 "표준편차"라는 이름을 한 가지 정의로만 쓰기 위함).
    limits: dict[tuple[str, str, int], SummaryControlLimit] = {}
    for group_key, means in baseline_means.items():
        n = len(means)
        center = sum(means) / n
        if n >= 2:
            variance = sum((m - center) ** 2 for m in means) / (n - 1)
            std = sqrt(variance)
        else:
            # baseline이 1건뿐이면 표본표준편차가 정의되지 않는다. 0으로 두면
            # UCL=LCL=CL이 되어(폭이 0) 그 값 자체를 제외한 모든 값이 즉시
            # OOC로 잡히는데, 이는 "이상값이라서"가 아니라 "기준 표본이 부족해서"
            # 생기는 결과다. summarize.py도 같은 이유로 point_cnt<2일 때 None을
            # 쓰지만, 여기서는 한계선 자체가 아예 없으면 이후 계산이 더 복잡해지므로
            # 0으로 두고 baseline_count로 "표본이 얕았다"는 사실을 남긴다.
            std = 0.0
        chamber_id, parameter_id, recipe_step_no = group_key
        limits[group_key] = SummaryControlLimit(
            chamber_id=chamber_id,
            parameter_id=parameter_id,
            recipe_step_no=recipe_step_no,
            center_line=center,
            upper_control_limit=center + 3 * std,
            lower_control_limit=center - 3 * std,
            baseline_count=n,
        )
    return limits


def judge_summary_alarm(
    point: SummaryStatPoint,
    limit: SummaryControlLimit,
) -> AlarmType | None:
    """summary mean이 동적 UCL/LCL을 벗어나면 OOC, 아니면 알람 없음(None)을 반환한다.

    설계서 4.3-3. TRACE의 `judge_point`와 달리 등급이 하나(OOC)뿐이라 IN/OOS를
    반환하지 않는다 — "알람이다(OOC)" 또는 "알람이 아니다(None)" 둘 중 하나다.
    """

    out_of_range = (
        point.value_mean > limit.upper_control_limit
        or point.value_mean < limit.lower_control_limit
    )
    return AlarmType.OOC if out_of_range else None


def build_summary_alarm_flags(
    points: Sequence[SummaryStatPoint],
) -> dict[GroupKey, AlarmType]:
    """summary_data 전체를 관리한계로 판정해 "알람이 발생한 행"만 돌려준다.

    반환값은 {GroupKey: AlarmType.OOC} 형태다 — 알람이 없는 4,800건 대부분은
    dict에 아예 나타나지 않는다(딕셔너리 key 존재 여부 자체가 "알람 있음"의
    의미). 수용값은 이 dict의 길이가 51이다.

    baseline이 하나도 없는 그룹(그 chamber·parameter·step을 지난 wafer가
    전부 OOS)이 나오면 추측으로 넘어가지 않고 예외를 던진다 — summarize.py의
    "한계값 없으면 예외" 원칙과 동일하다.
    """

    limits = compute_summary_control_limits(points)

    flagged: dict[GroupKey, AlarmType] = {}
    for point in points:
        limit = limits.get(_control_group_key(point))
        if limit is None:
            raise ValueError(
                "관리한계 baseline이 없는 그룹입니다(해당 chamber·parameter·step의 "
                f"모든 wafer가 OOS): {_control_group_key(point)}"
            )
        alarm_type = judge_summary_alarm(point, limit)
        if alarm_type is not None:
            flagged[point.key] = alarm_type
    return flagged


# =====================================================================
# V5-A-1.4 — R03 파생(연속 3 OOS)
# =====================================================================
R03_POLICY_VERSION = "R03_CONSEC_V1"


@dataclass(frozen=True, slots=True)
class R03GroupMember:
    """R03 연속성 판정에 들어가는 wafer 1장(=lot_hist_id 1건) 분량의 후보.

    "이 wafer가 이 chamber·parameter·step에서 OOS였는가"라는 그룹 단위 등급
    (evaluate_groups의 GroupEvaluation.alarm_type)과, 정렬·식별에 필요한
    lot_history 정보만 담는다. R03 alarm_id·member_alarm_refs 같은 "발행
    결과"는 여기 없다 — 그건 run이 3에 도달했을 때 R03Event로만 만들어진다.
    """

    lot_hist_id: str
    lot_id: str
    wafer_no: int
    wafer_id: str
    # 설계서 3.2 order: "chamber_wafer_cum ASC, track_in_at ASC, lot_hist_id
    # ASC". chamber_wafer_cum이 1차 정렬 기준이고, 나머지 둘은 동점 처리용이다.
    chamber_wafer_cum: int
    track_in_at: datetime | None
    alarm_type: AlarmType  # 이 wafer·parameter·step 조합의 evaluation 등급


@dataclass(frozen=True, slots=True)
class R03Event:
    """연속 OOS가 3에 "처음" 도달한 순간 발행되는 R03 event 1건.

    아직 `alarm_id`·`member_alarm_refs`(raw TRACE AlarmRef 목록)는 없다 —
    그 둘은 `trace_alarm_history` 조회가 필요해서 DB를 만지지 않는 이 계층
    대신 `service.py`가 채운 뒤 `build_r03_alarm_record`로 완성한다.
    """

    chamber_id: str
    parameter_id: str
    recipe_step_no: int
    equipment_id: str
    # 연속 3회를 이룬 wafer 3개를 "발생 순서(=chamber_wafer_cum 오름차순)"
    # 그대로 보관한다.
    members: tuple[R03GroupMember, R03GroupMember, R03GroupMember]

    @property
    def owner(self) -> R03GroupMember:
        # 설계서 3.2: "owner = 세 번째 OOS의 lot_id·chamber_id". 세 번째
        # 멤버(=연속 3회를 완성시킨 그 wafer)가 이 R03 event의 대표(owner)다.
        return self.members[2]

    @property
    def occurred_at(self) -> datetime | None:
        return self.owner.track_in_at


@dataclass(frozen=True, slots=True)
class R03AlarmRecord:
    """`r03_alarm_history` INSERT 1행 분량으로 완성된 값(설계서 3.2 필수 컬럼).

    `repository.insert_r03_alarms`가 이 값을 그대로 바인드 파라미터로 써서
    저장한다. jsonb 컬럼(member_wafer_refs·member_alarm_refs)은 여기서는
    아직 파이썬 list[dict]다 — SQL로 넘길 때 텍스트로 직렬화하는 일은
    repository.py가 한다(계층 규칙: 이 모듈은 DB를 모른다).
    """

    alarm_id: str
    occurred_at: datetime
    lot_hist_id: str  # owner의 lot_hist_id
    lot_id: str  # owner의 lot_id
    equipment_id: str
    chamber_id: str
    parameter_id: str
    recipe_step_no: int
    trigger_wafer_no: int  # owner의 wafer_no
    member_wafer_refs: list[dict[str, object]]
    member_alarm_refs: list[dict[str, object]]
    policy_version: str


def _member_sort_key(member: R03GroupMember) -> tuple[int, datetime, str]:
    # 설계서 3.2 order를 그대로 튜플 비교로 옮긴 것. track_in_at이 NULL인
    # 행은(있어서는 안 되지만) datetime.min으로 취급해 정렬이 죽지 않게 한다 —
    # 예외를 던지는 대신 "가장 이른 시각"으로 두면 chamber_wafer_cum(1차 기준)이
    # 대부분의 경우 이미 순서를 결정하므로 안전한 완충 장치가 된다.
    return (
        member.chamber_wafer_cum,
        member.track_in_at or datetime.min,
        member.lot_hist_id,
    )


def derive_r03_events(
    members: Iterable[R03GroupMember],
    *,
    chamber_id: str,
    parameter_id: str,
    recipe_step_no: int,
    equipment_id: str,
) -> list[R03Event]:
    """한 (chamber_id, parameter_id, recipe_step_no) 그룹 안에서 연속 3 OOS를 찾는다.

    호출자(service.py)가 이미 이 세 값으로 그룹을 나눠서 넘겨준다는 전제다 —
    이 함수는 "이 그룹 안에서" 정렬·연속 카운트만 한다(그룹을 나누는 일은
    하지 않는다).

    핵심 규칙(설계서 3.2):
      - chamber_wafer_cum 오름차순으로 봤을 때 OOS가 끊기지 않고 이어진 개수를 센다.
      - OOS가 아닌 wafer가 하나라도 나오면 그 개수를 즉시 0으로 되돌린다.
      - 개수가 "3이 되는 바로 그 순간"에만 R03Event를 1개 만든다. 4, 5, …로
        계속 OOS가 이어져도 같은 run에서는 두 번째 event를 만들지 않는다
        (설계서: "4번째 이상의 연속 OOS가 같은 run에서 중복 event를 만들면 실패한다").
    """

    ordered = sorted(members, key=_member_sort_key)

    events: list[R03Event] = []
    current_run: list[R03GroupMember] = []
    # 이번 run에서 이미 R03을 발행했는지 표시. run이 리셋(비OOS)될 때만 다시
    # False로 되돌아간다 — "3에 최초 도달"만 잡고 그 뒤는 무시하기 위한 장치다.
    already_emitted_for_current_run = False

    for member in ordered:
        if member.alarm_type is not AlarmType.OOS:
            # 연속이 끊겼다. 지금까지 쌓은 run과 "이미 발행했는지" 상태를
            # 함께 초기화한다.
            current_run = []
            already_emitted_for_current_run = False
            continue

        current_run.append(member)

        if len(current_run) == 3 and not already_emitted_for_current_run:
            # 방금 추가된 member가 "연속 3회를 완성시킨 세 번째 OOS"다.
            first_three = (current_run[0], current_run[1], current_run[2])
            events.append(
                R03Event(
                    chamber_id=chamber_id,
                    parameter_id=parameter_id,
                    recipe_step_no=recipe_step_no,
                    equipment_id=equipment_id,
                    members=first_three,
                )
            )
            already_emitted_for_current_run = True
        # len(current_run) > 3인 경우: run은 계속 자라지만(디버깅 편의상 버리지
        # 않는다) already_emitted_for_current_run이 True라 다시 발행되지 않는다.

    return events


def _compute_r03_alarm_id(
    *,
    owner_lot_hist_id: str,
    chamber_id: str,
    parameter_id: str,
    recipe_step_no: int,
    policy_version: str,
) -> str:
    """설계서 3.2의 ID 규칙을 그대로 구현한다.

    "owner lot_hist_id, chamber_id, parameter_id, 정수 recipe_step_no,
    policy_version을 key 오름차순·UTF-8·공백 없는 JSON으로 직렬화한 뒤
    SHA-256 앞 20개 lowercase hex에 R03-를 붙인다."

    occurred_at·member 배열은 **일부러 payload에 넣지 않는다** — 그 값들은
    "언제·어떤 순서로 보여주느냐"에 대한 부가 정보(provenance)일 뿐이고,
    이 다섯 필드만으로 이미 R03 event 하나가 유일하게 결정되기 때문이다.
    저장 순서가 달라졌다고 ID가 바뀌면 재실행 때마다 다른 alarm_id가 생겨
    멱등 적재(`ON CONFLICT`)가 깨진다.
    """

    payload = {
        "source": "R03",
        "owner_lot_hist_id": owner_lot_hist_id,
        "chamber_id": chamber_id,
        "parameter_id": parameter_id,
        "recipe_step_no": recipe_step_no,
        "policy_version": policy_version,
    }
    # sort_keys=True: 파이썬 dict의 key를 알파벳(코드포인트) 오름차순으로 정렬해
    # 직렬화한다 — "key 오름차순" 요구사항. separators=(",", ":")는 기본값의
    # ", "· ": " 대신 공백 없는 구분자를 써서 "공백 없는 JSON" 요구사항을 만족한다.
    serialized = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    # hexdigest()는 항상 소문자 hex 문자열이다.
    digest = hashlib.sha256(serialized).hexdigest()
    return f"R03-{digest[:20]}"


def build_r03_alarm_record(
    event: R03Event,
    *,
    member_alarm_ids: Sequence[str],
) -> R03AlarmRecord:
    """R03Event(발행 여부만 결정된 상태)와 TRACE alarm_id 목록을 저장 행으로 완성한다.

    `member_alarm_ids`는 호출자(service.py)가 `trace_alarm_history`를 조회해서
    만든, "이 3개 wafer·같은 parameter·step의 raw OOS TRACE alarm_id 전체"를
    `seq_no ASC, alarm_id ASC`로 이미 정렬해 넘긴 목록이다(설계서 3.2 — 최종
    epoch에서는 R03 한 건당 9개). 정렬·조회는 이 함수의 책임이 아니다(DB를
    모르는 계층이라 정렬 기준이 되는 seq_no 자체를 갖고 있지 않다) — 이 함수는
    이미 정렬된 순수 alarm_id 문자열들을 받아 "AlarmRef(source+alarm_id) 모양"으로
    감싸는, 순수하게 이 모듈만 아는 저장 형태 결정만 담당한다.
    """

    owner = event.owner
    alarm_id = _compute_r03_alarm_id(
        owner_lot_hist_id=owner.lot_hist_id,
        chamber_id=event.chamber_id,
        parameter_id=event.parameter_id,
        recipe_step_no=event.recipe_step_no,
        policy_version=R03_POLICY_VERSION,
    )

    if event.occurred_at is None:
        # owner(=세 번째 OOS wafer)의 track_in_at이 NULL이면 r03_alarm_history의
        # occurred_at NOT NULL 제약을 어기게 된다. 임의로 지금 시각 등을 넣지
        # 않고 즉시 실패시켜 "왜 발행이 안 됐는지" 바로 알 수 있게 한다.
        raise ValueError(
            f"R03 owner(lot_hist_id={owner.lot_hist_id})의 track_in_at이 없습니다"
        )

    # member_wafer_refs: 설계서 3.2 "{lot_hist_id, wafer_id} 정확히 3개",
    # 계산 순서(=연속 3회가 만들어진 순서) 그대로 담는다.
    member_wafer_refs = [
        {"lot_hist_id": member.lot_hist_id, "wafer_id": member.wafer_id}
        for member in event.members
    ]

    return R03AlarmRecord(
        alarm_id=alarm_id,
        occurred_at=event.occurred_at,
        lot_hist_id=owner.lot_hist_id,
        lot_id=owner.lot_id,
        equipment_id=event.equipment_id,
        chamber_id=event.chamber_id,
        parameter_id=event.parameter_id,
        recipe_step_no=event.recipe_step_no,
        trigger_wafer_no=owner.wafer_no,
        member_wafer_refs=member_wafer_refs,
        # member_alarm_refs: 설계서 3.1 AlarmRef 모양(source+alarm_id)을 그대로
        # 따른다. "TRACE"를 문자열로 직접 쓰지 않고 AlarmSource.TRACE.value를
        # 거치면, 나중에 누가 enum 멤버 이름을 오타로 바꿔도 여기서 바로
        # AttributeError로 걸린다(조용히 잘못된 문자열이 저장되지 않는다).
        member_alarm_refs=[
            {"source": AlarmSource.TRACE.value, "alarm_id": alarm_id}
            for alarm_id in member_alarm_ids
        ],
        policy_version=R03_POLICY_VERSION,
    )
