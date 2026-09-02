from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPOSITORY_ROOT / "deploy" / "compose" / "docker-compose.team.yml"
FRONTEND_DOCKERFILE_PATH = REPOSITORY_ROOT / "frontend" / "Dockerfile"
TEAM_ENV_EXAMPLE = REPOSITORY_ROOT / "deploy" / "compose" / ".env.team.example"
PREFLIGHT_PATH = REPOSITORY_ROOT / "deploy" / "compose" / "preflight_team_env.py"
E2E_OVERRIDE_PATH = (
    REPOSITORY_ROOT / "deploy" / "compose" / "docker-compose.e2e-backend.yml"
)
MES_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "v5_c_4_5"
    / "docker-compose.yml"
)

BACKEND_ENV_KEYS = {
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "APP_DB_USER",
    "APP_DB_PASSWORD",
    "READONLY_USER",
    "READONLY_PASSWORD",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "N8N_WEBHOOK_URL",
    "N8N_WF3_URL",
    "N8N_BASE_URL",
    "N8N_WEBHOOK_SECRET",
    "N8N_WEBHOOK_TIMEOUT_SEC",
    "DELIVERY_UNKNOWN_AFTER_SEC",
    "AGENT_EMAIL_RECIPIENTS",
    "CORS_ORIGINS",
    "LLM_PROVIDER",
    "LLM_MODEL_MAIN",
    "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS",
    "LLM_TIMEOUT_SEC",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_MODEL_REVISION",
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL_PATH",
    "KAFKA_BOOTSTRAP_INTERNAL",
    "KAFKA_CLIENT_USER_FILE",
    "KAFKA_CLIENT_PASSWORD_FILE",
}
MES_ENV_KEYS = {
    "KAFKA_BOOTSTRAP_INTERNAL",
    "KAFKA_CLIENT_USER_FILE",
    "KAFKA_CLIENT_PASSWORD_FILE",
    "MES_CONSUMER_GROUP",
}
FORBIDDEN_SERVICE_KEYS = {
    "postgres",
    "postgresql",
    "neo4j",
    "n8n",
    "database",
}
FORBIDDEN_BACKEND_PREFIXES = (
    "POSTGRES_BOOTSTRAP_",
    "POSTGRES_TRANSITION_",
    "NEO4J_BOOTSTRAP_",
    "EVALUATION_",
    "QUERY_LOGGER_",
)


