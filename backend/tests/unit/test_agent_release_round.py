"""Synthetic DTO replay, NOT a real canonical-dataset qualification batch."""

import subprocess
import sys
from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from app.agent import release_round as subject
from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS, build_diagnostic_snapshot
from app.agent.hypothesis_v3 import finalize_hypothesis
from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    canonical_json,
    component_ref,
    digest,
    write_private,
)
from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import HypothesisDraftV3
from app.agent.u10_observations import ObservationContext
from app.common import tool_contracts as dto
from tests.unit.test_agent_graph import _fdc
from tests.unit.test_agent_react import NOW, _level3_route
from tests.unit.test_agent_release import AT, ATTEMPT, REV, S, prepared_payload
from tests.unit.test_agent_u10_observations import history_call, history_result

LLM = dict(
    hypothesis_model_revision="test-hypothesis",
    selector_model_revision="test-selector",
    hypothesis_prompt_version="agent-hypothesis-v3-ko1",
    selector_prompt_version="agent-react-v2-ko1",
    temperature=0.0,
    seed=None,
)
CONFIG = digest(canonical_json(dict(llm=LLM, endpoint_sha256=S)))


def synthetic_run(index, lot, chamber):
    """Borrow the fixed incident keys, NOT their real source rows or findings.

    Every route/value/alarm below is synthetic. The positive path deliberately
    uses actual scope, citation and hypothesis arithmetic functions, not stubs.
    """
    from app.agent.react import ReactStep, arguments_digest

    source = "SUMMARY" if index < 5 else "TRACE" if index < 9 else "R03"
    action = "MONITORING" if index < 5 else "WARNING" if index < 9 else "EQP_HOLD"
    raw = TypeAdapter(ResolvedIncidentRoute).dump_json(_level3_route()).decode()
    raw = raw.replace("LOT001", lot).replace("EQP01-PM1", chamber)
    raw = raw.replace('"source":"TRACE"', f'"source":"{source}"')
    route = TypeAdapter(ResolvedIncidentRoute).validate_json(raw)
    wafer = route.wafer_routes[0]
    downstream = replace(
        wafer.steps[0],
        lot_hist_id="LH-DOWN",
        step_id="CT-ETCH",
        chamber_id="SYNTHETIC-DOWNSTREAM",
    )
    route = replace(
        route, wafer_routes=(replace(wafer, steps=(*wafer.steps, downstream)),)
    )
    ctx = ObservationContext(
        f"run-{index}", route, ["LH-REP"], document_model_code="MODEL-1"
    )
    reads, trace = [], []

    def record(tool, request, result, *, initial=False):
        internal = (
            ctx.resolve_history_context(request)
            if tool == "get_chamber_parameter_history"
            else None
        )
        ctx.authorize(tool, request, internal)
        ctx.record(tool, request, result, internal)
        seq = None if initial else len(trace) + 1
        reads.append(
            dict(
                seq=len(reads) + 1,
                selector_seq=seq,
                tool=tool,
                request=request,
                status="SUCCESS",
                result=result.model_dump(mode="json"),
                latency_ms=10,
            )
        )
        if not initial:
            trace.append(
                ReactStep(
                    seq=seq,
                    phase="OBSERVED",
                    tool=tool,
                    rationale_summary="test evidence",
                    argument_digest=arguments_digest({"tool": tool, **request}),
                    observation_summary="test observed",
                    react_prompt_version=LLM["selector_prompt_version"],
                    llm_model=LLM["selector_model_revision"],
                    selector_tokens={"input": 7, "output": 3},
                ).model_dump(mode="json")
            )

    fdc = _fdc()
    fdc.wafer.lot_id, fdc.wafer.chamber_id = lot, chamber
    parameter = fdc.parameters[0]
    parameter.target, parameter.ctrl_lower, parameter.ctrl_upper = 10, 8, 12
    parameter.value_min, parameter.value_max = 9, 16
    record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, fdc, initial=True)
    adjacent = fdc.model_copy(deep=True)
    adjacent.wafer.lot_hist_id = "LH-DOWN"
    adjacent.wafer.chamber_id = "SYNTHETIC-DOWNSTREAM"
    adjacent.wafer.step_id = "CT-ETCH"
    record("get_fdc_summary", {"lot_hist_id": "LH-DOWN"}, adjacent)
    request, _ = history_call(ctx)
    history = history_result()
    history.chamber_id, history.current.lot_id = chamber, lot
    record("get_chamber_parameter_history", request, history)
    record(
        "get_metrology_result",
        {"lot_id": lot, "step_id": "CT-PHOTO"},
        dto.MetrologyResultToolResult(
            ok=True,
            lot_id=lot,
            step_id="CT-PHOTO",
            results=[
                dto.MetrologyResultItem(
                    wafer_id=f"{lot}W001",
                    measure_type="CD",
                    measured_value=1.0,
                    alarm_result="PASS",
                    measured_at=NOW,
                )
            ],
            fail_count=0,
            disclaimer="synthetic, not a fault label",
        ),
    )
    trace.append(
        ReactStep(
            seq=len(trace) + 1,
            phase="STOPPED",
            tool="stop",
            rationale_summary="enough test evidence",
            react_prompt_version=LLM["selector_prompt_version"],
            llm_model=LLM["selector_model_revision"],
            selector_tokens={"input": 7, "output": 3},
            stop_reason="LLM_STOP",
        ).model_dump(mode="json")
    )
    inputs = ctx.hypothesis_inputs()
    draft = HypothesisDraftV3.model_validate(
        dict(
            predicted_fault_code="FOC",
            confidence=0.7,
            cause_summary="PARAM-1 test excursion",
            uncertainty="synthetic values",
            supporting_alarms=[route.incident.representative_alarm.model_dump()],
            supporting_parameter_ids=["PARAM-1"],
            supporting_lot_hist_ids=["LH-REP"],
            parameter_findings_draft=[
                dict(parameter_id="PARAM-1", lot_hist_ids=["LH-REP"])
            ],
            origin_claim=dict(
                scope="CURRENT_CHAMBER",
                basis_refs=[dict(namespace="PARAMETER", id="PARAM-1")],
            ),
        )
    )
    hyp = finalize_hypothesis(
        draft,
        inputs["fdc_evidence"],
        route,
        build_diagnostic_snapshot(inputs["fdc_evidence"], route),
        inputs["document_evidence"],
        inputs["investigation"],
    )
    channels = [] if index < 5 else ["EMAIL"] if index < 9 else ["EMAIL", "MES"]
    return dict(
        run_id=f"run-{index}",
        action_id=f"action-{index - 5}",
        autonomy_level=3,
        status="WAITING_APPROVAL" if index >= 9 else "COMPLETED",
        action_code=action,
        route=TypeAdapter(ResolvedIncidentRoute).dump_python(route, mode="json"),
        current_lot_hist_ids=["LH-REP"],
        document_model_code="MODEL-1",
        reads=reads,
        hypothesis=hyp.model_dump(mode="json"),
        react_trace=trace,
        error_codes=[],
        hypothesis_tokens={"input": 70, "output": 30},
        hypothesis_model_revision=LLM["hypothesis_model_revision"],
        hypothesis_prompt_version=LLM["hypothesis_prompt_version"],
        latency_ms=(index + 1) * 100,
        model_config_digest=CONFIG,
        deliveries=[
            dict(
                channel=c,
                status="SENT" if c == "EMAIL" else "BLOCKED",
                request_hash=digest(
                    (
                        f"test-key-{index - 5}" if c == "EMAIL" else f"mes-{index}"
                    ).encode()
                ),
            )
            for c in channels
        ],
        send_action_selected=0,
        unexpected_external_effects=0,
    )


