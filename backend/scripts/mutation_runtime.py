"""PostgreSQL bootstrap mutation runner가 공유하는 최소 안전 primitive."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from db_target import (
    BootstrapTarget,
    set_and_validate_public_search_path,
    validate_connected_identity,
)

SUPPORTED_ISOLATION_LEVELS = frozenset({"READ COMMITTED", "REPEATABLE READ"})


class MutationRuntimeError(RuntimeError):
    """공통 mutation 실행 모드·transaction 계약 오류."""


def prepare_transaction(
    connection: Any,
    target: BootstrapTarget,
    *,
    readonly: bool,
    isolation_level: str = "READ COMMITTED",
    acquire_lock: Callable[[Any, str], None],
) -> None:
    """identity/search_path/공통 advisory lock을 한 순서로 적용한다."""

    normalized = " ".join(isolation_level.strip().upper().split())
    if normalized not in SUPPORTED_ISOLATION_LEVELS:
        raise MutationRuntimeError("지원하지 않는 transaction isolation level입니다")
    # 기존 reference extension runner의 READ COMMITTED 동작을 보존한다.
    # corrected base loader만 REPEATABLE READ를 명시해 before/after를
    # 같은 snapshot에서 비교한다.
    if normalized == "READ COMMITTED":
        if readonly:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    else:
        access = "READ ONLY" if readonly else "READ WRITE"
        connection.exec_driver_sql(
            f"SET TRANSACTION ISOLATION LEVEL {normalized}, {access}"
        )
    validate_connected_identity(connection, target)
    set_and_validate_public_search_path(connection)
    acquire_lock(connection, target.database)


def resolve_exclusive_mode(
    modes: Mapping[str, bool],
    *,
    default_mode: str,
    mutually_exclusive_message: str,
) -> str:
    selected = [name for name, enabled in modes.items() if enabled]
    if len(selected) > 1:
        raise MutationRuntimeError(mutually_exclusive_message)
    return selected[0] if selected else default_mode


def require_exact_choice(value: str, choices: Sequence[str], *, label: str) -> str:
    if value not in choices:
        raise MutationRuntimeError(f"{label} 값이 허용 범위와 다릅니다")
    return value
