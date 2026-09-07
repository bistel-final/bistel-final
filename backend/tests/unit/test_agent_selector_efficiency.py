"""Production selector information contract; no benchmark oracle or live LLM."""

import json

import pytest

from app.agent import react
from tests.unit import test_agent_react as fixture


def raw_payload(context):
    return json.loads(react.build_react_select_messages(context)[1]["content"])


def test_same_tool_exhaustion_is_visible_before_model_selects():
    context = fixture._context(
        tool_attempts={"get_fdc_summary": 4, "search_documents": 2},
        remaining_tool_calls=2,
    )
    before = context.model_dump()
    budget = raw_payload(context)["budget"]
    assert budget["remaining_by_tool"]["get_fdc_summary"] == 0
    assert budget["remaining_by_tool"]["search_documents"] == 2
    assert "get_fdc_summary" not in budget["available_tools"]
    assert "search_documents" in budget["available_tools"]
    assert "stop" in budget["available_tools"]
    assert context.model_dump() == before


@pytest.mark.parametrize("remaining", [0, 1, 5])
def test_remaining_budget_never_exceeds_either_real_limit(remaining):
    budget = raw_payload(
        fixture._context(
            remaining_tool_calls=remaining,
            tool_attempts={"get_metrology_result": 3},
        )
    )["budget"]
    assert budget["remaining_by_tool"]["get_metrology_result"] == min(remaining, 1)
    assert budget["remaining_by_tool"]["search_documents"] == min(remaining, 4)
    if remaining == 0:
        assert budget["available_tools"] == ["stop"]


def test_candidate_tables_keep_all_tokens_and_scope_without_repeated_keys():
    context = fixture._context()
    candidates = raw_payload(context)["candidates"]
    fdc = candidates["fdc"]
    assert fdc["columns"] == [
        "id",
        "relation",
        "wafer_ordinal",
        "observed",
        "available",
    ]
    assert fdc["rows"] == [
        ["F1", "CURRENT", 1, True, False],
        ["F2", "DOWNSTREAM", 1, False, True],
    ]
    assert len(candidates["history"]["rows"]) == len(context.candidates.history)
    assert len(candidates["metrology"]["rows"]) == len(context.candidates.metrology)
    assert all(c.candidate_id in str(candidates) for c in context.candidates.fdc)


def test_prompt_has_bounded_decision_objective_not_exhaustive_investigation():
    system = react.build_react_select_messages(fixture._context())[0]["content"]
    assert len(system) <= 1200
    assert "예산이 남아 있어도" in system
    assert "모든 후보" in system and "필수" in system
    assert "다른 wafer" in system and "문서" in system
    assert "available_tools" in system and "remaining_by_tool" in system
    assert "발췌" in system and "명령" in system


def test_distinct_observations_survive_compaction_without_forcing_a_next_tool():
    contexts = [
        fixture._context(fdc_observations=(text,))
        for text in (
            "P1 upper=10 value=12 upstream unknown",
            "P1 upper=10 value=7 upstream checked",
        )
    ]
    payloads = [raw_payload(c) for c in contexts]
    assert payloads[0]["observations"]["fdc"] != payloads[1]["observations"]["fdc"]
    assert (
        payloads[0]["budget"]["available_tools"]
        == payloads[1]["budget"]["available_tools"]
    )
    assert len(payloads[0]["budget"]["available_tools"]) > 1
    for context in contexts:
        assert (
            react.guard_selection(
                fixture._selection("stop"), context, equipment_fetched=False
            )
            is None
        )


def test_successful_document_queries_are_available_for_novel_followup():
    ctx = fixture._context(
        successful_inputs=(
            {
                "tool": "search_documents",
                "request": {"query": "P1 관리 범위", "model_code": "MODEL"},
            },
            {
                "tool": "search_documents",
                "request": {"query": "P1 관리 범위", "model_code": "MODEL"},
            },
        )
    )
    assert raw_payload(ctx)["observations"]["document_queries"] == ["P1 관리 범위"]


def test_unavailable_documents_and_fetched_equipment_not_advertised():
    ctx = fixture._context(
        documents_available=False, equipment_observation="관계 조회 완료"
    )
    choices = raw_payload(ctx)["budget"]["available_tools"]
    assert "search_documents" not in choices
    assert "get_equipment_context" not in choices
    assert "stop" in choices