@pytest.fixture(scope="module")
def template():
    return dict(
        schema_version="level3-round1-v1",
        capture_phase="BATCH_BASELINE_PRE_HITL",
        R=REV,
        reset_attempt_id=ATTEMPT,
        images=prepared_payload()["images"],
        dataset_epoch="fdc_final_20260818",
        fixture_sha256=subject.fixture_sha256(),
        budget_policy_sha256=subject.budget_policy_sha256(),
        llm=LLM,
        model_endpoint_sha256=S,
        model_config_digest=CONFIG,
        preflight_output_sha256=S,
        prepared_attempt=dict(relative_path="prepared-attempt.json", sha256=S),
        smtp_approval=dict(relative_path="smtp-approval-grant.json", sha256=S),
        delivery_receipts=dict(relative_path="delivery-receipts.round1.json", sha256=S),
        captured_at=AT,
        kafka_before={"test-topic/0": 1},
        kafka_after={"test-topic/0": 1},
        runs=[
            synthetic_run(i, lot, chamber)
            for i, (lot, chamber) in enumerate(sorted(CANONICAL_INCIDENT_KEYS))
        ],
    )


@pytest.fixture
def evidence(template):
    return deepcopy(template)


def evaluate(evidence):
    return subject.assess_round(subject.RoundEvidence.model_validate(evidence))


