from __future__ import annotations

import logging
import sys
from re import sub
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.exceptions import ErrorCode
from app.common.tool_contracts import DocumentHit as ToolDocumentHit
from app.common.tool_timeouts import DependencyTimeoutError
from app.knowledge import embedding
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.exceptions import EmbeddingModelNotReadyError
from app.knowledge.repository import DocumentRepository
from app.knowledge.router import get_document as get_document_api
from app.knowledge.router import router as knowledge_router
from app.knowledge.router import search_documents as search_documents_api
from app.knowledge.schemas import (
    DocumentChunkItem,
    DocumentDetailResponse,
    DocumentSearchRequest,
)
from app.knowledge.service import DocumentSearchService, DocumentService
from app.knowledge.tools import search_documents as search_documents_tool


class FakeRepository:
    def __init__(self) -> None:
        self.arguments: tuple[list[float], int, str | None] | None = None

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        model_code: str | None,
    ) -> list[dict[str, object]]:
        self.arguments = (query_vector, top_k, model_code)
        return [
            {
                "chunk_id": "DOC-SPEC-ET7500:cs2:0001",
                "document_id": "DOC-SPEC-ET7500",
                "title": "ET Guide",
                "section": "적용 범위",
                "score": 0.82,
                "content": "content",
                "model_code": "ET-7500",
            }
        ]


def test_service_embeds_query_and_returns_document_hits(monkeypatch: Any) -> None:
    repository = FakeRepository()
    monkeypatch.setattr("app.knowledge.service.embed_query", lambda query: [0.1, 0.2])

    hits = DocumentSearchService(repository).search(
        "etch",
        top_k=4,
        model_code=" et-7500 ",
    )

    assert repository.arguments == ([0.1, 0.2], 4, "ET-7500")
    assert hits == [
        ToolDocumentHit(
            chunk_id="DOC-SPEC-ET7500:cs2:0001",
            document_id="DOC-SPEC-ET7500",
            title="ET Guide",
            section="적용 범위",
            score=0.82,
            content="content",
            model_code="ET-7500",
        )
    ]


def test_embedding_model_is_loaded_once_from_local_cache(monkeypatch: Any) -> None:
    created: list[str] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, *, local_files_only: bool) -> None:
            created.append(f"{model_name}|local={local_files_only}")

    embedding.get_embedding_model.cache_clear()
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setenv(
        "EMBEDDING_MODEL_REVISION",
        "5617a9f61b028005a4858fdac845db406aefb181",
    )
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", ".")
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    first = embedding.get_embedding_model()
    second = embedding.get_embedding_model()

    assert first is second
    assert created == [f"{embedding.REPOSITORY_ROOT}|local=True"]
    embedding.get_embedding_model.cache_clear()


def test_embedding_model_missing_cache_raises_model_not_ready(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    missing_path = tmp_path / "missing-bge-m3"

    embedding.get_embedding_model.cache_clear()
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setenv(
        "EMBEDDING_MODEL_REVISION",
        "5617a9f61b028005a4858fdac845db406aefb181",
    )
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", str(missing_path))

    try:
        with caplog.at_level(logging.WARNING, logger="app.knowledge.embedding"):
            with pytest.raises(EmbeddingModelNotReadyError) as excinfo:
                embedding.get_embedding_model()

        assert str(excinfo.value) == "임베딩 모델이 준비되지 않았습니다."
        assert str(missing_path) not in str(excinfo.value)
        assert str(missing_path) in caplog.text
    finally:
        embedding.get_embedding_model.cache_clear()


def test_repository_search_uses_current_rag_schema_and_common_filter(
    monkeypatch: Any,
) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        def mappings(self) -> FakeResult:
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class FakeConnection:
        connection = SimpleNamespace(driver_connection=object())
        closed = False

        def close(self) -> None:
            self.closed = True

        def execute(self, sql: object, params: dict[str, object]) -> FakeResult:
            captured["sql"] = str(sql)
            captured["params"] = params
            return FakeResult()

    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection)

    monkeypatch.setattr("app.knowledge.document_search.register_vector", lambda _: None)

    assert DocumentSearchRepository(engine).search([0.1], model_code="ET-7500") == []

    normalized_sql = sub(r"\s+", " ", str(captured["sql"]))
    assert "JOIN document d ON d.doc_id = c.doc_id" in normalized_sql
    assert "CAST(:query_vector AS vector)" in normalized_sql
    assert "d.model_code = :model_code OR d.model_code = 'COMMON'" in normalized_sql
    assert "corpus_revision" not in normalized_sql
    assert "document_corpus" not in normalized_sql
    assert captured["params"] == {
        "query_vector": "[0.1]",
        "top_k": 4,
        "model_code": "ET-7500",
    }
    assert connection.closed is True


