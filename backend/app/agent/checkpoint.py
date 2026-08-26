"""thread·checkpoint Runtime 계약 (`V5-C-0.2`).

`V5-CM-3.4`가 공용 Runtime 두 DB에 준비한 LangGraph checkpoint 4 table을 C 런타임이
**소비**하기 위한 최소 경계다.

## 이 모듈이 소유하지 않는 것

**schema를 소유하지 않는다.** `PostgresSaver.setup()`을 부르지 않는다. 시스템설계
§12.2가 "앱 시작 시 DDL을 자동 수행하지 않는다"고 정했고, migration·marker는 CM-3.4
one-shot이 가진다. 여기서 setup을 부르면 그 one-shot 계약이 무의미해진다.

**연결을 소유하지 않는다.** connection·pool을 만들지 않고 DSN을 읽지 않는다. caller가
연 것을 받아 쓰고, saver는 그 객체보다 오래 살지 않는다.

**Agent State를 소유하지 않는다.** 설계 §6.2의 전체 State와 node graph는 `V5-C-2.1`,
승인 재개는 `V5-C-3.3`이 만든다. 여기서는 thread key와 saver 주입까지다.

## `autocommit=True`가 계약인 이유

`PostgresSaver`는 쓰기에서 `conn.pipeline()`(미지원 시 `conn.transaction()`)을 쓴다.
그래서 autocommit이 아닌 연결에서는 checkpoint가 열린 transaction 안에 머물고,
**연결을 닫으면 사라진다.** 격리 PostgreSQL 16 실측이다.

```text
autocommit=False → 연결을 닫고 재연결 → checkpoint 0건
autocommit=True  → 연결을 닫고 재연결 → checkpoint 1건
```

interrupt 뒤 프로세스가 끝나고 다른 연결에서 재개하는 것이 이 Task의 존재 이유다. 그
성질이 성립하지 않는 연결은 **saver를 만들기 전에** 거부한다. caller가 `commit()`을
기억해야 하는 계약으로 두면 잊은 자리에서 조용히 유실된다.

pool은 선언(`pool.kwargs`)만으로 판정하지 않는다. `configure` callback이 checkout
직전에 그 값을 뒤집을 수 있으므로 **실제로 한 번 꺼내 관측한다.**
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from app.common.ids import new_thread_id

__all__ = [
    "AgentCheckpointError",
    "THREAD_ID_LENGTH",
    "new_thread_id",
    "normalize_thread_id",
    "build_thread_config",
    "build_postgres_saver",
]

#: `agent_run.thread_id`는 `varchar(36)`이고 canonical UUID 문자열이 정확히 36자다.
THREAD_ID_LENGTH: Final = 36


class AgentCheckpointError(RuntimeError):
    """checkpoint 경계의 sanitized 오류.

    `reason_code`는 상위가 분기할 수 있는 **안정 문자열**이다. driver 메시지·SQL·DSN을
    public 문자열에 넣지 않는다. 원인은 exception chaining으로만 보존한다.

    CM-3.4 admin script의 동명 오류를 import하지 않는다. 그쪽은 관리 명령의 계약이고
    이쪽은 런타임 소비 계약이다. 한 이름을 쓰면 어느 계약이 깨졌는지 구분되지 않는다.
    """

    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code


def normalize_thread_id(thread_id: object) -> str:
    """canonical UUID 문자열로 정규화한다. 아니면 거부한다.

    ## parser 통과로 끝내지 않는 이유

    `uuid.UUID()`는 중괄호·`urn:uuid:` 접두사·대문자·하이픈 없는 32자를 전부 받아들인다.
    그 표기들이 **같은 UUID인데 서로 다른 문자열**이므로, 그대로 checkpoint key로 쓰면
    같은 thread가 표기 차이로 갈라진다. DB의 `thread_id`는 문자열 비교다.

    그래서 parse한 값을 다시 canonical 문자열로 만들고 **입력과 같을 때만** 통과시킨다.
    """

    if not isinstance(thread_id, str):
        raise AgentCheckpointError(
            "INVALID_THREAD_ID", "thread id는 문자열이어야 합니다"
        )
    candidate = thread_id.strip()
    if not candidate:
        raise AgentCheckpointError("INVALID_THREAD_ID", "thread id가 비어 있습니다")
    try:
        parsed = uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError) as exc:
        raise AgentCheckpointError(
            "INVALID_THREAD_ID", "thread id가 UUID 형식이 아닙니다"
        ) from exc
    if str(parsed) != candidate:
        # 대문자·중괄호·`urn:uuid:`·하이픈 없는 표기를 조용히 받아들이지 않는다.
        raise AgentCheckpointError(
            "INVALID_THREAD_ID", "thread id가 canonical UUID 표기가 아닙니다"
        )
    return candidate


def build_thread_config(thread_id: str) -> dict[str, Any]:
    """LangGraph `RunnableConfig`를 **한 곳에서** 만든다.

    checkpoint identity key는 `configurable.thread_id` 하나다. `agent_run_id`는 업무
    식별자이지 checkpoint key가 아니므로 여기 들어올 수 없다 — `RUN-...`은 UUID가 아니라
    `normalize_thread_id()`에서 걸린다.

    `checkpoint_id`를 caller가 조립하지 않는다. 재개는 첫 실행과 **같은 base config**를
    쓰고 어느 checkpoint로 돌아갈지는 saver가 정한다.
    """

    return {"configurable": {"thread_id": normalize_thread_id(thread_id)}}


def _assert_pool_durable(pool: Any) -> None:
    """pool은 **선언과 관측을 모두** 본다.

    ## 선언만으로는 부족하다

    `pool.kwargs`는 caller가 적어 넣은 값이다. `psycopg_pool`은 checkout 직전에
    `configure` callback을 부르고, 그 callback이 `connection.autocommit = False`로
    되돌릴 수 있다. 그러면 **선언은 참인데 실제 연결은 거짓**인 pool이 만들어지고,
    checkpoint는 연결이 반납될 때 조용히 사라진다. 계획리뷰 2차가 이 경우를 재현했다.

    ## 그래서 한 번 꺼내 본다

    초판은 "checkout하면 수명주기 소유권이 흐려진다"는 이유로 선언만 봤다. 그 판단을
    되돌린다 — 여기서 빌린 연결은 즉시 반납하므로 소유권을 가져오지 않고, 반대로
    관측을 생략하면 **guard가 막겠다고 선언한 바로 그 상태**를 통과시킨다.

    관측 자체가 실패하면(닫힌 pool·대기 만료) 허용하지 않는다. 판정할 수 없는 것을
    통과시키면 fail-closed가 아니다.
    """

    kwargs = getattr(pool, "kwargs", None)
    if not isinstance(kwargs, dict) or kwargs.get("autocommit") is not True:
        raise AgentCheckpointError(
            "CHECKPOINT_POOL_CONFIG_INVALID",
            "pool kwargs에 autocommit=True가 명시돼야 합니다",
        )
    try:
        with pool.connection() as connection:
            observed = getattr(connection, "autocommit", None)
    except AgentCheckpointError:
        raise
    except Exception as exc:
        # 원인은 chaining으로만 보존한다. DSN·host가 public 문자열에 들어가지 않는다.
        raise AgentCheckpointError(
            "CHECKPOINT_POOL_UNOBSERVABLE",
            "pool에서 연결을 확인할 수 없습니다",
        ) from exc
    if observed is not True:
        raise AgentCheckpointError(
            "CHECKPOINT_POOL_NOT_DURABLE",
            "pool이 내주는 연결이 autocommit이 아닙니다",
        )


def _assert_durable(conn_or_pool: Any) -> None:
    """checkpoint가 연결 종료 뒤에도 남는 구성인지 **saver를 만들기 전에** 본다."""

    import psycopg
    import psycopg_pool

    if isinstance(conn_or_pool, psycopg.Connection):
        if conn_or_pool.autocommit is not True:
            raise AgentCheckpointError(
                "CHECKPOINT_AUTOCOMMIT_REQUIRED",
                "checkpoint 연결은 autocommit이어야 합니다",
            )
        return
    if isinstance(conn_or_pool, psycopg_pool.ConnectionPool):
        _assert_pool_durable(conn_or_pool)
        return
    raise AgentCheckpointError(
        "CHECKPOINT_CONNECTION_INVALID",
        "psycopg Connection 또는 ConnectionPool이어야 합니다",
    )


def build_postgres_saver(conn_or_pool: Any) -> Any:
    """주입받은 connection/pool을 감싼 `PostgresSaver`를 만든다.

    **`setup()`을 부르지 않는다.** 그리고 connection·pool·engine을 만들지 않는다 — 이
    함수가 반환한 saver는 주입 객체보다 오래 살면 안 되고, 그 수명은 caller가 소유한다.

    `from_conn_string()`을 쓰지 않는 이유도 같다. 그것은 context manager라 블록을 벗어난
    saver를 돌려주면 이미 닫힌 연결을 들고 있게 된다.
    """

    _assert_durable(conn_or_pool)
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - 설치 계약 위반
        raise AgentCheckpointError(
            "CHECKPOINT_PACKAGE_MISSING", "checkpoint package를 불러올 수 없습니다"
        ) from exc
    return PostgresSaver(conn_or_pool)