def test_twelve_scoped_runs_recompute_counts_citations_arithmetic_and_cost(evidence):
    summary, targets = evaluate(evidence)
    assert summary.robustness_verdict == summary.delivery_snapshot_verdict == "PASS"
    assert summary.failed_checks == summary.delivery_failed_checks == []
    assert summary.action_counts == {"MONITORING": 5, "WARNING": 4, "EQP_HOLD": 3}
    assert summary.status_counts == {"COMPLETED": 9, "WAITING_APPROVAL": 3}
    assert (summary.read_calls, summary.selector_tokens, summary.total_tokens) == (
        48,
        480,
        1680,
    )
    assert (summary.latency_p50_ms, summary.latency_p95_ms) == (600, 1200)
    assert len(targets) == 7
    assert all(
        a.parameter_findings == 1 and a.unsupported_citations == 0
        for a in summary.run_assessments
    )


@pytest.mark.parametrize(
    "kind,code",
    [
        ("run_duplicate", "ROUND_RUN_POPULATION_INVALID"),
        ("action_duplicate", "ROUND_RUN_POPULATION_INVALID"),
        ("fixture", "ROUND_FIXTURE_MISMATCH"),
        ("budget", "ROUND_BUDGET_POLICY_MISMATCH"),
        ("image", "ROUND_IMAGE_REVISION_MISMATCH"),
        ("revision", "ROUND_REVISION_MISMATCH"),
        ("model", "ROUND_MODEL_CONFIG_MISMATCH"),
        ("hypothesis_model", "ROUND_HYPOTHESIS_MODEL_MISMATCH"),
        ("selector_model", "ROUND_TRACE_MODEL_MISMATCH"),
        ("component", "ROUND_COMPONENT_INVALID"),
        ("read_sequence", "ROUND_READ_SEQUENCE_INVALID"),
        ("trace_sequence", "ROUND_TRACE_SEQUENCE_INVALID"),
        ("read_scope", "U10_READ_SCOPE_INVALID"),
        ("result_scope", "U10_OBSERVATION_SCOPE_INVALID"),
        ("trace_binding", "ROUND_READ_TRACE_MISMATCH"),
        ("trace_missing", "ROUND_READ_TRACE_MISMATCH"),
        ("initial", "ROUND_INITIAL_READ_INVALID"),
        ("failed_evidence", "ROUND_FAILED_READ_HAS_EVIDENCE"),
        ("success_missing", "ROUND_SUCCESS_EVIDENCE_MISSING"),
    ],
)
def test_resealed_identity_scope_or_provenance_corruption_is_rejected(
    evidence, kind, code
):
    run = evidence["runs"][0]
    if kind == "run_duplicate":
        evidence["runs"][1]["run_id"] = run["run_id"]
    elif kind == "action_duplicate":
        evidence["runs"][1]["action_id"] = run["action_id"]
    elif kind == "fixture":
        evidence["fixture_sha256"] = "0" * 64
    elif kind == "budget":
        evidence["budget_policy_sha256"] = "0" * 64
    elif kind == "image":
        evidence["images"]["runner"]["label_revision"] = "b" * 40
    elif kind == "revision":
        evidence["R"] = "b" * 40
    elif kind == "model":
        run["model_config_digest"] = "0" * 64
    elif kind == "hypothesis_model":
        run["hypothesis_model_revision"] = "other"
    elif kind == "selector_model":
        run["react_trace"][0]["llm_model"] = "other"
    elif kind == "component":
        evidence["smtp_approval"]["relative_path"] = "other.json"
    elif kind == "read_sequence":
        run["reads"][1]["seq"] = 99
    elif kind == "trace_sequence":
        run["react_trace"][-1]["seq"] = 99
    elif kind == "read_scope":
        run["reads"][0]["request"]["lot_hist_id"] = "OUTSIDE"
    elif kind == "result_scope":
        run["reads"][0]["result"]["wafer"]["lot_id"] = "OUTSIDE"
    elif kind == "trace_binding":
        run["react_trace"][0]["argument_digest"] = "0" * 64
    elif kind == "trace_missing":
        run["reads"][1]["selector_seq"] = 99
    elif kind == "initial":
        run["reads"][0]["selector_seq"] = 1
    elif kind == "failed_evidence":
        run["reads"][0]["status"] = "TIMEOUT"
    elif kind == "success_missing":
        run["reads"][0]["result"] = None
    with pytest.raises(EvidenceError, match=f"^{code}$"):
        evaluate(evidence)


