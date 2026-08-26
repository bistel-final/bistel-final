"""문서 검색용 BGE-M3 질의 임베딩 생명주기를 관리한다."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.knowledge.exceptions import EmbeddingModelNotReadyError

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_EMBEDDING_MODEL = "BAAI/bge-m3"
EXPECTED_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EXPECTED_EMBEDDING_DIMENSION = 1024


@lru_cache(maxsize=1)
def get_embedding_model() -> Any:
    """현재 API 프로세스에서 BGE-M3 모델을 한 번만 생성한다."""

    load_dotenv(REPOSITORY_ROOT / ".env")

    model_id = os.getenv("EMBEDDING_MODEL", EXPECTED_EMBEDDING_MODEL).strip()
    revision = os.getenv(
        "EMBEDDING_MODEL_REVISION",
        EXPECTED_EMBEDDING_REVISION,
    ).strip()
    try:
        dimension = int(os.getenv("EMBEDDING_DIM", str(EXPECTED_EMBEDDING_DIMENSION)))
    except ValueError as exc:
        logger.warning(
            "EMBEDDING_DIM이 정수가 아닙니다: %s",
            os.getenv("EMBEDDING_DIM"),
        )
        raise EmbeddingModelNotReadyError() from exc
    model_path = os.getenv("EMBEDDING_MODEL_PATH", "backend/model-cache/bge-m3").strip()

    if model_id != EXPECTED_EMBEDDING_MODEL:
        logger.warning(
            "EMBEDDING_MODEL이 계약과 다릅니다: %s",
            model_id,
        )
        raise EmbeddingModelNotReadyError()
    if revision != EXPECTED_EMBEDDING_REVISION:
        logger.warning(
            "EMBEDDING_MODEL_REVISION이 계약과 다릅니다: %s",
            revision,
        )
        raise EmbeddingModelNotReadyError()
    if dimension != EXPECTED_EMBEDDING_DIMENSION:
        logger.warning(
            "EMBEDDING_DIM이 계약과 다릅니다: %s",
            dimension,
        )
        raise EmbeddingModelNotReadyError()

    cache_path = Path(model_path)
    if not cache_path.is_absolute():
        cache_path = REPOSITORY_ROOT / cache_path
    if not cache_path.exists():
        logger.warning("임베딩 모델 캐시 경로가 없습니다: %s", cache_path)
        raise EmbeddingModelNotReadyError()

    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(str(cache_path), local_files_only=True)
    except Exception as exc:
        logger.warning(
            "임베딩 모델을 로컬 캐시에서 로드할 수 없습니다: %s",
            cache_path,
        )
        raise EmbeddingModelNotReadyError() from exc


def embed_query(query: str) -> list[float]:
    """검색 질의를 정규화된 1024차원 BGE-M3 벡터로 변환한다."""

    vector = get_embedding_model().encode([query], normalize_embeddings=True)[0]
    return [float(value) for value in vector]
