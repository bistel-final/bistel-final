"""Detection Agent Tool 정의 (V5-A-3.2-1).

`app/knowledge/tools.py`와 같은 얇은 wrapper 계층이다 — 실제 조회·조립은
`service.FdcSummaryService`가 하고, 이 파일은 (1) readonly DB 커넥션을 열고
(2) 예외를 공통 `ok`·`reason` 계약(`app.common.tool_contracts.REASON_PREFIXES`)
으로 바꾸는 두 가지만 한다. LangGraph node·`agent_tool_call` 감사 기록은
공통 wrapper(설계서 v2.1 8절 "latency_ms와 호출 status는 Tool payload가
아니다")가 하므로 여기서 중복 기록하지 않는다.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.common.config import TOOL_DB_TIMEOUT_SEC
from app.common.db import get_readonly_engine
from app.common.tool_contracts import FdcSummaryToolResult, fail
from app.common.tool_timeouts import (
    DependencyTimeoutError,
    apply_postgres_statement_timeout,
    postgres_timeout_error,
)
from app.detection.service import FdcSummaryService

logger = logging.getLogger(__name__)


@tool
def get_fdc_summary(lot_hist_id: str) -> FdcSummaryToolResult:
    """단일 lot_hist_id의 summary·evaluation·5선·
    준비된 경우의 anomaly evidence를 반환한다.

    Fault 정답(label)과 조치 권고는 반환하지 않는다(NFR-19, 설계서 v2.1 8절).
    모델 artifact가 없어도(또는 채점에 실패해도) anomaly=None으로 성공한다 —
    실패하는 경우는 lot_hist_id 자체를 찾지 못하거나(0건 포함) DB 접근이
    안 되는 경우뿐이다.
    """

    try:
        with get_readonly_engine().connect() as connection:
            apply_postgres_statement_timeout(
                connection,
                timeout_seconds=TOOL_DB_TIMEOUT_SEC,
            )
            result = FdcSummaryService(connection).get_fdc_summary(lot_hist_id)
    except DependencyTimeoutError as exc:
        return fail(FdcSummaryToolResult, f"TIMEOUT: {exc.reason_code}")
    except TimeoutError as exc:
        return fail(FdcSummaryToolResult, f"TIMEOUT: {exc}")
    except Exception as exc:
        if timeout := postgres_timeout_error(exc):
            return fail(FdcSummaryToolResult, f"TIMEOUT: {timeout.reason_code}")
        logger.exception("get_fdc_summary Tool dependency error")
        return fail(
            FdcSummaryToolResult,
            "DEPENDENCY_ERROR: FDC summary 조회 의존성 오류",
        )

    if result is None:
        return fail(FdcSummaryToolResult, f"NOT_FOUND: lot_hist_id={lot_hist_id}")

    return result
