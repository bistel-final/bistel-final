"""MOCK-NOTIFY-V1 golden-flow: five live observations and two isolated locators.

No approval/decision semantics, historical snapshot reconstruction or live
execution. The stage owner captures each snapshot at its named boundary.
"""

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS
from app.agent.golden_flow import (
    load_expected_oracle,
    snapshot_from_mapping,
    validate_expected_oracle_source,
)
from app.agent.golden_summary import MOCK_PHASES, validate_golden_summary
from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    digest,
    parse_json,
    read_private,
    resolve_component,
)
from app.agent.release_mock import instant
from app.agent.release_prepared import Attempt, Revision
from app.agent.release_round import verify_round

LIVE = MOCK_PHASES[:5]


class GoldenSnapshotV2(EvidenceModel):
    schema_version: Literal["mock-notify-golden-snapshot-v1"]
    action_policy_version: Literal["MOCK-NOTIFY-V1"]
    database: Literal["kosa_agent_e2e"]
    phase: Literal[
        "PREFLIGHT", "BATCH_BASELINE", "MOCK_RESULTS", "NO_DECISIONS", "SECOND_BATCH"
    ]
    recorded_at: str
    snapshot: dict
    kafka_offsets: dict[str, int]
    prediction_hash: Sha256 | None


class IsolatedEvidence(EvidenceModel):
    test_ref: str = Field(pattern=r"^tests/(unit|integration)/[A-Za-z0-9_./:-]+$")
    revision: Revision


class GoldenEvidenceV2(EvidenceModel):
    schema_version: Literal["mock-notify-golden-evidence-v1"]
    protocol: Literal["MOCK-NOTIFY-V1"]
    attempt_id: Attempt
    R: Revision
    source_manifest_sha256: Sha256
    round1_sha256: Sha256
    preflight_pending: Component
    preflight_offsets: Component
    snapshots: dict[str, Component]
    second_batch: Component
    isolated: dict[str, IsolatedEvidence]


def _require(ok, code):
    if not ok:
        raise EvidenceError(code)


def _snapshot(value):
    envelope = GoldenSnapshotV2.model_validate(value)
    _require(
        (envelope.prediction_hash is not None) == (envelope.phase == "BATCH_BASELINE"),
        "GOLDEN_MOCK_PREDICTION_PIN_INVALID",
    )
    instant(envelope.recorded_at)
    _require(
        set(envelope.kafka_offsets) == {"fdc.actions:0", "fdc.actions.result:0"}
        and all(type(v) is int and v >= 0 for v in envelope.kafka_offsets.values()),
        "GOLDEN_MOCK_OFFSETS_INVALID",
    )
    return envelope, snapshot_from_mapping(envelope.snapshot)


