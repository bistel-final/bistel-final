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


def get_bool_env(name: str, default: str) -> bool:
    raw = get_env(name, default)
    normalized = raw.strip().lower()

    if normalized not in {"true", "false"}:
        raise RuntimeError(f"{name} 은 true 또는 false여야 합니다: {raw}")
    return normalized == "true"


# PostgreSQL
POSTGRES_DB = get_env("POSTGRES_DB")
POSTGRES_HOST = get_env("POSTGRES_HOST")
POSTGRES_PORT = int(get_env("POSTGRES_PORT", "5432"))

# 일반 Backend는 bootstrap/owner credential을 절대 fallback으로 사용하지 않는다.
# full DSN 또는 app 전용 user/password 조합 중 하나만으로 연결한다.
APP_DATABASE_URL = os.getenv("APP_DATABASE_URL", "").strip() or None
APP_DB_USER = get_env("APP_DB_USER", "kosa_app")
APP_DB_PASSWORD = os.getenv("APP_DB_PASSWORD", "").strip() or None
if APP_DB_USER != "kosa_app":
    raise RuntimeError("APP_DB_USER 는 kosa_app 이어야 합니다")
# credential 부재는 import 실패가 아니라 첫 DB 사용 실패다. `/health`와 DB를 쓰지 않는
# 테스트·명령은 secret 없이도 떠야 하고, 실제 engine 생성은 common.db가 fail-closed로
# 검증한다.

# Text2SQL 전용 읽기 전용 계정
READONLY_USER = get_env("READONLY_USER")
READONLY_PASSWORD = get_env("READONLY_PASSWORD")

# 합성 라벨(fault_code) 평가 전용 계정 (V5-A-2.3, V5-CM-3.5 role matrix
# ManagedRole.EVALUATION). kosa_readonly는 evaluation profile(kosa_text2sql)에
# 붙어도 lot_history.fault_code column 자체에 권한이 없다 — 오직
# kosa_evaluation만 그 컬럼을 읽을 수 있다(시스템설계서 v2.1 2.6: "runtime
# role은 fault_code를 제외한 명시 column projection만 사용한다"). 평가
# DSN은 선택이다 — API 기동·학습에는 필요 없고, holdout 평가 실행
# 시점에만 있으면 된다(app.common.db.get_evaluation_engine 참고). READONLY_*
# 와 달리 get_env가 아니라 os.getenv를 쓰는 이유도 APP_DB_PASSWORD와 같다
# — credential 부재를 import 실패가 아니라 첫 사용 실패로 미룬다.
EVALUATION_DB_USER = os.getenv("EVALUATION_DB_USER", "kosa_evaluation").strip()
EVALUATION_DB_PASSWORD = os.getenv("EVALUATION_DB_PASSWORD", "").strip() or None

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

# score threshold는 model manifest의 AnomalySignal로만 주입한다. 환경변수
# threshold는 version 검증을 우회하므로 지원하지 않는다.
# 실제 gate 사용은 C 정책 후속이다.
MODEL_SIGNAL_ENABLED = get_bool_env("MODEL_SIGNAL_ENABLED", "false")

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

# ---------------------------------------------------------------------
# LLM (.env.example 계약과 1:1)
#   기본은 로컬 Ollama. 외부 API 는 LLM_API_KEY·LLM_BASE_URL 로 전환한다.
#   API key 는 선택값이므로 get_env(빈 값 거부)를 쓰지 않고 llm.py 가
#   os.getenv 로 직접 읽는다. 키 미설정은 기동 오류가 아니라 LLM_NOT_READY
#   거부로 처리한다.
# ---------------------------------------------------------------------
LLM_PROVIDER = get_env("LLM_PROVIDER", "ollama")
LLM_MODEL_MAIN = get_env("LLM_MODEL_MAIN", "qwen2.5:7b-instruct")
LLM_TEMPERATURE = float(get_env("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS = get_int_env("LLM_MAX_TOKENS", "1500", minimum=1)
LLM_TIMEOUT_SEC = get_int_env("LLM_TIMEOUT_SEC", "60", minimum=1)
OLLAMA_BASE_URL = get_env("OLLAMA_BASE_URL", "http://localhost:11434")
