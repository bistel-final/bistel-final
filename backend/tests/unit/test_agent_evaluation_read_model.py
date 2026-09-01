"""Agent 평가 artifact read-only projection과 독립 Empty 계약."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.evaluation_read_model import load_agent_evaluations
from app.agent.evaluation_schemas import AgentEvaluationResponse
from app.agent.golden_flow import (
    GoldenFlowResult,
    GoldenPhase,
    PhaseResult,
    PhaseStatus,
)
from app.agent.golden_summary import build_golden_summary
from app.evaluation import fault_5class as fault


def _fault_artifact() -> dict[str, object]:
    keys = tuple(
        fault.IncidentKey(f"LOT{index:03d}", f"EQP{index:02d}-PM1")
        for index in range(12)
    )
    labels = ("FOC", "FOC", "RFM", "MFD", "TMD", "OTH", "OTH")
    records = tuple(
        fault.PredictionRecord(
            incident=key,
            agent_run_id=f"RUN-{index:04d}",
            predicted_fault_code=labels[index] if index < 7 else "FOC",
            supporting_alarm_tokens=(f"TRACE:TA-{index}",),
            available_alarm_tokens=(f"TRACE:TA-{index}",),
            actual_action="MONITORING",
            model_version="model-v1",
            prompt_version="prompt-v1",
            policy_version="policy-v1",
        )
        for index, key in enumerate(keys)
    )
    rows: list[fault.IncidentFaultLabelRow] = []
    for index, key in enumerate(keys):
        rows.append(fault.IncidentFaultLabelRow(key, "NRM"))
        if index < 7:
            rows.append(fault.IncidentFaultLabelRow(key, labels[index]))
    frozen = fault.freeze_predictions(records)
    result = fault.evaluate_fault_5class(
        frozen,
        rows,
        {key: "MONITORING" for key in keys},
    )
    provenance = fault.ArtifactProvenance(
        golden_evidence_sha256="a" * 64,
        baseline_snapshot_artifact_sha256="b" * 64,
        oracle_sha256="c" * 64,
        population_sha256="d" * 64,
        prediction_hash=frozen.prediction_hash,
        runtime_provenance_sha256="e" * 64,
        evaluation_provenance_sha256="f" * 64,
        shared_key_sha256="1" * 64,
        code_revision="2" * 40,
    )
    return fault.artifact_to_dict(result, provenance)


def _golden_artifact() -> dict[str, object]:
    result = GoldenFlowResult(
        tuple(
            PhaseResult(phase, PhaseStatus.PASS, (), {"checked": 1})
            for phase in GoldenPhase
        )
    )
    return build_golden_summary(
        result,
        dataset_epoch="fdc_final_20260818",
        source_manifest_sha256="a" * 64,
        evidence_manifest_sha256="b" * 64,
    )


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_two_valid_artifacts_are_projected_without_hashes_or_paths(
    tmp_path: Path,
) -> None:
    fault_path = tmp_path / "fault.json"
    golden_path = tmp_path / "golden.json"
    _write(fault_path, _fault_artifact())
    _write(golden_path, _golden_artifact())

    response = load_agent_evaluations(
        fault_path=str(fault_path),
        golden_path=str(golden_path),
    ).model_dump(mode="json")

    assert response["fault_5class_empty_reason"] is None
    assert response["golden_flow_empty_reason"] is None
    assert response["fault_5class"]["classification"]["population_count"] == 7
    assert len(response["golden_flow"]["phases"]) == 7
    serialized = json.dumps(response)
    assert "sha256" not in serialized
    assert str(tmp_path) not in serialized
    assert "excluded_no_injected_fault_incidents" not in serialized


def test_artifacts_have_independent_empty_states(tmp_path: Path) -> None:
    fault_path = tmp_path / "fault.json"
    invalid_golden = tmp_path / "golden.json"
    _write(fault_path, _fault_artifact())
    _write(invalid_golden, {"status": "PASS"})

    response = load_agent_evaluations(
        fault_path=str(fault_path),
        golden_path=str(invalid_golden),
    )

    assert response.fault_5class is not None
    assert response.fault_5class_empty_reason is None
    assert response.golden_flow is None
    assert response.golden_flow_empty_reason == "CONTRACT_VIOLATION"


def test_unconfigured_missing_relative_and_symlink_are_fail_closed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "fault.json"
    link = tmp_path / "fault-link.json"
    _write(target, _fault_artifact())
    link.symlink_to(target)

    unconfigured = load_agent_evaluations(fault_path=None, golden_path=None)
    missing = load_agent_evaluations(
        fault_path=str(tmp_path / "missing.json"),
        golden_path=str(tmp_path / "missing-golden.json"),
    )
    unsafe = load_agent_evaluations(
        fault_path="relative.json",
        golden_path=str(link),
    )

    assert unconfigured.fault_5class_empty_reason == "NOT_CONFIGURED"
    assert missing.fault_5class_empty_reason == "NOT_FOUND"
    assert unsafe.fault_5class_empty_reason == "CONTRACT_VIOLATION"
    assert unsafe.golden_flow_empty_reason == "CONTRACT_VIOLATION"


def test_response_requires_value_xor_empty_reason_for_each_artifact() -> None:
    with pytest.raises(ValidationError, match="정확히 하나"):
        AgentEvaluationResponse(
            fault_5class=None,
            golden_flow=None,
            fault_5class_empty_reason=None,
            golden_flow_empty_reason="NOT_FOUND",
        )
