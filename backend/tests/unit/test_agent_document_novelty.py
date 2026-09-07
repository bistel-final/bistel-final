"""V5-C-7.1 actual retrieval progress, not a mandatory investigation policy."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent import react
from app.common.enums import ToolCallStatus
from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult
from tests.unit import test_agent_react as fixture


def hit(chunk="DOC-P1", content="P1 현재값과 과거값을 비교한다.", score=0.5):
    return DocumentHit(
        chunk_id=chunk,
        document_id="DOC",
        title="점검 기준",
        content=content,
        score=score,
    )


def result(*hits):
    return DocumentSearchToolResult(ok=True, hits=list(hits))


def context(documents=(), **kwargs):
    return react.build_context(
        run_id="RUN-1",
        lot_id="LOT001",
        chamber_id="EQP01-PM1",
        representative_alarm=fixture.ALARM,
        member_alarms=(fixture.ALARM,),
        route=fixture._level3_route(),
        candidates=fixture._candidates(),
        fdc_results=(),
        equipment=None,
        documents=documents,
        remaining_tool_calls=8,
        remaining_steps=10,
        guard_rejections=0,
        **kwargs,
    )


def payload(ctx):
    return json.loads(react.build_react_select_messages(ctx)[1]["content"])


def test_unattempted_search_is_not_an_empty_success():
    status = react.document_search_progress(())
    assert status.model_dump() == {
        "hits": 0,
        "excerpts": 0,
        "unique_chunks": 0,
        "latest_new_chunks": None,
        "consecutive_no_new_successes": 0,
        "latest_result": "NOT_CHECKED",
    }


def test_reordered_rescored_and_duplicate_hits_do_not_create_new_evidence():
    first, second = hit(), hit("DOC-P2", "P2 점검 기준")
    observations = (
        result(first, second),
        result(second.model_copy(update={"score": 0.9}), first),
        result(first, first),
    )
    before = [item.model_dump() for item in observations]
    status = react.document_search_progress(observations)
    assert status.hits == 6 and status.unique_chunks == 2
    assert status.latest_new_chunks == 0
    assert status.consecutive_no_new_successes == 2
    assert status.latest_result == "SUCCESS"
    assert [item.model_dump() for item in observations] == before


def test_changed_body_under_same_chunk_is_new_even_beyond_display_excerpt():
    common = "P1 점검 근거 " * 30
    first, changed = hit(content=common + "기준 하나"), hit(content=common + "기준 둘")
    status = react.document_search_progress((result(first), result(changed)))
    assert status.unique_chunks == 1 and status.latest_new_chunks == 1
    assert status.consecutive_no_new_successes == 0
    # A previously seen content version is not new if it reappears later.
    repeated = react.document_search_progress(
        (result(first), result(changed), result(first))
    )
    assert repeated.latest_new_chunks == 0


def test_new_chunk_with_same_body_is_a_distinct_source():
    status = react.document_search_progress((result(hit()), result(hit("DOC-P2"))))
    assert status.unique_chunks == 2 and status.latest_new_chunks == 1


def test_empty_success_counts_no_new_information_without_claiming_failure():
    status = react.document_search_progress((result(), result()))
    assert status.latest_result == "SUCCESS"
    assert status.latest_new_chunks == 0
    assert status.unique_chunks == 0
    assert status.consecutive_no_new_successes == 2


@pytest.mark.parametrize(
    "failed",
    [None, DocumentSearchToolResult(ok=False, reason="TIMEOUT: synthetic")],
)
def test_failure_breaks_no_new_streak_and_preserves_prior_information(failed):
    previous = (result(hit()), result(hit()), result(hit()))
    status = react.document_search_progress((*previous, failed))
    assert status.latest_result == "FAILED"
    assert status.latest_new_chunks is None
    assert status.consecutive_no_new_successes == 0
    assert status.unique_chunks == 1 and status.hits == 3
    recovered = react.document_search_progress((*previous, failed, result(hit())))
    assert recovered.latest_result == "SUCCESS"
    assert recovered.latest_new_chunks == 0
    assert recovered.consecutive_no_new_successes == 1


def test_new_evidence_after_empty_or_failed_search_resets_streak():
    status = react.document_search_progress((result(), None, result(), result(hit())))
    assert status.latest_new_chunks == 1
    assert status.consecutive_no_new_successes == 0


def test_actual_context_exposes_progress_without_suppressing_new_query_or_stop():
    ctx = context(
        (result(hit()), result(hit())),
        successful_inputs=(
            {
                "tool": "search_documents",
                "request": {"query": "P1 현재 기준", "model_code": None},
            },
            {
                "tool": "search_documents",
                "request": {"query": "P1 과거 비교", "model_code": None},
            },
        ),
        tool_attempts={"search_documents": 2},
    )
    observed = payload(ctx)
    assert observed["observations"]["document_status"] == {
        "hits": 2,
        "excerpts": 1,
        "unique_chunks": 1,
        "latest_new_chunks": 0,
        "consecutive_no_new_successes": 1,
        "latest_result": "SUCCESS",
    }
    assert {"search_documents", "stop"} <= set(observed["budget"]["available_tools"])
    history = [
        SimpleNamespace(
            tool_name=item["tool"], input=item["request"], status=ToolCallStatus.SUCCESS
        )
        for item in ctx.successful_inputs
    ]
    for selection in (
        fixture._selection("search_documents", query="P1 센서 점검 방법"),
        fixture._selection("stop"),
    ):
        assert (
            react.guard_selection(
                selection, ctx, equipment_fetched=False, tool_history=history
            )
            is None
        )
    assert (
        react.guard_selection(
            fixture._selection("search_documents", query="P1 현재 기준"),
            ctx,
            equipment_fetched=False,
            tool_history=history,
        )
        == "REACT_GUARD_QUERY_REPEATED"
    )


def test_progress_counts_all_actual_hits_not_only_three_displayed_excerpts():
    ctx = context((result(*(hit(f"DOC-P{i}") for i in range(7))),))
    assert ctx.document_status.unique_chunks == 7
    assert ctx.document_status.latest_new_chunks == 7
    assert ctx.document_status.hits == 7
    assert len(ctx.document_details) <= 3


def test_prompt_progress_is_advisory_and_does_not_require_exhaustive_search():
    system = react.build_react_select_messages(context())[0]["content"]
    assert "latest_new_chunks" in system and "다른 질문·도구" in system
    assert "자동 종료하지 않습니다" in system
    assert "최대 4회" not in system


def test_explicit_context_attempt_limit_is_displayed_but_default_remains_four():
    default = payload(context())
    expanded = payload(
        context(max_tool_attempts=8, tool_attempts={"search_documents": 5})
    )
    assert default["budget"]["max_tool_attempts"] == 4
    assert default["budget"]["remaining_by_tool"]["search_documents"] == 4
    assert expanded["budget"]["max_tool_attempts"] == 8
    assert expanded["budget"]["remaining_by_tool"]["search_documents"] == 3


def test_guard_uses_code_owned_limit_not_context_claim():
    ctx = context(max_tool_attempts=100)
    selection = fixture._selection("search_documents", query="P1 새 점검 질문")
    history = [
        SimpleNamespace(
            tool_name="search_documents", input={}, status=ToolCallStatus.TIMEOUT
        )
        for _ in range(4)
    ]
    assert (
        react.guard_selection(
            selection, ctx, equipment_fetched=False, tool_history=history
        )
        == "REACT_GUARD_BUDGET_EXHAUSTED"
    )
    assert (
        react.guard_selection(
            selection,
            ctx,
            equipment_fetched=False,
            tool_history=history,
            max_tool_attempts=8,
        )
        is None
    )
    assert (
        react.guard_selection(
            selection,
            ctx,
            equipment_fetched=False,
            tool_history=history * 2,
            max_tool_attempts=8,
        )
        == "REACT_GUARD_BUDGET_EXHAUSTED"
    )


@pytest.mark.parametrize("limit", [0, -1, True, 2.5, "8"])
def test_invalid_attempt_limits_fail_closed(limit):
    with pytest.raises(ValidationError):
        context(max_tool_attempts=limit)
    with pytest.raises(ValueError, match="REACT_TOOL_ATTEMPT_LIMIT_INVALID"):
        react.guard_selection(
            fixture._selection("stop"),
            context(),
            equipment_fetched=False,
            max_tool_attempts=limit,
        )
