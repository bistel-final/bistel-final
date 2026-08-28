"""Knowledge Agent Tool 정의."""

from __future__ import annotations

import logging

from langchain_core.tools import tool
from pydantic import ValidationError

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.common.tool_contracts import (
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    fail,
)
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.exceptions import EmbeddingModelNotReadyError
from app.knowledge.graph_query import GraphQueryRepository
from app.knowledge.service import DocumentSearchService, EquipmentContextService

logger = logging.getLogger(__name__)


# ==================
# 문서 RAG
# ==================
@tool(args_schema=DocumentSearchToolInput)
def search_documents(
    query: str,
    model_code: str | None = None,
    top_k: int = 4,
) -> DocumentSearchToolResult:
    """장비 SPEC과 FDC Troubleshooting 문서에서 근거 청크를 검색한다.

    알람 해석, 파라미터 한계값, 의심 원인, 권고 조치처럼 문서 근거가
    필요한 경우 사용한다. 장비 모델이 명확하면 model_code를 지정해
    해당 모델 문서와 COMMON 문서를 함께 검색하고, 모델이 불명확하면
    생략해 전체 문서를 검색한다. 검색 결과는 유사도 순 문서 청크이며,
    결과가 0건이어도 정상 성공 응답이다.
    """

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


@tool(args_schema=EquipmentContextToolInput)
def get_equipment_context(chamber_id: str) -> EquipmentContextToolResult:
    """Chamber 기준 장비·공정 Graph context를 조회한다.

    특정 Chamber의 설비, 장비 모델, Area, Process Step, 형제 Chamber,
    관련 파라미터, upstream/downstream 공정 맥락처럼 구조화된 관계
    정보가 필요할 때 사용한다. 문서 설명이나 조치 기준이 필요하면
    search_documents를 사용하고, 설비·공정 관계 확인이 필요하면 이
    Tool을 사용한다. 응답은 Agent 판단용 compact payload이며 Neo4j
    raw 정보나 내부 ID는 노출하지 않는다.
    """

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
