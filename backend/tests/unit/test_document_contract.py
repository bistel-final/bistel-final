"""001 RAG table과 현재 Knowledge DTO의 이름 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from app.knowledge.schemas import (
    DocumentChunkItem,
    DocumentDetailResponse,
    DocumentType,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SQL = (
    REPOSITORY_ROOT / "backend" / "migrations" / "001_reference_extensions.sql"
).read_text(encoding="utf-8")


def test_document_type_matches_database_check() -> None:
    assert {item.value for item in DocumentType} == {
        "SPEC",
        "MANUAL",
        "TROUBLESHOOT",
    }
    assert "doc_type IN ('SPEC', 'MANUAL', 'TROUBLESHOOT')" in SQL


def test_database_names_map_to_api_names_without_schema_aliases() -> None:
    detail_fields = set(DocumentDetailResponse.model_fields)
    chunk_fields = set(DocumentChunkItem.model_fields)

    assert {"document_id", "corpus_revision", "source_path", "version"} <= (
        detail_fields
    )
    assert {"chunk_id", "chunk_seq", "section_title", "content"} <= chunk_fields
    assert "doc_id" in SQL
    assert "document_id" not in SQL


def test_pgvector_and_active_revision_constraints_are_explicit() -> None:
    assert "embedding       vector(1024) NOT NULL" in SQL
    assert "embedding_dim = 1024" in SQL
    assert "CREATE UNIQUE INDEX ux_document_corpus_active" in SQL
    assert "WHERE status = 'ACTIVE'" in SQL
