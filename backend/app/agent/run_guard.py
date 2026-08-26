"""동일 incident 중복 실행 방지 (`V5-C-1.3`).

## 무엇을 보장하나

`FR-C-09`·`FR-C-14`. 같은 `(lot_id, chamber_id)`에 대해 **활성 run은 언제나 1건**이고,
이미 `COMPLETED`된 incident는 다시 실행하지 않는다. `FAILED`는 재시도를 허용하되
새 run이 직전 실패를 가리킨다.

## 순서가 계약이다

```text
0. transaction 확인          → 실패 시 SQL·ID 생성 0회
1. C-1.1 incident 해석       → lock key를 만들려면 incident를 먼저 알아야 한다
2. advisory lock             → transaction 종료 시 자동 해제
3. history 재조회 (FOR UPDATE)
4. active → completed → failed 판정
5. 통과한 경우에만 thread ID 발급
6. C-0.1 create_agent_run()
```

**history는 반드시 lock 뒤에만 읽는다.** 앞에서 읽으면 두 caller가 같은 "0건"을 보고
둘 다 INSERT로 간다. 1번이 lock 앞인 것은 의도된 순서다 — 요청 AlarmRef가 어느
incident인지 알아야 lock key를 만들 수 있다.

5번도 순서가 뜻을 갖는다. 거부되는 요청이 thread UUID를 발급하면 checkpoint 공간에
쓰이지 않을 ID가 계속 쌓인다.

## 격리 수준 전제

정상 경로는 **`READ COMMITTED`를 전제한다.** 그 수준에서만 "lock 뒤 재조회가 앞선
caller의 commit을 본다"가 참이다 — statement마다 새 snapshot을 잡기 때문이다.

`REPEATABLE READ`·`SERIALIZABLE`에서는 transaction 첫 statement(=1번의 incident 해석)가
snapshot을 고정하므로, lock을 얻은 뒤 다시 읽어도 앞선 caller가 commit한 `RUNNING`이
**보이지 않는다.** 그러면 판정은 history 0건으로 통과하고 INSERT까지 간다. 중복 run은
여전히 막히지만 — partial unique index가 잡는다 — **경로와 오류 code가 달라진다.**

이 계층은 격리 수준을 설정하지 않는다(C-0.1·C-1.1과 같은 계약). 전제를 문서에만 두면
나중에 caller가 `REPEATABLE READ`를 켤 때 아무도 이 알고리즘을 다시 보지 않으므로,
container 회귀가 기본값을 실측하고 강한 격리에서의 대체 경로도 함께 고정한다.

## 이 계층이 만들지 않는 것

HTTP response를 만들지 않는다. 도메인 예외만 낸다. API v3의 409 body는 `V5-C-5.1`이
common exception handler를 통해 조립한다. 기존 run ID나 DB 메시지를 `details`에 넣지
않는다 — 다른 사용자의 실행 정보가 요청자에게 흐른다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy.engine import Connection

from app.agent.incident import ResolvedIncident, resolve_incident
from app.agent.repository import (
    AgentRunRow,
    CreateAgentRunCommand,
    RepositoryConflict,
    create_agent_run,
)
from app.agent.run_guard_repository import (
    IncidentRunRow,
    lock_incident,
    read_incident_runs,
    require_active_transaction,
)
from app.common.enums import ActorType, RunStatus
from app.common.exceptions import (
    IncidentAlreadyProcessedError,
    IncidentAlreadyRunningError,
)
from app.common.schemas import AlarmRef

__all__ = [
    "ACTIVE_STATUSES",
    "StartedIncidentRun",
    "new_thread_id",
    "select_retry_target",
    "start_incident_run",
]

#: **활성의 정의는 한 곳에만 둔다.**
#:
#: `002`의 `ux_agent_run_incident_active`가 쓰는 집합과 같아야 한다. 두 정의가
#: 갈라지면 DB는 막는데 정책은 통과시키거나 그 반대인 상태가 된다. 정적 회귀가
#: migration SQL과 이 상수를 대조한다.
ACTIVE_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}
)

#: C-0.1이 `RepositoryConflict`로 옮기는 partial unique 위반 code.
_ACTIVE_RUN_EXISTS: Final = "ACTIVE_RUN_EXISTS"


@dataclass(frozen=True, slots=True)
class StartedIncidentRun:
    """생성된 run과 그 근거 incident를 **함께** 돌려준다.

    C-2.1이 그래프를 조립할 때 incident를 DB에서 다시 해석하지 않게 하려는 것이다.
    두 값이 따로 다니면 caller가 다른 incident의 해석 결과를 붙일 수 있다 — C-1.2가
    `IncidentRoute` envelope로 닫은 것과 같은 종류의 위험이다.
    """

    run: AgentRunRow
    incident: ResolvedIncident
    retry_of_run_id: str | None


def new_thread_id() -> str:
    """LangGraph checkpoint thread ID.

    `checkpoint.normalize_thread_id()`가 canonical UUID 표기만 받는다. `str(uuid4())`가
    그 표기이므로 여기서 별도 가공을 하지 않는다.
    """

    return str(uuid.uuid4())


def select_retry_target(history: tuple[IncidentRunRow, ...]) -> str | None:
    """재시도가 가리킬 직전 FAILED run.

    **retry chain의 root로 평탄화하지 않는다.** 새 run은 바로 직전 실패를 가리킨다.
    root로 접으면 "몇 번째 재시도인가"는 알 수 있어도 "무엇을 이어받았는가"를 잃는다.

    history는 `started_at DESC, agent_run_id DESC`로 이미 정렬돼 온다. 그 순서의
    첫 FAILED를 고른다. `agent_run_id`는 random hex라 시간을 담지 않으므로, 시각이
    정확히 같은 두 FAILED의 실제 선후를 구분할 근거는 없다 — 그 경우 이 순서는
    **반복 결과만 고정하는 tie-break**이고 recency 판정이 아니다.
    """

    for row in history:
        if row.status is RunStatus.FAILED:
            return row.agent_run_id
    return None


def _assert_startable(history: tuple[IncidentRunRow, ...]) -> None:
    """active → completed 순으로 본다. **순서가 뜻을 갖는다.**

    completed를 먼저 보면, 재실행이 진행 중인 incident가 "이미 처리됨"으로 보고된다.
    운영자가 받는 이유가 실제 상태와 달라진다.
    """

    if any(row.status in ACTIVE_STATUSES for row in history):
        raise IncidentAlreadyRunningError()
    if any(row.status is RunStatus.COMPLETED for row in history):
        raise IncidentAlreadyProcessedError()


def start_incident_run(
    connection: Connection,
    requested_alarm: AlarmRef,
    *,
    autonomy_level: int,
    llm_model: str | None = None,
    prompt_version: str | None = None,
    thread_id_factory: Callable[[], str] = new_thread_id,
    actor_type: ActorType = ActorType.AGENT,
    actor_id: str | None = None,
) -> StartedIncidentRun:
    """요청 알람 하나로 incident를 해석하고 중복 없이 run을 만든다.

    **commit·rollback하지 않는다.** caller가 연 unit of work 안에서 동작하고 반환 뒤
    caller가 commit한다. 이 함수가 commit하면 advisory lock이 그 시점에 풀려, 반환과
    caller commit 사이에 다른 caller가 끼어들 수 있다.
    """

    # SQL도 ID 발급도 하기 전에 막는다. transaction 밖이면 advisory lock이 즉시
    # 해제되므로 "잠갔다"는 말이 거짓이 된다.
    require_active_transaction(connection)

    incident = resolve_incident(connection, requested_alarm)
    lock_incident(connection, lot_id=incident.lot_id, chamber_id=incident.chamber_id)
    # **lock 뒤 재조회.** 이 두 줄의 순서가 이 Task의 핵심이다.
    history = read_incident_runs(
        connection, lot_id=incident.lot_id, chamber_id=incident.chamber_id
    )

    _assert_startable(history)
    retry_of_run_id = select_retry_target(history)

    # 정책을 통과한 뒤에만 발급한다.
    command = CreateAgentRunCommand(
        thread_id=thread_id_factory(),
        lot_id=incident.lot_id,
        chamber_id=incident.chamber_id,
        autonomy_level=autonomy_level,
        requested_alarm=incident.requested_alarm,
        representative_alarm=incident.representative_alarm,
        member_alarms=incident.member_alarms,
        retry_of_run_id=retry_of_run_id,
        llm_model=llm_model,
        prompt_version=prompt_version,
    )

    try:
        run = create_agent_run(
            connection, command, actor_type=actor_type, actor_id=actor_id
        )
    except RepositoryConflict as exc:
        if exc.code == _ACTIVE_RUN_EXISTS:
            # **advisory lock을 쓰지 않는 writer가 있을 때만 여기 온다.**
            #
            # 두 C-1.3 caller 사이에서는 lock이 직렬화하므로 두 번째는 위 history
            # 정책에서 이미 거부된다. 이 분기는 3단 방어의 마지막 단이 발화한 경우이고,
            # 같은 사실("활성 run이 이미 있다")이므로 같은 도메인 예외로 옮긴다.
            raise IncidentAlreadyRunningError() from exc
        # 다른 conflict는 뜻이 다르다. 삼키지 않는다.
        raise

    return StartedIncidentRun(
        run=run, incident=incident, retry_of_run_id=retry_of_run_id
    )
