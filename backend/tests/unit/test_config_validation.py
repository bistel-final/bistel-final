import importlib

import pytest

CONFIG_MODULE = "app.common.config"

# .env 로드가 실제 값을 덮어쓰지 않도록 필수 키를 함께 넣는다.
BASE_ENV = {
    "POSTGRES_USER": "kosa",
    "POSTGRES_PASSWORD": "pw",
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
    "N8N_WEBHOOK_URL": "http://localhost:5678/webhook/equipment-alert",
    "AGENT_AUTONOMY_LEVEL": "2",
    "AGENT_MAX_TOOL_CALLS": "8",
    "AGENT_MAX_RETRY": "3",
    "HITL_REQUIRED_SEVERITY": "HIGH",
    "MODEL_SIGNAL_ENABLED": "false",
}


def load_config(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    for key, value in {**BASE_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)

    # load_dotenv 는 기존 환경변수를 덮어쓰지 않으므로 monkeypatch 값이 우선한다.
    return importlib.reload(importlib.import_module(CONFIG_MODULE))


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(importlib.import_module(CONFIG_MODULE))


class TestDefaults:
    def test_valid_values_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = load_config(monkeypatch)

        assert config.AGENT_AUTONOMY_LEVEL == 2
        assert config.AGENT_MAX_TOOL_CALLS == 8
        assert config.AGENT_MAX_RETRY == 3
        assert config.HITL_REQUIRED_SEVERITY == "HIGH"
        assert config.MODEL_SIGNAL_ENABLED is False


class TestApplicationCredentialBoundary:
    def test_app_role_name_is_fixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError, match="APP_DB_USER"):
            load_config(monkeypatch, APP_DB_USER="kosa")

    def test_missing_app_credential_is_deferred_until_database_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = load_config(
            monkeypatch,
            APP_DATABASE_URL="",
            APP_DB_PASSWORD="",
        )

        assert config.APP_DATABASE_URL is None
        assert config.APP_DB_PASSWORD is None


class TestApprovalGateCannotBeBypassed:
    @pytest.mark.parametrize("value", ["MEDIUM", "LOW", "high", "", "HIGH,MEDIUM"])
    def test_non_high_severity_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="HITL_REQUIRED_SEVERITY"):
            load_config(monkeypatch, HITL_REQUIRED_SEVERITY=value)


class TestAutonomyLevel:
    @pytest.mark.parametrize("value", ["1", "2", "3"])
    def test_allowed_levels(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        assert load_config(monkeypatch, AGENT_AUTONOMY_LEVEL=value).AGENT_AUTONOMY_LEVEL

    @pytest.mark.parametrize("value", ["0", "4", "-1"])
    def test_out_of_range_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="AGENT_AUTONOMY_LEVEL"):
            load_config(monkeypatch, AGENT_AUTONOMY_LEVEL=value)

    def test_non_integer_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError, match="정수"):
            load_config(monkeypatch, AGENT_AUTONOMY_LEVEL="two")


class TestBudgetBounds:
    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_tool_calls_must_be_positive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="AGENT_MAX_TOOL_CALLS"):
            load_config(monkeypatch, AGENT_MAX_TOOL_CALLS=value)

    def test_retry_allows_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert load_config(monkeypatch, AGENT_MAX_RETRY="0").AGENT_MAX_RETRY == 0

    def test_retry_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError, match="AGENT_MAX_RETRY"):
            load_config(monkeypatch, AGENT_MAX_RETRY="-1")


class TestTimeoutAndRetryBounds:
    TIMEOUTS = (
        "TOOL_DB_TIMEOUT_SEC",
        "TOOL_EMBEDDING_TIMEOUT_SEC",
        "N8N_WEBHOOK_TIMEOUT_SEC",
    )

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = load_config(monkeypatch)

        assert config.CLASSIFICATION_OUTPUT_RETRY == 1
        assert config.TOOL_DB_TIMEOUT_SEC == 5
        assert config.TOOL_EMBEDDING_TIMEOUT_SEC == 15
        assert config.N8N_WEBHOOK_TIMEOUT_SEC == 10

    def test_classification_retry_allows_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(monkeypatch, CLASSIFICATION_OUTPUT_RETRY="0")

        assert config.CLASSIFICATION_OUTPUT_RETRY == 0

    @pytest.mark.parametrize("value", ["-1", "-10"])
    def test_classification_retry_rejects_negative(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="CLASSIFICATION_OUTPUT_RETRY"):
            load_config(monkeypatch, CLASSIFICATION_OUTPUT_RETRY=value)

    def test_classification_retry_rejects_non_integer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(RuntimeError, match="정수"):
            load_config(monkeypatch, CLASSIFICATION_OUTPUT_RETRY="once")

    @pytest.mark.parametrize("name", TIMEOUTS)
    def test_timeout_allows_one(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
    ) -> None:
        config = load_config(monkeypatch, **{name: "1"})

        assert getattr(config, name) == 1

    @pytest.mark.parametrize("name", TIMEOUTS)
    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_timeout_rejects_non_positive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match=name):
            load_config(monkeypatch, **{name: value})

    @pytest.mark.parametrize("name", TIMEOUTS)
    def test_timeout_rejects_non_integer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="정수"):
            load_config(monkeypatch, **{name: "5s"})


class TestModelSignalGate:
    @pytest.mark.parametrize("value", ["true", "TRUE", " True "])
    def test_explicit_true_enables_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        config = load_config(monkeypatch, MODEL_SIGNAL_ENABLED=value)

        assert config.MODEL_SIGNAL_ENABLED is True

    @pytest.mark.parametrize("value", ["false", "FALSE", " False "])
    def test_explicit_false_disables_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        config = load_config(monkeypatch, MODEL_SIGNAL_ENABLED=value)

        assert config.MODEL_SIGNAL_ENABLED is False

    @pytest.mark.parametrize("value", ["1", "0", "yes", "no", "on", "off", "enabled"])
    def test_non_boolean_values_are_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="true 또는 false"):
            load_config(monkeypatch, MODEL_SIGNAL_ENABLED=value)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_value_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        with pytest.raises(RuntimeError, match="MODEL_SIGNAL_ENABLED"):
            load_config(monkeypatch, MODEL_SIGNAL_ENABLED=value)