@pytest.mark.parametrize(
    "kind,code",
    [
        ("finding", "HYPOTHESIS_RECOUNT_MISMATCH"),
        ("compared", "COMPARED_MISMATCH"),
        ("citation", "UNSUPPORTED_CITATION"),
        ("hypothesis_missing", "HYPOTHESIS_MISSING"),
        ("degraded", "REACT_DEGRADED"),
        ("stop", "REACT_NOT_COMPLETED"),
        ("action", "ACTION_MISMATCH"),
        ("status", "RUN_INCOMPLETE"),
        ("send", "SAFETY_VIOLATION"),
        ("effect", "SAFETY_VIOLATION"),
    ],
)
def test_investigation_failure_does_not_become_delivery_failure(evidence, kind, code):
    run = evidence["runs"][0]
    if kind == "finding":
        run["hypothesis"]["parameter_findings"][0]["excursion_ratio"] = 999.0
    elif kind == "compared":
        run["hypothesis"]["origin_assessment"]["compared"]["upstream"] = "CHECKED"
    elif kind == "citation":
        run["hypothesis"]["supporting_parameter_ids"].append("MISSING")
    elif kind == "hypothesis_missing":
        run["hypothesis"] = None
    elif kind == "degraded":
        run["error_codes"].append("REACT_DEGRADED_TO_HYPOTHESIS")
    elif kind == "stop":
        run["react_trace"].pop()
    elif kind == "action":
        run["action_code"] = "WARNING"
    elif kind == "status":
        run["status"] = "FAILED"
    elif kind == "send":
        run["send_action_selected"] = 1
    elif kind == "effect":
        run["unexpected_external_effects"] = 1
    summary, _ = evaluate(evidence)
    assert summary.robustness_verdict == "FAIL" and code in summary.failed_checks
    assert summary.delivery_snapshot_verdict == "PASS"


@pytest.mark.parametrize(
    "kind,code",
    [
        ("smtp", "DELIVERY_PRE_HITL_STATE"),
        ("mes", "DELIVERY_PRE_HITL_STATE"),
        ("channels", "DELIVERY_CHANNELS"),
        ("key", "DELIVERY_KEY_DUPLICATE"),
        ("kafka", "PRE_HITL_KAFKA_EFFECT"),
    ],
)
def test_delivery_failure_does_not_become_investigation_failure(evidence, kind, code):
    if kind == "smtp":
        evidence["runs"][5]["deliveries"][0]["status"] = "UNKNOWN"
    elif kind == "mes":
        evidence["runs"][9]["deliveries"][1]["status"] = "SENT"
    elif kind == "channels":
        evidence["runs"][5]["deliveries"] = []
    elif kind == "key":
        evidence["runs"][6]["deliveries"][0]["request_hash"] = evidence["runs"][5][
            "deliveries"
        ][0]["request_hash"]
    elif kind == "kafka":
        evidence["kafka_after"]["test-topic/0"] = 2
    summary, _ = evaluate(evidence)
    assert summary.robustness_verdict == "PASS"
    assert (
        summary.delivery_snapshot_verdict == "FAIL"
        and code in summary.delivery_failed_checks
    )


