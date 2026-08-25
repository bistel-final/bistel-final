"""Knowledge domain exceptions."""

from __future__ import annotations

from app.common.exceptions import DependencyNotReadyError


class GraphProjectionShapeError(DependencyNotReadyError):
    """Neo4j graph projection row가 API 계약과 맞지 않을 때 발생한다."""

    message = "Neo4j graph projection 형식이 올바르지 않습니다."
