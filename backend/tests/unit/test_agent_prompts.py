"""`V5-C-2.3` prompt 결정성·label 노출 차단 회귀."""

from __future__ import annotations

import json

import pytest

from app.agent.diagnostics import (
    BoundaryDeviation,
    DiagnosticSourceIds,
    DirectScope,
    IncidentDiagnosticSnapshot,
    WaferParameterObservation,
)
from app.agent.incident import ResolvedIncident
from app.agent.prompts import (
    MAX_DOCUMENT_EXCERPT_CHARS,
    MAX_PROMPT_CHARS,
    MAX_PROMPT_MEMBER_ALARMS,
    MAX_PROMPT_WAFER_OBSERVATIONS,
    TRUNCATION_MARKER,
    HypothesisPromptError,
    build_hypothesis_messages,
    scan_hypothesis_messages,
)
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute
from app.common.enums import AlarmSource, AlarmType
from app.common.schemas import AlarmRef
from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult

ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")


def _route() -> ResolvedIncidentRoute:
    return ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id="LOT-1",
            chamber_id="EQP-1-PM1",
            requested_alarm=ALARM,
            representative_alarm=ALARM,
            member_alarms=(ALARM,),
        ),
        wafer_routes=(),
        graph_evidence=(
            GraphRouteEvidence(
                chamber_id="EQP-1-PM1",
                equipment_id="EQP-1",
                model_code="MODEL-1",
                process_step_id="STEP-1",
                upstream_process_step_ids=(),
                downstream_process_step_ids=(),
                relation_ids=("REL-1",),
                graph_revision="rev-1",
            ),
        ),
        route_consistency=True,
        mismatches=(),
    )


def _docs(*contents: tuple[str, str]) -> DocumentSearchToolResult:
    return DocumentSearchToolResult(
        ok=True,
        hits=[
            DocumentHit(
                chunk_id=chunk_id,
                document_id=f"DOC-{chunk_id}",
                title="Guide",
                score=0.8,
                content=content,
            )
            for chunk_id, content in contents
        ],
    )


def test_document_hits_are_sorted_and_content_is_bounded() -> None:
    messages = build_hypothesis_messages(
        None,
        None,
        _docs(
            ("C-2", "short"),
            ("C-1", "x" * (MAX_DOCUMENT_EXCERPT_CHARS + 20)),
        ),
        _route(),
    )
    payload = json.loads(messages[1]["content"].removeprefix("Evidence JSON:\n"))
    hits = payload["document"]["hits"]
    assert [hit["chunk_id"] for hit in hits] == ["C-1", "C-2"]
    assert hits[0]["content"] == "x" * MAX_DOCUMENT_EXCERPT_CHARS + TRUNCATION_MARKER


def test_large_incident_uses_bounded_citation_candidates_without_losing_counts() -> (
    None
):
    representative = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-00")
    requested = AlarmRef(source=AlarmSource.R03, alarm_id="R03-01")
    members = (
        representative,
        *(
            AlarmRef(source=AlarmSource.TRACE, alarm_id=f"TA-{index:02d}")
            for index in range(1, 31)
        ),
        *(
            AlarmRef(source=AlarmSource.SUMMARY, alarm_id=f"SA-{index:02d}")
            for index in range(1, 18)
        ),
        requested,
    )
    base = _route()
    route = ResolvedIncidentRoute(
        incident=ResolvedIncident(
            lot_id="LOT-1",
            chamber_id="EQP-1-PM1",
            requested_alarm=requested,
            representative_alarm=representative,
            member_alarms=members,
        ),
        wafer_routes=(),
        graph_evidence=base.graph_evidence,
        route_consistency=True,
        mismatches=(),
    )

    messages = build_hypothesis_messages(None, None, _docs(("C-1", "safe")), route)
    payload = json.loads(messages[1]["content"].removeprefix("Evidence JSON:\n"))
    incident = payload["route"]["incident"]
    candidates = {
        f"{alarm['source']}:{alarm['alarm_id']}" for alarm in incident["member_alarms"]
    }

    assert len(incident["member_alarms"]) == MAX_PROMPT_MEMBER_ALARMS
    assert representative.to_token() in candidates
    assert requested.to_token() in candidates
    assert incident["member_alarm_count"] == 49
    assert incident["member_alarm_source_counts"] == {
        "TRACE": 31,
        "SUMMARY": 17,
        "R03": 1,
    }
    assert incident["member_alarms_omitted_count"] == 37

    observations = tuple(
        WaferParameterObservation(
            lot_hist_id=f"LH-{wafer_no}",
            wafer_id=f"W{wafer_no}",
            wafer_no=wafer_no,
            step_id="STEP-1",
            recipe_step_no=1,
            recipe_step_name="ETCH",
            parameter_id=f"P{parameter_no}",
            parameter_name=f"Parameter {parameter_no}",
            alarm_type=AlarmType.OOS if parameter_no == 1 else AlarmType.IN,
            point_count=3,
            ooc_point_count=0,
            oos_point_count=1 if parameter_no == 1 else 0,
            value_mean=float(parameter_no),
            value_min=float(parameter_no - 1),
            value_max=float(parameter_no + 1),
            deviation=(
                BoundaryDeviation(level="OOS", direction="UPPER", magnitude=1.0)
                if parameter_no == 1
                else None
            ),
        )
        for wafer_no in (1, 2, 3)
        for parameter_no in range(1, 9)
    )
    snapshot = IncidentDiagnosticSnapshot(
        lot_id="LOT-1",
        chamber_id="EQP-1-PM1",
        representative_alarm_ref=representative.to_token(),
        member_alarm_count=len(members),
        target_wafer_count=3,
        observed_wafer_count=3,
        wafer_observations=observations,
        parameter_patterns=(),
        step_patterns=(),
        direct_scope=DirectScope(
            lot_ids=("LOT-1",),
            wafer_ids=("W1", "W2", "W3"),
            chamber_ids=("EQP-1-PM1",),
            parameter_ids=tuple(f"P{index}" for index in range(1, 9)),
            model_codes=("MODEL-1",),
        ),
        data_gaps=(),
        source_ids=DiagnosticSourceIds(
            alarm_refs=tuple(alarm.to_token() for alarm in members),
            lot_hist_ids=("LH-1", "LH-2", "LH-3"),
            parameter_ids=tuple(f"P{index}" for index in range(1, 9)),
            relation_ids=("REL-1",),
            graph_revisions=("rev-1",),
        ),
    )

    messages = build_hypothesis_messages(
        None,
        None,
        _docs(("C-1", "safe")),
        route,
        diagnostic_snapshot=snapshot,
    )
    payload = json.loads(messages[1]["content"].removeprefix("Evidence JSON:\n"))
    diagnostic = payload["diagnostic_snapshot"]

    assert len(diagnostic["wafer_observations"]) == MAX_PROMPT_WAFER_OBSERVATIONS
    assert {item["wafer_id"] for item in diagnostic["wafer_observations"]} == {
        "W1",
        "W2",
        "W3",
    }
    assert diagnostic["wafer_observation_count"] == 24
    assert diagnostic["wafer_observations_omitted_count"] == 18
    assert len(diagnostic["source_ids"]["alarm_refs"]) == MAX_PROMPT_MEMBER_ALARMS
    assert diagnostic["source_ids"]["alarm_ref_count"] == 49
    assert diagnostic["source_ids"]["alarm_refs_omitted_count"] == 37


