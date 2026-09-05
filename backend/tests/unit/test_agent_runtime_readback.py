from __future__ import annotations

import pytest

from app.agent.runtime_readback import PROFILES, validate_readback


def _payload(profile):
    database, level, enabled = PROFILES[profile]
    return {
        "database": database,
        "database_user": "kosa_app",
        "autonomy_level": level,
        "level3_enabled": enabled,
        "ack_matches_receipt": profile == "production_level3",
        "budget_policy": {
            "level12_total": 8,
            "level3_total": 10,
            "send": 2,
            "same_tool_attempts": 4,
            "selector_steps": 10,
        },
    }


@pytest.mark.parametrize("profile", PROFILES)
def test_named_profiles_own_their_expected_runtime(profile):
    payload = _payload(profile)
    validate_readback(payload, profile)
    for other in PROFILES.keys() - {profile}:
        with pytest.raises(ValueError, match="AUTONOMY_LEVEL_NOT_READY"):
            validate_readback(payload, other)


@pytest.mark.parametrize(
    "key",
    ["level12_total", "level3_total", "send", "same_tool_attempts", "selector_steps"],
)
def test_budget_drift_is_rejected(key):
    payload = _payload("e2e_level3")
    payload["budget_policy"][key] += 1
    with pytest.raises(ValueError, match="AUTONOMY_LEVEL_NOT_READY"):
        validate_readback(payload, "e2e_level3")


def test_production_ack_is_not_substituted_by_valid_budget():
    payload = _payload("production_level3")
    payload["ack_matches_receipt"] = False
    with pytest.raises(ValueError, match="AUTONOMY_LEVEL_NOT_READY"):
        validate_readback(payload, "production_level3")


@pytest.mark.parametrize("key,value", [("autonomy_level", 3.0), ("level3_enabled", 1)])
def test_runtime_types_are_not_coerced(key, value):
    payload = _payload("e2e_level3")
    payload[key] = value
    with pytest.raises(ValueError, match="AUTONOMY_LEVEL_NOT_READY"):
        validate_readback(payload, "e2e_level3")


@pytest.mark.parametrize("key", _payload("e2e_level3")["budget_policy"])
def test_budget_float_alias_is_rejected(key):
    payload = _payload("e2e_level3")
    payload["budget_policy"][key] = float(payload["budget_policy"][key])
    with pytest.raises(ValueError, match="AUTONOMY_LEVEL_NOT_READY"):
        validate_readback(payload, "e2e_level3")
