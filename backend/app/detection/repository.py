"""Detection Repository (V5-A-1.1·V5-A-1.2).

시스템설계서 v2.1 1.3 계층 규칙: Repository는 PostgreSQL 조회만 담당한다.
HTTP 응답 조립, 업무 판정(OOS/OOC/IN, alarm 발행 등), LLM 호출을 하지 않는다.
`summarize.py`(Rules 계층)의 입력·비교 대상을 만들어 넘기기만 한다.

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

이 모듈의 함수는 전부 읽기 전용이다(base 9 table은 bootstrap이 적재하고, A는
쓰지 않는다). 호출자는 `app.common.db.get_readonly_connection()`으로 얻은
Connection을 넘긴다.

[팀원용 요약]
이 파일이 하는 일은 크게 두 갈래다.
  1) summarize.py에 넣을 "원본" 조회: fetch_trace_points, fetch_parameter_limits
     -> fdc_trace·dim_parameter를 읽어서 summarize.py가 이해하는 모양
        (TracePoint·ParameterLimit)으로 바꿔준다.
  2) 이미 있는 "정답"(reference) 조회: fetch_reference_summary,
     fetch_reference_evaluation
     -> summary_data·evaluation 테이블에는 최종 데이터에 이미 계산돼서 들어있는
        결과가 있다. 우리가 summarize.py로 새로 계산한 값이 이 정답과 같은지
        비교(대조)하는 데 쓴다. 비교 자체는 이 파일이 아니라 service.py가 한다
        — 이 파일은 "가져오기"만 하고 "맞다/틀리다" 판단은 하지 않는다.
모든 함수가 SELECT만 하고 INSERT/UPDATE는 하나도 없다. Repository 계층은 DB를
읽고 쓰는 창구일 뿐, "이 값이 OOS인지 아닌지" 같은 업무 판단은 하지 않는다
(그건 Rules 계층인 summarize.py 담당).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.common.enums import AlarmType
from app.detection.summarize import GroupKey, ParameterLimit, TracePoint

__all__ = [
    "ReferenceSummaryRow",
    "ReferenceEvaluationRow",
    "fetch_trace_points",
    "fetch_parameter_limits",
    "fetch_reference_summary",
    "fetch_reference_evaluation",
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