def test_repository_search_without_model_code_searches_all_documents(
    monkeypatch: Any,
) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        def mappings(self) -> FakeResult:
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class FakeConnection:
        connection = SimpleNamespace(driver_connection=object())
        closed = False

        def close(self) -> None:
            self.closed = True

        def execute(self, sql: object, params: dict[str, object]) -> FakeResult:
            captured["sql"] = str(sql)
            captured["params"] = params
            return FakeResult()

    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection)

    monkeypatch.setattr("app.knowledge.document_search.register_vector", lambda _: None)

    assert DocumentSearchRepository(engine).search([0.1], top_k=4) == []

    normalized_sql = sub(r"\s+", " ", str(captured["sql"]))
    assert "JOIN document d ON d.doc_id = c.doc_id" in normalized_sql
    assert "CAST(:query_vector AS vector)" in normalized_sql
    assert "d.model_code = :model_code OR d.model_code = 'COMMON'" not in normalized_sql
    assert "corpus_revision" not in normalized_sql
    assert "document_corpus" not in normalized_sql
    assert captured["params"] == {
        "query_vector": "[0.1]",
        "top_k": 4,
    }
    assert connection.closed is True


def test_repository_applies_timeout_before_vector_registration_and_query() -> None:
    events: list[str] = []

    class FakeResult:
        def mappings(self) -> FakeResult:
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class FakeConnection:
        connection = SimpleNamespace(driver_connection=object())
        closed = False

        def execute(self, sql: object, _params: dict[str, object]) -> FakeResult:
            if "set_config('statement_timeout'" in str(sql):
                events.append("timeout")
            else:
                events.append("query")
            return FakeResult()

        def close(self) -> None:
            self.closed = True
            events.append("close")

    connection = FakeConnection()
    repository = DocumentSearchRepository(
        SimpleNamespace(connect=lambda: connection),
        timeout_seconds=0.2,
        vector_registrar=lambda _: events.append("register_vector"),
    )

    assert repository.search([0.1]) == []
    assert events == ["timeout", "register_vector", "query", "close"]


def test_repository_maps_registration_query_cancel_and_closes_connection() -> None:
    from psycopg import errors as psycopg_errors

    class FakeResult:
        def mappings(self) -> FakeResult:
            return self

        def all(self) -> list[dict[str, object]]:
            return []

    class FakeConnection:
        connection = SimpleNamespace(driver_connection=object())
        closed = False

        def execute(self, _sql: object, _params: dict[str, object]) -> FakeResult:
            return FakeResult()

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()

    def canceled_registration(_: object) -> None:
        raise psycopg_errors.QueryCanceled("secret driver detail")

    repository = DocumentSearchRepository(
        SimpleNamespace(connect=lambda: connection),
        vector_registrar=canceled_registration,
    )

    with pytest.raises(DependencyTimeoutError) as raised:
        repository.search([0.1])

    assert raised.value.reason_code == "DB_STATEMENT_TIMEOUT"
    assert "secret" not in str(raised.value)
    assert connection.closed is True


def test_document_repository_get_document_meta_returns_document_row() -> None:
    captured: dict[str, object] = {}

    class FakeMappingResult:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows

        def mappings(self) -> FakeMappingResult:
            return self

        def first(self) -> dict[str, object] | None:
            return self.rows[0] if self.rows else None

        def all(self) -> list[dict[str, object]]:
            return self.rows

    class FakeConnection:
        closed = False

        def execute(
            self,
            sql: object,
            params: dict[str, object],
        ) -> FakeMappingResult:
            captured["sql"] = sub(r"\s+", " ", str(sql))
            captured["params"] = params
            return FakeMappingResult(
                [
                    {
                        "doc_id": "DOC-SPEC-PH9000",
                        "title": "PH Guide",
                        "doc_type": "SPEC",
                        "model_code": "PH-9000",
                        "source_path": "docs/knowledge/rag-corrected/ph.md",
                        "version": "v1",
                    },
                ]
            )

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection)

    document = DocumentRepository(engine).get_document_meta("DOC-SPEC-PH9000")

    assert document == {
        "doc_id": "DOC-SPEC-PH9000",
        "title": "PH Guide",
        "doc_type": "SPEC",
        "model_code": "PH-9000",
        "source_path": "docs/knowledge/rag-corrected/ph.md",
        "version": "v1",
    }
    assert "FROM document" in str(captured["sql"])
    assert captured["params"] == {"document_id": "DOC-SPEC-PH9000"}
    assert connection.closed is True


