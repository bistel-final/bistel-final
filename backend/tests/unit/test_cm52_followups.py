from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from app.agent.golden_flow import (  # noqa: E402
    GoldenFlowResult,
    GoldenPhase,
    PhaseResult,
    PhaseStatus,
)
from app.agent.golden_summary import build_golden_summary  # noqa: E402
from app.common.enums import ToolCallStatus  # noqa: E402
from app.evaluation import fault_5class as fault  # noqa: E402
from scripts import e2e_analytics_questions as questions  # noqa: E402
from scripts import e2e_reset_evidence as reset_evidence  # noqa: E402
from scripts import emit_diagnostic_targets as targets  # noqa: E402
from scripts import observe_public_databases as observer  # noqa: E402
from scripts import (  # noqa: E402
    preflight_agent_evaluation_artifacts as artifact_preflight,
)

ATTEMPT = "20260902T010203Z-0123456789ab"
REVISION = "0123456789abcdef0123456789abcdef01234567"


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return self

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _LogConnection:
    def exec_driver_sql(self, _sql: str) -> _Mappings:
        return _Mappings([{"row_count": 13, "max_id": 14, "sequence_last_value": 14}])

    def execute(self, _sql: Any, params: dict[str, int]) -> _Mappings:
        assert params == {"baseline_max": 11}
        return _Mappings(
            [
                {"nl_query_log_id": 12, "question": " 민감한   원문  "},
                {"nl_query_log_id": 13, "question": "두 번째 질문"},
                {"nl_query_log_id": 14, "question": "세 번째 질문"},
            ]
        )


def _fault_artifact(
    *,
    code_revision: str = REVISION,
    prompt_version: str = artifact_preflight.PROMPT_VERSION,
) -> dict[str, Any]:
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
            prompt_version=prompt_version,
            policy_version="policy-v1",
        )
        for index, key in enumerate(keys)
    )
    label_rows: list[fault.IncidentFaultLabelRow] = []
    for index, key in enumerate(keys):
        label_rows.append(fault.IncidentFaultLabelRow(key, "NRM"))
        if index < 7:
            label_rows.append(fault.IncidentFaultLabelRow(key, labels[index]))
    frozen = fault.freeze_predictions(records)
    result = fault.evaluate_fault_5class(
        frozen,
        label_rows,
        {key: "MONITORING" for key in keys},
    )
    return fault.artifact_to_dict(
        result,
        fault.ArtifactProvenance(
            golden_evidence_sha256="a" * 64,
            baseline_snapshot_artifact_sha256="b" * 64,
            oracle_sha256="c" * 64,
            population_sha256="d" * 64,
            prediction_hash=frozen.prediction_hash,
            runtime_provenance_sha256="e" * 64,
            evaluation_provenance_sha256="f" * 64,
            shared_key_sha256="1" * 64,
            code_revision=code_revision,
        ),
    )


def _golden_artifact() -> dict[str, Any]:
    result = GoldenFlowResult(
        tuple(
            PhaseResult(phase, PhaseStatus.PASS, (), {"checked": 1})
            for phase in GoldenPhase
        )
    )
    return build_golden_summary(
        result,
        dataset_epoch=fault.DATASET_EPOCH,
        source_manifest_sha256="9" * 64,
        evidence_manifest_sha256="a" * 64,
    )