def test_system_prompt_requires_korean_narratives() -> None:
    messages = build_hypothesis_messages(None, None, _docs(), _route())

    assert "모든 설명형 문자열은 한국어로 작성하세요" in messages[0]["content"]
    assert "제공된 근거만 사용해" in messages[0]["content"]


@pytest.mark.parametrize(
    "token",
    ["fault_code", "FAULTCODE", "FAULTS", "is_fault", "fault_of", "faulty_lots", "NRM"],
)
def test_independent_blocked_tokens_are_case_insensitive(token: str) -> None:
    with pytest.raises(HypothesisPromptError):
        scan_hypothesis_messages(
            [{"role": "user", "content": f"x {token.swapcase()} y"}]
        )


@pytest.mark.parametrize(
    "allowed",
    ["predicted_fault_code", "norm", "normalization", "focus", "gas flow"],
)
def test_legitimate_substrings_are_allowed(allowed: str) -> None:
    scan_hypothesis_messages([{"role": "user", "content": allowed}])


def test_prompt_length_boundary_has_dedicated_error_code() -> None:
    scan_hypothesis_messages([{"role": "user", "content": "x" * MAX_PROMPT_CHARS}])
    with pytest.raises(HypothesisPromptError) as exc:
        scan_hypothesis_messages(
            [{"role": "user", "content": "x" * (MAX_PROMPT_CHARS + 1)}]
        )
    assert exc.value.code == "HYPOTHESIS_PROMPT_TOO_LARGE"


def test_blocked_token_keeps_security_error_code() -> None:
    with pytest.raises(HypothesisPromptError) as exc:
        scan_hypothesis_messages([{"role": "user", "content": "contains NRM"}])
    assert exc.value.code == "HYPOTHESIS_PROMPT_BLOCKED"


def test_blocked_token_takes_precedence_over_prompt_size() -> None:
    content = "NRM " + "x" * MAX_PROMPT_CHARS
    with pytest.raises(HypothesisPromptError) as exc:
        scan_hypothesis_messages([{"role": "user", "content": content}])
    assert exc.value.code == "HYPOTHESIS_PROMPT_BLOCKED"


def test_dynamic_document_label_is_blocked_before_llm() -> None:
    with pytest.raises(HypothesisPromptError):
        build_hypothesis_messages(
            None, None, _docs(("C-1", "contains NRM label")), _route()
        )


def test_correction_uses_same_evidence_builder_and_only_sanitized_reason() -> None:
    first = build_hypothesis_messages(None, None, _docs(("C-1", "safe")), _route())
    correction = build_hypothesis_messages(
        None,
        None,
        _docs(("C-1", "safe")),
        _route(),
        correction_reason="STRUCTURE_INVALID",
    )
    assert first[0] == correction[0]
    assert first[1]["content"] in correction[1]["content"]
    assert "STRUCTURE_INVALID" in correction[1]["content"]