def verify_mock_golden(
    *, root: Path, evidence: Component, expected_attempt_id, expected_revision
):
    """Full local source read/recount; summary PASS is never an input."""
    files = {}

    def read(ref):
        raw = read_private(root, ref.relative_path)
        _require(digest(raw) == ref.sha256, "GOLDEN_MOCK_COMPONENT_MISMATCH")
        files[ref.relative_path] = raw
        return parse_json(raw)

    manifest = GoldenEvidenceV2.model_validate(read(evidence))
    repository = Path(__file__).resolve().parents[3]
    source_sha = digest(
        (repository / "infra/bootstrap/source-manifest-v4.json").read_bytes()
    )
    oracle = load_expected_oracle(
        parse_json(
            (
                repository / "backend/tests/fixtures/v5_c_6_1/golden_incidents.json"
            ).read_bytes()
        )
    )
    validate_expected_oracle_source(oracle, source_sha)
    _require(
        manifest.source_manifest_sha256 == source_sha, "GOLDEN_MOCK_SOURCE_MISMATCH"
    )
    from scripts.verify_golden_flow import _validate_plan

    pending = read(manifest.preflight_pending)
    _validate_plan(pending)
    _require(
        len(pending["selected"]) == 12
        and {(p["lot_id"], p["chamber_id"]) for p in pending["selected"]}
        == CANONICAL_INCIDENT_KEYS
        and not pending["rejected"]
        and not pending["incomplete"]
        and pending["excluded"]
        == {"canonical_null_rows": 0, "canonical_null_by_source": {}},
        "GOLDEN_MOCK_PENDING_INVALID",
    )
    _require(
        manifest.attempt_id == expected_attempt_id
        and manifest.R == expected_revision
        and set(manifest.snapshots) == set(LIVE)
        and set(manifest.isolated) == {"UNKNOWN", "MANUAL_RETRY"},
        "GOLDEN_MOCK_BINDING_INVALID",
    )
    round1, assessment, _ = verify_round(
        root / "robustness",
        Component(relative_path="round1.json", sha256=manifest.round1_sha256),
    )
    _require(
        round1.schema_version == "level3-round1-v2"
        and round1.R == manifest.R
        and round1.reset_attempt_id == manifest.attempt_id,
        "GOLDEN_MOCK_BINDING_INVALID",
    )
    prepared = resolve_component(root / "robustness", round1.prepared_attempt)
    _require(
        read(manifest.preflight_offsets) == round1.kafka_before,
        "GOLDEN_MOCK_BEFORE_OFFSETS_MISMATCH",
    )
    _require(
        manifest.snapshots["PREFLIGHT"].sha256 == prepared["preflight_snapshot_sha256"],
        "GOLDEN_MOCK_PREFLIGHT_MISMATCH",
    )
    # The preflight capture is the original raw CM-4.7/manual snapshot. It must
    # remain byte-identical to the input pinned before SMTP approval.
    snapshots = {}
    envelopes = {}
    for phase in LIVE:
        value = read(manifest.snapshots[phase])
        if phase == "PREFLIGHT" and "schema_version" not in value:
            snapshots[phase] = snapshot_from_mapping(value)
            continue
        envelope, snap = _snapshot(value)
        _require(envelope.phase == phase, "GOLDEN_MOCK_PHASE_MISMATCH")
        envelopes[phase], snapshots[phase] = envelope, snap
    times = [instant(envelopes[p].recorded_at) for p in LIVE if p in envelopes]
    _require(times == sorted(times), "GOLDEN_MOCK_TIME_INVALID")
    before = snapshots["PREFLIGHT"]
    _require(
        not any(
            getattr(before, k)
            for k in ("runs", "actions", "approvals", "deliveries", "tools", "audits")
        ),
        "GOLDEN_MOCK_PREFLIGHT_NOT_EMPTY",
    )
    _require(
        set(before.r03_incidents)
        == {i.key for i in oracle.incidents if "R03" in i.alarm_sources},
        "GOLDEN_MOCK_INCIDENTS_INVALID",
    )
    baseline = snapshots["BATCH_BASELINE"]
    pins = {r.run_id: r for r in round1.runs}
    _require(
        len(baseline.runs) == len(baseline.actions) == 12
        and {r.agent_run_id for r in baseline.runs} == set(pins)
        and len({a.action_id for a in baseline.actions}) == 12
        and all(
            r.autonomy_level == 3 and r.retry_of_run_id is None for r in baseline.runs
        )
        and not baseline.approvals,
        "GOLDEN_MOCK_BASELINE_INVALID",
    )
    for action in baseline.actions:
        r = pins.get(action.agent_run_id)
        _require(
            r is not None
            and r.action_id == action.action_id
            and action.link_role == "CREATED"
            and (action.lot_id, action.chamber_id)
            == (r.route["incident"]["lot_id"], r.route["incident"]["chamber_id"])
            and action.action_code == r.action_code,
            "GOLDEN_MOCK_BASELINE_INVALID",
        )
    mock = snapshots["MOCK_RESULTS"]
    _require(
        {r.agent_run_id for r in mock.runs} == set(pins)
        and len(mock.runs) == 12
        and all(r.status == "COMPLETED" for r in mock.runs)
        and mock.actions == baseline.actions
        and not mock.approvals
        and Counter((d.channel, d.status) for d in mock.deliveries)
        == {("EMAIL", "SENT"): 7, ("MES_MOCK", "SENT"): 3},
        "GOLDEN_MOCK_RESULTS_INVALID",
    )
    _require(
        envelopes["MOCK_RESULTS"].kafka_offsets == round1.kafka_after,
        "GOLDEN_MOCK_OFFSETS_INVALID",
    )
    no_decisions = snapshots["NO_DECISIONS"]
    _require(
        not no_decisions.approvals
        and not any(
            a.event_type
            in {"APPROVE", "REJECT", "APPROVAL_APPROVED", "APPROVAL_REJECTED"}
            for a in no_decisions.audits
        )
        and no_decisions.runs == mock.runs
        and no_decisions.actions == mock.actions
        and no_decisions.deliveries == mock.deliveries
        and envelopes["NO_DECISIONS"].kafka_offsets == round1.kafka_after,
        "GOLDEN_MOCK_DECISION_EFFECT_DETECTED",
    )
    second = snapshots["SECOND_BATCH"]
    _require(
        second == no_decisions
        and envelopes["SECOND_BATCH"].kafka_offsets == round1.kafka_after,
        "GOLDEN_MOCK_SECOND_BATCH_CHANGED",
    )
    # Raw CLI result is independently bound; an unchanged DB alone cannot prove
    # that the second pending-batch invocation actually took place.
    raw = read_private(root, manifest.second_batch.relative_path)
    _require(
        digest(raw) == manifest.second_batch.sha256, "GOLDEN_MOCK_COMPONENT_MISMATCH"
    )
    files[manifest.second_batch.relative_path] = raw
    lines = [parse_json(line) for line in raw.splitlines()]
    _require(
        len(lines) == 1
        and lines[0].get("type") == "final"
        and lines[0].get("attempted") == 0
        and lines[0].get("new_runs_observed") == 0
        and all(
            type(lines[0].get(k)) is int and lines[0][k] == 0
            for k in (
                "attempted",
                "new_runs_observed",
                "succeeded",
                "failed",
                "skipped",
                "new_actions_observed",
                "new_deliveries_observed",
            )
        ),
        "GOLDEN_MOCK_SECOND_BATCH_NOT_EMPTY",
    )
    for isolated in manifest.isolated.values():
        _require(
            isolated.revision == manifest.R
            and ".." not in isolated.test_ref.split("/"),
            "GOLDEN_MOCK_ISOLATED_BINDING_INVALID",
        )
    phases = [dict(phase=p, status="PASS", reasons=[], metrics={}) for p in LIVE]
    phases[1]["metrics"] = dict(
        run_count=12, action_distribution=dict(assessment.action_counts)
    )
    phases[2]["metrics"] = dict(
        completed=12, email_sent=7, mes_requests=3, mes_results=3, mes_writebacks=3
    )
    phases.extend(
        dict(
            phase=p,
            status="NOT_LIVE",
            reasons=[],
            metrics={},
            evidence=manifest.isolated[p].model_dump(),
        )
        for p in MOCK_PHASES[5:]
    )
    result = dict(
        format_version=1,
        artifact_type="golden_flow_summary",
        protocol="MOCK-NOTIFY-V1",
        dataset_epoch=round1.dataset_epoch,
        source_manifest_sha256=manifest.source_manifest_sha256,
        evidence_manifest_sha256=evidence.sha256,
        status="PASS",
        phases=phases,
    )
    validate_golden_summary(result)
    _require(
        all(read_private(root, name) == raw for name, raw in files.items()),
        "GOLDEN_MOCK_EVIDENCE_DRIFT",
    )
    # Round dependency is recounted again, including its four independent sources.
    _require(
        verify_round(
            root / "robustness",
            Component(relative_path="round1.json", sha256=manifest.round1_sha256),
        )[0]
        == round1,
        "GOLDEN_MOCK_EVIDENCE_DRIFT",
    )
    return result, manifest, baseline
