"""Read-only U10 preflight check 7, not a profile/identity or enable gate.

Both sequential Compose profiles publish the frontend gateway on localhost:8080.
Production's backend is not host-published; /api/health/ready traverses nginx.
The caller must separately bind that deployment to pinned images and DB identity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import ValidationError

from app.agent.release_artifacts import EvidenceError, EvidenceModel, parse_json
from app.common.schemas import ReadinessResponse

GATEWAY_ORIGIN = "http://127.0.0.1:8080"
_PATHS = ("/api/health/ready", "/", "/api")
_MAX_READINESS_BYTES = 16384


@dataclass(frozen=True)
class ProbeResponse:
    status_code: int
    body: bytes = b""


Fetch = Callable[[str], ProbeResponse]


def fetch_gateway(path: str) -> ProbeResponse:
    """GET only code-owned local paths, without proxies, redirects or retries."""
    if type(path) is not str or path not in _PATHS:
        raise EvidenceError("U10_READINESS_PATH_INVALID")
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=15.0
        ) as client:
            with client.stream("GET", GATEWAY_ORIGIN + path) as response:
                body = bytearray()
                # Only readiness needs a payload. Never retain HTML or API bodies.
                if path == "/api/health/ready" and response.status_code == 200:
                    for chunk in response.iter_bytes(chunk_size=4096):
                        body.extend(chunk)
                        if len(body) > _MAX_READINESS_BYTES:
                            raise EvidenceError("U10_READINESS_RESPONSE_INVALID")
                return ProbeResponse(response.status_code, bytes(body))
    except httpx.HTTPError:
        raise EvidenceError("U10_READINESS_HTTP_FAILED") from None


class ReadinessObservation(EvidenceModel):
    gateway_origin: Literal["http://127.0.0.1:8080"]
    backend_readiness: ReadinessResponse
    frontend_status: Literal[200]
    api_status: Literal[200]


def _probe(fetch: Fetch, path: str) -> ProbeResponse:
    try:
        response = fetch(path)
    except (httpx.HTTPError, OSError):
        raise EvidenceError("U10_READINESS_HTTP_FAILED") from None
    if (
        type(response) is not ProbeResponse
        or type(response.status_code) is not int
        or type(response.body) is not bytes
    ):
        raise EvidenceError("U10_READINESS_RESPONSE_INVALID")
    if response.status_code != 200:
        raise EvidenceError("U10_READINESS_HTTP_STATUS_INVALID")
    return response


def verify_readiness(*, fetch: Fetch = fetch_gateway) -> ReadinessObservation:
    """Require backend READY/exact six PASS, frontend / 200 and /api 200.

    Return an in-memory point-in-time observation only. No receipt, overall
    integrity, profile assertion, allowed_actions, or external-effect grant.
    """
    response = _probe(fetch, "/api/health/ready")
    if len(response.body) > _MAX_READINESS_BYTES:
        raise EvidenceError("U10_READINESS_RESPONSE_INVALID")
    try:
        readiness = ReadinessResponse.model_validate(
            parse_json(response.body), strict=True
        )
    except (EvidenceError, ValidationError):
        raise EvidenceError("U10_READINESS_RESPONSE_INVALID") from None
    if readiness.status != "READY":
        raise EvidenceError("U10_READINESS_NOT_READY")
    frontend = _probe(fetch, "/")
    api = _probe(fetch, "/api")
    return ReadinessObservation(
        gateway_origin=GATEWAY_ORIGIN,
        backend_readiness=readiness,
        frontend_status=frontend.status_code,
        api_status=api.status_code,
    )
