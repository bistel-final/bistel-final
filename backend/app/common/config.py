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


def get_int_env(name: str, default: str, minimum: int) -> int:
    raw = get_env(name, default)

    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 은 정수여야 합니다: {raw}") from exc

    if value < minimum:
        raise RuntimeError(f"{name} 은 {minimum} 이상이어야 합니다: {value}")

    return value


def get_ratio_env(name: str, default: str) -> float:
    raw = get_env(name, default)

    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 은 실수여야 합니다: {raw}") from exc

    if not 0.0 <= value <= 1.0:
        raise RuntimeError(f"{name} 은 0~1 범위여야 합니다: {value}")

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

# ---------------------------------------------------------------------
# 에이전트 동작 설정
# ---------------------------------------------------------------------
AGENT_AUTONOMY_LEVEL = get_int_env("AGENT_AUTONOMY_LEVEL", "2", minimum=1)

if AGENT_AUTONOMY_LEVEL not in (1, 2, 3):
    raise RuntimeError(
        f"AGENT_AUTONOMY_LEVEL 은 1·2·3 중 하나여야 합니다: {AGENT_AUTONOMY_LEVEL}"
    )

# 그래프 1회 실행의 총 실제 Tool 호출 상한(재시도 포함)
AGENT_MAX_TOOL_CALLS = get_int_env("AGENT_MAX_TOOL_CALLS", "8", minimum=1)

# 같은 Tool 의 최초 실패 후 추가 시도 수
AGENT_MAX_RETRY = get_int_env("AGENT_MAX_RETRY", "3", minimum=0)

CLASSIFICATION_OUTPUT_RETRY = get_int_env("CLASSIFICATION_OUTPUT_RETRY", "1", minimum=0)

ANOMALY_SCORE_THRESHOLD = get_ratio_env("ANOMALY_SCORE_THRESHOLD", "0.62")
SEVERITY_HIGH_THRESHOLD = get_ratio_env("SEVERITY_HIGH_THRESHOLD", "0.80")

# 승인 게이트는 설정으로 우회할 수 없다. HIGH 외의 값이면 기동을 거부한다.
# EQP_HOLD 만 사람 승인 대상이라는 안전장치가 환경변수로 무력화되면 안 되기 때문이다.
HITL_REQUIRED_SEVERITY = get_env("HITL_REQUIRED_SEVERITY", "HIGH")

if HITL_REQUIRED_SEVERITY != "HIGH":
    raise RuntimeError(
        "HITL_REQUIRED_SEVERITY 는 HIGH 여야 합니다. "
        f"승인 게이트를 우회할 수 없습니다: {HITL_REQUIRED_SEVERITY}"
    )

# ---------------------------------------------------------------------
# Tool 제한시간
# ---------------------------------------------------------------------
TOOL_DB_TIMEOUT_SEC = get_int_env("TOOL_DB_TIMEOUT_SEC", "5", minimum=1)
TOOL_EMBEDDING_TIMEOUT_SEC = get_int_env("TOOL_EMBEDDING_TIMEOUT_SEC", "15", minimum=1)
N8N_WEBHOOK_TIMEOUT_SEC = get_int_env("N8N_WEBHOOK_TIMEOUT_SEC", "10", minimum=1)