def _write_json(path: Path, payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def _artifact_pair(tmp_path: Path) -> tuple[Path, Path, str, str, dict[str, str]]:
    root = tmp_path / "cm-5.2" / ATTEMPT
    root.mkdir(parents=True)
    fault_path = root / "fault-5class.json"
    golden_path = root / "golden-flow.json"
    fault_sha = _write_json(fault_path, _fault_artifact())
    golden_sha = _write_json(golden_path, _golden_artifact())
    environ = {
        "AGENT_FAULT_EVAL_ARTIFACT_PATH": str(fault_path),
        "AGENT_GOLDEN_FLOW_SUMMARY_PATH": str(golden_path),
        "BISTEL_SOURCE_REVISION": REVISION,
    }
    return fault_path, golden_path, fault_sha, golden_sha, environ


def _run_preflight(
    fault_path: Path,
    golden_path: Path,
    fault_sha: str,
    golden_sha: str,
    environ: dict[str, str],
    *,
    attempt_id: str = ATTEMPT,
    expect_revision: str = REVISION,
    expect_container_revision: str | None = None,
) -> None:
    artifact_preflight.preflight(
        fault_path=fault_path,
        golden_path=golden_path,
        expect_fault_sha=fault_sha,
        expect_golden_sha=golden_sha,
        expect_revision=expect_revision,
        expect_container_revision=expect_container_revision,
        attempt_id=attempt_id,
        environ=environ,
    )


def test_analytics_questions_emit_only_ordered_digests(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "digests.json"
    assert questions.main(["--ids", "12,13,14", "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    receipt = json.loads(capsys.readouterr().out)

    assert payload == questions.expected_digests([12, 13, 14])
    assert receipt == {
        "status": "PASS",
        "artifact_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert all(question not in output.read_text() for question in questions.QUESTIONS)
    assert questions.normalize_question("  A\u0301   B ") == "Á B"


def test_analytics_questions_reject_unsorted_ids_before_writing(
    tmp_path: Path,
) -> None:
    output = tmp_path / "digests.json"

    with pytest.raises(ValueError, match="ANALYTICS_IDS_INVALID"):
        questions.expected_digests([14, 12, 13])
    assert questions.main(["--ids", "14,12,13", "--output", str(output)]) == 1
    assert not output.exists()


def test_log_probe_hashes_questions_inside_snapshot_without_raw_text() -> None:
    assert observer.evidence is reset_evidence
    assert observer.digest is questions.digest
    assert observer.snapshot_observer.__globals__["evidence"] is reset_evidence
    assert observer.snapshot_observer.__globals__["reset"].evidence is reset_evidence
    payload = observer._log_verify_probe(11)(_LogConnection())
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["new_entries"][0] == [12, questions.digest("민감한 원문")]
    assert "민감한 원문" not in serialized


def _snapshot(value: str) -> dict[str, str]:
    return {"sha256": value * 64}


def _observer_states() -> tuple[dict[str, Any], dict[str, Any], list[list[Any]]]:
    expected = [[12, "1" * 64], [13, "2" * 64], [14, "3" * 64]]
    baseline = {
        "immutable": {
            "kosa_agent": _snapshot("a"),
            "kosa_text2sql": _snapshot("b"),
        },
        "strict_kosa_agent": _snapshot("c"),
        "text2sql_log": {"row_count": 10, "max_id": 11, "sequence_last_value": 11},
    }
    current = {
        "immutable": {
            "kosa_agent": _snapshot("a"),
            "kosa_text2sql": _snapshot("b"),
        },
        "strict_kosa_agent": _snapshot("c"),
        "text2sql_log": {
            "row_count": 13,
            "max_id": 14,
            "sequence_last_value": 14,
            "new_entries": expected,
        },
    }
    return baseline, current, expected


def test_observer_distinguishes_all_three_delta_failures() -> None:
    baseline, current, expected = _observer_states()
    assert observer.verify_state(baseline, current, expected)["status"] == "PASS"

    current["immutable"]["kosa_text2sql"] = _snapshot("d")
    with pytest.raises(observer.ObserverError, match="OBSERVER_DRIFT"):
        observer.verify_state(baseline, current, expected)
    current["immutable"]["kosa_text2sql"] = _snapshot("b")
    current["strict_kosa_agent"] = _snapshot("d")
    with pytest.raises(observer.ObserverError, match="PUBLIC_RUNTIME_WRITTEN"):
        observer.verify_state(baseline, current, expected)
    current["strict_kosa_agent"] = _snapshot("c")
    current["text2sql_log"]["sequence_last_value"] = 15
    with pytest.raises(observer.ObserverError, match="LOG_DELTA_MISMATCH"):
        observer.verify_state(baseline, current, expected)


def _target_rows() -> list[dict[str, Any]]:
    counts = [1] * 5 + [2] * 4 + [3] * 3
    rows: list[dict[str, Any]] = []
    ordinal = 0
    for run_index, count in enumerate(counts, start=1):
        for wafer_index in reversed(range(1, count + 1)):
            ordinal += 1
            rows.append(
                {
                    "agent_run_id": f"RUN-{run_index:04d}",
                    "lot_hist_id": f"LH-{ordinal:04d}",
                    "wafer_id": f"LOT{run_index:03d}W{wafer_index:03d}",
                    "wafer_no": wafer_index,
                }
            )
    return rows


def test_diagnostic_targets_are_exact_and_wafer_sorted() -> None:
    payload = targets.build_receipt(
        _target_rows(), database=targets.TARGET_DATABASE, attempt_id=ATTEMPT
    )

    assert payload["run_count"] == 12
    assert payload["target_count"] == 22
    assert payload["targets_per_run_distribution"] == {"1": 5, "2": 4, "3": 3}
    assert all(
        [item["target_order"] for item in run["targets"]]
        == list(range(1, len(run["targets"]) + 1))
        for run in payload["runs"]
    )
    assert payload["runs"][-1]["targets"][0]["wafer_no"] == 1


def test_diagnostic_target_sql_uses_the_runtime_success_contract() -> None:
    sql = " ".join(str(targets.TARGET_SQL).split())

    assert "tool.status = :success_status" in sql
    assert targets.TARGET_SQL.compile().params == {
        "success_status": ToolCallStatus.SUCCESS.value
    }
    assert "JOIN lot_history AS history" in sql
    assert "history.lot_hist_id = tool.input ->> 'lot_hist_id'" in sql
    assert "ORDER BY tool.agent_run_id, tool.call_seq" in sql


def test_diagnostic_target_read_failure_has_a_distinct_nonsecret_reason() -> None:
    class FailingEngine:
        def connect(self) -> Any:
            raise RuntimeError("credential-and-host-must-not-leak")

    with pytest.raises(targets.DiagnosticTargetError) as caught:
        targets._load_rows(FailingEngine(), targets.TARGET_DATABASE)

    assert caught.value.reason == "TARGET_READ_FAILED"
    assert caught.value.exit_code == 1
    assert "credential-and-host-must-not-leak" not in str(caught.value)


def test_diagnostic_targets_order_by_wafer_number_not_identifier_suffix() -> None:
    rows = _target_rows()
    run = [row for row in rows if row["agent_run_id"] == "RUN-0006"]
    by_number = {row["wafer_no"]: row for row in run}
    by_number[1]["wafer_id"] = "LOT006W999"
    by_number[2]["wafer_id"] = "LOT006W001"

    payload = targets.build_receipt(
        rows,
        database=targets.TARGET_DATABASE,
        attempt_id=ATTEMPT,
    )
    actual = next(
        item for item in payload["runs"] if item["agent_run_id"] == "RUN-0006"
    )

    assert [item["wafer_no"] for item in actual["targets"]] == [1, 2]


def test_diagnostic_target_structure_and_distribution_fail_separately() -> None:
    duplicate = _target_rows()
    duplicate.append(dict(duplicate[0]))
    with pytest.raises(targets.DiagnosticTargetError) as structure:
        targets.build_receipt(
            duplicate, database=targets.TARGET_DATABASE, attempt_id=ATTEMPT
        )
    assert structure.value.reason == "TARGET_STRUCTURE_INVALID"
    assert structure.value.exit_code == 1

    wrong_distribution = _target_rows()
    # 3-target run에서 하나를 1-target run으로 옮겨 12/22·1~3·중복 0은 유지한다.
    move_index = next(
        index
        for index, row in enumerate(wrong_distribution)
        if row["agent_run_id"] == "RUN-0010"
    )
    moved = wrong_distribution.pop(move_index)
    moved["agent_run_id"] = "RUN-0001"
    wrong_distribution.append(moved)
    with pytest.raises(targets.DiagnosticTargetError) as distribution:
        targets.build_receipt(
            wrong_distribution,
            database=targets.TARGET_DATABASE,
            attempt_id=ATTEMPT,
        )
    assert distribution.value.reason == "TARGET_DISTRIBUTION_MISMATCH"
    assert distribution.value.exit_code == 3


def test_artifact_preflight_accepts_the_bound_pair(tmp_path: Path) -> None:
    pair = _artifact_pair(tmp_path)
    _run_preflight(*pair)


@pytest.mark.parametrize(
    "prompt", ["agent-hypothesis-v2-ko1", "agent-hypothesis-v3-ko1"]
)
def test_artifact_preflight_accepts_legacy_and_current_evidence(tmp_path, prompt):
    fault_path, golden_path, _sha, golden_sha, environment = _artifact_pair(tmp_path)
    fault_sha = _write_json(fault_path, _fault_artifact(prompt_version=prompt))
    _run_preflight(fault_path, golden_path, fault_sha, golden_sha, environment)


def test_artifact_preflight_separates_old_artifact_from_current_container_revision(
    tmp_path: Path,
) -> None:
    previous_revision = "f" * 40
    fault_path, golden_path, _fault_sha, golden_sha, environ = _artifact_pair(tmp_path)
    fault_sha = _write_json(
        fault_path,
        _fault_artifact(code_revision=previous_revision),
    )

    _run_preflight(
        fault_path,
        golden_path,
        fault_sha,
        golden_sha,
        environ,
        expect_revision=previous_revision,
        expect_container_revision=REVISION,
    )

    with pytest.raises(
        artifact_preflight.ArtifactPreflightError,
        match="REVISION_MISMATCH",
    ):
        _run_preflight(
            fault_path,
            golden_path,
            fault_sha,
            golden_sha,
            environ,
            expect_revision=previous_revision,
        )


def test_fault_validator_accepts_the_writer_canonical_sorted_keys(
    tmp_path: Path,
) -> None:
    assert fault.FAULT_CLASS_SET == frozenset(fault.FAULT_CLASSES)
    artifact = tmp_path / "fault-5class.json"
    reset_evidence.write_atomic_receipt(artifact, _fault_artifact())
    persisted = json.loads(artifact.read_text(encoding="utf-8"))

    assert tuple(persisted["classification"]["by_class"]) != fault.FAULT_CLASSES
    fault.validate_artifact(persisted)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("not_regular", "ARTIFACT_NOT_REGULAR"),
        ("not_json", "ARTIFACT_NOT_JSON"),
        ("invalid", "ARTIFACT_INVALID"),
        ("sha", "ARTIFACT_SHA_MISMATCH"),
        ("env", "ENV_MISMATCH"),
        ("prompt", "PROMPT_VERSION_MISMATCH"),
        ("pair", "EVIDENCE_PAIR_MISMATCH"),
        ("epoch", "EPOCH_MISMATCH"),
        ("revision", "REVISION_MISMATCH"),
        ("attempt", "ATTEMPT_ID_MISMATCH"),
    ],
)
def test_artifact_preflight_mutations_have_exact_reason_codes(
    tmp_path: Path,
    case: str,
    reason: str,
) -> None:
    fault_path, golden_path, fault_sha, golden_sha, environ = _artifact_pair(tmp_path)
    attempt_id = ATTEMPT
    if case == "not_regular":
        fault_path.unlink()
    elif case == "not_json":
        fault_path.write_text("{", encoding="utf-8")
    elif case == "invalid":
        payload = _fault_artifact()
        payload["unexpected"] = True
        fault_sha = _write_json(fault_path, payload)
    elif case == "sha":
        fault_sha = "0" * 64
    elif case == "env":
        environ["AGENT_FAULT_EVAL_ARTIFACT_PATH"] = "/wrong/path"
    elif case == "prompt":
        payload = _fault_artifact()
        payload["prompt_version"] = "agent-hypothesis-v1"
        fault_sha = _write_json(fault_path, payload)
    elif case == "pair":
        payload = _fault_artifact()
        payload["golden_evidence_sha256"] = "7" * 64
        fault_sha = _write_json(fault_path, payload)
    elif case == "epoch":
        payload = _golden_artifact()
        payload["dataset_epoch"] = "different_epoch"
        golden_sha = _write_json(golden_path, payload)
    elif case == "revision":
        payload = _fault_artifact()
        payload["code_revision"] = "7" * 40
        fault_sha = _write_json(fault_path, payload)
    elif case == "attempt":
        attempt_id = "20260902T010204Z-0123456789ab"

    with pytest.raises(artifact_preflight.ArtifactPreflightError) as caught:
        _run_preflight(
            fault_path,
            golden_path,
            fault_sha,
            golden_sha,
            environ,
            attempt_id=attempt_id,
        )
    assert caught.value.reason == reason
