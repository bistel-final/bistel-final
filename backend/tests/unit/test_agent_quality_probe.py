"""Offline safety/provenance checks for the explicitly synthetic live probe.

Completion doubles traverse the real graph and parser; transport tests traverse
the common LLM client with fake HTTP. No provider, database or source corpus is
read and these tests are not evidence of live-model quality.
"""

import json
import stat
from dataclasses import replace

import httpx
import pytest

from app.agent import react
from app.common import llm
from scripts import probe_agent_quality as subject
from tests.support import agent_quality_holdout_cases as holdout
from tests.unit import test_agent_hypothesis as hypothesis_fixture


def _forbidden(*_args, **_kwargs):
    pytest.fail("Unexpected network/source/effect access")


def _configure(monkeypatch):
    monkeypatch.setattr(
        llm,
        "_resolve_endpoint",
        lambda: ("https://api.openai.com/v1", "offline-secret-never-serialize"),
    )
    monkeypatch.setattr(llm, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", subject.MODEL)
    monkeypatch.setattr(llm, "LLM_MAX_TOKENS", subject.MAX_COMPLETION_TOKENS)
    monkeypatch.setattr(llm, "_retry_max", lambda: 1)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)


def _response(content="테스트 응답", *, status=200, usage=True):
    body = {
        "model": subject.MODEL,
        "choices": [{"message": {"content": content}}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 12, "completion_tokens": 5}
    return httpx.Response(status, json=body)


def _request_body(**overrides):
    return {
        "model": subject.MODEL,
        "max_completion_tokens": subject.MAX_COMPLETION_TOKENS,
        "reasoning_effort": "low",
        "messages": [{"role": "user", "content": "합성 관측"}],
        **overrides,
    }


@pytest.mark.parametrize(
    "case", subject.selected_cases("all"), ids=lambda case: case.case_id
)
def test_all_synthetic_cases_cross_real_graph_without_metadata_or_effects(
    monkeypatch, case
):
    marker = "EVALUATOR_ONLY_ACCEPTANCE_9F8D"
    isolated = replace(case, case_id=marker, title=marker, acceptance=(marker,))
    messages_seen = []
    tools_seen = []
    original_tools = subject.SyntheticInvestigationTools
    original_holdout_tools = holdout.SyntheticHoldoutInvestigationTools
    read_text = subject.Path.read_text

    def package_metadata_only(path, *args, **kwargs):
        # Lazy LangGraph imports read installed package version metadata. Those
        # are dependencies, not the final source corpus or an experiment bundle.
        if "site-packages" not in path.parts:
            _forbidden()
        return read_text(path, *args, **kwargs)

    def tools(*args, **kwargs):
        value = original_tools(*args, **kwargs)
        tools_seen.append(value)
        return value

    def holdout_tools(*args, **kwargs):
        value = original_holdout_tools(*args, **kwargs)
        tools_seen.append(value)
        return value

    monkeypatch.setattr(subject, "SyntheticInvestigationTools", tools)
    monkeypatch.setattr(holdout, "SyntheticHoldoutInvestigationTools", holdout_tools)
    monkeypatch.setattr(subject.httpx, "post", _forbidden)
    monkeypatch.setattr(subject.Path, "read_bytes", _forbidden)
    monkeypatch.setattr(subject.Path, "read_text", package_metadata_only)

    def complete(messages, **kwargs):
        messages_seen.append(messages)
        assert marker not in json.dumps(messages, ensure_ascii=False)
        if kwargs["json_schema"] == react.REACT_SELECT_SCHEMA:
            payload = json.loads(messages[1]["content"])
            assert payload["budget"]["max_tool_attempts"] == 8
            stopping = bool(payload["observations"]["documents"])
            answer = react.ReactSelection(
                rationale_summary="관측을 해석할 점검 근거를 확인한다",
                next="stop" if stopping else "search_documents",
                arguments=react.ReactArguments(
                    **({} if stopping else {"query": "P1 관리 기준 점검"})
                ),
                stop_reason="확인된 관측과 문서 범위에서 결과를 제공한다"
                if stopping
                else None,
            ).model_dump_json()
        else:
            payload = json.loads(messages[1]["content"].split("\n", 1)[1])
            sources = payload["diagnostic_snapshot"]["source_ids"]
            answer = hypothesis_fixture._content(
                cause_summary="P1 관측과 문서를 바탕으로 점검 범위를 확인했습니다.",
                supporting_alarms=payload["route"]["incident"]["member_alarms"],
                supporting_chunk_ids=[
                    hit["chunk_id"] for hit in payload["document"]["hits"]
                ],
                supporting_relation_ids=[],
                supporting_lot_hist_ids=sources["lot_hist_ids"],
                supporting_parameter_ids=sources["parameter_ids"],
                evidence_synthesis="현재 P1 관측과 문서의 점검 범위를 비교했습니다.",
            )
        return hypothesis_fixture._completion(answer, model=subject.MODEL)

    result = subject.run_case(isolated, complete)
    assert result["investigation_completed"] is True
    assert result["stopped_before_external_effects"] is True
    assert result["next_nodes"] == ["persist_action"]
    assert result["real_shared_service_effects"] == 0
    assert result["automated_quality_verdict"] == "NOT_GRADED_REQUIRES_EVIDENCE_REVIEW"
    assert result["acceptance_for_human_review_only"] == [marker]
    assert len(messages_seen) >= 3
    assert len(tools_seen) == 1 and tools_seen[0].send_count == 0
    assert all(row["tool"] != "send_action" for row in result["read_history"])
    assert result["hypothesis"]["supporting_parameter_ids"] == ["P1"]
    assert result["react_trace"][-1]["phase"] == "STOPPED"


def test_live_complete_uses_common_client_and_records_retry_usage(
    monkeypatch, tmp_path
):
    _configure(monkeypatch)
    responses = iter([_response(status=503, usage=False), _response()])
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(subject.httpx, "post", post)
    live = subject.Live(tmp_path, 2)
    result = live.complete([{"role": "user", "content": "합성 관측"}])
    assert result.content == "테스트 응답"
    assert (result.prompt_tokens, result.completion_tokens) == (12, 5)
    assert live.count == len(sent) == len(live.calls) == 2
    assert live.calls[0]["usage_unobserved"] is True
    assert live.calls[1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 5}
    raw = "".join(path.read_text() for path in tmp_path.iterdir())
    assert "offline-secret-never-serialize" not in raw and "Authorization" not in raw
    with pytest.raises(llm.LlmDependencyError, match="DEVELOPMENT_HTTP_LIMIT"):
        live.complete([{"role": "user", "content": "합성 관측"}])
    assert len(sent) == 2


@pytest.mark.parametrize("limit", [0, -1, subject.MAX_HTTP + 1, True, 1.0, "1"])
def test_live_constructor_rejects_limit_bypass(tmp_path, limit):
    with pytest.raises(ValueError, match="DEVELOPMENT_HTTP_LIMIT_INVALID"):
        subject.Live(tmp_path, limit)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url,changes",
    [
        ("https://unapproved.example/v1/chat/completions", {}),
        ("https://api.openai.com/v1/chat/completions", {"model": "wrong-model"}),
        ("https://api.openai.com/v1/chat/completions", {"max_completion_tokens": 4097}),
        ("https://api.openai.com/v1/chat/completions", {"reasoning_effort": "high"}),
        ("https://api.openai.com/v1/chat/completions", {"temperature": 0}),
        ("https://api.openai.com/v1/chat/completions", {"seed": 0}),
    ],
)
def test_transport_configuration_drift_blocks_before_http(
    monkeypatch, tmp_path, url, changes
):
    monkeypatch.setattr(subject.httpx, "post", _forbidden)
    live = subject.Live(tmp_path, 1)
    with pytest.raises(
        llm.LlmDependencyError, match="DEVELOPMENT_CONFIGURATION_MISMATCH"
    ):
        live.request(url, json=_request_body(**changes))
    assert live.count == 0 and list(tmp_path.iterdir()) == []


