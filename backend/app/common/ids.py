import uuid
from typing import Annotated

from pydantic import StringConstraints

# 식별자는 공백 제거 후 빈 문자열을 허용하지 않는다(설계 10.1).
# str_min_length 를 전역 적용하면 성공 결과의 reason="" 까지 막히므로 전용 타입을 쓴다.
# strip 을 타입 자체에 포함해 모델의 str_strip_whitespace 설정에 의존하지 않는다.
NonEmptyId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

# 원본 varchar 길이를 바꾸지 않는다. prefix + hex 길이가 컬럼 상한 이내여야 한다.
AGENT_RUN_ID_PREFIX = "RUN-"
ACTION_ID_PREFIX = "ACT-"
APPROVAL_ID_PREFIX = "APR-"
TOOL_CALL_ID_PREFIX = "TOOL-"

_SHORT_HEX_LENGTH = 16
_TOOL_HEX_LENGTH = 24

AGENT_RUN_ID_MAX_LENGTH = 20
ACTION_ID_MAX_LENGTH = 20
APPROVAL_ID_MAX_LENGTH = 20
TOOL_CALL_ID_MAX_LENGTH = 29
THREAD_ID_MAX_LENGTH = 36


def _hex(length: int) -> str:
    return uuid.uuid4().hex[:length]


def new_agent_run_id() -> str:
    return f"{AGENT_RUN_ID_PREFIX}{_hex(_SHORT_HEX_LENGTH)}"


def new_action_id() -> str:
    return f"{ACTION_ID_PREFIX}{_hex(_SHORT_HEX_LENGTH)}"


def new_approval_id() -> str:
    return f"{APPROVAL_ID_PREFIX}{_hex(_SHORT_HEX_LENGTH)}"


def new_tool_call_id() -> str:
    return f"{TOOL_CALL_ID_PREFIX}{_hex(_TOOL_HEX_LENGTH)}"


def new_thread_id() -> str:
    return str(uuid.uuid4())
