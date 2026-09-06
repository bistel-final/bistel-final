"""Offline replay of a captured Level 3 pre-HITL round; never runs a Tool/LLM.

The live collector owns snapshot authenticity. This layer reconstructs scoped
observations with production DTOs, arithmetic, citations and action policy.
Reported metrics are recomputed, not accepted as deployment permission.
"""

from collections import Counter
from math import ceil
from typing import Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, model_serializer

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    digest,
    resolve_component,
)
from app.agent.release_delivery import EmailTarget, EmailTargetV2, Identifier
from app.agent.release_model import RuntimeLlmConfiguration
from app.agent.release_prepared import Attempt, Revision, RuntimeImages, UtcTime, utc
from app.agent.u10_comparison import Tokens

READ_TOOLS = (
    "get_fdc_summary",
    "get_equipment_context",
    "search_documents",
    "get_chamber_parameter_history",
    "get_metrology_result",
)
BUDGET = dict(
    level12_total=8, level3_total=10, send=2, same_tool_attempts=4, selector_steps=10
)


def budget_policy_sha256():
    return digest(canonical_json(BUDGET))


def fixture_sha256():
    from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS

    return digest(canonical_json(sorted(CANONICAL_INCIDENT_KEYS)))


class CapturedRead(EvidenceModel):
    seq: int = Field(ge=1)
    # None only for the mandatory initial FDC read (and its one retry).
    selector_seq: int | None = Field(ge=1)
    tool: Literal[
        "get_fdc_summary",
        "get_equipment_context",
        "search_documents",
        "get_chamber_parameter_history",
        "get_metrology_result",
    ]
    request: dict[str, Any]
    status: Literal["SUCCESS", "ERROR", "TIMEOUT"]
    result: dict[str, Any] | None
    latency_ms: int = Field(ge=0)


class CapturedDelivery(EvidenceModel):
    channel: Literal["EMAIL", "MES"]
    status: Literal[
        "WAITING", "SENDING", "SENT", "FAILED", "UNKNOWN", "BLOCKED", "CANCELED"
    ]
    request_hash: Sha256


class CapturedRun(EvidenceModel):
    run_id: Identifier
    action_id: Identifier
    autonomy_level: Literal[3]
    status: Literal["COMPLETED", "WAITING_APPROVAL", "FAILED", "RUNNING"]
    action_code: Literal["MONITORING", "WARNING", "EQP_HOLD"]
    route: dict[str, Any]
    current_lot_hist_ids: list[str] = Field(min_length=1, max_length=100)
    document_model_code: Identifier
    reads: list[CapturedRead] = Field(max_length=100)
    hypothesis: dict[str, Any] | None
    react_trace: list[dict[str, Any]] = Field(max_length=100)
    error_codes: list[Identifier] = Field(max_length=100)
    hypothesis_tokens: Tokens
    hypothesis_model_revision: Identifier
    hypothesis_prompt_version: Literal[
        "agent-hypothesis-v3-ko1", "agent-hypothesis-v3-ko2"
    ]
    latency_ms: int = Field(ge=0)
    model_config_digest: Sha256
    deliveries: list[CapturedDelivery] = Field(max_length=10)
    send_action_selected: int = Field(ge=0)
    unexpected_external_effects: int = Field(ge=0)


class RoundEvidence(EvidenceModel):
    schema_version: Literal["level3-round1-v1"]
    capture_phase: Literal["BATCH_BASELINE_PRE_HITL"]
    R: Revision
    reset_attempt_id: Attempt
    images: RuntimeImages
    dataset_epoch: Literal["fdc_final_20260818"]
    fixture_sha256: Sha256
    budget_policy_sha256: Sha256
    llm: RuntimeLlmConfiguration
    model_endpoint_sha256: Sha256
    model_config_digest: Sha256
    preflight_output_sha256: Sha256
    prepared_attempt: Component
    smtp_approval: Component
    delivery_receipts: Component
    captured_at: UtcTime
    # Keys are topic/partition identities; values are observed offsets, not deltas.
    kafka_before: dict[str, int] = Field(min_length=1)
    kafka_after: dict[str, int] = Field(min_length=1)
    runs: list[CapturedRun] = Field(min_length=12, max_length=12)


class CapturedRunV2(CapturedRun):
    action_policy_version: Literal["MOCK-NOTIFY-V1"]
    link_type: Literal["CREATED"]


