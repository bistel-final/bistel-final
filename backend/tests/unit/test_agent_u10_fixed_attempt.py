"""Fixed-policy end-to-end with real DTOs and local read/generator ports."""

from copy import deepcopy
from dataclasses import replace

import pytest

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_attempt import execute_fixed_attempt
from app.agent.u10_comparison import Adjacent, Dimensions, Fixture
from app.agent.u10_observations import ObservationContext
from app.agent.u10_read_adapter import ReadPorts
from app.agent.u10_read_execution import DocumentContext, fixed_policy_document_query
from tests.unit.test_agent_graph import _equipment, _fdc
from tests.unit.test_agent_react import _level3_route
from tests.unit.test_agent_u10_attempt import run as react_run
from tests.unit.test_agent_u10_attempt import setup
from tests.unit.test_agent_u10_hypothesis import docs, generated
from tests.unit.test_agent_u10_observations import context, history_call, history_result


def parameters(*, sibling=False):
    route = _level3_route()
    if sibling:
        route = replace(
            route,
            graph_evidence=(
                replace(route.graph_evidence[0], sibling_chamber_ids=("EQP01-PM2",)),
            ),
        )
    state = ObservationContext(
        "RUN-1", route, ["LH-REP"], document_model_code="MODEL-1"
    )
    params = setup(state)
    inv = params["fixture"].candidate_inventory
    if sibling:
        inv = inv.model_copy(update={"sibling_chamber_id": "EQP01-PM2"})
        params["fixture"] = params["fixture"].model_copy(
            update={
                "candidate_inventory": inv,
                "expected_compared": Dimensions.model_validate(inv.dimensions()),
            }
        )
        # Revalidate at the public execution boundary, including nested DTOs.
        params["fixture"] = Fixture.model_validate(params["fixture"].model_dump())
    observed = ObservationContext(
        "RUN-1", route, ["LH-REP"], document_model_code="MODEL-1"
    )
    eq = _equipment().model_copy(
        update={"sibling_chamber_ids": ["EQP01-PM2"] if sibling else []}
    )
    observed.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    observed.record("get_equipment_context", {"chamber_id": "EQP01-PM1"}, eq)
    current_request, internal = history_call(observed)
    bound = {
        "CURRENT_FDC": {"lot_hist_id": "LH-REP"},
        "EQUIPMENT": {"chamber_id": "EQP01-PM1"},
        "HISTORY": current_request,
    }
    if sibling:
        bound["SIBLING"] = {**current_request, "chamber_id": "EQP01-PM2"}
    events = []

    def read_fdc(payload):
        events.append(("fdc", deepcopy(payload)))
        return _fdc()

    def read_eq(payload):
        events.append(("equipment", deepcopy(payload)))
        return eq

    def read_history(payload):
        events.append(("history", deepcopy(payload)))
        kind = "SIBLING" if payload["chamber_id"] == "EQP01-PM2" else "CURRENT"
        assert payload["_context"] == {**internal, "scope": kind}
        return history_result().model_copy(
            update={
                "scope": kind,
                "comparison": kind,
                "chamber_id": payload["chamber_id"],
            }
        )

    def document(payload):
        events.append(("document", deepcopy(payload)))
        return docs(("C1", 0.5))

    params.update(
        bound_inputs=bound,
        document_context=DocumentContext(
            model_code="MODEL-1", parameter_ids=["PARAM-1"]
        ),
        read_ports=ReadPorts(
            read_fdc,
            read_eq,
            document,
            read_history,
            lambda _: pytest.fail("metrology"),
        ),
    )
    return params, events


def test_fixed_full_path_queries_seed_metrics_and_action():
    params, events = parameters()
    original = deepcopy(params["bound_inputs"])

    def generate(**inputs):
        assert inputs["seed"] == 13 and "oracle" not in inputs
        return generated(**inputs)

    params["generate"] = generate
    result = execute_fixed_attempt(**params)
    a = result.attempt
    assert [e[0] for e in events] == [
        "fdc",
        "equipment",
        "history",
        "document",
        "document",
    ]
    assert [c.slot for c in a.calls] == [
        "CURRENT_FDC",
        "EQUIPMENT",
        "HISTORY",
        "DOCUMENT_1",
        "DOCUMENT_2",
    ]
    assert [s.slot for s in a.skipped_slots] == ["ADJACENT_FDC", "SIBLING", "METROLOGY"]
    assert a.completion and a.action == "WARNING"
    assert (
        a.selector == []
        and a.selector_calls == a.selector_tokens.total() == a.selector_latency_ms == 0
    )
    assert (
        a.read_attempts == a.successful_reads == 5 and a.hypothesis_tokens.total() == 14
    )
    assert a.compared.history == "CHECKED"
    assert [e[1]["query"] for e in events[-2:]] == [
        fixed_policy_document_query(params["document_context"], slot)
        for slot in ("DOCUMENT_1", "DOCUMENT_2")
    ]
    assert all(e[1]["model_code"] == "MODEL-1" for e in events[-2:])
    assert params["bound_inputs"] == original and "_context" not in original["HISTORY"]
    result.calls[0].evidence_ids.values.clear()
    assert a.calls[0].evidence_ids.values


