"""Knowledge RAG 문서 검색 Service."""

from __future__ import annotations

from app.common.tool_contracts import DocumentHit
from app.knowledge.document_search import embed_query
from app.knowledge.repository import DocumentSearchRepository


class DocumentSearchService:
    """API와 Tool이 공유하는 문서 검색 application service."""

    def __init__(self, repository: DocumentSearchRepository) -> None:
        self._repository = repository

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        model_code: str | None = None,
    ) -> list[DocumentHit]:
        query_vector = embed_query(query)
        rows = self._repository.search(
            query_vector,
            top_k=top_k,
            model_code=model_code,
        )
        return [
            DocumentHit(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                title=row.title,
                section=row.section,
                score=row.score,
                content=row.content,
                model_code=row.model_code,
            )
            for row in rows
        ]