class RoundEvidenceV2(RoundEvidence):
    schema_version: Literal["level3-round1-v2"]
    capture_phase: Literal["POST_MOCK_CONVERGENCE"]
    action_policy_version: Literal["MOCK-NOTIFY-V1"]
    approval_rows: Literal[0]
    mock_sources: Component
    mock_results: Component
    runs: list[CapturedRunV2] = Field(min_length=12, max_length=12)


class RunAssessment(EvidenceModel):
    run_id: Identifier
    lot_id: str
    chamber_id: str
    action_code: str
    status: str
    compared: dict[str, str]
    parameter_findings: int
    unsupported_citations: int
    read_calls: int
    total_tokens: int
    selector_tokens: int
    latency_ms: int
    failed_checks: list[str]
    origin_degraded: bool = False

    @model_serializer(mode="wrap")
    def legacy_shape(self, handler):
        value = handler(self)
        if not self.origin_degraded:
            value.pop("origin_degraded", None)
        return value


class RoundAssessment(EvidenceModel):
    robustness_verdict: Literal["PASS", "FAIL"]
    # Transport failures are independent of investigation robustness.
    delivery_snapshot_verdict: Literal["PASS", "FAIL"]
    failed_checks: list[str]
    delivery_failed_checks: list[str]
    run_assessments: list[RunAssessment]
    action_counts: dict[str, int]
    status_counts: dict[str, int]
    total_tokens: int
    selector_tokens: int
    read_calls: int
    latency_p50_ms: int
    latency_p95_ms: int
    degraded_origin_count: int = Field(default=0, ge=0, le=12)

    @model_serializer(mode="wrap")
    def legacy_shape(self, handler):
        value = handler(self)
        if not self.degraded_origin_count:
            value.pop("degraded_origin_count", None)
        return value


class RoundArtifact(RoundEvidence):
    batch_summary: RoundAssessment


class RoundArtifactV2(RoundEvidenceV2):
    batch_summary: RoundAssessment


def parse_round(value, *, artifact=True):
    is_mock = value.get("schema_version") == "level3-round1-v2"
    model = (
        (RoundArtifactV2 if is_mock else RoundArtifact)
        if artifact
        else (RoundEvidenceV2 if is_mock else RoundEvidence)
    )
    return model.model_validate(value)


def _check(condition, code):
    if not condition:
        raise EvidenceError(code)


def _decode(payload, adapter):
    """Use production types without silently dropping extra captured fields."""
    raw = canonical_json(payload)
    value = adapter.validate_json(raw, strict=True)
    normalized = adapter.dump_python(value, mode="json")

    def keys(original, parsed):
        if isinstance(original, dict):
            _check(
                isinstance(parsed, dict) and original.keys() <= parsed.keys(),
                "ROUND_EVIDENCE_SCHEMA_INVALID",
            )
            for key, item in original.items():
                keys(item, parsed[key])
        elif isinstance(original, list):
            _check(
                isinstance(parsed, list) and len(original) == len(parsed),
                "ROUND_EVIDENCE_SCHEMA_INVALID",
            )
            for item, converted in zip(original, parsed, strict=True):
                keys(item, converted)

    keys(payload, normalized)
    return value


