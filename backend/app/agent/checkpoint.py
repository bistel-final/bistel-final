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

## 요구하는 것은 `autocommit` 하나다

`from_conn_string()`은 `prepare_threshold=0`·`row_factory=dict_row`도 함께 넣는다.
그러나 `PostgresSaver`의 put·get은 **`autocommit=True`만으로 통과한다** — 격리
PostgreSQL 16에서 네 조합을 모두 실측했다.

```text
autocommit만            put=OK get=OK
+ row_factory           put=OK get=OK
+ prepare_threshold     put=OK get=OK
셋 다                    put=OK get=OK
```

그래서 계약에 넣지 않는다. 검증하지 않은 것을 요구하면 caller가 이유 없이 따라 적는다.

## pool은 다르다 — 측정으로 정정한 것

초판은 pool도 같은 이유로 autocommit이 필요하다고 적었다. **틀렸다.**
`psycopg_pool.ConnectionPool.connection()`은 `with conn:`으로 감싸 반납 시 commit하므로
non-autocommit pool에서도 checkpoint가 남는다. 격리 PostgreSQL 16 실측이다.

```text
connection autocommit=False → close → checkpoint 0건
connection autocommit=True  → close → checkpoint 1건
pool       autocommit=False → 반납 → checkpoint 1건   ← 잃지 않는다
pool       autocommit=True  → 반납 → checkpoint 1건
```

그래도 pool에 같은 계약을 요구한다. 이유는 유실이 아니라 **두 경로의 실패 모드를 같게
두기 위해서**다 — non-autocommit 연결은 saver의 연결 블록에서 예외가 나면 이미 쓴
checkpoint까지 rollback하고 autocommit이면 남는다. 지금까지 확인된 유일한 차이다.

선언(`pool.kwargs`)만 보지 않는다. `configure`는 연결 생성 시 불리므로 **한 번 꺼내
관측**하면 잡힌다. `reset`은 반납마다 불리는데 **관측으로는 잡을 수 없어** 거부한다.
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

#: `psycopg_pool.ConnectionPool`이 `reset` callback을 보관하는 속성.
#:
#: **public 접근자가 없어 private 속성을 읽는다.** 그래서 `psycopg-pool==3.3.1`을
#: `requirements.txt`와 CI 양쪽에 exact pin으로 고정했고, 이 이름이 사라지면 단위 회귀가
#: red가 된다. 이름이 바뀌었는데 조용히 `None`으로 읽혀 통과하는 상태를 막는다.
_RESET_ATTRIBUTE: Final = "_reset"


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

    `pool.kwargs`는 caller가 적어 넣은 값이다. `psycopg_pool`은 두 지점에서 그 값을
    뒤집을 수 있다.

    | callback | 언제 |
    |---|---|
    | `configure` | 연결을 **만들 때** 한 번 |
    | `reset` | 연결을 **반납할 때마다** |

    ## `configure`는 관측으로 잡고 `reset`은 거부한다

    `configure`는 연결을 만들 때 불린다. 그래서 checkout 한 번이면 그 결과를 본다.

    `reset`은 다르다. **관측으로 잡을 수 없다.** `psycopg-pool==3.3.1`의 `_putconn()`은
    `reset`이 설정돼 있으면 반납 처리를 worker task로 보낸다.

    ```python
    if self._reset:
        self.run_task(ReturnConnection(self, conn, from_getconn=from_getconn))
    ```

    즉 `with pool.connection()` 블록이 끝났다는 것은 reset이 **끝났다는 barrier가
    아니다.** 빌리고 반납한 뒤 다시 빌려 두 번 관측하는 방식을 먼저 시도했는데, 같은
    container 회귀가 6회 중 2회 실패했다 — 결정론이 아니다. 통과하는 것이 증거가
    아니라 **비결정성이 증거**다.

    그래서 `reset`이 설정된 pool은 관측 없이 거부한다. 비용을 적어 둔다 — session 상태를
    되돌리는 정당한 `reset`도 함께 막힌다. 그런 caller는 그 처리를 `configure`나 연결
    kwargs로 옮겨야 한다. 매 checkout을 검증하려면 `PostgresSaver` 주입 경계를 감싸야
    하는데 그것은 이 모듈이 소유하지 않기로 한 범위다.

    **한계를 적어 둔다.** 관측은 build 시점 한 순간이다. 그 뒤에 pool 설정을 바꾸는
    caller는 막지 못한다. 막을 수 있는 것과 없는 것을 구분해 적는다.

    관측 자체가 실패하면(닫힌 pool·대기 만료) 허용하지 않는다. 판정할 수 없는 것을
    통과시키면 fail-closed가 아니다.
    """

    kwargs = getattr(pool, "kwargs", None)
    if not isinstance(kwargs, dict) or kwargs.get("autocommit") is not True:
        raise AgentCheckpointError(
            "CHECKPOINT_POOL_CONFIG_INVALID",
            "pool kwargs에 autocommit=True가 명시돼야 합니다",
        )
    if getattr(pool, _RESET_ATTRIBUTE, None) is not None:
        # 관측 전에 거부한다 — 이 pool은 빌려 봐도 판정할 수 없다.
        raise AgentCheckpointError(
            "CHECKPOINT_POOL_RESET_UNSUPPORTED",
            "reset callback이 설정된 pool은 검증할 수 없습니다",
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