def test_live_transport_failure_preserves_attempt_without_secret(monkeypatch, tmp_path):
    def post(*_args, **_kwargs):
        raise httpx.ConnectError("SECRET_DETAIL_SHOULD_NOT_LEAK")

    monkeypatch.setattr(subject.httpx, "post", post)
    live = subject.Live(tmp_path, 1)
    with pytest.raises(httpx.ConnectError):
        live.request("https://api.openai.com/v1/chat/completions", json=_request_body())
    record = json.loads((tmp_path / "response-001.json").read_text())
    assert record["usage_unobserved"] is True
    assert record["error_type"] == "ConnectError"
    assert "SECRET_DETAIL_SHOULD_NOT_LEAK" not in json.dumps(record)
    assert live.count == 1 and len(live.calls) == 1


def test_save_is_0600_no_clobber_and_rejects_symlink_target(tmp_path):
    subject.save(tmp_path, "case.json", {"original": True})
    target = tmp_path / "case.json"
    before = target.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        subject.save(tmp_path, "case.json", {"original": False})
    assert target.read_bytes() == before
    (tmp_path / "alias.json").symlink_to(target)
    with pytest.raises(FileExistsError):
        subject.save(tmp_path, "alias.json", {"changed": True})
    assert target.read_bytes() == before


