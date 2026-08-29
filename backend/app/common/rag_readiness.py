from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Connection

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MARKER_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
PACKAGED_MARKER_ROOT = Path(__file__).resolve().parent / "rag_markers"

CANONICAL_DOCUMENT_IDS = (
    "DOC-SPEC-ET7500",
    "DOC-SPEC-PH9000",
    "DOC-TROUBLE-FDC",
)
CHUNK_SCHEMA_VERSION = "cs2"
CHUNK_CONTRACT_SHA256 = (
    "1b6571df8a8c3fdcd4de8f7f7184340273184ea72b14b3dbc1ee6e1b8c7f266b"
)
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_WEIGHTS_SHA256 = (
    "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38"
)
EMBEDDING_DIMENSION = 1024
DATABASE_PROFILE = {
    "kosa_agent": "runtime",
    "kosa_agent_e2e": "runtime",
    "kosa_text2sql": "evaluation",
}
SOURCE_SHA256_BY_DOCUMENT = {
    "DOC-SPEC-ET7500": (
        "f3f5e04db8a06fc2f14f8b65422b3647a1fcda46a4e32dd5252bb1010076720f"
    ),
    "DOC-SPEC-PH9000": (
        "a1ee6bd6a1410d389ed80a6937251f4d6a46aacb8109773a7797af6781c7d07a"
    ),
    "DOC-TROUBLE-FDC": (
        "5a44e862faaf6f16a4aad103ecd6f37825a1387c14d18a9c09b32d0b90e97289"
    ),
}
CORRECTION_REASON_BY_DOCUMENT = {
    document_id: (
        "원본 RAG의 머리말과 메타성 안내가 검색 chunk 본문에 섞여 문서 검색 품질을 "
        "떨어뜨렸으므로, 최종 FDC 계약에 맞는 본문 중심 corrected source를 적재한다."
    )
    for document_id in SOURCE_SHA256_BY_DOCUMENT
}

MARKER_REQUIRED_KEYS = frozenset(
    {
        "artifact_type",
        "chunk_contract_sha256",
        "chunk_count",
        "chunk_schema_version",
        "corrected_sha256_by_document",
        "correction_reason_by_document",
        "database",
        "dimension",
        "document_count",
        "document_ids",
        "embedding_model",
        "embedding_model_revision",
        "embedding_weights_sha256",
        "format_version",
        "live_db_fingerprint_sha256",
        "null_embedding_count",
        "profile",
        "recorded_at",
        "schema_sha256",
        "search_smoke",
        "source_sha256_by_document",
        "status",
    }
)


class RagReadinessError(RuntimeError):
    """RAG marker와 live DB 상태가 readiness 계약을 만족하지 않는다."""


def _canonical_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return unicodedata.normalize("NFC", value)
        return _canonical_json(parsed)
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_canonical_json(item) for item in value]
    return value


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        _canonical_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mapping_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _current_database(connection: Connection) -> str:
    row = (
        connection.exec_driver_sql("SELECT current_database() AS database")
        .mappings()
        .one()
    )
    return str(row["database"])


def _marker_root() -> Path:
    override = os.getenv("RAG_MARKER_ROOT", "").strip()
    if override:
        return Path(override)
    if MARKER_ROOT.exists():
        return MARKER_ROOT
    return PACKAGED_MARKER_ROOT


def marker_path(database: str) -> Path:
    if database not in DATABASE_PROFILE:
        raise RagReadinessError("RAG marker database가 허용되지 않았습니다")
    return _marker_root() / f"rag_load.{database}.json"


def load_marker(database: str) -> dict[str, Any]:
    path = marker_path(database)
    if not path.exists():
        raise RagReadinessError("RAG marker가 없습니다")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) != MARKER_REQUIRED_KEYS:
        raise RagReadinessError("RAG marker key 집합이 계약과 다릅니다")
    return payload


