"""Concrete U10 projections from production DTOs; no services or providers."""

import subprocess
import sys
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.state import Hypothesis
from app.agent.u10_evidence import (
    evidence_id,
    project_hypothesis_citations,
    project_initial_evidence,
    project_read_evidence,
    projection_sha256,
    projection_spec,
)
from app.agent.u10_react_execution import execute_react_policy
from app.agent.u10_read_adapter import ReadAdapter
from app.agent.u10_read_execution import ReadRequest, ReadSession
from app.common import tool_contracts as dto
from tests.unit.test_agent_graph import _equipment, _fdc
from tests.unit.test_agent_react import NOW, _level3_route
from tests.unit.test_agent_u10_observations import context, history_result
from tests.unit.test_agent_u10_react_execution import outcome
from tests.unit.test_agent_u10_read_adapter import Immediate, ports
from tests.unit.test_agent_u10_read_execution import inventory


def hypothesis(**changes):
    return Hypothesis.model_validate(
        {
            "predicted_fault_code": "OTH",
            "confidence": 0.5,
            "cause_summary": "테스트 가설",
            "uncertainty": "",
            **changes,
        }
    )


def test_namespace_collision_and_separator_are_preserved():
    projected = project_hypothesis_citations(
        hypothesis(supporting_chunk_ids=["SAME"], supporting_parameter_ids=["SAME"])
    )
    assert projected.values == ["CHUNK:SAME", "PARAMETER:SAME"]
    assert evidence_id("CHUNK", "PARAMETER:SAME") == "CHUNK:PARAMETER:SAME"
    assert projected.sha256 == digest(canonical_json(projected.values))


@pytest.mark.parametrize(
    "namespace,value",
    [
        ("UNKNOWN", "x"),
        ("CHUNK", ""),
        ("CHUNK", " x"),
        ("CHUNK", 1),
        ("CHUNK", "x" * 200),
    ],
)
def test_invalid_evidence_id_is_not_coerced(namespace, value):
    with pytest.raises(EvidenceError, match="U10_EVIDENCE_ID_INVALID"):
        evidence_id(namespace, value)


def test_initial_route_does_not_count_unread_candidate_as_fdc_evidence():
    route = _level3_route()
    result = project_initial_evidence(route)
    expected = {"ALARM:" + a.to_token() for a in route.incident.member_alarms}
    expected |= {"RELATION:" + r for g in route.graph_evidence for r in g.relation_ids}
    assert result.values == sorted(expected)
    assert not any(v.startswith(("LOT_HIST:", "PARAMETER:")) for v in result.values)
    assert context().initial_evidence_ids() == result
    with pytest.raises(EvidenceError, match="U10_SNAPSHOT_SCOPE_INVALID"):
        project_initial_evidence(replace(route, route_consistency=False))


def test_fdc_ids_are_actual_citable_fields_only_and_canonical():
    result = _fdc()
    result.parameters.append(result.parameters[0].model_copy(deep=True))
    projected = project_read_evidence("get_fdc_summary", result)
    assert projected.values == ["LOT_HIST:LH-REP", "PARAMETER:PARAM-1"]
    projected.values.clear()
    assert project_read_evidence("get_fdc_summary", result).values == [
        "LOT_HIST:LH-REP",
        "PARAMETER:PARAM-1",
    ]


def test_documents_use_chunk_not_document_title_content_or_model():
    result = dto.DocumentSearchToolResult(
        ok=True,
        hits=[
            dto.DocumentHit(
                chunk_id="C2",
                document_id="D1",
                title="제목",
                score=0.5,
                content="내용에 PARAM-FAKE가 있어도 근거가 아니다",
                model_code="MODEL-1",
            )
        ],
    )
    assert project_read_evidence("search_documents", result).values == ["CHUNK:C2"]
    assert (
        project_read_evidence(
            "search_documents", dto.DocumentSearchToolResult(ok=True)
        ).values
        == []
    )


