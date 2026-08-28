from __future__ import annotations

# ruff: noqa: E402,I001

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from app.common import rag_readiness
import load_rag_documents as loader  # noqa: E402

RUNTIME_DATABASES = ("kosa_agent", "kosa_agent_e2e", "kosa_text2sql")


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one(self) -> dict[str, Any]:
        if len(self.rows) != 1:
            raise LookupError("expected one row")
        return self.rows[0]


class _Connection:
    def __init__(self, marker: dict[str, Any]) -> None:
        self.database = str(marker["database"])
        self.documents = [
            {
                "doc_id": doc_id,
                "title": f"{doc_id} title",
                "doc_type": "SPEC" if "SPEC" in doc_id else "TROUBLESHOOT",
                "model_code": None,
                "source_path": f"docs/knowledge/rag-corrected/{doc_id}.md",
                "version": "corrected",
            }
            for doc_id in marker["document_ids"]
        ]
        self.chunks = [
            {
                "chunk_id": f"{doc_id}:cs2:0001",
                "doc_id": doc_id,
                "chunk_seq": 1,
                "section_title": "개요",
                "content": f"{doc_id} body",
                "token_cnt": 3,
                "metadata_json": {"chunk_schema_version": "cs2"},
            }
            for doc_id in marker["document_ids"]
        ]

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Rows:
        normalized = " ".join(sql.split())
        if "current_database()" in normalized:
            return _Rows([{"database": self.database}])
        if normalized.startswith("SELECT doc_id, title"):
            return _Rows(self.documents)
        if normalized.startswith("SELECT chunk_id, doc_id"):
            return _Rows(self.chunks)
        if normalized.startswith("SELECT count(*) AS value FROM document WHERE"):
            return _Rows([{"value": len(self.documents)}])
        if "embedding IS NULL OR vector_dims" in normalized:
            return _Rows([{"value": 0}])
        if normalized.startswith("SELECT count(*) AS value FROM document_chunk WHERE"):
            return _Rows([{"value": len(self.chunks)}])
        raise AssertionError(f"unexpected SQL: {normalized}")


class _CorpusConnection:
    def __init__(
        self, corpus: loader.PreparedRagCorpus, database: str = "kosa_agent"
    ) -> None:
        self.database = database
        self.documents = [
            {
                "doc_id": document.document_id,
                "title": document.title,
                "doc_type": document.doc_type,
                "model_code": document.model_code,
                "source_path": document.source_path,
                "version": document.version,
            }
            for document in corpus.documents
        ]
        self.chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "chunk_seq": chunk.chunk_seq,
                "section_title": chunk.section_title,
                "content": chunk.content,
                "token_cnt": chunk.token_cnt,
                "metadata_json": chunk.metadata_json,
            }
            for chunk in corpus.chunks
        ]

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Rows:
        normalized = " ".join(sql.split())
        document_ids = set((parameters or {}).get("document_ids", []))
        if "current_database()" in normalized:
            return _Rows([{"database": self.database}])
        if normalized.startswith("SELECT doc_id, title"):
            return _Rows(
                sorted(
                    [
                        document
                        for document in self.documents
                        if document["doc_id"] in document_ids
                    ],
                    key=lambda item: item["doc_id"],
                )
            )
        if normalized.startswith("SELECT chunk_id, doc_id"):
            return _Rows(
                sorted(
                    [chunk for chunk in self.chunks if chunk["doc_id"] in document_ids],
                    key=lambda item: (
                        item["doc_id"],
                        item["chunk_seq"],
                        item["chunk_id"],
                    ),
                )
            )
        if normalized.startswith("SELECT count(*) AS value FROM document WHERE"):
            return _Rows(
                [
                    {
                        "value": sum(
                            1
                            for document in self.documents
                            if document["doc_id"] in document_ids
                        )
                    }
                ]
            )
        if "embedding IS NULL OR vector_dims" in normalized:
            return _Rows([{"value": 0}])
        if normalized.startswith("SELECT count(*) AS value FROM document_chunk WHERE"):
            return _Rows(
                [
                    {
                        "value": sum(
                            1
                            for chunk in self.chunks
                            if chunk["doc_id"] in document_ids
                        )
                    }
                ]
            )
        raise AssertionError(f"unexpected SQL: {normalized}")


