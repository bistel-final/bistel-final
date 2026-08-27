"""읽기 Tool 호출·감사·soft deadline 경계 (`V5-C-2.1`).

호출 한 번은 ``reserve commit → 외부 호출 → finalize commit`` 순서다. 동기 Tool은
실행 중인 thread를 강제 중단할 수 없으므로 여기서 보장하는 것은 caller의 대기 상한과
TIMEOUT 기록까지다. executor의 수명과 worker 회수는 주입한 쪽이 소유한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import AbstractContextManager
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, TypeVar

from pydantic import ValidationError
from sqlalchemy.engine import Connection

from app.agent.repository import (
    RESERVED_TOOL_OUTPUT_KEYS,
    count_tool_calls,
    finalize_tool_call,
    reserve_tool_call,
)
from app.agent.state import ToolBudget
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import (
    REASON_PREFIXES,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
    ToolResult,
)

ResultT = TypeVar("ResultT", bound=ToolResult)


class ToolDeadlineExceeded(TimeoutError):
    """soft deadline 초과. 예외 문자열·URI·SQL을 담지 않는다."""


class ToolBoundaryError(RuntimeError):
    """Tool wrapper 자체의 sanitized 구성 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DeadlineRunner(Protocol):
    """외부 소유 executor에서 caller 대기만 제한하는 실행 경계."""

    def call(
        self,
        fn: Callable[[dict[str, Any]], Any],
        payload: dict[str, Any],
        *,
        seconds: float,
    ) -> Any: ...


class ThreadDeadlineRunner:
    """주입받은 executor를 사용하며 생성·종료하지 않는다."""

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def call(
        self,
        fn: Callable[[dict[str, Any]], Any],
        payload: dict[str, Any],
        *,
        seconds: float,
    ) -> Any:
        if seconds <= 0:
            raise ValueError("deadline seconds는 0보다 커야 합니다")
        future = self._executor.submit(fn, payload)
        try:
            return future.result(timeout=seconds)
        except FuturesTimeout as exc:
            # ``concurrent.futures.TimeoutError``는 built-in ``TimeoutError``의 alias다.
            # Tool 함수 자체가 TimeoutError를 던진 경우에만 원래 예외를 돌려준다.
            # deadline 직후 정상 완료된 future도 ``done``일 수 있으므로 done 여부만으로
            # 구분하면 wrapper timeout을 Tool 예외로 오분류한다.
            if future.done() and isinstance(future.exception(), FuturesTimeout):
                raise
            # 실행 전이면 취소되고, 이미 실행 중이면 best effort로 끝난다.
            future.cancel()
            raise ToolDeadlineExceeded from exc


@dataclass(frozen=True, slots=True)
class ToolBoundary:
    """세 StructuredTool의 ``.invoke(dict)``만 노출하는 내부 경계."""

    fdc_summary: Callable[[dict[str, Any]], Any]
    equipment_context: Callable[[dict[str, Any]], Any]
    document_search: Callable[[dict[str, Any]], Any]

    @classmethod
    def production(cls) -> ToolBoundary:
        """실 Tool을 지연 import한다.

        module import를 이 factory 호출 시점까지 늦춰 설정·DB 연결 없이 State와
        graph 모듈을 import할 수 있게 한다.
        """

        from app.detection.tools import get_fdc_summary
        from app.knowledge.tools import get_equipment_context, search_documents

        return cls(
            fdc_summary=get_fdc_summary.invoke,
            equipment_context=get_equipment_context.invoke,
            document_search=search_documents.invoke,
        )


class TransactionFactory(Protocol):
    """호출마다 새 짧은 transaction scope를 돌려주는 factory."""

    def __call__(self) -> AbstractContextManager[Connection]: ...


