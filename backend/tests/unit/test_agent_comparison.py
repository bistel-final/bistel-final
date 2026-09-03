from __future__ import annotations

import copy
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import comparison
from scripts import compare_autonomy_levels as runner

REVISION = "a" * 40
DIGEST = "b" * 64


def _level_artifact() -> dict[str, Any]:
    incidents = []
    for index in range(12):
        action = "MONITORING" if index < 5 else "WARNING" if index < 9 else "EQP_HOLD"
        rows = []
        for level in (1, 2, 3):
            rows.append(
                {
                    "level": level,
                    "outcome": "COMPLETED",
                    "action": action,
                    "tool_path": ["get_fdc_summary"],
                    "tool_calls": [{"tool": "get_fdc_summary", "status": "SUCCESS"}],
                    "tokens": {
                        "hypothesis": 10,
                        "selector": 0 if level < 3 else 5,
                    },
                    "latency_ms": 1,
                    "guard_rejections": 0,
                    "degraded": False,
                }
            )
        incidents.append(
            {
                "identity": {
                    "lot_id": f"LOT-{index}",
                    "chamber_id": f"EQP-{index}",
                    "requested_alarm": {
                        "source": "TRACE",
                        "alarm_id": f"TA-{index}",
                    },
                },
                "levels": rows,
            }
        )
    return {
        "schema_version": "level-comparison-v1",
        "source_revision": REVISION,
        "fixture_source_sha256": DIGEST,
        "fixture_projection_sha256": comparison.canonical_sha256(
            [item["identity"] for item in incidents]
        ),
        "initial_snapshot_sha256": DIGEST,
        "SYNTHETIC_DETERMINISTIC_BENCHMARK": True,
        "PRODUCTION_PERFORMANCE_NOT_CLAIMED": True,
        "EXPERIMENT_ONLY": True,
        "runs": 36,
        "level_configs": comparison.EXPECTED_LEVEL_CONFIGS,
        "incidents": incidents,
        "aggregate": {
            "1": {
                "outcome_counts": {"COMPLETED": 12},
                "completion_rate": 1.0,
                "tokens": {"hypothesis": 120, "selector": 0},
                "tool_calls": 12,
                "latency_ms": 12,
            },
            "2": {
                "outcome_counts": {"COMPLETED": 12},
                "completion_rate": 1.0,
                "tokens": {"hypothesis": 120, "selector": 0},
                "tool_calls": 12,
                "latency_ms": 12,
            },
            "3": {
                "outcome_counts": {"COMPLETED": 12},
                "completion_rate": 1.0,
                "tokens": {"hypothesis": 120, "selector": 60},
                "tool_calls": 12,
                "latency_ms": 12,
            },
        },
        "safety": {
            "send_action_selected": 0,
            "hitl_bypass": 0,
            "pre_approval_mes": 0,
        },
        "contract_verdict": "ADAPTIVE_LOOP_CONTRACT_PASS",
    }


def _projection(**values: Any) -> dict[str, Any]:
    value = dict(values)
    value["sha256"] = comparison.canonical_sha256(value)
    return value


def _baseline() -> dict[str, Any]:
    return _projection(
        tool_order=["get_fdc_summary", "search_documents"],
        query_features=[
            "CHAMBER",
            "FIRST_FDC_PARAMETER_IDS",
            "REPRESENTATIVE_ALARM",
            "ROUTE_STEP",
        ],
        query_parameter_ids=["PARAM-BASE"],
        query_step_ids=["STEP-BASE"],
        evidence_ids_available=["DOC-X"],
    )