@pytest.mark.parametrize("attempt_no,order", [(1, 1), (2, 4)])
def test_fixed_interleave_order(attempt_no, order):
    params, _ = parameters()
    params["attempt_no"] = attempt_no
    assert execute_fixed_attempt(**params).attempt.execution_order == order


def test_fixed_and_react_share_config_initial_action_and_hypothesis_boundary():
    params, _ = parameters()
    fixed = execute_fixed_attempt(**params).attempt
    react = react_run().attempt
    assert fixed.action == react.action and fixed.completion == react.completion
    assert fixed.llm_config_sha256 == react.llm_config_sha256
    assert fixed.initial_evidence_ids == react.initial_evidence_ids
    assert fixed.hypothesis_tokens == react.hypothesis_tokens
    assert fixed.selector_calls == 0 < react.selector_calls


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "FIXED_INPUT_SLOTS_MISMATCH"),
        ("extra", "FIXED_INPUT_SLOTS_MISMATCH"),
        ("fdc", "FIXED_INPUT_SCOPE_INVALID"),
        ("history", "FIXED_INPUT_SCOPE_INVALID"),
        ("model", "U10_DOCUMENT_MODEL_REQUIRED"),
    ],
)
def test_invalid_fixed_binding_fails_before_any_read(change, code):
    params, events = parameters()
    if change == "missing":
        del params["bound_inputs"]["HISTORY"]
    elif change == "extra":
        params["bound_inputs"]["DOCUMENT_1"] = {"query": "oracle"}
    elif change == "fdc":
        params["bound_inputs"]["CURRENT_FDC"] = {"lot_hist_id": "OUTSIDE"}
    elif change == "history":
        params["bound_inputs"]["HISTORY"]["chamber_id"] = "EQP01-PM2"
    else:
        params["document_context"] = DocumentContext(
            model_code="OTHER", parameter_ids=[]
        )
    with pytest.raises(EvidenceError, match=code):
        execute_fixed_attempt(**params)
    assert events == []


def test_missing_observed_fdc_blocks_history_port_and_counts_failed_attempts():
    params, events = parameters()
    params["read_ports"] = replace(
        params["read_ports"], fdc_summary=lambda _: _fdc(ok=False)
    )
    params["generate"] = lambda **_: pytest.fail("hypothesis")
    result = execute_fixed_attempt(**params)
    assert not result.attempt.completion and result.attempt.action is None
    assert result.attempt.read_attempts == 7
    assert [c.status for c in result.calls if c.slot == "HISTORY"] == ["ERROR", "ERROR"]
    assert not any(e[0] == "history" for e in events)


@pytest.mark.parametrize(
    "bad",
    [{"step_no": True}, {"parameter_id": "UNSEEN"}, {"_context": {"scope": "SIBLING"}}],
)
def test_history_internal_scope_cannot_be_forged(bad):
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    request, _ = history_call(state)
    with pytest.raises(EvidenceError, match="U10_READ_SCOPE_INVALID"):
        state.resolve_history_context({**request, **bad})


def test_history_and_sibling_slots_cannot_be_swapped():
    params, events = parameters(sibling=True)
    bound = params["bound_inputs"]
    bound["HISTORY"], bound["SIBLING"] = bound["SIBLING"], bound["HISTORY"]
    with pytest.raises(EvidenceError, match="FIXED_INPUT_SCOPE_INVALID"):
        execute_fixed_attempt(**params)
    assert events == []


def test_sibling_receives_resolved_scope_and_is_checked():
    params, events = parameters(sibling=True)
    result = execute_fixed_attempt(**params)
    assert result.attempt.completion
    assert (
        result.attempt.compared.history == result.attempt.compared.sibling == "CHECKED"
    )
    histories = [p for name, p in events if name == "history"]
    assert [p["_context"]["scope"] for p in histories] == ["CURRENT", "SIBLING"]
    assert [p["chamber_id"] for p in histories] == ["EQP01-PM1", "EQP01-PM2"]


