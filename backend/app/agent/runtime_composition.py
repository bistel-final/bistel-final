"""Public Agent API의 지연 production composition과 실행 orchestration.

모듈 import·FastAPI startup은 원격 연결을 열지 않는다. 첫 실행 요청의 preflight가
checkpointer pool과 설정을 확인하고, 그 뒤 같은 graph·pool·executor를 lifespan 동안
재사용한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

from app.agent import action_store, ask, decision, hypothesis
from app.agent.approval_store import (
    approval_email_port,
    cancel_mes_port,
    hitl_decision_port,
    resume_after_approval,
)
from app.agent.checkpoint import build_postgres_saver, build_thread_config
from app.agent.email_delivery import production_ports as email_production_ports
from app.agent.graph import (
    AgentGraphDependencies,
    AgentGraphInputError,
    build_agent_graph,
)
from app.agent.mes_delivery import production_ports as mes_production_ports
from app.agent.public_read_model import to_public_approval
from app.agent.public_schemas import PublicApprovalItem
from app.agent.repository import (
    AgentRepositoryError,
    RepositoryConflict,
    RepositoryContractError,
    RepositoryNotFound,
    RepositoryRetryable,
    RepositoryUnavailable,
    decide_approval,
    finish_agent_run_with_active_latency,
    get_agent_run,
    get_agent_run_by_thread_exact,
    get_approval_public,
    merge_run_action_provenance,
)
from app.agent.routing import GraphBoundary
from app.agent.schemas import ApprovalDecisionRequest
from app.agent.state import AgentNodePorts
from app.agent.tools import AuditedToolExecutor, ThreadDeadlineRunner, ToolBoundary
from app.common import config as settings
from app.common import llm
from app.common.boundary_adapters import to_internal_decision
from app.common.config import AGENT_AUTONOMY_LEVEL
from app.common.db import create_app_postgres_url, get_app_engine
from app.common.enums import RunStatus
from app.common.exceptions import (
    AppError,
    ApprovalAlreadyDecidedError,
    DependencyNotReadyError,
    InvalidRequestError,
    NotFoundError,
)
from app.common.ids import new_thread_id
from app.common.schemas import AlarmRef
from app.common.tool_deadlines import READ_TOOL_CALLER_DEADLINE_SECONDS

logger = logging.getLogger(__name__)


class AgentRuntimeError(RuntimeError):
    """원문 driver·DSN을 노출하지 않는 composition 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RuntimeFactory(Protocol):
    def __call__(self, llm_model: str) -> RuntimeResources: ...


class AskServiceFactory(Protocol):
    def __call__(self) -> ask.AgentAskService: ...


@dataclass(frozen=True, slots=True)
class StartedPublicRun:
    agent_run_id: str
    alarm: AlarmRef
    thread_id: str


@dataclass(frozen=True, slots=True)
class DecidedPublicApproval:
    item: PublicApprovalItem
    thread_id: str
    agent_run_id: str


@dataclass(slots=True)
class RuntimeResources:
    graph: Any
    transactions: Any
    resume_connections: Any
    checkpoint_pool: Any
    deadline_executor: ThreadPoolExecutor
    llm_model: str

    def close(self) -> None:
        """worker 제출을 먼저 막고 checkpoint pool을 닫는다."""

        try:
            self.deadline_executor.shutdown(wait=False, cancel_futures=True)
        finally:
            self.checkpoint_pool.close()


@dataclass(frozen=True, slots=True)
class _ProductionNodePorts:
    generate_hypothesis: Any
    decide_action: Any
    persist_action: Any
    notify_email: Any
    approval_email: Any
    hitl_interrupt: Any
    publish_mes: Any
    writeback_result: Any
    cancel_mes: Any


def _production_tool_executor(
    *,
    transactions: Any,
    boundary: ToolBoundary,
    executor: ThreadPoolExecutor,
) -> AuditedToolExecutor:
    """read Tool caller deadline을 production 조립 경계에 고정한다."""

    return AuditedToolExecutor(
        transactions=transactions,
        boundary=boundary,
        deadline_runner=ThreadDeadlineRunner(executor),
        deadline_seconds=READ_TOOL_CALLER_DEADLINE_SECONDS,
    )