def _step(fixture: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "seq": 1,
        "observation_source": "DOCUMENTS",
        "observation_features": [],
        "observed_tool": "search_documents",
        "observed_status": "SUCCESS",
        "new_identifiers": [],
        "next_tool": "stop",
        "next_query_features": [],
        "next_query_parameter_ids": [],
        "next_query_step_ids": [],
        "document_query_sha256": DIGEST,
    }
    if fixture == "CF-1":
        values.update(
            observation_source="FDC",
            observation_features=["OOS_ABOVE_UPPER"],
            observed_tool="get_fdc_summary",
            next_tool="search_documents",
            next_query_features=["DIRECTION_ABOVE"],
        )
    elif fixture == "CF-2":
        values.update(
            observation_source="FDC",
            observation_features=["NEW_PARAMETER_ID"],
            observed_tool="get_fdc_summary",
            new_identifiers=["PARAM-X"],
            next_tool="search_documents",
            next_query_parameter_ids=["PARAM-X"],
        )
    elif fixture == "CF-3":
        values.update(
            observation_source="TOOL_FAILURE",
            observation_features=["TIMEOUT"],
            observed_tool="search_documents",
            observed_status="TIMEOUT",
            next_tool="get_equipment_context",
            next_query_features=["FAILURE_ALTERNATE"],
        )
    return _projection(**values)


def _ids(row: dict[str, Any], key: str, values: list[str]) -> None:
    row[key] = values
    row[f"{key}_sha256"] = comparison.canonical_sha256(values)


def _attempt(fixture: str, attempt: int, level: int) -> dict[str, Any]:
    if level == 2 and fixture == "CF-4":
        tool_count = 4
    elif level == 3 and fixture == "CF-4":
        tool_count = 2
    else:
        tool_count = 3
    row: dict[str, Any] = {
        "fixture_id": fixture,
        "attempt_no": attempt,
        "pair_id": f"{fixture}:{attempt}",
        "initial_snapshot_sha256": DIGEST,
        "terminal": "decide_action",
        "completion": True,
        "tool_path": ["get_fdc_summary"] * tool_count,
        "tool_input_digests": [DIGEST] * tool_count,
        "document_query_sha256": DIGEST,
        "unsupported_count": 0,
        "recall": 1.0,
        "tokens": {
            "hypothesis": {"input": 10, "output": 5},
            "selector": None if level == 2 else {"input": 5, "output": 2},
        },
        "latency_ms": 10,
        "baseline_projection": _baseline() if level == 2 else None,
        "step_projections": None if level == 2 else [_step(fixture)],
        "react_trace_summary": None if level == 2 else [],
    }
    if level == 3:
        row["baseline_delta_kind"] = {"CF-1": "a", "CF-2": "c", "CF-3": "b"}.get(
            fixture, "none"
        )
    for key in ("cited_evidence_ids", "available_evidence_ids"):
        _ids(row, key, ["DOC-X"])
    return row


def _observational(level_sha: str = DIGEST) -> dict[str, Any]:
    level2 = [_attempt(f, a, 2) for f in comparison.FIXTURES for a in (1, 2)]
    level3 = [_attempt(f, a, 3) for f in comparison.FIXTURES for a in (1, 2)]
    pairs = []
    for fixture in comparison.FIXTURES:
        for attempt in (1, 2):
            pairs.append(
                {
                    "pair_id": f"{fixture}:{attempt}",
                    "recall_delta": 0.0,
                    "completion_delta": 0,
                    "tool_delta": 2 if fixture == "CF-4" else 0,
                }
            )
    oracle = []
    for fixture in comparison.FIXTURES:
        row = {"fixture_id": fixture}
        _ids(row, "required_evidence_ids", ["DOC-X"])
        oracle.append(row)
    return {
        "schema_version": "agent-justification-v1",
        "source_revision": REVISION,
        "level_comparison_sha256": level_sha,
        "fixture_sha256": DIGEST,
        "oracle_sha256": DIGEST,
        "OBSERVATIONAL_REAL_LLM": True,
        "attempts_per_fixture": 2,
        "llm": {
            "hypothesis_model_revision": "model",
            "selector_model_revision": "model",
            "temperature": 0,
            "seed": 0,
        },
        "oracle": oracle,
        "level2_attempts": level2,
        "level3_attempts": level3,
        "pairs": pairs,
        "metrics": {
            "recall_delta_median": 0.0,
            "completion_rate": {"2": 1.0, "3": 1.0},
            "baseline_delta_ratio": {
                "CF-1": 1.0,
                "CF-2": 1.0,
                "CF-3": 1.0,
                "CF-4": 0.0,
                "CF-5": 0.0,
            },
        },
        "safety": {
            "send_action_selected": 0,
            "hitl_bypass": 0,
            "pre_approval_mes": 0,
        },
        "agent_justification_verdict": "ESTABLISHED",
    }


