"""Knowledge 도메인 예외."""

from __future__ import annotations

from app.common.exceptions import DependencyNotReadyError


class GraphProjectionShapeError(DependencyNotReadyError):
    """Neo4j 그래프 응답 행이 API projection 계약과 맞지 않을 때 발생한다."""

    message = "Neo4j graph projection 형식이 올바르지 않습니다."
