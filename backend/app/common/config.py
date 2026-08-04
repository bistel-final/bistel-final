import os
from pathlib import Path

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPOSITORY_ROOT / ".env")


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)

    if value is None or not value.strip():
        raise RuntimeError(f"필수 환경변수가 없습니다: {name}")

    return value


# PostgreSQL
POSTGRES_USER = get_env("POSTGRES_USER")
POSTGRES_PASSWORD = get_env("POSTGRES_PASSWORD")
POSTGRES_DB = get_env("POSTGRES_DB")
POSTGRES_HOST = get_env("POSTGRES_HOST")
POSTGRES_PORT = int(get_env("POSTGRES_PORT", "5432"))

# Text2SQL 전용 읽기 전용 계정
READONLY_USER = get_env("READONLY_USER")
READONLY_PASSWORD = get_env("READONLY_PASSWORD")

# Neo4j
NEO4J_USER = get_env("NEO4J_USER")
NEO4J_PASSWORD = get_env("NEO4J_PASSWORD")
NEO4J_URI = get_env("NEO4J_URI")

# n8n
N8N_WEBHOOK_URL = get_env("N8N_WEBHOOK_URL")

# FastAPI
API_HOST = get_env("API_HOST", "0.0.0.0")
API_PORT = int(get_env("API_PORT", "8000"))

CORS_ORIGINS = [
    origin.strip()
    for origin in get_env(
        "CORS_ORIGINS",
        "http://localhost:5173",
    ).split(",")
    if origin.strip()
]
