from __future__ import annotations

import json

import pytest

from app.agent.level3_gate import production_level3_allowed, receipt_matches
from app.agent.runtime_composition import AgentRuntime, AgentRuntimeError

ATTEMPT = "20260903T010203Z-0123456789ab"


def test_e2e_requires_both_keys() -> None:
    assert production_level3_allowed(
        autonomy_level=3,
        enabled=True,
        database="kosa_agent_e2e",
        demo_ack=None,
        receipt_validator=lambda _value: False,
    )
    assert not production_level3_allowed(
        autonomy_level=3,
        enabled=False,
        database="kosa_agent_e2e",
        demo_ack=None,
        receipt_validator=lambda _value: True,
    )


def test_live_requires_matching_ack_and_receipt(tmp_path) -> None:
    artifact = tmp_path / "fault-5class.json"
    artifact.write_text("{}", encoding="utf-8")
    (tmp_path / "attempt.json").write_text(
        json.dumps({"attempt": ATTEMPT}), encoding="utf-8"
    )
    validator = lambda value: receipt_matches(  # noqa: E731
        value, evaluation_artifact_path=str(artifact)
    )
    assert production_level3_allowed(
        autonomy_level=3,
        enabled=True,
        database="kosa_agent",
        demo_ack=ATTEMPT,
        receipt_validator=validator,
    )
    assert not production_level3_allowed(
        autonomy_level=3,
        enabled=True,
        database="kosa_agent",
        demo_ack="20260903T010203Z-aaaaaaaaaaaa",
        receipt_validator=validator,
    )


@pytest.mark.parametrize(
    ("level", "enabled", "database"),
    [(3, False, "kosa_agent_e2e"), (3, True, "wrong"), (2, True, "kosa_agent")],
)
def test_runtime_rejects_invalid_gate_before_factory(
    level: int, enabled: bool, database: str
) -> None:
    calls: list[str] = []
    runtime = AgentRuntime(
        factory=lambda model: calls.append(model),  # type: ignore[arg-type]
        model_config=lambda: "fixture-model",
        autonomy_level=level,
        level3_enabled=enabled,
        database_name=database,
    )
    with pytest.raises(AgentRuntimeError, match="AUTONOMY_LEVEL_NOT_READY"):
        runtime.resources()
    assert calls == []
