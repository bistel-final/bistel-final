import logging
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
)
from sqlalchemy.engine import Connection

from app.agent.ask import AgentAskUnavailable, extract_ask_identifiers
from app.agent.delivery_callback import (
    DeliveryCallbackService,
    DeliveryResult,
    callback_request_openapi_schema,
    get_delivery_callback_service,
    normalize_action_id,
    parse_callback_body,
    validate_json_content_type,
)
from app.agent.evaluation_read_model import load_agent_evaluations
from app.agent.evaluation_schemas import AgentEvaluationResponse
from app.agent.public_read_model import (
    PublicDateRangeError,
    list_public_actions,
    list_public_agent_runs,
    list_public_approvals,
    load_public_action_detail,
    load_public_agent_run_detail,
)
from app.agent.public_schemas import (
    ASK_EVIDENCE_ADAPTER,
    ActionDetailResponse,
    ActionItem,
    AgentAskRequest,
    AgentAskResponse,
    AgentRunAskEvidence,
    AgentRunDetailResponse,
    PublicAgentRunItem,
    PublicApprovalItem,
)
from app.agent.repository import (
    RepositoryContractError,
    RepositoryNotFound,
    RepositoryRetryable,
    RepositoryUnavailable,
)
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
from app.common.db import get_app_engine, get_db_connection
from app.common.enums import ActionCode, FaultHypothesis, RunStatus
from app.common.exceptions import (
    AppError,
    DependencyNotReadyError,
    ErrorResponse,
    IdempotencyConflictError,
    InvalidRequestError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


def _agent_read_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Agent 조회 데이터베이스가 준비되지 않았습니다.",
    )


def _agent_read_contract_error(exc: RepositoryContractError) -> AppError:
    """손상 code만 기록하고 public 응답에는 내부 row·driver 정보를 숨긴다."""

    logger.error("agent public read contract failed (code=%s)", exc.code)
    mapped = runtime_http_error(exc)
    if isinstance(mapped, AppError):
        return mapped
    return AppError()


def _ask_run_context(detail: AgentRunDetailResponse) -> tuple[object, ...]:
    """공개 run DTO만으로 후속 질문 citation allowlist를 만든다."""

    diagnosis = detail.diagnosis
    parts = [
        diagnosis.cause_summary,
        diagnosis.evidence_synthesis,
        *diagnosis.observations,
        *diagnosis.verification_steps,
        *diagnosis.limitations,
    ]
    context = [
        AgentRunAskEvidence(
            type="AGENT_RUN",
            source_id=detail.agent_run_id,
            title="저장된 Agent 종합 진단",
            excerpt="\n".join(value for value in parts if value)[:4000]
            or "저장된 v2 종합 진단 내용이 없습니다.",
        )
    ]
    for item in detail.evidence_items:
        payload = item.model_dump(mode="json")
        payload.pop("alarm", None)
        context.append(ASK_EVIDENCE_ADAPTER.validate_python(payload))
    return tuple(context)


def _validate_ask_context_identifiers(
    question: str,
    detail: AgentRunDetailResponse,
) -> None:
    identifiers = extract_ask_identifiers(question)
    values = {
        detail.chamber_id.upper(),
        *(item.source_id.upper() for item in detail.impact_scope.direct),
        *(item.source_id.upper() for item in detail.evidence_items),
    }
    requested = {
        value.upper()
        for value in (
            identifiers.lot_hist_id,
            identifiers.lot_id,
            identifiers.chamber_id,
            identifiers.model_code,
        )
        if value is not None
    }
    if not requested <= values:
        raise InvalidRequestError(
            details={"reason": "AGENT_RUN_CONTEXT_IDENTIFIER_CONFLICT"}
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
            # add_task는 현재 in-memory 등록만 보상한다. 202 뒤 process 종료 창은
            # agent-background-recovery runbook의 수동 복구 범위다.
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
    status: RunStatus | None = None,
    predicted_fault_code: FaultHypothesis | None = None,
) -> list[PublicAgentRunItem]:
    """Auto Analysis 실행 이력. API v3 호환 bare array다."""

    try:
        return list_public_agent_runs(
            connection,
            date_from=date_from,
            date_to=date_to,
            status=status,
            predicted_fault_code=predicted_fault_code,
        )
    except PublicDateRangeError as exc:
        raise HTTPException(
            status_code=422,
            detail="date_from과 date_to를 올바른 순서로 함께 보내야 합니다.",
        ) from exc
    except (RepositoryRetryable, RepositoryUnavailable) as exc:
        raise _agent_read_unavailable() from exc
    except RepositoryContractError as exc:
        raise _agent_read_contract_error(exc) from exc


