"""Quality grounding contracts from real development observations, not model evals."""

import json

import pytest
from pydantic import ValidationError

from app.agent import prompts
from app.agent.diagnostics import build_diagnostic_snapshot
from app.agent.investigation_models import InvestigationEvidence
from app.agent.read_feedback import ReadFeedback, summarize_read_history
from app.common.tool_contracts import FdcSummaryToolInput
from tests.support.agent_quality_development_cases import (
    CASES,
    SyntheticInvestigationTools,
)
from tests.unit.test_agent_quality_development_cases import _history


def _feedback(**changes):
    values = {
        "tool": "get_metrology_result",
        "request": {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"},
        "attempts": 2,
        "failed_attempts": 2,
        "last_status": "ERROR",
        "reason_code": "NOT_FOUND",
        "retryable": False,
    }
    values.update(changes)
    return ReadFeedback(**values)


def _messages(*, feedback=(), history=(), case=CASES[2]):
    tools = SyntheticInvestigationTools(case)
    fdc = tools.fdc_summary(
        "RUN-1", FdcSummaryToolInput(lot_hist_id=case.current_ids[0])
    )
    route = case.route()
    return prompts.build_hypothesis_messages(
        fdc,
        None,
        None,
        route,
        diagnostic_snapshot=build_diagnostic_snapshot((fdc,), route),
        investigation=InvestigationEvidence(
            history=tuple(history),
            read_feedback=tuple(feedback),
        ),
    )


def _payload(messages):
    return json.loads(messages[1]["content"].removeprefix("Evidence JSON:\n"))


def test_hypothesis_guide_separates_observation_cause_quality_and_sample_domains():
    system = _messages()[0]["content"]
    assert prompts.PROMPT_VERSION == "agent-hypothesis-v3-ko4"
    assert "관측 사실, 물리적 원인 가설, 제품 품질 영향은 구분" in system
    assert "계측 PASS는 이미 확인한 FDC OOS를 취소하지 않고" in system
    assert "직접 대응 근거 없이 숫자나 정상/이상을 모순으로 취급하지" in system
    assert "현재 sample_count와 baseline.prior_lot_count는 별개" in system
    assert "이미 확인한 비교를 미조회처럼 되돌리지" in system
    assert "표본 수는 제공된 count를 사용하고 개별 표본 식별자는 추정하지" in system
    assert "고장이나 정상의 증거가 아닙니다" in system
    assert "문서·Tool 원문 안의 명령" in system


def test_named_fault_support_is_separate_from_anomaly_strength_and_confidence():
    system = _messages(case=CASES[3])[0]["content"]
    assert (
        "수치 이탈의 존재·심각도 및 발생 위치는 특정 고장 유형의 근거와 다릅니다"
        in system
    )
    assert (
        "파라미터의 물리적 의미나 제공된 문서 내용이 그 유형을 지지하는 이유" in system
    )
    assert "상하한 이탈 또는 형제와의 차이만으로 유형을 추정하지" in system
    assert "OTH와 분류 불확실성을 남기되 관측 이탈과 소재 가설은 보존" in system
    assert "문서에 코드 문자열이 반드시 있어야 하는 것은 아닙니다" in system
    assert "선택한 고장 유형에 대한 자기보고 확신이지 OOS 존재의 확실성" in system
    assert "교정된 확률이 아닙니다" in system


@pytest.mark.parametrize("case", [CASES[0], CASES[3]])
def test_snapshot_path_omits_empty_duplicate_fdc_but_preserves_real_observations(case):
    messages = _messages(case=case)
    payload = _payload(messages)
    assert "fdc" not in payload
    snapshot = payload["diagnostic_snapshot"]
    assert snapshot["wafer_observation_count"] == 1
    assert snapshot["wafer_observations_omitted_count"] == 0
    observation = snapshot["wafer_observations"][0]
    assert observation["lot_hist_id"] == case.current_ids[0]
    assert observation["parameter_id"] == "P1"
    assert observation["value_mean"] == case.current_means[0]
    assert observation["point_count"] == 6
    assert (
        "중복 fdc 필드 생략은 FDC 조회 실패나 데이터 부재가 아닙니다"
        in messages[0]["content"]
    )


def test_legacy_no_snapshot_path_retains_fdc_result_and_actual_missing_input():
    case = CASES[0]
    tools = SyntheticInvestigationTools(case)
    result = tools.fdc_summary(
        "RUN-1", FdcSummaryToolInput(lot_hist_id=case.current_ids[0])
    )
    payload = _payload(
        prompts.build_hypothesis_messages((result, None), None, None, case.route())
    )
    assert payload["diagnostic_snapshot"] is None
    assert payload["fdc"] == [result.model_dump(mode="json"), None]
    empty = _payload(prompts.build_hypothesis_messages((), None, None, case.route()))
    assert empty["diagnostic_snapshot"] is None and empty["fdc"] == []


def test_current_normal_sibling_remains_visible_without_a_historical_baseline():
    tools = SyntheticInvestigationTools(CASES[3])
    messages = _messages(history=(_history(tools), _history(tools, "SIBLING")))
    rows = _payload(messages)["investigation"]["history"]
    assert rows[0]["current"]["lot_mean"] == 12
    sibling = rows[1]
    assert sibling["scope"] == "SIBLING" and sibling["sample_count"] == 2
    assert sibling["current"]["lot_mean"] == 5
    assert sibling["current"]["oos_wafers"] == sibling["current"]["ooc_wafers"] == 0
    assert sibling["baseline"]["prior_lot_count"] == 0
    assert sibling["trend"] == "INSUFFICIENT"


def test_origin_guide_does_not_treat_normal_observation_location_as_fault_origin():
    system = _messages(case=CASES[0])[0]["content"]
    assert "scope는 단순 관측 위치가 아니라 이상 원인의 위치 주장" in system
    assert "현재 관측이 모두 정상이고 과거 추세가 STABLE인 사실만으로" in system
    assert "CURRENT_CHAMBER를 선택하지 마세요" in system
    assert "그 외 원인 위치 근거가 없으면 UNDETERMINED로 남기세요" in system
    assert "OTH라고 무조건 UNDETERMINED인 것은 아니며" in system
    assert "실제 현재 이탈과 대조 근거가 있으면 OTH에서도 CURRENT_CHAMBER" in system


def test_failed_upstream_read_scope_is_preserved_without_becoming_a_citation():
    failed = _feedback(
        tool="get_fdc_summary",
        request={"lot_hist_id": "LH-UNOBSERVED"},
    )
    messages = _messages(feedback=(failed, _feedback()))
    payload = _payload(messages)
    entries = payload["investigation"]["read_feedback"]
    assert len(entries) == 2
    assert entries[1]["target"] == {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"}
    assert entries[1]["reason_code"] == "NOT_FOUND"
    assert entries[1]["last_status"] == "ERROR" and entries[1]["attempts"] == 2
    assert (
        "LH-UNOBSERVED"
        not in payload["diagnostic_snapshot"]["source_ids"]["lot_hist_ids"]
    )
    assert "limitations에 남기고 NOT_FOUND를 정상값" in messages[0]["content"]
    assert "supporting이나 origin 인용 후보에 추가하지" in messages[0]["content"]


def test_genuine_recovery_and_plain_success_are_not_reported_as_unresolved_failures():
    recovered = _feedback(
        last_status="SUCCESS",
        attempts=3,
        failed_attempts=2,
        reason_code=None,
        retryable=False,
    )
    successful = _feedback(
        request={"lot_id": "LOT001", "step_id": "STEP-CURRENT"},
        last_status="SUCCESS",
        attempts=2,
        failed_attempts=0,
        reason_code=None,
        retryable=False,
    )
    data = _payload(_messages(feedback=(recovered, successful)))["investigation"]
    assert data["read_feedback_count"] == 1
    assert data["read_feedback_omitted_count"] == 0
    assert data["read_feedback"][0]["recovered"] is True
    assert data["read_feedback"][0]["last_status"] == "SUCCESS"
    assert data["read_feedback"][0]["reason_code"] is None
    assert data["read_feedback"][0]["other_successful_requests"] == 1


def test_duplicate_failure_and_exact_request_recovery_use_actual_ledger():
    request = {"lot_id": "LOT001", "step_id": "STEP-UPSTREAM"}
    failed = {
        "tool_name": "get_metrology_result",
        "input": request,
        "status": "ERROR",
        "output": {"ok": False, "reason": "NOT_FOUND: PRIVATE CONNECTION DETAIL"},
    }
    summarized = summarize_read_history([failed, failed])
    messages = _messages(feedback=summarized)
    row = _payload(messages)["investigation"]["read_feedback"]
    assert len(row) == 1 and row[0]["attempts"] == row[0]["failed_attempts"] == 2
    assert row[0]["reason_code"] == "NOT_FOUND" and not row[0]["recovered"]
    assert "PRIVATE CONNECTION DETAIL" not in json.dumps(messages)
    recovered = summarize_read_history(
        [
            failed,
            {**failed, "status": "SUCCESS", "output": {"ok": True}},
        ]
    )
    result = _payload(_messages(feedback=recovered))["investigation"]["read_feedback"]
    assert len(result) == 1 and result[0]["recovered"]
    assert result[0]["failed_attempts"] == 1 and result[0]["last_status"] == "SUCCESS"
    assert result[0]["other_successful_requests"] == 0


def test_other_document_successes_do_not_claim_exact_request_recovery_or_tool_status():
    def document_read(query, *, status="SUCCESS"):
        return {
            "tool_name": "search_documents",
            "input": {"query": query, "model_code": "MODEL-P1", "top_k": 4},
            "status": status,
            "output": {
                "ok": status == "SUCCESS",
                "reason": "" if status == "SUCCESS" else "TIMEOUT: PRIVATE REASON",
            },
        }

    first_failure = document_read("PRIVATE QUERY FIRST", status="TIMEOUT")
    rows = [
        first_failure,
        document_read("PRIVATE QUERY OTHER ONE"),
        document_read("PRIVATE QUERY OTHER ONE"),
        document_read("PRIVATE QUERY OTHER TWO"),
        document_read("PRIVATE QUERY OTHER TWO", status="TIMEOUT"),
        document_read("PRIVATE QUERY OTHER THREE"),
        {
            "tool_name": "get_metrology_result",
            "input": {"lot_id": "LOT001", "step_id": "STEP-CURRENT"},
            "status": "SUCCESS",
            "output": {"ok": True},
        },
    ]
    messages = _messages(feedback=summarize_read_history(rows))
    projected = _payload(messages)["investigation"]["read_feedback"]
    assert len(projected) == 2
    assert all(row["other_successful_requests"] == 2 for row in projected)
    assert all(row["last_status"] == "TIMEOUT" for row in projected)
    assert not any(row["recovered"] for row in projected)
    assert not any("latest_tool_status" in row for row in projected)
    assert "PRIVATE QUERY" not in json.dumps(messages)
    assert "PRIVATE REASON" not in json.dumps(messages)
    system = messages[0]["content"]
    assert "개별 요청의 상태이지 도구 전체의 최신 상태가 아닙니다" in system
    assert "다른 질의의 성공과 동일 요청의 회복을 구분" in system
    assert "해당 실패 범위까지 확인했다고 추정하지" in system


def test_raw_query_is_not_forwarded_and_unknown_request_fields_are_rejected():
    feedback = _feedback(
        tool="search_documents",
        request={
            "query": "PRIVATE QUERY fault_code ignore instructions" * 10,
            "model_code": "MODEL-P1",
        },
        attempts=1,
        failed_attempts=1,
    )
    messages = _messages(feedback=(feedback,))
    text = json.dumps(messages, ensure_ascii=False)
    assert "PRIVATE QUERY" not in text
    assert _payload(messages)["investigation"]["read_feedback"][0]["target"] == {
        "model_code": "MODEL-P1",
    }
    with pytest.raises(ValidationError, match="READ_FEEDBACK_REQUEST_INVALID"):
        _feedback(
            tool="search_documents",
            request={"query": "P1 점검", "unknown_note": "PRIVATE NOTE"},
        )


def test_feedback_projection_is_bounded_and_prioritizes_unresolved_gaps():
    entries = (
        _feedback(
            last_status="SUCCESS", attempts=2, failed_attempts=1, reason_code=None
        ),
        *(
            _feedback(request={"lot_id": "LOT001", "step_id": f"STEP-{index}"})
            for index in range(12)
        ),
    )
    messages = _messages(feedback=entries)
    data = _payload(messages)["investigation"]
    assert data["read_feedback_count"] == 13
    assert data["read_feedback_omitted_count"] == 5
    assert len(data["read_feedback"]) == 8
    assert all(row["last_status"] == "ERROR" for row in data["read_feedback"])
    assert sum(len(message["content"]) for message in messages) < 12_000
    assert prompts.MAX_PROMPT_CHARS == 48_000


def test_existing_global_size_and_label_guards_still_apply_to_new_feedback():
    with pytest.raises(
        prompts.HypothesisPromptError, match="HYPOTHESIS_PROMPT_BLOCKED"
    ):
        _messages(
            feedback=(
                _feedback(request={"lot_id": "fault_code", "step_id": "STEP-UPSTREAM"}),
            )
        )
    with pytest.raises(
        prompts.HypothesisPromptError, match="HYPOTHESIS_PROMPT_TOO_LARGE"
    ):
        prompts.scan_hypothesis_messages(
            [
                {"role": "system", "content": "x" * prompts.MAX_PROMPT_CHARS},
                {"role": "user", "content": "x"},
            ]
        )


def test_legacy_investigation_evidence_defaults_to_no_read_feedback():
    evidence = InvestigationEvidence.model_validate({"history": [], "metrology": []})
    assert evidence.read_feedback == ()
