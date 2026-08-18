"""V4-D-1.3 schema cache 단위 테스트.

가짜 engine 으로 검증한다. 실제 DB 없이도 캐시 동작·논리 key 분리·migration
차이 처리를 확인할 수 있어야 한다.
"""

from __future__ import annotations

import pytest

from app.analytics.db_pool import LogicalDb, PoolRole
from app.analytics.schema_cache import SchemaCache

# (table_name, column_name, data_type, is_nullable)
RUNTIME_ROWS = [
    ("lot_history", "lot_hist_id", "character varying", "NO"),
    ("lot_history", "chamber_id", "character varying", "YES"),
    ("nl_query_log", "nl_query_log_id", "bigint", "NO"),
    ("agent_run", "agent_run_id", "character varying", "NO"),
]

# 평가 DB 에는 002_agent_runtime_clean 이 적용되지 않아 agent_run 이 없다.
EVALUATION_ROWS = [
    ("lot_history", "lot_hist_id", "character varying", "NO"),
    ("lot_history", "chamber_id", "character varying", "YES"),
    ("nl_query_log", "nl_query_log_id", "bigint", "NO"),
]


class FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeConnection:
    def __init__(self, rows: list[tuple], counter: list[int]) -> None:
        self._rows = rows
        self._counter = counter

    def execute(self, *_args, **_kwargs) -> FakeResult:
        self._counter[0] += 1
        return FakeResult(self._rows)

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_exc) -> None:
        return None


class FakeEngine:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.execute_count = [0]

    def connect(self) -> FakeConnection:
        return FakeConnection(self._rows, self.execute_count)


class FakeFactory:
    """논리 DB 별로 다른 스키마를 돌려주는 가짜 pool factory."""

    def __init__(self) -> None:
        self.engines = {
            LogicalDb.RUNTIME: FakeEngine(RUNTIME_ROWS),
            LogicalDb.EVALUATION: FakeEngine(EVALUATION_ROWS),
        }
        self.requested_roles: list[PoolRole] = []

    def get_engine(self, logical_db: LogicalDb, role: PoolRole) -> FakeEngine:
        self.requested_roles.append(role)
        return self.engines[logical_db]


@pytest.fixture()
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture()
def cache(factory: FakeFactory) -> SchemaCache:
    return SchemaCache(factory=factory)


# ---------------------------------------------------------------------------
# 1회 조회
# ---------------------------------------------------------------------------


def test_information_schema_is_queried_once_per_logical_db(
    cache: SchemaCache, factory: FakeFactory
) -> None:
    for _ in range(5):
        cache.get(LogicalDb.RUNTIME)

    assert factory.engines[LogicalDb.RUNTIME].execute_count[0] == 1
    assert cache.query_count(LogicalDb.RUNTIME) == 1


def test_repeated_get_returns_same_snapshot(cache: SchemaCache) -> None:
    first = cache.get(LogicalDb.RUNTIME)
    second = cache.get(LogicalDb.RUNTIME)

    assert first is second


def test_invalidate_forces_requery(cache: SchemaCache, factory: FakeFactory) -> None:
    """migration 적용 후 캐시를 비우면 다시 읽어야 한다."""
    cache.get(LogicalDb.RUNTIME)
    cache.invalidate(LogicalDb.RUNTIME)
    cache.get(LogicalDb.RUNTIME)

    assert factory.engines[LogicalDb.RUNTIME].execute_count[0] == 2


# ---------------------------------------------------------------------------
# 논리 key 분리
# ---------------------------------------------------------------------------


def test_logical_dbs_are_cached_separately(
    cache: SchemaCache, factory: FakeFactory
) -> None:
    cache.get(LogicalDb.RUNTIME)
    cache.get(LogicalDb.EVALUATION)

    assert factory.engines[LogicalDb.RUNTIME].execute_count[0] == 1
    assert factory.engines[LogicalDb.EVALUATION].execute_count[0] == 1


def test_invalidating_one_logical_db_keeps_the_other(
    cache: SchemaCache, factory: FakeFactory
) -> None:
    cache.get(LogicalDb.RUNTIME)
    cache.get(LogicalDb.EVALUATION)

    cache.invalidate(LogicalDb.RUNTIME)
    cache.get(LogicalDb.RUNTIME)
    cache.get(LogicalDb.EVALUATION)

    assert factory.engines[LogicalDb.RUNTIME].execute_count[0] == 2
    assert factory.engines[LogicalDb.EVALUATION].execute_count[0] == 1


def test_schema_is_loaded_through_query_pool_only(
    cache: SchemaCache, factory: FakeFactory
) -> None:
    """logger 계정은 SELECT 권한이 없으므로 스키마 조회에 쓰면 안 된다."""
    cache.get(LogicalDb.RUNTIME)

    assert factory.requested_roles == [PoolRole.QUERY]


# ---------------------------------------------------------------------------
# migration 차이
# ---------------------------------------------------------------------------


def test_migration_difference_is_not_an_error(cache: SchemaCache) -> None:
    """Runtime 에만 002 가 적용된 상태가 정상이다."""
    runtime = cache.get(LogicalDb.RUNTIME)
    evaluation = cache.get(LogicalDb.EVALUATION)

    assert runtime.has_table("agent_run") is True
    assert evaluation.has_table("agent_run") is False


def test_diff_tables_reports_migration_gap(cache: SchemaCache) -> None:
    diff = cache.diff_tables()

    assert diff["runtime_only"] == frozenset({"agent_run"})
    assert diff["evaluation_only"] == frozenset()
    assert "lot_history" in diff["shared"]


# ---------------------------------------------------------------------------
# allowlist 조회 (V4-D-2.2 가 사용한다)
# ---------------------------------------------------------------------------


def test_column_lookup_is_case_insensitive(cache: SchemaCache) -> None:
    snapshot = cache.get(LogicalDb.RUNTIME)

    assert snapshot.has_column("LOT_HISTORY", "Lot_Hist_Id") is True
    assert snapshot.has_table("Lot_History") is True


def test_unknown_column_is_rejected(cache: SchemaCache) -> None:
    """R33·R34 가 이 판정을 쓴다."""
    snapshot = cache.get(LogicalDb.RUNTIME)

    assert snapshot.has_column("lot_history", "not_a_column") is False
    assert snapshot.has_column("no_such_table", "lot_hist_id") is False


def test_column_names_returns_all_columns(cache: SchemaCache) -> None:
    snapshot = cache.get(LogicalDb.RUNTIME)

    assert snapshot.column_names("lot_history") == frozenset(
        {"lot_hist_id", "chamber_id"}
    )
    assert snapshot.column_names("no_such_table") == frozenset()


def test_nullable_flag_is_parsed(cache: SchemaCache) -> None:
    snapshot = cache.get(LogicalDb.RUNTIME)
    columns = {item.name: item for item in snapshot.tables["lot_history"]}

    assert columns["lot_hist_id"].is_nullable is False
    assert columns["chamber_id"].is_nullable is True
