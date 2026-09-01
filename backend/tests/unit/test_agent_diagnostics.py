from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agent.diagnostics import (
    CANONICAL_INCIDENT_KEYS,
    EvidenceAssessmentBlock,
    PostActionObservationBlock,
    abnormal_parameter_ids,
    assess_evidence,
    build_diagnostic_snapshot,
    parameter_jaccard,
    similar_incident_score,
)
from app.agent.incident import ResolvedIncident
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute, WaferRoute
from app.agent.routing_repository import RouteStep
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    DocumentHit,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)

ROOT = Path(__file__).resolve().parents[3]


def _route() -> ResolvedIncidentRoute:
    alarm = AlarmRef(source="R03", alarm_id="R03-00000000000000000001")
    wafer_routes = tuple(
        WaferRoute(
            wafer_id=f"W{wafer_no}",
            member_alarms=(alarm,),
            steps=(
                RouteStep(
                    lot_hist_id=f"LH-{wafer_no}",
                    lot_id="LOT004",
                    wafer_id=f"W{wafer_no}",
                    wafer_no=wafer_no,
                    step_id="CT-ETCH",
                    area_id="ETCH",
                    equipment_id="EQP04",
                    chamber_id="EQP04-PM2",
                    recipe_id="RECIPE-1",
                    track_in_at=datetime(2026, 6, 1, tzinfo=UTC),
                    track_out_at=None,
                ),
            ),
        )
        for wafer_no in (2, 4, 6)
    )
    return ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id="LOT004",
            chamber_id="EQP04-PM2",
            requested_alarm=alarm,
            representative_alarm=alarm,
            member_alarms=(alarm,),
        ),
        wafer_routes=wafer_routes,
        graph_evidence=(
            GraphRouteEvidence(
                chamber_id="EQP04-PM2",
                equipment_id="EQP04",
                model_code="ET-7500",
                process_step_id="CT-ETCH",
                upstream_process_step_ids=("CT-PHOTO",),
                downstream_process_step_ids=(),
                relation_ids=("REL-1",),
                graph_revision="rev-1",
            ),
        ),
        route_consistency=True,
        mismatches=(),
    )


def _fdc(wafer_no: int, *, oos: int, value_max: float) -> FdcSummaryToolResult:
    return FdcSummaryToolResult(
        ok=True,
        wafer=WaferContext(
            lot_hist_id=f"LH-{wafer_no}",
            lot_id="LOT004",
            wafer_no=wafer_no,
            chamber_id="EQP04-PM2",
            equipment_id="EQP04",
            step_id="CT-ETCH",
        ),
        parameters=[
            ParameterSummaryItem(
                parameter_id="ET_REFL",
                parameter_name="Reflected power",
                recipe_step_no=2,
                recipe_step_name="ETCH",
                value_mean=value_max - 1,
                value_min=value_max - 2,
                value_max=value_max,
                point_cnt=3,
                ooc_point_cnt=0,
                oos_point_cnt=oos,
                spec_upper=10,
                alarm_type="OOS" if oos else "IN",
            )
        ],
    )


def test_snapshot_preserves_three_wafers_and_deterministic_patterns() -> None:
    snapshot = build_diagnostic_snapshot(
        (
            _fdc(6, oos=1, value_max=13),
            _fdc(2, oos=1, value_max=11),
            _fdc(4, oos=1, value_max=12),
        ),
        _route(),
        target_count=3,
    )

    assert snapshot.observed_wafer_count == snapshot.target_wafer_count == 3
    assert snapshot.source_ids.lot_hist_ids == ("LH-2", "LH-4", "LH-6")
    assert snapshot.parameter_patterns[0].affected_wafer_count == 3
    assert snapshot.parameter_patterns[0].maximum_deviation == 3
    assert snapshot.step_patterns[0].affected_wafer_count == 3
    assert abnormal_parameter_ids(snapshot) == {"ET_REFL"}


def test_partial_and_sufficient_are_not_collapsed() -> None:
    snapshot = build_diagnostic_snapshot((_fdc(2, oos=1, value_max=11),), _route())
    graph = EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP04-PM2",
        equipment_id="EQP04",
        area="ETCH",
        model_code="ET-7500",
        process_step_id="CT-ETCH",
        graph_revision="rev-1",
    )
    documents = DocumentSearchToolResult(
        ok=True,
        hits=[
            DocumentHit(
                chunk_id="CHUNK-1",
                document_id="DOC-1",
                title="Guide",
                score=0.8,
                content="safe",
            )
        ],
    )
    sufficient = assess_evidence(snapshot, _route(), graph, documents)
    partial = assess_evidence(snapshot, _route(), graph, None)
    bounded_snapshot = build_diagnostic_snapshot(
        (_fdc(2, oos=1, value_max=11),),
        _route(),
        extra_data_gaps=("FDC_TARGET_BUDGET_EXCEEDED",),
    )
    bounded = assess_evidence(bounded_snapshot, _route(), graph, documents)

    assert sufficient.status == "SUFFICIENT"
    assert partial.status == "PARTIAL"
    assert "RAG" in partial.missing_sources
    assert bounded.status == "PARTIAL"
    assert "FDC_TARGET_BUDGET_EXCEEDED" in bounded.reason_codes
    assert EvidenceAssessmentBlock.model_validate(partial) == partial


