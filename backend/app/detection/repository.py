"""Detection Repository (V5-A-1.1~V5-A-1.5).

시스템설계서 v2.1 1.3 계층 규칙: Repository는 PostgreSQL 조회만 담당한다.
HTTP 응답 조립, 업무 판정(OOS/OOC/IN, alarm 발행 등), LLM 호출을 하지 않는다.
`summarize.py`·`rules.py`(Rules 계층)의 입력·비교 대상을 만들어 넘기기만 한다.

01-project-rules.md 6절: SELECT * 를 쓰지 않고 필요한 컬럼만 조회한다. SQL 문자열
조합 대신 파라미터 바인딩을 쓴다.

컬럼 근거:
- 시스템설계서 v2.1 2.2 "네이밍·물리 schema" — `summary_data`·`evaluation`·
  `trace_alarm_history`·`summary_alarm_history`의 `wafer`는 smallint가 아니라
  varchar(24) wafer_id 문자열이다. `lot_history`만 `wafer_no`(smallint)와
  `wafer_id`(varchar)를 모두 가진다. 이 모듈은 wafer 비교가 필요 없는 조회만
  다루므로 wafer 컬럼 자체는 읽지 않는다.
- `infra/bootstrap/001_base_schema.sql` — 컬럼 이름 표기(예: `parameter`,
  `step_no`)의 근거. 다만 이 파일은 kosa_0813 epoch 산출물이라 V5-CM-1.6에서
  삭제 예정이며, 최종 스키마의 유일한 근거는 멘토 `sample/schema/03_schema_clean.sql`
  원본이다. `V5-CM-2.4` 적재 검증 결과 실제 컬럼명이 다르면 이 파일의 SQL을
  맞춰 고친다 — 지금은 설계서 v2.1 값 정의와 일치하는 최선 추정치다.
- `backend/migrations/v5/001_reference_extensions_final.sql` — `r03_alarm_history`
  12컬럼·`v_alarm_event`의 근거. `V5-CM-3.1`이 **빈 테이블**로 먼저 만들어두고,
  이 파일의 V5-A-1.4 절이 실제로 채운다.

V5-A-1.1·V5-A-1.2 함수(위쪽 절)는 전부 읽기 전용이다(base 9 table은 bootstrap이
적재하고, A는 쓰지 않는다). 호출자는 `app.common.db.get_readonly_connection()`으로
얻은 Connection을 넘긴다.

**예외: V5-A-1.4 절의 `insert_r03_alarms`만 쓰기 함수다.** `r03_alarm_history`는
base 9 table이 아니라 `V5-CM-3.1`이 만들어둔 **빈** reference extension이고,
"R03 파생은 V5-A-1.4가 적재한다"라고 그 migration 자체에 적혀 있다(설계서 3.3).
그래서 이 함수만 `app.common.db.get_db_connection()`(쓰기 권한) Connection을
받는다 — 나머지 함수에 readonly Connection을 넘기던 습관대로 이 함수도 readonly로
부르면 권한 오류가 난다.

[팀원용 요약]
이 파일이 하는 일은 크게 네 갈래다.
  1) summarize.py에 넣을 "원본" 조회: fetch_trace_points, fetch_parameter_limits
     -> fdc_trace·dim_parameter를 읽어서 summarize.py가 이해하는 모양
        (TracePoint·ParameterLimit)으로 바꿔준다.
  2) 이미 있는 "정답"(reference) 조회: fetch_reference_summary,
     fetch_reference_evaluation, fetch_trace_alarm_reference_stats,
     fetch_summary_alarm_reference_stats
     -> summary_data·evaluation·trace_alarm_history·summary_alarm_history에는
        최종 데이터에 이미 계산돼서 들어있는 결과가 있다. 우리가 새로 계산한
        값이 이 정답과 같은지 비교하는 데 쓴다. 비교 자체는 이 파일이 아니라
        service.py가 한다 — 이 파일은 "가져오기"만 하고 "맞다/틀리다" 판단은
        하지 않는다.
  3) R03 계산에 필요한 lot_history 순서·TRACE alarm_id 조회: fetch_lot_history_rows,
     fetch_lot_history_by_id, fetch_trace_alarm_refs_by_group
     -> rules.derive_r03_events·rules.build_r03_alarm_record가 쓸 입력을 만든다.
  4) (유일한 예외) R03 적재: fetch_r03_alarm_count, insert_r03_alarms
     -> rules.py가 만든 R03AlarmRecord를 실제로 저장한다. 멱등(재실행해도
        중복 없음)하도록 `ON CONFLICT ... DO NOTHING`을 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.common.enums import AlarmType
from app.detection.rules import R03AlarmRecord
from app.detection.summarize import GroupKey, ParameterLimit, TracePoint
from sqlalchemy import text
from sqlalchemy.engine import Connection

__all__ = [
    "ReferenceSummaryRow",
    "ReferenceEvaluationRow",
    "fetch_trace_points",
    "fetch_parameter_limits",
    "fetch_reference_summary",
    "fetch_reference_evaluation",
    "AlarmReferenceStats",
    "fetch_trace_alarm_reference_stats",
    "fetch_summary_alarm_reference_stats",
    "SummaryStatRow",
    "fetch_summary_statistics",
    "LotHistoryRow",
    "fetch_lot_history_rows",
    "fetch_lot_history_by_id",
    "TraceAlarmRefRow",
    "fetch_trace_alarm_refs_by_group",
    "fetch_r03_alarm_count",
    "insert_r03_alarms",
    "IncidentAlarmCountRow",
    "fetch_incident_alarm_counts",
    "ReferenceActionRow",
    "fetch_reference_actions",
]


def _as_float_or_none(value: object) -> float | None:
    # DB에서 온 값(Decimal 또는 None)을 float 또는 None으로 통일한다.
    # PostgreSQL의 numeric 타입은 파이썬에서 Decimal로 오는데, summarize.py의
    # 데이터 클래스들은 전부 float를 기대하므로 여기서 한 번에 변환해준다.
    # value가 None이면(즉 DB 컬럼이 NULL이면) None을 그대로 돌려준다 — 0.0으로
    # 임의 대체하지 않는다(0과 NULL은 의미가 다르므로).
    return None if value is None else float(value)


# ---------------------------------------------------------------------
# fdc_trace / dim_parameter — summarize.py 입력
# ---------------------------------------------------------------------
def fetch_trace_points(
    connection: Connection,
    *,
    lot_hist_ids: Sequence[str] | None = None,
) -> list[TracePoint]:
    """`fdc_trace` raw point를 읽어 `summarize.py` 입력(TracePoint)으로 변환한다.

    `lot_hist_ids`를 주면 해당 LOT 이력만 읽는다(단건 재계산 재사용, summarize.py
    docstring이 말하는 "batch/단건 요청 모두 같은 함수를 재사용" 경로). 생략하면
    14,400건 전체를 읽는다(V5-A-1.1 batch 재계산 완료 기준 검증용).

    dim_parameter overlay·seq_no 재채번은 하지 않는다(A-detection.md 주의). 최종
    데이터를 그대로 읽는다.
    """

    # 필요한 컬럼만 명시적으로 나열한다(SELECT * 금지, 01-project-rules.md §6).
    # 이 5개 컬럼이 summarize.TracePoint의 5개 필드와 1:1로 대응한다.
    query = """
        SELECT lot_hist_id, parameter_id, recipe_step_no, seq_no, value
        FROM fdc_trace
    """
    params: dict[str, object] = {}

    # lot_hist_ids가 주어졌을 때만 WHERE 절을 덧붙인다. 이때도 실제 값은 SQL
    # 문자열에 직접 끼워 넣지 않고 :lot_hist_ids 라는 이름의 바인드 파라미터로
    # 전달한다(파라미터 바인딩) — SQL 인젝션을 막고, 값 자체는 아래 params
    # 딕셔너리에만 담긴다. `= ANY(:lot_hist_ids)`는 "lot_hist_id가 이 목록 중
    # 하나와 같으면"이라는 뜻으로, IN (...) 과 같은 동작을 하는 PostgreSQL 문법이다.
    if lot_hist_ids is not None:
        query += " WHERE lot_hist_id = ANY(:lot_hist_ids)"
        params["lot_hist_ids"] = list(lot_hist_ids)

    # 결과 순서를 고정해둔다. summarize.py 쪽에서 다시 정렬하긴 하지만, 조회
    # 단계에서부터 순서가 일정하면 디버깅할 때 결과를 눈으로 보기 편하다.
    query += " ORDER BY lot_hist_id, parameter_id, seq_no"

    # text(query): 순수 문자열 SQL을 SQLAlchemy가 실행할 수 있는 객체로 감싼다.
    # .execute(...).mappings(): 각 행을 (컬럼명 -> 값) 딕셔너리처럼 다룰 수 있게
    # 해준다(row[0] 같은 위치 인덱싱 대신 row["value"]처럼 이름으로 접근).
    # .all(): 커서를 끝까지 읽어서 리스트로 한 번에 받는다.
    rows = connection.execute(text(query), params).mappings().all()

    # DB 행(딕셔너리) 하나하나를 summarize.py가 아는 TracePoint 객체로 바꾼다.
    # value만 float()로 명시 변환한다 — numeric(12,4) 컬럼이 Decimal로 오기 때문.
    return [
        TracePoint(
            lot_hist_id=row["lot_hist_id"],
            parameter_id=row["parameter_id"],
            recipe_step_no=row["recipe_step_no"],
            seq_no=row["seq_no"],
            value=float(row["value"]),
        )
        for row in rows
    ]


def fetch_parameter_limits(connection: Connection) -> dict[str, ParameterLimit]:
    """`dim_parameter` 8행을 `parameter_id -> ParameterLimit` 매핑으로 읽는다.

    한계값이 없는 parameter_id를 임의로 채우지 않는다 — 없으면 그대로 매핑에서
    빠지고, `summarize.evaluate_groups`가 데이터 누락으로 예외를 낸다.
    """

    # dim_parameter는 8행뿐이라 WHERE 없이 전체를 읽는다.
    query = """
        SELECT parameter_id, spec_lower, ctrl_lower, ctrl_upper, spec_upper, upper_only
        FROM dim_parameter
    """
    rows = connection.execute(text(query)).mappings().all()

    # 딕셔너리 컴프리헨션: "parameter_id를 key로, 나머지 값을 담은 ParameterLimit을
    # value로" 하는 dict를 한 줄로 만든다. 한계선(spec_lower 등)은
    # _as_float_or_none을 거쳐 None 또는 float가 된다 — 값이 없다고 0으로
    # 대체하면 "한계가 0이다"라는 잘못된 뜻이 되므로 반드시 None을 구분해야 한다.
    return {
        row["parameter_id"]: ParameterLimit(
            parameter_id=row["parameter_id"],
            spec_lower=_as_float_or_none(row["spec_lower"]),
            ctrl_lower=_as_float_or_none(row["ctrl_lower"]),
            ctrl_upper=_as_float_or_none(row["ctrl_upper"]),
            spec_upper=_as_float_or_none(row["spec_upper"]),
            upper_only=bool(row["upper_only"]),
        )
        for row in rows
    }


# ---------------------------------------------------------------------
# summary_data / evaluation reference — 재계산 대조용
#
# 이 함수들은 비교하지 않는다. 재계산 결과와 대조해 "불일치 0건"을 증명하는
# 일은 service.py의 몫이다(계층 규칙: Repository는 업무 판정을 하지 않는다).
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ReferenceSummaryRow:
    """`summary_data` reference 원본 한 행. V5-A-1.1 재계산 대조용."""

    # summarize.py의 GroupSummary와 필드 구성이 거의 같다. 다른 점은 이쪽은
    # "DB에 이미 저장된 정답"이고, GroupSummary는 "우리가 방금 계산한 값"이라는
    # 것뿐이다. key(GroupKey)를 필드로 들고 있어서 service.py가 두 값을 같은
    # key끼리 짝지어 비교할 수 있다.
    key: GroupKey
    value_mean: float
    value_std: float | None
    value_min: float
    value_max: float
    point_cnt: int


def fetch_reference_summary(connection: Connection) -> dict[GroupKey, ReferenceSummaryRow]:
    """기존 `summary_data` 4,800건을 `GroupKey -> ReferenceSummaryRow`로 읽는다."""

    # 실제 컬럼명은 parameter·step_no이지만, 우리 코드 전체에서는
    # summarize.GroupKey와 이름을 맞추려고 parameter_id·recipe_step_no라는
    # 별칭(AS)을 붙인다. DB 컬럼명을 바꾸는 게 아니라 "이 조회 결과에서만" 그렇게
    # 부르겠다는 뜻이다 — 이후 파이썬 코드에서 두 종류의 이름을 섞어 쓰지 않아도
    # 되니 실수를 줄여준다.
    query = """
        SELECT lot_hist_id, parameter AS parameter_id, step_no AS recipe_step_no,
               value_mean, value_std, value_min, value_max, point_cnt
        FROM summary_data
    """
    rows = connection.execute(text(query)).mappings().all()

    result: dict[GroupKey, ReferenceSummaryRow] = {}
    for row in rows:
        # 행 하나에서 3개 키 컬럼만 뽑아 GroupKey를 만든다. GroupKey는
        # frozen=True라 불변이고, 그래서 dict의 key로 쓸 수 있다(파이썬 규칙:
        # 변경 가능한 객체는 dict key가 될 수 없다).
        key = GroupKey(
            lot_hist_id=row["lot_hist_id"],
            parameter_id=row["parameter_id"],
            recipe_step_no=row["recipe_step_no"],
        )
        # 같은 key로 값(ReferenceSummaryRow)을 찾을 수 있도록 dict에 채워 넣는다.
        # summary_data의 PK가 (lot_hist_id, parameter, step_no)라서 key 중복은
        # 원래 있을 수 없다 — 중복이 생긴다면 그건 데이터 자체가 이상한 것이다.
        result[key] = ReferenceSummaryRow(
            key=key,
            value_mean=float(row["value_mean"]),
            value_std=_as_float_or_none(row["value_std"]),
            value_min=float(row["value_min"]),
            value_max=float(row["value_max"]),
            point_cnt=int(row["point_cnt"]),
        )
    return result


@dataclass(frozen=True, slots=True)
class ReferenceEvaluationRow:
    """`evaluation` reference 원본 한 행. V5-A-1.2 재현 대조용."""

    key: GroupKey
    point_cnt: int
    ooc_point_cnt: int
    oos_point_cnt: int
    # DB에는 문자열('IN'/'OOC'/'OOS')로 저장돼 있지만, 여기서는 미리 AlarmType
    # enum으로 변환해둔다. 그래야 service.py에서 summarize.py가 계산한
    # GroupEvaluation.alarm_type(역시 AlarmType)과 타입을 맞춰서 바로
    # == 비교할 수 있다("OOS" == AlarmType.OOS는 문자열 비교로도 참이긴 하지만,
    # 아예 같은 타입으로 통일해두면 실수로 오타 문자열이 섞여도 여기서 먼저
    # ValueError로 걸러진다).
    alarm_type: AlarmType


def fetch_reference_evaluation(connection: Connection) -> dict[GroupKey, ReferenceEvaluationRow]:
    """기존 `evaluation` 4,800건을 `GroupKey -> ReferenceEvaluationRow`로 읽는다."""

    query = """
        SELECT lot_hist_id, parameter AS parameter_id, step_no AS recipe_step_no,
               point_cnt, ooc_point_cnt, oos_point_cnt, alarm_type
        FROM evaluation
    """
    rows = connection.execute(text(query)).mappings().all()

    result: dict[GroupKey, ReferenceEvaluationRow] = {}
    for row in rows:
        key = GroupKey(
            lot_hist_id=row["lot_hist_id"],
            parameter_id=row["parameter_id"],
            recipe_step_no=row["recipe_step_no"],
        )
        result[key] = ReferenceEvaluationRow(
            key=key,
            point_cnt=int(row["point_cnt"]),
            ooc_point_cnt=int(row["ooc_point_cnt"]),
            oos_point_cnt=int(row["oos_point_cnt"]),
            # row["alarm_type"]은 'IN'/'OOC'/'OOS' 같은 문자열이다.
            # AlarmType(...)으로 감싸면 이 3개 값 중 하나가 아닐 때 즉시
            # ValueError가 나서, 데이터가 이상해도 조용히 넘어가지 않는다.
            alarm_type=AlarmType(row["alarm_type"]),
        )
    return result


# ---------------------------------------------------------------------
# trace_alarm_history / summary_alarm_history reference — V5-A-1.3 대조용
#
# 이 두 테이블은 summary_data·evaluation과 같은 신분이다 — Generator가 만든
# "이미 저장된 정답"이며 A가 새로 채우는 테이블이 아니다(그건 r03_alarm_history
# 하나뿐이고, 아래 절에서 별도로 다룬다). 그래서 여기서는 값 하나하나를 읽어오지
# 않고 "총 몇 건, 그중 occurred_at이 비어있는 게 몇 건" 두 숫자만 집계해서
# service.py의 재현 대조(recomputed count와 비교)에 쓴다.
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AlarmReferenceStats:
    """`trace_alarm_history`·`summary_alarm_history` 한 테이블의 요약 통계."""

    count: int  # 전체 저장 건수(수용값: TRACE 138, SUMMARY 51)
    occurred_at_null_count: int  # occurred_at이 NULL인 건수(수용값: 둘 다 0)


def _fetch_alarm_reference_stats(
    connection: Connection, table_name: str
) -> AlarmReferenceStats:
    # table_name은 함수 인자로 받되 사용자 입력이 아니라 이 파일 안에서만 두
    # 상수("trace_alarm_history"/"summary_alarm_history") 중 하나로 호출하므로
    # SQL 인젝션 우려 없이 f-string으로 테이블명을 끼워 넣는다(값이 아니라
    # 테이블 식별자라서 애초에 바인드 파라미터로 넘길 수도 없다 — PostgreSQL은
    # 테이블명 자리에 파라미터를 허용하지 않는다).
    #
    # COUNT(*) FILTER (WHERE ...): 조건에 맞는 행만 세는 PostgreSQL 집계
    # 문법이다. "WHERE occurred_at IS NULL인 행 수"를 별도 서브쿼리 없이
    # 한 번의 스캔으로 같이 구한다.
    query = f"""
        SELECT
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE occurred_at IS NULL) AS null_count
        FROM {table_name}
    """
    row = connection.execute(text(query)).mappings().one()
    return AlarmReferenceStats(
        count=int(row["total_count"]),
        occurred_at_null_count=int(row["null_count"]),
    )


def fetch_trace_alarm_reference_stats(connection: Connection) -> AlarmReferenceStats:
    """`trace_alarm_history`의 총 건수·occurred_at NULL 건수를 읽는다."""

    return _fetch_alarm_reference_stats(connection, "trace_alarm_history")


def fetch_summary_alarm_reference_stats(connection: Connection) -> AlarmReferenceStats:
    """`summary_alarm_history`의 총 건수·occurred_at NULL 건수를 읽는다."""

    return _fetch_alarm_reference_stats(connection, "summary_alarm_history")


# ---------------------------------------------------------------------
# summary_data 통계값 — V5-A-1.3 SUMMARY 알람(동적 CL±3σ) 계산 입력
#
# fetch_reference_summary와 같은 테이블(summary_data)을 읽지만 목적이 다르다.
# fetch_reference_summary는 "value_mean 등이 fdc_trace 재계산과 같은가"를 보고,
# 이 함수는 "이 value_mean이 동적 관리한계를 벗어나는가"를 보려고 chamber_id를
# 추가로 읽는다(rules.compute_summary_control_limits의 그룹 기준이 chamber_id다).
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SummaryStatRow:
    """`summary_data`에서 SUMMARY 알람 판정에 필요한 컬럼만 뽑은 한 행."""

    key: GroupKey  # lot_hist_id, parameter_id, recipe_step_no
    chamber_id: str
    value_mean: float


def fetch_summary_statistics(connection: Connection) -> list[SummaryStatRow]:
    """`summary_data` 4,800건을 SUMMARY 알람 계산용 모양(SummaryStatRow)으로 읽는다."""

    query = """
        SELECT lot_hist_id, parameter AS parameter_id, step_no AS recipe_step_no,
               chamber AS chamber_id, value_mean
        FROM summary_data
    """
    rows = connection.execute(text(query)).mappings().all()
    return [
        SummaryStatRow(
            key=GroupKey(
                lot_hist_id=row["lot_hist_id"],
                parameter_id=row["parameter_id"],
                recipe_step_no=row["recipe_step_no"],
            ),
            chamber_id=row["chamber_id"],
            value_mean=float(row["value_mean"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------
# lot_history — V5-A-1.4 R03 연속성 판정 입력
#
# lot_history는 "WAFER 1장이 한 번의 설비 방문(=2개 recipe step)을 지난 기록"
# 600건이다. R03 연속 판정의 1차 정렬 기준인 chamber_wafer_cum과, wafer/lot/
# 시각 정보가 전부 이 테이블에만 있어서 fdc_trace·summary_data·evaluation
# 어느 것도 대신할 수 없다.
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LotHistoryRow:
    """`lot_history` 한 행. R03 정렬·식별에 필요한 컬럼만 담는다."""

    lot_hist_id: str
    lot_id: str
    wafer_no: int
    wafer_id: str
    area_id: str | None
    equipment_id: str | None
    chamber_id: str | None
    recipe_id: str | None
    track_in_at: datetime | None
    chamber_wafer_cum: int | None


def fetch_lot_history_rows(connection: Connection) -> list[LotHistoryRow]:
    """`lot_history` 600건 전체를 읽는다."""

    query = """
        SELECT lot_hist_id, lot_id, wafer_no, wafer_id, area_id, equipment_id,
               chamber_id, recipe_id, track_in_at, chamber_wafer_cum
        FROM lot_history
    """
    rows = connection.execute(text(query)).mappings().all()
    return [
        LotHistoryRow(
            lot_hist_id=row["lot_hist_id"],
            lot_id=row["lot_id"],
            wafer_no=int(row["wafer_no"]),
            wafer_id=row["wafer_id"],
            area_id=row["area_id"],
            equipment_id=row["equipment_id"],
            chamber_id=row["chamber_id"],
            recipe_id=row["recipe_id"],
            track_in_at=row["track_in_at"],
            chamber_wafer_cum=(
                None
                if row["chamber_wafer_cum"] is None
                else int(row["chamber_wafer_cum"])
            ),
        )
        for row in rows
    ]


def fetch_lot_history_by_id(connection: Connection) -> dict[str, LotHistoryRow]:
    """`fetch_lot_history_rows`와 같은 데이터를 `lot_hist_id -> LotHistoryRow`로 읽는다.

    R03 계산은 evaluation(=fdc_trace 재계산) 결과를 lot_hist_id 단위로 순회하면서
    "이 wafer는 어느 chamber·언제 들어왔는지"를 매번 찾아봐야 해서, 리스트보다
    dict(빠른 조회) 모양이 더 자연스럽다.
    """

    return {row.lot_hist_id: row for row in fetch_lot_history_rows(connection)}


# ---------------------------------------------------------------------
# trace_alarm_history × lot_history — R03 member_alarm_refs용 TRACE alarm_id 조회
#
# r03_alarm_history.member_alarm_refs에 넣을 "raw OOS TRACE AlarmRef"는 이미
# trace_alarm_history에 저장돼 있는 실제 alarm_id다. 다만 trace_alarm_history는
# lot_hist_id가 아니라 (lot, wafer, chamber) 자연키로 저장돼 있어서, R03 계산이
# 쓰는 lot_hist_id 기준으로 다시 묶으려면 lot_history와 조인해야 한다 — 이 조인은
# `v_alarm_event` View가 TRACE를 lot_history와 붙일 때 쓰는 것과 똑같다
# (`h.lot_id = a.lot AND h.wafer_id = a.wafer AND h.chamber_id = a.chamber`).
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TraceAlarmRefRow:
    """R03 member_alarm_refs 직렬화 순서(seq_no ASC, alarm_id ASC) 정렬 입력."""

    alarm_id: str
    seq_no: int | None


def fetch_trace_alarm_refs_by_group(
    connection: Connection,
) -> dict[GroupKey, list[TraceAlarmRefRow]]:
    """`trace_alarm_history`를 `(lot_hist_id, parameter_id, recipe_step_no)`로
    묶어 읽는다.

    반환값의 각 리스트는 이미 `seq_no ASC, alarm_id ASC`로 정렬돼 있다(설계서
    3.2: "AlarmRef는 해당 WAFER 순서 안에서 seq_no ASC, alarm_id ASC로
    직렬화한다") — 호출자(service.py)가 다시 정렬할 필요가 없다.
    """

    query = """
        SELECT
            h.lot_hist_id      AS lot_hist_id,
            a.parameter        AS parameter_id,
            a.step_no          AS recipe_step_no,
            a.alarm_id         AS alarm_id,
            a.seq_no           AS seq_no
        FROM trace_alarm_history AS a
        JOIN lot_history AS h
          ON h.lot_id = a.lot
         AND h.wafer_id = a.wafer
         AND h.chamber_id = a.chamber
        ORDER BY h.lot_hist_id, a.parameter, a.step_no, a.seq_no, a.alarm_id
    """
    rows = connection.execute(text(query)).mappings().all()

    result: dict[GroupKey, list[TraceAlarmRefRow]] = {}
    for row in rows:
        key = GroupKey(
            lot_hist_id=row["lot_hist_id"],
            parameter_id=row["parameter_id"],
            recipe_step_no=row["recipe_step_no"],
        )
        # SQL의 ORDER BY로 이미 seq_no·alarm_id 순으로 정렬해서 가져오므로,
        # 여기서는 그 순서를 유지한 채 그룹별 리스트에 append만 하면 된다.
        result.setdefault(key, []).append(
            TraceAlarmRefRow(
                alarm_id=row["alarm_id"],
                seq_no=(None if row["seq_no"] is None else int(row["seq_no"])),
            )
        )
    return result


# ---------------------------------------------------------------------
# r03_alarm_history — V5-A-1.4 적재 (이 파일의 유일한 쓰기 함수)
#
# `V5-CM-3.1`이 이미 빈 테이블·제약(CHECK 7개, UNIQUE (lot_hist_id, parameter_id,
# recipe_step_no, policy_version))을 만들어뒀다. 여기서는 그 제약을 그대로
# 믿고 애플리케이션 쪽에서 중복을 미리 걸러내지 않는다 — 대신 DB의 UNIQUE
# 제약과 정확히 같은 컬럼 조합으로 `ON CONFLICT ... DO NOTHING`을 써서
# "이미 있으면 조용히 건너뛴다(멱등)"를 DB에 위임한다.
# ---------------------------------------------------------------------
def fetch_r03_alarm_count(connection: Connection) -> int:
    """`r03_alarm_history`에 현재 저장된 행 수를 읽는다(수용값: 3).

    readonly Connection으로도 호출할 수 있다 — 단순 COUNT라서 쓰기 권한이
    필요 없다. `insert_r03_alarms` 실행 전후로 각각 불러서 "몇 건이 새로
    생겼는지"를 눈으로 확인하는 용도로 쓸 수 있다.
    """

    query = "SELECT COUNT(*) AS total_count FROM r03_alarm_history"
    row = connection.execute(text(query)).mappings().one()
    return int(row["total_count"])


def insert_r03_alarms(connection: Connection, records: Sequence[R03AlarmRecord]) -> int:
    """`rules.build_r03_alarm_record`가 만든 행을 `r03_alarm_history`에 멱등 적재한다.

    **쓰기 함수다.** 호출자는 `app.common.db.get_db_connection()`으로 얻은
    Connection을 넘겨야 한다(readonly role은 INSERT 권한이 없다 — 시스템설계서
    §최소권한 role). 이 함수는 `connection.commit()`을 호출하지 않는다 —
    트랜잭션 경계는 호출자(service.py 또는 그 위 스크립트/테스트)가 결정한다.

    반환값은 이번 호출로 **실제로 새로 INSERT된** 행 수다. 이미 있던 행은
    `ON CONFLICT DO NOTHING`으로 조용히 건너뛰므로 카운트에 잡히지 않는다 —
    즉 같은 records로 두 번 부르면 처음엔 3(또는 N), 두 번째는 0을 반환해야
    "재실행은 no-op"이라는 완료 기준을 만족한다.
    """

    query = """
        INSERT INTO r03_alarm_history (
            alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id, chamber_id,
            parameter_id, recipe_step_no, trigger_wafer_no,
            member_wafer_refs, member_alarm_refs, policy_version
        ) VALUES (
            :alarm_id, :occurred_at, :lot_hist_id, :lot_id, :equipment_id, :chamber_id,
            :parameter_id, :recipe_step_no, :trigger_wafer_no,
            CAST(:member_wafer_refs AS jsonb),
            CAST(:member_alarm_refs AS jsonb),
            :policy_version
        )
        ON CONFLICT ON CONSTRAINT r03_alarm_history_incident_key DO NOTHING
    """

    inserted_count = 0
    for record in records:
        # member_wafer_refs·member_alarm_refs는 파이썬 list[dict]다. psycopg는
        # list[dict]를 jsonb로 자동 변환해주지 않으므로, json.dumps로 텍스트를
        # 만든 뒤 SQL에서 CAST(... AS jsonb)로 명시 변환한다.
        # ensure_ascii=False: 한글 등 비ASCII 문자가 들어가도 \uXXXX로 escape하지
        # 않고 그대로 저장한다(현재는 영문·숫자뿐이라 결과는 같지만, 값 자체를
        # 바꾸지 않는 안전한 선택이다).
        params = {
            "alarm_id": record.alarm_id,
            "occurred_at": record.occurred_at,
            "lot_hist_id": record.lot_hist_id,
            "lot_id": record.lot_id,
            "equipment_id": record.equipment_id,
            "chamber_id": record.chamber_id,
            "parameter_id": record.parameter_id,
            "recipe_step_no": record.recipe_step_no,
            "trigger_wafer_no": record.trigger_wafer_no,
            "member_wafer_refs": json.dumps(
                record.member_wafer_refs, ensure_ascii=False
            ),
            "member_alarm_refs": json.dumps(
                record.member_alarm_refs, ensure_ascii=False
            ),
            "policy_version": record.policy_version,
        }
        result = connection.execute(text(query), params)
        # INSERT ... ON CONFLICT DO NOTHING의 rowcount는, 실제로 새 행이
        # 생겼으면 1, 충돌로 건너뛰었으면 0이다. 이 합계가 곧 "이번에 새로
        # 생긴 행 수"다.
        inserted_count += result.rowcount
    return inserted_count


# ---------------------------------------------------------------------
# v_alarm_event / action_history — V5-A-1.5 incident 집계 대조용
#
# incident은 실제 알람(TRACE·SUMMARY·R03)이 있는 (lot_id, chamber_id) 조합이다
# (시스템설계서 v2.1 2.1: "TRACE·SUMMARY·R03의 lot_id·chamber_id 합집합은 12
# incident").
# `v_alarm_event`(`backend/migrations/v5/001_reference_extensions_final.sql`)는
# TRACE·SUMMARY·R03 세 source를 UNION ALL 했지만, View가 내보내는 lot_id·
# chamber_id 컬럼 자체는 source마다 출처가 다르다 — TRACE·SUMMARY 분기는 원본
# 테이블의 lot·chamber(경량 축약 컬럼, 값 형식이 lot_history.chamber_id의
# `EQP0x-PMy` 합성 표기와 같은지 실 데이터로 확인된 적이 없다)를 그대로
# alias하고, R03 분기는 r03_alarm_history.chamber_id(derive_r03_events가
# lot_history.chamber_id를 그대로 복사해 적재 — service.py의
# `chamber_id=row.chamber_id` 참고)를 쓴다. 두 값의 실제 형식이 같다는 보장
# 없이 View의 lot_id·chamber_id를 곧바로 GROUP BY 하면, 형식이 다를 경우 같은
# incident가 서로 다른 그룹으로 쪼개져 12보다 많은 incident가 나올 수 있다
# (코드 리뷰 지적).
#
# 그래서 View가 직접 내보내는 lot_id·chamber_id는 쓰지 않고, View 안에서 이미
# resolve된 lot_hist_id(TRACE·SUMMARY는 LEFT JOIN, R03는 JOIN)로 lot_history를
# 다시 조인해 그 lot_id·chamber_id만 쓴다. R03의 chamber_id도 애초에
# lot_history.chamber_id 출처이므로, 이렇게 하면 세 source가 항상 같은 한 곳
# (lot_history)의 값으로 통일되어 형식 불일치 가능성이 원천적으로 사라진다.
# lot_hist_id가 resolve되지 않은(NULL) 행이 있으면 — 기준표(§2)상 실 데이터
# 에서는 0건이어야 한다 — 조용히 집계에서 빼지 않고 즉시 예외를 낸다.
#
# `action_history`는 12건 참고 fixture다(evaluation profile `kosa_text2sql`에만
# 적재되고 runtime 2 DB(`kosa_agent`·`kosa_agent_e2e`)는 `action_history=0`
# guard 대상이라 항상 0건이다 — 시스템설계서 v2.1 2.4·2.5). 이 함수를 runtime
# DB에 대고 부르면 참고 action이 0건으로 나온다 — service.verify_incident_
# aggregation이 이 경우를 "판정 불가"로 따로 처리한다(코드 결함이 아니다).
# evaluation profile이나, `verify_detection_recalculation.py`가 원래 가정하는
# "9개 CSV를 전부 올려둔 로컬 dev DB"에 대고 불러야 1:1 대조까지 의미가 있다.
#
# `action_history`의 lot_id·chamber_id·action_code 컬럼명은 이 파일 상단
# "컬럼 근거" 문단과 같은 사정으로 03_schema_clean.sql 원문을 아직 대조하지
# 못한 최선 추정치다(lot_history가 쓰는 lot_id·chamber_id 전체 이름 관례를
# 따랐다 — trace_alarm_history·summary_alarm_history의 lot·chamber 축약
# 관례와는 다르다). V5-CM-2.4 적재 검증 결과 실제 컬럼명이 다르면 이 함수만
# 맞춰 고치면 된다(호출부는 영향 없음). action_history는 알람 union이 아니라
# 별도 테이블이라 위 lot_history 재조인 우회를 적용할 수 없다 — 이 컬럼명은
# 아직 실 DB로 검증되지 않았다(PR 미완료 항목 참고).
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class IncidentAlarmCountRow:
    """incident(=lot_id, chamber_id) 하나의 alarm union 집계 한 행."""

    lot_id: str
    chamber_id: str
    trace_count: int
    summary_count: int
    r03_count: int

    @property
    def total_count(self) -> int:
        # R03를 포함한 이 incident의 alarm 총 건수. 전체 incident에 대해
        # 합산하면 수용값 192(=189 TRACE+SUMMARY + 3 R03 각 3건 alarm)가 된다.
        return self.trace_count + self.summary_count + self.r03_count


def fetch_incident_alarm_counts(connection: Connection) -> list[IncidentAlarmCountRow]:
    """`v_alarm_event`를 `lot_history` 기준 (lot_id, chamber_id)로 묶어
    source별 alarm 건수를 센다.

    읽기 전용이다. `v_alarm_event`가 직접 내보내는 lot_id·chamber_id가 아니라
    lot_hist_id로 lot_history를 다시 조인해 그 값을 쓴다 — TRACE·SUMMARY·R03
    세 source의 chamber 표기 형식이 실제로 같은지 확인된 적이 없어서다(위
    모듈 주석 참고). 알람이 하나도 없는 (lot_id, chamber_id) 조합은 애초에
    `v_alarm_event`에 행이 없으므로 결과에도 나타나지 않는다 — "알람이 있는"
    incident만 세는 완료 기준과 그대로 맞는다.

    v_alarm_event.lot_hist_id가 resolve되지 않은(NULL) 행이 하나라도 있으면
    ValueError를 낸다 — 그런 행을 조용히 집계에서 빼면 incident 수가 실제보다
    적게 나올 수 있고, 기준표(§2)상 실 데이터에서는 이런 행이 0건이어야
    하므로 있다면 그 자체가 즉시 확인해야 할 이상 징후다.
    """

    unresolved_count = connection.execute(
        text("SELECT COUNT(*) FROM v_alarm_event WHERE lot_hist_id IS NULL")
    ).scalar_one()
    if unresolved_count:
        raise ValueError(
            "v_alarm_event에 lot_history로 resolve되지 않은(lot_hist_id "
            f"NULL) 알람이 {unresolved_count}건 있습니다 — incident 집계가 "
            "조용히 누락되는 대신 즉시 확인이 필요합니다."
        )

    query = """
        SELECT
            h.lot_id     AS lot_id,
            h.chamber_id AS chamber_id,
            COUNT(*) FILTER (WHERE v.source = 'TRACE')   AS trace_count,
            COUNT(*) FILTER (WHERE v.source = 'SUMMARY') AS summary_count,
            COUNT(*) FILTER (WHERE v.source = 'R03')     AS r03_count
        FROM v_alarm_event AS v
        JOIN lot_history AS h ON h.lot_hist_id = v.lot_hist_id
        GROUP BY h.lot_id, h.chamber_id
        ORDER BY h.lot_id, h.chamber_id
    """
    rows = connection.execute(text(query)).mappings().all()
    return [
        IncidentAlarmCountRow(
            lot_id=row["lot_id"],
            chamber_id=row["chamber_id"],
            trace_count=int(row["trace_count"]),
            summary_count=int(row["summary_count"]),
            r03_count=int(row["r03_count"]),
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class ReferenceActionRow:
    """`action_history` 참고 fixture 한 행(V5-A-1.5 1:1 대조용).

    `trigger_alarm_lot_hist_id`·`recipe_step_name`은 12행 모두 NULL이라(기준표
    §2 "검증된 물리 데이터" 하단 문단) 여기서는 읽지 않는다 — 단일 알람 FK로
    쓰지 말라는 게 원본 데이터의 의도이므로, 이 Row도 incident 매칭에 실제로
    쓰는 lot_id·chamber_id·action_code 세 컬럼만 담는다.
    """

    lot_id: str
    chamber_id: str
    action_code: str


def fetch_reference_actions(connection: Connection) -> list[ReferenceActionRow]:
    """`action_history` 참고 fixture(수용값 12건: MONITORING 5 / WARNING 4 /
    EQP_HOLD 3)를 읽는다.

    evaluation profile(`kosa_text2sql`)에만 12건이 적재된다 — runtime 2 DB는
    `action_history=0` guard 대상이라 항상 0건이 반환된다(모듈 docstring 참고).
    """

    query = """
        SELECT lot_id, chamber_id, action_code
        FROM action_history
    """
    rows = connection.execute(text(query)).mappings().all()
    return [
        ReferenceActionRow(
            lot_id=row["lot_id"],
            chamber_id=row["chamber_id"],
            action_code=row["action_code"],
        )
        for row in rows
    ]