def _production_resources(llm_model: str) -> RuntimeResources:
    """첫 public run 요청에서만 실제 runtime 자원을 조립한다."""

    from psycopg_pool import ConnectionPool

    engine = get_app_engine()
    url = create_app_postgres_url().set(drivername="postgresql")
    pool: Any | None = None
    executor: ThreadPoolExecutor | None = None

    @contextmanager
    def transactions() -> Iterator[Any]:
        with engine.begin() as connection:
            yield connection

    @contextmanager
    def resume_connections() -> Iterator[Any]:
        with engine.connect() as connection:
            yield connection

    try:
        pool = ConnectionPool(
            conninfo=url.render_as_string(hide_password=False),
            kwargs={"autocommit": True},
            min_size=1,
            max_size=4,
            timeout=5.0,
            open=False,
            name="agent-checkpoint",
        )
        pool.open(wait=True, timeout=5.0)
        saver = build_postgres_saver(pool)

        executor = ThreadPoolExecutor(
            max_workers=8,
            thread_name_prefix="agent-tool",
        )
        boundary = ToolBoundary.production(
            settings=settings,
            transactions=transactions,
        )
        tools = _production_tool_executor(
            transactions=transactions,
            boundary=boundary,
            executor=executor,
        )

        email = email_production_ports(settings, transactions)
        mes = mes_production_ports(settings, transactions)
        ports: AgentNodePorts = _ProductionNodePorts(  # type: ignore[assignment]
            generate_hypothesis=hypothesis.production_port(),
            decide_action=decision.production_port(),
            persist_action=action_store.production_port(transactions),
            notify_email=email.notify_email,
            approval_email=approval_email_port(
                transactions,
                email.approval_sender,
            ),
            hitl_interrupt=hitl_decision_port(transactions),
            publish_mes=mes.publish_mes,
            writeback_result=mes.writeback_result,
            cancel_mes=cancel_mes_port(transactions),
        )
        graph = build_agent_graph(
            AgentGraphDependencies(
                transactions=transactions,
                tools=tools,
                routing_graph=GraphBoundary.production(),
                ports=ports,
                configured_llm_model=llm_model,
                require_bound_thread=True,
            ),
            checkpointer=saver,
            interrupt_after=("load_incident", "approval_email"),
        )
        return RuntimeResources(
            graph=graph,
            transactions=transactions,
            resume_connections=resume_connections,
            checkpoint_pool=pool,
            deadline_executor=executor,
            llm_model=llm_model,
        )
    except Exception:
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.error("agent deadline executor partial cleanup failed")
        if pool is not None:
            try:
                pool.close()
            except Exception:
                logger.error("agent checkpoint pool partial cleanup failed")
        raise


def _production_ask_service() -> ask.AgentAskService:
    """run/checkpoint 자원과 분리된 읽기 전용 Chat composition."""

    return ask.AgentAskService(tools=ask.StructuredAskReadTools.production())


def _production_embedding_preflight() -> None:
    """새 run DML 전에 local RAG model을 warm-up한다."""

    from app.knowledge.embedding import warm_embedding_model

    warm_embedding_model()


