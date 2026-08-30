"""DB driver 의존성 없이 공유하는 Tool deadline 정본."""

from typing import Final

READ_TOOL_CALLER_DEADLINE_SECONDS: Final[float] = 8.0

__all__ = ["READ_TOOL_CALLER_DEADLINE_SECONDS"]
