from __future__ import annotations

import hashlib
import json
import re
import sys
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import load_rag_documents as loader  # noqa: E402
import rag_chunk_contract as chunk_contract  # noqa: E402


class _Result:
    rowcount = 0


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _MappingResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one(self) -> dict[str, Any]:
        if len(self.rows) != 1:
            raise LookupError("expected exactly one row")
        return self.rows[0]


class _Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, Any]] = []

    def exec_driver_sql(self, sql: str, parameters: Any = None) -> _Result:
        self.statements.append((" ".join(sql.split()), parameters))
        return _Result()


def _embedding(texts: list[str]) -> list[list[float]]:
    return [[0.01] * loader.EMBEDDING_DIMENSION for _ in texts]


def test_corrected_documents_are_loaded_from_canonical_source_dir() -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)

    assert [document.document_id for document in corpus.documents] == [
        "DOC-SPEC-ET7500",
        "DOC-SPEC-PH9000",
        "DOC-TROUBLE-FDC",
    ]
    assert len(corpus.chunks) == 33
    assert all(chunk.chunk_id.startswith(chunk.doc_id + ":cs2:") for chunk in corpus.chunks)
    assert all("corpus_revision" not in chunk.metadata_json for chunk in corpus.chunks)


def test_chunk_ids_are_deterministic_and_title_is_embedding_only() -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)

    first_by_doc = {
        document.document_id: next(
            chunk for chunk in corpus.chunks if chunk.doc_id == document.document_id
        )
        for document in corpus.documents
    }

    assert first_by_doc["DOC-SPEC-ET7500"].chunk_id == "DOC-SPEC-ET7500:cs2:0001"
    assert first_by_doc["DOC-SPEC-PH9000"].chunk_id == "DOC-SPEC-PH9000:cs2:0001"
    assert first_by_doc["DOC-TROUBLE-FDC"].chunk_id == "DOC-TROUBLE-FDC:cs2:0001"
    assert first_by_doc["DOC-SPEC-ET7500"].content.strip()
    assert "ET-7500 Dry Etcher" not in first_by_doc["DOC-SPEC-ET7500"].content
    assert "ET-7500 Dry Etcher" in first_by_doc["DOC-SPEC-ET7500"].embedding_input


def test_h3_chunks_keep_parent_h2_context_without_short_piece_merge() -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)
    et_chunks = [chunk for chunk in corpus.chunks if chunk.doc_id == "DOC-SPEC-ET7500"]

    reflected_power = next(
        chunk for chunk in et_chunks if "4.2 Reflected Power" in chunk.section_title
    )
    assert reflected_power.section_title == (
        "4. 파라미터별 상세 > 4.2 Reflected Power (`ET_REFL`)"
    )
    assert "4.1 Chamber Pressure" not in reflected_power.content


def test_chunk_contract_uses_cs2_title_hierarchy_rules() -> None:
    assert loader.CHUNK_SCHEMA_VERSION == "cs2"
    assert loader.CHUNK_CONTRACT_SHA256 == chunk_contract.CHUNK_CONTRACT_SHA256


def test_design_document_chunk_contract_matches_loader_contract() -> None:
    design_path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "specifications"
        / "시스템설계서_v2_1_작업본.md"
    )
    design = design_path.read_text(encoding="utf-8")
    json_match = re.search(
        r"hash 입력 JSON은 다음 한 줄과 정확히 같다\.\s*```json\s*(\{.+?\})\s*```",
        design,
        re.DOTALL,
    )
    hash_match = re.search(r"`([0-9a-f]{64})`다\. 규칙이 하나라도", design)

    assert json_match is not None
    assert hash_match is not None
    declared_contract = json.loads(json_match.group(1))
    declared_json = chunk_contract.canonical_contract_json(declared_contract)
    declared_hash = hashlib.sha256(declared_json.encode("utf-8")).hexdigest()

    assert declared_contract == chunk_contract.CHUNK_CONTRACT
    assert declared_hash == hash_match.group(1)
    assert declared_hash == loader.CHUNK_CONTRACT_SHA256


def test_target_allowlist_excludes_evaluation_database() -> None:
    with pytest.raises(loader.TargetValidationError):
        loader.validate_rag_target("kosa_text2sql")