def test_document_repository_list_document_chunks_orders_by_sequence() -> None:
    captured: dict[str, object] = {}

    class FakeMappingResult:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows

        def mappings(self) -> FakeMappingResult:
            return self

        def all(self) -> list[dict[str, object]]:
            return self.rows

    class FakeConnection:
        closed = False

        def execute(
            self,
            sql: object,
            params: dict[str, object],
        ) -> FakeMappingResult:
            captured["sql"] = sub(r"\s+", " ", str(sql))
            captured["params"] = params
            return FakeMappingResult(
                [
                    {
                        "chunk_id": "DOC-SPEC-PH9000:cs2:0001",
                        "chunk_seq": 1,
                        "section_title": "1. 개요",
                        "content": "overview",
                    },
                    {
                        "chunk_id": "DOC-SPEC-PH9000:cs2:0002",
                        "chunk_seq": 2,
                        "section_title": "2. 기준",
                        "content": "limits",
                    },
                ]
            )

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection)

    chunks = DocumentRepository(engine).list_document_chunks("DOC-SPEC-PH9000")

    assert chunks == [
        {
            "chunk_id": "DOC-SPEC-PH9000:cs2:0001",
            "chunk_seq": 1,
            "section_title": "1. 개요",
            "content": "overview",
        },
        {
            "chunk_id": "DOC-SPEC-PH9000:cs2:0002",
            "chunk_seq": 2,
            "section_title": "2. 기준",
            "content": "limits",
        },
    ]
    assert "ORDER BY chunk_seq ASC, chunk_id ASC" in str(captured["sql"])
    assert captured["params"] == {"document_id": "DOC-SPEC-PH9000"}
    assert connection.closed is True


def test_document_repository_get_document_meta_returns_none() -> None:
    class FakeMappingResult:
        def mappings(self) -> FakeMappingResult:
            return self

        def first(self) -> None:
            return None

    class FakeConnection:
        closed = False

        def execute(self, sql: object, params: dict[str, object]) -> FakeMappingResult:
            return FakeMappingResult()

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection)

    assert DocumentRepository(engine).get_document_meta("missing") is None
    assert connection.closed is True


def test_document_service_builds_detail_response_with_chunk_items() -> None:
    class FakeRepository:
        def get_document_meta(self, document_id: str) -> dict[str, object]:
            assert document_id == "DOC-SPEC-PH9000"
            return {
                "doc_id": "DOC-SPEC-PH9000",
                "title": "PH Guide",
                "doc_type": "SPEC",
                "model_code": "PH-9000",
                "source_path": "docs/knowledge/rag-corrected/ph.md",
                "version": "v1",
            }

        def list_document_chunks(self, document_id: str) -> list[dict[str, object]]:
            assert document_id == "DOC-SPEC-PH9000"
            return [
                {
                    "chunk_id": "DOC-SPEC-PH9000:cs2:0001",
                    "chunk_seq": 1,
                    "section_title": "1. 개요",
                    "content": "overview",
                }
            ]

    document = DocumentService(FakeRepository()).get_document("DOC-SPEC-PH9000")

    assert document is not None
    assert document == DocumentDetailResponse(
        document_id="DOC-SPEC-PH9000",
        title="PH Guide",
        doc_type="SPEC",
        model_code="PH-9000",
        source_path="docs/knowledge/rag-corrected/ph.md",
        version="v1",
        chunks=[
            DocumentChunkItem(
                chunk_id="DOC-SPEC-PH9000:cs2:0001",
                chunk_seq=1,
                section_title="1. 개요",
                content="overview",
            )
        ],
    )
    assert isinstance(document.chunks[0], DocumentChunkItem)


