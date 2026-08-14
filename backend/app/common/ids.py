import uuid
from typing import Annotated

from pydantic import StringConstraints, TypeAdapter

from app.common.enums import AlarmSource

# 식별자는 공백 제거 후 빈 문자열을 허용하지 않는다(설계 9.1).
# strip을 타입 자체에 포함해 모델의 ConfigDict 설정에 의존하지 않는다.
NonEmptyId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

_NON_EMPTY_ID_ADAPTER = TypeAdapter(NonEmptyId)

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


def format_alarm_ref_token(
    source: AlarmSource | str,
    alarm_id: str,
) -> str:
    """AlarmRef를 React deep-link용 source-qualified token으로 직렬화한다."""

    normalized_source = AlarmSource(source)
    normalized_alarm_id = _NON_EMPTY_ID_ADAPTER.validate_python(alarm_id)
    return f"{normalized_source.value}:{normalized_alarm_id}"


def parse_alarm_ref_token(token: str) -> tuple[AlarmSource, str]:
    """source-qualified token을 손실 없이 AlarmRef 구성값으로 복원한다."""

    normalized_token = _NON_EMPTY_ID_ADAPTER.validate_python(token)
    source_value, separator, alarm_id = normalized_token.partition(":")
    if not separator:
        raise ValueError("AlarmRef token에는 source 구분자 ':'가 필요합니다")

    source = AlarmSource(source_value)
    normalized_alarm_id = _NON_EMPTY_ID_ADAPTER.validate_python(alarm_id)
    return source, normalized_alarm_id