def test_snapshot_never_synthesizes_a_wafer_id_outside_the_route() -> None:
    with pytest.raises(ValueError, match="DIAGNOSTIC_WAFER_ROUTE_MISMATCH"):
        build_diagnostic_snapshot((_fdc(3, oos=1, value_max=11),), _route())


def test_retryable_final_failure_marks_the_shared_retry_as_exhausted() -> None:
    failed = FdcSummaryToolResult(ok=False, reason="TIMEOUT: fixture")
    snapshot = build_diagnostic_snapshot((failed,), _route(), target_count=1)

    assert snapshot.data_gaps == (
        "TIMEOUT",
        "RETRY_BUDGET_EXHAUSTED",
        "FDC_COVERAGE_PARTIAL",
    )


def test_graph_and_rag_retryable_failures_exhaust_the_shared_retry_reason() -> None:
    snapshot = build_diagnostic_snapshot(
        (_fdc(2, oos=1, value_max=11),),
        _route(),
        target_count=1,
    )
    graph_ok = EquipmentContextToolResult(
        ok=True,
        chamber_id="EQP04-PM2",
        equipment_id="EQP04",
        area="ETCH",
        model_code="ET-7500",
        process_step_id="CT-ETCH",
        graph_revision="rev-1",
    )
    documents_ok = DocumentSearchToolResult(
        ok=True,
        hits=[
            DocumentHit(
                chunk_id="CHUNK-1",
                document_id="DOC-1",
                title="Guide",
                score=0.8,
                content="safe",
            )
        ],
    )
    cases = (
        (
            EquipmentContextToolResult(ok=False, reason="TIMEOUT: fixture"),
            documents_ok,
            "GRAPH",
            "GRAPH_TIMEOUT",
        ),
        (
            graph_ok,
            DocumentSearchToolResult(
                ok=False,
                reason="DEPENDENCY_ERROR: fixture",
            ),
            "RAG",
            "RAG_DEPENDENCY_ERROR",
        ),
    )

    for graph, documents, missing_source, source_reason in cases:
        assessment = assess_evidence(snapshot, _route(), graph, documents)

        assert assessment.status == "PARTIAL"
        assert missing_source in assessment.missing_sources
        assert source_reason in assessment.reason_codes
        assert "RETRY_BUDGET_EXHAUSTED" in assessment.reason_codes


def test_similarity_formula_and_static_post_action_contract() -> None:
    assert parameter_jaccard(frozenset(), frozenset()) == 0.0
    assert parameter_jaccard(frozenset({"A", "B"}), frozenset({"B", "C"})) == 0.33
    assert (
        similar_incident_score(
            same_model=True,
            parameter_similarity=0.5,
            same_fault=True,
            same_action=True,
        )
        == 85
    )
    assert PostActionObservationBlock().model_dump() == {
        "status": "NOT_AVAILABLE_STATIC_DATASET",
        "message": (
            "최종 정적 데이터셋에는 조치 이후 공정 관측값이 없어 "
            "효과를 평가할 수 없음"
        ),
    }


def test_final_diagnostic_fixture_matches_golden_incident_keys() -> None:
    contract = json.loads(
        (
            ROOT / "backend/tests/fixtures/v5_c_5_2_1/final_diagnostic_contract.json"
        ).read_text()
    )
    golden = json.loads(
        (ROOT / "backend/tests/fixtures/v5_c_6_1/golden_incidents.json").read_text()
    )
    keys = {(item["lot_id"], item["chamber_id"]) for item in contract["incidents"]}
    golden_keys = {(item["lot_id"], item["chamber_id"]) for item in golden["incidents"]}

    assert contract["dataset_epoch"] == golden["dataset_epoch"]
    assert contract["canonical_incident_count"] == len(keys) == 12
    assert (
        contract["strict_r03_count"]
        == sum(item["strict_r03"] for item in contract["incidents"])
        == 3
    )
    assert contract["r03_member_wafer_count"] == 3
    assert contract["r03_member_alarm_count"] == 9
    assert contract["max_fdc_targets_per_run"] == 3
    assert (
        contract["max_read_calls_per_run"] + contract["reserved_send_calls_per_run"]
        == 8
    )
    assert keys == golden_keys
    assert keys == CANONICAL_INCIDENT_KEYS
