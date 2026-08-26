"""중복 실행 방지의 DB 경계 (`V5-C-1.3`).

## 이 계층이 하는 일

설계 §12.1의 3단 방어 중 **앞 두 단**을 제공한다.

```text
pg_advisory_xact_lock(incident)  →  transaction 재조회  →  partial unique index
        (이 파일)                        (이 파일)          (002 migration)
```

세 번째 단은 이미 `ux_agent_run_incident_active (lot_id, chamber_id)
WHERE status IN ('RUNNING','WAITING_APPROVAL')`로 존재한다. migration을 추가하지
않는다. 다만 그 index는 **terminal row를 제외**하므로 `COMPLETED` 재실행 금지는
DB 제약이 아니라 여기의 lock·재조회가 담당한다.

## 정책은 여기 없다

이 파일은 **읽고 잠그기만** 한다. active/completed/failed 판정은 `run_guard.py`가
한다. 같은 판정이 SQL과 Python 두 벌로 갈라지면 한쪽만 고치는 순간 조용히 어긋난다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import SQLAlchemyError

from app.agent.repository import (
    AgentRepositoryError,
    _require_transaction,
    translate_db_error,
)
from app.common.enums import RunStatus

__all__ = [
    "IncidentRunRow",
    "lock_incident",
    "read_incident_runs",
    "require_active_transaction",
]


def require_active_transaction(connection: Connection) -> None:
    """활성 transaction 요구. **판정은 C-0.1에 위임한다.**

    `in_transaction()` 판정을 새로 복제하지 않는다. 두 벌이 되면 C-0.1이 조건을 바꿀 때
    이쪽만 남아 조용히 갈라진다.

    그렇다고 각 진입점이 C-0.1의 private `_require_transaction`을 **직접** 부르지도
    않는다. 그러면 새 계층 두 파일이 private seam에 각각 결합되고, C-0.1이 이름이나
    서명을 바꿀 때 고쳐야 할 자리가 흩어진다. **이 wrapper 한 곳만 private에 닿는다**
    (작업계획 §5.1-2).
    """

    _require_transaction(connection)


@dataclass(frozen=True, slots=True)
class IncidentRunRow:
    """history 한 행. **정책 판정에 필요한 최소 필드만.**

    `agent_run` 전체를 읽지 않는다. 이 계층이 `thread_id`·`requested_alarm_id`까지
    들고 오면 caller가 그것으로 다른 판단을 하기 시작한다.
    """

    agent_run_id: str
    status: RunStatus
    started_at: datetime


#: **두 ID를 구분 가능하게 직렬화한 뒤** hash한다.
#:
#: 단순 연결하면 `('AB','C')`와 `('A','BC')`가 같은 key가 된다. 서로 다른 incident가
#: 같은 lock을 공유하면 하나가 다른 하나를 막는다 — 중복을 허용하는 방향은 아니지만
#: 원인 없는 직렬화가 생긴다. `jsonb_build_array`는 각 원소를 따옴표로 감싸므로 그
#: 모호성이 없다.
#:
#: `pg_advisory_xact_lock`이다. session lock(`pg_advisory_lock`)을 쓰면 명시적
#: unlock을 놓쳤을 때 **pool로 반납된 연결에 lock이 남는다.** transaction lock은
#: commit·rollback 어느 쪽으로 끝나도 자동 해제된다.
#:
#: hash 충돌은 서로 다른 incident를 불필요하게 직렬화할 뿐, 중복 run을 만들지 않는다.
#: **두 인자에 명시 cast를 건다.** `jsonb_build_array`의 인자 타입이 `"any"`라
#: PostgreSQL이 bind parameter의 타입을 추론하지 못하고 `IndeterminateDatatype`
#: (`could not determine data type of parameter $1`)으로 실패한다. 격리 컨테이너
#: 회귀가 이것을 잡았다 — 단위 fake로는 재현되지 않는다.
_LOCK: Final = text(
    "SELECT pg_advisory_xact_lock("
    "    hashtextextended("
    "        jsonb_build_array("
    "            CAST(:lot_id AS text), CAST(:chamber_id AS text)"
    "        )::text,"
    "        0"
    "    )"
    ")"
)

#: **명시 column만 SELECT한다.** `SELECT *`를 쓰면 `agent_run.requested_alarm_id`·
#: `severity` 같은 값이 딸려 와 정책 계층이 쓰지 말아야 할 것을 볼 수 있게 된다.
#:
#: `FOR UPDATE`는 advisory lock과 **겹치는** 방어다. advisory lock을 얻지 않고 이
#: 함수만 부르는 caller가 생겨도 기존 행에 대한 경합은 남는다. 다만 history가 0건이면
#: 잠글 행이 없어 phantom을 막지 못한다 — 그것이 advisory lock이 따로 있는 이유다.
#:
#: **이 줄을 지우는 변이는 회귀에서 관측되지 않는다**(unit·container 모두 green).
#: 정상 경로에서는 advisory lock이 이미 직렬화하므로 도달하는 상태가 같기 때문이다.
#: 관측되지 않는다는 사실 자체를 남긴다 — "변이 red 확인"이라고만 적으면 거짓이다.
#:
#: 정렬은 **결정론 tie-break이지 recency 판정이 아니다.** `agent_run_id`는
#: `new_agent_run_id()`가 만드는 random hex라 시간 순서를 담지 않는다. `started_at`이
#: 같은 두 행의 실제 선후를 이 값으로 알 수 없고, 알 수 있는 척하지도 않는다.
_HISTORY: Final = text(
    """
    SELECT agent_run_id, status, started_at
    FROM agent_run
    WHERE lot_id = :lot_id AND chamber_id = :chamber_id
    ORDER BY started_at DESC, agent_run_id DESC
    FOR UPDATE
    """
)


def lock_incident(connection: Connection, *, lot_id: str, chamber_id: str) -> None:
    """incident 단위 transaction advisory lock을 얻는다.

    **transaction 안에서만 호출한다.** transaction 밖이면 lock은 얻는 즉시 해제되고
    (autocommit statement가 곧 transaction이다) 아무것도 지키지 못한다. 그 상태를
    조용히 통과시키면 "lock을 걸었다"는 사실이 거짓이 된다.

    lock wait 만료·취소는 C-0.1 `translate_db_error()`가 `55P03`·`57014`를
    `RepositoryRetryable`로 옮긴다. 이 계층이 SQLSTATE 표를 새로 만들지 않는다.
    """

    require_active_transaction(connection)
    try:
        connection.execute(_LOCK, {"lot_id": lot_id, "chamber_id": chamber_id})
    except SQLAlchemyError as exc:
        raise translate_db_error(exc) from exc


def read_incident_runs(
    connection: Connection, *, lot_id: str, chamber_id: str
) -> tuple[IncidentRunRow, ...]:
    """lock **뒤에** incident의 run history를 읽는다.

    이 함수는 자기가 lock 뒤인지 알 수 없다. 순서는 `run_guard.py`가 지키고 회귀가
    고정한다. 다만 transaction 요구는 여기서도 건다 — `FOR UPDATE`가 transaction
    밖에서는 의미가 없기 때문이다.
    """

    require_active_transaction(connection)
    try:
        rows = connection.execute(
            _HISTORY, {"lot_id": lot_id, "chamber_id": chamber_id}
        ).all()
    except SQLAlchemyError as exc:
        raise translate_db_error(exc) from exc
    return tuple(_row(row) for row in rows)


def _row(row: Row[tuple[str, str, datetime]]) -> IncidentRunRow:
    try:
        status = RunStatus(row.status)
    except ValueError as exc:
        # DB에 계약 밖 상태가 있으면 정책이 그것을 "FAILED도 active도 아닌 것"으로
        # 조용히 무시하게 된다. 그 무시가 곧 중복 run 허용이다.
        raise _unknown_status() from exc
    return IncidentRunRow(
        agent_run_id=row.agent_run_id,
        status=status,
        started_at=row.started_at,
    )


def _unknown_status() -> AgentRepositoryError:
    from app.agent.repository import RepositoryContractError

    # 값 자체를 담지 않는다 — 계약 밖 문자열이 그대로 로그·응답으로 흐를 수 있다.
    return RepositoryContractError("UNKNOWN_RUN_STATUS")
