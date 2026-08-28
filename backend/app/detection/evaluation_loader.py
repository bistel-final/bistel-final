"""합성 라벨(fault_code) 평가 전용 loader (V5-A-2.3).

시스템설계서 v2.1 2.6 "공개 합성 라벨 격리": fault_code는 Generator가 넣은
공개 합성 라벨이며 production ground truth가 아니다. 이 모듈이 그 fault_code를
읽는 **유일한** 곳이다 — Runtime repository(`app/detection/repository.py`)와
물리적으로 다른 파일이며, DB 연결도 `app.common.db.get_readonly_engine()`이
아니라 `get_evaluation_engine()`(kosa_evaluation role, kosa_text2sql DB)만
쓴다. kosa_readonly는 kosa_text2sql에 붙어도 lot_history.fault_code column
자체에 권한이 없다(V5-CM-3.5 role matrix) — 그래서 "잘못된 engine을 실수로
넘기면 DB가 막는다"는 것이 1차 방어선이고, 이 모듈이 "필요한 column만
SELECT한다"는 게 2차 방어선이다.

## 이 모듈을 절대 import하면 안 되는 곳

- `app/detection/model.py`·`model_artifact.py` (feature·threshold 계산 —
  score는 fault_code를 절대 보지 않는다는 NFR-19 계약)
- `app/detection/service.py`의 `FdcSummaryService` 경로, `tools.py`,
  `router.py`, `schemas.py` (Tool·API 응답에 Fault 정답을 절대 노출하지 않는다)
- `app/agent/*` 전체 (Tool·State·checkpoint·prompt·ActionPolicy)

이 계약은 `tests/contract/test_detection_label_isolation.py`의 import
allowlist 테스트가 정적으로 고정한다 — 이 모듈을 새로 import하는 파일이
생기면 그 테스트가 먼저 깨진다.

## 순서 계약 (설계서 4.5, 14.4)

"prediction을 먼저 고정한 뒤에만 label을 읽는다"("label read가 먼저 발생하면
평가를 실패시킨다"). 이 모듈 자체는 그 순서를 강제하지 않는다 — Connection
하나 던져주면 그냥 그대로 SELECT해서 돌려주는 얇은 조회 계층이기 때문이다
(repository.py와 같은 계층 원칙). 순서 강제는 호출자인
`app/detection/evaluation.py`(V5-A-2.4)의 `run_holdout_evaluation()`이
담당한다 — 그 함수는 예측을 `freeze_predictions()`로 고정한 *뒤에만* 이
모듈의 함수를 호출하도록 만들어져 있고, 그 순서 자체는
`tests/unit/test_detection_evaluation.py`가 fake I/O로 검증한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

__all__ = [
    "SyntheticFaultLabelRow",
    "fetch_synthetic_fault_labels",
    "MetrologyOutcomeRow",
    "fetch_metrology_outcomes",
]


@dataclass(frozen=True, slots=True)
class SyntheticFaultLabelRow:
    """`lot_history` 한 행의 합성 Fault 라벨.

    **평가 전용**이며 어떤 Runtime dataclass와도 타입을 공유하지 않는다 —
    예를 들어 `repository.WaferLotContextRow`와 필드가 일부 겹쳐 보여도
    별도 타입이다. 일부러 그렇게 만들었다: 두 타입이 같았다면 실수로
    `SyntheticFaultLabelRow`를 Runtime 경로(Tool 응답 조립 등)에 그대로
    넘겨도 타입 검사가 이를 못 잡는다. 타입 자체를 분리해 두면 그런 실수는
    타입 하나만 비교해도(또는 mypy로도) 바로 드러난다.
    """

    lot_hist_id: str
    lot_id: str
    fault_code: str


def fetch_synthetic_fault_labels(
    connection: Connection, lot_hist_ids: Sequence[str]
) -> list[SyntheticFaultLabelRow]:
    """지정한 `lot_hist_id`들의 공개 합성 `fault_code`를 읽는다.

    호출자는 반드시 `app.common.db.get_evaluation_engine()`으로 연 connection만
    넘겨야 한다(kosa_evaluation role). 다른 connection을 넘기면 이 함수
    자체는 그것을 막을 수 없다 — role 강제는 DB 권한(V5-CM-3.5)이 1차
    방어선이고, 이 함수는 "필요한 컬럼만 SELECT한다"는 2차 방어선만
    담당한다(모듈 docstring 참고).

    `lot_hist_ids`가 비어 있으면 쿼리를 실행하지 않고 빈 목록을 반환한다 —
    빈 `ANY(:lot_hist_ids)`도 SQL 자체는 유효하지만, "평가 대상이 0건"이라는
    호출자의 의도를 DB 왕복 없이 그대로 돌려주는 편이 더 명확하다.
    """

    if not lot_hist_ids:
        return []

    query = text(
        """
        SELECT lot_hist_id, lot_id, fault_code
        FROM lot_history
        WHERE lot_hist_id = ANY(:lot_hist_ids)
        """
    )
    rows = (
        connection.execute(query, {"lot_hist_ids": list(lot_hist_ids)})
        .mappings()
        .all()
    )
    return [
        SyntheticFaultLabelRow(
            lot_hist_id=row["lot_hist_id"],
            lot_id=row["lot_id"],
            fault_code=row["fault_code"],
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class MetrologyOutcomeRow:
    """metrology 표본 한 행의 PASS/FAIL 결과.

    기준표: metrology는 48/600 lot_history 표본에만 존재한다(PASS 39 /
    FAIL 9) — 나머지 552개 `lot_hist_id`는 이 함수의 반환 목록에 아예
    나타나지 않는다. 이 48/600이라는 coverage 자체가 평가 artifact에
    반드시 기록해야 하는 값이다(설계서 v2.1 4.5, 14.2).
    """

    lot_hist_id: str
    alarm_result: str


def fetch_metrology_outcomes(
    connection: Connection, lot_hist_ids: Sequence[str]
) -> list[MetrologyOutcomeRow]:
    """`metrology.alarm_result`를 읽는다.

    `fetch_synthetic_fault_labels`와 마찬가지로 evaluation engine 전용이다.
    metrology는 fault_code처럼 라벨 자체는 아니지만, 이 값도 Generator가
    주입한 결과와 연결된 합성 데이터라 같은 evaluation-only 경계 안에
    둔다(design 4.5: "metrology 기반 detection precision·recall을 계산할
    수 있지만" — 즉 평가 전용 용도로만 쓴다는 뜻이다).
    """

    if not lot_hist_ids:
        return []

    query = text(
        """
        SELECT lot_hist_id, alarm_result
        FROM metrology
        WHERE lot_hist_id = ANY(:lot_hist_ids)
        """
    )
    rows = (
        connection.execute(query, {"lot_hist_ids": list(lot_hist_ids)})
        .mappings()
        .all()
    )
    return [
        MetrologyOutcomeRow(
            lot_hist_id=row["lot_hist_id"], alarm_result=row["alarm_result"]
        )
        for row in rows
    ]