def test_documents_search_api_returns_bare_array_with_doc_id_alias(
    monkeypatch: Any,
) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            assert (query, top_k, model_code) == ("check", 4, "ET-7500")
            return [
                ToolDocumentHit(
                    chunk_id="DOC-SPEC-ET7500:cs2:0001",
                    document_id="DOC-SPEC-ET7500",
                    title="ET Guide",
                    section="적용 범위",
                    score=0.82,
                    content="content",
                    model_code="ET-7500",
                )
            ]

    monkeypatch.setattr("app.knowledge.router.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.router.DocumentSearchService", FakeService)

    response = search_documents_api(
        DocumentSearchRequest(query="check", model_code="ET-7500")
    )

    assert [hit.model_dump() for hit in response] == [
        {
            "chunk_id": "DOC-SPEC-ET7500:cs2:0001",
            "document_id": "DOC-SPEC-ET7500",
            "doc_id": "DOC-SPEC-ET7500",
            "title": "ET Guide",
            "section": "적용 범위",
            "score": 0.82,
            "content": "content",
            "model_code": "ET-7500",
        }
    ]


def test_documents_search_openapi_response_is_bare_array() -> None:
    app = FastAPI()
    app.include_router(knowledge_router)

    schema = app.openapi()["paths"]["/documents/search"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]

    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/DocumentHit")


def test_documents_search_http_response_is_bare_array_with_doc_id_alias(
    monkeypatch: Any,
) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            assert (query, top_k, model_code) == ("check", 4, "ET-7500")
            return [
                ToolDocumentHit(
                    chunk_id="DOC-SPEC-ET7500:cs2:0001",
                    document_id="DOC-SPEC-ET7500",
                    title="ET Guide",
                    score=0.82,
                    content="content",
                    model_code="ET-7500",
                )
            ]

    app = FastAPI()
    app.include_router(knowledge_router)

    monkeypatch.setattr("app.knowledge.router.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.router.DocumentSearchService", FakeService)

    response = TestClient(app).post(
        "/documents/search",
        json={"query": "check", "model_code": "ET-7500"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "chunk_id": "DOC-SPEC-ET7500:cs2:0001",
            "document_id": "DOC-SPEC-ET7500",
            "doc_id": "DOC-SPEC-ET7500",
            "title": "ET Guide",
            "section": None,
            "score": 0.82,
            "content": "content",
            "model_code": "ET-7500",
        }
    ]


def test_documents_search_http_returns_model_not_ready(
    monkeypatch: Any,
) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class ModelNotReadyService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            raise EmbeddingModelNotReadyError()

    monkeypatch.setattr("app.knowledge.router.pool_factory", FakePoolFactory())
    monkeypatch.setattr(
        "app.knowledge.router.DocumentSearchService",
        ModelNotReadyService,
    )

    from app.main import app

    response = TestClient(app).post("/documents/search", json={"query": "check"})

    assert response.status_code == 503
    assert response.json() == {
        "code": ErrorCode.MODEL_NOT_READY.value,
        "message": "임베딩 모델이 준비되지 않았습니다.",
        "details": {},
    }


def test_get_document_api_returns_document_detail(monkeypatch: Any) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_document(self, document_id: str) -> DocumentDetailResponse:
            assert document_id == "DOC-SPEC-PH9000"
            return DocumentDetailResponse(
                document_id="DOC-SPEC-PH9000",
                title="PH Guide",
                doc_type="SPEC",
                model_code="PH-9000",
                source_path="docs/knowledge/rag-corrected/ph.md",
                version="v1",
                chunks=[
                    {
                        "chunk_id": "DOC-SPEC-PH9000:cs2:0001",
                        "chunk_seq": 1,
                        "section_title": "1. 개요",
                        "content": "overview",
                    }
                ],
            )

    monkeypatch.setattr("app.knowledge.router.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.router.DocumentService", FakeService)

    response = get_document_api("DOC-SPEC-PH9000")

    assert response.document_id == "DOC-SPEC-PH9000"
    assert response.chunks[0].chunk_id == "DOC-SPEC-PH9000:cs2:0001"


