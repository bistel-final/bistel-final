"""Independent target guard for destructive-safe Neo4j bootstrap commands."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

BOOTSTRAP_ENV_KEYS = (
    "NEO4J_BOOTSTRAP_URI",
    "NEO4J_BOOTSTRAP_USER",
    "NEO4J_BOOTSTRAP_PASSWORD",
    "NEO4J_BOOTSTRAP_ALLOWED_TARGET_SHA256",
    "NEO4J_BOOTSTRAP_BACKUP_ROOT",
)
ALLOWED_SCHEMES = frozenset({"bolt", "bolt+s", "neo4j", "neo4j+s"})
DEFAULT_PORT = 7687
DATABASE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,62}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class Neo4jTargetError(RuntimeError):
    """Target settings are missing, malformed or outside the trust boundary."""


@dataclass(frozen=True)
class Neo4jBootstrapTarget:
    """Validated connection values; never stringify this object in logs."""

    uri: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    database: str
    target_fingerprint_sha256: str
    backup_root: str = field(repr=False)


def validate_database_name(database: str) -> str:
    if not isinstance(database, str) or not DATABASE_PATTERN.fullmatch(database):
        raise Neo4jTargetError("Neo4j database 이름 형식이 잘못됐습니다")
    return database


def canonical_target_text(uri: str, database: str) -> str:
    validate_database_name(database)
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise Neo4jTargetError("Neo4j bootstrap URI port 형식이 잘못됐습니다") from exc
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise Neo4jTargetError("허용되지 않은 Neo4j bootstrap URI scheme입니다")
    if parsed.username is not None or parsed.password is not None:
        raise Neo4jTargetError("Neo4j bootstrap URI에 userinfo를 넣을 수 없습니다")
    if parsed.path not in {"", None} or parsed.query or parsed.fragment:
        raise Neo4jTargetError(
            "Neo4j bootstrap URI에 path/query/fragment를 넣을 수 없습니다"
        )
    host = parsed.hostname
    if not host:
        raise Neo4jTargetError("Neo4j bootstrap URI host가 없습니다")
    port = DEFAULT_PORT if port is None else port
    if not 1 <= port <= 65535:
        raise Neo4jTargetError("Neo4j bootstrap URI port 범위가 잘못됐습니다")
    canonical_host = host.lower()
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    return f"{scheme}://{canonical_host}:{port}/{database}"


def target_fingerprint(uri: str, database: str) -> str:
    return hashlib.sha256(canonical_target_text(uri, database).encode()).hexdigest()


def _required(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "")
    if not isinstance(value, str) or not value.strip():
        raise Neo4jTargetError(f"Neo4j bootstrap 설정이 비어 있습니다: {key}")
    return value.strip()


def load_neo4j_bootstrap_target(
    database: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Neo4jBootstrapTarget:
    """Load only the five loader-specific keys and validate before connect."""

    validate_database_name(database)
    source = os.environ if environ is None else environ
    values = {key: _required(source, key) for key in BOOTSTRAP_ENV_KEYS}
    uri = values["NEO4J_BOOTSTRAP_URI"]
    actual = target_fingerprint(uri, database)
    expected = values["NEO4J_BOOTSTRAP_ALLOWED_TARGET_SHA256"]
    if not SHA256_PATTERN.fullmatch(expected):
        raise Neo4jTargetError("Neo4j target fingerprint 형식이 잘못됐습니다")
    if not hmac.compare_digest(actual, expected):
        raise Neo4jTargetError("Neo4j target fingerprint가 allowlist와 다릅니다")
    backup_root = Path(values["NEO4J_BOOTSTRAP_BACKUP_ROOT"]).expanduser()
    if not backup_root.is_absolute():
        raise Neo4jTargetError("Neo4j backup root는 절대경로여야 합니다")
    return Neo4jBootstrapTarget(
        uri=uri,
        username=values["NEO4J_BOOTSTRAP_USER"],
        password=values["NEO4J_BOOTSTRAP_PASSWORD"],
        database=database,
        target_fingerprint_sha256=actual,
        backup_root=str(backup_root),
    )


def validate_connected_database(session: Any, database: str) -> None:
    """Fail closed unless Neo4j reports exactly the requested database."""

    try:
        rows = list(session.run("CALL db.info() YIELD name RETURN name"))
    except Exception as exc:  # driver exceptions are intentionally not exposed
        raise Neo4jTargetError("연결된 Neo4j database를 확인할 수 없습니다") from exc
    if len(rows) != 1:
        raise Neo4jTargetError("Neo4j database identity 응답 수가 잘못됐습니다")
    row = rows[0]
    try:
        actual = row["name"]
    except (KeyError, TypeError) as exc:
        raise Neo4jTargetError(
            "Neo4j database identity 응답 형식이 잘못됐습니다"
        ) from exc
    if actual != database:
        raise Neo4jTargetError("연결된 Neo4j database가 요청 target과 다릅니다")
