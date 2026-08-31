"""V5-C-6.2 Fault 5-class 격리 평가 단위·CLI 계약."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.evaluation import fault_5class as subject
from app.evaluation import predictions_repository as repository
from scripts import evaluate_fault_5class as cli
from scripts.fault_evaluation_artifact import (
    FaultArtifactWriteError,
    write_fault_evaluation_artifact,
)
from scripts.fault_evaluation_population import (
    EvaluationPopulation,
    PopulationEvidenceInvalid,
    PopulationMember,
    load_evaluation_population,
)
from scripts.fault_evaluation_provenance import (
    ProvenanceInvalid,
    StaticProvenance,
    load_static_provenance,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"


def _keys() -> tuple[subject.IncidentKey, ...]:
    return tuple(
        subject.IncidentKey(f"LOT{index:03d}", f"EQP{index:02d}-PM1")
        for index in range(12)
    )


def _faults() -> tuple[str | None, ...]:
    return ("FOC", "FOC", "RFM", "MFD", "TMD", "OTH", "OTH", *([None] * 5))


def _label_rows() -> tuple[subject.IncidentFaultLabelRow, ...]:
    rows: list[subject.IncidentFaultLabelRow] = []
    for key, fault in zip(_keys(), _faults(), strict=True):
        # incident 전체 member를 읽는 계약을 fixture 자체로 표현한다.
        rows.append(subject.IncidentFaultLabelRow(key, "NRM"))
        if fault is not None:
            rows.append(subject.IncidentFaultLabelRow(key, fault))
    return tuple(rows)


def _predictions(
    *,
    perfect_classification: bool = True,
) -> tuple[subject.PredictionRecord, ...]:
    records: list[subject.PredictionRecord] = []
    for index, (key, fault) in enumerate(zip(_keys(), _faults(), strict=True)):
        alarm = f"TRACE:TA-{index:02d}"
        predicted = fault if perfect_classification and fault is not None else "FOC"
        records.append(
            subject.PredictionRecord(
                incident=key,
                agent_run_id=f"RUN-{index:04d}",
                predicted_fault_code=predicted,
                supporting_alarm_tokens=(alarm,),
                supporting_chunk_ids=(f"CHUNK-{index:02d}",),
                supporting_relation_ids=(f"REL-{index:02d}",),
                available_alarm_tokens=(alarm,),
                available_chunk_ids=(f"CHUNK-{index:02d}",),
                available_relation_ids=(f"REL-{index:02d}",),
                actual_action="MONITORING",
                model_version="model-v1",
                prompt_version="agent-hypothesis-v1",
                policy_version="ACTION-POLICY-V1",
            )
        )
    return tuple(records)


def _expected_actions() -> dict[subject.IncidentKey, str]:
    return {key: "MONITORING" for key in _keys()}


def _result(
    records: tuple[subject.PredictionRecord, ...] | None = None,
) -> subject.FaultEvaluationResult:
    frozen = subject.freeze_predictions(records or _predictions())
    return subject.evaluate_fault_5class(frozen, _label_rows(), _expected_actions())


def _provenance(prediction_hash: str) -> subject.ArtifactProvenance:
    return subject.ArtifactProvenance(
        golden_evidence_sha256="a" * 64,
        baseline_snapshot_artifact_sha256="b" * 64,
        oracle_sha256="c" * 64,
        population_sha256="d" * 64,
        prediction_hash=prediction_hash,
        runtime_provenance_sha256="e" * 64,
        evaluation_provenance_sha256="f" * 64,
        shared_key_sha256="1" * 64,
        code_revision="2" * 40,
    )


def test_exact_7_5_0_support_and_perfect_metrics() -> None:
    frozen = subject.freeze_predictions(_predictions())
    result = subject.evaluate_fault_5class(frozen, _label_rows(), _expected_actions())

    assert result.hard_gate_passed
    assert result.structured_prediction.as_dict() == {
        "numerator": 12,
        "denominator": 12,
        "rate": 1.0,
    }
    assert result.evidence_valid_run.numerator == 12
    assert result.rule_action_agreement.numerator == 12
    assert result.classification.accuracy.numerator == 7
    assert result.classification.macro_f1_5class == 1.0
    assert {
        name: metric.support for name, metric in result.classification.by_class.items()
    } == dict(subject.EXPECTED_CLASS_SUPPORT)
    assert (
        sum(
            item.disposition is subject.LabelDisposition.NO_INJECTED_FAULT
            for item in result.labels
        )
        == 5
    )


def test_prediction_hash_is_order_independent_and_label_free() -> None:
    records = _predictions()
    forward = subject.freeze_predictions(records)
    reverse = subject.freeze_predictions(tuple(reversed(records)))

    assert forward == reverse
    assert len(forward.prediction_hash) == 64


def test_null_prediction_is_unclassified_fn_without_false_positive() -> None:
    records = list(_predictions())
    records[2] = replace(
        records[2],
        predicted_fault_code=None,
        supporting_alarm_tokens=(),
        supporting_chunk_ids=(),
        supporting_relation_ids=(),
    )
    result = _result(tuple(records))

    assert result.classification.unclassified_count == 1
    assert result.classification.accuracy.numerator == 6
    assert result.classification.by_class["RFM"].false_negative == 1
    assert (
        sum(item.false_positive for item in result.classification.by_class.values())
        == 0
    )
    assert "STRUCTURED_PREDICTION_NOT_100_PERCENT" in result.hard_gate_reasons
    assert "EVIDENCE_ID_NOT_100_PERCENT" in result.hard_gate_reasons


def test_citation_must_exist_in_the_same_run_and_empty_evidence_is_invalid() -> None:
    records = list(_predictions())
    records[0] = replace(
        records[0], available_alarm_tokens=(records[1].supporting_alarm_tokens[0],)
    )
    result = _result(tuple(records))
    assert result.evidence_valid_run.numerator == 11

    records = list(_predictions())
    records[0] = replace(
        records[0],
        supporting_alarm_tokens=(),
        supporting_chunk_ids=(),
        supporting_relation_ids=(),
    )
    assert _result(tuple(records)).evidence_valid_run.numerator == 11


def test_classification_score_is_report_only_and_macro_has_fixed_five_classes() -> None:
    result = _result(_predictions(perfect_classification=False))

    assert result.hard_gate_passed
    assert result.classification.accuracy.numerator == 2
    assert result.classification.by_class["RFM"].precision == 0.0
    assert result.classification.by_class["RFM"].f1 == 0.0
    expected = sum(item.f1 for item in result.classification.by_class.values()) / 5
    assert result.classification.macro_f1_5class == expected


def test_macro_f1_keeps_five_class_denominator_when_classes_are_unobserved() -> None:
    key = _keys()[0]
    record = _predictions()[0]
    metric = subject._classification_metrics(
        {key: record},
        (
            subject.IncidentLabel(
                incident=key,
                disposition=subject.LabelDisposition.EVAL_TARGET,
                fault_code="FOC",
            ),
        ),
    )

    assert metric.by_class["FOC"].f1 == 1.0
    assert metric.by_class["RFM"].support == 0
    assert metric.macro_f1_5class == 0.2
    assert metric.observed_class_macro_f1 == 1.0


def test_rule_action_disagreement_is_a_hard_gate_failure() -> None:
    records = list(_predictions())
    records[0] = replace(records[0], actual_action="WARNING")

    result = _result(tuple(records))

    assert result.rule_action_agreement.numerator == 11
    assert "RULE_ACTION_NOT_100_PERCENT" in result.hard_gate_reasons


def test_ambiguous_or_wrong_support_is_rejected() -> None:
    rows = list(_label_rows())
    rows.append(subject.IncidentFaultLabelRow(_keys()[0], "RFM"))

    with pytest.raises(
        subject.FaultEvaluationContractError, match="LABEL_DISTRIBUTION_NOT_EXACT"
    ):
        subject.classify_incident_labels(_keys(), rows)


@pytest.mark.parametrize("missing", sorted(subject._ARTIFACT_KEYS))
def test_artifact_rejects_every_missing_exact_key(missing: str) -> None:
    frozen = subject.freeze_predictions(_predictions())
    artifact = subject.artifact_to_dict(
        subject.evaluate_fault_5class(frozen, _label_rows(), _expected_actions()),
        _provenance(frozen.prediction_hash),
    )
    artifact.pop(missing)

    with pytest.raises(subject.FaultEvaluationContractError):
        subject.validate_artifact(artifact)


def test_artifact_carries_the_small_sample_interpretation_limit() -> None:
    frozen = subject.freeze_predictions(_predictions())
    artifact = subject.artifact_to_dict(_result(), _provenance(frozen.prediction_hash))

    disclaimer = artifact["production_performance_disclaimer"]
    assert "분류 모집단은 7건" in disclaimer
    assert "클래스별 support는 1~2건" in disclaimer
    assert "성능 추정치로 해석하지 않는다" in disclaimer

    artifact["production_performance_disclaimer"] = "표본 한계를 삭제한 문구"
    with pytest.raises(
        subject.FaultEvaluationContractError, match="ARTIFACT_METADATA_INVALID"
    ):
        subject.validate_artifact(artifact)


def test_artifact_rejects_metric_and_gate_inconsistency() -> None:
    frozen = subject.freeze_predictions(_predictions())
    artifact = subject.artifact_to_dict(_result(), _provenance(frozen.prediction_hash))
    artifact["classification"]["macro_f1_5class"] = 0.5
    with pytest.raises(
        subject.FaultEvaluationContractError, match="ARTIFACT_CLASSIFICATION_INVALID"
    ):
        subject.validate_artifact(artifact)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self.rows

    def one(self) -> Any:
        assert len(self.rows) == 1
        return self.rows[0]


class _Connection:
    def __init__(self, *, database: str, role: str, prediction_rows: list[Any]) -> None:
        self.database = database
        self.role = role
        self.prediction_rows = prediction_rows
        self.calls: list[str] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def exec_driver_sql(self, sql: str) -> None:
        self.calls.append(sql)

    def execute(self, statement: Any, params: Any = None) -> _Result:
        del params
        if statement is repository.IDENTITY_SQL:
            self.calls.append("identity")
            return _Result(
                [
                    SimpleNamespace(
                        database_name=self.database,
                        role_name=self.role,
                    )
                ]
            )
        if statement is repository.SHARED_KEYS_SQL:
            self.calls.append("shared")
            return _Result(
                [
                    {
                        "lot_hist_id": f"LH-{index:04d}",
                        "lot_id": f"LOT-{index:04d}",
                        "chamber_id": f"CH-{index:04d}",
                    }
                    for index in range(600)
                ]
            )
        if statement is repository.PREDICTIONS_SQL:
            self.calls.append("predictions")
            return _Result(self.prediction_rows)
        raise AssertionError(str(statement))


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection


def _repository_prediction_rows() -> list[Any]:
    rows: list[Any] = []
    for record in _predictions():
        source, alarm_id = record.supporting_alarm_tokens[0].split(":", maxsplit=1)
        rows.append(
            SimpleNamespace(
                agent_run_id=record.agent_run_id,
                lot_id=record.incident.lot_id,
                chamber_id=record.incident.chamber_id,
                action=record.actual_action,
                retry_of_run_id=None,
                run_evidence={
                    "action_provenance": {
                        "schema": "action-provenance-v1",
                        "action_policy_version": record.policy_version,
                    },
                    "rehydration_snapshot": {
                        "schema_version": "rehydration-snapshot-v1",
                        "route": {
                            "graph_evidence": [
                                {"relation_ids": list(record.available_relation_ids)}
                            ]
                        },
                    },
                },
                predicted_fault_code=record.predicted_fault_code,
                prediction_evidence={
                    "schema_version": "agent-evidence-v1",
                    "supporting_alarms": [{"source": source, "alarm_id": alarm_id}],
                    "supporting_chunk_ids": list(record.supporting_chunk_ids),
                    "supporting_relation_ids": list(record.supporting_relation_ids),
                },
                llm_model=record.model_version,
                prompt_version=record.prompt_version,
                alarms=[{"source": source, "alarm_id": alarm_id}],
                tools=[
                    {
                        "tool_name": "search_documents",
                        "status": "SUCCESS",
                        "output": {
                            "hits": [{"chunk_id": record.available_chunk_ids[0]}]
                        },
                    }
                ],
            )
        )
    return rows


def test_runtime_repository_left_join_preserves_missing_prediction() -> None:
    rows = _repository_prediction_rows()
    rows[0].predicted_fault_code = None
    rows[0].prediction_evidence = None
    rows[0].llm_model = None
    rows[0].prompt_version = None
    connection = _Connection(
        database="kosa_agent_e2e", role="kosa_app", prediction_rows=rows
    )

    snapshot = repository.read_runtime_evaluation_snapshot(
        _Engine(connection),
        database="kosa_agent_e2e",
        run_ids=tuple(row.agent_run_id for row in rows),
    )

    assert len(snapshot.records) == 12
    assert snapshot.records[0].predicted_fault_code is None
    assert connection.calls[:4] == [
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "identity",
        "shared",
        "predictions",
    ]


def test_label_callback_runs_only_after_identity_and_shared_hash_match() -> None:
    runtime_connection = _Connection(
        database="kosa_agent_e2e",
        role="kosa_app",
        prediction_rows=_repository_prediction_rows(),
    )
    runtime = repository.read_runtime_evaluation_snapshot(
        _Engine(runtime_connection),
        database="kosa_agent_e2e",
        run_ids=tuple(item.agent_run_id for item in _predictions()),
    )
    frozen = subject.freeze_predictions(runtime.records)
    evaluation_connection = _Connection(
        database="kosa_text2sql",
        role="kosa_evaluation",
        prediction_rows=[],
    )
    called: list[str] = []

    def labels(_connection: Any, keys: Any) -> list[Any]:
        called.append("labels")
        assert evaluation_connection.calls[-2:] == ["identity", "shared"]
        return [
            SimpleNamespace(
                lot_id=row.incident.lot_id,
                chamber_id=row.incident.chamber_id,
                fault_code=row.fault_code,
            )
            for row in _label_rows()
        ]

    rows = repository.read_evaluation_label_snapshot(
        _Engine(evaluation_connection),
        frozen=frozen,
        expected_shared_key_sha256=runtime.shared_key_sha256,
        label_loader=labels,
    )
    assert len(rows) == len(_label_rows())
    assert called == ["labels"]

    with pytest.raises(repository.PredictionTargetMismatch, match="TARGET_MISMATCH"):
        repository.read_evaluation_label_snapshot(
            _Engine(evaluation_connection),
            frozen=frozen,
            expected_shared_key_sha256="0" * 64,
            label_loader=labels,
        )
    assert called == ["labels"]


def _snapshot_for_oracle(
    *,
    retry_index: int | None = None,
    action_mismatch_index: int | None = None,
) -> dict[str, Any]:
    oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))["incidents"]
    runs: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for index, incident in enumerate(oracle):
        run_id = f"RUN-{index:04d}"
        action = incident["expected_action"]
        if action_mismatch_index == index:
            action = "WARNING" if action != "WARNING" else "MONITORING"
        runs.append(
            {
                "agent_run_id": run_id,
                "lot_id": incident["lot_id"],
                "chamber_id": incident["chamber_id"],
                "status": "COMPLETED",
                "autonomy_level": 2,
                "action": action,
                "retry_of_run_id": "RUN-ORIGINAL" if retry_index == index else None,
                "latency_ms": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "rehydration_snapshot_bytes": None,
            }
        )
        actions.append(
            {
                "agent_run_id": run_id,
                "action_id": f"ACT-{index:04d}",
                "link_role": "CREATED",
                "lot_id": incident["lot_id"],
                "chamber_id": incident["chamber_id"],
                "action_code": action,
            }
        )
    return {
        "runs": runs,
        "actions": actions,
        "approvals": [],
        "deliveries": [],
        "tools": [],
        "audits": [],
        "r03_incidents": [],
    }


def _write_evidence(
    root: Path,
    *,
    retry_index: int | None = None,
    action_mismatch_index: int | None = None,
) -> tuple[Path, Path]:
    artifact_dir = root / "artifacts"
    artifact_dir.mkdir()
    snapshot_path = artifact_dir / "baseline.json"
    raw = (
        json.dumps(
            _snapshot_for_oracle(
                retry_index=retry_index,
                action_mismatch_index=action_mismatch_index,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    snapshot_path.write_bytes(raw)
    manifest = {
        "format_version": 1,
        "dataset_epoch": "fdc_final_20260818",
        "gate_kind": "PUBLIC_GOLDEN_FLOW",
        "level_round": 2,
        "phases": {
            "BATCH_BASELINE": {
                "execution_scope": "PUBLIC_E2E",
                "artifact_ids": ["baseline-db"],
            }
        },
        "artifacts": [
            {
                "artifact_id": "baseline-db",
                "kind": "DB_SNAPSHOT",
                "relative_path": "artifacts/baseline.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "phase": "BATCH_BASELINE",
                "level_round": 2,
                "media_type": "application/json",
            }
        ],
    }
    manifest_path = root / "evidence.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, snapshot_path


def test_population_is_rederived_from_the_single_baseline_snapshot(
    tmp_path: Path,
) -> None:
    evidence, _snapshot = _write_evidence(tmp_path)
    population = load_evaluation_population(evidence)

    assert len(population.members) == 12
    assert population.run_ids == tuple(f"RUN-{index:04d}" for index in range(12))
    assert len(population.population_sha256) == 64
    assert len(population.golden_evidence_sha256) == 64


def test_population_does_not_preempt_the_rule_action_hard_gate(tmp_path: Path) -> None:
    evidence, _snapshot = _write_evidence(tmp_path, action_mismatch_index=0)

    population = load_evaluation_population(evidence)

    assert population.members[0].expected_action != "WARNING"


def test_population_rejects_retry_and_artifact_hash_tampering(tmp_path: Path) -> None:
    evidence, _snapshot = _write_evidence(tmp_path, retry_index=0)
    with pytest.raises(PopulationEvidenceInvalid, match="EVIDENCE_INVALID"):
        load_evaluation_population(evidence)

    other = tmp_path / "other"
    other.mkdir()
    evidence, snapshot = _write_evidence(other)
    snapshot.write_text("{}", encoding="utf-8")
    with pytest.raises(PopulationEvidenceInvalid, match="EVIDENCE_INVALID"):
        load_evaluation_population(evidence)


def test_static_provenance_committed_files_are_exact() -> None:
    value = load_static_provenance()
    assert len(value.runtime_sha256) == 64
    assert len(value.evaluation_sha256) == 64


def _population() -> EvaluationPopulation:
    members = tuple(
        PopulationMember(key, f"RUN-{index:04d}", f"ACT-{index:04d}", "MONITORING")
        for index, key in enumerate(_keys())
    )
    return EvaluationPopulation(
        members,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
    )


def test_cli_enforces_freeze_before_label_and_writes_report_only_scores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    original_freeze = subject.freeze_predictions

    def runtime_snapshot(
        *args: Any, **kwargs: Any
    ) -> repository.RuntimeEvaluationSnapshot:
        del args, kwargs
        events.append("runtime")
        return repository.RuntimeEvaluationSnapshot(
            _predictions(perfect_classification=False), "1" * 64
        )

    def freeze(records: Any) -> subject.FrozenPredictions:
        events.append("freeze")
        return original_freeze(records)

    def label_snapshot(*args: Any, **kwargs: Any) -> Any:
        del args
        assert isinstance(kwargs["frozen"], subject.FrozenPredictions)
        events.append("label")
        return _label_rows()

    written: list[dict[str, Any]] = []

    def writer(_path: Path, payload: Any) -> str:
        written.append(dict(payload))
        return "9" * 64

    monkeypatch.setattr(cli, "read_runtime_evaluation_snapshot", runtime_snapshot)
    monkeypatch.setattr(cli, "freeze_predictions", freeze)
    monkeypatch.setattr(cli, "read_evaluation_label_snapshot", label_snapshot)

    exit_code = cli.main(
        [
            "--agent-database",
            "kosa_agent_e2e",
            "--golden-evidence",
            str(tmp_path / "evidence.json"),
            "--output",
            str(tmp_path / "metric.json"),
        ],
        app_engine_factory=lambda: events.append("runtime_engine") or object(),
        evaluation_engine_factory=lambda: events.append("label_engine") or object(),
        population_loader=lambda _path: events.append("population") or _population(),
        provenance_loader=lambda: events.append("provenance")
        or StaticProvenance("e" * 64, "f" * 64),
        label_loader=lambda *_args: [],
        artifact_writer=writer,
        revision_loader=lambda: "2" * 40,
    )

    assert exit_code == 0
    assert events.index("freeze") < events.index("label")
    assert events[:4] == ["population", "provenance", "runtime_engine", "runtime"]
    assert written[0]["classification"]["accuracy"]["numerator"] == 2
    assert written[0]["hard_gate_passed"] is True


def test_cli_evidence_failure_happens_before_any_connector(tmp_path: Path) -> None:
    connectors: list[str] = []

    def invalid(_path: Path) -> Any:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")

    assert (
        cli.main(
            [
                "--agent-database",
                "kosa_agent_e2e",
                "--golden-evidence",
                str(tmp_path / "bad.json"),
                "--output",
                str(tmp_path / "metric.json"),
            ],
            app_engine_factory=lambda: connectors.append("runtime"),
            evaluation_engine_factory=lambda: connectors.append("evaluation"),
            population_loader=invalid,
        )
        == 3
    )
    assert connectors == []


def test_cli_provenance_failure_happens_before_any_connector(tmp_path: Path) -> None:
    connectors: list[str] = []

    def invalid() -> Any:
        raise ProvenanceInvalid("EVIDENCE_INVALID")

    assert (
        cli.main(
            [
                "--agent-database",
                "kosa_agent_e2e",
                "--golden-evidence",
                str(tmp_path / "evidence.json"),
                "--output",
                str(tmp_path / "metric.json"),
            ],
            app_engine_factory=lambda: connectors.append("runtime"),
            evaluation_engine_factory=lambda: connectors.append("evaluation"),
            population_loader=lambda _path: _population(),
            provenance_loader=invalid,
        )
        == 3
    )
    assert connectors == []


def test_cli_hard_gate_failure_still_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records = list(_predictions())
    records[0] = replace(
        records[0],
        predicted_fault_code=None,
        supporting_alarm_tokens=(),
        supporting_chunk_ids=(),
        supporting_relation_ids=(),
    )
    runtime = repository.RuntimeEvaluationSnapshot(tuple(records), "1" * 64)
    monkeypatch.setattr(
        cli,
        "read_runtime_evaluation_snapshot",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        cli,
        "read_evaluation_label_snapshot",
        lambda *_args, **_kwargs: _label_rows(),
    )
    written: list[dict[str, Any]] = []

    exit_code = cli.main(
        [
            "--agent-database",
            "kosa_agent_e2e",
            "--golden-evidence",
            str(tmp_path / "evidence.json"),
            "--output",
            str(tmp_path / "metric.json"),
        ],
        app_engine_factory=object,
        evaluation_engine_factory=object,
        population_loader=lambda _path: _population(),
        provenance_loader=lambda: StaticProvenance("e" * 64, "f" * 64),
        label_loader=lambda *_args: [],
        artifact_writer=lambda _path, payload: written.append(dict(payload))
        or "9" * 64,
        revision_loader=lambda: "2" * 40,
    )

    assert exit_code == 1
    assert written[0]["hard_gate_passed"] is False
    assert written[0]["structured_prediction"]["numerator"] == 11


def test_artifact_writer_is_no_clobber_and_concurrent_safe(tmp_path: Path) -> None:
    target = tmp_path / "metric.json"

    def attempt(value: int) -> str:
        return write_fault_evaluation_artifact(target, {"value": value})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(attempt, value) for value in (1, 2)]
    succeeded = [future.result() for future in results if future.exception() is None]
    failed = [
        future.exception() for future in results if future.exception() is not None
    ]

    assert len(succeeded) == 1
    assert len(failed) == 1
    assert isinstance(failed[0], FaultArtifactWriteError)
    assert json.loads(target.read_text(encoding="utf-8"))["value"] in {1, 2}
    assert not list(tmp_path.glob("*.tmp"))

    with pytest.raises(FaultArtifactWriteError, match="ARTIFACT_WRITE_FAILED"):
        write_fault_evaluation_artifact(target, {"value": 3})


def test_artifact_writer_cleans_temp_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "metric.json"

    def fail_publish(_source: Path, _target: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr("os.link", fail_publish)
    with pytest.raises(FaultArtifactWriteError, match="ARTIFACT_WRITE_FAILED"):
        write_fault_evaluation_artifact(target, {"value": 1})

    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))