def _load_yaml() -> dict[str, object]:
    payload = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location("team_env_preflight", PREFLIGHT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_env(tmp_path: Path) -> dict[str, str]:
    preflight = _load_preflight()
    values, findings = preflight.parse_env_file(TEAM_ENV_EXAMPLE)
    assert findings == []
    cache_dir = tmp_path / "bge-m3"
    cache_dir.mkdir()
    values.update(
        {
            "POSTGRES_HOST": "10.20.30.40",
            "APP_DB_PASSWORD": "app-secret-value",
            "READONLY_PASSWORD": "readonly-secret-value",
            "NEO4J_URI": "bolt://10.20.30.40:7687",
            "NEO4J_PASSWORD": "neo4j-secret-value",
            "N8N_WEBHOOK_URL": "http://10.20.30.40:5678/webhook/fdc-notify-email",
            "N8N_WF3_URL": "http://10.20.30.40:5678/webhook/fdc-mes-hold",
            "N8N_BASE_URL": "http://10.20.30.40:5678",
            "N8N_WEBHOOK_SECRET": "webhook-secret-value",
            "CORS_ORIGINS": "http://10.20.30.40:8080,http://10.20.30.40:53000",
            "BACKEND_BASE_URL": "http://10.20.30.40:8080/api",
            "LLM_API_KEY": "llm-secret-value",
            "LLM_BASE_URL": "http://10.20.30.41:11434/v1",
            "RAG_MODEL_CACHE_DIR": str(cache_dir),
            "KAFKA_ADVERTISED_HOST": "10.20.30.40",
            "KAFKA_BROKER_PASSWORD": "broker-secret-value",
            "KAFKA_CLIENT_PASSWORD": "client-secret-value",
        }
    )
    return values


def test_compose_defines_only_team_owned_services() -> None:
    payload = _load_yaml()
    services = payload["services"]

    assert set(services) == {"backend", "frontend", "kafka", "mes-mock"}
    assert not (set(services) & FORBIDDEN_SERVICE_KEYS)
    assert services["mes-mock"]["profiles"] == ["mes"]
    assert services["mes-mock"]["command"] == ["python", "-m", "app.mes_mock"]


def test_backend_and_mes_environment_are_exact_allowlists() -> None:
    services = _load_yaml()["services"]
    backend = services["backend"]
    mes_mock = services["mes-mock"]

    assert "env_file" not in backend
    assert "env_file" not in mes_mock
    assert set(backend["environment"]) == BACKEND_ENV_KEYS
    assert set(mes_mock["environment"]) == MES_ENV_KEYS
    assert set(mes_mock["secrets"]) == {
        "kafka_client_user",
        "kafka_client_password",
    }
    assert set(backend["secrets"]) == {
        "kafka_client_user",
        "kafka_client_password",
    }
    assert backend["environment"]["KAFKA_BOOTSTRAP_INTERNAL"] == "kafka:9092"
    assert backend["environment"]["KAFKA_CLIENT_USER_FILE"] == (
        "/run/secrets/kafka_client_user"
    )
    assert backend["environment"]["KAFKA_CLIENT_PASSWORD_FILE"] == (
        "/run/secrets/kafka_client_password"
    )
    assert not {"KAFKA_CLIENT_USER", "KAFKA_CLIENT_PASSWORD"} & set(
        backend["environment"]
    )
    assert mes_mock["environment"]["KAFKA_CLIENT_USER_FILE"] == (
        "/run/secrets/kafka_client_user"
    )
    assert mes_mock["environment"]["KAFKA_CLIENT_PASSWORD_FILE"] == (
        "/run/secrets/kafka_client_password"
    )
    assert not {"KAFKA_CLIENT_USER", "KAFKA_CLIENT_PASSWORD"} & set(
        mes_mock["environment"]
    )
    assert not any(
        key.startswith(FORBIDDEN_BACKEND_PREFIXES)
        or key in {"N8N_USER", "N8N_PASSWORD"}
        for key in backend["environment"]
    )
    assert mes_mock["environment"]["KAFKA_CLIENT_USER_FILE"] == (
        "/run/secrets/kafka_client_user"
    )
    assert mes_mock["environment"]["KAFKA_CLIENT_PASSWORD_FILE"] == (
        "/run/secrets/kafka_client_password"
    )
    assert set(mes_mock["secrets"]) == {
        "kafka_client_user",
        "kafka_client_password",
    }
    assert not {"KAFKA_CLIENT_USER", "KAFKA_CLIENT_PASSWORD"} & set(
        mes_mock["environment"]
    )


def test_external_services_and_retired_loader_are_absent() -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "00_load.sh" not in text
    assert not re.search(r"image:\s*(?:postgres|neo4j|n8nio/n8n)(?::|\s|$)", text)
    assert not re.search(r"(?:POSTGRES|NEO4J)_(?:BOOTSTRAP|TRANSITION)_", text)


def test_mes_fixture_runs_the_real_entrypoint_with_file_only_secrets() -> None:
    fixture = yaml.safe_load(MES_FIXTURE_PATH.read_text(encoding="utf-8"))
    service = fixture["services"]["mes-mock"]

    assert service["profiles"] == ["consumer"]
    assert service["command"] == ["python", "-m", "app.mes_mock"]
    assert service["environment"] == {
        "KAFKA_BOOTSTRAP_INTERNAL": "kafka:9092",
        "KAFKA_CLIENT_USER_FILE": "/run/secrets/kafka_client_user",
        "KAFKA_CLIENT_PASSWORD_FILE": "/run/secrets/kafka_client_password",
        "MES_CONSUMER_GROUP": "kosa-fdc-mes-mock",
    }
    assert set(service["secrets"]) == {
        "kafka_client_user",
        "kafka_client_password",
    }


def test_images_and_build_contracts_are_exactly_pinned() -> None:
    payload = _load_yaml()
    services = payload["services"]
    backend_dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    backend_requirements = (REPOSITORY_ROOT / "backend" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    frontend_dockerfile = (REPOSITORY_ROOT / "frontend" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert services["backend"]["image"].startswith("bistel-backend:${TEAM_IMAGE_TAG:?")
    assert services["frontend"]["image"].startswith(
        "bistel-frontend:${TEAM_IMAGE_TAG:?"
    )
    assert services["kafka"]["image"] == "apache/kafka:3.9.1"
    assert "latest" not in COMPOSE_PATH.read_text(encoding="utf-8")
    assert backend_dockerfile.splitlines()[0] == "FROM python:3.12.8-slim"
    assert "WORKDIR /workspace/backend" in backend_dockerfile
    assert "COPY backend/scripts ./scripts" in backend_dockerfile
    assert "COPY backend/artifacts ./artifacts" in backend_dockerfile
    assert "https://download.pytorch.org/whl/cpu" in backend_dockerfile
    assert "torch==2.5.1" in backend_dockerfile
    assert "torch==2.5.1" in backend_requirements
    assert "transformers==4.46.3" in backend_requirements
    assert "FROM node:22.14.0-alpine AS build" in frontend_dockerfile
    assert "FROM nginx:1.27.3-alpine" in frontend_dockerfile
    assert "npm ci" in frontend_dockerfile
    assert "npm run build" in frontend_dockerfile


def test_frontend_proxy_and_production_build_args_are_fixed() -> None:
    frontend = _load_yaml()["services"]["frontend"]
    nginx = (REPOSITORY_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    dockerfile = FRONTEND_DOCKERFILE_PATH.read_text(encoding="utf-8")
    domain_mock_args = {
        "VITE_USE_MOCK_DETECTION": "false",
        "VITE_USE_MOCK_AGENT": "false",
        "VITE_USE_MOCK_KNOWLEDGE": "false",
        "VITE_USE_MOCK_ANALYTICS": "false",
    }

    assert frontend["ports"] == ["8080:80"]
    assert frontend["build"]["args"] == {
        "VITE_API_BASE_URL": "/api",
        "VITE_USE_MOCK": "false",
        **domain_mock_args,
    }
    assert "ARG VITE_API_BASE_URL=/api" in dockerfile
    assert "ARG VITE_USE_MOCK=false" in dockerfile
    assert "ENV VITE_USE_MOCK=${VITE_USE_MOCK}" in dockerfile
    assert 'test "$VITE_USE_MOCK" = "false"' in dockerfile
    for name, value in domain_mock_args.items():
        assert frontend["build"]["args"][name] == value
        assert f"ARG {name}=false" in dockerfile
        assert f"ENV {name}=${{{name}}}" in dockerfile
        assert f'test "${name}" = "false"' in dockerfile
    assert "location /api/" in nginx
    assert "proxy_pass http://backend:8000/;" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx


def test_backend_has_no_host_publish_and_rag_mount_is_read_only() -> None:
    backend = _load_yaml()["services"]["backend"]

    assert "ports" not in backend
    assert backend["volumes"] == [
        {
            "type": "bind",
            "source": "${RAG_MODEL_CACHE_DIR:?RAG_MODEL_CACHE_DIR is required}",
            "target": "/models/bge-m3",
            "read_only": True,
        }
    ]
    assert backend["environment"]["EMBEDDING_MODEL_PATH"] == "/models/bge-m3"
    assert backend["healthcheck"]["test"][:2] == ["CMD", "python"]


def test_kafka_listener_sasl_and_topic_lifecycle_are_explicit() -> None:
    kafka = _load_yaml()["services"]["kafka"]
    environment = kafka["environment"]
    script = (
        REPOSITORY_ROOT / "deploy" / "compose" / "kafka" / "manage_topics.sh"
    ).read_text(encoding="utf-8")
    start_script = (
        REPOSITORY_ROOT / "deploy" / "compose" / "kafka" / "start_kafka.sh"
    ).read_text(encoding="utf-8")
    offset_script = (
        REPOSITORY_ROOT / "deploy" / "compose" / "kafka" / "manage_wf4_offsets.sh"
    ).read_text(encoding="utf-8")

    assert kafka["ports"] == ["53005:9094"]
    assert environment["KAFKA_LISTENERS"] == (
        "INTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093," "EXTERNAL://0.0.0.0:9094"
    )
    assert environment["KAFKA_ADVERTISED_LISTENERS"] == (
        "INTERNAL://kafka:9092,"
        "EXTERNAL://${KAFKA_ADVERTISED_HOST:?KAFKA_ADVERTISED_HOST is required}:53005"
    )
    assert environment["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"] == (
        "INTERNAL:SASL_PLAINTEXT,EXTERNAL:SASL_PLAINTEXT,CONTROLLER:PLAINTEXT"
    )
    assert environment["KAFKA_INTER_BROKER_LISTENER_NAME"] == "INTERNAL"
    assert environment["KAFKA_SASL_ENABLED_MECHANISMS"] == "PLAIN"
    assert environment["KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL"] == "PLAIN"
    assert environment["KAFKA_OPTS"] == (
        "-Djava.security.auth.login.config=/tmp/kafka_server_jaas.conf"
    )
    assert environment["KAFKA_AUTO_CREATE_TOPICS_ENABLE"] == "false"
    assert not {
        "KAFKA_BROKER_USER",
        "KAFKA_BROKER_PASSWORD",
        "KAFKA_CLIENT_USER",
        "KAFKA_CLIENT_PASSWORD",
    } & set(environment)
    assert set(kafka["secrets"]) == {
        "kafka_broker_user",
        "kafka_broker_password",
        "kafka_client_user",
        "kafka_client_password",
    }
    assert kafka["healthcheck"]["interval"] == "30s"
    assert (
        "./kafka/manage_wf4_offsets.sh:/opt/team/manage_wf4_offsets.sh:ro"
        in kafka["volumes"]
    )
    assert "exec /etc/kafka/docker/run" in start_script
    assert "/run/secrets/kafka_broker_password" in start_script
    assert "--if-not-exists" in script
    assert "fdc.actions fdc.actions.result" in script
    assert "kafka-console-producer" not in script
    assert "group='kosa-fdc-wf4-writeback'" in offset_script
    assert "topic='fdc.actions.result'" in offset_script
    assert "WF4_DISABLED" in offset_script
    assert "--to-offset" in offset_script
    assert "--to-earliest" not in offset_script
    assert "Empty|Dead" in offset_script
    assert "retention bounds" in offset_script


def test_e2e_backend_override_isolated_database_and_port_are_exact() -> None:
    payload = yaml.safe_load(E2E_OVERRIDE_PATH.read_text(encoding="utf-8"))
    backend = payload["services"]["backend"]

    assert backend["ports"] == ["53081:8000"]
    assert backend["environment"] == {
        "POSTGRES_DB": "kosa_agent_e2e",
        "DELIVERY_CALLBACK_TRAIL_DIR": "${DELIVERY_CALLBACK_TRAIL_DIR:-}",
        "DELIVERY_CALLBACK_TRAIL_RUN_ID": "${DELIVERY_CALLBACK_TRAIL_RUN_ID:-}",
    }
    assert backend["volumes"] == [
        {
            "type": "bind",
            "source": "./trail",
            "target": "/var/lib/bistel/delivery-trail",
            "read_only": False,
        }
    ]


def test_external_kafka_probe_uses_a_separate_network_and_negative_auth() -> None:
    probe = (
        REPOSITORY_ROOT / "deploy" / "compose" / "probe_external_kafka.py"
    ).read_text(encoding="utf-8")

    assert '"--network"' in probe
    assert 'KAFKA_IMAGE = "apache/kafka:3.9.1"' in probe
    assert "bootstrap = f\"{values['KAFKA_ADVERTISED_HOST']}:53005\"" in probe
    assert '"intentionally-wrong-credential"' in probe
    assert "KAFKA_INVALID_CREDENTIAL_ACCEPTED" in probe
    assert "fdc.actions.result" in probe


def test_example_fails_closed_without_real_secrets_or_hosts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    preflight = _load_preflight()

    exit_code = preflight.main(["--env-file", str(TEAM_ENV_EXAMPLE)])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "SECRET_REQUIRED" in output
    assert "PLACEHOLDER_FORBIDDEN" in output
    assert "example.invalid" not in output


def test_preflight_accepts_a_complete_nonlocal_contract(tmp_path: Path) -> None:
    preflight = _load_preflight()

    assert preflight.validate(_valid_env(tmp_path)) == []


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("TEAM_IMAGE_TAG", "latest", "INVALID_FIXED_TAG"),
        ("POSTGRES_HOST", "localhost", "LOCALHOST_FORBIDDEN"),
        ("CORS_ORIGINS", "*", "WILDCARD_OR_NULL"),
        ("BACKEND_BASE_URL", "http://10.20.30.40:8080/backend", "ORIGIN_MISMATCH"),
        ("KAFKA_ADVERTISED_HOST", "host.docker.internal", "LOCALHOST_FORBIDDEN"),
        ("LLM_PROVIDER", "ollama", "CONTAINER_LOCAL_PROVIDER_FORBIDDEN"),
    ],
)
def test_preflight_rejects_unsafe_deployment_values(
    tmp_path: Path,
    key: str,
    value: str,
    code: str,
) -> None:
    preflight = _load_preflight()
    values = _valid_env(tmp_path)
    values[key] = value

    assert any(
        finding.key == key and finding.code == code
        for finding in preflight.validate(values)
    )


def test_docker_context_excludes_local_model_cache_and_secrets() -> None:
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env.*" in dockerignore
    assert "backend/model-cache" in dockerignore
    assert "backend/artifacts/**/*.joblib" in dockerignore
    assert (
        REPOSITORY_ROOT / "backend" / "artifacts" / "embedding_model_manifest.json"
    ).is_file()
