"""V4-D-1.3 pool별 schema cache.

`information_schema` 를 논리 key 당 **1회만** 조회해 캐시한다.
V4-D-2.2 컬럼 allowlist 가 이 캐시를 근거로 "존재하지 않는 컬럼"을 차단한다.

왜 캐시하는가
- 질의마다 information_schema 를 읽으면 Text2SQL 응답 지연이 커진다.
  스키마는 migration 시점에만 바뀌므로 요청 단위로 다시 읽을 이유가 없다.

왜 논리 key 로 나누는가
- Runtime(kosa_agent)과 평가(kosa_text2sql)는 migration 진행도가 다를 수 있다.
  Runtime 에만 `002_agent_runtime_clean` 이 적용된 상태가 정상이다
  (manifest_v3 BOOTSTRAP_STAGE_CONTRACTS). 두 DB 를 한 캐시에 섞으면
  평가 DB 에 없는 table 을 allowlist 가 허용해버린다.
- 같은 논리 DB 라면 query/logger 계정이 달라도 스키마는 같다. 다만 logger 는
  SELECT 권한이 없어 information_schema 조회 자체가 제한되므로, 캐시는 항상
  query pool 로 채운다.

migration 차이 처리
- 두 논리 DB 의 table 집합이 달라도 오류가 아니다. 각자의 실제 상태를 그대로
  캐시하고, 차이는 `diff_tables()` 로 조회만 가능하게 한다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analytics.db_pool import (
    AnalyticsPoolFactory,
    LogicalDb,
    PoolRole,
    pool_factory,
)

#: 사용자 데이터가 있는 스키마. pg_catalog·information_schema 는 조회 대상이
#: 아니다. V4-D-2.2 가 system catalog 접근 자체를 차단하기 때문이다.
_TARGET_SCHEMA = "public"

_COLUMN_QUERY = text(
    """
    SELECT table_name, column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = :schema
    ORDER BY table_name, ordinal_position
    """
)


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool


@dataclass(frozen=True)
class SchemaSnapshot:
    """한 논리 DB 의 public 스키마 상태."""

    logical_db: LogicalDb
    tables: dict[str, tuple[ColumnInfo, ...]]

    @property
    def table_names(self) -> frozenset[str]:
        return frozenset(self.tables)

    def has_table(self, table: str) -> bool:
        return table.lower() in self.tables

    def has_column(self, table: str, column: str) -> bool:
        columns = self.tables.get(table.lower())
        if columns is None:
            return False
        return any(item.name == column.lower() for item in columns)

    def column_names(self, table: str) -> frozenset[str]:
        columns = self.tables.get(table.lower())
        if columns is None:
            return frozenset()
        return frozenset(item.name for item in columns)


class SchemaCache:
    """논리 DB 별 스키마 스냅샷을 1회 조회해 보관한다."""

    def __init__(self, factory: AnalyticsPoolFactory | None = None) -> None:
        self._factory = factory or pool_factory
        self._snapshots: dict[LogicalDb, SchemaSnapshot] = {}
        self._query_counts: dict[LogicalDb, int] = {}
        self._lock = threading.Lock()

    def get(self, logical_db: LogicalDb) -> SchemaSnapshot:
        snapshot = self._snapshots.get(logical_db)
        if snapshot is not None:
            return snapshot

        with self._lock:
            snapshot = self._snapshots.get(logical_db)
            if snapshot is not None:
                return snapshot

            snapshot = self._load(logical_db)
            self._snapshots[logical_db] = snapshot
            return snapshot

    def query_count(self, logical_db: LogicalDb) -> int:
        """information_schema 를 실제로 조회한 횟수. 캐시 검증용이다."""
        return self._query_counts.get(logical_db, 0)

    def invalidate(self, logical_db: LogicalDb | None = None) -> None:
        """migration 적용 후 호출한다. 인자가 없으면 전체를 비운다."""
        with self._lock:
            if logical_db is None:
                self._snapshots.clear()
            else:
                self._snapshots.pop(logical_db, None)

    def diff_tables(self) -> dict[str, frozenset[str]]:
        """두 논리 DB 의 table 집합 차이. migration 진행도 차이는 정상이다."""
        runtime = self.get(LogicalDb.RUNTIME).table_names
        evaluation = self.get(LogicalDb.EVALUATION).table_names

        return {
            "runtime_only": runtime - evaluation,
            "evaluation_only": evaluation - runtime,
            "shared": runtime & evaluation,
        }

    # ------------------------------------------------------------------
    def _load(self, logical_db: LogicalDb) -> SchemaSnapshot:
        # 스키마 조회는 항상 query pool 로 한다. logger 계정은 SELECT 권한이
        # 없어 information_schema 를 읽지 못한다.
        engine: Engine = self._factory.get_engine(logical_db, PoolRole.QUERY)

        grouped: dict[str, list[ColumnInfo]] = {}
        with engine.connect() as connection:
            rows = connection.execute(_COLUMN_QUERY, {"schema": _TARGET_SCHEMA})
            for table_name, column_name, data_type, is_nullable in rows:
                grouped.setdefault(table_name.lower(), []).append(
                    ColumnInfo(
                        name=column_name.lower(),
                        data_type=data_type,
                        is_nullable=(is_nullable == "YES"),
                    )
                )

        self._query_counts[logical_db] = self._query_counts.get(logical_db, 0) + 1

        return SchemaSnapshot(
            logical_db=logical_db,
            tables={table: tuple(columns) for table, columns in grouped.items()},
        )


#: 애플리케이션 전역 캐시. 테스트는 새 인스턴스로 격리한다.
schema_cache = SchemaCache()
