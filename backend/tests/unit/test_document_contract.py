"""RAG schema, Knowledge DTO, corrected source 계약을 검증한다."""

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
CORRECTED_RAG_DIR = REPOSITORY_ROOT / "docs" / "knowledge" / "rag-corrected"


def test_document_type_matches_database_check() -> None:
    assert {item.value for item in DocumentType} == {
        "SPEC",
        "MANUAL",
        "TROUBLESHOOT",
    }
    assert "doc_type IN ('SPEC','MANUAL','TROUBLESHOOT')" in SQL


def test_database_names_map_to_api_names_without_schema_aliases() -> None:
    detail_fields = set(DocumentDetailResponse.model_fields)
    chunk_fields = set(DocumentChunkItem.model_fields)

    assert {"document_id", "source_path", "version"} <= detail_fields
    assert "corpus_revision" not in detail_fields
    assert {"chunk_id", "chunk_seq", "section_title", "content"} <= chunk_fields
    assert "doc_id" in SQL
    assert "document_id" not in SQL


def test_pgvector_and_mentor_document_schema_are_explicit() -> None:
    assert "embedding      vector(1024)" in SQL
    assert "CREATE TABLE document_corpus" not in SQL
    assert "corpus_revision" not in SQL
    assert "REFERENCES document(doc_id) ON DELETE CASCADE" in SQL


def test_corrected_rag_sources_exist_with_canonical_ids() -> None:
    files = sorted(path.name for path in CORRECTED_RAG_DIR.glob("*.md"))
    assert files == [
        "SPEC_ET-7500_DryEtcher.md",
        "SPEC_PH-9000_PhotoScanner.md",
        "TROUBLE_FDC_FaultGuide.md",
    ]

    contents = {
        path.name: path.read_text(encoding="utf-8")
        for path in CORRECTED_RAG_DIR.glob("*.md")
    }
    joined = "\n".join(contents.values())

    for doc_id in ("DOC-SPEC-PH9000", "DOC-SPEC-ET7500", "DOC-TROUBLE-FDC"):
        assert f"doc_id: {doc_id}" in joined

    assert "EQP01`, `EQP02`, `EQP03`" in contents[
        "SPEC_PH-9000_PhotoScanner.md"
    ]
    assert "RECIPE01`, `RECIPE03`" in contents["SPEC_PH-9000_PhotoScanner.md"]
    assert "EQP04`, `EQP05`, `EQP06`" in contents[
        "SPEC_ET-7500_DryEtcher.md"
    ]
    assert "RECIPE02`, `RECIPE04`" in contents["SPEC_ET-7500_DryEtcher.md"]
    assert "`OTH`" in contents["TROUBLE_FDC_FaultGuide.md"]


def test_corrected_rag_sources_remove_forbidden_legacy_phrases() -> None:
    joined = "\n".join(
        path.read_text(encoding="utf-8") for path in CORRECTED_RAG_DIR.glob("*.md")
    )
    forbidden = (
        "EQP01 → EQP04",
        "LOT_HOLD",
        "조치 상향",
        "조치 하향",
        "metrology",
        "anomaly_score",
        "후속 정상",
        "여러 LOT",
    )
    for phrase in forbidden:
        assert phrase not in joined
