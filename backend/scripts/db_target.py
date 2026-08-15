"""PostgreSQL bootstrap target guard.

This module deliberately does not import application database settings.  The
bootstrap DDL credential and the trusted host fingerprint are a separate
contract so a target cannot approve itself.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import URL

BOOTSTRAP_DRIVER = "postgresql+psycopg"
BOOTSTRAP_ENV_KEYS = (
    "POSTGRES_BOOTSTRAP_HOST",
    "POSTGRES_BOOTSTRAP_PORT",
    "POSTGRES_BOOTSTRAP_USER",
    "POSTGRES_BOOTSTRAP_PASSWORD",
    "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256",
)
DATABASE_PROFILE = {
    "kosa_agent": "runtime",
    "kosa_agent_e2e": "runtime",
    "kosa_text2sql": "evaluation",
}
ALLOWED_DATABASES = frozenset(DATABASE_PROFILE)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class TargetValidationError(RuntimeError):
    """Target information is absent, malformed, or outside the allowlist."""


@dataclass(frozen=True)
class BootstrapTarget:
    """Validated connection components without a printable DSN."""

    host: str
    port: int
    username: str
    password: str
    database: str
    profile: str

    def create_url(self) -> URL:
        return URL.create(
            drivername=BOOTSTRAP_DRIVER,
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


def normalize_host_port(host: str, port: int) -> str:
    """Return the exact value covered by the independent trust fingerprint."""

    return f"{host.strip().lower()}:{int(port)}"


def host_fingerprint(host: str, port: int) -> str:
    return hashlib.sha256(normalize_host_port(host, port).encode()).hexdigest()


def _required_value(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise TargetValidationError(f"bootstrap 설정이 비어 있습니다: {key}")
    return value


def load_bootstrap_target(
    database: str, *, environ: Mapping[str, str] | None = None
) -> BootstrapTarget:
    """Load and validate only the five bootstrap-specific environment keys."""

    if database not in ALLOWED_DATABASES:
        raise TargetValidationError("허용되지 않은 bootstrap database입니다")
    source = os.environ if environ is None else environ
    values = {key: _required_value(source, key) for key in BOOTSTRAP_ENV_KEYS}

    host = values["POSTGRES_BOOTSTRAP_HOST"].lower()
    if host in LOCAL_HOSTS:
        raise TargetValidationError(
            "로컬 기본 host는 공용 DB bootstrap에 허용되지 않습니다"
        )
    try:
        port = int(values["POSTGRES_BOOTSTRAP_PORT"])
    except ValueError as exc:
        raise TargetValidationError("bootstrap port는 정수여야 합니다") from exc
    if not 1 <= port <= 65535:
        raise TargetValidationError("bootstrap port 범위가 잘못됐습니다")

    expected = values["POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256"]
    if not SHA256_PATTERN.fullmatch(expected):
        raise TargetValidationError("bootstrap host fingerprint 형식이 잘못됐습니다")
    actual = host_fingerprint(host, port)
    if not hmac.compare_digest(actual, expected):
        raise TargetValidationError("bootstrap host fingerprint가 allowlist와 다릅니다")

    return BootstrapTarget(
        host=host,
        port=port,
        username=values["POSTGRES_BOOTSTRAP_USER"],
        password=values["POSTGRES_BOOTSTRAP_PASSWORD"],
        database=database,
        profile=DATABASE_PROFILE[database],
    )


def validate_url_components(url: URL, target: BootstrapTarget) -> None:
    """Fail before connect if URL construction ever drifts from the contract."""

    if (
        url.drivername != BOOTSTRAP_DRIVER
        or url.host != target.host
        or url.port != target.port
        or url.database != target.database
    ):
        raise TargetValidationError("bootstrap URL target 계약이 다릅니다")


def _mapping_one(result: Any) -> Mapping[str, Any]:
    try:
        return result.mappings().one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise TargetValidationError(
            "bootstrap identity 응답 형식이 잘못됐습니다"
        ) from exc


def validate_connected_identity(connection: Any, target: BootstrapTarget) -> None:
    """Re-check database and public-schema privileges after connecting."""

    result = connection.exec_driver_sql(
        """
        SELECT current_database() AS database_name,
               EXISTS (
                   SELECT 1 FROM pg_namespace WHERE nspname = 'public'
               ) AS public_exists,
               has_schema_privilege(current_user, 'public', 'USAGE') AS can_use,
               has_schema_privilege(current_user, 'public', 'CREATE') AS can_create
        """
    )
    identity = _mapping_one(result)
    if identity.get("database_name") != target.database:
        raise TargetValidationError("연결된 database가 요청 target과 다릅니다")
    if not all(
        identity.get(field) is True
        for field in ("public_exists", "can_use", "can_create")
    ):
        raise TargetValidationError(
            "public schema 또는 bootstrap 권한이 준비되지 않았습니다"
        )


def set_and_validate_public_search_path(connection: Any) -> None:
    connection.exec_driver_sql("SET LOCAL search_path = public")
    result = connection.exec_driver_sql("SELECT current_schema() AS schema_name")
    if _mapping_one(result).get("schema_name") != "public":
        raise TargetValidationError(
            "bootstrap search_path를 public으로 고정할 수 없습니다"
        )
