#!/usr/bin/env python3
"""팀 compose 입력을 Docker·공용 서비스 접근 전에 fail-closed 검증한다."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_KEYS = frozenset(
    {
        "TEAM_IMAGE_TAG",
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
        "N8N_WEBHOOK_SECRET",
        "N8N_WEBHOOK_TIMEOUT_SEC",
        "AGENT_EMAIL_RECIPIENTS",
        "CORS_ORIGINS",
        "BACKEND_BASE_URL",
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
        "RAG_MODEL_CACHE_DIR",
        "KAFKA_ADVERTISED_HOST",
        "KAFKA_BROKER_USER",
        "KAFKA_BROKER_PASSWORD",
        "KAFKA_CLIENT_USER",
        "KAFKA_CLIENT_PASSWORD",
    }
)

SECRET_KEYS = frozenset(
    {
        "APP_DB_PASSWORD",
        "READONLY_PASSWORD",
        "NEO4J_PASSWORD",
        "N8N_WEBHOOK_SECRET",
        "LLM_API_KEY",
        "KAFKA_BROKER_PASSWORD",
        "KAFKA_CLIENT_PASSWORD",
    }
)

URL_KEYS = frozenset(
    {"N8N_WEBHOOK_URL", "N8N_WF3_URL", "LLM_BASE_URL", "BACKEND_BASE_URL"}
)

LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
)
PLACEHOLDER_FRAGMENTS = (
    "change_me",
    "changeme",
    "replace_me",
    "example.invalid",
    "not-a-secret",
    "<set",
    "your_",
)
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
IMAGE_TAG_PATTERN = re.compile(r"^v5-[0-9a-f]{7,40}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
KAFKA_USER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
KAFKA_PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9!#%+,./:=@?_-]{16,128}$")


@dataclass(frozen=True, order=True)
class Finding:
    key: str
    code: str


def parse_env_file(path: Path) -> tuple[dict[str, str], list[Finding]]:
    values: dict[str, str] = {}
    findings: list[Finding] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}, [Finding("ENV_FILE", "UNREADABLE")]

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            findings.append(Finding(f"LINE_{line_number}", "INVALID_SYNTAX"))
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if not KEY_PATTERN.fullmatch(key):
            findings.append(Finding(f"LINE_{line_number}", "INVALID_KEY"))
            continue
        if key in values:
            findings.append(Finding(key, "DUPLICATE_KEY"))
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values, findings


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized == "latest"
        or any(fragment in normalized for fragment in PLACEHOLDER_FRAGMENTS)
    )


def _validate_external_host(key: str, host: str | None) -> list[Finding]:
    if host is None or host.strip().lower() in LOCAL_HOSTS:
        return [Finding(key, "LOCALHOST_FORBIDDEN")]
    if "example.invalid" in host.lower():
        return [Finding(key, "PLACEHOLDER_FORBIDDEN")]
    return []


def _validate_url(key: str, value: str) -> list[Finding]:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return [Finding(key, "INVALID_URL")]
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return [Finding(key, "INVALID_URL")]
    if parsed.username or parsed.password or parsed.fragment:
        return [Finding(key, "URL_CREDENTIAL_OR_FRAGMENT")]
    return _validate_external_host(key, parsed.hostname)


def _validate_origin_list(value: str) -> tuple[list[str], list[Finding]]:
    findings: list[Finding] = []
    origins = [part.strip() for part in value.split(",")]
    if not origins or any(not origin for origin in origins):
        return [], [Finding("CORS_ORIGINS", "EMPTY_ORIGIN")]
    if len(set(origins)) != len(origins):
        findings.append(Finding("CORS_ORIGINS", "DUPLICATE_ORIGIN"))
    for origin in origins:
        if origin in {"*", "null"}:
            findings.append(Finding("CORS_ORIGINS", "WILDCARD_OR_NULL"))
            continue
        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except ValueError:
            findings.append(Finding("CORS_ORIGINS", "INVALID_ORIGIN"))
            continue
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            findings.append(Finding("CORS_ORIGINS", "INVALID_ORIGIN"))
            continue
        findings.extend(_validate_external_host("CORS_ORIGINS", parsed.hostname))
    return origins, findings


def validate(values: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for key in sorted(EXPECTED_KEYS - values.keys()):
        findings.append(Finding(key, "MISSING_KEY"))
    for key in sorted(values.keys() - EXPECTED_KEYS):
        findings.append(Finding(key, "UNEXPECTED_KEY"))
    if findings:
        return sorted(set(findings))

    for key in SECRET_KEYS:
        if _is_placeholder(values[key]):
            findings.append(Finding(key, "SECRET_REQUIRED"))

    if not IMAGE_TAG_PATTERN.fullmatch(values["TEAM_IMAGE_TAG"]):
        findings.append(Finding("TEAM_IMAGE_TAG", "INVALID_FIXED_TAG"))
    if values["POSTGRES_DB"] != "kosa_agent":
        findings.append(Finding("POSTGRES_DB", "UNEXPECTED_DATABASE"))
    if values["APP_DB_USER"] != "kosa_app":
        findings.append(Finding("APP_DB_USER", "UNEXPECTED_ROLE"))
    findings.extend(_validate_external_host("POSTGRES_HOST", values["POSTGRES_HOST"]))

    try:
        postgres_port = int(values["POSTGRES_PORT"])
    except ValueError:
        postgres_port = 0
    if not 1 <= postgres_port <= 65535:
        findings.append(Finding("POSTGRES_PORT", "INVALID_PORT"))

    try:
        neo4j = urlsplit(values["NEO4J_URI"])
        _ = neo4j.port
    except ValueError:
        neo4j = None
    if (
        neo4j is None
        or neo4j.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}
        or not neo4j.hostname
        or neo4j.username
        or neo4j.password
        or neo4j.path
        or neo4j.query
        or neo4j.fragment
    ):
        findings.append(Finding("NEO4J_URI", "INVALID_URI"))
    else:
        findings.extend(_validate_external_host("NEO4J_URI", neo4j.hostname))

    for key in URL_KEYS:
        findings.extend(_validate_url(key, values[key]))

    origins, origin_findings = _validate_origin_list(values["CORS_ORIGINS"])
    findings.extend(origin_findings)
    if origins and values["BACKEND_BASE_URL"] not in {
        f"{origin}/api" for origin in origins
    }:
        findings.append(Finding("BACKEND_BASE_URL", "ORIGIN_MISMATCH"))

    if values["LLM_PROVIDER"].strip().lower() == "ollama":
        findings.append(Finding("LLM_PROVIDER", "CONTAINER_LOCAL_PROVIDER_FORBIDDEN"))
    for key, minimum in (
        ("N8N_WEBHOOK_TIMEOUT_SEC", 25),
        ("LLM_MAX_TOKENS", 1),
        ("LLM_TIMEOUT_SEC", 1),
    ):
        try:
            number = int(values[key])
        except ValueError:
            number = 0
        if number < minimum:
            findings.append(Finding(key, "INVALID_INTEGER"))
    try:
        temperature = float(values["LLM_TEMPERATURE"])
    except ValueError:
        temperature = -1.0
    if not 0.0 <= temperature <= 2.0:
        findings.append(Finding("LLM_TEMPERATURE", "INVALID_NUMBER"))

    if values["EMBEDDING_MODEL"] != "BAAI/bge-m3":
        findings.append(Finding("EMBEDDING_MODEL", "UNEXPECTED_MODEL"))
    if not REVISION_PATTERN.fullmatch(values["EMBEDDING_MODEL_REVISION"]):
        findings.append(Finding("EMBEDDING_MODEL_REVISION", "INVALID_REVISION"))
    if values["EMBEDDING_DIM"] != "1024":
        findings.append(Finding("EMBEDDING_DIM", "UNEXPECTED_DIMENSION"))
    cache_dir = Path(values["RAG_MODEL_CACHE_DIR"])
    if not cache_dir.is_absolute():
        findings.append(Finding("RAG_MODEL_CACHE_DIR", "ABSOLUTE_PATH_REQUIRED"))
    elif not cache_dir.is_dir():
        findings.append(Finding("RAG_MODEL_CACHE_DIR", "DIRECTORY_NOT_FOUND"))

    kafka_host = values["KAFKA_ADVERTISED_HOST"].strip()
    if ":" in kafka_host or "/" in kafka_host:
        findings.append(Finding("KAFKA_ADVERTISED_HOST", "HOST_ONLY_REQUIRED"))
    findings.extend(_validate_external_host("KAFKA_ADVERTISED_HOST", kafka_host))
    if values["KAFKA_BROKER_USER"] == values["KAFKA_CLIENT_USER"]:
        findings.append(Finding("KAFKA_CLIENT_USER", "ROLE_REUSE_FORBIDDEN"))
    for key in ("KAFKA_BROKER_USER", "KAFKA_CLIENT_USER"):
        if not KAFKA_USER_PATTERN.fullmatch(values[key]):
            findings.append(Finding(key, "INVALID_JAAS_USER"))
    for key in ("KAFKA_BROKER_PASSWORD", "KAFKA_CLIENT_PASSWORD"):
        if not KAFKA_PASSWORD_PATTERN.fullmatch(values[key]):
            findings.append(Finding(key, "INVALID_JAAS_PASSWORD"))

    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args(argv)

    values, parse_findings = parse_env_file(args.env_file)
    findings = (
        sorted(set(parse_findings + validate(values)))
        if not parse_findings
        else parse_findings
    )
    if findings:
        for finding in findings:
            print(f"ERROR {finding.key} {finding.code}")
        return 1
    print(f"OK TEAM_ENV_VALID key_count={len(EXPECTED_KEYS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