@pytest.mark.parametrize("changed", ["endpoint", "key", "provider", "model", "effort"])
def test_configuration_requires_explicit_official_runtime(monkeypatch, changed):
    _configure(monkeypatch)
    if changed in {"endpoint", "key"}:
        monkeypatch.setattr(
            llm,
            "_resolve_endpoint",
            lambda: (
                "https://other.example/v1"
                if changed == "endpoint"
                else "https://api.openai.com/v1",
                "fake-key" if changed == "endpoint" else "",
            ),
        )
    elif changed == "provider":
        monkeypatch.setattr(llm, "LLM_PROVIDER", "local")
    elif changed == "model":
        monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "wrong-model")
    else:
        monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    with pytest.raises(ValueError, match="DEVELOPMENT_CONFIGURATION_MISMATCH"):
        subject.configuration()


def test_main_retains_failed_case_and_records_source_configuration_binding(
    monkeypatch, tmp_path
):
    cases = subject.CASES[:2]
    monkeypatch.setattr(subject, "CASES", cases)
    monkeypatch.setattr(subject, "configuration", lambda: {"model": subject.MODEL})
    monkeypatch.setattr(
        subject,
        "git",
        lambda *args: "a" * 40 if args[0] == "rev-parse" else " M synthetic",
    )
    monkeypatch.setattr(subject.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(subject.sys, "argv", ["probe_agent_quality.py", "--execute"])
    monkeypatch.setattr(subject.httpx, "post", _forbidden)
    visited = []

    def run_case(case, _complete):
        visited.append(case.case_id)
        if case is cases[0]:
            raise ValueError("PRIVATE_RAW_FAILURE")
        return {
            "investigation_completed": True,
            "stopped_before_external_effects": True,
        }

    monkeypatch.setattr(subject, "run_case", run_case)
    assert subject.main() == 1
    assert visited == [case.case_id for case in cases]
    plan = json.loads((tmp_path / "plan.json").read_text())
    result = json.loads((tmp_path / "result.json").read_text())
    failed = json.loads((tmp_path / f"case-{cases[0].case_id}.json").read_text())
    assert plan["git_revision"] == "a" * 40
    assert plan["working_tree_status"] == " M synthetic"
    assert plan["profile"]["read_cap"] == 24
    assert plan["configuration"]["model"] == subject.MODEL
    for name in (
        "backend/app/agent/react.py",
        "backend/app/agent/graph.py",
        "backend/app/common/llm.py",
        "backend/scripts/probe_agent_quality.py",
        "backend/tests/support/agent_quality_development_cases.py",
    ):
        assert plan["source_sha256"][name] == subject.sha(
            (subject.REPO / name).read_bytes()
        )
    assert failed["error_type"] == "ValueError"
    assert "PRIVATE_RAW_FAILURE" not in json.dumps(failed)
    assert result["all_cases_attempted"] is True
    assert result["official_u10_verdict_unchanged"] is True
    assert result["automated_quality_verdict"] == "NOT_GRADED_REQUIRES_EVIDENCE_REVIEW"
    assert result["source_changed_during_run"] is False


def test_source_drift_preserves_results_but_prevents_success(monkeypatch, tmp_path):
    monkeypatch.setattr(subject, "CASES", subject.CASES[:1])
    monkeypatch.setattr(subject, "configuration", lambda: {"model": subject.MODEL})
    monkeypatch.setattr(subject, "git", lambda *args: "a" * 40)
    monkeypatch.setattr(subject.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(subject.sys, "argv", ["probe_agent_quality.py", "--execute"])
    monkeypatch.setattr(subject.httpx, "post", _forbidden)

    def run_case(case, _complete):
        # Change the measured fingerprint, never an actual repository file.
        monkeypatch.setattr(subject, "sha", lambda raw: "b" * 64)
        return {
            "case_id": case.case_id,
            "investigation_completed": True,
            "stopped_before_external_effects": True,
        }

    monkeypatch.setattr(subject, "run_case", run_case)
    assert subject.main() == 1
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["source_changed_during_run"] is True
    assert result["all_cases_attempted"] is True
    assert result["cases"][0]["investigation_completed"] is True
    assert set(result["final_source_sha256"].values()) == {"b" * 64}


def test_suites_are_explicit_disjoint_and_preserve_all_cases():
    base = subject.selected_cases("base")
    holdout = subject.selected_cases("holdout")
    assert len(base) == len(holdout) == 8
    assert len(subject.selected_cases("all")) == 16
    assert not {case.case_id for case in base} & {case.case_id for case in holdout}
    assert subject.selected_cases("all") == (*base, *holdout)
    with pytest.raises(ValueError, match="DEVELOPMENT_SUITE_INVALID"):
        subject.selected_cases("unknown")
