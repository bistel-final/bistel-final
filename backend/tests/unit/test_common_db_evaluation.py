"""V5-A-2.3 kosa_evaluation 전용 engine 단위 테스트(`app/common/db.py` 확장분).

실제 DB 접속 없이 검증한다 — engine 생성은 지연이므로 `.connect()`를 부르지
않는 한 네트워크가 필요 없다(`tests/unit/test_db_pool.py`와 같은 원칙).
"""

from __future__ import annotations

import importlib

import pytest

CONFIG_MODULE = "app.common.config"
DB_MODULE = "app.common.db"

# .env 로드가 실제 값을 덮어쓰지 않도록 필수 키를 함께 넣는다
# (test_config_validation.py의 BASE_ENV와 같은 이유).
BASE_ENV = {
    "POSTGRES_DB": "kosa_agent",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "APP_DATABASE_URL": "postgresql+psycopg://kosa_app:pw@localhost:5432/kosa_agent",
    "APP_DB_USER": "kosa_app",
    "READONLY_USER": "kosa_readonly",
    "READONLY_PASSWORD": "pw",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "pw",
    "NEO4J_URI": "bolt://localhost:7687",
    "N8N_WEBHOOK_URL": "http://localhost:5678/webhook/fdc-notify-email",
    "AGENT_AUTONOMY_LEVEL": "2",
    "AGENT_MAX_TOOL_CALLS": "8",
    "AGENT_MAX_RETRY": "3",
    "HITL_REQUIRED_SEVERITY": "HIGH",
    "MODEL_SIGNAL_ENABLED": "false",
}


def _reload_db(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    for key, value in {**BASE_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    # config.py가 먼저 새 env를 반영해야 db.py의 `from app.common.config import
    # EVALUATION_DB_USER, ...`도 새 값을 담는다 — 두 모듈 다 reload해야 한다
    # (test_config_validation.py의 load_config와 같은 이유).
    importlib.reload(importlib.import_module(CONFIG_MODULE))
    return importlib.reload(importlib.import_module(DB_MODULE))


@pytest.fixture(autouse=True)
def _restore_modules():
    yield
    importlib.reload(importlib.import_module(CONFIG_MODULE))
    importlib.reload(importlib.import_module(DB_MODULE))


def test_evaluation_dsn_requires_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _reload_db(monkeypatch, EVALUATION_DB_PASSWORD="")

    with pytest.raises(RuntimeError, match="EVALUATION_DB_PASSWORD"):
        db.create_evaluation_postgres_url()


def test_evaluation_role_name_is_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정 실수로 kosa_readonly 같은 다른 계정을 넣으면 즉시 거부해야 한다 —
    role이 fault_code 접근의 1차 방어선이므로(V5-CM-3.5), 이름만 다른 계정을
    조용히 통과시키면 안 된다.
    """

    db = _reload_db(
        monkeypatch, EVALUATION_DB_USER="kosa_readonly", EVALUATION_DB_PASSWORD="pw"
    )

    with pytest.raises(RuntimeError, match="EVALUATION_DB_USER"):
        db.create_evaluation_postgres_url()


def test_evaluation_url_targets_kosa_text2sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _reload_db(monkeypatch, EVALUATION_DB_PASSWORD="pw")

    url = db.create_evaluation_postgres_url()

    assert url.username == "kosa_evaluation"
    assert url.database == "kosa_text2sql"
    assert url.drivername == "postgresql+psycopg"


def test_get_evaluation_engine_is_lazy_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _reload_db(monkeypatch, EVALUATION_DB_PASSWORD="pw")

    first = db.get_evaluation_engine()
    second = db.get_evaluation_engine()

    assert first is second
    assert first.url.username == "kosa_evaluation"

    db.dispose_engines()


def test_missing_evaluation_password_does_not_block_other_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """평가 DSN이 없어도(V5-A-2.4 holdout 평가를 아직 실행하지 않는 한) 다른
    engine 사용에는 영향이 없어야 한다 — `db_pool.py`의 "평가 DSN은
    선택이다"와 같은 설계 원칙을 `db.py` 쪽에도 그대로 적용한다.
    """

    db = _reload_db(monkeypatch, EVALUATION_DB_PASSWORD="")

    with pytest.raises(RuntimeError):
        db.get_evaluation_engine()

    readonly_engine = db.get_readonly_engine()
    assert readonly_engine.url.username == "kosa_readonly"

    db.dispose_engines()
