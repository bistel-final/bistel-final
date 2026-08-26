"""Knowledge Agent Tool 정의."""

from __future__ import annotations

import logging

from langchain_core.tools import tool
from pydantic import ValidationError

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    fail,
)
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.embedding import EmbeddingModelNotReadyError
from app.knowledge.graph_query import GraphQueryRepository
from app.knowledge.service import DocumentSearchService, EquipmentContextService

logger = logging.getLogger(__name__)


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
    except TimeoutError as exc:
        return fail(DocumentSearchToolResult, f"TIMEOUT: {exc}")
    except EmbeddingModelNotReadyError:
        logger.exception("search_documents embedding model not ready")
        return fail(
            DocumentSearchToolResult,
            "MODEL_NOT_READY: 임베딩 모델이 준비되지 않았습니다",
        )
    except Exception:
        logger.exception("search_documents Tool dependency error")
        return fail(DocumentSearchToolResult, "DEPENDENCY_ERROR: 문서 검색 의존성 오류")


# ==================
# 그래프 조회
# ==================


@tool
def get_equipment_context(chamber_id: str) -> EquipmentContextToolResult:
    """챔버 기준 장비·공정 그래프 context를 조회한다."""

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
    except Exception:
        logger.exception("get_equipment_context Tool dependency error")
        return fail(
            EquipmentContextToolResult,
            "DEPENDENCY_ERROR: 장비 그래프 context 의존성 오류",
        )
