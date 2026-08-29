from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.engine import Connection

from app.agent.ask import AgentAskUnavailable
from app.agent.delivery_callback import (
    DeliveryCallbackService,
    DeliveryResult,
    callback_request_openapi_schema,
    get_delivery_callback_service,
    normalize_action_id,
    parse_callback_body,
    validate_json_content_type,
)
from app.agent.public_read_model import (
    PublicDateRangeError,
    list_public_agent_runs,
    list_public_approvals,
)
from app.agent.public_schemas import (
    AgentAskRequest,
    AgentAskResponse,
    PublicAgentRunItem,
    PublicApprovalItem,
)
from app.agent.repository import RepositoryRetryable, RepositoryUnavailable
from app.agent.runtime_composition import (
    AgentRuntime,
    get_agent_runtime,
    runtime_http_error,
)
from app.agent.schemas import (
    AgentRunAcceptedResponse,
    AgentRunCreateRequest,
    ApprovalDecisionRequest,
)
from app.common.db import get_db_connection
from app.common.exceptions import DependencyNotReadyError, IdempotencyConflictError

router = APIRouter(tags=["Agent"])


def _agent_read_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Agent 조회 데이터베이스가 준비되지 않았습니다.",
    )


@router.post(
    "/agent/runs",
    response_model=AgentRunAcceptedResponse,
    status_code=202,
)
def create_agent_run(
    payload: AgentRunCreateRequest,
    background_tasks: BackgroundTasks,
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> AgentRunAcceptedResponse:
    """첫 durable checkpoint까지 동기 확정한 뒤 나머지 graph를 이어간다."""

    try:
        started = runtime.start_run(payload.alarm)
        try:
            background_tasks.add_task(
                runtime.continue_run,
                started.thread_id,
                started.agent_run_id,
            )
        except Exception:
            runtime.fail_registered_run(started.agent_run_id)
            raise
    except Exception as exc:
        mapped = runtime_http_error(exc)
        if mapped is exc:
            raise
        raise mapped from exc
    return AgentRunAcceptedResponse(
        agent_run_id=started.agent_run_id,
        status="RUNNING",
        alarm=started.alarm,
    )


@router.get("/agent/runs", response_model=list[PublicAgentRunItem])
def read_agent_runs(
    connection: Annotated[Connection, Depends(get_db_connection)],
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[PublicAgentRunItem]:
    """Auto Analysis 실행 이력. API v3 호환 bare array다."""

    try:
        return list_public_agent_runs(
            connection,
            date_from=date_from,
            date_to=date_to,
        )
    except PublicDateRangeError as exc:
        raise HTTPException(
            status_code=422,
            detail="date_from과 date_to를 올바른 순서로 함께 보내야 합니다.",
        ) from exc
    except (RepositoryRetryable, RepositoryUnavailable) as exc:
        raise _agent_read_unavailable() from exc


@router.post("/agent/ask", response_model=AgentAskResponse)
def ask_agent(
    payload: AgentAskRequest,
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> AgentAskResponse:
    """A/B 읽기 Tool 근거만 사용하는 Chat facade."""

    try:
        return runtime.ask_public(payload.question)
    except AgentAskUnavailable as exc:
        raise DependencyNotReadyError() from exc
    except Exception as exc:
        mapped = runtime_http_error(exc)
        if mapped is exc:
            raise
        raise mapped from exc


@router.get("/approvals", response_model=list[PublicApprovalItem])
def read_approvals(
    connection: Annotated[Connection, Depends(get_db_connection)],
) -> list[PublicApprovalItem]:
    """EQP_HOLD 승인 대기·결정 이력. 내부 AUTO·EXPIRED는 노출하지 않는다."""

    try:
        return list_public_approvals(connection)
    except (RepositoryRetryable, RepositoryUnavailable) as exc:
        raise _agent_read_unavailable() from exc


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=PublicApprovalItem,
)
def decide_public_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    background_tasks: BackgroundTasks,
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> PublicApprovalItem:
    """결정·공개 projection을 한 UoW로 commit한 뒤 graph 재개를 등록한다."""

    try:
        decided = runtime.decide_approval_public(approval_id, payload)
        try:
            background_tasks.add_task(
                runtime.resume_decided,
                decided.thread_id,
                decided.agent_run_id,
            )
        except Exception:
            runtime.fail_registered_run(decided.agent_run_id)
            raise
    except Exception as exc:
        mapped = runtime_http_error(exc)
        if mapped is exc:
            raise
        raise mapped from exc
    return decided.item


@router.post(
    "/internal/actions/{action_id}/delivery",
    response_model=DeliveryResult,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": callback_request_openapi_schema(),
                }
            },
        }
    },
)
async def write_back_delivery(
    action_id: str,
    request: Request,
    service: Annotated[
        DeliveryCallbackService,
        Depends(get_delivery_callback_service),
    ],
    timestamp: Annotated[
        str | None,
        Header(alias="X-Delivery-Timestamp"),
    ] = None,
    signature: Annotated[
        str | None,
        Header(alias="X-Delivery-Signature"),
    ] = None,
) -> DeliveryResult:
    verified = service.verify_headers(timestamp, signature)
    raw = await request.body()
    service.verify_signature(verified, raw)
    normalized_action_id = normalize_action_id(action_id)
    validate_json_content_type(request.headers.get("content-type"))
    callback = parse_callback_body(raw)
    try:
        result = service.settle(
            action_id=normalized_action_id,
            callback=callback,
        )
    except IdempotencyConflictError:
        service.record_http_result(
            action_id=normalized_action_id,
            channel=callback.channel,
            result=None,
            http_status=409,
        )
        raise
    service.record_http_result(
        action_id=normalized_action_id,
        channel=callback.channel,
        result=result,
        http_status=200,
    )
    return result
