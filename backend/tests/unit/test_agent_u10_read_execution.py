"""U10 execution core with deterministic in-process adapters, never live LLMs."""

import subprocess
import sys
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent import u10_comparison as comparison
from app.agent import u10_read_execution as execution
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from tests.unit.test_agent_u10_comparison import (
    artifact_payload,
    ids,
    recompute,
    sync_attempt,
)


def inventory():
    return comparison.Inventory(
        current_wafers=1,
        adjacent=comparison.Adjacent(relation="UPSTREAM", wafers=1),
        sibling_chamber_id="C2",
        history_prior_lots=3,
        metrology_samples=2,
        documents=True,
    )


def inputs(inv):
    return {
        slot: {"target": slot}
        for slot, available in inv.available_slots().items()
        if available and not slot.startswith("DOCUMENT_")
    }


def context():
    return execution.DocumentContext(model_code="PH-9000", parameter_ids=["PH_FOCUS"])


def success(*values):
    return execution.ReadObservation(
        status="SUCCESS",
        evidence_ids=comparison.EvidenceIds.model_validate(ids(*values)),
    )


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        self.now += 2_000_000
        return self.now


def request(slot="CURRENT_FDC"):
    return execution.ReadRequest(slot=slot, arguments={"target": slot})


def test_fixed_eight_slots_and_query_ownership():
    observed = []

    def invoke(tool, arguments):
        observed.append((tool, arguments))
        return success()

    calls, skips = execution.execute_fixed_policy(
        inventory(), inputs(inventory()), context(), invoke, clock_ns=Clock()
    )
    assert [c.slot for c in calls] == list(comparison.SLOTS)
    assert [c.tool for c in calls] == [comparison.TOOLS[s] for s in comparison.SLOTS]
    assert [c.selection for c in calls] == list(range(1, 9))
    assert [c.retry for c in calls] == [0] * 8
    assert [c.latency_ms for c in calls] == [2] * 8
    assert skips == []
    assert [arguments["query"] for _, arguments in observed[-2:]] == [
        "PH-9000 PH_FOCUS FDC 이상 원인 점검",
        "PH-9000 PH_FOCUS FDC 점검 절차",
    ]
    assert all(
        c.input_digest == digest(canonical_json(args))
        for c, (_, args) in zip(calls, observed, strict=False)
    )


def test_missing_candidates_are_skipped_but_empty_history_is_still_queried():
    inv = inventory().model_copy(
        update={
            "adjacent": comparison.Adjacent(relation="NONE", wafers=0),
            "sibling_chamber_id": None,
            "history_prior_lots": 0,
            "metrology_samples": 0,
        }
    )
    calls, skips = execution.execute_fixed_policy(
        inv, inputs(inv), context(), lambda *_: success()
    )
    assert [c.slot for c in calls] == [
        "CURRENT_FDC",
        "EQUIPMENT",
        "HISTORY",
        "DOCUMENT_1",
        "DOCUMENT_2",
    ]
    assert [s.model_dump() for s in skips] == [
        {"slot": s, "reason": "NO_CANDIDATE"}
        for s in ("ADJACENT_FDC", "SIBLING", "METROLOGY")
    ]


@pytest.mark.parametrize(
    "outcome", ["ERROR", "TIMEOUT", "exception", "timeout_exception"]
)
def test_retry_uses_same_choice_and_pristine_arguments(outcome):
    observed = []

    def invoke(tool, arguments):
        observed.append(deepcopy(arguments))
        arguments["target"] = "adapter-mutated"
        if len(observed) == 1:
            if outcome == "exception":
                raise RuntimeError("secret-provider-detail")
            if outcome == "timeout_exception":
                raise TimeoutError("secret-provider-detail")
            return execution.ReadObservation(
                status=outcome,
                evidence_ids=comparison.EvidenceIds.model_validate(ids()),
            )
        return success("CURRENT_FDC")

    session = execution.ReadSession(inventory(), invoke, clock_ns=Clock())
    calls = session.execute(request())
    assert observed == [{"target": "CURRENT_FDC"}] * 2
    assert [c.retry for c in calls] == [0, 1]
    assert [c.selection for c in calls] == [1, 1]
    assert calls[0].input_digest == calls[1].input_digest
    assert calls[0].status == (
        "TIMEOUT" if outcome in ("TIMEOUT", "timeout_exception") else "ERROR"
    )
    assert "secret" not in canonical_json([c.model_dump() for c in calls]).decode()
    assert calls[1].status == "SUCCESS"