def _assess_run(
    run: CapturedRun, *, is_mock=False
) -> tuple[RunAssessment, list[EmailTarget], list[str]]:
    from app.agent.decision import decide_action
    from app.agent.diagnostics import build_diagnostic_snapshot
    from app.agent.hypothesis_v3 import comparison_matrix, finalize_hypothesis
    from app.agent.react import ReactStep, arguments_digest
    from app.agent.routing import ResolvedIncidentRoute
    from app.agent.state import Hypothesis, HypothesisDraftV3
    from app.agent.u10_evidence import (
        project_hypothesis_citations,
        project_initial_evidence,
        project_read_evidence,
    )
    from app.agent.u10_observations import ObservationContext
    from app.common import tool_contracts as dto

    route = _decode(run.route, TypeAdapter(ResolvedIncidentRoute))
    steps = [step for wafer in route.wafer_routes for step in wafer.steps]
    by_history = {step.lot_hist_id: step for step in steps}
    _check(
        not route.mismatches
        and len(by_history) == len(steps)
        and all(step.lot_id == route.incident.lot_id for step in steps),
        "ROUND_ROUTE_IDENTITY_INVALID",
    )
    context = ObservationContext(
        run.run_id,
        route,
        run.current_lot_hist_ids,
        document_model_code=run.document_model_code,
    )
    _check(
        [r.seq for r in run.reads] == list(range(1, len(run.reads) + 1)),
        "ROUND_READ_SEQUENCE_INVALID",
    )
    types = dict(
        zip(
            READ_TOOLS,
            (
                dto.FdcSummaryToolResult,
                dto.EquipmentContextToolResult,
                dto.DocumentSearchToolResult,
                dto.ChamberParameterHistoryToolResult,
                dto.MetrologyResultToolResult,
            ),
            strict=True,
        )
    )
    available = set(project_initial_evidence(route).values)
    for read in run.reads:
        internal = (
            context.resolve_history_context(read.request)
            if read.tool == "get_chamber_parameter_history"
            else None
        )
        context.authorize(read.tool, read.request, internal)
        if read.status != "SUCCESS":
            _check(read.result is None, "ROUND_FAILED_READ_HAS_EVIDENCE")
            continue
        _check(read.result is not None, "ROUND_SUCCESS_EVIDENCE_MISSING")
        result = _decode(read.result, TypeAdapter(types[read.tool]))
        _check(result.ok, "ROUND_SUCCESS_EVIDENCE_MISSING")
        if read.tool == "get_fdc_summary":
            step = by_history[read.request["lot_hist_id"]]
            _check(
                result.wafer.wafer_no == step.wafer_no
                and result.wafer.equipment_id == step.equipment_id
                and result.wafer.recipe_id in {None, step.recipe_id},
                "ROUND_FDC_IDENTITY_INVALID",
            )
        context.record(read.tool, read.request, result, internal)
        available.update(project_read_evidence(read.tool, result).values)
    inputs = context.hypothesis_inputs()
    compared = comparison_matrix(route, inputs["investigation"]).model_dump()
    failed, delivery_failed, targets = [], [], []
    expected_action = decide_action(route).action
    expected_action = None if expected_action is None else expected_action.value
    if expected_action != run.action_code:
        failed.append("ACTION_MISMATCH")
    expected_status = (
        "WAITING_APPROVAL"
        if expected_action == "EQP_HOLD" and not is_mock
        else "COMPLETED"
    )
    if run.status != expected_status:
        failed.append("RUN_INCOMPLETE")
    if (
        len(run.reads) > 8
        or max(Counter(r.tool for r in run.reads).values(), default=0) > 4
    ):
        failed.append("READ_BUDGET_EXCEEDED")
    trace = [_decode(row, TypeAdapter(ReactStep)) for row in run.react_trace]
    _check(
        [row.seq for row in trace] == list(range(1, len(trace) + 1)),
        "ROUND_TRACE_SEQUENCE_INVALID",
    )
    # The production graph replaces SELECTED with OBSERVED in the same slot.
    # Bind reads to those slots; a fabricated STOP alone cannot explain reads.
    initial = [read for read in run.reads if read.selector_seq is None]
    _check(
        1 <= len(initial) <= 2
        and run.reads[: len(initial)] == initial
        and all(
            read.tool == "get_fdc_summary"
            and read.request == {"lot_hist_id": run.current_lot_hist_ids[0]}
            for read in initial
        ),
        "ROUND_INITIAL_READ_INVALID",
    )
    groups = {None: initial}
    observed = {row.seq: row for row in trace if row.phase == "OBSERVED"}
    for read in run.reads[len(initial) :]:
        _check(read.selector_seq in observed, "ROUND_READ_TRACE_MISMATCH")
        row = observed[read.selector_seq]
        _check(
            row.tool == read.tool
            and row.argument_digest
            == arguments_digest({"tool": read.tool, **read.request}),
            "ROUND_READ_TRACE_MISMATCH",
        )
        groups.setdefault(read.selector_seq, []).append(read)
    _check(set(groups) - {None} == set(observed), "ROUND_READ_TRACE_MISMATCH")
    _check(
        [read.selector_seq for read in run.reads[len(initial) :]]
        == sorted(read.selector_seq for read in run.reads[len(initial) :]),
        "ROUND_READ_TRACE_MISMATCH",
    )
    _check(
        # Unlike the U10 research executor, the live graph retries only the
        # bootstrap FDC call. Each OBSERVED selector slot invokes one read.
        all(len(group) == 1 for key, group in groups.items() if key is not None)
        and (len(initial) == 1 or initial[0].status in {"ERROR", "TIMEOUT"}),
        "ROUND_READ_RETRY_INVALID",
    )
    if any(row.phase == "STOPPED" for row in trace[:-1]):
        failed.append("REACT_NOT_COMPLETED")
    if not trace or trace[-1].phase != "STOPPED" or trace[-1].stop_reason != "LLM_STOP":
        failed.append("REACT_NOT_COMPLETED")
    if (
        any(row.degraded for row in trace)
        or "REACT_DEGRADED_TO_HYPOTHESIS" in run.error_codes
    ):
        failed.append("REACT_DEGRADED")
    if any(row.phase == "SELECTED" for row in trace) or len(trace) > 10:
        failed.append("REACT_INCOMPLETE_OR_OVER_BUDGET")
    if "HYPOTHESIS_STRUCTURE_INVALID" in run.error_codes:
        failed.append("HYPOTHESIS_STRUCTURE_INVALID")
    selector_tokens = sum(
        row.selector_tokens.input + row.selector_tokens.output for row in trace
    )
    if run.send_action_selected or run.unexpected_external_effects:
        failed.append("SAFETY_VIOLATION")
    findings, unsupported, origin_degraded = 0, 0, False
    if run.hypothesis is None:
        failed.append("HYPOTHESIS_MISSING")
    else:
        hypothesis = _decode(run.hypothesis, TypeAdapter(Hypothesis))
        unsupported = len(
            set(project_hypothesis_citations(hypothesis).values) - available
        )
        if unsupported:
            failed.append("UNSUPPORTED_CITATION")
        origin = hypothesis.origin_assessment
        origin_degraded = bool(origin and origin.degraded)
        if origin is None or origin.compared.model_dump() != compared:
            failed.append("COMPARED_MISMATCH")
        if origin is not None:
            draft = hypothesis.model_dump(
                exclude={"parameter_findings", "origin_assessment"}
            )
            draft["parameter_findings_draft"] = [
                dict(parameter_id=f.parameter_id, lot_hist_ids=f.lot_hist_ids)
                for f in hypothesis.parameter_findings
            ]
            draft["origin_claim"] = dict(scope=origin.scope, basis_refs=origin.basis)
            try:
                recomputed = finalize_hypothesis(
                    HypothesisDraftV3.model_validate(draft),
                    inputs["fdc_evidence"],
                    route,
                    build_diagnostic_snapshot(inputs["fdc_evidence"], route),
                    inputs["document_evidence"],
                    inputs["investigation"],
                )
                # Recompute retained evidence/arithmetic, not discarded private IDs.
                # Public degradation metadata is shape-validated and reported only.
                exclude = {
                    "origin_assessment": {
                        "degraded",
                        "degraded_reasons",
                        "dropped_basis_count",
                    }
                }
                if canonical_json(
                    recomputed.model_dump(exclude=exclude)
                ) != canonical_json(hypothesis.model_dump(exclude=exclude)):
                    failed.append("HYPOTHESIS_RECOUNT_MISMATCH")
                else:
                    findings = len(recomputed.parameter_findings)
            except ValueError:
                failed.append("HYPOTHESIS_RECOUNT_INVALID")
        if hypothesis.predicted_fault_code.value != "OTH" and not findings:
            failed.append("PARAMETER_FINDING_REQUIRED")
    if sum(value == "CHECKED" for value in compared.values()) < 3:
        failed.append("INVESTIGATION_COVERAGE")
    expected_channels = (
        []
        if expected_action == "MONITORING"
        else ["EMAIL", "MES"]
        if expected_action == "EQP_HOLD"
        else ["EMAIL"]
    )
    if sorted(d.channel for d in run.deliveries) != expected_channels:
        delivery_failed.append("DELIVERY_CHANNELS")
    for delivery in run.deliveries:
        if delivery.status != (
            "SENT" if delivery.channel == "EMAIL" or is_mock else "BLOCKED"
        ):
            delivery_failed.append(
                "DELIVERY_POST_MOCK_STATE" if is_mock else "DELIVERY_PRE_HITL_STATE"
            )
        if delivery.channel == "EMAIL" and expected_action in {"WARNING", "EQP_HOLD"}:
            targets.append(
                (EmailTargetV2 if is_mock else EmailTarget)(
                    action_id=run.action_id,
                    action_code=expected_action,
                    email_kind="ACTION_NOTIFY"
                    if is_mock
                    else "WARNING_NOTIFY"
                    if is_mock or expected_action == "WARNING"
                    else "APPROVAL_REQUEST",
                    request_hash=delivery.request_hash,
                )
            )
    return (
        RunAssessment(
            run_id=run.run_id,
            lot_id=route.incident.lot_id,
            chamber_id=route.incident.chamber_id,
            action_code=run.action_code,
            status=run.status,
            compared=compared,
            parameter_findings=findings,
            unsupported_citations=unsupported,
            read_calls=len(run.reads),
            total_tokens=run.hypothesis_tokens.input
            + run.hypothesis_tokens.output
            + selector_tokens,
            selector_tokens=selector_tokens,
            latency_ms=run.latency_ms,
            failed_checks=sorted(set(failed)),
            origin_degraded=origin_degraded,
        ),
        targets,
        delivery_failed,
    )