def test_round_no_clobber_recheck_and_forged_summary(tmp_path, evidence):
    tmp_path.chmod(0o700)
    artifact = subject.build_round(subject.RoundEvidence.model_validate(evidence))
    ref = write_private(tmp_path, "round1.json", artifact)
    assert subject.verify_round(tmp_path, ref)[0] == artifact
    with pytest.raises(EvidenceError):
        write_private(tmp_path, "round1.json", artifact)
    tampered = artifact.model_dump()
    tampered["batch_summary"]["total_tokens"] += 1
    (tmp_path / "round1.json").write_bytes(canonical_json(tampered))
    with pytest.raises(EvidenceError, match="COMPONENT_SHA_MISMATCH"):
        subject.verify_round(tmp_path, ref)
    with pytest.raises(EvidenceError, match="ROUND_SUMMARY_MISMATCH"):
        subject.verify_round(tmp_path, component_ref(tmp_path, "round1.json"))


def test_round_component_filename_is_code_owned(tmp_path):
    with pytest.raises(EvidenceError, match="ROUND_COMPONENT_INVALID"):
        subject.verify_round(tmp_path, Component(relative_path="other.json", sha256=S))


@pytest.mark.parametrize(
    "kind",
    [
        "route_extra",
        "result_extra",
        "trace_extra",
        "hypothesis_extra",
        "trace_bool",
        "trace_string",
        "wafer_bool",
    ],
)
def test_nested_production_dtos_do_not_silently_coerce_or_drop_fields(evidence, kind):
    run = evidence["runs"][0]
    if kind == "route_extra":
        run["route"]["incident"]["fault_code"] = "NRM"
    elif kind == "result_extra":
        run["reads"][0]["result"]["wafer"]["fault_code"] = "NRM"
    elif kind == "trace_extra":
        run["react_trace"][0]["unexpected"] = "ignored?"
    elif kind == "hypothesis_extra":
        run["hypothesis"]["unexpected"] = "ignored?"
    elif kind == "trace_bool":
        run["react_trace"][0]["selector_tokens"]["input"] = True
    elif kind == "trace_string":
        run["react_trace"][0]["selector_tokens"]["input"] = "7"
    elif kind == "wafer_bool":
        run["route"]["wafer_routes"][0]["steps"][0]["wafer_no"] = True
    with pytest.raises(EvidenceError, match="^ROUND_EVIDENCE_SCHEMA_INVALID$"):
        evaluate(evidence)


def test_another_incident_even_with_twelve_unique_run_ids_is_rejected(evidence):
    evidence["runs"][0] = synthetic_run(0, "LOT999", "CHAMBER999")
    with pytest.raises(EvidenceError, match="ROUND_INCIDENT_POPULATION_INVALID"):
        evaluate(evidence)


@pytest.mark.parametrize(
    "field,value", [("wafer_no", 2), ("equipment_id", "OTHER"), ("recipe_id", "OTHER")]
)
def test_fdc_cannot_borrow_another_wafer_equipment_or_recipe(evidence, field, value):
    evidence["runs"][0]["reads"][0]["result"]["wafer"][field] = value
    with pytest.raises(EvidenceError, match="ROUND_FDC_IDENTITY_INVALID"):
        evaluate(evidence)


@pytest.mark.parametrize("kind", ["duplicate_history", "wrong_lot"])
def test_route_history_mapping_must_be_unambiguous(evidence, kind):
    steps = evidence["runs"][0]["route"]["wafer_routes"][0]["steps"]
    if kind == "duplicate_history":
        steps[1]["lot_hist_id"] = steps[0]["lot_hist_id"]
    else:
        steps[1]["lot_id"] = "OTHER"
    with pytest.raises(EvidenceError, match="ROUND_ROUTE_IDENTITY_INVALID"):
        evaluate(evidence)