def test_repeated_failure_retries_once_then_fixed_moves_to_next_slot():
    observed = []

    def invoke(tool, args):
        observed.append(deepcopy(args))
        if args.get("target") == "CURRENT_FDC":
            raise TimeoutError()
        return success()

    calls, _ = execution.execute_fixed_policy(
        inventory(), inputs(inventory()), context(), invoke
    )
    assert len(calls) == len(observed) == 8
    assert [c.slot for c in calls[:3]] == ["CURRENT_FDC", "CURRENT_FDC", "ADJACENT_FDC"]
    assert [c.retry for c in calls[:3]] == [0, 1, 0]
    assert calls[-1].slot == "DOCUMENT_1"


def test_total_cap_blocks_retry_of_eighth_failed_read_without_ninth_invocation():
    count = 0

    def invoke(*_):
        nonlocal count
        count += 1
        if count == 8:
            raise TimeoutError()
        return success()

    calls, _ = execution.execute_fixed_policy(
        inventory(), inputs(inventory()), context(), invoke
    )
    assert len(calls) == count == 8
    assert calls[-1].status == "TIMEOUT" and calls[-1].retry == 0


def test_same_tool_cap_includes_failed_reads_and_prevents_fifth_invocation():
    observed = []

    def invoke(*args):
        observed.append(args)
        raise TimeoutError()

    session = execution.ReadSession(inventory(), invoke)
    assert len(session.execute(request())) == 2
    assert len(session.execute(request())) == 2
    with pytest.raises(EvidenceError, match="^TOOL_BUDGET_EXCEEDED$"):
        session.execute(request())
    assert len(observed) == len(session.calls) == 4


def test_cap_during_retry_keeps_failed_attempt_and_terminates_session():
    observed = []

    def invoke(*args):
        observed.append(args)
        if len(observed) == 4:
            raise TimeoutError()
        return success()

    session = execution.ReadSession(inventory(), invoke)
    for _ in range(3):
        session.execute(request())
    with pytest.raises(EvidenceError, match="^TOOL_BUDGET_EXCEEDED$"):
        session.execute(request())
    assert len(session.calls) == len(observed) == 4
    assert session.calls[-1].status == "TIMEOUT"
    with pytest.raises(EvidenceError, match="^READ_SESSION_INVALID$"):
        session.execute(request("EQUIPMENT"))
    assert len(observed) == 4


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "document_override", "oversized"]
)
def test_entire_fixed_input_is_checked_before_invocation(mutation):
    bound = inputs(inventory())
    if mutation == "missing":
        bound.pop("CURRENT_FDC")
    elif mutation == "extra":
        bound["send_action"] = {"action_id": "forbidden"}
    elif mutation == "document_override":
        bound["DOCUMENT_1"] = {"query": "oracle-driven"}
    else:
        bound["HISTORY"] = {"target": "x" * 16385}
    observed = []
    with pytest.raises((EvidenceError, ValidationError)):
        execution.execute_fixed_policy(
            inventory(), bound, context(), lambda *args: observed.append(args)
        )
    assert observed == []


def test_no_candidate_and_send_action_cannot_invoke_adapter():
    inv = inventory().model_copy(update={"sibling_chamber_id": None})
    observed = []
    session = execution.ReadSession(inv, lambda *args: observed.append(args))
    with pytest.raises(EvidenceError, match="^NO_CANDIDATE_CALL$"):
        session.execute(request("SIBLING"))
    with pytest.raises(ValidationError):
        request("send_action")
    assert observed == []


