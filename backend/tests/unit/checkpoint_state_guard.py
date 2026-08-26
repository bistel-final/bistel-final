"""checkpoint에 실린 값의 안전 계약 (`V5-C-0.2`).

**test 파일이 아니다.** 단위 회귀와 container 회귀가 같은 판정을 쓰도록 여기 둔다.
container에서만 쓰면 판정 자체가 실제 DB 없이는 검증되지 않고, 판정이 느슨해져도
아무 테스트가 red가 되지 않는다.

## 두 축을 본다

1. **도메인 field가 정확히 허용 집합인가** — 부분집합이 아니라 exact다. 부분집합
   비교는 field가 사라지는 변이를 놓치고, 예상 밖 key가 내부 채널로 오인돼 조용히
   빠지는 것도 막지 못한다.
2. **어떤 값에도 label·secret·DSN이 없는가** — 키와 값을 재귀로 본다.

설계 §6.2가 State/checkpoint에 label·secret을 금지한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = [
    "ALLOWED_STATE_FIELDS",
    "FORBIDDEN_TOKENS",
    "internal_channels",
    "domain_fields",
    "find_sensitive",
]

#: fixture State가 가질 수 있는 field. **exact 비교 대상이다.**
#:
#: 설계 §6.2의 전체 State는 `V5-C-2.1`이 만든다. 여기에 hypothesis·evidence·approval을
#: 미리 넣으면 후속 Task의 소유권을 침범하고, 직렬화 대상에 label이 섞일 여지가 생긴다.
ALLOWED_STATE_FIELDS = frozenset(
    {"agent_run_id", "thread_id", "pre_interrupt_count", "resume_value", "phase"}
)

#: LangGraph가 `channel_values`에 함께 싣는 내부 채널 중 node와 무관한 고정 이름.
_GRAPH_CHANNELS = frozenset({"__start__", "__end__", "__interrupt__"})

#: 값에도 키에도 나타나면 안 되는 조각. **소문자로 비교한다.**
#:
#: `postgresql`은 `postgresql://`와 `postgresql+psycopg://`를 함께 잡는다 — 앞의 것만
#: 적으면 SQLAlchemy 형식 DSN이 통과한다. `fault_code`는 `predicted_fault_code`·
#: `ground_truth_fault_code`를 함께 잡는다.
FORBIDDEN_TOKENS = (
    "fault_code",
    "ground_truth",
    "hidden_gold",
    "injected",
    "injection",
    "password",
    "api_key",
    "apikey",
    "secret",
    "postgresql",
    "dsn",
)


def internal_channels(node_names: Iterable[str]) -> frozenset[str]:
    """**제외할 이름을 전부 적는다.**

    초판은 `":" not in key`로 colon이 든 key를 모두 내부 채널로 봤다. 그러면 예상 밖의
    `foo:bar`가 조용히 검사에서 빠진다 — "새 이름이 생기면 깨진다"는 설명과 반대다.
    LangGraph는 node 이름 채널과 `branch:to:<node>`를 싣는다.
    """

    names = tuple(node_names)
    return frozenset(
        set(names) | {f"branch:to:{name}" for name in names} | set(_GRAPH_CHANNELS)
    )


def domain_fields(values: dict[str, Any], node_names: Iterable[str]) -> set[str]:
    """내부 채널을 뺀 나머지. 예상 밖 key는 **여기 남아** exact 비교에서 깨진다."""

    excluded = internal_channels(node_names)
    return {key for key in values if key not in excluded}


def find_sensitive(node: Any, path: str = "$") -> list[str]:
    """label·secret·DSN이 있는 위치를 모두 돌려준다. 첫 건에서 멈추지 않는다.

    ## 반환 문자열에 입력이 한 조각도 들어가지 않는다

    초판은 위치를 `f"{path}.{key}"`로 적었다. 값은 넣지 않았지만 **key는 넣었다.**
    그래서 민감한 것이 값이 아니라 key 자체일 때 — `{"postgresql://u:pw@h/db": ...}` —
    검사가 red가 되는 바로 그 순간에 원문이 assertion 출력과 CI 로그로 나갔다
    (구현리뷰 2차 필수 1).

    이제 위치는 **안정적인 index로만** 적는다. `$.key[3]`은 3번째 항목의 키,
    `$.value[3]`은 그 값이다. 반환 문자열은 우리가 정의한 상수(경로 뼈대와 token
    이름)로만 이뤄지므로 입력이 무엇이든 새지 않는다.
    """

    found: list[str] = []
    if isinstance(node, dict):
        for index, (key, item) in enumerate(node.items()):
            found.extend(_hits(str(key), f"{path}.key[{index}]"))
            found.extend(find_sensitive(item, f"{path}.value[{index}]"))
    elif isinstance(node, list | tuple | set | frozenset):
        for index, item in enumerate(node):
            found.extend(find_sensitive(item, f"{path}[{index}]"))
    else:
        found.extend(_hits(str(node), path))
    return found


def _hits(text: str, path: str) -> list[str]:
    lowered = text.lower()
    return [f"{path}: {token}" for token in FORBIDDEN_TOKENS if token in lowered]
