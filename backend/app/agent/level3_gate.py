"""V5-C-7.1 production Level 3의 DB·실행 receipt 진입 게이트."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

E2E_DATABASE: Final = "kosa_agent_e2e"
LIVE_DATABASE: Final = "kosa_agent"
_ATTEMPT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")


def receipt_matches(
    attempt_id: str,
    *,
    evaluation_artifact_path: str | None,
) -> bool:
    """CM-5.2 immutable artifact와 같은 디렉터리의 receipt를 exact 검증한다."""

    if not _ATTEMPT_ID.fullmatch(attempt_id) or not evaluation_artifact_path:
        return False
    artifact = Path(evaluation_artifact_path)
    receipt = artifact.parent / "attempt.json"
    try:
        if receipt.is_symlink() or not receipt.is_file():
            return False
        value: Any = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("attempt") == attempt_id


def production_level3_allowed(
    *,
    autonomy_level: int,
    enabled: bool,
    database: str,
    demo_ack: str | None,
    receipt_validator: Callable[[str], bool],
) -> bool:
    """Level 3만 opt-in과 DB allowlist를 요구하며 public DB는 receipt까지 묶는다."""

    if autonomy_level != 3:
        return not enabled
    if not enabled:
        return False
    if database == E2E_DATABASE:
        return True
    if database != LIVE_DATABASE or demo_ack is None:
        return False
    return bool(_ATTEMPT_ID.fullmatch(demo_ack)) and receipt_validator(demo_ack)


__all__ = [
    "E2E_DATABASE",
    "LIVE_DATABASE",
    "production_level3_allowed",
    "receipt_matches",
]