def validate_marker(marker: Mapping[str, Any], *, database: str) -> None:
    try:
        profile = DATABASE_PROFILE[database]
    except KeyError as exc:
        raise RagReadinessError("RAG marker database가 허용되지 않았습니다") from exc
    expected = {
        "artifact_type": "rag_load_marker",
        "format_version": 1,
        "status": "COMMITTED",
        "database": database,
        "profile": profile,
        "document_ids": list(CANONICAL_DOCUMENT_IDS),
        "source_sha256_by_document": SOURCE_SHA256_BY_DOCUMENT,
        "correction_reason_by_document": CORRECTION_REASON_BY_DOCUMENT,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_contract_sha256": CHUNK_CONTRACT_SHA256,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_revision": EMBEDDING_MODEL_REVISION,
        "embedding_weights_sha256": EMBEDDING_WEIGHTS_SHA256,
        "dimension": EMBEDDING_DIMENSION,
        "document_count": 3,
        "null_embedding_count": 0,
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise RagReadinessError(f"RAG marker 계약 위반: {key}")
    smoke = marker.get("search_smoke")
    if not isinstance(smoke, list) or len(smoke) != 3:
        raise RagReadinessError("RAG 검색 smoke marker가 3건이 아닙니다")
    if any(
        not isinstance(item, Mapping) or item.get("passed") is not True
        for item in smoke
    ):
        raise RagReadinessError("RAG 검색 smoke marker에 실패가 있습니다")


def live_fingerprint(connection: Connection, document_ids: Sequence[str]) -> str:
    documents = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT doc_id, title, doc_type, model_code, source_path, version
              FROM document
             WHERE doc_id = ANY (%(document_ids)s)
             ORDER BY doc_id
            """,
            {"document_ids": list(document_ids)},
        )
    )
    chunks = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT chunk_id, doc_id, chunk_seq, section_title, content, token_cnt,
                   metadata_json
              FROM document_chunk
             WHERE doc_id = ANY (%(document_ids)s)
             ORDER BY doc_id, chunk_seq, chunk_id
            """,
            {"document_ids": list(document_ids)},
        )
    )
    return _canonical_sha256(
        {
            "documents": [_canonical_json(row) for row in documents],
            "chunks": [_canonical_json(row) for row in chunks],
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "chunk_contract_sha256": CHUNK_CONTRACT_SHA256,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_model_revision": EMBEDDING_MODEL_REVISION,
            "embedding_dimension": EMBEDDING_DIMENSION,
        }
    )


def verify_live_state(connection: Connection, marker: Mapping[str, Any]) -> None:
    document_ids = marker["document_ids"]
    document_count = int(
        connection.exec_driver_sql(
            """
            SELECT count(*) AS value
              FROM document
             WHERE doc_id = ANY (%(document_ids)s)
            """,
            {"document_ids": document_ids},
        )
        .mappings()
        .one()["value"]
    )
    chunk_count = int(
        connection.exec_driver_sql(
            """
            SELECT count(*) AS value
              FROM document_chunk
             WHERE doc_id = ANY (%(document_ids)s)
            """,
            {"document_ids": document_ids},
        )
        .mappings()
        .one()["value"]
    )
    broken_embeddings = int(
        connection.exec_driver_sql(
            """
            SELECT count(*) AS value
              FROM document_chunk
             WHERE doc_id = ANY (%(document_ids)s)
               AND (embedding IS NULL OR vector_dims(embedding) <> 1024)
            """,
            {"document_ids": document_ids},
        )
        .mappings()
        .one()["value"]
    )
    if document_count != marker["document_count"]:
        raise RagReadinessError("RAG document 수가 marker와 다릅니다")
    if chunk_count != marker["chunk_count"]:
        raise RagReadinessError("RAG chunk 수가 marker와 다릅니다")
    if broken_embeddings != 0:
        raise RagReadinessError("RAG embedding NULL 또는 dimension 오류가 있습니다")
    if (
        live_fingerprint(connection, document_ids)
        != marker["live_db_fingerprint_sha256"]
    ):
        raise RagReadinessError("RAG live fingerprint가 marker와 다릅니다")


def verify_rag_readiness(connection: Connection) -> None:
    database = _current_database(connection)
    marker = load_marker(database)
    validate_marker(marker, database=database)
    verify_live_state(connection, marker)