def test_level_comparison_recomputes_36_run_contract() -> None:
    assert (
        comparison.validate_level_comparison(_level_artifact())
        == "ADAPTIVE_LOOP_CONTRACT_PASS"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["incidents"][0]["identity"]["requested_alarm"].__setitem__(
            "alarm_id", "TA-mutated"
        ),
        lambda p: p["incidents"][0]["levels"][0]["tool_calls"][0].__setitem__(
            "status", "BROKEN"
        ),
        lambda p: p["incidents"][0]["levels"][0].__setitem__("action", "WARNING"),
        lambda p: p["safety"].__setitem__("hitl_bypass", 1),
        lambda p: p.__setitem__("contract_verdict", "ADAPTIVE_LOOP_CONTRACT_FAIL"),
        lambda p: (
            p["incidents"][0]["levels"][2]["tool_path"].__setitem__(0, "send_action"),
            p["incidents"][0]["levels"][2]["tool_calls"][0].__setitem__(
                "tool", "send_action"
            ),
        ),
    ],
)
def test_level_comparison_mutations_are_rejected(mutate: Any) -> None:
    payload = copy.deepcopy(_level_artifact())
    mutate(payload)
    with pytest.raises(comparison.ComparisonArtifactError):
        comparison.validate_level_comparison(payload)


def test_agent_justification_recomputes_projection_and_metrics() -> None:
    assert (
        comparison.validate_agent_justification(
            _observational(),
            level_comparison_sha256=DIGEST,
            level_comparison_revision=REVISION,
        )
        == "ESTABLISHED"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["level3_attempts"][0]["available_evidence_ids"].__setitem__(
            0, "DOC-Y"
        ),
        lambda p: p["level3_attempts"][0].__setitem__(
            "available_evidence_ids_sha256", "0" * 64
        ),
        lambda p: p["level3_attempts"][0].__setitem__("recall", 0.0),
        lambda p: (
            p["level3_attempts"][0]["step_projections"][0].__setitem__(
                "observation_features", []
            ),
            p["level3_attempts"][0]["step_projections"][0].__setitem__(
                "sha256",
                comparison.canonical_sha256(
                    {
                        key: value
                        for key, value in p["level3_attempts"][0]["step_projections"][
                            0
                        ].items()
                        if key != "sha256"
                    }
                ),
            ),
        ),
        lambda p: p["level3_attempts"][0].__setitem__("baseline_delta_kind", "none"),
        lambda p: p["metrics"]["baseline_delta_ratio"].__setitem__("CF-1", 0.5),
    ],
)
def test_observational_mutations_are_rejected(mutate: Any) -> None:
    payload = copy.deepcopy(_observational())
    mutate(payload)
    with pytest.raises(comparison.ComparisonArtifactError):
        comparison.validate_agent_justification(
            payload,
            level_comparison_sha256=DIGEST,
            level_comparison_revision=REVISION,
        )


def test_immutable_writer_is_mode_0600_and_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    comparison.write_immutable_json(path, {"ok": True})
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(
        comparison.ComparisonArtifactError, match="ARTIFACT_CLOBBER_BLOCKED"
    ):
        comparison.write_immutable_json(path, {"ok": False})


def test_dependency_failure_keeps_only_redacted_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = "\n".join(
        [
            "old line omitted",
            "line 2",
            "PGPASSWORD=top-secret",
            "postgresql://user:top-secret@db.example/test",
            "authorization: Bearer bearer-value",
            "line 6 root cause",
        ]
    )
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr=stderr),
    )

    with pytest.raises(runner.ComparisonExecutionError) as info:
        runner._run(["fixture"], environment={"PGPASSWORD": "top-secret"})

    assert info.value.code == "COMPARISON_DEPENDENCY_FAILED"
    assert "old line omitted" not in str(info.value)
    assert "top-secret" not in str(info.value)
    assert "bearer-value" not in str(info.value)
    assert "line 6 root cause" in str(info.value)