def _marker(database: str = "kosa_agent") -> dict[str, Any]:
    return {
        "artifact_type": "rag_load_marker",
        "chunk_contract_sha256": rag_readiness.CHUNK_CONTRACT_SHA256,
        "chunk_count": 3,
        "chunk_schema_version": rag_readiness.CHUNK_SCHEMA_VERSION,
        "corrected_sha256_by_document": {
            doc_id: "a" * 64 for doc_id in rag_readiness.CANONICAL_DOCUMENT_IDS
        },
        "correction_reason_by_document": rag_readiness.CORRECTION_REASON_BY_DOCUMENT,
        "database": database,
        "dimension": rag_readiness.EMBEDDING_DIMENSION,
        "document_count": 3,
        "document_ids": list(rag_readiness.CANONICAL_DOCUMENT_IDS),
        "embedding_model": rag_readiness.EMBEDDING_MODEL,
        "embedding_model_revision": rag_readiness.EMBEDDING_MODEL_REVISION,
        "embedding_weights_sha256": rag_readiness.EMBEDDING_WEIGHTS_SHA256,
        "format_version": 1,
        "live_db_fingerprint_sha256": "",
        "null_embedding_count": 0,
        "profile": "runtime",
        "recorded_at": "2026-08-28T00:00:00+00:00",
        "schema_sha256": "b" * 64,
        "search_smoke": [
            {"query": "q1", "passed": True},
            {"query": "q2", "passed": True},
            {"query": "q3", "passed": True},
        ],
        "source_sha256_by_document": rag_readiness.SOURCE_SHA256_BY_DOCUMENT,
        "status": "COMMITTED",
    }


def test_rag_readiness_passes_with_matching_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _marker()
    connection = _Connection(marker)
    marker["live_db_fingerprint_sha256"] = rag_readiness.live_fingerprint(
        connection,
        marker["document_ids"],
    )
    (tmp_path / "rag_load.kosa_agent.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag_readiness, "MARKER_ROOT", tmp_path)

    rag_readiness.verify_rag_readiness(connection)


def test_rag_readiness_accepts_loader_committed_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)
    connection = _CorpusConnection(corpus)
    verification = loader.PostLoadVerification(
        document_count=len(corpus.documents),
        chunk_count=len(corpus.chunks),
        null_embedding_count=0,
        live_db_fingerprint_sha256=loader.live_fingerprint(
            connection,
            loader.CANONICAL_DOCUMENT_IDS,
        ),
        search_smoke=(
            {"query": "q1", "passed": True},
            {"query": "q2", "passed": True},
            {"query": "q3", "passed": True},
        ),
    )
    marker = loader.build_marker(
        loader.BootstrapTarget(
            "db.example.internal",
            5432,
            "bootstrap_ddl",
            "hidden",
            "kosa_agent",
            "runtime",
        ),
        corpus,
        verification,
    )
    (tmp_path / "rag_load.kosa_agent.json").write_text(
        json.dumps(marker, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag_readiness, "MARKER_ROOT", tmp_path)

    assert (
        rag_readiness.live_fingerprint(
            connection,
            marker["document_ids"],
        )
        == marker["live_db_fingerprint_sha256"]
    )
    rag_readiness.verify_rag_readiness(connection)


def test_rag_readiness_rejects_stale_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _marker()
    marker["live_db_fingerprint_sha256"] = "0" * 64
    (tmp_path / "rag_load.kosa_agent.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag_readiness, "MARKER_ROOT", tmp_path)

    with pytest.raises(rag_readiness.RagReadinessError, match="fingerprint"):
        rag_readiness.verify_rag_readiness(_Connection(marker))


def test_rag_readiness_rejects_non_mapping_search_smoke() -> None:
    marker = _marker()
    marker["search_smoke"] = ["pass", "pass", "pass"]

    with pytest.raises(rag_readiness.RagReadinessError, match="검색 smoke"):
        rag_readiness.validate_marker(marker, database="kosa_agent")


def test_packaged_rag_markers_match_repository_markers() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    source_root = repository_root / "infra" / "bootstrap" / "markers"
    for database in RUNTIME_DATABASES:
        filename = f"rag_load.{database}.json"
        assert (rag_readiness.PACKAGED_MARKER_ROOT / filename).read_bytes() == (
            source_root / filename
        ).read_bytes()


def test_packaged_marker_root_is_used_when_repository_marker_root_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _marker()
    package_root = tmp_path / "package-markers"
    package_root.mkdir()
    (package_root / "rag_load.kosa_agent.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    monkeypatch.delenv("RAG_MARKER_ROOT", raising=False)
    monkeypatch.setattr(rag_readiness, "MARKER_ROOT", tmp_path / "missing")
    monkeypatch.setattr(rag_readiness, "PACKAGED_MARKER_ROOT", package_root)

    assert rag_readiness.load_marker("kosa_agent")["database"] == "kosa_agent"


def test_rag_readiness_rejects_missing_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _marker()
    marker.pop("correction_reason_by_document")
    (tmp_path / "rag_load.kosa_agent.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag_readiness, "MARKER_ROOT", tmp_path)

    with pytest.raises(rag_readiness.RagReadinessError, match="key"):
        rag_readiness.verify_rag_readiness(_Connection(_marker()))