class AgentRuntime:
    """public endpoint가 공유하는 lazy runtime과 crash-window 보상 경계."""

    def __init__(
        self,
        *,
        factory: RuntimeFactory = _production_resources,
        ask_factory: AskServiceFactory = _production_ask_service,
        llm_preflight: Callable[[], str] = llm.preflight_model,
        model_config: Callable[[], str] = llm.configured_model,
        embedding_preflight: Callable[[], None] | None = None,
        autonomy_level: int = AGENT_AUTONOMY_LEVEL,
    ) -> None:
        self._factory = factory
        self._ask_factory = ask_factory
        self._llm_preflight = llm_preflight
        self._model_config = model_config
        self._embedding_preflight = embedding_preflight
        self._autonomy_level = autonomy_level
        self._resources: RuntimeResources | None = None
        self._ask_service: ask.AgentAskService | None = None
        self._closed = False
        self._lock = Lock()

    def _require_open(self) -> None:
        if self._closed:
            raise AgentRuntimeError("AGENT_RUNTIME_CLOSED")

    def _build_or_get(self, model: str) -> RuntimeResources:
        self._require_open()
        if self._resources is not None:
            if self._resources.llm_model != model:
                raise AgentRuntimeError("LLM_MODEL_CHANGED")
            return self._resources
        with self._lock:
            self._require_open()
            if self._resources is not None:
                if self._resources.llm_model != model:
                    raise AgentRuntimeError("LLM_MODEL_CHANGED")
                return self._resources
            try:
                self._resources = self._factory(model)
            except Exception as exc:
                raise AgentRuntimeError("AGENT_RUNTIME_NOT_READY") from exc
            return self._resources

    def resources(self) -> RuntimeResources:
        """재개/종료 경로용 조립. LLM 원격 가용성을 다시 요구하지 않는다."""

        self._require_open()
        if self._autonomy_level not in (1, 2):
            raise AgentRuntimeError("AUTONOMY_LEVEL_NOT_READY")
        try:
            model = self._model_config()
        except Exception as exc:
            raise AgentRuntimeError("LLM_NOT_READY") from exc
        return self._build_or_get(model)

    def preflight(self) -> RuntimeResources:
        """POST run DML 전에 configured model의 원격 준비까지 확인한다."""

        self._require_open()
        if self._autonomy_level not in (1, 2):
            raise AgentRuntimeError("AUTONOMY_LEVEL_NOT_READY")
        try:
            model = self._llm_preflight()
        except Exception as exc:
            raise AgentRuntimeError("LLM_NOT_READY") from exc
        if self._embedding_preflight is not None:
            try:
                self._embedding_preflight()
            except Exception as exc:
                raise AgentRuntimeError("RAG_MODEL_NOT_READY") from exc
        return self._build_or_get(model)

    @staticmethod
    def _checkpoint_values(
        resources: RuntimeResources,
        thread_id: str,
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        snapshot = resources.graph.get_state(build_thread_config(thread_id))
        values = dict(getattr(snapshot, "values", {}) or {})
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        return values, next_nodes

    def _compensate_thread(
        self,
        resources: RuntimeResources,
        thread_id: str,
        *,
        code: str,
    ) -> None:
        try:
            with resources.transactions() as connection:
                run = get_agent_run_by_thread_exact(connection, thread_id)
                if run.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
                    return
                merged = merge_run_action_provenance(
                    connection,
                    run.agent_run_id,
                    terminal_evidence={"code": code},
                )
                finish_agent_run_with_active_latency(
                    connection,
                    run.agent_run_id,
                    RunStatus.FAILED,
                    now=datetime.now(UTC),
                    evidence=merged.evidence,
                )
        except RepositoryNotFound:
            return
        except Exception:
            logger.error("agent initial compensation failed (code=%s)", code)

    def _recover_started(
        self,
        resources: RuntimeResources,
        thread_id: str,
    ) -> str | None:
        """invoke 응답 유실 뒤에도 저장된 첫 checkpoint를 성공으로 판정한다."""

        try:
            values, next_nodes = self._checkpoint_values(resources, thread_id)
            run_id = values.get("run_id")
            if (
                next_nodes != ("collect_fdc",)
                or not isinstance(run_id, str)
                or values.get("thread_id") != thread_id
            ):
                return None
            with resources.transactions() as connection:
                run = get_agent_run_by_thread_exact(connection, thread_id)
            if run.agent_run_id != run_id or run.status is not RunStatus.RUNNING:
                return None
            return run_id
        except Exception:
            return None

    def start_run(self, alarm: AlarmRef) -> StartedPublicRun:
        resources = self.preflight()
        thread_id = new_thread_id()
        config = build_thread_config(thread_id)
        run_id: str | None = None
        try:
            result = resources.graph.invoke(
                {
                    "requested_alarm": alarm,
                    "autonomy_level": self._autonomy_level,
                    "thread_id": thread_id,
                },
                config=config,
            )
            values, next_nodes = self._checkpoint_values(resources, thread_id)
            candidate = values.get("run_id")
            if not isinstance(candidate, str) and isinstance(result, dict):
                candidate = result.get("run_id")
            if (
                not isinstance(candidate, str)
                or values.get("thread_id") != thread_id
                or next_nodes != ("collect_fdc",)
            ):
                raise AgentRuntimeError("INITIAL_CHECKPOINT_INVALID")
            run_id = candidate
            with resources.transactions() as connection:
                stored = get_agent_run_by_thread_exact(connection, thread_id)
            if stored.agent_run_id != run_id or stored.status is not RunStatus.RUNNING:
                raise AgentRuntimeError("INITIAL_RUN_IDENTITY_INVALID")
        except AgentRuntimeError:
            self._compensate_thread(
                resources,
                thread_id,
                code="INITIAL_CHECKPOINT_INVALID",
            )
            raise
        except Exception:
            run_id = self._recover_started(resources, thread_id)
            if run_id is None:
                self._compensate_thread(
                    resources,
                    thread_id,
                    code="INITIAL_CHECKPOINT_FAILED",
                )
                raise
        return StartedPublicRun(
            agent_run_id=run_id,
            alarm=alarm,
            thread_id=thread_id,
        )

    def _fail_run(self, resources: RuntimeResources, run_id: str, code: str) -> None:
        try:
            with resources.transactions() as connection:
                run = get_agent_run(connection, run_id)
                if run.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
                    return
                merged = merge_run_action_provenance(
                    connection,
                    run_id,
                    terminal_evidence={"code": code},
                )
                finish_agent_run_with_active_latency(
                    connection,
                    run_id,
                    RunStatus.FAILED,
                    now=datetime.now(UTC),
                    evidence=merged.evidence,
                )
        except Exception:
            logger.error("agent background failure persistence failed (code=%s)", code)

    def fail_registered_run(self, run_id: str) -> None:
        resources = self.resources()
        self._fail_run(resources, run_id, "BACKGROUND_REGISTRATION_FAILED")

    def continue_run(self, thread_id: str, run_id: str) -> None:
        resources = self.resources()
        try:
            resources.graph.invoke(None, config=build_thread_config(thread_id))
        except Exception:
            # 승인 email의 재시도 가능 checkpoint는 WAITING을 유지해야 한다.
            try:
                with resources.transactions() as connection:
                    run = get_agent_run(connection, run_id)
                if run.status is RunStatus.WAITING_APPROVAL:
                    logger.warning("agent continuation paused at approval boundary")
                    return
            except AgentRepositoryError:
                pass
            self._fail_run(resources, run_id, "BACKGROUND_EXECUTION_FAILED")
            logger.error("agent continuation failed")

    def decide_approval_public(
        self,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> DecidedPublicApproval:
        resources = self.resources()
        try:
            with resources.transactions() as connection:
                decided = decide_approval(
                    connection,
                    approval_id=approval_id,
                    decision=to_internal_decision(request.decision),
                    decided_by=request.decided_by,
                    decision_comment=request.decision_comment,
                )
                record = get_approval_public(connection, approval_id)
                run = get_agent_run(connection, decided.approval.agent_run_id)
            return DecidedPublicApproval(
                item=to_public_approval(record),
                thread_id=run.thread_id,
                agent_run_id=run.agent_run_id,
            )
        except RepositoryNotFound as exc:
            raise NotFoundError() from exc
        except RepositoryConflict as exc:
            if exc.code in {
                "APPROVAL_NOT_PENDING",
                "RUN_NOT_WAITING_APPROVAL",
                "ACTION_APPROVAL_NOT_PENDING",
            }:
                raise ApprovalAlreadyDecidedError() from exc
            raise
        except (RepositoryRetryable, RepositoryUnavailable) as exc:
            raise DependencyNotReadyError() from exc

    def resume_decided(self, thread_id: str, run_id: str) -> None:
        resources = self.resources()
        try:
            resume_after_approval(
                resources.graph,
                resources.transactions,
                resources.resume_connections,
                thread_id,
            )
        except Exception:
            self._fail_run(resources, run_id, "APPROVAL_RESUME_FAILED")
            logger.error("agent approval resume failed")

    def ask_public(
        self,
        question: str,
        *,
        context_evidence: Sequence[ask.AskEvidenceItem] = (),
    ) -> ask.AgentAskResponse:
        """checkpoint·run DML 없이 Ask 전용 A/B read Tool만 조립한다."""

        self._require_open()
        service = self._ask_service
        if service is None:
            with self._lock:
                self._require_open()
                service = self._ask_service
                if service is None:
                    try:
                        service = self._ask_factory()
                    except Exception as exc:
                        raise AgentRuntimeError("AGENT_ASK_NOT_READY") from exc
                    self._ask_service = service
        return service.ask(question, context_evidence=context_evidence)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            resources = self._resources
            self._resources = None
            self._ask_service = None
        if resources is not None:
            resources.close()


_production_runtime = AgentRuntime(
    embedding_preflight=_production_embedding_preflight,
)


def get_agent_runtime() -> AgentRuntime:
    """FastAPI dependency override가 가능한 process singleton."""

    return _production_runtime


def close_agent_runtime() -> None:
    _production_runtime.close()


def runtime_http_error(exc: Exception) -> Exception:
    """composition 오류를 public 503으로 정규화한다."""

    if isinstance(exc, AgentRuntimeError):
        return DependencyNotReadyError()
    if isinstance(exc, AgentGraphInputError):
        if exc.code == "ALARM_NOT_FOUND":
            return NotFoundError()
        if exc.code in {
            "DATABASE_UNAVAILABLE",
            "SERIALIZATION_FAILURE",
            "DEADLOCK_DETECTED",
            "LOCK_NOT_AVAILABLE",
            "STATEMENT_CANCELED",
        }:
            return DependencyNotReadyError()
        if exc.code in {
            "AUTONOMY_LEVEL_INVALID",
            "AUTONOMY_LEVEL_NOT_IMPLEMENTED",
            "REQUESTED_ALARM_INVALID",
        }:
            return InvalidRequestError()
        return RuntimeError("AGENT_RUNTIME_INPUT_CONTRACT_ERROR")
    if isinstance(exc, RepositoryRetryable | RepositoryUnavailable):
        return DependencyNotReadyError()
    if isinstance(exc, RepositoryNotFound):
        return NotFoundError()
    if isinstance(exc, RepositoryContractError):
        return AppError()
    return exc


__all__ = [
    "AgentRuntime",
    "AgentRuntimeError",
    "DecidedPublicApproval",
    "RuntimeResources",
    "StartedPublicRun",
    "close_agent_runtime",
    "get_agent_runtime",
    "runtime_http_error",
]