def assess_round(evidence: RoundEvidence) -> tuple[RoundAssessment, list[EmailTarget]]:
    """Rebuild twelve scoped runs and both snapshot axes from observations."""
    from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS

    try:
        evidence = parse_round(evidence.model_dump(), artifact=False)
        is_mock = isinstance(evidence, RoundEvidenceV2)
        utc(evidence.captured_at)
        _check(
            all(
                component.relative_path == name
                for component, name in (
                    (evidence.prepared_attempt, "prepared-attempt.json"),
                    (evidence.smtp_approval, "smtp-approval-grant.json"),
                    (evidence.delivery_receipts, "delivery-receipts.round1.json"),
                )
            ),
            "ROUND_COMPONENT_INVALID",
        )
        _check(
            evidence.reset_attempt_id.endswith(evidence.R[:12]),
            "ROUND_REVISION_MISMATCH",
        )
        _check(
            all(
                i.label_revision == evidence.R
                for i in (
                    evidence.images.backend,
                    evidence.images.frontend,
                    evidence.images.runner,
                )
            ),
            "ROUND_IMAGE_REVISION_MISMATCH",
        )
        _check(evidence.fixture_sha256 == fixture_sha256(), "ROUND_FIXTURE_MISMATCH")
        _check(
            evidence.budget_policy_sha256 == budget_policy_sha256(),
            "ROUND_BUDGET_POLICY_MISMATCH",
        )
        expected_config = digest(
            canonical_json(
                dict(
                    llm=evidence.llm.model_dump(),
                    endpoint_sha256=evidence.model_endpoint_sha256,
                )
            )
        )
        _check(
            evidence.model_config_digest == expected_config
            and all(
                run.model_config_digest == expected_config for run in evidence.runs
            ),
            "ROUND_MODEL_CONFIG_MISMATCH",
        )
        _check(
            all(
                run.hypothesis_model_revision == evidence.llm.hypothesis_model_revision
                and run.hypothesis_prompt_version
                == evidence.llm.hypothesis_prompt_version
                for run in evidence.runs
            ),
            "ROUND_HYPOTHESIS_MODEL_MISMATCH",
        )
        _check(
            len({r.run_id for r in evidence.runs}) == 12
            and len({r.action_id for r in evidence.runs}) == 12,
            "ROUND_RUN_POPULATION_INVALID",
        )
        assessments, targets, delivery_failed = [], [], []
        for run in evidence.runs:
            assessment, emails, problems = _assess_run(run, is_mock=is_mock)
            assessments.append(assessment)
            targets.extend(emails)
            delivery_failed.extend(problems)
            for row in run.react_trace:
                if row.get("llm_model") is not None:
                    _check(
                        row.get("llm_model") == evidence.llm.selector_model_revision
                        and row.get("react_prompt_version")
                        == evidence.llm.selector_prompt_version,
                        "ROUND_TRACE_MODEL_MISMATCH",
                    )
        _check(
            {(a.lot_id, a.chamber_id) for a in assessments} == CANONICAL_INCIDENT_KEYS,
            "ROUND_INCIDENT_POPULATION_INVALID",
        )
        failed = sorted({code for a in assessments for code in a.failed_checks})
        actions, statuses = (
            dict(Counter(a.action_code for a in assessments)),
            dict(Counter(a.status for a in assessments)),
        )
        if actions != {"MONITORING": 5, "WARNING": 4, "EQP_HOLD": 3}:
            failed.append("ACTION_DISTRIBUTION")
        if statuses != (
            {"COMPLETED": 12} if is_mock else {"COMPLETED": 9, "WAITING_APPROVAL": 3}
        ):
            failed.append("STATUS_DISTRIBUTION")
        for group in (
            ("upstream", "downstream"),
            ("sibling", "history"),
            ("metrology",),
        ):
            if not any(a.compared[k] == "CHECKED" for a in assessments for k in group):
                failed.append("BATCH_INVESTIGATION_COVERAGE")
        keys = [d.request_hash for r in evidence.runs for d in r.deliveries]
        if len(keys) != len(set(keys)):
            delivery_failed.append("DELIVERY_KEY_DUPLICATE")
        if is_mock:
            if (
                set(evidence.kafka_before) != set(evidence.kafka_after)
                or len(evidence.kafka_before) != 2
                or any(
                    evidence.kafka_after[k] - v != 3
                    for k, v in evidence.kafka_before.items()
                )
            ):
                delivery_failed.append("MOCK_KAFKA_DELTA")
        elif evidence.kafka_before != evidence.kafka_after or any(
            type(n) is not int or n < 0
            for n in [*evidence.kafka_before.values(), *evidence.kafka_after.values()]
        ):
            delivery_failed.append("PRE_HITL_KAFKA_EFFECT")
        latency = sorted(a.latency_ms for a in assessments)
        return RoundAssessment(
            robustness_verdict="FAIL" if failed else "PASS",
            delivery_snapshot_verdict="FAIL" if delivery_failed else "PASS",
            failed_checks=sorted(set(failed)),
            delivery_failed_checks=sorted(set(delivery_failed)),
            run_assessments=assessments,
            action_counts=actions,
            status_counts=statuses,
            total_tokens=sum(a.total_tokens for a in assessments),
            selector_tokens=sum(a.selector_tokens for a in assessments),
            read_calls=sum(a.read_calls for a in assessments),
            latency_p50_ms=latency[ceil(len(latency) * 0.5) - 1],
            latency_p95_ms=latency[ceil(len(latency) * 0.95) - 1],
            degraded_origin_count=sum(a.origin_degraded for a in assessments),
        ), targets
    except ValidationError:
        raise EvidenceError("ROUND_EVIDENCE_SCHEMA_INVALID") from None