def test_cli_requires_explicit_database_and_source_dir() -> None:
    with pytest.raises(SystemExit):
        loader.parse_args([])

    args = loader.parse_args(
        [
            "--database",
            "kosa_agent",
            "--confirm-target",
            "kosa_agent",
            "--source-dir",
            str(loader.DEFAULT_CORRECTED_RAG_DIR),
        ]
    )
    assert args.database == "kosa_agent"
    assert args.confirm_target == "kosa_agent"
    assert args.source_dir == loader.DEFAULT_CORRECTED_RAG_DIR


def test_cli_rejects_mismatched_confirm_target() -> None:
    with pytest.raises(loader.RagLoadError):
        loader.parse_args(
            [
                "--database",
                "kosa_agent",
                "--confirm-target",
                "kosa_agent_e2e",
                "--source-dir",
                str(loader.DEFAULT_CORRECTED_RAG_DIR),
            ]
        )


def test_load_corpus_replaces_only_canonical_documents() -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)
    connection = _Connection()

    inserted = loader.load_corpus(connection, corpus, encode=_embedding)

    sql_text = "\n".join(sql for sql, _ in connection.statements)
    assert inserted == len(corpus.documents) + len(corpus.chunks)
    assert "DELETE FROM document WHERE doc_id = ANY" in sql_text
    assert "DELETE FROM document_chunk" not in sql_text
    assert "TRUNCATE" not in sql_text
    assert "document_corpus" not in sql_text
    delete_parameters = connection.statements[0][1]
    assert delete_parameters == {
        "document_ids": [
            "DOC-SPEC-ET7500",
            "DOC-SPEC-PH9000",
            "DOC-TROUBLE-FDC",
        ]
    }


def test_embedding_dimension_is_checked() -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)

    with pytest.raises(loader.RagLoadError):
        loader.load_corpus(_Connection(), corpus, encode=lambda texts: [[0.01]])


def test_preflight_schema_accepts_exact_document_tables() -> None:
    class Connection:
        def exec_driver_sql(self, _sql: str, _parameters: Any = None) -> _MappingResult:
            rows = [
                {"table_name": table, "column_name": column}
                for table, columns in {
                    "document": [
                        "doc_id",
                        "title",
                        "doc_type",
                        "model_code",
                        "source_path",
                        "version",
                        "created_at",
                    ],
                    "document_chunk": [
                        "chunk_id",
                        "doc_id",
                        "chunk_seq",
                        "section_title",
                        "content",
                        "token_cnt",
                        "embedding",
                        "metadata_json",
                    ],
                }.items()
                for column in columns
            ]
            return _MappingResult(rows)

    loader.preflight_schema(Connection())


def test_preflight_schema_rejects_corpus_revision_column() -> None:
    class Connection:
        def exec_driver_sql(self, _sql: str, _parameters: Any = None) -> _MappingResult:
            return _MappingResult(
                [
                    {"table_name": "document", "column_name": "doc_id"},
                    {"table_name": "document", "column_name": "corpus_revision"},
                ]
            )

    with pytest.raises(loader.RagLoadError):
        loader.preflight_schema(Connection())


def test_marker_records_committed_load_without_revision_aliases() -> None:
    corpus = loader.prepare_corpus(loader.DEFAULT_CORRECTED_RAG_DIR)
    target = loader.BootstrapTarget(
        "db.example.internal",
        5432,
        "bootstrap_ddl",
        "hidden",
        "kosa_agent",
        "runtime",
    )
    verification = loader.PostLoadVerification(
        document_count=3,
        chunk_count=len(corpus.chunks),
        null_embedding_count=0,
        live_db_fingerprint_sha256="a" * 64,
        search_smoke=(
            {
                "query": "PH-9000 Photo Scanner 적용 범위",
                "model_code": "PH-9000",
                "expected_document_id": "DOC-SPEC-PH9000",
                "top_document_ids": ["DOC-SPEC-PH9000"],
                "passed": True,
            },
        ),
    )

    marker = loader.build_marker(target, corpus, verification)

    assert marker["artifact_type"] == "rag_load_marker"
    assert marker["status"] == "COMMITTED"
    assert marker["document_ids"] == list(loader.CANONICAL_DOCUMENT_IDS)
    assert marker["dimension"] == 1024
    assert "corpus_revision" not in str(marker)
    assert "graph_revision" not in str(marker)
