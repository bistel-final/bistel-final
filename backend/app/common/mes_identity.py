"""Backend adapter와 MES Mock이 공유하는 순수 MES event identity 계약."""

from __future__ import annotations

import hashlib
import re

EVENT_ID_PATTERN = re.compile(r"^MES:[0-9a-f]{64}$")
_REQUEST_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class MesIdentityError(ValueError):
    """원문 identity를 노출하지 않는 공통 계약 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def event_id_for(action_id: str, request_hash: str) -> str:
    """UTF-8/NUL 직렬화로 stable MES event identity를 만든다."""

    if not isinstance(action_id, str) or not action_id.strip():
        raise MesIdentityError("MES_ACTION_ID_INVALID")
    if (
        not isinstance(request_hash, str)
        or _REQUEST_HASH_PATTERN.fullmatch(request_hash) is None
    ):
        raise MesIdentityError("MES_REQUEST_HASH_INVALID")
    identity_raw = action_id + "\0MES_MOCK\0" + request_hash
    digest = hashlib.sha256(identity_raw.encode("utf-8")).hexdigest()
    event_id = f"MES:{digest}"
    if EVENT_ID_PATTERN.fullmatch(event_id) is None:  # pragma: no cover
        raise MesIdentityError("MES_EVENT_ID_INVALID")
    return event_id


__all__ = ["EVENT_ID_PATTERN", "MesIdentityError", "event_id_for"]
