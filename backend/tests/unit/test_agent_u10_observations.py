"""Observation context uses actual production DTOs, never external services."""

import subprocess
import sys
from dataclasses import replace

import pytest

from app.agent import react
from app.agent.release_artifacts import EvidenceError
from app.agent.u10_observations import ObservationContext
from app.agent.u10_react_execution import execute_react_policy
from app.common import tool_contracts as dto
from tests.unit.test_agent_graph import _equipment, _fdc
from tests.unit.test_agent_react import NOW, _level3_route
from tests.unit.test_agent_u10_react_execution import outcome
from tests.unit.test_agent_u10_read_execution import inventory, success


def context():
    return ObservationContext(
        "RUN-1", _level3_route(), ["LH-REP"], document_model_code="MODEL-1"
    )


def history_call(state):
    ctx = state.build_context()
    chosen = outcome(
        "get_chamber_parameter_history",
        history_candidate_id=ctx.candidates.history[0].candidate_id,
    )
    resolved = react.resolve_call(chosen.selection, ctx)
    return resolved["request"], resolved["internal_context"]


def history_result():
    return dto.ChamberParameterHistoryToolResult(
        ok=True,
        scope="CURRENT",
        chamber_id="EQP01-PM1",
        parameter_id="PARAM-1",
        step_no=1,
        current=dto.LotAggregate(
            lot_id="LOT001",
            lot_mean=1.0,
            wafer_count=1,
            ooc_wafers=0,
            oos_wafers=1,
            evaluation_missing=0,
            track_in_from=NOW,
            track_in_to=NOW,
        ),
        baseline=dto.HistoryBaseline(mean_hist=None, sd_hist=None, prior_lot_count=0),
        trend="INSUFFICIENT",
        comparison="CURRENT",
        sample_count=1,
    )


def test_fdc_success_builds_observations_and_current_history_candidates():
    state = context()
    assert state.build_context().candidates.history == ()
    assert state.build_context().fdc_observations == ()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    ctx = state.build_context()
    assert ctx.fetched_fdc_candidate_ids == ("F1",)
    assert ctx.observed_parameter_keys == (("PARAM-1", 1),)
    assert len(ctx.fdc_observations) == len(ctx.candidates.history) == 1
    assert ctx.candidates.history[0].scope == "CURRENT"


def test_equipment_success_adds_sibling_only_after_observed_parameter():
    route = _level3_route()
    route = replace(
        route,
        graph_evidence=(
            replace(route.graph_evidence[0], sibling_chamber_ids=("EQP01-PM2",)),
        ),
    )
    state = ObservationContext(
        "RUN-1", route, ["LH-REP"], document_model_code="MODEL-1"
    )
    equipment = _equipment().model_copy(update={"sibling_chamber_ids": ["EQP01-PM2"]})
    state.record("get_equipment_context", {"chamber_id": "EQP01-PM1"}, equipment)
    assert state.build_context().candidates.history == ()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    ctx = state.build_context()
    assert [c.scope for c in ctx.candidates.history] == ["CURRENT", "SIBLING"]
    assert ctx.sibling_chamber_ids == ("EQP01-PM2",)


def test_failed_and_duplicate_results_do_not_inflate_observations():
    state = context()
    request = {"lot_hist_id": "LH-REP"}
    state.record("get_fdc_summary", request, _fdc(ok=False))
    assert state.results("get_fdc_summary") == []
    state.record("get_fdc_summary", request, _fdc())
    state.record("get_fdc_summary", request, _fdc())
    assert len(state.results("get_fdc_summary")) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("lot_id", "OTHER"),
        ("lot_hist_id", "OTHER"),
        ("chamber_id", "OTHER"),
        ("step_id", "OTHER"),
    ],
)
def test_wrong_fdc_identity_cannot_enter_context(field, value):
    state = context()
    result = _fdc()
    setattr(result.wafer, field, value)
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_SCOPE_INVALID$"):
        state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, result)
    assert state.build_context().fdc_observations == ()


@pytest.mark.parametrize(
    "field,value",
    [
        ("graph_revision", "other"),
        ("sibling_chamber_ids", ["OTHER"]),
        ("model_code", "OTHER"),
    ],
)
def test_equipment_must_match_pinned_route(field, value):
    state = context()
    result = _equipment().model_copy(update={field: value})
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_SCOPE_INVALID$"):
        state.record("get_equipment_context", {"chamber_id": "EQP01-PM1"}, result)
    assert state.build_context().equipment_observation is None


@pytest.mark.parametrize(
    "tool,arguments,internal",
    [
        ("get_fdc_summary", {"lot_hist_id": "OUTSIDE"}, None),
        ("get_equipment_context", {"chamber_id": "OUTSIDE"}, None),
        ("search_documents", {"query": "valid", "model_code": "OTHER"}, None),
        ("search_documents", {"query": "{bad}"}, None),
        ("search_documents", {"query": "valid", "top_k": 10}, None),
        ("get_metrology_result", {"lot_id": "OTHER", "step_id": "CT-PHOTO"}, None),
        ("send_action", {"action_id": "A"}, None),
        ("get_fdc_summary", {"lot_hist_id": "LH-REP"}, {"scope": "CURRENT"}),
    ],
)
def test_unauthorized_input_is_rejected_before_read(tool, arguments, internal):
    with pytest.raises(EvidenceError, match="^U10_READ_SCOPE_INVALID$"):
        context().authorize(tool, arguments, internal)


