import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from app.common.tool_deadlines import READ_TOOL_CALLER_DEADLINE_SECONDS

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

# Agent 평가 화면은 실행 시 재채점하지 않고 검증 완료된 immutable artifact
# 두 건만 읽는다.
# 미설정은 정상 Empty이며, 상대경로·symlink·계약 위반은 read model이 fail-closed한다.
AGENT_FAULT_EVAL_ARTIFACT_PATH = (
    os.getenv("AGENT_FAULT_EVAL_ARTIFACT_PATH", "").strip() or None
)
AGENT_GOLDEN_FLOW_SUMMARY_PATH = (
    os.getenv("AGENT_GOLDEN_FLOW_SUMMARY_PATH", "").strip() or None
)

# Neo4j
NEO4J_USER = get_env("NEO4J_USER")
NEO4J_PASSWORD = get_env("NEO4J_PASSWORD")
NEO4J_URI = get_env("NEO4J_URI")

# n8n
N8N_WEBHOOK_URL = get_env("N8N_WEBHOOK_URL")
# Email delivery를 조립하지 않는 health·DB-only command는 secret/recipient 부재로
# import 단계에서 죽지 않는다. production factory가 DB transaction 전에 fail-closed로
# 검증한다(V5-C-4.3).
N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET")
AGENT_EMAIL_RECIPIENTS = os.getenv("AGENT_EMAIL_RECIPIENTS")
# MES adapter를 조립하지 않는 command는 WF3 URL 부재로 import 단계에서 죽지 않는다.
# production factory가 DB 접근 전에 exact path와 secret을 함께 검증한다(V5-C-4.5).
N8N_WF3_URL = os.getenv("N8N_WF3_URL")

# FastAPI
API_HOST = get_env("API_HOST", "0.0.0.0")
API_PORT = int(get_env("API_PORT", "8000"))


def parse_cors_origins(raw_origins: str) -> list[str]:
    """Credential CORS에서 사용할 명시 origin 목록만 허용한다."""

    origins = [origin.strip() for origin in raw_origins.split(",")]
    if not origins or any(not origin for origin in origins):
        raise RuntimeError("CORS_ORIGINS 에 빈 origin을 사용할 수 없습니다")
    if len(set(origins)) != len(origins):
        raise RuntimeError("CORS_ORIGINS 에 중복 origin을 사용할 수 없습니다")

    for origin in origins:
        if origin in {"*", "null"}:
            raise RuntimeError(
                "CORS_ORIGINS 에 wildcard 또는 null을 사용할 수 없습니다"
            )
        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except ValueError as exc:
            raise RuntimeError("CORS_ORIGINS 형식이 올바르지 않습니다") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "CORS_ORIGINS 는 credential·path·query·fragment 없는 "
                "http(s) origin이어야 합니다"
            )
    return origins


CORS_ORIGINS = parse_cors_origins(
    get_env(
        "CORS_ORIGINS",
        "http://localhost:5173",
    )
)

# ---------------------------------------------------------------------
# 에이전트 동작 설정
# ---------------------------------------------------------------------
AGENT_AUTONOMY_LEVEL = get_int_env("AGENT_AUTONOMY_LEVEL", "2", minimum=1)
AGENT_LEVEL3_ENABLED = get_bool_env("AGENT_LEVEL3_ENABLED", "false")
AGENT_LEVEL3_DEMO_ACK = os.getenv("AGENT_LEVEL3_DEMO_ACK", "").strip() or None

if AGENT_AUTONOMY_LEVEL not in (1, 2, 3):
    raise RuntimeError(
        f"AGENT_AUTONOMY_LEVEL 은 1·2·3 중 하나여야 합니다: {AGENT_AUTONOMY_LEVEL}"
    )
if (AGENT_AUTONOMY_LEVEL == 3) != AGENT_LEVEL3_ENABLED:
    raise RuntimeError(
        "AUTONOMY_LEVEL_INVALID: Level 3는 AGENT_AUTONOMY_LEVEL=3과 "
        "AGENT_LEVEL3_ENABLED=true를 함께 설정해야 합니다"
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
if TOOL_DB_TIMEOUT_SEC >= READ_TOOL_CALLER_DEADLINE_SECONDS:
    raise RuntimeError(
        "TOOL_DB_TIMEOUT_SEC 은 read Tool caller deadline 8초보다 작아야 합니다"
    )
# n8n delivery webhook은 SMTP callback 왕복을 포함하므로 25초 미만을 허용하지 않는다.
# C-4.3 EMAIL과 C-4.5 MES adapter가 같은 n8n delivery 경계를 재사용한다.
N8N_WEBHOOK_TIMEOUT_SEC = get_int_env("N8N_WEBHOOK_TIMEOUT_SEC", "30", minimum=25)

# 전송 결과를 알 수 없는 SENDING을 운영자가 UNKNOWN으로 확정할 때 쓰는 cutoff다.
# webhook timeout보다 짧으면 아직 정상 응답을 기다리는 delivery를 미확정으로 닫을 수
# 있으므로 import 시점에 fail-closed한다.
DELIVERY_UNKNOWN_AFTER_SEC = get_int_env(
    "DELIVERY_UNKNOWN_AFTER_SEC",
    "600",
    minimum=1,
)
if DELIVERY_UNKNOWN_AFTER_SEC < N8N_WEBHOOK_TIMEOUT_SEC * 2:
    raise RuntimeError(
        "DELIVERY_UNKNOWN_AFTER_SEC 은 N8N_WEBHOOK_TIMEOUT_SEC의 2배 이상이어야 합니다"
    )

# public controlled run에서만 켜는 callback 증적 sink. 둘 중 하나만 설정되면 증적이
# 어느 run에 속하는지 결정할 수 없으므로 애플리케이션 import 단계에서 거부한다.
_TRAIL_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DELIVERY_CALLBACK_TRAIL_DIR = (
    os.getenv("DELIVERY_CALLBACK_TRAIL_DIR", "").strip() or None
)
DELIVERY_CALLBACK_TRAIL_RUN_ID = (
    os.getenv("DELIVERY_CALLBACK_TRAIL_RUN_ID", "").strip() or None
)
if (DELIVERY_CALLBACK_TRAIL_DIR is None) != (DELIVERY_CALLBACK_TRAIL_RUN_ID is None):
    raise RuntimeError(
        "DELIVERY_CALLBACK_TRAIL_DIR 과 DELIVERY_CALLBACK_TRAIL_RUN_ID는 "
        "함께 설정해야 합니다"
    )
if (
    DELIVERY_CALLBACK_TRAIL_RUN_ID is not None
    and _TRAIL_RUN_ID.fullmatch(DELIVERY_CALLBACK_TRAIL_RUN_ID) is None
):
    raise RuntimeError("DELIVERY_CALLBACK_TRAIL_RUN_ID 형식이 올바르지 않습니다")
if (
    DELIVERY_CALLBACK_TRAIL_DIR is not None
    and not Path(DELIVERY_CALLBACK_TRAIL_DIR).is_absolute()
):
    raise RuntimeError("DELIVERY_CALLBACK_TRAIL_DIR 은 절대 경로여야 합니다")

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
