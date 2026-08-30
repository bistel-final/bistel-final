"""Golden-flow E2E evidence를 판정하는 순수 도메인 core (``V5-C-6.1``).

이 모듈은 DB·파일·네트워크에 접근하지 않는다. verifier 경계가 검증한 oracle,
phase artifact와 repeatable-read snapshot을 받아 7개 phase의 판정만 수행한다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from statistics import mean
from typing import Any, Final

from app.common.enums import ActionCode, resolve_delivery_channels

DATASET_EPOCH: Final = "fdc_final_20260818"
EXPECTED_INCIDENT_COUNT: Final = 12
EXPECTED_ACTION_COUNTS: Final[Mapping[str, int]] = {
    ActionCode.MONITORING.value: 5,
    ActionCode.WARNING.value: 4,
    ActionCode.EQP_HOLD.value: 3,
}


class GoldenFlowContractError(ValueError):
    """증빙 또는 oracle 구조가 계약과 다르다."""


class GateKind(StrEnum):
    PUBLIC_GOLDEN_FLOW = "PUBLIC_GOLDEN_FLOW"
    LEVEL_COMPARISON = "LEVEL_COMPARISON"


class ExecutionScope(StrEnum):
    PUBLIC_E2E = "PUBLIC_E2E"
    ISOLATED_CONTAINER = "ISOLATED_CONTAINER"


class GoldenPhase(StrEnum):
    PREFLIGHT = "PREFLIGHT"
    BATCH_BASELINE = "BATCH_BASELINE"
    PRE_APPROVAL = "PRE_APPROVAL"
    DECISIONS = "DECISIONS"
    UNKNOWN = "UNKNOWN"
    MANUAL_RETRY = "MANUAL_RETRY"
    SECOND_BATCH = "SECOND_BATCH"


class PhaseStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


class ArtifactKind(StrEnum):
    BATCH_NDJSON = "BATCH_NDJSON"
    HTTP_RESULTS = "HTTP_RESULTS"
    N8N_EXECUTIONS = "N8N_EXECUTIONS"
    KAFKA_OFFSETS = "KAFKA_OFFSETS"
    SMTP_RECEIPT = "SMTP_RECEIPT"
    DB_SNAPSHOT = "DB_SNAPSHOT"


@dataclass(frozen=True, slots=True)
class ExpectedIncident:
    lot_id: str
    chamber_id: str
    expected_action: str
    alarm_sources: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.lot_id, self.chamber_id


@dataclass(frozen=True, slots=True)
class ExpectedOracle:
    dataset_epoch: str
    source_manifest_sha256: str
    incidents: tuple[ExpectedIncident, ...]


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    artifact_id: str
    kind: ArtifactKind
    phase: GoldenPhase
    level_round: int
    payload: Any


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    dataset_epoch: str
    gate_kind: GateKind
    level_rounds: tuple[int, ...]
    scopes: Mapping[GoldenPhase, ExecutionScope]
    artifacts: tuple[EvidenceArtifact, ...]

    def for_phase(
        self,
        phase: GoldenPhase,
        kind: ArtifactKind | None = None,
        *,
        level_round: int | None = None,
    ) -> tuple[EvidenceArtifact, ...]:
        return tuple(
            artifact
            for artifact in self.artifacts
            if artifact.phase is phase
            and (kind is None or artifact.kind is kind)
            and (level_round is None or artifact.level_round == level_round)
        )


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    agent_run_id: str
    lot_id: str
    chamber_id: str
    status: str
    autonomy_level: int
    action: str | None
    retry_of_run_id: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    rehydration_snapshot_bytes: int | None

    @property
    def key(self) -> tuple[str, str]:
        return self.lot_id, self.chamber_id


@dataclass(frozen=True, slots=True)
class ActionSnapshot:
    agent_run_id: str
    action_id: str
    link_role: str
    lot_id: str
    chamber_id: str
    action_code: str

    @property
    def key(self) -> tuple[str, str]:
        return self.lot_id, self.chamber_id


@dataclass(frozen=True, slots=True)
class ApprovalSnapshot:
    approval_id: str
    action_id: str
    agent_run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class DeliverySnapshot:
    action_id: str
    channel: str
    status: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ToolSnapshot:
    agent_run_id: str
    tool_name: str
    status: str


@dataclass(frozen=True, slots=True)
class AuditSnapshot:
    event_type: str
    entity_id: str
    channel: str | None


@dataclass(frozen=True, slots=True)
class GoldenFlowSnapshot:
    runs: tuple[RunSnapshot, ...]
    actions: tuple[ActionSnapshot, ...]
    approvals: tuple[ApprovalSnapshot, ...]
    deliveries: tuple[DeliverySnapshot, ...]
    tools: tuple[ToolSnapshot, ...]
    audits: tuple[AuditSnapshot, ...]
    r03_incidents: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: GoldenPhase
    status: PhaseStatus
    reasons: tuple[str, ...]
    metrics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GoldenFlowResult:
    phases: tuple[PhaseResult, ...]

    @property
    def status(self) -> PhaseStatus:
        if any(item.status is PhaseStatus.FAIL for item in self.phases):
            return PhaseStatus.FAIL
        if any(item.status is PhaseStatus.EVIDENCE_INCOMPLETE for item in self.phases):
            return PhaseStatus.EVIDENCE_INCOMPLETE
        return PhaseStatus.PASS


def load_expected_oracle(payload: object) -> ExpectedOracle:
    """독립 source-derived incident oracle의 exact schema와 12·5/4/3을 고정한다."""

    root = _object(payload, "ORACLE_INVALID")
    _exact_keys(
        root,
        {"format_version", "dataset_epoch", "source_manifest_sha256", "incidents"},
        "ORACLE_INVALID",
    )
    if root["format_version"] != 1 or root["dataset_epoch"] != DATASET_EPOCH:
        raise GoldenFlowContractError("ORACLE_INVALID")
    digest = root["source_manifest_sha256"]
    if not _sha256(digest):
        raise GoldenFlowContractError("ORACLE_INVALID")
    raw_incidents = root["incidents"]
    if not isinstance(raw_incidents, list):
        raise GoldenFlowContractError("ORACLE_INVALID")
    incidents: list[ExpectedIncident] = []
    for raw in raw_incidents:
        item = _object(raw, "ORACLE_INVALID")
        _exact_keys(
            item,
            {"lot_id", "chamber_id", "expected_action", "alarm_sources"},
            "ORACLE_INVALID",
        )
        lot_id = _text(item["lot_id"], "ORACLE_INVALID")
        chamber_id = _text(item["chamber_id"], "ORACLE_INVALID")
        try:
            action = ActionCode(item["expected_action"])
        except (TypeError, ValueError) as exc:
            raise GoldenFlowContractError("ORACLE_INVALID") from exc
        sources = item["alarm_sources"]
        if (
            not isinstance(sources, list)
            or not sources
            or any(source not in {"TRACE", "SUMMARY", "R03"} for source in sources)
            or sources != sorted(set(sources))
        ):
            raise GoldenFlowContractError("ORACLE_INVALID")
        if (action is ActionCode.EQP_HOLD) is not ("R03" in sources):
            raise GoldenFlowContractError("ORACLE_INVALID")
        if action is ActionCode.WARNING and "TRACE" not in sources:
            raise GoldenFlowContractError("ORACLE_INVALID")
        if action is ActionCode.MONITORING and "TRACE" in sources:
            raise GoldenFlowContractError("ORACLE_INVALID")
        incidents.append(
            ExpectedIncident(lot_id, chamber_id, action.value, tuple(sources))
        )
    keys = [item.key for item in incidents]
    counts = Counter(item.expected_action for item in incidents)
    if (
        len(incidents) != EXPECTED_INCIDENT_COUNT
        or len(set(keys)) != EXPECTED_INCIDENT_COUNT
        or dict(counts) != dict(EXPECTED_ACTION_COUNTS)
        or sum("R03" in item.alarm_sources for item in incidents) != 3
    ):
        raise GoldenFlowContractError("ORACLE_INVALID")
    return ExpectedOracle(DATASET_EPOCH, digest, tuple(incidents))


def snapshot_from_mapping(payload: object) -> GoldenFlowSnapshot:
    """Repository와 DB_SNAPSHOT artifact가 공유하는 exact snapshot parser."""

    root = _object(payload, "SNAPSHOT_INVALID")
    _exact_keys(
        root,
        {
            "runs",
            "actions",
            "approvals",
            "deliveries",
            "tools",
            "audits",
            "r03_incidents",
        },
        "SNAPSHOT_INVALID",
    )
    return GoldenFlowSnapshot(
        runs=tuple(_run(item) for item in _array(root["runs"])),
        actions=tuple(_action(item) for item in _array(root["actions"])),
        approvals=tuple(_approval(item) for item in _array(root["approvals"])),
        deliveries=tuple(_delivery(item) for item in _array(root["deliveries"])),
        tools=tuple(_tool(item) for item in _array(root["tools"])),
        audits=tuple(_audit(item) for item in _array(root["audits"])),
        r03_incidents=tuple(
            _incident_key(item) for item in _array(root["r03_incidents"])
        ),
    )


def evaluate_golden_flow(
    snapshot: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    oracle: ExpectedOracle,
    *,
    phase: GoldenPhase | None = None,
) -> GoldenFlowResult:
    """선택 phase 또는 전체 7-phase Gate를 판정한다."""

    phases = (phase,) if phase is not None else tuple(GoldenPhase)
    result = GoldenFlowResult(
        tuple(_evaluate_phase(item, snapshot, evidence, oracle) for item in phases)
    )
    if phase is None:
        result = _apply_cross_phase_checks(result, evidence)
    return _require_live_snapshot(
        result,
        snapshot,
        evidence,
        phase or GoldenPhase.SECOND_BATCH,
    )


def _require_live_snapshot(
    result: GoldenFlowResult,
    live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    target_phase: GoldenPhase,
) -> GoldenFlowResult:
    """현재 DB가 선택 phase(전체는 phase 7)의 마지막 snapshot과 같은지 확인한다."""

    try:
        expected = _snapshot_for(evidence, target_phase, max(evidence.level_rounds))
    except GoldenFlowContractError:
        return result
    if live == expected:
        return result
    return GoldenFlowResult(
        tuple(
            PhaseResult(
                item.phase,
                PhaseStatus.FAIL,
                (*item.reasons, "LIVE_SNAPSHOT_MISMATCH"),
                item.metrics,
            )
            if item.phase is target_phase
            else item
            for item in result.phases
        )
    )


def _apply_cross_phase_checks(
    result: GoldenFlowResult,
    evidence: EvidenceBundle,
) -> GoldenFlowResult:
    """phase snapshot 사이의 불변 identity와 상태 전이만 별도로 검증한다."""

    reasons: list[str] = []
    for level_round in evidence.level_rounds:
        try:
            pre = _snapshot_for(evidence, GoldenPhase.PRE_APPROVAL, level_round)
            decisions = _snapshot_for(evidence, GoldenPhase.DECISIONS, level_round)
            unknown = _snapshot_for(evidence, GoldenPhase.UNKNOWN, level_round)
        except GoldenFlowContractError:
            # 개별 phase가 EVIDENCE_INCOMPLETE로 이미 판정한다.
            continue
        pre_delivery = {(item.action_id, item.channel) for item in pre.deliveries}
        decision_delivery = {
            (item.action_id, item.channel) for item in decisions.deliveries
        }
        if pre_delivery != decision_delivery:
            reasons.append(f"ROUND_{level_round}_DECISION_CREATED_NEW_DELIVERY")
        if {(item.approval_id, item.action_id) for item in pre.approvals} != {
            (item.approval_id, item.action_id) for item in decisions.approvals
        }:
            reasons.append(f"ROUND_{level_round}_DECISION_APPROVAL_IDENTITY_CHANGED")
        if decision_delivery != {
            (item.action_id, item.channel) for item in unknown.deliveries
        }:
            reasons.append(f"ROUND_{level_round}_UNKNOWN_DELIVERY_IDENTITY_CHANGED")
        http = _single_json_for_round(
            evidence,
            GoldenPhase.DECISIONS,
            ArtifactKind.HTTP_RESULTS,
            level_round,
        )
        if http is None:
            continue
        rejected_ids = {
            item.get("action_id")
            for item in http["results"]
            if item["case"] == "REJECT" and 200 <= item.get("status_code", 0) < 300
        }
        approved_ids = {
            item.get("action_id")
            for item in http["results"]
            if item["case"] == "APPROVE" and 200 <= item.get("status_code", 0) < 300
        }
        mes_by_action = {
            item.action_id: item
            for item in decisions.deliveries
            if item.channel == "MES_MOCK"
        }
        if not rejected_ids or any(
            action_id not in mes_by_action
            or mes_by_action[action_id].status != "CANCELED"
            for action_id in rejected_ids
        ):
            reasons.append(f"ROUND_{level_round}_REJECTION_NOT_CANCELED")
        if not approved_ids or any(
            action_id not in mes_by_action
            or mes_by_action[action_id].status not in {"SENT", "FAILED", "UNKNOWN"}
            or mes_by_action[action_id].attempt_count > 1
            for action_id in approved_ids
        ):
            reasons.append(f"ROUND_{level_round}_APPROVAL_EFFECT_NOT_EXACT")
    if not reasons:
        return result
    updated: list[PhaseResult] = []
    for item in result.phases:
        if item.phase is GoldenPhase.DECISIONS:
            updated.append(
                PhaseResult(
                    item.phase,
                    PhaseStatus.FAIL,
                    (*item.reasons, *reasons),
                    item.metrics,
                )
            )
        else:
            updated.append(item)
    return GoldenFlowResult(tuple(updated))


def _snapshot_for(
    evidence: EvidenceBundle,
    phase: GoldenPhase,
    level_round: int,
) -> GoldenFlowSnapshot:
    artifacts = evidence.for_phase(
        phase, ArtifactKind.DB_SNAPSHOT, level_round=level_round
    )
    if len(artifacts) != 1:
        raise GoldenFlowContractError("SNAPSHOT_INVALID")
    return snapshot_from_mapping(artifacts[0].payload)


def _single_json_for_round(
    evidence: EvidenceBundle,
    phase: GoldenPhase,
    kind: ArtifactKind,
    level_round: int,
) -> Mapping[str, Any] | None:
    artifacts = evidence.for_phase(phase, kind, level_round=level_round)
    if len(artifacts) != 1 or not isinstance(artifacts[0].payload, Mapping):
        return None
    return artifacts[0].payload


def level_metrics(
    snapshot: GoldenFlowSnapshot, *, batch_wall_clock_ms: int
) -> dict[str, Any]:
    """Level 1·2 비교표의 5개 지표를 NULL을 숨기지 않고 계산한다."""

    comparison_runs = [run for run in snapshot.runs if run.retry_of_run_id is None]
    terminal = Counter(run.status for run in comparison_runs)
    completed = terminal["COMPLETED"] + terminal["FAILED"]
    latencies = [
        run.latency_ms for run in comparison_runs if run.latency_ms is not None
    ]
    token_rows = [
        run.input_tokens + run.output_tokens
        for run in comparison_runs
        if run.input_tokens is not None and run.output_tokens is not None
    ]
    snapshots = [
        run.rehydration_snapshot_bytes
        for run in comparison_runs
        if run.rehydration_snapshot_bytes is not None
    ]
    return {
        "run_active_latency_ms": {
            "count": len(latencies),
            "sum": sum(latencies),
            "mean": None if not latencies else mean(latencies),
        },
        "batch_wall_clock_ms": batch_wall_clock_ms,
        "completion_rate": {
            "numerator": completed,
            "numerator_by_status": {
                "COMPLETED": terminal["COMPLETED"],
                "FAILED": terminal["FAILED"],
            },
            "denominator": 12,
        },
        "tool_calls": {
            f"{tool_name}:{status}": count
            for (tool_name, status), count in sorted(
                Counter(
                    (item.tool_name, item.status) for item in snapshot.tools
                ).items()
            )
        },
        "tokens": {
            "count": len(token_rows),
            "sum": sum(token_rows),
            "mean": None if not token_rows else mean(token_rows),
            "null_run_count": sum(
                run.input_tokens is None or run.output_tokens is None
                for run in comparison_runs
            ),
            "failed_run_count": terminal["FAILED"],
        },
        "rehydration_snapshot_bytes": {
            "count": len(snapshots),
            "min": None if not snapshots else min(snapshots),
            "max": None if not snapshots else max(snapshots),
            "mean": None if not snapshots else mean(snapshots),
        },
    }


def _evaluate_phase(
    phase: GoldenPhase,
    live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    oracle: ExpectedOracle,
) -> PhaseResult:
    checks = {
        GoldenPhase.PREFLIGHT: _preflight,
        GoldenPhase.BATCH_BASELINE: _batch_baseline,
        GoldenPhase.PRE_APPROVAL: _pre_approval,
        GoldenPhase.DECISIONS: _decisions,
        GoldenPhase.UNKNOWN: _unknown,
        GoldenPhase.MANUAL_RETRY: _manual_retry,
        GoldenPhase.SECOND_BATCH: _second_batch,
    }
    combined_reasons: list[str] = []
    metrics_by_round: dict[str, Any] = {}
    for level_round in evidence.level_rounds:
        artifacts = evidence.for_phase(phase, level_round=level_round)
        if not artifacts:
            return PhaseResult(
                phase,
                PhaseStatus.EVIDENCE_INCOMPLETE,
                (f"ROUND_{level_round}_PHASE_EVIDENCE_MISSING",),
                {},
            )
        db_artifacts = [
            item for item in artifacts if item.kind is ArtifactKind.DB_SNAPSHOT
        ]
        if len(db_artifacts) != 1:
            return PhaseResult(
                phase,
                PhaseStatus.EVIDENCE_INCOMPLETE,
                (f"ROUND_{level_round}_DB_SNAPSHOT_MISSING",),
                {},
            )
        phase_snapshot = snapshot_from_mapping(db_artifacts[0].payload)
        round_evidence = EvidenceBundle(
            dataset_epoch=evidence.dataset_epoch,
            gate_kind=evidence.gate_kind,
            level_rounds=(level_round,),
            scopes=evidence.scopes,
            artifacts=artifacts,
        )
        reasons, metrics = checks[phase](phase_snapshot, live, round_evidence, oracle)
        combined_reasons.extend(f"ROUND_{level_round}_{reason}" for reason in reasons)
        metrics_by_round[str(level_round)] = metrics
    return PhaseResult(
        phase,
        PhaseStatus.PASS if not combined_reasons else PhaseStatus.FAIL,
        tuple(combined_reasons),
        metrics_by_round,
    )


def _preflight(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    expected_keys = {item.key for item in oracle.incidents}
    if set(snapshot.r03_incidents) != {
        item.key for item in oracle.incidents if "R03" in item.alarm_sources
    }:
        reasons.append("R03_INCIDENTS_NOT_EXACT")
    batches = evidence.for_phase(GoldenPhase.PREFLIGHT, ArtifactKind.BATCH_NDJSON)
    if len(batches) != 1:
        return [*reasons, "PREFLIGHT_BATCH_EVIDENCE_MISSING"], {}
    plan = _only_line(batches[0].payload, "plan")
    selected = {(item["lot_id"], item["chamber_id"]) for item in plan["selected"]}
    if selected != expected_keys:
        reasons.append("PREFLIGHT_SELECTED_NOT_EXACT")
    if plan["rejected"] or plan["incomplete"]:
        reasons.append("PREFLIGHT_REJECTED_OR_INCOMPLETE")
    excluded = plan["excluded"]
    if excluded != {"canonical_null_rows": 0, "canonical_null_by_source": {}}:
        reasons.append("PREFLIGHT_CANONICAL_NULL")
    return reasons, {"selected": len(selected), "r03": len(snapshot.r03_incidents)}


def _batch_baseline(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    batches = evidence.for_phase(GoldenPhase.BATCH_BASELINE, ArtifactKind.BATCH_NDJSON)
    if len(batches) != 1:
        return ["BASELINE_BATCH_EVIDENCE_MISSING"], {}
    lines = batches[0].payload
    incidents = [line for line in lines if line["type"] == "incident"]
    outcomes = Counter(line["outcome"] for line in incidents)
    if outcomes != Counter({"STARTED_COMPLETED": 9, "STARTED_WAITING_APPROVAL": 3}):
        reasons.append("BASELINE_OUTCOMES_NOT_EXACT")
    final = _only_line(lines, "final")
    expected_final = {
        "type": "final",
        "attempted": 12,
        "succeeded": 12,
        "failed": 0,
        "skipped": 0,
        "new_runs_observed": 12,
        "new_actions_observed": 12,
        "new_deliveries_observed": 10,
    }
    if final != expected_final:
        reasons.append("BASELINE_FINAL_NOT_EXACT")
    created = [item for item in snapshot.actions if item.link_role == "CREATED"]
    expected = {item.key: item.expected_action for item in oracle.incidents}
    if len(created) != 12 or Counter(item.key for item in created) != Counter(
        expected.keys()
    ):
        reasons.append("BASELINE_CREATED_ACTIONS_NOT_EXACT")
    elif any(expected[item.key] != item.action_code for item in created):
        reasons.append("BASELINE_ACTION_CLASS_MISMATCH")
    channels = Counter(item.channel for item in snapshot.deliveries)
    if channels != Counter({"EMAIL": 7, "MES_MOCK": 3}):
        reasons.append("BASELINE_DELIVERY_PLAN_NOT_EXACT")
    independent_delivery_count = sum(
        len(resolve_delivery_channels(ActionCode(item.expected_action)))
        for item in oracle.incidents
    )
    if independent_delivery_count != 10:
        reasons.append("DELIVERY_RULE_CONTRACT_CHANGED")
    wall_clock = _batch_wall_clock(evidence, GoldenPhase.BATCH_BASELINE)
    if wall_clock is None:
        reasons.append("BATCH_WALL_CLOCK_MISSING")
    return reasons, {
        "outcomes": dict(outcomes),
        "delivery_channels": dict(channels),
        "batch_wall_clock_ms": wall_clock,
    }


def _pre_approval(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons = _require_kinds(
        evidence,
        GoldenPhase.PRE_APPROVAL,
        {
            ArtifactKind.N8N_EXECUTIONS,
            ArtifactKind.KAFKA_OFFSETS,
            ArtifactKind.SMTP_RECEIPT,
        },
    )
    holds = {
        item.key for item in oracle.incidents if item.expected_action == "EQP_HOLD"
    }
    hold_actions = {
        item.action_id
        for item in snapshot.actions
        if item.key in holds and item.link_role == "CREATED"
    }
    mes = [
        item
        for item in snapshot.deliveries
        if item.action_id in hold_actions and item.channel == "MES_MOCK"
    ]
    if len(mes) != 3 or any(
        item.status != "BLOCKED" or item.attempt_count != 0 for item in mes
    ):
        reasons.append("PRE_APPROVAL_MES_NOT_BLOCKED")
    emails = [
        item
        for item in snapshot.deliveries
        if item.action_id in hold_actions and item.channel == "EMAIL"
    ]
    if len(emails) != 3 or any(item.status != "SENT" for item in emails):
        reasons.append("PRE_APPROVAL_EMAIL_NOT_SENT")
    approvals = [item for item in snapshot.approvals if item.action_id in hold_actions]
    if len(approvals) != 3 or any(item.status != "PENDING" for item in approvals):
        reasons.append("PRE_APPROVAL_REQUESTS_NOT_PENDING")
    n8n = _single_json(evidence, GoldenPhase.PRE_APPROVAL, ArtifactKind.N8N_EXECUTIONS)
    if n8n is not None:
        wf2_actions = {
            item["action_id"]
            for item in n8n["executions"]
            if item["workflow"] == "WF2" and item["status"] == "SUCCESS"
        }
        if wf2_actions != hold_actions:
            reasons.append("PRE_APPROVAL_WF2_NOT_EXACT")
    smtp = _single_json(evidence, GoldenPhase.PRE_APPROVAL, ArtifactKind.SMTP_RECEIPT)
    if (
        smtp is not None
        and {item["action_id"] for item in smtp["receipts"]} != hold_actions
    ):
        reasons.append("PRE_APPROVAL_SMTP_NOT_EXACT")
    kafka = _single_json(evidence, GoldenPhase.PRE_APPROVAL, ArtifactKind.KAFKA_OFFSETS)
    if kafka is not None and kafka["after"] - kafka["before"] != 0:
        reasons.append("PRE_APPROVAL_KAFKA_DELTA_NONZERO")
    return reasons, {"blocked_mes": len(mes), "sent_email": len(emails)}


def _decisions(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    _oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons = _require_kinds(
        evidence,
        GoldenPhase.DECISIONS,
        {
            ArtifactKind.HTTP_RESULTS,
            ArtifactKind.N8N_EXECUTIONS,
            ArtifactKind.KAFKA_OFFSETS,
        },
    )
    statuses = Counter(item.status for item in snapshot.approvals)
    if statuses["APPROVED"] < 1 or statuses["REJECTED"] < 1:
        reasons.append("DECISION_ROWS_MISSING")
    http = _single_json(evidence, GoldenPhase.DECISIONS, ArtifactKind.HTTP_RESULTS)
    if http is not None:
        codes = [
            item["status_code"]
            for item in http["results"]
            if item["case"] == "CONCURRENT_DECISION"
        ]
        if (
            len(codes) != 2
            or sum(200 <= code < 300 for code in codes) != 1
            or codes.count(409) != 1
        ):
            reasons.append("CONCURRENT_DECISION_NOT_EXACT")
    action_sent = {
        item.entity_id for item in snapshot.audits if item.event_type == "ACTION_SENT"
    }
    send_actions = {
        item.agent_run_id for item in snapshot.tools if item.tool_name == "send_action"
    }
    action_to_run = {item.action_id: item.agent_run_id for item in snapshot.actions}
    for action_id in action_sent:
        run_id = action_to_run.get(action_id)
        if run_id is None or run_id not in send_actions:
            reasons.append("SEND_ACTION_TOOL_AUDIT_UNBOUND")
            break
    kafka = _single_json(evidence, GoldenPhase.DECISIONS, ArtifactKind.KAFKA_OFFSETS)
    if kafka is not None and kafka["after"] - kafka["before"] > 1:
        reasons.append("DECISION_EXTERNAL_EFFECT_DUPLICATED")
    return reasons, {"approval_statuses": dict(statuses)}


def _unknown(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    _oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons = _require_kinds(
        evidence,
        GoldenPhase.UNKNOWN,
        {ArtifactKind.HTTP_RESULTS, ArtifactKind.KAFKA_OFFSETS},
    )
    unknown = [item for item in snapshot.deliveries if item.status == "UNKNOWN"]
    http = _single_json(evidence, GoldenPhase.UNKNOWN, ArtifactKind.HTTP_RESULTS)
    if not unknown:
        reasons.append("UNKNOWN_DELIVERY_MISSING")
    if http is not None:
        retry = [item for item in http["results"] if item["case"] == "UNKNOWN_RETRY"]
        if (
            len(retry) != 1
            or retry[0]["exit_code"] != 3
            or retry[0].get("before_status") != "UNKNOWN"
            or retry[0].get("after_status") != "UNKNOWN"
        ):
            reasons.append("UNKNOWN_RETRY_NOT_BLOCKED")
        failed_retry = [
            item for item in http["results"] if item["case"] == "FAILED_RETRY"
        ]
        if len(failed_retry) != 1 or failed_retry[0]["exit_code"] != 0:
            reasons.append("FAILED_RETRY_EVIDENCE_MISSING")
    kafka = _single_json(evidence, GoldenPhase.UNKNOWN, ArtifactKind.KAFKA_OFFSETS)
    if kafka is not None and kafka["after"] - kafka["before"] != 0:
        reasons.append("UNKNOWN_AUTO_RESEND_DETECTED")
    return reasons, {"unknown_deliveries": len(unknown)}


def _manual_retry(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    _evidence: EvidenceBundle,
    _oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    by_id = {run.agent_run_id: run for run in snapshot.runs}
    retries = [run for run in snapshot.runs if run.retry_of_run_id is not None]
    if not retries:
        reasons.append("MANUAL_RETRY_MISSING")
    for retry in retries:
        original = by_id.get(retry.retry_of_run_id or "")
        if original is None or original.status != "FAILED" or original.key != retry.key:
            reasons.append("MANUAL_RETRY_LINEAGE_INVALID")
            break
    return reasons, {"retry_runs": len(retries)}


def _second_batch(
    snapshot: GoldenFlowSnapshot,
    _live: GoldenFlowSnapshot,
    evidence: EvidenceBundle,
    _oracle: ExpectedOracle,
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    batches = evidence.for_phase(GoldenPhase.SECOND_BATCH, ArtifactKind.BATCH_NDJSON)
    if len(batches) != 2:
        return ["SECOND_BATCH_EVIDENCE_NOT_EXACT"], {}
    plan_artifacts = [
        item for item in batches if any(line["type"] == "plan" for line in item.payload)
    ]
    final_artifacts = [
        item
        for item in batches
        if any(line["type"] == "final" for line in item.payload)
    ]
    if len(plan_artifacts) != 1 or len(final_artifacts) != 1:
        return ["SECOND_BATCH_EVIDENCE_NOT_EXACT"], {}
    plan = _only_line(plan_artifacts[0].payload, "plan")
    if (
        plan["selected"]
        or plan["rejected"]
        or plan["incomplete"]
        or plan["excluded"]
        != {"canonical_null_rows": 0, "canonical_null_by_source": {}}
    ):
        reasons.append("SECOND_BATCH_PLAN_NOT_EMPTY")
    final = _only_line(final_artifacts[0].payload, "final")
    if final != {
        "type": "final",
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "new_runs_observed": 0,
        "new_actions_observed": 0,
        "new_deliveries_observed": 0,
    }:
        reasons.append("SECOND_BATCH_FINAL_NOT_ZERO")
    wall_clock = _batch_wall_clock(evidence, GoldenPhase.SECOND_BATCH)
    if wall_clock is None:
        reasons.append("BATCH_WALL_CLOCK_MISSING")
    metrics = level_metrics(snapshot, batch_wall_clock_ms=wall_clock or 0)
    return reasons, metrics


def _require_kinds(
    evidence: EvidenceBundle, phase: GoldenPhase, kinds: set[ArtifactKind]
) -> list[str]:
    present = {item.kind for item in evidence.for_phase(phase)}
    return [] if kinds <= present else ["PHASE_EXTERNAL_EVIDENCE_MISSING"]


def _single_json(
    evidence: EvidenceBundle, phase: GoldenPhase, kind: ArtifactKind
) -> Mapping[str, Any] | None:
    items = evidence.for_phase(phase, kind)
    if len(items) != 1 or not isinstance(items[0].payload, Mapping):
        return None
    return items[0].payload


def _only_line(payload: Any, kind: str) -> Mapping[str, Any]:
    lines = [line for line in payload if line["type"] == kind]
    if len(lines) != 1:
        raise GoldenFlowContractError("BATCH_NDJSON_INVALID")
    return lines[0]


def _batch_wall_clock(evidence: EvidenceBundle, phase: GoldenPhase) -> int | None:
    http = _single_json(evidence, phase, ArtifactKind.HTTP_RESULTS)
    if http is None:
        return None
    values = [
        item.get("duration_ms")
        for item in http["results"]
        if item["case"] == "BATCH_WALL_CLOCK"
    ]
    if len(values) != 1 or not isinstance(values[0], int):
        return None
    return values[0]


def _object(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoldenFlowContractError(code)
    return value


def _array(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise GoldenFlowContractError("SNAPSHOT_INVALID")
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], code: str) -> None:
    if set(value) != keys:
        raise GoldenFlowContractError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldenFlowContractError(code)
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GoldenFlowContractError("SNAPSHOT_INVALID")
    return value


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _run(value: object) -> RunSnapshot:
    item = _object(value, "SNAPSHOT_INVALID")
    keys = {
        "agent_run_id",
        "lot_id",
        "chamber_id",
        "status",
        "autonomy_level",
        "action",
        "retry_of_run_id",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "rehydration_snapshot_bytes",
    }
    _exact_keys(item, keys, "SNAPSHOT_INVALID")
    autonomy = item["autonomy_level"]
    if isinstance(autonomy, bool) or autonomy not in {1, 2, 3}:
        raise GoldenFlowContractError("SNAPSHOT_INVALID")
    return RunSnapshot(
        _text(item["agent_run_id"], "SNAPSHOT_INVALID"),
        _text(item["lot_id"], "SNAPSHOT_INVALID"),
        _text(item["chamber_id"], "SNAPSHOT_INVALID"),
        _text(item["status"], "SNAPSHOT_INVALID"),
        autonomy,
        None if item["action"] is None else _text(item["action"], "SNAPSHOT_INVALID"),
        None
        if item["retry_of_run_id"] is None
        else _text(item["retry_of_run_id"], "SNAPSHOT_INVALID"),
        _optional_int(item["latency_ms"]),
        _optional_int(item["input_tokens"]),
        _optional_int(item["output_tokens"]),
        _optional_int(item["rehydration_snapshot_bytes"]),
    )


def _action(value: object) -> ActionSnapshot:
    item = _object(value, "SNAPSHOT_INVALID")
    _exact_keys(
        item,
        {
            "agent_run_id",
            "action_id",
            "link_role",
            "lot_id",
            "chamber_id",
            "action_code",
        },
        "SNAPSHOT_INVALID",
    )
    return ActionSnapshot(
        *(
            _text(item[key], "SNAPSHOT_INVALID")
            for key in (
                "agent_run_id",
                "action_id",
                "link_role",
                "lot_id",
                "chamber_id",
                "action_code",
            )
        )
    )


def _approval(value: object) -> ApprovalSnapshot:
    item = _object(value, "SNAPSHOT_INVALID")
    _exact_keys(
        item, {"approval_id", "action_id", "agent_run_id", "status"}, "SNAPSHOT_INVALID"
    )
    return ApprovalSnapshot(
        *(
            _text(item[key], "SNAPSHOT_INVALID")
            for key in ("approval_id", "action_id", "agent_run_id", "status")
        )
    )


def _delivery(value: object) -> DeliverySnapshot:
    item = _object(value, "SNAPSHOT_INVALID")
    _exact_keys(
        item, {"action_id", "channel", "status", "attempt_count"}, "SNAPSHOT_INVALID"
    )
    count = _optional_int(item["attempt_count"])
    assert count is not None
    return DeliverySnapshot(
        _text(item["action_id"], "SNAPSHOT_INVALID"),
        _text(item["channel"], "SNAPSHOT_INVALID"),
        _text(item["status"], "SNAPSHOT_INVALID"),
        count,
    )


def _tool(value: object) -> ToolSnapshot:
    item = _object(value, "SNAPSHOT_INVALID")
    _exact_keys(item, {"agent_run_id", "tool_name", "status"}, "SNAPSHOT_INVALID")
    return ToolSnapshot(
        *(
            _text(item[key], "SNAPSHOT_INVALID")
            for key in ("agent_run_id", "tool_name", "status")
        )
    )


def _audit(value: object) -> AuditSnapshot:
    item = _object(value, "SNAPSHOT_INVALID")
    _exact_keys(item, {"event_type", "entity_id", "channel"}, "SNAPSHOT_INVALID")
    channel = item["channel"]
    return AuditSnapshot(
        _text(item["event_type"], "SNAPSHOT_INVALID"),
        _text(item["entity_id"], "SNAPSHOT_INVALID"),
        None if channel is None else _text(channel, "SNAPSHOT_INVALID"),
    )


def _incident_key(value: object) -> tuple[str, str]:
    item = _object(value, "SNAPSHOT_INVALID")
    _exact_keys(item, {"lot_id", "chamber_id"}, "SNAPSHOT_INVALID")
    return _text(item["lot_id"], "SNAPSHOT_INVALID"), _text(
        item["chamber_id"], "SNAPSHOT_INVALID"
    )


__all__ = [
    "ArtifactKind",
    "DATASET_EPOCH",
    "EvidenceArtifact",
    "EvidenceBundle",
    "ExecutionScope",
    "ExpectedOracle",
    "GateKind",
    "GoldenFlowContractError",
    "GoldenFlowResult",
    "GoldenFlowSnapshot",
    "GoldenPhase",
    "PhaseStatus",
    "evaluate_golden_flow",
    "level_metrics",
    "load_expected_oracle",
    "snapshot_from_mapping",
]