def test_empty_candidates_do_not_advertise_impossible_tools():
    ctx = fixture._context(candidates=react.ReactCandidates(run_id="RUN-1"))
    assert raw_payload(ctx)["budget"]["available_tools"] == [
        "search_documents",
        "get_equipment_context",
        "stop",
    ]


def test_equipment_success_is_not_readvertised_when_summary_is_missing():
    ctx = fixture._context(
        successful_inputs=(
            {"tool": "get_equipment_context", "request": {"chamber_id": "EQP01-PM1"}},
        )
    )
    assert ctx.equipment_observation is None
    assert "get_equipment_context" not in raw_payload(ctx)["budget"]["available_tools"]


def test_history_becomes_available_only_after_its_prerequisites():
    for keys in ((), (("P1", 2),)):
        payload = raw_payload(fixture._context(observed_parameter_keys=keys))
        assert (
            "get_chamber_parameter_history" not in payload["budget"]["available_tools"]
        )
        assert payload["candidates"]["history"]["rows"][0][-1] is False
    base = fixture._context()
    sibling = base.candidates.history[0].model_copy(
        update={"scope": "SIBLING", "chamber_id": "EQP01-PM2"}
    )
    candidates = base.candidates.model_copy(update={"history": (sibling,)})
    for observation, siblings, available in (
        (None, ("EQP01-PM2",), False),
        ("설비 조회 완료", (), False),
        ("설비 조회 완료", ("EQP01-PM2",), True),
    ):
        payload = raw_payload(
            fixture._context(
                candidates=candidates,
                equipment_observation=observation,
                sibling_chamber_ids=siblings,
            )
        )
        assert payload["candidates"]["history"]["rows"][0][-1] is available


@pytest.mark.parametrize(
    "tool,key,token,kind",
    [
        ("get_fdc_summary", "fdc_candidate_id", "F2", "fdc"),
        ("get_chamber_parameter_history", "history_candidate_id", "H1", "history"),
        ("get_metrology_result", "metrology_candidate_id", "M1", "metrology"),
    ],
)
def test_successful_canonical_requests_are_not_advertised_again(tool, key, token, kind):
    base = fixture._context()
    request = react.resolve_call(fixture._selection(tool, **{key: token}), base)[
        "request"
    ]
    ctx = base.model_copy(
        update={"successful_inputs": ({"tool": tool, "request": request},)}
    )
    payload = raw_payload(ctx)
    assert tool not in payload["budget"]["available_tools"]
    assert all(not row[-1] for row in payload["candidates"][kind]["rows"])


def test_large_payload_is_lossless_and_bounded_with_observations():
    base = fixture._context()
    candidates = base.candidates.model_copy(
        update={
            "fdc": tuple(
                base.candidates.fdc[1].model_copy(
                    update={
                        "candidate_id": f"F{i}",
                        "lot_hist_id": f"LH-{i}",
                        "wafer_ordinal": i,
                    }
                )
                for i in range(1, 101)
            ),
            "history": tuple(
                base.candidates.history[0].model_copy(
                    update={"candidate_id": f"H{i}", "parameter_id": f"P{i}"}
                )
                for i in range(1, 21)
            ),
        }
    )
    observations = tuple(f"F{i}: " + "관측값 " * 118 for i in range(1, 5))
    ctx = base.model_copy(
        update={
            "candidates": candidates,
            "fdc_observations": observations,
            "history_observations": ("이전 표본 " * 90,),
            "metrology_observations": ("계측값 " * 118,),
        }
    )
    messages = react.build_react_select_messages(ctx)
    payload = json.loads(messages[1]["content"])
    tables = payload["candidates"]
    expanded = {
        name: [dict(zip(table["columns"], row, strict=True)) for row in table["rows"]]
        for name, table in tables.items()
    }
    assert [r["id"] for r in expanded["fdc"]] == [
        c.candidate_id for c in candidates.fdc
    ]
    assert [r["parameter_id"] for r in expanded["history"]] == [
        c.parameter_id for c in candidates.history
    ]
    assert payload["observations"]["fdc"] == list(observations)
    assert payload["observations"]["history"] == list(ctx.history_observations)
    assert payload["observations"]["metrology"] == list(ctx.metrology_observations)
    packed_size = len(json.dumps(tables, ensure_ascii=False, separators=(",", ":")))
    expanded_size = len(json.dumps(expanded, ensure_ascii=False, separators=(",", ":")))
    assert packed_size < expanded_size * 0.6
    assert len("\n".join(m["content"] for m in messages)) <= 48000