@pytest.mark.parametrize(
    "tool,result",
    [
        ("get_equipment_context", _equipment()),
        ("get_chamber_parameter_history", history_result()),
        (
            "get_metrology_result",
            dto.MetrologyResultToolResult(
                ok=True,
                lot_id="LOT001",
                step_id="CT-PHOTO",
                results=[
                    dto.MetrologyResultItem(
                        wafer_id="LOT001W001",
                        measure_type="CD",
                        measured_value=1.0,
                        alarm_result="PASS",
                        measured_at=NOW,
                    )
                ],
                fail_count=0,
                disclaimer="정량 지표만 사용",
            ),
        ),
    ],
)
def test_metadata_and_investigation_success_does_not_invent_citations(tool, result):
    assert project_read_evidence(tool, result).values == []


def test_failed_dto_and_wrong_type_and_bypassed_model_are_closed():
    assert project_read_evidence("get_fdc_summary", _fdc(ok=False)).values == []
    for tool, result in [
        ("send_action", _fdc()),
        ("get_fdc_summary", _equipment()),
        ("get_fdc_summary", _fdc().model_dump()),
    ]:
        with pytest.raises(EvidenceError, match="U10_EVIDENCE_PROJECTION_INVALID"):
            project_read_evidence(tool, result)
    with pytest.raises(ValidationError):
        project_read_evidence(
            "get_fdc_summary", _fdc().model_copy(update={"wafer": None})
        )


def test_citations_include_origin_basis_findings_and_do_not_hide_unsupported():
    value = hypothesis(
        supporting_chunk_ids=["C1"],
        supporting_lot_hist_ids=["LH-REP"],
        origin_assessment={
            "scope": "UNDETERMINED",
            "basis": [{"namespace": "CHUNK", "id": "UNSUPPORTED"}],
            "compared": {},
        },
        parameter_findings=[
            {
                "parameter_id": "PARAM-1",
                "step_no": 1,
                "direction": "ABOVE",
                "excursion_ratio": 1.0,
                "wafer_scope": "SINGLE",
                "lot_hist_ids": ["LH-REP"],
            }
        ],
    )
    projected = project_hypothesis_citations(value)
    assert projected.values == [
        "CHUNK:C1",
        "CHUNK:UNSUPPORTED",
        "LOT_HIST:LH-REP",
        "PARAMETER:PARAM-1",
    ]
    available = project_read_evidence("get_fdc_summary", _fdc())
    assert set(projected.values) - set(available.values) == {
        "CHUNK:C1",
        "CHUNK:UNSUPPORTED",
    }
    with pytest.raises(EvidenceError, match="U10_HYPOTHESIS_PROJECTION_INVALID"):
        project_hypothesis_citations(value.model_dump())


def test_default_adapter_fixed_and_react_use_same_projection_without_oracle():
    fixed_state, react_state = context(), context()
    fixed = ReadAdapter(fixed_state, ports(lambda _: _fdc()), Immediate())
    session = ReadSession(inventory(), fixed)
    session.execute(
        ReadRequest(slot="CURRENT_FDC", arguments={"lot_hist_id": "LH-REP"}),
    )
    choices = iter([outcome("get_fdc_summary", fdc_candidate_id="F1"), outcome("stop")])
    react = execute_react_policy(
        inventory(),
        react_state.build_context,
        lambda _: next(choices),
        ReadAdapter(react_state, ports(lambda _: _fdc()), Immediate()),
        document_model_code="MODEL-1",
        expected_selector_model="fixture-model",
    )
    assert react.calls[0].evidence_ids == session.calls[0].evidence_ids
    cited = project_hypothesis_citations(
        hypothesis(
            supporting_lot_hist_ids=["LH-REP"], supporting_parameter_ids=["PARAM-1"]
        )
    )
    assert cited == react.calls[0].evidence_ids
    assert fixed_state.initial_evidence_ids() == react_state.initial_evidence_ids()
    assert fixed_state.results("get_fdc_summary") == react_state.results(
        "get_fdc_summary"
    )


def test_projection_contract_copy_and_hash():
    before = projection_sha256()
    spec = projection_spec()
    spec["reads"]["get_fdc_summary"].clear()
    assert projection_sha256() == before
    assert digest(canonical_json(spec)) != before


def test_import_does_not_load_runtime_or_providers():
    code = """
import sys
import app.agent.u10_evidence
import app.agent.u10_read_adapter
assert 'app.agent.hypothesis' not in sys.modules
assert 'app.agent.tools' not in sys.modules
assert 'app.common.llm' not in sys.modules
assert 'app.common.config' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