def build_round(evidence: RoundEvidence) -> RoundArtifact:
    summary, _ = assess_round(evidence)
    model = RoundArtifactV2 if isinstance(evidence, RoundEvidenceV2) else RoundArtifact
    return model(**evidence.model_dump(), batch_summary=summary)


def verify_round(root, reference: Component):
    _check(reference.relative_path == "round1.json", "ROUND_COMPONENT_INVALID")
    try:
        artifact = parse_round(resolve_component(root, reference))
    except ValidationError:
        raise EvidenceError("ROUND_EVIDENCE_SCHEMA_INVALID") from None
    evidence = parse_round(
        artifact.model_dump(exclude={"batch_summary"}), artifact=False
    )
    summary, targets = assess_round(evidence)
    if isinstance(evidence, RoundEvidenceV2):
        from app.agent.release_mock import MockTarget, verify_mock_results

        by_run = {a.run_id: a for a in summary.run_assessments}
        mock_targets = [
            MockTarget(
                action_id=r.action_id,
                created_run_id=r.run_id,
                incident_key={
                    "lot_id": by_run[r.run_id].lot_id,
                    "chamber_id": by_run[r.run_id].chamber_id,
                },
                request_hash=next(
                    d.request_hash for d in r.deliveries if d.channel == "MES"
                ),
                action_code=r.action_code,
                action_policy_version=r.action_policy_version,
                link_type=r.link_type,
            )
            for r in evidence.runs
            if r.action_code == "EQP_HOLD"
        ]
        checked = verify_mock_results(
            root=root,
            sources=evidence.mock_sources,
            results=evidence.mock_results,
            targets=mock_targets,
            expected_attempt_id=evidence.reset_attempt_id,
        )
        before, after = {}, {}
        for topic, window in (
            ("fdc.actions", checked.summary.actions_topic),
            ("fdc.actions.result", checked.summary.result_topic),
        ):
            key = f"{topic}:{window.partition}"
            before[key], after[key] = window.offset_before, window.offset_after
        _check(
            evidence.kafka_before == before and evidence.kafka_after == after,
            "MOCK_KAFKA_BINDING_MISMATCH",
        )
    _check(summary == artifact.batch_summary, "ROUND_SUMMARY_MISMATCH")
    _check(
        canonical_json(resolve_component(root, reference)) == canonical_json(artifact),
        "ROUND_EVIDENCE_DRIFT",
    )
    return artifact, summary, targets
