import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.router import router as agent_router
from app.agent.runtime_composition import close_agent_runtime
from app.analytics.router import router as analytics_router
from app.common.config import CORS_ORIGINS
from app.common.db import dispose_engines, get_app_engine, get_readonly_engine
from app.common.exceptions import AppError, ErrorCode, ErrorResponse
from app.common.neo4j import close_neo4j_driver, get_neo4j_driver
from app.common.rag_readiness import verify_rag_readiness
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
async def lifespan(_: FastAPI):
    yield

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


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["System"])
def readiness() -> dict[str, object]:
    services: dict[str, str] = {}

    try:
        with get_app_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        services["postgres"] = "available"
    except Exception:
        services["postgres"] = "unavailable"

    try:
        with get_app_engine().connect() as connection:
            verify_rag_readiness(connection)
        services["rag"] = "available"
    except Exception:
        services["rag"] = "unavailable"

    try:
        with get_readonly_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        services["postgres_readonly"] = "available"
    except Exception:
        services["postgres_readonly"] = "unavailable"

    try:
        get_neo4j_driver().verify_connectivity()
        services["neo4j"] = "available"
    except Exception:
        services["neo4j"] = "unavailable"

    if "unavailable" in services.values():
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "services": services},
        )

    return {"status": "ready", "services": services}


app.include_router(detection_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(analytics_router)
