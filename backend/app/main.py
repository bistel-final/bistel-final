import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.router import router as agent_router
from app.agent.runtime_composition import close_agent_runtime
from app.analytics.router import router as analytics_router
from app.common.config import CORS_ORIGINS
from app.common.db import dispose_engines
from app.common.exceptions import AppError, ErrorCode, ErrorResponse
from app.common.neo4j import close_neo4j_driver
from app.common.readiness import ReadinessManager, create_readiness_manager
from app.common.schemas import HealthResponse, ReadinessResponse
from app.detection.router import router as detection_router
from app.knowledge.router import router as knowledge_router

logger = logging.getLogger(__name__)

# HTTPException 으로 올라온 상태코드를 공통 오류 코드에 대응시킨다.
_STATUS_ERROR_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    409: ErrorCode.INCIDENT_ALREADY_RUNNING,
    422: ErrorCode.VALIDATION_ERROR,
    503: ErrorCode.DEPENDENCY_NOT_READY,
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    factory = getattr(
        application.state,
        "readiness_manager_factory",
        create_readiness_manager,
    )
    manager: ReadinessManager = factory()
    application.state.readiness_manager = manager
    try:
        manager.start()
    except Exception:
        logger.warning("readiness background startup failed")

    try:
        yield
    finally:
        try:
            manager.close()
        finally:
            try:
                close_agent_runtime()
            finally:
                try:
                    dispose_engines()
                finally:
                    close_neo4j_driver()


app = FastAPI(
    title="BISTel FDC Agent API",
    description="LangGraph 기반 반도체 FDC 이상감지 에이전트 Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_response(status_code: int, payload: ErrorResponse) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return _error_response(exc.status_code, exc.to_response())


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    # 필드 위치와 사유만 노출한다. 입력값 원문은 details 에 넣지 않는다.
    fields = [
        {"loc": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]

    return _error_response(
        422,
        ErrorResponse(
            code=ErrorCode.VALIDATION_ERROR,
            message="요청 형식이 올바르지 않습니다.",
            details={"fields": fields},
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    _: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    detail = exc.detail
    details = detail if isinstance(detail, dict) else {}
    message = detail if isinstance(detail, str) else "요청을 처리할 수 없습니다."

    return _error_response(
        exc.status_code,
        ErrorResponse(
            code=_STATUS_ERROR_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR),
            message=message,
            details=details,
        ),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # 예외 메시지·traceback 을 남기지 않는다. DSN·비밀번호·API Key·SQL 원문이
    # 예외 문자열에 실려 로그로 새는 경로를 원천 차단한다(설계 2.3).
    # 진단에 필요한 문맥은 각 Service 가 민감정보를 뺀 구조화 로그로 직접 남긴다.
    logger.error(
        "처리되지 않은 오류 type=%s method=%s path=%s",
        type(exc).__name__,
        request.method,
        request.url.path,
    )

    return _error_response(
        500,
        ErrorResponse(
            code=ErrorCode.INTERNAL_ERROR,
            message="서버 오류가 발생했습니다.",
        ),
    )


@app.get("/health", tags=["System"], response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="UP")


@app.get(
    "/health/ready",
    tags=["System"],
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def readiness(request: Request) -> ReadinessResponse | Response:
    result: ReadinessResponse = request.app.state.readiness_manager.collect()
    if result.status == "NOT_READY":
        return JSONResponse(
            status_code=503,
            content=result.model_dump(mode="json"),
        )
    return result


app.include_router(detection_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(analytics_router)
