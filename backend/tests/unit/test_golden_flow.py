"""V5-C-6.1 pure Gate·evidence boundary 회귀."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import golden_flow as subject  # noqa: E402
from scripts import verify_golden_flow as cli  # noqa: E402

ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"


def _oracle() -> subject.ExpectedOracle:
    return subject.load_expected_oracle(json.loads(ORACLE_PATH.read_text()))


def _empty_snapshot(*, r03: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "runs": [],
        "actions": [],
        "approvals": [],
        "deliveries": [],
        "tools": [],
        "audits": [],
        "r03_incidents": r03 or [],
    }


def _artifact(
    kind: subject.ArtifactKind,
    phase: subject.GoldenPhase,
    payload: Any,
    *,
    artifact_id: str,
    level_round: int = 2,
) -> subject.EvidenceArtifact:
    return subject.EvidenceArtifact(artifact_id, kind, phase, level_round, payload)


def _bundle(
    phase: subject.GoldenPhase,
    *artifacts: subject.EvidenceArtifact,
) -> subject.EvidenceBundle:
    return subject.EvidenceBundle(
        dataset_epoch=subject.DATASET_EPOCH,
        gate_kind=subject.GateKind.PUBLIC_GOLDEN_FLOW,
        level_rounds=(2,),
        scopes={phase: subject.ExecutionScope.PUBLIC_E2E},
        artifacts=tuple(artifacts),
    )


def _plan(oracle: subject.ExpectedOracle) -> dict[str, Any]:
    return {
        "type": "plan",
        "database": "kosa_agent_e2e",
        "selected": [
            {
                "lot_id": item.lot_id,
                "chamber_id": item.chamber_id,
                "member_count": len(item.alarm_sources),
                "representative": {
                    "source": item.alarm_sources[0],
                    "alarm_id": f"ALARM-{index}",
                },
            }
            for index, item in enumerate(oracle.incidents)
        ],
        "rejected": [],
        "incomplete": [],
        "excluded": {
            "canonical_null_rows": 0,
            "canonical_null_by_source": {},
        },
    }


def test_source_derived_oracle_is_exact_12_5_4_3_and_r03_three() -> None:
    oracle = _oracle()

    assert len(oracle.incidents) == 12
    assert {
        action: sum(item.expected_action == action for item in oracle.incidents)
        for action in ("MONITORING", "WARNING", "EQP_HOLD")
    } == {"MONITORING": 5, "WARNING": 4, "EQP_HOLD": 3}
    assert sum("R03" in item.alarm_sources for item in oracle.incidents) == 3
    assert all(
        "action_id" not in item.__dataclass_fields__ for item in oracle.incidents
    )


def test_oracle_rejects_distribution_relaxation() -> None:
    payload = json.loads(ORACLE_PATH.read_text())
    payload["incidents"][0]["expected_action"] = "WARNING"
    payload["incidents"][0]["alarm_sources"] = ["SUMMARY", "TRACE"]

    with pytest.raises(subject.GoldenFlowContractError, match="ORACLE_INVALID"):
        subject.load_expected_oracle(payload)


def test_preflight_requires_exact_selection_and_both_canonical_null_fields() -> None:
    oracle = _oracle()
    r03 = [
        {"lot_id": item.lot_id, "chamber_id": item.chamber_id}
        for item in oracle.incidents
        if "R03" in item.alarm_sources
    ]
    snapshot_payload = _empty_snapshot(r03=r03)
    snapshot = subject.snapshot_from_mapping(snapshot_payload)
    phase = subject.GoldenPhase.PREFLIGHT
    evidence = _bundle(
        phase,
        _artifact(
            subject.ArtifactKind.DB_SNAPSHOT,
            phase,
            snapshot_payload,
            artifact_id="db",
        ),
        _artifact(
            subject.ArtifactKind.BATCH_NDJSON,
            phase,
            [_plan(oracle)],
            artifact_id="batch",
        ),
    )

    result = subject.evaluate_golden_flow(snapshot, evidence, oracle, phase=phase)
    assert result.status is subject.PhaseStatus.PASS

    bad_plan = _plan(oracle)
    bad_plan["excluded"]["canonical_null_by_source"] = {"TRACE": 1}
    bad = _bundle(
        phase,
        evidence.artifacts[0],
        _artifact(
            subject.ArtifactKind.BATCH_NDJSON,
            phase,
            [bad_plan],
            artifact_id="bad-batch",
        ),
    )
    failed = subject.evaluate_golden_flow(snapshot, bad, oracle, phase=phase)
    assert failed.status is subject.PhaseStatus.FAIL
    assert "ROUND_2_PREFLIGHT_CANONICAL_NULL" in failed.phases[0].reasons

    live_drift = _empty_snapshot(r03=r03)
    live_drift["tools"] = [
        {"agent_run_id": "RUN-X", "tool_name": "send_action", "status": "SUCCESS"}
    ]
    drifted = subject.evaluate_golden_flow(
        subject.snapshot_from_mapping(live_drift),
        evidence,
        oracle,
        phase=phase,
    )
    assert drifted.status is subject.PhaseStatus.FAIL
    assert "LIVE_SNAPSHOT_MISMATCH" in drifted.phases[0].reasons


def _baseline_snapshot(oracle: subject.ExpectedOracle) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    for index, incident in enumerate(oracle.incidents):
        action_id = f"ACT-{index:04d}"
        actions.append(
            {
                "agent_run_id": f"RUN-{index:04d}",
                "action_id": action_id,
                "link_role": "CREATED",
                "lot_id": incident.lot_id,
                "chamber_id": incident.chamber_id,
                "action_code": incident.expected_action,
            }
        )
        channels = {
            "MONITORING": (),
            "WARNING": ("EMAIL",),
            "EQP_HOLD": ("EMAIL", "MES_MOCK"),
        }[incident.expected_action]
        for channel in channels:
            deliveries.append(
                {
                    "action_id": action_id,
                    "channel": channel,
                    "status": "BLOCKED" if channel == "MES_MOCK" else "WAITING",
                    "attempt_count": 0,
                }
            )
    payload = _empty_snapshot()
    payload["actions"] = actions
    payload["deliveries"] = deliveries
    return payload


def test_baseline_binds_outcomes_final_actions_and_delivery_rule() -> None:
    oracle = _oracle()
    payload = _baseline_snapshot(oracle)
    snapshot = subject.snapshot_from_mapping(payload)
    phase = subject.GoldenPhase.BATCH_BASELINE
    incident_lines = [
        {
            "type": "incident",
            "lot_id": item.lot_id,
            "chamber_id": item.chamber_id,
            "outcome": (
                "STARTED_WAITING_APPROVAL"
                if item.expected_action == "EQP_HOLD"
                else "STARTED_COMPLETED"
            ),
        }
        for item in oracle.incidents
    ]
    final = {
        "type": "final",
        "attempted": 12,
        "succeeded": 12,
        "failed": 0,
        "skipped": 0,
        "new_runs_observed": 12,
        "new_actions_observed": 12,
        "new_deliveries_observed": 10,
    }
    evidence = _bundle(
        phase,
        _artifact(subject.ArtifactKind.DB_SNAPSHOT, phase, payload, artifact_id="db"),
        _artifact(
            subject.ArtifactKind.BATCH_NDJSON,
            phase,
            [*incident_lines, final],
            artifact_id="batch",
        ),
        _artifact(
            subject.ArtifactKind.HTTP_RESULTS,
            phase,
            {
                "format_version": 1,
                "results": [{"case": "BATCH_WALL_CLOCK", "duration_ms": 25}],
            },
            artifact_id="timing",
        ),
    )

    assert (
        subject.evaluate_golden_flow(snapshot, evidence, oracle, phase=phase).status
        is subject.PhaseStatus.PASS
    )
    incident_lines[0]["outcome"] = "SKIPPED_RACE"
    assert (
        subject.evaluate_golden_flow(snapshot, evidence, oracle, phase=phase).status
        is subject.PhaseStatus.FAIL
    )


def test_preapproval_requires_three_blocked_mes_rows_not_row_absence() -> None:
    oracle = _oracle()
    payload = _baseline_snapshot(oracle)
    hold_actions = {
        action["action_id"]
        for action in payload["actions"]
        if action["action_code"] == "EQP_HOLD"
    }
    for delivery in payload["deliveries"]:
        if delivery["action_id"] in hold_actions and delivery["channel"] == "EMAIL":
            delivery["status"] = "SENT"
            delivery["attempt_count"] = 1
    payload["approvals"] = [
        {
            "approval_id": f"APR-{index}",
            "action_id": action_id,
            "agent_run_id": next(
                item["agent_run_id"]
                for item in payload["actions"]
                if item["action_id"] == action_id
            ),
            "status": "PENDING",
        }
        for index, action_id in enumerate(sorted(hold_actions))
    ]
    phase = subject.GoldenPhase.PRE_APPROVAL
    external = (
        _artifact(
            subject.ArtifactKind.N8N_EXECUTIONS,
            phase,
            {
                "format_version": 1,
                "executions": [
                    {
                        "workflow": "WF2",
                        "action_id": action_id,
                        "status": "SUCCESS",
                        "execution_id": f"EX-{index}",
                    }
                    for index, action_id in enumerate(sorted(hold_actions))
                ],
            },
            artifact_id="n8n",
        ),
        _artifact(
            subject.ArtifactKind.KAFKA_OFFSETS,
            phase,
            {"format_version": 1, "topic": "fdc.actions", "before": 10, "after": 10},
            artifact_id="kafka",
        ),
        _artifact(
            subject.ArtifactKind.SMTP_RECEIPT,
            phase,
            {
                "format_version": 1,
                "receipts": [
                    {
                        "action_id": action_id,
                        "status": "SENT",
                        "receipt_id": f"SMTP-{index}",
                    }
                    for index, action_id in enumerate(sorted(hold_actions))
                ],
            },
            artifact_id="smtp",
        ),
    )
    snapshot = subject.snapshot_from_mapping(payload)
    evidence = _bundle(
        phase,
        _artifact(subject.ArtifactKind.DB_SNAPSHOT, phase, payload, artifact_id="db"),
        *external,
    )
    assert (
        subject.evaluate_golden_flow(snapshot, evidence, oracle, phase=phase).status
        is subject.PhaseStatus.PASS
    )

    without_mes = json.loads(json.dumps(payload))
    without_mes["deliveries"] = [
        item
        for item in without_mes["deliveries"]
        if not (item["action_id"] in hold_actions and item["channel"] == "MES_MOCK")
    ]
    missing = subject.snapshot_from_mapping(without_mes)
    missing_evidence = _bundle(
        phase,
        _artifact(
            subject.ArtifactKind.DB_SNAPSHOT,
            phase,
            without_mes,
            artifact_id="db-missing",
        ),
        *external,
    )
    result = subject.evaluate_golden_flow(
        missing, missing_evidence, oracle, phase=phase
    )
    assert result.status is subject.PhaseStatus.FAIL
    assert "ROUND_2_PRE_APPROVAL_MES_NOT_BLOCKED" in result.phases[0].reasons


def test_unknown_retry_is_blocked_and_failed_retry_is_separate() -> None:
    oracle = _oracle()
    payload = _empty_snapshot()
    payload["deliveries"] = [
        {
            "action_id": "ACT-U",
            "channel": "EMAIL",
            "status": "UNKNOWN",
            "attempt_count": 1,
        }
    ]
    phase = subject.GoldenPhase.UNKNOWN
    http_payload = {
        "format_version": 1,
        "results": [
            {
                "case": "UNKNOWN_RETRY",
                "action_id": "ACT-U",
                "exit_code": 3,
                "before_status": "UNKNOWN",
                "after_status": "UNKNOWN",
            },
            {"case": "FAILED_RETRY", "action_id": "ACT-F", "exit_code": 0},
        ],
    }
    evidence = _bundle(
        phase,
        _artifact(subject.ArtifactKind.DB_SNAPSHOT, phase, payload, artifact_id="db"),
        _artifact(
            subject.ArtifactKind.HTTP_RESULTS,
            phase,
            http_payload,
            artifact_id="http",
        ),
        _artifact(
            subject.ArtifactKind.KAFKA_OFFSETS,
            phase,
            {"format_version": 1, "topic": "fdc.actions", "before": 3, "after": 3},
            artifact_id="kafka",
        ),
    )
    snapshot = subject.snapshot_from_mapping(payload)
    assert (
        subject.evaluate_golden_flow(snapshot, evidence, oracle, phase=phase).status
        is subject.PhaseStatus.PASS
    )
    http_payload["results"][0]["exit_code"] = 0
    assert (
        subject.evaluate_golden_flow(snapshot, evidence, oracle, phase=phase).status
        is subject.PhaseStatus.FAIL
    )


def test_second_batch_needs_dry_run_once_and_monotonic_timing() -> None:
    oracle = _oracle()
    payload = _empty_snapshot()
    phase = subject.GoldenPhase.SECOND_BATCH
    plan = {
        "type": "plan",
        "database": "kosa_agent_e2e",
        "selected": [],
        "rejected": [],
        "incomplete": [],
        "excluded": {
            "canonical_null_rows": 0,
            "canonical_null_by_source": {},
        },
    }
    final = {
        "type": "final",
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "new_runs_observed": 0,
        "new_actions_observed": 0,
        "new_deliveries_observed": 0,
    }
    base_artifacts = (
        _artifact(subject.ArtifactKind.DB_SNAPSHOT, phase, payload, artifact_id="db"),
        _artifact(subject.ArtifactKind.BATCH_NDJSON, phase, [plan], artifact_id="plan"),
        _artifact(
            subject.ArtifactKind.BATCH_NDJSON, phase, [final], artifact_id="once"
        ),
    )
    snapshot = subject.snapshot_from_mapping(payload)
    missing_timing = _bundle(phase, *base_artifacts)
    assert (
        subject.evaluate_golden_flow(
            snapshot, missing_timing, oracle, phase=phase
        ).status
        is subject.PhaseStatus.FAIL
    )
    complete = _bundle(
        phase,
        *base_artifacts,
        _artifact(
            subject.ArtifactKind.HTTP_RESULTS,
            phase,
            {
                "format_version": 1,
                "results": [{"case": "BATCH_WALL_CLOCK", "duration_ms": 7}],
            },
            artifact_id="timing",
        ),
    )
    assert (
        subject.evaluate_golden_flow(snapshot, complete, oracle, phase=phase).status
        is subject.PhaseStatus.PASS
    )


def test_level_metrics_keep_null_and_failed_counts_separate() -> None:
    payload = _empty_snapshot()
    payload["runs"] = [
        {
            "agent_run_id": "RUN-1",
            "lot_id": "LOT1",
            "chamber_id": "CH1",
            "status": "COMPLETED",
            "autonomy_level": 2,
            "action": "WARNING",
            "retry_of_run_id": None,
            "latency_ms": 10,
            "input_tokens": 4,
            "output_tokens": 2,
            "rehydration_snapshot_bytes": 20,
        },
        {
            "agent_run_id": "RUN-2",
            "lot_id": "LOT2",
            "chamber_id": "CH2",
            "status": "FAILED",
            "autonomy_level": 2,
            "action": None,
            "retry_of_run_id": None,
            "latency_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "rehydration_snapshot_bytes": None,
        },
    ]
    metrics = subject.level_metrics(
        subject.snapshot_from_mapping(payload), batch_wall_clock_ms=25
    )

    assert metrics["tokens"] == {
        "count": 1,
        "sum": 6,
        "mean": 6,
        "null_run_count": 1,
        "failed_run_count": 1,
    }
    assert metrics["completion_rate"] == {
        "numerator": 2,
        "numerator_by_status": {"COMPLETED": 1, "FAILED": 1},
        "denominator": 12,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")


def _write_ndjson(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(value, separators=(",", ":")) for value in values) + "\n",
        encoding="utf-8",
    )


def _manifest_for(tmp_path: Path, *, bad_scope: bool = False) -> Path:
    snapshot = tmp_path / "snapshot.json"
    _write_json(snapshot, _empty_snapshot())
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    manifest = {
        "format_version": 1,
        "dataset_epoch": subject.DATASET_EPOCH,
        "gate_kind": "PUBLIC_GOLDEN_FLOW",
        "level_round": 2,
        "phases": {
            "PREFLIGHT": {
                "execution_scope": (
                    "ISOLATED_CONTAINER" if bad_scope else "PUBLIC_E2E"
                ),
                "artifact_ids": ["db"],
            }
        },
        "artifacts": [
            {
                "artifact_id": "db",
                "kind": "DB_SNAPSHOT",
                "relative_path": "snapshot.json",
                "sha256": digest,
                "phase": "PREFLIGHT",
                "level_round": 2,
                "media_type": "application/json",
            }
        ],
    }
    path = tmp_path / "evidence.json"
    _write_json(path, manifest)
    return path


def _level_comparison_manifest(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    oracle = _oracle()
    snapshot_payload = _empty_snapshot(
        r03=[
            {"lot_id": item.lot_id, "chamber_id": item.chamber_id}
            for item in oracle.incidents
            if "R03" in item.alarm_sources
        ]
    )
    artifact_ids: list[str] = []
    artifacts: list[dict[str, Any]] = []
    for level_round in (1, 2):
        for kind, suffix, payload in (
            ("DB_SNAPSHOT", "snapshot.json", snapshot_payload),
            ("BATCH_NDJSON", "preflight.ndjson", [_plan(oracle)]),
        ):
            artifact_id = f"round-{level_round}-{kind.lower()}"
            relative_path = f"round-{level_round}-{suffix}"
            artifact_path = tmp_path / relative_path
            if kind == "BATCH_NDJSON":
                _write_ndjson(artifact_path, payload)
                media_type = "application/x-ndjson"
            else:
                _write_json(artifact_path, payload)
                media_type = "application/json"
            artifact_ids.append(artifact_id)
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "relative_path": relative_path,
                    "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                    "phase": "PREFLIGHT",
                    "level_round": level_round,
                    "media_type": media_type,
                }
            )
    manifest = {
        "format_version": 1,
        "dataset_epoch": subject.DATASET_EPOCH,
        "gate_kind": "LEVEL_COMPARISON",
        "level_round": [1, 2],
        "phases": {
            "PREFLIGHT": {
                "execution_scope": "ISOLATED_CONTAINER",
                "artifact_ids": artifact_ids,
            }
        },
        "artifacts": artifacts,
    }
    path = tmp_path / "level-evidence.json"
    _write_json(path, manifest)
    return path, manifest


def test_level_comparison_requires_isolated_scope_and_both_rounds(
    tmp_path: Path,
) -> None:
    path, manifest = _level_comparison_manifest(tmp_path)
    bundle = cli.load_evidence_bundle(path)
    live = subject.snapshot_from_mapping(bundle.artifacts[-2].payload)

    result = subject.evaluate_golden_flow(
        live,
        bundle,
        _oracle(),
        phase=subject.GoldenPhase.PREFLIGHT,
    )
    assert result.status is subject.PhaseStatus.PASS
    assert set(result.phases[0].metrics) == {"1", "2"}

    public_scope = json.loads(json.dumps(manifest))
    public_scope["phases"]["PREFLIGHT"]["execution_scope"] = "PUBLIC_E2E"
    _write_json(path, public_scope)
    with pytest.raises(cli.EvidenceInvalid):
        cli.load_evidence_bundle(path)

    missing_round = json.loads(json.dumps(manifest))
    missing_round["phases"]["PREFLIGHT"]["artifact_ids"] = [
        artifact_id
        for artifact_id in missing_round["phases"]["PREFLIGHT"]["artifact_ids"]
        if not artifact_id.startswith("round-1-")
    ]
    missing_round["artifacts"] = [
        artifact
        for artifact in missing_round["artifacts"]
        if artifact["level_round"] != 1
    ]
    _write_json(path, missing_round)
    incomplete_bundle = cli.load_evidence_bundle(path)
    incomplete = subject.evaluate_golden_flow(
        live,
        incomplete_bundle,
        _oracle(),
        phase=subject.GoldenPhase.PREFLIGHT,
    )
    assert incomplete.status is subject.PhaseStatus.EVIDENCE_INCOMPLETE
    assert incomplete.phases[0].reasons == ("ROUND_1_PHASE_EVIDENCE_MISSING",)


def test_evidence_loader_checks_scope_path_hash_and_parser_before_db(
    tmp_path: Path,
) -> None:
    path = _manifest_for(tmp_path)
    bundle = cli.load_evidence_bundle(path)
    assert bundle.gate_kind is subject.GateKind.PUBLIC_GOLDEN_FLOW

    payload = json.loads(path.read_text())
    payload["artifacts"][0]["sha256"] = "0" * 64
    _write_json(path, payload)
    with pytest.raises(cli.EvidenceInvalid):
        cli.load_evidence_bundle(path)

    path = _manifest_for(tmp_path, bad_scope=True)
    with pytest.raises(cli.EvidenceInvalid):
        cli.load_evidence_bundle(path)


def test_evidence_loader_rejects_escape_and_symlink_but_allows_repeated_kind(
    tmp_path: Path,
) -> None:
    path = _manifest_for(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["artifacts"][0]["relative_path"] = "../snapshot.json"
    _write_json(path, manifest)
    with pytest.raises(cli.EvidenceInvalid):
        cli.load_evidence_bundle(path)

    path = _manifest_for(tmp_path)
    linked = tmp_path / "linked.json"
    linked.symlink_to(tmp_path / "snapshot.json")
    manifest = json.loads(path.read_text())
    manifest["artifacts"][0]["relative_path"] = "linked.json"
    _write_json(path, manifest)
    with pytest.raises(cli.EvidenceInvalid):
        cli.load_evidence_bundle(path)
    linked.unlink()

    path = _manifest_for(tmp_path)
    second = tmp_path / "snapshot-2.json"
    _write_json(second, _empty_snapshot())
    manifest = json.loads(path.read_text())
    manifest["phases"]["PREFLIGHT"]["artifact_ids"].append("db-2")
    manifest["artifacts"].append(
        {
            **manifest["artifacts"][0],
            "artifact_id": "db-2",
            "relative_path": "snapshot-2.json",
            "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
        }
    )
    _write_json(path, manifest)
    loaded = cli.load_evidence_bundle(path)
    assert [item.kind for item in loaded.artifacts] == [
        subject.ArtifactKind.DB_SNAPSHOT,
        subject.ArtifactKind.DB_SNAPSHOT,
    ]


def test_invalid_evidence_never_constructs_engine(tmp_path: Path) -> None:
    evidence = tmp_path / "invalid.json"
    _write_json(evidence, {})
    engine_calls: list[str] = []

    result = cli.main(
        [
            "--database",
            "kosa_agent_e2e",
            "--evidence-file",
            str(evidence),
        ],
        engine_factory=lambda: engine_calls.append("called"),
    )

    assert result == cli.EXIT_EVIDENCE
    assert engine_calls == []


def test_phase_mode_never_claims_full_golden_flow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = _manifest_for(tmp_path)
    snapshot = subject.snapshot_from_mapping(_empty_snapshot())
    monkeypatch.setattr(cli, "read_golden_flow_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(
        cli,
        "evaluate_golden_flow",
        lambda *_a, **_k: subject.GoldenFlowResult(
            (
                subject.PhaseResult(
                    subject.GoldenPhase.PREFLIGHT,
                    subject.PhaseStatus.PASS,
                    (),
                    {},
                ),
            )
        ),
    )

    assert (
        cli.main(
            [
                "--database",
                "kosa_agent_e2e",
                "--evidence-file",
                str(path),
                "--phase",
                "PREFLIGHT",
            ],
            engine_factory=lambda: object(),
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"status":"PHASE_PASS"' in output
    assert "GOLDEN_FLOW_PASS" not in output


class _Connection:
    def __init__(self, *, database: str = "kosa_agent_e2e") -> None:
        self.database = database
        self.calls: list[str] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def exec_driver_sql(self, statement: str) -> None:
        self.calls.append(statement)

    def execute(self, statement: Any) -> Any:
        rendered = str(statement)
        self.calls.append(rendered)
        if "current_database" in rendered:
            row = SimpleNamespace(database_name=self.database, role_name="kosa_app")
        else:
            row = SimpleNamespace(snapshot=_empty_snapshot())
        return SimpleNamespace(one=lambda: row)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection


def test_repository_orders_readonly_begin_identity_then_business() -> None:
    from app.agent.golden_flow_repository import read_golden_flow_snapshot

    connection = _Connection()
    read_golden_flow_snapshot(_Engine(connection), database="kosa_agent_e2e")

    assert connection.calls[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert "current_database" in connection.calls[1]
    assert "jsonb_build_object" in connection.calls[2]


def test_target_mismatch_stops_before_business_query() -> None:
    from app.agent.golden_flow_repository import (
        GoldenFlowTargetMismatch,
        read_golden_flow_snapshot,
    )

    connection = _Connection(database="kosa_agent")
    with pytest.raises(GoldenFlowTargetMismatch):
        read_golden_flow_snapshot(_Engine(connection), database="kosa_agent_e2e")

    assert len(connection.calls) == 2