def test_query_is_canonical_bounded_and_policy_hash_is_stable():
    a = execution.DocumentContext(model_code="PH-9000", parameter_ids=["B", "A", "B"])
    b = execution.DocumentContext(model_code="PH-9000", parameter_ids=["A", "B"])
    assert execution.fixed_policy_document_query(
        a, "DOCUMENT_1"
    ) == execution.fixed_policy_document_query(b, "DOCUMENT_1")
    long = execution.DocumentContext(
        model_code="M" * 40, parameter_ids=["P" * 39 + str(i) for i in range(10)]
    )
    queries = [
        execution.fixed_policy_document_query(long, slot)
        for slot in ("DOCUMENT_1", "DOCUMENT_2")
    ]
    assert all(len(q) <= 200 for q in queries) and queries[0] != queries[1]
    assert execution.fixed_policy_sha256() == digest(
        canonical_json(execution.FIXED_POLICY_SPEC)
    )
    with pytest.raises(ValidationError):
        execution.DocumentContext(model_code="M", parameter_ids=[], oracle="forbidden")


def test_actual_fixed_call_log_is_accepted_by_offline_validator():
    artifact, benchmark = artifact_payload()
    for row in artifact["attempts"]:
        if row["policy"] != "FIXED_POLICY_V21":
            continue
        doc_count = 0

        def invoke(tool, args):
            nonlocal doc_count
            if tool == "search_documents":
                doc_count += 1
                return success(f"DOCUMENT_{doc_count}")
            return success(args["target"])

        calls, skips = execution.execute_fixed_policy(
            inventory(), inputs(inventory()), context(), invoke, clock_ns=Clock()
        )
        row["calls"] = [c.model_dump() for c in calls]
        row["skipped_slots"] = [s.model_dump() for s in skips]
        sync_attempt(
            row, benchmark["fixtures"][comparison.FIXTURE_IDS.index(row["fixture_id"])]
        )
    artifact["result"] = recompute(artifact, benchmark)
    assert comparison.validate_artifact(artifact, benchmark)["verdict_reason"] is None


def test_invalid_observation_or_clock_cannot_resume_or_hide_consumed_budget():
    observed = []
    session = execution.ReadSession(inventory(), lambda *args: observed.append(args))
    with pytest.raises(EvidenceError, match="^READ_OBSERVATION_INVALID$"):
        session.execute(request())
    with pytest.raises(EvidenceError, match="^READ_SESSION_INVALID$"):
        session.execute(request())
    assert len(observed) == 1
    ticks = iter([2, 1])
    session = execution.ReadSession(
        inventory(), lambda *_: success(), clock_ns=lambda: next(ticks)
    )
    with pytest.raises(EvidenceError, match="^MONOTONIC_CLOCK_INVALID$"):
        session.execute(request())


def test_returned_records_cannot_mutate_session_history():
    session = execution.ReadSession(inventory(), lambda *_: success("original"))
    calls = session.execute(request())
    calls[0].evidence_ids.values.append("invented")
    assert session.calls[0].evidence_ids.values == ["original"]


def test_reentrant_session_cannot_reserve_or_invoke_again():
    def invoke(*_):
        with pytest.raises(EvidenceError, match="^READ_SESSION_BUSY$"):
            session.execute(request())
        return success()

    session = execution.ReadSession(inventory(), invoke)
    assert len(session.execute(request())) == len(session.calls) == 1


def test_interrupt_poisoning_prevents_unrecorded_attempt_reuse():
    def interrupted(*_):
        raise KeyboardInterrupt()

    session = execution.ReadSession(inventory(), interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.execute(request())
    with pytest.raises(EvidenceError, match="^READ_SESSION_INVALID$"):
        session.execute(request())


def test_execution_policy_matches_offline_validator_budget():
    spec = execution.FIXED_POLICY_SPEC
    rules = comparison.U10_VERDICT_RULES
    assert spec["read_cap"] == rules["budget"]["read"] == 8
    assert spec["same_tool_cap"] == rules["budget"]["same_tool"] == 4
    assert spec["retry_per_selection"] == rules["retry_per_selection"] == 1
    assert spec["selection_cap"] == rules["selector_step_cap"] == 10


def test_core_import_does_not_load_provider_or_runtime_configuration():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from app.agent import u10_read_execution; "
            "assert not {'app.common.config', 'app.common.llm', 'app.agent.react', "
            "'app.agent.runtime_composition', 'psycopg', 'numpy'} & set(sys.modules)",
        ],
        cwd=execution.__file__.rsplit("/app/", 1)[0],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
