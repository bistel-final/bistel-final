import logging

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.common.exceptions import (
    ApprovalAlreadyDecidedError,
    DependencyNotReadyError,
    ErrorCode,
    IncidentAlreadyRunningError,
    ModelNotReadyError,
    NotFoundError,
    PolicyRejectedError,
    UnauthorizedError,
)
from app.main import (
    handle_app_error,
    handle_http_exception,
    handle_request_validation_error,
    handle_unexpected_error,
)

SECRETS = ("kosa_pw", "postgresql+psycopg", "readonly_pw", "SELECT 1 FROM")


class _Body(BaseModel):
    alarm_id: str


def _build_app() -> FastAPI:
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.common.exceptions import AppError

    app = FastAPI()
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)

    router = APIRouter()

    @router.get("/not-found")
    def _not_found() -> None:
        raise NotFoundError(details={"alarm_id": "ALM-9999"})

    @router.get("/unauthorized")
    def _unauthorized() -> None:
        raise UnauthorizedError()

    @router.get("/http-unauthorized")
    def _http_unauthorized() -> None:
        raise HTTPException(status_code=401, detail="인증 정보가 올바르지 않습니다.")

    @router.get("/conflict")
    def _conflict() -> None:
        raise IncidentAlreadyRunningError(details={"agent_run_id": "RUN-1"})

    @router.get("/approval-conflict")
    def _approval_conflict() -> None:
        raise ApprovalAlreadyDecidedError()

    @router.get("/policy")
    def _policy() -> None:
        raise PolicyRejectedError()

    @router.get("/not-ready")
    def _not_ready() -> None:
        raise DependencyNotReadyError(details={"postgres": "down"})

    @router.get("/model-not-ready")
    def _model_not_ready() -> None:
        raise ModelNotReadyError()

    @router.get("/boom")
    def _boom() -> None:
        raise RuntimeError(
            "connection to postgresql+psycopg://kosa:kosa_pw@10.0.0.1:5432/kosa failed"
        )

    @router.post("/echo")
    def _echo(body: _Body) -> dict[str, str]:
        return {"alarm_id": body.alarm_id}

    app.include_router(router)
    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


class TestStatusMapping:
    @pytest.mark.parametrize(
        "path, status, code",
        [
            ("/unauthorized", 401, ErrorCode.UNAUTHORIZED),
            ("/http-unauthorized", 401, ErrorCode.UNAUTHORIZED),
            ("/not-found", 404, ErrorCode.RESOURCE_NOT_FOUND),
            ("/conflict", 409, ErrorCode.INCIDENT_ALREADY_RUNNING),
            ("/approval-conflict", 409, ErrorCode.APPROVAL_ALREADY_DECIDED),
            ("/policy", 422, ErrorCode.POLICY_REJECTED),
            ("/not-ready", 503, ErrorCode.DEPENDENCY_NOT_READY),
            ("/model-not-ready", 503, ErrorCode.MODEL_NOT_READY),
            ("/boom", 500, ErrorCode.INTERNAL_ERROR),
        ],
    )
    def test_status_and_code(
        self,
        client: TestClient,
        path: str,
        status: int,
        code: ErrorCode,
    ) -> None:
        response = client.get(path)

        assert response.status_code == status
        assert response.json()["code"] == code.value

    def test_request_validation_is_422(self, client: TestClient) -> None:
        response = client.post("/echo", json={})

        assert response.status_code == 422
        assert response.json()["code"] == ErrorCode.VALIDATION_ERROR.value


class TestResponseShape:
    @pytest.mark.parametrize(
        "path",
        [
            "/unauthorized",
            "/not-found",
            "/conflict",
            "/policy",
            "/not-ready",
            "/boom",
        ],
    )
    def test_body_has_exactly_three_keys(self, client: TestClient, path: str) -> None:
        body = client.get(path).json()

        assert set(body) == {"code", "message", "details"}
        assert isinstance(body["message"], str) and body["message"]
        assert isinstance(body["details"], dict)

    def test_details_carry_identifiers(self, client: TestClient) -> None:
        body = client.get("/not-found").json()

        assert body["details"] == {"alarm_id": "ALM-9999"}

    def test_validation_details_expose_field_location_only(
        self,
        client: TestClient,
    ) -> None:
        body = client.post("/echo", json={}).json()
        fields = body["details"]["fields"]

        assert fields
        assert set(fields[0]) == {"loc", "type"}


class TestNoSecretLeak:
    def test_internal_error_hides_dsn(self, client: TestClient) -> None:
        raw = client.get("/boom").text

        for secret in SECRETS:
            assert secret not in raw

    def test_internal_error_message_is_generic(self, client: TestClient) -> None:
        body = client.get("/boom").json()

        assert body["message"] == "서버 오류가 발생했습니다."
        assert body["details"] == {}

    def test_server_log_does_not_contain_secrets(
        self,
        client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            client.get("/boom")

        recorded = "\n".join(
            [record.getMessage() for record in caplog.records] + [caplog.text]
        )

        for secret in SECRETS:
            assert secret not in recorded

    def test_server_log_keeps_diagnostic_context(
        self,
        client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="app.main"):
            client.get("/boom")

        recorded = "\n".join(record.getMessage() for record in caplog.records)

        assert "RuntimeError" in recorded
        assert "/boom" in recorded

    def test_no_traceback_is_logged(
        self,
        client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            client.get("/boom")

        assert all(record.exc_info is None for record in caplog.records)