def test_a_missing_comparison_dimension_cannot_be_claimed_checked(evidence):
    run = evidence["runs"][0]
    run["reads"].pop()
    run["react_trace"].pop(2)
    run["react_trace"][-1]["seq"] = 3
    summary, _ = evaluate(evidence)
    assert "INVESTIGATION_COVERAGE" in summary.failed_checks
    assert "COMPARED_MISMATCH" in summary.failed_checks
    assert summary.run_assessments[0].compared["metrology"] == "NOT_CHECKED"


def test_one_initial_timeout_retry_is_counted_but_not_published(evidence):
    run = evidence["runs"][0]
    failed = deepcopy(run["reads"][0])
    failed.update(status="TIMEOUT", result=None)
    run["reads"].insert(0, failed)
    for seq, read in enumerate(run["reads"], 1):
        read["seq"] = seq
    summary, _ = evaluate(evidence)
    assert summary.robustness_verdict == "PASS"
    assert summary.read_calls == 49
    assert summary.run_assessments[0].parameter_findings == 1


def test_retry_after_success_is_not_a_legal_initial_retry(evidence):
    run = evidence["runs"][0]
    run["reads"].insert(0, deepcopy(run["reads"][0]))
    for seq, read in enumerate(run["reads"], 1):
        read["seq"] = seq
    with pytest.raises(EvidenceError, match="ROUND_READ_RETRY_INVALID"):
        evaluate(evidence)


def test_react_slot_cannot_hide_two_physical_reads(evidence):
    run = evidence["runs"][0]
    retry = deepcopy(run["reads"][1])
    retry.update(status="TIMEOUT", result=None)
    run["reads"].insert(1, retry)
    for seq, read in enumerate(run["reads"], 1):
        read["seq"] = seq
    with pytest.raises(EvidenceError, match="ROUND_READ_RETRY_INVALID"):
        evaluate(evidence)


@pytest.mark.parametrize("extra_reads", [3, 5, 8])
def test_read_same_tool_and_selector_budgets_are_not_taken_from_claimed_summary(
    evidence, extra_reads
):
    run = evidence["runs"][0]
    stop = run["react_trace"].pop()
    for _ in range(extra_reads):
        read = deepcopy(run["reads"][1])
        event = deepcopy(run["react_trace"][0])
        read["seq"] = len(run["reads"]) + 1
        read["selector_seq"] = event["seq"] = len(run["react_trace"]) + 1
        run["reads"].append(read)
        run["react_trace"].append(event)
    stop["seq"] = len(run["react_trace"]) + 1
    run["react_trace"].append(stop)
    summary, _ = evaluate(evidence)
    assert "READ_BUDGET_EXCEEDED" in summary.failed_checks
    if extra_reads == 8:
        assert "REACT_INCOMPLETE_OR_OVER_BUDGET" in summary.failed_checks


def test_deliveries_in_monitoring_and_changed_partition_sets_fail_only_delivery(
    evidence,
):
    evidence["runs"][0]["deliveries"] = deepcopy(evidence["runs"][5]["deliveries"])
    evidence["kafka_after"] = {"new-topic/0": 1}
    summary, _ = evaluate(evidence)
    assert summary.robustness_verdict == "PASS"
    assert {
        "DELIVERY_CHANNELS",
        "DELIVERY_KEY_DUPLICATE",
        "PRE_HITL_KAFKA_EFFECT",
    } <= set(summary.delivery_failed_checks)


def test_round_drift_detected_after_recount(tmp_path, evidence, monkeypatch):
    tmp_path.chmod(0o700)
    artifact = subject.build_round(subject.RoundEvidence.model_validate(evidence))
    ref = write_private(tmp_path, "round1.json", artifact)
    original = subject.resolve_component
    calls = 0

    def resolve(root, reference):
        nonlocal calls
        value = original(root, reference)
        calls += 1
        if calls == 2:
            value["captured_at"] = "2026-09-05T01:01:00Z"
        return value

    monkeypatch.setattr(subject, "resolve_component", resolve)
    with pytest.raises(EvidenceError, match="ROUND_EVIDENCE_DRIFT"):
        subject.verify_round(tmp_path, ref)


def test_round_import_does_not_initialize_live_runtime():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from app.agent import release_round
for name in ('httpx', 'sqlalchemy', 'app.common.config', 'app.common.db'):
    assert name not in sys.modules, name
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
