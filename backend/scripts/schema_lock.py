"""Shared PostgreSQL schema-mutation advisory lock contract.

Every bootstrap or migration runner that mutates the public schema must use the
same namespace and the database-specific lock id.  Keeping the values in one
module prevents base-schema repair and later migrations from racing each other.
"""

from __future__ import annotations

POSTGRES_SCHEMA_MUTATION_LOCK_NAMESPACE = 1_111_905_090  # 0x42465342 = "BFSB"

DATABASE_LOCK_ID = {
    "kosa_agent": 1,
    "kosa_agent_e2e": 2,
    "kosa_text2sql": 3,
}


def advisory_lock_key(database: str) -> tuple[int, int]:
    """Return the immutable two-part advisory-lock key for ``database``."""

    try:
        database_lock_id = DATABASE_LOCK_ID[database]
    except KeyError as exc:
        raise ValueError("허용되지 않은 schema mutation database입니다") from exc
    return POSTGRES_SCHEMA_MUTATION_LOCK_NAMESPACE, database_lock_id
