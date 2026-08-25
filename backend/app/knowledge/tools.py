"""Knowledge Agent Tools."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import ValidationError

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    fail,
)
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.graph_query import GraphQueryRepository
from app.knowledge.service import DocumentSearchService, EquipmentContextService


# ==================
# 문서 RAG
# ==================
@tool
def search_documents(
    query: str,
    model_code: str | None = None,
    top_k: int = 4,
) -> DocumentSearchToolResult:
    """질문과 관련된 RAG 문서 chunk를 검색한다."""

    try:
        engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
        service = DocumentSearchService(DocumentSearchRepository(engine))
        hits = service.search(query, top_k=top_k, model_code=model_code)
        return DocumentSearchToolResult(ok=True, hits=hits)
    except Exception as exc:
        return fail(DocumentSearchToolResult, f"DEPENDENCY_ERROR: {exc}")


# ==================
# Graph
# ==================


@tool
def get_equipment_context(chamber_id: str) -> EquipmentContextToolResult:
    """Chamber 기준 장비·공정 graph context를 조회한다."""

    try:
        service = EquipmentContextService(GraphQueryRepository())
        result = service.get_equipment_context(chamber_id)
        if result is None:
            return fail(
                EquipmentContextToolResult,
                f"NOT_FOUND: chamber_id={chamber_id}",
            )
        return result
    except TimeoutError as exc:
        return fail(EquipmentContextToolResult, f"TIMEOUT: {exc}")
    except ValidationError:
        return fail(
            EquipmentContextToolResult,
            "GRAPH_SHAPE_ERROR: 장비 graph context 필수 값이 누락됐습니다",
        )
    except Exception as exc:
        return fail(EquipmentContextToolResult, f"DEPENDENCY_ERROR: {exc}")
