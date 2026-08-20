"""Knowledge 문서 검색 Tool."""

from __future__ import annotations

from langchain_core.tools import tool

from app.common.tool_contracts import DocumentSearchToolResult, fail
from app.knowledge.repository import DocumentSearchRepository
from app.knowledge.service import DocumentSearchService


@tool
def search_documents(
    query: str,
    model_code: str | None = None,
    top_k: int = 4,
) -> DocumentSearchToolResult:
    """질문과 관련된 RAG 문서 chunk를 검색한다."""

    try:
        service = DocumentSearchService(DocumentSearchRepository())
        hits = service.search(query, top_k=top_k, model_code=model_code)
        return DocumentSearchToolResult(ok=True, hits=hits)
    except Exception as exc:
        return fail(DocumentSearchToolResult, f"DEPENDENCY_ERROR: {exc}")
