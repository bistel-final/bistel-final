from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import comparison
from scripts import compare_autonomy_levels as runner
from scripts import derive_agent_justification as derivation
from scripts import verify_agent_justification as verifier

REVISION = "a" * 40
DIGEST = "b" * 64
DERIVED_REVISION = "c" * 40
DERIVATION_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures/v5_c_7_1/derivation/lazy_import_commit_diff.json"
)


def test_executor_module_imports_do_not_load_detection_stack() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from scripts import compare_autonomy_levels, "
                "observe_agent_justification; "
                "assert 'sklearn' not in sys.modules; "
                "assert 'numpy' not in sys.modules; "
                "assert 'app.detection.service' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


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


def _derived_step(seq: int, **updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "seq": seq,
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
    values.update(updates)
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


def _observational_not_established(level_sha: str = DIGEST) -> dict[str, Any]:
    payload = _observational(level_sha)
    for row in payload["level3_attempts"]:
        if row["fixture_id"] == "CF-1":
            row["step_projections"] = [
                _derived_step(
                    1,
                    observation_source="FDC",
                    observation_features=["OOS_ABOVE_UPPER"],
                    observed_tool="get_fdc_summary",
                    next_tool="get_equipment_context",
                ),
                _derived_step(
                    2,
                    observation_source="EQUIPMENT",
                    observed_tool="get_equipment_context",
                    next_tool="search_documents",
                    next_query_features=["DIRECTION_ABOVE"],
                ),
            ]
            row["baseline_delta_kind"] = "none"
    payload["metrics"]["baseline_delta_ratio"]["CF-1"] = 0.0
    payload["agent_justification_verdict"] = "NOT_ESTABLISHED"
    return payload


def _source_check() -> dict[str, Any]:
    changed = sorted(comparison.DERIVATION_STRICT_SCRIPTS)
    hunks = {
        path: list(comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES)
        for path in comparison.DERIVATION_STRICT_SCRIPTS
    }
    return comparison.build_derivation_source_check(
        base_revision=REVISION,
        head_revision=DERIVED_REVISION,
        changed_backend_files=changed,
        script_hunks=hunks,
    )


def _v2_payload(
    source: dict[str, Any] | None = None,
    *,
    source_sha256: str = DIGEST,
    level_sha256: str = DIGEST,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_payload = source or _observational_not_established(level_sha256)
    payload = comparison.derive_agent_justification_v2(
        source_payload,
        source_revision=DERIVED_REVISION,
        derived_from_sha256=source_sha256,
        level_comparison_sha256=level_sha256,
        level_comparison_revision=REVISION,
        derivation_source_check=_source_check(),
    )
    return source_payload, payload


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


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            [
                _derived_step(
                    1,
                    observation_source="FDC",
                    observation_features=["OOS_ABOVE_UPPER"],
                    observed_tool="get_fdc_summary",
                    next_tool="get_equipment_context",
                ),
                _derived_step(
                    2,
                    observation_source="EQUIPMENT",
                    observed_tool="get_equipment_context",
                    next_tool="search_documents",
                    next_query_features=["DIRECTION_ABOVE"],
                ),
            ],
            "a",
        ),
        (
            [
                _derived_step(
                    1,
                    observation_source="FDC",
                    observation_features=["NEW_PARAMETER_ID"],
                    observed_tool="get_fdc_summary",
                    new_identifiers=["PARAM-DYNAMIC"],
                    next_tool="get_equipment_context",
                ),
                _derived_step(
                    2,
                    observation_source="EQUIPMENT",
                    observed_tool="get_equipment_context",
                    next_tool="search_documents",
                    next_query_parameter_ids=["PARAM-DYNAMIC"],
                ),
            ],
            "c",
        ),
    ],
)
def test_v2_delta_kind_accumulates_only_forward(
    steps: list[dict[str, Any]], expected: str
) -> None:
    assert comparison.derive_baseline_delta_kind(_baseline(), steps) == "none"
    assert comparison.derive_baseline_delta_kind_v2(_baseline(), steps) == expected
    backwards = copy.deepcopy(list(reversed(steps)))
    for seq, step in enumerate(backwards, start=1):
        step["seq"] = seq
    assert comparison.derive_baseline_delta_kind_v2(_baseline(), backwards) == "none"