def test_get_document_http_returns_404_for_missing_document(
    monkeypatch: Any,
) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def get_document(self, document_id: str) -> None:
            assert document_id == "missing"
            return None

    app = FastAPI()
    app.include_router(knowledge_router)

    monkeypatch.setattr("app.knowledge.router.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.router.DocumentService", FakeService)

    response = TestClient(app).get("/documents/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "document not found"}


def test_search_documents_tool_returns_common_tool_contract(monkeypatch: Any) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class FakeService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            assert (query, top_k, model_code) == ("check", 4, "ET-7500")
            return [
                ToolDocumentHit(
                    chunk_id="DOC-SPEC-ET7500:cs2:0001",
                    document_id="DOC-SPEC-ET7500",
                    title="ET Guide",
                    score=0.82,
                    content="content",
                    model_code="ET-7500",
                )
            ]

    monkeypatch.setattr("app.knowledge.tools.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.tools.DocumentSearchService", FakeService)

    result = search_documents_tool.invoke(
        {"query": "check", "model_code": "ET-7500", "top_k": 4}
    )

    assert result.ok is True
    assert result.reason == ""
    assert result.hits[0].document_id == "DOC-SPEC-ET7500"
    assert "corpus_revision" not in result.hits[0].model_dump()


def test_search_documents_tool_treats_zero_hits_as_success(monkeypatch: Any) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class EmptyService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            assert (query, top_k, model_code) == ("check", 4, None)
            return []

    monkeypatch.setattr("app.knowledge.tools.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.tools.DocumentSearchService", EmptyService)

    result = search_documents_tool.invoke({"query": "check"})

    assert result.ok is True
    assert result.reason == ""
    assert result.hits == []


def test_search_documents_tool_returns_dependency_failure(monkeypatch: Any) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class FailingService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            raise RuntimeError(
                "SELECT * FROM document_chunk WHERE query='check' "
                "postgresql://user:secret@localhost/db"
            )

    monkeypatch.setattr("app.knowledge.tools.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.tools.DocumentSearchService", FailingService)

    result = search_documents_tool.invoke({"query": "check"})

    assert result.ok is False
    assert result.hits == []
    assert result.reason == "DEPENDENCY_ERROR: 문서 검색 의존성 오류"
    assert "SELECT" not in result.reason
    assert "postgresql://" not in result.reason
    assert "secret" not in result.reason


def test_search_documents_tool_returns_model_not_ready_failure(
    monkeypatch: Any,
) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class ModelNotReadyService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            raise EmbeddingModelNotReadyError()

    monkeypatch.setattr("app.knowledge.tools.pool_factory", FakePoolFactory())
    monkeypatch.setattr(
        "app.knowledge.tools.DocumentSearchService",
        ModelNotReadyService,
    )

    result = search_documents_tool.invoke({"query": "check"})

    assert result.ok is False
    assert result.hits == []
    assert result.reason == "MODEL_NOT_READY: 임베딩 모델이 준비되지 않았습니다"


def test_search_documents_tool_returns_timeout_failure(monkeypatch: Any) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class TimeoutService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            raise TimeoutError("검색 시간 초과")

    monkeypatch.setattr("app.knowledge.tools.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.tools.DocumentSearchService", TimeoutService)

    result = search_documents_tool.invoke({"query": "check"})

    assert result.ok is False
    assert result.hits == []
    assert result.reason == "TIMEOUT: 검색 시간 초과"


def test_search_documents_tool_maps_dependency_timeout_reason_code(
    monkeypatch: Any,
) -> None:
    class FakePoolFactory:
        def get_engine(self, logical_db: object, role: object) -> object:
            return object()

    class TimeoutService:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def search(
            self,
            query: str,
            *,
            top_k: int,
            model_code: str | None,
        ) -> list[ToolDocumentHit]:
            error = DependencyTimeoutError("DB_STATEMENT_TIMEOUT")
            error.args = ("postgresql://user:secret@localhost/db",)
            raise error

    monkeypatch.setattr("app.knowledge.tools.pool_factory", FakePoolFactory())
    monkeypatch.setattr("app.knowledge.tools.DocumentSearchService", TimeoutService)

    result = search_documents_tool.invoke({"query": "check"})

    assert result.ok is False
    assert result.hits == []
    assert result.reason == "TIMEOUT: DB_STATEMENT_TIMEOUT"
    assert "postgresql://" not in result.reason
    assert "secret" not in result.reason
