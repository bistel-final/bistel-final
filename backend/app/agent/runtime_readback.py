"""V5-C-7.1 비공개 runtime readback. public health 응답은 확장하지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text

from app.agent.level3_gate import receipt_matches
from app.common import config
from app.common.db import get_app_engine

PROFILES = {
    "production_level2": ("kosa_agent", 2, False),
    "e2e_level3": ("kosa_agent_e2e", 3, True),
    "production_level3": ("kosa_agent", 3, True),
}


def validate_readback(payload: Mapping[str, Any], profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError("READBACK_PROFILE_INVALID")
    database, level, enabled = PROFILES[profile]
    if (
        payload.get("database"),
        payload.get("autonomy_level"),
        payload.get("level3_enabled"),
    ) != (
        database,
        level,
        enabled,
    ) or payload.get("database_user") != "kosa_app":
        raise ValueError("AUTONOMY_LEVEL_NOT_READY")
    if payload.get("budget_policy") != {
        "level12_total": 8,
        "level3_total": 10,
        "send": 2,
        "same_tool_attempts": 4,
        "selector_steps": 10,
    }:
        raise ValueError("AUTONOMY_LEVEL_NOT_READY")
    if (
        profile == "production_level3"
        and payload.get("ack_matches_receipt") is not True
    ):
        raise ValueError("AUTONOMY_LEVEL_NOT_READY")


def collect_readback() -> dict[str, Any]:
    from app.agent.react import REACT_MAX_STEPS
    from app.agent.tools import SEND_ACTION_BUDGET

    try:
        with get_app_engine().connect() as connection:
            database, user = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
    except Exception as exc:
        raise ValueError("RUNTIME_IDENTITY_UNAVAILABLE") from exc
    ack = config.AGENT_LEVEL3_DEMO_ACK
    return {
        "schema_version": "agent-runtime-readback-v1",
        "database": database,
        "database_user": user,
        "autonomy_level": config.AGENT_AUTONOMY_LEVEL,
        "level3_enabled": config.AGENT_LEVEL3_ENABLED,
        "demo_ack": ack,
        "ack_matches_receipt": bool(
            ack
            and receipt_matches(
                ack,
                evaluation_artifact_path=config.AGENT_FAULT_EVAL_ARTIFACT_PATH,
            )
        ),
        "budget_policy": {
            "level12_total": config.AGENT_MAX_TOOL_CALLS,
            "level3_total": config.AGENT_LEVEL3_MAX_TOOL_CALLS,
            "send": SEND_ACTION_BUDGET,
            "same_tool_attempts": config.AGENT_MAX_RETRY + 1,
            "selector_steps": REACT_MAX_STEPS,
        },
    }