@router.get("/agent/evaluations", response_model=AgentEvaluationResponse)
def read_agent_evaluations() -> AgentEvaluationResponse:
    """검증 완료된 두 immutable artifact를 독립 Empty 상태로 projection한다."""

    return load_agent_evaluations()


@router.get(
    "/agent/runs/{run_id}",
    response_model=AgentRunDetailResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_agent_run_detail(
    run_id: Annotated[str, Path(min_length=1)],
    connection: Annotated[Connection, Depends(get_db_connection)],
) -> AgentRunDetailResponse:
    """저장된 실행·Tool 결과만 projection한 Agent 상세다."""

    try:
        return load_public_agent_run_detail(connection, run_id)
    except RepositoryNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Agent 실행을 찾을 수 없습니다.",
        ) from exc
    except (RepositoryRetryable, RepositoryUnavailable) as exc:
        raise _agent_read_unavailable() from exc
    except RepositoryContractError as exc:
        raise _agent_read_contract_error(exc) from exc


@router.get(
    "/actions",
    response_model=list[ActionItem],
    responses={
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_actions(
    connection: Annotated[Connection, Depends(get_db_connection)],
    action_code: ActionCode | None = None,
) -> list[ActionItem]:
    """조치 이력 bare array. severity는 action_code에서 파생하므로 노출하지 않는다."""

    try:
        return list_public_actions(connection, action_code=action_code)
    except (
        RepositoryContractError,
        RepositoryRetryable,
        RepositoryUnavailable,
    ) as exc:
        raise _agent_read_unavailable() from exc


@router.get(
    "/actions/{action_id}",
    response_model=ActionDetailResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_action_detail(
    action_id: Annotated[str, Path(min_length=1)],
    connection: Annotated[Connection, Depends(get_db_connection)],
) -> ActionDetailResponse:
    """delivery 시각까지 확장한 조치 상세다."""

    try:
        return load_public_action_detail(connection, action_id)
    except RepositoryNotFound as exc:
        raise HTTPException(status_code=404, detail="조치를 찾을 수 없습니다.") from exc
    except (RepositoryRetryable, RepositoryUnavailable) as exc:
        raise _agent_read_unavailable() from exc
    except RepositoryContractError as exc:
        raise _agent_read_contract_error(exc) from exc


@router.post(
    "/agent/ask",
    response_model=AgentAskResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def ask_agent(
    payload: AgentAskRequest,
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> AgentAskResponse:
    """A/B 읽기 Tool 근거만 사용하는 Chat facade."""

    try:
        if payload.agent_run_id is None:
            return runtime.ask_public(payload.question)
        with get_app_engine().connect() as connection:
            detail = load_public_agent_run_detail(connection, payload.agent_run_id)
        _validate_ask_context_identifiers(payload.question, detail)
        return runtime.ask_public(
            payload.question,
            context_evidence=_ask_run_context(detail),
        )
    except RepositoryNotFound as exc:
        raise HTTPException(
            status_code=404, detail="Agent 실행을 찾을 수 없습니다."
        ) from exc
    except (RepositoryRetryable, RepositoryUnavailable) as exc:
        raise _agent_read_unavailable() from exc
    except RepositoryContractError as exc:
        raise _agent_read_contract_error(exc) from exc
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
    except RepositoryContractError as exc:
        raise _agent_read_contract_error(exc) from exc


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
            # 등록-time 예외만 보상한다. 응답 뒤 process 종료는 runbook에서 대조한다.
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
