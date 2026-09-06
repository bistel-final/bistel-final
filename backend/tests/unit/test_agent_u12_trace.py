"""U12 actual executor -> private diagnostic trace, no provider/DB."""

from copy import deepcopy

import pytest

from app.agent import react
from app.agent import u10_selector_trace as trace
from app.agent.release_artifacts import EvidenceError
from tests.unit import test_agent_u10_attempt as attempt_fixture
from tests.unit import test_agent_u10_react_execution as fixture


def test_two_schema_failures_count_two_calls_not_three():
    def invalid(_):
        raise react.ReactSelectionError(
            "REACT_STRUCTURE_INVALID", usage=fixture._usage()
        )

    result = fixture.run(invalid)
    events = trace.project_selector_trace(result)
    assert [s.llm_call for s in events] == [True, True, False]
    assert events[-1].stop_reason == "REACT_STRUCTURE_INVALID"


def test_attempt_writer_emits_trace_for_new_prompt_only():
    config = attempt_fixture.setup()["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    result = attempt_fixture.run(llm=config)
    assert [s.phase for s in result.attempt.selector_trace] == [
        "SELECTED",
        "OBSERVED",
        "STOPPED",
    ]
    assert result.attempt.selector_trace[-1].tool == "stop"
    assert attempt_fixture.run().attempt.selector_trace is None


def test_trace_tampering_is_rejected():
    config = attempt_fixture.setup()["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    attempt = attempt_fixture.run(llm=config).attempt
    for index, update in (
        (1, {"retry": 1}),
        (0, {"llm_call": False}),
        (-1, {"tool": None}),
        (0, {"slot": "EQUIPMENT"}),
    ):
        changed = deepcopy(attempt)
        changed.selector_trace[index] = changed.selector_trace[index].model_copy(
            update=update
        )
        with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
            trace.check_selector_trace(changed)


def test_guard_allowlist_matches_production_without_importing_runtime_in_contract():
    assert trace.GUARD_CODES == react._FEEDBACK_GUARD_CODES


def test_trace_rejects_raw_fields_and_unlisted_guard_codes():
    from pydantic import ValidationError

    config = attempt_fixture.setup()["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    result = attempt_fixture.run(
        llm=config,
        select=lambda ctx, *, seed: fixture.outcome(
            "get_fdc_summary", fdc_candidate_id="F999"
        ),
    )
    attempt = result.attempt
    raw = attempt.selector_trace[0].model_dump()
    with pytest.raises(ValidationError):
        trace.SelectorStep.model_validate({**raw, "rationale": "must not persist"})
    attempt.selector_trace[0] = attempt.selector_trace[0].model_copy(
        update={"guard_code": "UNLISTED_GUARD"}
    )
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        trace.check_selector_trace(attempt)


def test_batch_writer_preserves_new_trace_and_legacy_bytes():
    from app.agent.release_artifacts import canonical_json
    from app.agent.u10_batch import execute_batch
    from app.agent.u10_comparison import Artifact, validate_artifact
    from tests.unit.test_agent_u10_batch import inputs

    params, *_ = inputs()
    legacy = execute_batch(**params)
    raw = legacy.model_dump(mode="json")
    assert all("selector_trace" not in a for a in raw["attempts"])
    assert canonical_json(Artifact.model_validate(raw)) == canonical_json(raw)
    params, *_ = inputs()
    params["llm"] = params["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    new = execute_batch(**params)
    payload = new.model_dump(mode="json")
    assert all("selector_trace" in a for a in payload["attempts"])
    assert all(
        a.selector_trace == [] for a in new.attempts if a.policy == "FIXED_POLICY_V21"
    )
    assert all(a.selector_trace for a in new.attempts if a.policy == "REACT_V2")
    validate_artifact(payload, params["benchmark"].model_dump(mode="json"))
    del payload["attempts"][0]["selector_trace"]
    with pytest.raises(ValueError, match="U10_SCHEMA_INVALID"):
        Artifact.model_validate(payload)


def test_schema_failure_and_measured_dependency_writer_roundtrip():
    from app.agent.u10_comparison import Attempt

    for code in ("REACT_STRUCTURE_INVALID", "LLM_DEPENDENCY"):

        def failure(*_, error=code, **__):
            raise react.ReactSelectionError(error, usage=fixture._usage())

        cfg = attempt_fixture.setup()["llm"].model_copy(
            update={"selector_prompt_version": "agent-react-v2-ko2"}
        )
        result = attempt_fixture.run(llm=cfg, select=failure)
        attempt = Attempt.model_validate(result.attempt.model_dump(mode="json"))
        trace.check_selector_trace(attempt)
        assert sum(s.llm_call for s in attempt.selector_trace) == (
            2 if code == "REACT_STRUCTURE_INVALID" else 1
        )

    def timeout(*_, **__):
        raise react.ReactSelectionError("LLM_TIMEOUT")

    with pytest.raises(EvidenceError, match="METRIC_PRECONDITION_INVALID"):
        attempt_fixture.run(llm=cfg, select=timeout)


def test_retry_joins_observed_reads_not_selector_counts():
    choices = iter(
        [
            fixture.outcome("get_chamber_parameter_history", history_candidate_id="H1"),
            fixture.outcome("stop"),
        ]
    )
    count = 0

    def invoke(*_):
        nonlocal count
        count += 1
        if count == 1:
            raise TimeoutError()
        return fixture.success("HISTORY")

    result = fixture.run(lambda _: next(choices), invoke)
    events = trace.project_selector_trace(result)
    assert [s.retry for s in events if s.phase == "OBSERVED"] == [0, 1]
    assert [s.slot for s in events if s.phase == "OBSERVED"] == ["HISTORY", "HISTORY"]
    assert sum(s.llm_call for s in events) == len(result.selector) == 2


def test_impossible_thirteen_selector_calls_are_rejected():
    from types import SimpleNamespace

    config = attempt_fixture.setup()["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    attempt = attempt_fixture.run(llm=config).attempt
    selected, observed, stopped = attempt.selector_trace
    events, calls = [], []
    for i in range(6):
        events += [
            trace.SelectorStep(
                seq=len(events) + 1,
                phase="REJECTED",
                tool=None,
                slot=None,
                retry=None,
                guard_code="REACT_SCHEMA_INVALID",
                stop_reason=None,
                llm_call=True,
            ),
            selected.model_copy(update={"seq": len(events) + 2}),
            observed.model_copy(update={"seq": len(events) + 3}),
        ]
        calls.append(attempt.calls[0].model_copy(update={"selection": i + 1}))
    events.append(stopped.model_copy(update={"seq": len(events) + 1}))
    invalid = SimpleNamespace(
        policy="REACT_V2",
        selector_trace=events,
        selector_calls=13,
        calls=calls,
        read_stop_reason="LLM_STOP",
    )
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        trace.check_selector_trace(invalid)


@pytest.mark.parametrize("timeout_reads", [{1}, {1, 8}, {7, 8}])
def test_eight_reads_with_one_retry_use_seven_selections_and_system_stop(timeout_reads):
    from types import SimpleNamespace

    choices = iter(
        [fixture.outcome("search_documents", query=f"query {i}") for i in range(3)]
        + [
            fixture.outcome("get_chamber_parameter_history", history_candidate_id="H1"),
            fixture.outcome("get_equipment_context"),
            fixture.outcome("get_metrology_result", metrology_candidate_id="M1"),
            fixture.outcome("get_fdc_summary", fdc_candidate_id="F1"),
        ]
    )
    reads = 0

    def invoke(*_):
        nonlocal reads
        reads += 1
        if reads in timeout_reads:
            raise TimeoutError()
        return fixture.success()

    result = fixture.run(
        lambda _: next(choices),
        invoke,
        build_context=lambda: fixture._context(fetched_fdc_candidate_ids=()),
    )
    events = trace.project_selector_trace(result)
    assert result.stop_reason == "BUDGET_EXHAUSTED"
    assert len(result.calls) == 8 and len(result.selector) == 7
    assert events[-1].tool is None and not events[-1].llm_call
    trace.check_selector_trace(
        SimpleNamespace(
            policy="REACT_V2",
            selector_trace=events,
            selector_calls=7,
            calls=result.calls,
            read_stop_reason=result.stop_reason,
        )
    )


def test_two_guard_rejections_are_not_schema_rejections():
    from types import SimpleNamespace

    result = fixture.run(
        lambda _: fixture.outcome("get_fdc_summary", fdc_candidate_id="F99")
    )
    events = trace.project_selector_trace(result)
    assert [s.phase for s in events] == ["REJECTED", "REJECTED", "STOPPED"]
    trace.check_selector_trace(
        SimpleNamespace(
            policy="REACT_V2",
            selector_trace=events,
            selector_calls=2,
            calls=[],
            read_stop_reason="GUARD_LIMIT",
        )
    )
    changed = events[-1].model_copy(
        update={"stop_reason": "LLM_STOP", "tool": "stop", "llm_call": True}
    )
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        trace.check_selector_trace(
            SimpleNamespace(
                policy="REACT_V2",
                selector_trace=events[:-1] + [changed],
                selector_calls=3,
                calls=[],
                read_stop_reason="LLM_STOP",
            )
        )


def test_schema_retry_cannot_be_relabelled_as_budget_stop_in_artifact():
    from app.agent.u10_batch import execute_batch
    from app.agent.u10_comparison import validate_artifact
    from tests.unit.test_agent_u10_batch import inputs

    params, *_ = inputs()
    params["llm"] = params["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    payload = execute_batch(**params).model_dump(mode="json")
    row = next(a for a in payload["attempts"] if a["policy"] == "REACT_V2")
    assert len(row["calls"]) == 1 and row["selector_calls"] == 2
    row["read_stop_reason"] = "BUDGET_EXHAUSTED"
    row["selector_trace"][-1].update(
        seq=4, tool=None, stop_reason="BUDGET_EXHAUSTED", llm_call=False
    )
    row["selector_trace"].insert(
        -1,
        dict(
            seq=3,
            phase="REJECTED",
            tool=None,
            slot=None,
            retry=None,
            guard_code="REACT_SCHEMA_INVALID",
            stop_reason=None,
            llm_call=True,
        ),
    )
    # No metric/result changes: this used to pass the complete artifact verifier.
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        validate_artifact(payload, params["benchmark"].model_dump(mode="json"))


def test_budget_stop_requires_read_or_same_tool_retry_exhaustion():
    config = attempt_fixture.setup()["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    attempt = attempt_fixture.run(llm=config).attempt
    attempt.selector_trace[-1] = attempt.selector_trace[-1].model_copy(
        update={"tool": None, "stop_reason": "BUDGET_EXHAUSTED", "llm_call": False}
    )
    invalid = attempt.model_copy(
        update={"selector_calls": 1, "read_stop_reason": "BUDGET_EXHAUSTED"}
    )
    # The previous event is OBSERVED, but a single successful read uses neither cap.
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        trace.check_selector_trace(invalid)


@pytest.mark.parametrize("failure", ["TIMEOUT: test", "DEPENDENCY_ERROR: test"])
def test_fourth_same_tool_failed_read_preserves_budget_stop(failure):
    from dataclasses import replace

    from app.common.tool_contracts import DocumentSearchToolResult

    choices = iter(
        [fixture.outcome("get_fdc_summary", fdc_candidate_id="F1")]
        + [
            fixture.outcome("search_documents", query=f"FDC check {i}")
            for i in range(4)
        ]
    )
    document_calls = 0

    def documents(_):
        nonlocal document_calls
        document_calls += 1
        if document_calls == 4:
            return DocumentSearchToolResult(ok=False, reason=failure)
        return attempt_fixture.docs((f"C{document_calls}", 0.5))

    config = attempt_fixture.setup()["llm"].model_copy(
        update={"selector_prompt_version": "agent-react-v2-ko2"}
    )
    result = attempt_fixture.run(
        llm=config,
        select=lambda ctx, *, seed: next(choices),
        read_ports=replace(
            attempt_fixture.ports(lambda _: attempt_fixture._fdc()),
            document_search=documents,
        ),
    )
    assert document_calls == 4 and len(result.attempt.calls) == 5
    assert result.attempt.calls[-1].retry == 0
    assert result.attempt.calls[-1].status in {"ERROR", "TIMEOUT"}
    assert result.attempt.read_stop_reason == "BUDGET_EXHAUSTED"
    assert result.attempt.completion is False
    trace.check_selector_trace(result.attempt)