def test_history_requires_observation_and_exact_internal_context():
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    request, internal = history_call(state)
    state.authorize("get_chamber_parameter_history", request, internal)
    for changed_request, changed_internal in [
        (request, {**internal, "current_lot_id": "OTHER"}),
        (request, {**internal, "scope": "SIBLING"}),
        ({**request, "n_lots": 2}, internal),
        ({**request, "step_no": True}, internal),
        ({**request, "before": "2099-01-01T00:00:00+00:00"}, internal),
    ]:
        with pytest.raises(EvidenceError, match="^U10_READ_SCOPE_INVALID$"):
            state.authorize(
                "get_chamber_parameter_history", changed_request, changed_internal
            )
    with pytest.raises(EvidenceError, match="^U10_READ_SCOPE_INVALID$"):
        context().authorize("get_chamber_parameter_history", request, internal)
    state.record("get_chamber_parameter_history", request, history_result(), internal)
    assert "INSUFFICIENT" in state.build_context().history_observations[0]


def test_wrong_history_and_metrology_result_scopes_are_rejected():
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    request, internal = history_call(state)
    result = history_result()
    result.current.lot_id = "OTHER"
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_SCOPE_INVALID$"):
        state.record("get_chamber_parameter_history", request, result, internal)
    metrology = dto.MetrologyResultToolResult(
        ok=True,
        lot_id="OTHER",
        step_id="CT-PHOTO",
        results=[
            dto.MetrologyResultItem(
                wafer_id="W1",
                measure_type="CD",
                measured_value=1.0,
                alarm_result="PASS",
                measured_at=NOW,
            )
        ],
        fail_count=0,
        disclaimer="not fault label",
    )
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_SCOPE_INVALID$"):
        state.record(
            "get_metrology_result",
            {"lot_id": "LOT001", "step_id": "CT-PHOTO"},
            metrology,
        )


def test_copy_isolation_preserves_context_and_candidate_identity():
    state = context()
    original = _fdc()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, original)
    original.parameters[0].parameter_id = "changed"
    state.results("get_fdc_summary")[0].parameters.clear()
    built = state.build_context()
    built.candidates.history[0].chamber_id = "changed"
    assert state.build_context().observed_parameter_keys == (("PARAM-1", 1),)
    assert state.build_context().candidates.history[0].chamber_id == "EQP01-PM1"


def test_document_and_metrology_successes_build_real_observation_summaries():
    state = context()
    document = dto.DocumentSearchToolResult(
        hits=[
            dto.DocumentHit(
                chunk_id="CHUNK-1",
                document_id="DOC-1",
                title="FDC",
                section="test",
                score=0.9,
                content="Read evidence",
                model_code="MODEL-1",
            )
        ],
        ok=True,
    )
    state.record("search_documents", {"query": "FDC"}, document)
    metrology = dto.MetrologyResultToolResult(
        ok=True,
        lot_id="LOT001",
        step_id="CT-PHOTO",
        results=[
            dto.MetrologyResultItem(
                wafer_id="W1",
                measure_type="CD",
                measured_value=1.0,
                alarm_result="FAIL",
                measured_at=NOW,
            )
        ],
        fail_count=1,
        disclaimer="not fault label",
    )
    state.record(
        "get_metrology_result", {"lot_id": "LOT001", "step_id": "CT-PHOTO"}, metrology
    )
    built = state.build_context()
    assert len(built.document_observations) == len(built.metrology_observations) == 1
    assert "fail_count=1" in built.metrology_observations[0]
    wrong = document.model_copy(deep=True)
    wrong.hits[0].model_code = "OTHER"
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_SCOPE_INVALID$"):
        state.record("search_documents", {"query": "FDC"}, wrong)
    assert len(state.results("search_documents")) == 1


def test_real_context_builder_connects_fdc_observation_to_next_selector():
    state = context()
    count = 0

    def select(ctx):
        nonlocal count
        count += 1
        if count == 1:
            assert ctx.fdc_observations == ()
            return outcome("get_fdc_summary", fdc_candidate_id="F1")
        assert ctx.observed_parameter_keys == (("PARAM-1", 1),)
        assert ctx.candidates.history[0].parameter_id == "PARAM-1"
        return outcome("stop")

    def invoke(tool, request, internal):
        state.authorize(tool, request, internal)
        state.record(tool, request, _fdc(), internal)
        return success("LH-REP")

    result = execute_react_policy(
        inventory(),
        state.build_context,
        select,
        invoke,
        document_model_code="MODEL-1",
        expected_selector_model="fixture-model",
    )
    assert result.stop_reason == "LLM_STOP" and len(result.calls) == 1


def test_invalid_snapshot_scope_and_dto_type_are_rejected():
    with pytest.raises(EvidenceError, match="^U10_SNAPSHOT_SCOPE_INVALID$"):
        ObservationContext(
            "RUN-1", _level3_route(), ["UNKNOWN"], document_model_code="MODEL-1"
        )
    with pytest.raises(EvidenceError, match="^U10_OBSERVATION_TYPE_INVALID$"):
        context().record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _equipment())


def test_inconsistent_route_is_rejected_before_context_creation():
    route = replace(_level3_route(), route_consistency=False)
    with pytest.raises(EvidenceError, match="^U10_SNAPSHOT_SCOPE_INVALID$"):
        ObservationContext("RUN-1", route, ["LH-REP"], document_model_code="MODEL-1")


def test_module_import_does_not_open_runtime_or_provider():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from app.agent import u10_observations; "
            "assert not {'app.common.config', 'app.agent.react', "
            "'app.common.llm'} & set(sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
