"""V5-C-7.1 private E2E preparation readback; no workload or public API.

Only the app-role cluster identity and effective delivery recipients leave the
container. Never print this projection publicly: exact recipients are private.
Runtime config/engine imports are delayed until the explicit live entry point.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import text

from app.agent.release_artifacts import EvidenceError, EvidenceModel, parse_json
from app.agent.release_prepared import DbIdentity, canonical_recipients

IDENTITY_SQL = text("""
    SELECT current_database() AS database_name, current_user AS role_name,
           current_setting('transaction_read_only') AS read_only,
           current_setting('transaction_isolation') AS isolation,
           system_identifier::text AS system_identifier
    FROM pg_catalog.pg_control_system()
""")


class PreparationContext(EvidenceModel):
    schema_version: Literal["level3-preparation-context-v1"]
    identity: DbIdentity
    recipients: list[str] = Field(min_length=1, max_length=10)


def read_context(engine: Any, settings: Any) -> PreparationContext:
    """Fresh read-only transaction, no privileged fallback or business SELECT.

    Compare recipient v2 with the production parser's *effective* recipients.
    In particular, refuse case-fold deduplication ambiguity instead of granting
    a list that differs from what the sender would actually use.
    """
    try:
        from app.agent.email_delivery import _parse_recipients

        if (
            type(settings.AGENT_AUTONOMY_LEVEL) is not int
            or settings.AGENT_AUTONOMY_LEVEL != 3
            or settings.AGENT_LEVEL3_ENABLED is not True
            or settings.AGENT_LEVEL3_DEMO_ACK not in (None, "")
        ):
            raise EvidenceError("PREPARATION_CONTEXT_ENV_INVALID")
        effective = canonical_recipients(
            list(_parse_recipients(settings.AGENT_EMAIL_RECIPIENTS))
        )
        if effective != canonical_recipients(
            settings.AGENT_EMAIL_RECIPIENTS.split(",")
        ):
            raise EvidenceError("PREPARATION_CONTEXT_RECIPIENT_INVALID")
        host = engine.url.host
        if (
            engine.url.database != "kosa_agent_e2e"
            or engine.url.username != "kosa_app"
            or type(host) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]+", host) is None
        ):
            raise EvidenceError("PREPARATION_CONTEXT_TARGET_INVALID")
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            observed = dict(connection.execute(IDENTITY_SQL).mappings().one())
            system_id = observed.pop("system_identifier", None)
            if observed != {
                "database_name": "kosa_agent_e2e",
                "role_name": "kosa_app",
                "read_only": "on",
                "isolation": "repeatable read",
            }:
                raise EvidenceError("PREPARATION_CONTEXT_IDENTITY_INVALID")
            return PreparationContext(
                schema_version="level3-preparation-context-v1",
                identity=DbIdentity(
                    host_alias=host,
                    database="kosa_agent_e2e",
                    current_database="kosa_agent_e2e",
                    system_identifier=system_id,
                ),
                recipients=effective,
            )
    except EvidenceError:
        raise
    except Exception:
        # Config/DB/parser exceptions may contain credentials or addresses.
        raise EvidenceError("PREPARATION_CONTEXT_READ_FAILED") from None


def collect_current_context() -> PreparationContext:
    """Explicit container-only live entry point; imports do not send anything."""
    try:
        from app.common import config
        from app.common.db import get_app_engine

        return read_context(get_app_engine(), config)
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("PREPARATION_CONTEXT_READ_FAILED") from None


def docker_context(container_id: str) -> PreparationContext:
    """Read by independently pinned immutable ID, without env overrides."""
    if (
        type(container_id) is not str
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise EvidenceError("PREPARATION_CONTEXT_CONTAINER_INVALID")
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-B",
                "/workspace/backend/scripts/read_preparation_context.py",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError
        return PreparationContext.model_validate(parse_json(result.stdout))
    except Exception:
        raise EvidenceError("PREPARATION_CONTEXT_READ_FAILED") from None
