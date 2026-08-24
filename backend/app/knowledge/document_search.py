"""Document search compatibility exports."""

from __future__ import annotations

from app.knowledge.embedding import (
    EXPECTED_EMBEDDING_DIMENSION,
    EXPECTED_EMBEDDING_MODEL,
    EXPECTED_EMBEDDING_REVISION,
    REPOSITORY_ROOT,
    embed_query,
    get_embedding_model,
)

__all__ = [
    "EXPECTED_EMBEDDING_DIMENSION",
    "EXPECTED_EMBEDDING_MODEL",
    "EXPECTED_EMBEDDING_REVISION",
    "REPOSITORY_ROOT",
    "embed_query",
    "get_embedding_model",
]
