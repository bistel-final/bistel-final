from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INCIDENT_ALREADY_RUNNING = "INCIDENT_ALREADY_RUNNING"
    INCIDENT_ALREADY_PROCESSED = "INCIDENT_ALREADY_PROCESSED"
    APPROVAL_ALREADY_DECIDED = "APPROVAL_ALREADY_DECIDED"
    LEGACY_APPROVAL_NOT_LINKED = "LEGACY_APPROVAL_NOT_LINKED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    POLICY_REJECTED = "POLICY_REJECTED"
    DEPENDENCY_NOT_READY = "DEPENDENCY_NOT_READY"
    MODEL_NOT_READY = "MODEL_NOT_READY"
    LLM_NOT_READY = "LLM_NOT_READY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AppError(Exception):
    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    message: str = "서버 오류가 발생했습니다."

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or type(self).message
        # details 에는 식별자만 담는다. 비밀번호·DSN·API Key·SQL 원문을 넣지 않는다.
        self.details = details or {}
        super().__init__(self.message)

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(code=self.code, message=self.message, details=self.details)


class UnauthorizedError(AppError):
    status_code = 401
    code = ErrorCode.UNAUTHORIZED
    message = "인증 정보가 올바르지 않습니다."


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.RESOURCE_NOT_FOUND
    message = "리소스를 찾을 수 없습니다."


class ConflictError(AppError):
    status_code = 409
    code = ErrorCode.INCIDENT_ALREADY_RUNNING
    message = "이미 처리 중입니다."


class IncidentAlreadyRunningError(ConflictError):
    code = ErrorCode.INCIDENT_ALREADY_RUNNING
    message = "동일 incident의 실행이 진행 중입니다."


class IncidentAlreadyProcessedError(ConflictError):
    code = ErrorCode.INCIDENT_ALREADY_PROCESSED
    message = "동일 incident가 이미 처리됐습니다."


class ApprovalAlreadyDecidedError(ConflictError):
    code = ErrorCode.APPROVAL_ALREADY_DECIDED
    message = "이미 처리된 승인 요청입니다."


class LegacyApprovalNotLinkedError(ConflictError):
    code = ErrorCode.LEGACY_APPROVAL_NOT_LINKED
    message = "조치와 연결되지 않은 승인 요청입니다."


class IdempotencyConflictError(ConflictError):
    code = ErrorCode.IDEMPOTENCY_CONFLICT
    message = "같은 식별자로 다른 내용이 이미 처리됐습니다."


# FastAPI 의 RequestValidationError 와 혼동하지 않도록 다른 이름을 쓴다.
class InvalidRequestError(AppError):
    status_code = 422
    code = ErrorCode.VALIDATION_ERROR
    message = "요청 형식이 올바르지 않습니다."


class PolicyRejectedError(AppError):
    """정책 거부를 HTTP 오류로 표현해야 하는 경로에서만 사용한다.

    ``POST /analytics/query``는 사용자가 거부 사유와 질의 이력을 화면에서 확인할
    수 있도록 이 예외를 발생시키지 않고 HTTP 200의 구조화 응답을 반환한다. Tool
    경로는 별도 계약인 ``POLICY_REJECTED:`` reason 접두어를 그대로 사용한다.
    """

    status_code = 422
    code = ErrorCode.POLICY_REJECTED
    message = "정책상 허용되지 않는 요청입니다."


class DependencyNotReadyError(AppError):
    status_code = 503
    code = ErrorCode.DEPENDENCY_NOT_READY
    message = "의존 서비스가 준비되지 않았습니다."


class ModelNotReadyError(AppError):
    status_code = 503
    code = ErrorCode.MODEL_NOT_READY
    message = "모델 산출물이 준비되지 않았습니다."


class LLMNotReadyError(AppError):
    status_code = 503
    code = ErrorCode.LLM_NOT_READY
    message = "LLM 설정이 준비되지 않았습니다."