@dataclass(frozen=True, slots=True)
class AuditedToolExecutor:
    """Tool 결과와 ``agent_tool_call`` 한 행을 같은 분류로 닫는다."""

    transactions: TransactionFactory
    boundary: ToolBoundary
    deadline_runner: DeadlineRunner | None
    deadline_seconds: float = 8.0
    clock: Callable[[], float] = monotonic

    def fdc_summary(
        self, agent_run_id: str, request: FdcSummaryToolInput
    ) -> FdcSummaryToolResult | None:
        return self._invoke(
            agent_run_id=agent_run_id,
            tool_name="get_fdc_summary",
            request=request.model_dump(mode="json"),
            invoke=self.boundary.fdc_summary,
            result_type=FdcSummaryToolResult,
        )

    def equipment_context(
        self, agent_run_id: str, request: EquipmentContextToolInput
    ) -> EquipmentContextToolResult | None:
        return self._invoke(
            agent_run_id=agent_run_id,
            tool_name="get_equipment_context",
            request=request.model_dump(mode="json"),
            invoke=self.boundary.equipment_context,
            result_type=EquipmentContextToolResult,
        )

    def document_search(
        self, agent_run_id: str, request: DocumentSearchToolInput
    ) -> DocumentSearchToolResult | None:
        return self._invoke(
            agent_run_id=agent_run_id,
            tool_name="search_documents",
            request=request.model_dump(mode="json"),
            invoke=self.boundary.document_search,
            result_type=DocumentSearchToolResult,
        )

    def budget(self, agent_run_id: str) -> ToolBudget:
        """실제 예약 행 수를 단일 기준으로 읽는다."""

        with self.transactions() as connection:
            used = count_tool_calls(connection, agent_run_id)
        return ToolBudget(used=used)

    def _invoke(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        request: dict[str, Any],
        invoke: Callable[[dict[str, Any]], Any],
        result_type: type[ResultT],
    ) -> ResultT | None:
        if self.deadline_runner is None:
            raise ToolBoundaryError("RUNNER_NOT_WIRED")

        # UoW 1. context가 성공적으로 빠져나오면 commit된 뒤에만 Tool을 부른다.
        with self.transactions() as connection:
            reserved = reserve_tool_call(
                connection,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                input=request,
            )

        started_at = self.clock()
        status = ToolCallStatus.ERROR
        output: Mapping[str, Any] | None = None
        error_code: str | None = "TOOL_INVOCATION_ERROR"
        result: ResultT | None = None
        try:
            raw = self.deadline_runner.call(
                invoke,
                request,
                seconds=self.deadline_seconds,
            )
            result = result_type.model_validate(raw)
            status, output, error_code = _classify_result(result)
        except ToolDeadlineExceeded:
            result = None
            status = ToolCallStatus.TIMEOUT
            output = None
            error_code = "TOOL_DEADLINE_EXCEEDED"
        except ToolBoundaryError as exc:
            result = None
            status = ToolCallStatus.ERROR
            output = None
            error_code = exc.code
        except ValidationError:
            result = None
            status = ToolCallStatus.ERROR
            output = None
            error_code = "TOOL_RESULT_INVALID"
        except Exception:
            # 예외 문자열을 읽거나 저장하지 않는다.
            result = None
            status = ToolCallStatus.ERROR
            output = None
            error_code = "TOOL_INVOCATION_ERROR"
        finally:
            latency_ms = max(0, int((self.clock() - started_at) * 1000))
            # UoW 2. 외부 호출의 성공 여부와 무관하게 예약한 행을 정확히 한 번 닫는다.
            with self.transactions() as connection:
                finalize_tool_call(
                    connection,
                    tool_call_id=reserved.tool_call_id,
                    agent_run_id=agent_run_id,
                    status=status,
                    latency_ms=latency_ms,
                    output=output,
                    error_msg=error_code,
                )
        return result


def _classify_result(
    result: ResultT,
) -> tuple[ToolCallStatus, Mapping[str, Any], str | None]:
    payload = result.model_dump(mode="json")
    if any(key in payload for key in RESERVED_TOOL_OUTPUT_KEYS):
        raise ToolBoundaryError("RESERVED_OUTPUT_KEY")
    if result.ok:
        return ToolCallStatus.SUCCESS, payload, None

    prefix = next(
        candidate
        for candidate in REASON_PREFIXES
        if result.reason.startswith(candidate)
    )
    safe_payload = dict(payload)
    safe_payload["reason"] = prefix
    status = ToolCallStatus.TIMEOUT if prefix == "TIMEOUT:" else ToolCallStatus.ERROR
    return status, safe_payload, prefix.removesuffix(":")


__all__ = [
    "AuditedToolExecutor",
    "DeadlineRunner",
    "ThreadDeadlineRunner",
    "ToolBoundary",
    "ToolBoundaryError",
    "ToolDeadlineExceeded",
    "TransactionFactory",
]
