"""읽기 Tool 호출·감사·예산·soft deadline 경계 (`V5-C-2.1`, `V5-C-2.2`).

호출 한 번은 ``reserve commit → 외부 호출 → finalize commit`` 순서다. 동기 Tool은
실행 중인 thread를 강제 중단할 수 없으므로 여기서 보장하는 것은 caller의 대기 상한과
TIMEOUT 기록까지다. executor의 수명과 worker 회수는 주입한 쪽이 소유한다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import AbstractContextManager
from dataclasses import dataclass
from time import monotonic
from typing import Any, Final, Protocol, TypeVar

from pydantic import ValidationError
from sqlalchemy.engine import Connection

from app.agent.repository import (
    RESERVED_TOOL_OUTPUT_KEYS,
    ToolBudgetCounts,
    ToolCallRow,
    count_tool_calls_for_budget,
    finalize_tool_call,
    list_tool_calls,
    reserve_tool_call,
)
from app.agent.state import ToolBudget
from app.common.config import (
    AGENT_LEVEL3_MAX_TOOL_CALLS,
    AGENT_MAX_RETRY,
    AGENT_MAX_TOOL_CALLS,
    N8N_WEBHOOK_TIMEOUT_SEC,
)
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import (
    AGENT_TOOL_NAMES,
    REASON_PREFIXES,
    ChamberParameterHistoryToolInput,
    ChamberParameterHistoryToolResult,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
    MetrologyResultToolInput,
    MetrologyResultToolResult,
    SendActionToolInput,
    SendActionToolResult,
    ToolResult,
)

ResultT = TypeVar("ResultT", bound=ToolResult)
logger = logging.getLogger(__name__)
SEND_ACTION_BUDGET: Final = 2
SEND_ACTION_DEADLINE_GRACE_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class ToolBudgetPolicy:
    """DB에 고정된 autonomy level별 immutable Tool 예산."""

    max_calls: int
    send_budget: int = SEND_ACTION_BUDGET


def _budget_policy(autonomy_level: int) -> ToolBudgetPolicy:
    if autonomy_level in (1, 2):
        return ToolBudgetPolicy(max_calls=AGENT_MAX_TOOL_CALLS)
    if autonomy_level == 3:
        return ToolBudgetPolicy(max_calls=AGENT_LEVEL3_MAX_TOOL_CALLS)
    raise ToolBoundaryError("AUTONOMY_LEVEL_INVALID")


class ToolDeadlineExceeded(TimeoutError):
    """soft deadline 초과. 예외 문자열·URI·SQL을 담지 않는다."""


class ToolRunnerSaturated(TimeoutError):
    """deadline 안에 worker를 얻지 못해 queued 호출을 취소했다."""


class ToolBudgetBlocked(RuntimeError):
    """예약 전 예산 정책 차단. 원문 입력·DB 상세를 예외 문자열에 넣지 않는다."""

    def __init__(
        self,
        code: str,
        counts: ToolBudgetCounts,
        policy: ToolBudgetPolicy | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.counts = counts
        self.budget = _budget_snapshot(
            counts, policy or _budget_policy(counts.autonomy_level)
        )


class ToolBoundaryError(RuntimeError):
    """Tool wrapper 자체의 sanitized 구성 오류."""

    def __init__(self, code: str, *, prior_code: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.prior_code = prior_code


def _send_action_deadline_for_timeout(webhook_timeout_seconds: Any) -> float:
    """adapter timeout보다 반드시 긴 wrapper deadline을 만든다."""

    if type(webhook_timeout_seconds) is not int or webhook_timeout_seconds < 25:
        raise ToolBoundaryError("SEND_ACTION_TIMEOUT_CONFIG_INVALID")
    return float(webhook_timeout_seconds) + SEND_ACTION_DEADLINE_GRACE_SECONDS


SEND_ACTION_DEADLINE_SECONDS: Final = _send_action_deadline_for_timeout(
    N8N_WEBHOOK_TIMEOUT_SEC
)


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
        started = threading.Event()

        def invoke(value: dict[str, Any]) -> Any:
            started.set()
            return fn(value)

        future = self._executor.submit(invoke, payload)
        try:
            return future.result(timeout=seconds)
        except FuturesTimeout as exc:
            # ``concurrent.futures.TimeoutError``는 built-in ``TimeoutError``의 alias다.
            # Tool 함수 자체가 TimeoutError를 던진 경우에만 원래 예외를 돌려준다.
            # deadline 직후 정상 완료된 future도 ``done``일 수 있으므로 done 여부만으로
            # 구분하면 wrapper timeout을 Tool 예외로 오분류한다.
            if future.done() and isinstance(future.exception(), FuturesTimeout):
                raise
            if not started.is_set() and future.cancel():
                raise ToolRunnerSaturated from exc
            raise ToolDeadlineExceeded from exc


def _send_action_not_wired(_payload: dict[str, Any]) -> Any:
    raise ToolBoundaryError("SEND_ACTION_NOT_WIRED")


def _investigation_not_wired(_payload: dict[str, Any]) -> Any:
    raise ToolBoundaryError("INVESTIGATION_TOOL_NOT_WIRED")


@dataclass(frozen=True, slots=True)
class ToolBoundary:
    """StructuredTool의 ``.invoke(dict)``만 노출하는 내부 경계."""

    fdc_summary: Callable[[dict[str, Any]], Any]
    equipment_context: Callable[[dict[str, Any]], Any]
    document_search: Callable[[dict[str, Any]], Any]
    chamber_parameter_history: Callable[[dict[str, Any]], Any] = (
        _investigation_not_wired
    )
    metrology_result: Callable[[dict[str, Any]], Any] = _investigation_not_wired
    send_action: Callable[[dict[str, Any]], Any] = _send_action_not_wired
    send_action_deadline_seconds: float = SEND_ACTION_DEADLINE_SECONDS

    @classmethod
    def production(
        cls,
        *,
        settings: Any | None = None,
        transactions: TransactionFactory | None = None,
        http_post: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> ToolBoundary:
        """실 Tool을 지연 import한다.

        module import를 이 factory 호출 시점까지 늦춰 설정·DB 연결 없이 State와
        graph 모듈을 import할 수 있게 한다.
        """

        from app.agent.investigation import (
            get_chamber_parameter_history,
            get_metrology_result,
        )
        from app.detection.tools import get_fdc_summary
        from app.knowledge.tools import get_equipment_context, search_documents

        send_action = _send_action_not_wired
        send_action_deadline_seconds = SEND_ACTION_DEADLINE_SECONDS
        if settings is not None or transactions is not None:
            if settings is None or transactions is None:
                raise ToolBoundaryError("SEND_ACTION_FACTORY_INCOMPLETE")
            from app.agent.email_delivery import EmailDeliveryConfigError
            from app.agent.mes_delivery import MesDeliveryConfigError
            from app.agent.send_action import build_send_action_tool

            send_action_deadline_seconds = _send_action_deadline_for_timeout(
                getattr(settings, "N8N_WEBHOOK_TIMEOUT_SEC", None)
            )
            try:
                send_action = build_send_action_tool(
                    settings,
                    transactions,
                    **({} if http_post is None else {"http_post": http_post}),
                    **({} if clock is None else {"clock": clock}),
                )
            except (EmailDeliveryConfigError, MesDeliveryConfigError) as exc:
                raise ToolBoundaryError("SEND_ACTION_CONFIG_INVALID") from exc

        return cls(
            fdc_summary=get_fdc_summary.invoke,
            equipment_context=get_equipment_context.invoke,
            document_search=search_documents.invoke,
            chamber_parameter_history=get_chamber_parameter_history,
            metrology_result=get_metrology_result,
            send_action=send_action,
            send_action_deadline_seconds=send_action_deadline_seconds,
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
    send_action_deadline_seconds: float | None = None
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

    def send_action(
        self, agent_run_id: str, request: SendActionToolInput
    ) -> SendActionToolResult | None:
        deadline_seconds = (
            self.boundary.send_action_deadline_seconds
            if self.send_action_deadline_seconds is None
            else self.send_action_deadline_seconds
        )
        if deadline_seconds < self.boundary.send_action_deadline_seconds:
            raise ToolBoundaryError("SEND_ACTION_DEADLINE_INVALID")
        return self._invoke(
            agent_run_id=agent_run_id,
            tool_name="send_action",
            request=request.model_dump(mode="json"),
            invoke=self.boundary.send_action,
            result_type=SendActionToolResult,
            deadline_seconds=deadline_seconds,
            timeout_result=_send_action_timeout_result,
        )

    def chamber_parameter_history(
        self,
        agent_run_id: str,
        request: ChamberParameterHistoryToolInput,
        *,
        current_lot_id: str,
        incident_step_id: str,
        scope: str,
    ) -> ChamberParameterHistoryToolResult | None:
        canonical = request.model_dump(mode="json")
        return self._invoke(
            agent_run_id=agent_run_id,
            tool_name="get_chamber_parameter_history",
            request=canonical,
            invoke=self.boundary.chamber_parameter_history,
            result_type=ChamberParameterHistoryToolResult,
            invoke_payload={
                **canonical,
                "_context": {
                    "current_lot_id": current_lot_id,
                    "incident_step_id": incident_step_id,
                    "scope": scope,
                },
            },
        )

    def metrology_result(
        self,
        agent_run_id: str,
        request: MetrologyResultToolInput,
    ) -> MetrologyResultToolResult | None:
        return self._invoke(
            agent_run_id=agent_run_id,
            tool_name="get_metrology_result",
            request=request.model_dump(mode="json"),
            invoke=self.boundary.metrology_result,
            result_type=MetrologyResultToolResult,
        )

    def budget(self, agent_run_id: str) -> ToolBudget:
        """실제 예약 행의 상세 snapshot을 단일 기준으로 읽는다."""

        with self.transactions() as connection:
            return self.budget_from_connection(connection, agent_run_id)

    def history(self, agent_run_id: str) -> tuple[ToolCallRow, ...]:
        """ReAct 중복·재시도 판정용 persisted call_seq 정본."""

        with self.transactions() as connection:
            return tuple(list_tool_calls(connection, agent_run_id))

    def budget_from_connection(
        self,
        connection: Connection,
        agent_run_id: str,
    ) -> ToolBudget:
        """caller가 연 transaction 안에서 종료 시점 DB snapshot을 읽는다."""

        counts = count_tool_calls_for_budget(connection, agent_run_id)
        return _budget_snapshot(counts, _budget_policy(counts.autonomy_level))

    def _reserve_within_budget(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        request: Mapping[str, Any] | None,
    ) -> ToolCallRow:
        """예산 판정과 예약을 같은 run lock·transaction 안에서 수행한다."""

        if tool_name not in AGENT_TOOL_NAMES:
            raise ToolBoundaryError("TOOL_NAME_INVALID")
        with self.transactions() as connection:
            counts = count_tool_calls_for_budget(connection, agent_run_id)
            policy = _budget_policy(counts.autonomy_level)
            if code := _budget_block_code(counts, tool_name, policy):
                raise ToolBudgetBlocked(code, counts, policy)
            return reserve_tool_call(
                connection,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                input=request,
            )

    def _invoke(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        request: dict[str, Any],
        invoke: Callable[[dict[str, Any]], Any],
        result_type: type[ResultT],
        deadline_seconds: float | None = None,
        timeout_result: Callable[[str], ResultT] | None = None,
        invoke_payload: dict[str, Any] | None = None,
    ) -> ResultT | None:
        if self.deadline_runner is None:
            raise ToolBoundaryError("RUNNER_NOT_WIRED")

        # UoW 1. context가 성공적으로 빠져나오면 commit된 뒤에만 Tool을 부른다.
        reserved = self._reserve_within_budget(
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            request=request,
        )

        started_at = self.clock()
        status = ToolCallStatus.ERROR
        output: Mapping[str, Any] | None = None
        error_code: str | None = "TOOL_INVOCATION_ERROR"
        result: ResultT | None = None
        try:
            raw = self.deadline_runner.call(
                invoke,
                request if invoke_payload is None else invoke_payload,
                seconds=(
                    self.deadline_seconds
                    if deadline_seconds is None
                    else deadline_seconds
                ),
            )
            result = result_type.model_validate(raw)
            status, output, error_code = _classify_result(result)
        except ToolRunnerSaturated:
            result = (
                None
                if timeout_result is None
                else timeout_result("TOOL_RUNNER_SATURATED")
            )
            status = ToolCallStatus.TIMEOUT
            output = None
            error_code = "TOOL_RUNNER_SATURATED"
        except ToolDeadlineExceeded:
            result = (
                None
                if timeout_result is None
                else timeout_result("SEND_ACTION_DEADLINE")
            )
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
        latency_ms = max(0, int((self.clock() - started_at) * 1000))
        # UoW 2. 외부 호출의 성공 여부와 무관하게 예약한 행을 정확히 한 번 닫는다.
        try:
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
        except Exception:
            # finalize 오류가 Tool의 원 분류를 raw 예외로 덮지 않게 prior_code를
            # 구조화해 보존한다. C-2.2는 sentinel을 보존·계속 집계하며,
            # 시간 기반 자동 회수의 안전성은 CM-4.8에서 재평가한다.
            logger.error(
                "tool-call finalization failed (prior_code=%s)",
                error_code,
            )
            raise ToolBoundaryError(
                "TOOL_FINALIZE_FAILED",
                prior_code=error_code,
            ) from None
        return result


def _send_action_timeout_result(code: str) -> SendActionToolResult:
    return SendActionToolResult(ok=False, reason=f"TIMEOUT: {code}")


def _classify_result(
    result: ResultT,
) -> tuple[ToolCallStatus, Mapping[str, Any], str | None]:
    payload = result.model_dump(mode="json")
    if any(key in payload for key in RESERVED_TOOL_OUTPUT_KEYS):
        raise ToolBoundaryError("RESERVED_OUTPUT_KEY")
    if result.ok:
        return ToolCallStatus.SUCCESS, payload, None

    prefix = next(
        (
            candidate
            for candidate in REASON_PREFIXES
            if result.reason.startswith(candidate)
        ),
        None,
    )
    if prefix is None:
        raise ToolBoundaryError("REASON_PREFIX_INVALID")
    safe_payload = dict(payload)
    safe_payload["reason"] = prefix
    status = ToolCallStatus.TIMEOUT if prefix == "TIMEOUT:" else ToolCallStatus.ERROR
    return status, safe_payload, prefix.removesuffix(":")


def _budget_snapshot(
    counts: ToolBudgetCounts,
    policy: ToolBudgetPolicy | None = None,
) -> ToolBudget:
    """Repository 집계를 checkpoint-safe 상세 State로 바꾼다."""

    resolved = policy or _budget_policy(counts.autonomy_level)
    return ToolBudget(
        max_calls=resolved.max_calls,
        used=counts.total,
        by_tool=dict(counts.by_tool),
        send_budget=resolved.send_budget,
        send_used=counts.by_tool.get("send_action", 0),
        pending_reservations=counts.pending_reservations,
    )


def _budget_block_code(
    counts: ToolBudgetCounts,
    tool_name: str,
    policy: ToolBudgetPolicy | None = None,
) -> str | None:
    """고정 우선순위로 다음 예약의 차단 code를 결정한다."""

    resolved = policy or _budget_policy(counts.autonomy_level)
    send_used = counts.by_tool.get("send_action", 0)
    non_send_used = counts.total - send_used
    if counts.total >= resolved.max_calls:
        return "TOOL_BUDGET_EXHAUSTED"
    if tool_name == "send_action" and send_used >= resolved.send_budget:
        return "TOOL_SEND_ACTION_LIMIT"
    if tool_name != "send_action" and non_send_used >= (
        resolved.max_calls - resolved.send_budget
    ):
        return "TOOL_BUDGET_RESERVED"
    if counts.by_tool.get(tool_name, 0) >= AGENT_MAX_RETRY + 1:
        return "TOOL_RETRY_EXHAUSTED"
    return None


__all__ = [
    "AuditedToolExecutor",
    "DeadlineRunner",
    "ThreadDeadlineRunner",
    "ToolBoundary",
    "ToolBoundaryError",
    "ToolBudgetPolicy",
    "ToolBudgetBlocked",
    "ToolDeadlineExceeded",
    "ToolRunnerSaturated",
    "TransactionFactory",
    "SEND_ACTION_DEADLINE_SECONDS",
    "SEND_ACTION_DEADLINE_GRACE_SECONDS",
]
