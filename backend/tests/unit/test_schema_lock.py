"""공용 PostgreSQL schema mutation lock 계약을 검증한다."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import bootstrap_base_schema as base_schema  # noqa: E402
import schema_lock  # noqa: E402


def test_base_schema_reexports_shared_lock_contract() -> None:
    assert (
        base_schema.BASE_SCHEMA_LOCK_NAMESPACE
        == schema_lock.POSTGRES_SCHEMA_MUTATION_LOCK_NAMESPACE
    )
    assert base_schema.DATABASE_LOCK_ID is schema_lock.DATABASE_LOCK_ID


@pytest.mark.parametrize(
    ("database", "database_id"),
    [("kosa_agent", 1), ("kosa_agent_e2e", 2), ("kosa_text2sql", 3)],
)
def test_database_specific_lock_key(database: str, database_id: int) -> None:
    assert schema_lock.advisory_lock_key(database) == (
        schema_lock.POSTGRES_SCHEMA_MUTATION_LOCK_NAMESPACE,
        database_id,
    )


def test_unknown_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="허용되지 않은"):
        schema_lock.advisory_lock_key("kosa")
