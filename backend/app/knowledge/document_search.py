"""Knowledge RAG 문서 검색 저장소."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pgvector.psycopg import register_vector
from sqlalchemy import text
from sqlalchemy.engine import Engine


def _format_vector(values: Sequence[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


class DocumentSearchRepository:
    """runtime ``kosa_agent``의 pgvector RAG chunk를 cosine distance로 검색한다."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 4,
        model_code: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT c.chunk_id,
                   d.doc_id AS document_id,
                   d.title,
                   c.section_title AS section,
                   1 - (c.embedding <=> CAST(:query_vector AS vector)) AS score,
                   c.content,
                   d.model_code
              FROM document_chunk c
              JOIN document d ON d.doc_id = c.doc_id
             WHERE c.embedding IS NOT NULL
        """
        params: dict[str, object] = {
            "query_vector": _format_vector(query_vector),
            "top_k": top_k,
        }

        if model_code is not None:
            sql += """
               AND (d.model_code = :model_code OR d.model_code = 'COMMON')
            """
            params["model_code"] = model_code

        sql += """
             ORDER BY c.embedding <=> CAST(:query_vector AS vector),
                      d.doc_id ASC,
                      c.chunk_id ASC
             LIMIT :top_k
        """

        connection = self._engine.connect()
        try:
            register_vector(connection.connection.driver_connection)
            rows = connection.execute(text(sql), params).mappings().all()
        finally:
            connection.close()

        return [
            {
                "chunk_id": str(row["chunk_id"]),
                "document_id": str(row["document_id"]),
                "title": str(row["title"]),
                "section": str(row["section"]) if row["section"] is not None else None,
                "score": float(row["score"]),
                "content": str(row["content"]),
                "model_code": (
                    str(row["model_code"]) if row["model_code"] is not None else None
                ),
            }
            for row in rows
        ]
