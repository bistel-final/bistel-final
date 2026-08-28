from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.common import rag_readiness


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "_Rows":
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