def test_v2_derivation_preserves_v1_and_recomputes_established_verdict() -> None:
    source, payload = _v2_payload()

    assert payload["agent_justification_verdict"] == "ESTABLISHED"
    assert payload["previous_verdict"] == "NOT_ESTABLISHED"
    assert payload["level2_attempts"] == source["level2_attempts"]
    assert payload["level3_attempts"] == source["level3_attempts"]
    assert payload["pairs"] == source["pairs"]
    assert [row["pair_id"] for row in payload["derived_attempts"]] == sorted(
        row["pair_id"] for row in payload["derived_attempts"]
    )
    assert payload["derivation_rules_sha256"] == comparison.DERIVATION_RULES_SHA256
    assert (
        comparison.validate_agent_justification_v2(
            payload,
            derived_from=source,
            derived_from_sha256=DIGEST,
            level_comparison_sha256=DIGEST,
            level_comparison_revision=REVISION,
        )
        == "ESTABLISHED"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["level3_attempts"][0].__setitem__("latency_ms", 11),
        lambda p: p["level3_attempts"][0].__setitem__("unexpected", True),
        lambda p: p["level3_attempts"][0].__setitem__("baseline_delta_kind", "a"),
        lambda p: p["derived_attempts"][0].__setitem__("baseline_delta_kind", "none"),
        lambda p: p["derived_attempts"][0].__setitem__("fdc_delta", 99),
        lambda p: p["derived_attempts"].pop(),
        lambda p: p["derived_attempts"].append(copy.deepcopy(p["derived_attempts"][0])),
        lambda p: p["pairs"][0].__setitem__("recall_delta", 99.0),
        lambda p: p["pairs"][0].__setitem__("fdc_delta", 0),
        lambda p: p["pairs"].pop(),
        lambda p: p["pairs"].append(copy.deepcopy(p["pairs"][0])),
        lambda p: p.__setitem__("run_revision", "d" * 40),
        lambda p: p.__setitem__("source_revision", "d" * 40),
        lambda p: p.__setitem__("derivation_rules_sha256", "0" * 64),
        lambda p: p.__setitem__("previous_verdict", "ESTABLISHED"),
        lambda p: p["cf5"].__setitem__("independent_post_change_validation", True),
        lambda p: p["derivation_source_check"]["changed_backend_files"].append(
            "backend/app/agent/graph.py"
        ),
        lambda p: p["metrics"].__setitem__("recall_delta_median", -1.0),
        lambda p: p.__setitem__("agent_justification_verdict", "NOT_ESTABLISHED"),
    ],
)
def test_v2_observational_mutations_are_rejected(mutate: Any) -> None:
    source, payload = _v2_payload()
    mutate(payload)
    with pytest.raises(comparison.ComparisonArtifactError):
        comparison.validate_agent_justification_v2(
            payload,
            derived_from=source,
            derived_from_sha256=DIGEST,
            level_comparison_sha256=DIGEST,
            level_comparison_revision=REVISION,
        )


def test_v2_rejects_different_source_and_source_file_sha() -> None:
    source, payload = _v2_payload()
    other = copy.deepcopy(source)
    other["llm"]["selector_model_revision"] = "different-model"

    with pytest.raises(comparison.ComparisonArtifactError):
        comparison.validate_agent_justification_v2(
            payload,
            derived_from=other,
            derived_from_sha256=DIGEST,
            level_comparison_sha256=DIGEST,
            level_comparison_revision=REVISION,
        )
    with pytest.raises(
        comparison.ComparisonArtifactError, match="ARTIFACT_SHA_MISMATCH"
    ):
        comparison.validate_agent_justification_v2(
            payload,
            derived_from=source,
            derived_from_sha256="d" * 64,
            level_comparison_sha256=DIGEST,
            level_comparison_revision=REVISION,
        )


def test_actual_lazy_import_commit_diff_matches_strict_fixture() -> None:
    fixture = json.loads(DERIVATION_FIXTURE.read_text(encoding="utf-8"))
    hunks = {
        path: derivation._changed_lines(diff)
        for path, diff in fixture["script_diffs"].items()
    }
    result = comparison.build_derivation_source_check(
        base_revision=fixture["base_revision"],
        head_revision=fixture["head_revision"],
        changed_backend_files=fixture["changed_backend_files"],
        script_hunks=hunks,
    )

    assert result["allowlist_verdict"] == "PASS"
    assert result["strict_hunk_verdict"] == "PASS"


