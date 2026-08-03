import os

from dotenv import load_dotenv

load_dotenv()


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)

    if value is None:
        raise RuntimeError(f"필수 환경변수가 없습니다: {name}")

    return value


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