@pytest.mark.parametrize("chamber", ["EQP01-PM1", "UNKNOWN"])
def test_sibling_cannot_be_current_or_outside_pinned_graph(chamber):
    params, events = parameters()
    inventory = params["fixture"].candidate_inventory.model_copy(
        update={"sibling_chamber_id": chamber}
    )
    params["fixture"] = params["fixture"].model_copy(
        update={
            "candidate_inventory": inventory,
            "expected_compared": Dimensions.model_validate(inventory.dimensions()),
        }
    )
    params["bound_inputs"]["SIBLING"] = {
        **params["bound_inputs"]["HISTORY"],
        "chamber_id": chamber,
    }
    with pytest.raises(EvidenceError, match="FIXED_INPUT_SCOPE_INVALID"):
        execute_fixed_attempt(**params)
    assert events == []


def test_current_chamber_rejected_even_if_listed_as_graph_sibling():
    params, events = parameters()
    route = _level3_route()
    chamber = route.incident.chamber_id
    # Deliberately malformed snapshot: satisfy the separate graph-membership
    # guard so only the explicit current != sibling check can reject it.
    route = replace(
        route,
        graph_evidence=(
            replace(route.graph_evidence[0], sibling_chamber_ids=(chamber,)),
        ),
    )
    assert chamber in route.graph_evidence[0].sibling_chamber_ids
    params["context"] = ObservationContext(
        "RUN-1", route, ["LH-REP"], document_model_code="MODEL-1"
    )
    inventory = params["fixture"].candidate_inventory.model_copy(
        update={"sibling_chamber_id": chamber}
    )
    params["fixture"] = params["fixture"].model_copy(
        update={
            "candidate_inventory": inventory,
            "expected_compared": Dimensions.model_validate(inventory.dimensions()),
        }
    )
    params["bound_inputs"]["SIBLING"] = deepcopy(params["bound_inputs"]["HISTORY"])
    with pytest.raises(EvidenceError, match="^FIXED_INPUT_SCOPE_INVALID$"):
        execute_fixed_attempt(**params)
    assert events == []


@pytest.mark.parametrize("relation", ["UPSTREAM", "DOWNSTREAM"])
def test_current_and_adjacent_fdc_cannot_be_swapped(relation):
    params, events = parameters()
    route = _level3_route()
    wafer = route.wafer_routes[0]
    current = wafer.steps[0]
    adjacent = replace(
        current,
        lot_hist_id="LH-ADJ",
        chamber_id="EQP02-PM1",
        equipment_id="EQP02",
        step_id="STEP-ADJ",
    )
    steps = (adjacent, current) if relation == "UPSTREAM" else (current, adjacent)
    route = replace(route, wafer_routes=(replace(wafer, steps=steps),))
    state = ObservationContext(
        "RUN-1", route, ["LH-REP"], document_model_code="MODEL-1"
    )
    assert {
        c.lot_hist_id: c.relation for c in state.build_context().candidates.fdc
    } == {
        "LH-REP": "CURRENT",
        "LH-ADJ": relation,
    }
    inventory = params["fixture"].candidate_inventory.model_copy(
        update={
            "adjacent": Adjacent(relation=relation, wafers=1),
        }
    )
    params["fixture"] = params["fixture"].model_copy(
        update={
            "candidate_inventory": inventory,
            "expected_compared": Dimensions.model_validate(inventory.dimensions()),
        }
    )
    params["context"] = state
    bound = params["bound_inputs"]
    bound["ADJACENT_FDC"] = {"lot_hist_id": "LH-ADJ"}
    state.validate_fixed_inputs(inventory, bound)  # Positive control: both exist.
    bound["CURRENT_FDC"], bound["ADJACENT_FDC"] = (
        bound["ADJACENT_FDC"],
        bound["CURRENT_FDC"],
    )
    with pytest.raises(EvidenceError, match="^FIXED_INPUT_SCOPE_INVALID$"):
        execute_fixed_attempt(**params)
    assert events == []


def test_common_retries_exhaust_eight_reads_without_forcing_second_document():
    from app.common import tool_contracts as dto

    params, _ = parameters()
    original = params["read_ports"]

    def retry(fn, cls):
        count = 0

        def invoke(payload):
            nonlocal count
            count += 1
            return cls(ok=False, reason="TIMEOUT:test") if count == 1 else fn(payload)

        return invoke

    params["read_ports"] = replace(
        original,
        fdc_summary=retry(original.fdc_summary, dto.FdcSummaryToolResult),
        equipment_context=retry(
            original.equipment_context, dto.EquipmentContextToolResult
        ),
        chamber_parameter_history=retry(
            original.chamber_parameter_history, dto.ChamberParameterHistoryToolResult
        ),
        document_search=retry(original.document_search, dto.DocumentSearchToolResult),
    )
    result = execute_fixed_attempt(**params)
    assert result.attempt.completion and result.attempt.read_attempts == 8
    assert result.attempt.successful_reads == 4
    assert [c.retry for c in result.calls] == [0, 1] * 4
    assert result.calls[-1].slot == "DOCUMENT_1"
    assert not any(c.slot == "DOCUMENT_2" for c in result.calls)
