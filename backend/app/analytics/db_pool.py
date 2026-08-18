"""V4-D-1.2 process별 pool factory.

Runtime(kosa_agent)과 평가(kosa_text2sql)라는 두 논리 DB 에 대해,
용도별(query 실행 / log 기록) engine 을 분리 생성한다.

계정 분리가 1차 방어선이다 (backend/migrations/002_analytics_roles.sql).
- query pool: kosa_readonly — SELECT 만 가능. LLM 이 생성한 SQL 은 반드시
  이 pool 로만 실행한다.
- logger pool: kosa_query_logger — nl_query_log INSERT 만 가능. 로그 기록이
  query pool 로 흘러가거나 그 반대가 되면 안 된다.

설계 원칙
- 지연 생성: import 시점이 아니라 첫 요청 시 engine 을 만든다. 평가 DSN 이
  비어 있어도 Runtime 만 쓰는 코드는 동작해야 한다 (V4-D-7.x 전까지 평가
  DSN 은 선택이다).
- DSN 비노출: 예외·로그 어디에도 전체 DSN 이나 비밀번호를 남기지 않는다.
  진단에는 마스킹된 표현(계정·호스트·DB명)만 사용한다.
- 실패 계약: Tool 경계에서는 예외 대신 {ok, reason} 을 쓰지만, 이 모듈은
  Tool 이 아니라 하부 계층이므로 명시적 예외(PoolConfigurationError)를
  던지고 Tool 계층(V4-D-3.3)이 이를 reason 으로 변환한다.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

from app.common.config import TOOL_DB_TIMEOUT_SEC


class LogicalDb(str, Enum):
    """논리 DB. 물리 서버는 같아도 목적이 다르면 다른 논리 DB 다."""

    RUNTIME = "runtime"
    EVALUATION = "evaluation"


class PoolRole(str, Enum):
    """pool 용도. 계정과 1:1 대응한다."""

    QUERY = "query"  # kosa_readonly — LLM 생성 SQL 실행 전용
    LOGGER = "logger"  # kosa_query_logger — nl_query_log 기록 전용


#: (논리 DB, 용도) -> DSN 환경변수 이름
_DSN_ENV_NAMES: dict[tuple[LogicalDb, PoolRole], str] = {
    (LogicalDb.RUNTIME, PoolRole.QUERY): "TEXT2SQL_DATABASE_URL",
    (LogicalDb.RUNTIME, PoolRole.LOGGER): "TEXT2SQL_LOG_DATABASE_URL",
    (LogicalDb.EVALUATION, PoolRole.QUERY): "TEXT2SQL_EVAL_DATABASE_URL",
    (LogicalDb.EVALUATION, PoolRole.LOGGER): "TEXT2SQL_EVAL_LOG_DATABASE_URL",
}


#: pool 용도별로 허용되는 DB 계정.
#:
#: role 이 이름표에 그치면 1차 방어선이 무너진다. QUERY 자리에 쓰기 가능한
#: 계정을 적어두면 LLM 이 생성한 SQL 이 그대로 실행된다. DSN 오타 하나로
#: 계정 분리 설계가 무의미해지므로 engine 생성 시점에 강제한다.
_REQUIRED_USERNAME: dict[PoolRole, str] = {
    PoolRole.QUERY: "kosa_readonly",
    PoolRole.LOGGER: "kosa_query_logger",
}


class PoolConfigurationError(RuntimeError):
    """DSN 미설정·형식 오류·계정 불일치. 메시지에 비밀번호를 절대 담지 않는다."""


@dataclass(frozen=True)
class PoolInfo:
    """진단용 마스킹 정보. 비밀번호가 없다.

    host·port 까지 담는 이유는 DB 이름만으로는 동일성을 판정할 수 없기
    때문이다. 다른 서버의 같은 이름 DB 가 있을 수 있다.
    """

    logical_db: LogicalDb
    role: PoolRole
    username: str
    host: str
    port: int | None
    database: str

    def describe(self) -> str:
        port = f":{self.port}" if self.port is not None else ""
        return (
            f"{self.logical_db.value}/{self.role.value}"
            f" ({self.username}@{self.host}{port}/{self.database})"
        )


class AnalyticsPoolFactory:
    """(논리 DB × 용도) 별 engine 을 지연 생성·캐시한다.

    같은 key 로 다시 요청하면 같은 engine 을 돌려준다. engine 자체가
    connection pool 이므로 이 팩토리는 engine 을 pool 단위로 관리한다.
    """

    def __init__(self) -> None:
        self._engines: dict[tuple[LogicalDb, PoolRole], Engine] = {}
        self._lock = threading.Lock()

    def get_engine(self, logical_db: LogicalDb, role: PoolRole) -> Engine:
        key = (logical_db, role)

        engine = self._engines.get(key)
        if engine is not None:
            return engine

        with self._lock:
            engine = self._engines.get(key)
            if engine is not None:
                return engine

            engine = self._create_engine(logical_db, role)
            self._engines[key] = engine
            return engine

    def get_pool_info(self, logical_db: LogicalDb, role: PoolRole) -> PoolInfo:
        """마스킹된 접속 정보. 로그·오류 메시지에 이것만 쓴다."""
        url = self._parse_dsn(logical_db, role)
        return PoolInfo(
            logical_db=logical_db,
            role=role,
            username=url.username or "?",
            host=url.host or "?",
            port=url.port,
            database=url.database or "?",
        )

    def dispose_all(self) -> None:
        """테스트·종료 시 모든 engine 을 정리한다."""
        with self._lock:
            for engine in self._engines.values():
                engine.dispose()
            self._engines.clear()

    # ------------------------------------------------------------------
    def _parse_dsn(self, logical_db: LogicalDb, role: PoolRole):
        env_name = _DSN_ENV_NAMES[(logical_db, role)]
        raw = os.getenv(env_name, "").strip()

        if not raw:
            raise PoolConfigurationError(
                f"{env_name} 이 설정되지 않았다. "
                f"{logical_db.value}/{role.value} pool 을 만들 수 없다."
            )

        try:
            url = make_url(raw)
        except Exception as exc:
            # 원본 DSN(비밀번호 포함 가능)을 메시지에 싣지 않는다.
            raise PoolConfigurationError(
                f"{env_name} 형식이 잘못됐다. "
                "postgresql+psycopg://계정:비번@호스트:포트/DB 형태여야 한다."
            ) from exc

        if url.drivername != "postgresql+psycopg":
            raise PoolConfigurationError(
                f"{env_name} 드라이버는 postgresql+psycopg 여야 한다: {url.drivername}"
            )

        # role 과 계정을 강제 연결한다. 설정 실수로 1차 방어선이 무너지는 것을
        # 기동 시점에 막는다.
        required = _REQUIRED_USERNAME[role]
        if url.username != required:
            raise PoolConfigurationError(
                f"{env_name} 계정이 {role.value} pool 계약과 다르다: "
                f"{url.username} (기대 {required}). "
                "계정 권한이 1차 방어선이므로 다른 계정을 허용하지 않는다."
            )

        return url

    def _create_engine(self, logical_db: LogicalDb, role: PoolRole) -> Engine:
        url = self._parse_dsn(logical_db, role)

        return create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args={"connect_timeout": TOOL_DB_TIMEOUT_SEC},
            # 예외 문자열에 URL 이 섞이지 않도록 한다.
            hide_parameters=True,
        )


#: 애플리케이션 전역 팩토리. 테스트는 새 인스턴스를 만들어 격리한다.
pool_factory = AnalyticsPoolFactory()
