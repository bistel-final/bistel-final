"""U11 contracts with local ports only; never export data or invoke a provider."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent.diagnostics import build_diagnostic_snapshot
from app.agent.hypothesis import generate_hypothesis
from app.agent.hypothesis_v3 import finalize_hypothesis
from app.agent.investigation_models import InvestigationEvidence, OriginBasisRef
from app.agent.origin_diagnostics import DEGRADED_REASON, capture_dropped
from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.u10_batch import execute_batch
from app.agent.u10_comparison import (
    DIAGNOSTIC_FIELDS,
    Artifact,
    Attempt,
    _check_attempt_diagnostics,
    validate_artifact,
)
from tests.unit import test_agent_hypothesis as generation
from tests.unit import test_agent_hypothesis_v3 as v3
from tests.unit import test_agent_u10_batch as batch
from tests.unit.test_agent_u10_comparison import artifact_payload
from tests.unit.test_agent_u10_hypothesis import generated


def finalized(scope, refs, *, directional=False, degrade=True):
    draft = v3._draft(origin_claim={"scope": scope, "basis_refs": refs})
    route, fdc = v3.fixture._route(), [v3._fdc()]
    investigation = InvestigationEvidence()
    if directional:
        draft.supporting_lot_hist_ids += ("LH-ETCH",)
        investigation = InvestigationEvidence(
            successful_calls=[
                {"tool_name": "get_fdc_summary", "input": {"lot_hist_id": "LH-ETCH"}},
            ]
        )
    captured = []
    value = finalize_hypothesis(
        draft,
        fdc,
        route,
        build_diagnostic_snapshot(fdc, route),
        None,
        investigation,
        degrade_origin=degrade,
        diagnostics=captured,
    )
    return value, captured


VALID = {"namespace": "PARAMETER", "id": "PH_FOCUS"}
BAD = {"namespace": "PARAMETER", "id": "unknown"}


@pytest.mark.parametrize(
    "scope,refs,expected",
    [
        ("UNDETERMINED", [BAD], "UNDETERMINED"),
        ("CURRENT_CHAMBER", [VALID, BAD], "CURRENT_CHAMBER"),
        ("CURRENT_CHAMBER", [BAD], "UNDETERMINED"),
        ("EQUIPMENT_COMMON", [BAD], "UNDETERMINED"),
        ("DOWNSTREAM", [VALID, BAD], "DOWNSTREAM"),
    ],
)
def test_degradation_matrix_and_private_public_boundary(scope, refs, expected):
    value, private = finalized(scope, refs, directional=scope == "DOWNSTREAM")
    origin = value.origin_assessment
    assert origin.scope == expected and origin.degraded
    assert origin.dropped_basis_count == 1 and len(private) == 1
    assert "unknown" not in canonical_json(value).decode()
    assert set(origin.model_dump()) == {
        "scope",
        "basis",
        "compared",
        "degraded",
        "degraded_reasons",
        "dropped_basis_count",
    }
    assert private[0].dropped_basis_refs[0].id == "unknown"


def test_original_direction_and_last_round_policy_cannot_be_bypassed():
    with pytest.raises(ValueError, match="ORIGIN_CLAIM_UNSUPPORTED"):
        finalized("UPSTREAM", [BAD])
    with pytest.raises(ValueError, match="ORIGIN_BASIS_OUTSIDE_EVIDENCE"):
        finalized("DOWNSTREAM", [BAD], directional=True)
    with pytest.raises(ValueError, match="ORIGIN_BASIS_OUTSIDE_EVIDENCE"):
        finalized("CURRENT_CHAMBER", [BAD], degrade=False)
    value, private = finalized("CURRENT_CHAMBER", [VALID])
    assert not value.origin_assessment.degraded and not private


@pytest.mark.parametrize(
    "raw,unique",
    [
        (["bad x", "bad y"], 2),
        (["bad x", "bad x"], 1),
        ([f"bad {i}" for i in range(20)], 20),
    ],
)
def test_identity_is_full_set_display_is_capped_and_safe(raw, unique):
    value = capture_dropped([OriginBasisRef(namespace="PARAMETER", id=s) for s in raw])
    assert value.dropped_basis_count == len(raw)
    assert len(value.dropped_evidence_ids) == unique
    assert len(value.dropped_basis_refs) == min(16, len(raw))
    assert all(r.id == "<INVALID_FORMAT>" for r in value.dropped_basis_refs)
    assert all(s not in canonical_json(value).decode() for s in raw)
    from app.agent.u10_evidence import project_hypothesis_citations

    normal, _ = finalized("CURRENT_CHAMBER", [VALID])
    available = set(project_hypothesis_citations(normal).values)
    cited = set(project_hypothesis_citations(normal, dropped=value).values)
    assert len(cited - available) == unique


def test_actual_generation_corrects_then_degrades_and_keeps_usage():
    messages = []
    raw = generation._content(
        origin_claim={"scope": "CURRENT_CHAMBER", "basis_refs": [BAD]}
    )

    def complete(value, **kwargs):
        messages.append(value)
        return generation._completion(raw)

    outcome = generate_hypothesis(
        None, None, generation._docs(), generation._route(), completion_port=complete
    )
    assert len(messages) == 2
    assert outcome.hypothesis.origin_assessment.scope == "UNDETERMINED"
    assert outcome.origin_diagnostics.dropped_basis_count == 1
    assert "허용 ID 요약" in messages[1][1]["content"]
    assert "diagnostic_snapshot.source_ids.alarm_refs" in messages[0][0]["content"]
    assert (
        outcome.llm_usage.input_tokens == generation._completion(raw).prompt_tokens * 2
    )


def degraded_generator(**inputs):
    value = generated(**inputs)
    private = capture_dropped([OriginBasisRef(**BAD)])
    value.origin_diagnostics = private
    value.hypothesis.origin_assessment = value.hypothesis.origin_assessment.model_copy(
        update={
            "degraded": True,
            "degraded_reasons": ("ORIGIN_BASIS_OUTSIDE_EVIDENCE",),
            "dropped_basis_count": 1,
        }
    )
    return value


def issued(mode="normal"):
    def tweak(key, params):
        if mode == "degraded":
            params["generate"] = degraded_generator
        if mode == "rejected":
            from app.agent.hypothesis import HypothesisGenerationError
            from tests.unit.test_agent_u10_hypothesis import usage

            def reject(**kwargs):
                raise HypothesisGenerationError(
                    "HYPOTHESIS_STRUCTURE_INVALID",
                    usage=usage(),
                    last_rejection_reason="ORIGIN_CLAIM_UNSUPPORTED",
                )

            params["generate"] = reject

    params, *_ = batch.inputs(tweak=tweak)
    return execute_batch(**params), params["benchmark"]


@pytest.mark.parametrize("mode", ["normal", "degraded", "rejected"])
def test_actual_batch_writer_ko2_roundtrip_and_strict_service_split(mode):
    artifact, benchmark = issued(mode)
    payload = artifact.model_dump(mode="json")
    assert validate_artifact(payload, benchmark.model_dump()) == artifact.result
    assert Artifact.model_validate(payload).model_dump(mode="json") == payload
    for row in payload["attempts"]:
        assert DIAGNOSTIC_FIELDS <= row.keys()
        assert row["completion"] is (mode == "normal")
        assert row["service_completion"] is (mode != "rejected")
        assert row["hypothesis_final_reason"] == (
            None
            if mode == "normal"
            else DEGRADED_REASON
            if mode == "degraded"
            else "ORIGIN_CLAIM_UNSUPPORTED"
        )
        if mode == "degraded":
            assert (
                len(
                    set(row["cited_evidence_ids"]["values"])
                    - set(row["available_evidence_ids"]["values"])
                )
                >= 1
            )


@pytest.fixture(scope="module")
def normal_payload():
    a, b = issued()
    return a.model_dump(mode="json"), b.model_dump()


@pytest.mark.parametrize("key", sorted(DIAGNOSTIC_FIELDS))
def test_ko2_every_key_required_before_defaults(normal_payload, key):
    a, b = deepcopy(normal_payload)
    del a["attempts"][0][key]
    with pytest.raises(EvidenceError, match="U10_SCHEMA_INVALID"):
        validate_artifact(a, b)


@pytest.mark.parametrize(
    "change",
    [
        {"service_completion": False},
        {"origin_degraded": True},
        {"dropped_basis_count": 1},
        {"dropped_basis_unique": 1},
        {"hypothesis_final_reason": DEGRADED_REASON},
        {"hypothesis_final_reason": "untrusted"},
        {"read_stop_reason": "LLM_DEPENDENCY"},
    ],
)
def test_ko2_matrix_tampering_rejected(normal_payload, change):
    a, b = deepcopy(normal_payload)
    a["attempts"][0].update(change)
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        validate_artifact(a, b)


def test_ko1_bytes_and_negative_rows_are_not_backfilled():
    a, b = artifact_payload()
    for row in a["attempts"]:
        row["completion"] = False
    from tests.unit.test_agent_u10_comparison import recompute

    a["result"] = recompute(a, b)
    raw = canonical_json(a)
    assert canonical_json(Artifact.model_validate(a)) == raw
    assert validate_artifact(a, b) == a["result"]
    a["attempts"][0]["service_completion"] = False
    with pytest.raises(ValidationError, match="U10_SCHEMA_INVALID"):
        Artifact.model_validate(a)


def test_actual_dependency_stop_is_forwarded_and_cannot_claim_service():
    from app.agent.react import ReactSelectionError
    from tests.unit.test_agent_react import _usage
    from tests.unit.test_agent_u10_attempt import run
    from tests.unit.test_agent_u10_react_execution import outcome

    calls = []

    def select(ctx, **kwargs):
        calls.append(ctx)
        if len(calls) == 1:
            return outcome("get_fdc_summary", fdc_candidate_id="F1")
        raise ReactSelectionError("LLM_DEPENDENCY", usage=_usage())

    row = run(select=select, generate=degraded_generator).attempt
    assert row.read_stop_reason == "LLM_DEPENDENCY"
    assert not row.completion and not row.service_completion
    assert row.hypothesis_final_reason == DEGRADED_REASON
    tampered = Attempt.model_validate({**row.model_dump(), "service_completion": True})
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        _check_attempt_diagnostics(tampered)


@pytest.mark.parametrize("mode", ["degraded", "rejected"])
def test_non_success_null_reason_is_not_a_missing_key(mode):
    a, b = issued(mode)
    payload = a.model_dump(mode="json")
    payload["attempts"][0]["hypothesis_final_reason"] = None
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        validate_artifact(payload, b.model_dump())


def test_reserved_identity_cannot_become_required_evidence():
    from app.agent.u10_comparison import EvidenceIds, _check_attempt
    from tests.unit.test_agent_u10_comparison import ids

    a, b = issued("degraded")
    row = a.attempts[0]
    token = next(v for v in row.cited_evidence_ids.values if ":DROPPED#" in v)
    fixture = b.fixtures[0].model_copy(
        update={"required_evidence_ids": EvidenceIds.model_validate(ids(token))}
    )
    with pytest.raises(EvidenceError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        _check_attempt(row, fixture, row.execution_order)


def test_real_generation_graph_finalize_and_public_diagnosis_hide_private_refs(
    monkeypatch,
):
    from dataclasses import replace

    from app.agent import graph as subject
    from app.agent.public_read_model import _diagnosis_block
    from tests.unit import test_agent_graph as graph_fixture
    from tests.unit import test_agent_screen_read_model as screen
    from tests.unit.test_agent_react import _level3_route

    outcomes = []

    class Ports(graph_fixture._Ports):
        def generate_hypothesis(
            self, fdc, graph, docs, route, extra_data_gaps=(), investigation=None
        ):
            raw = generation._content(
                supporting_alarms=[
                    a.model_dump(mode="json") for a in route.incident.member_alarms
                ],
                supporting_chunk_ids=[h.chunk_id for h in docs.hits],
                supporting_relation_ids=[],
                origin_claim={"scope": "CURRENT_CHAMBER", "basis_refs": [BAD]},
            )
            result = generate_hypothesis(
                fdc,
                graph,
                docs,
                route,
                extra_data_gaps,
                investigation,
                completion_port=lambda *a, **kw: generation._completion(
                    raw, model="fixture-model"
                ),
            )
            outcomes.append(result)
            return result

    graph, _, _, finishes, _ = graph_fixture._build(
        monkeypatch, ports=Ports(), level_route=_level3_route()
    )
    state = graph_fixture._invoke(graph)
    assert [status for status, _ in finishes] == ["COMPLETED"], state.get("errors")
    assert state["hypothesis"].origin_assessment.degraded
    saved = subject._prediction_evidence(outcomes[0])
    assert "origin_diagnostics" not in saved
    assert "unknown" not in canonical_json(saved).decode()
    record = replace(screen._run(), prediction_evidence=saved)
    dto = _diagnosis_block(record, outcomes[0].diagnostic_snapshot).model_dump(
        mode="json"
    )
    assert dto["origin_assessment"]["degraded"] is True
    assert "DROPPED#" not in canonical_json(dto).decode()
    assert "dropped_basis_refs" not in canonical_json(dto).decode()


@pytest.mark.parametrize(
    "bad",
    [
        {"supporting_chunk_ids": ["missing"]},
        {"cause_summary": "English only"},
        {"predicted_fault_code": "FOC"},
    ],
)
def test_last_round_degradation_does_not_relax_other_validators(bad):
    from app.agent.hypothesis import HypothesisGenerationError

    raw = generation._content(
        origin_claim={"scope": "CURRENT_CHAMBER", "basis_refs": [BAD]}, **bad
    )
    with pytest.raises(HypothesisGenerationError) as caught:
        generate_hypothesis(
            None,
            None,
            generation._docs(),
            generation._route(),
            completion_port=lambda *a, **kw: generation._completion(raw),
        )
    assert caught.value.last_rejection_reason in {
        "DOCUMENT_CITATION_OUTSIDE_EVIDENCE",
        "KOREAN_OUTPUT_REQUIRED",
        "PARAMETER_FINDING_REQUIRED",
    }
