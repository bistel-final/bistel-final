"""`V5-C-2.3` prompt 결정성·label 노출 차단 회귀."""

from __future__ import annotations

import json

import pytest

from app.agent.incident import ResolvedIncident
from app.agent.prompts import (
    MAX_DOCUMENT_EXCERPT_CHARS,
    MAX_PROMPT_CHARS,
    TRUNCATION_MARKER,
    HypothesisPromptError,
    build_hypothesis_messages,
    scan_hypothesis_messages,
)
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute
from app.common.enums import AlarmSource
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
