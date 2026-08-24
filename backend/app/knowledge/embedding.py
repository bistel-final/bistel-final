"""문서 검색용 BGE-M3 query embedding lifecycle을 관리한다."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_EMBEDDING_MODEL = "BAAI/bge-m3"
EXPECTED_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EXPECTED_EMBEDDING_DIMENSION = 1024


@lru_cache(maxsize=1)
def get_embedding_model() -> Any:
    """현재 API process에서 BGE-M3 모델을 한 번만 생성한다."""

    load_dotenv(REPOSITORY_ROOT / ".env")

    model_id = os.getenv("EMBEDDING_MODEL", EXPECTED_EMBEDDING_MODEL).strip()
    revision = os.getenv("EMBEDDING_MODEL_REVISION", EXPECTED_EMBEDDING_REVISION).strip()
    dimension = int(os.getenv("EMBEDDING_DIM", str(EXPECTED_EMBEDDING_DIMENSION)))
    model_path = os.getenv("EMBEDDING_MODEL_PATH", "backend/model-cache/bge-m3").strip()

    if model_id != EXPECTED_EMBEDDING_MODEL:
        raise RuntimeError(f"EMBEDDING_MODEL은 {EXPECTED_EMBEDDING_MODEL}이어야 합니다")
    if revision != EXPECTED_EMBEDDING_REVISION:
        raise RuntimeError("EMBEDDING_MODEL_REVISION이 공식 revision과 다릅니다")
    if dimension != EXPECTED_EMBEDDING_DIMENSION:
        raise RuntimeError(f"EMBEDDING_DIM은 {EXPECTED_EMBEDDING_DIMENSION}이어야 합니다")

    cache_path = Path(model_path)
    if not cache_path.is_absolute():
        cache_path = REPOSITORY_ROOT / cache_path
    if not cache_path.exists():
        raise RuntimeError(f"임베딩 모델 캐시 경로가 없습니다: {cache_path}")

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(str(cache_path), local_files_only=True)


def embed_query(query: str) -> list[float]:
    """검색 Query를 정규화된 1024차원 BGE-M3 vector로 변환한다."""

    vector = get_embedding_model().encode([query], normalize_embeddings=True)[0]
    return [float(value) for value in vector]