@pytest.mark.parametrize(
    "mutation",
    [
        [*comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES, "+# comment"],
        [
            comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES[0],
            "+       from app.detection.service import FdcSummaryService",
            "+",
        ],
        [*comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES, "+print('changed')"],
    ],
)
def test_derivation_source_check_rejects_non_exact_script_hunks(
    mutation: list[str],
) -> None:
    hunks = {
        path: list(comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES)
        for path in comparison.DERIVATION_STRICT_SCRIPTS
    }
    hunks[comparison.DERIVATION_STRICT_SCRIPTS[0]] = mutation
    with pytest.raises(
        comparison.ComparisonArtifactError, match="DERIVATION_SOURCE_CHANGED"
    ):
        comparison.build_derivation_source_check(
            base_revision=REVISION,
            head_revision=DERIVED_REVISION,
            changed_backend_files=sorted(comparison.DERIVATION_STRICT_SCRIPTS),
            script_hunks=hunks,
        )


def test_derivation_source_check_rejects_file_outside_allowlist() -> None:
    with pytest.raises(
        comparison.ComparisonArtifactError, match="DERIVATION_SOURCE_CHANGED"
    ):
        comparison.build_derivation_source_check(
            base_revision=REVISION,
            head_revision=DERIVED_REVISION,
            changed_backend_files=[
                *sorted(comparison.DERIVATION_STRICT_SCRIPTS),
                "backend/app/agent/graph.py",
            ],
            script_hunks={
                path: list(comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES)
                for path in comparison.DERIVATION_STRICT_SCRIPTS
            },
        )


def test_derivation_source_check_rejects_non_hunk_script_metadata() -> None:
    with pytest.raises(
        comparison.ComparisonArtifactError, match="DERIVATION_SOURCE_CHANGED"
    ):
        derivation._changed_lines(
            "\n".join(
                [
                    "diff --git a/script b/script",
                    "old mode 100644",
                    "new mode 100755",
                    *comparison.DERIVATION_ALLOWED_IMPORT_CHANGE_LINES,
                ]
            )
        )


def test_verifier_requires_derived_from_exactly_for_v2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    level_path = tmp_path / "level.json"
    level_path.write_text(json.dumps(_level_artifact()), encoding="utf-8")
    level_sha256 = comparison.file_sha256(level_path)
    source = _observational_not_established(level_sha256)
    source_path = tmp_path / "v1.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    source_sha256 = comparison.file_sha256(source_path)
    _, payload = _v2_payload(
        source,
        source_sha256=source_sha256,
        level_sha256=level_sha256,
    )
    artifact_path = tmp_path / "v2.json"
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        verifier.main(
            [
                "--artifact",
                str(artifact_path),
                "--level-comparison",
                str(level_path),
            ]
        )
        == 1
    )
    assert "DERIVED_FROM_REQUIRED" in capsys.readouterr().err
    assert (
        verifier.main(
            [
                "--artifact",
                str(source_path),
                "--level-comparison",
                str(level_path),
                "--derived-from",
                str(source_path),
            ]
        )
        == 1
    )
    assert "DERIVED_FROM_NOT_ALLOWED" in capsys.readouterr().err
    assert (
        verifier.main(
            [
                "--artifact",
                str(artifact_path),
                "--level-comparison",
                str(level_path),
                "--derived-from",
                str(source_path),
            ]
        )
        == 0
    )
    assert "AGENT_JUSTIFICATION_OK verdict=ESTABLISHED" in capsys.readouterr().out


def test_derivation_executor_writes_immutable_valid_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    level_path = tmp_path / "level.json"
    level_path.write_text(json.dumps(_level_artifact()), encoding="utf-8")
    source = _observational_not_established(comparison.file_sha256(level_path))
    source_path = tmp_path / "v1.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    artifact_path = tmp_path / "v2.json"
    source_check = _source_check()
    monkeypatch.setattr(
        derivation, "_verify_clean_revision", lambda _expected: DERIVED_REVISION
    )
    monkeypatch.setattr(
        derivation,
        "inspect_derivation_source",
        lambda **_kwargs: source_check,
    )

    digest, recorded_check = derivation.execute(
        source=source_path,
        level_comparison=level_path,
        revision=DERIVED_REVISION,
        artifact=artifact_path,
    )

    assert digest == comparison.file_sha256(artifact_path)
    assert recorded_check == source_check
    assert os.stat(artifact_path).st_mode & 0o777 == 0o600
    assert comparison.load_json(artifact_path)["agent_justification_verdict"] == (
        "ESTABLISHED"
    )
    with pytest.raises(
        comparison.ComparisonArtifactError, match="ARTIFACT_CLOBBER_BLOCKED"
    ):
        derivation.execute(
            source=source_path,
            level_comparison=level_path,
            revision=DERIVED_REVISION,
            artifact=artifact_path,
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
