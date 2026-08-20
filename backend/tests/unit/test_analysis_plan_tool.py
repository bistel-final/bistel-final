"""V5-D-2.3 generate_analysis_plan Tool unit test.

LLM 호출(app.common.llm.chat)을 monkeypatch 로 대체해 네트워크 없이
성공·미준비·timeout·파싱 실패 경로를 고정한다.
"""

from __future__ import annotations

import pytest

from app.analytics import tools
from app.common import llm
from app.common.tool_contracts import AnalysisPlanToolInput


def _ask(question: str = "알람이 가장 많은 설비는?") -> AnalysisPlanToolInput:
    return AnalysisPlanToolInput(question=question)


class TestGenerateAnalysisPlan:
    def test_success_extracts_sql_from_code_fence(self, monkeypatch) -> None:
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: (
                "```sql\nSELECT eqp_id, COUNT(*) AS cnt\n"
                "FROM trace_alarm_history GROUP BY eqp_id\n```"
            ),
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is True
        assert result.sql is not None
        assert result.sql.lower().startswith("select")
        assert "```" not in result.sql
        # group by 가 있으면 heuristic 이 bar 를 고른다
        assert result.visualization is not None
        assert result.visualization.chart_type == "bar"

    def test_success_accepts_raw_sql_without_fence(self, monkeypatch) -> None:
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: "SELECT count(*) AS cnt FROM trace_alarm_history;",
        )

        result = tools.generate_analysis_plan(_ask("trace 알람 총 건수"))

        assert result.ok is True
        assert result.sql == "SELECT count(*) AS cnt FROM trace_alarm_history"
        assert result.visualization is not None
        assert result.visualization.chart_type == "table"

    def test_llm_not_ready_is_contract_failure_not_exception(self, monkeypatch) -> None:
        def _raise(messages):
            raise llm.LlmNotReadyError("LLM_API_KEY 가 설정되지 않았다.")

        monkeypatch.setattr(llm, "chat", _raise)

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is False
        assert result.reason.startswith("LLM_NOT_READY:")
        assert result.sql is None

    def test_llm_timeout_maps_to_timeout_prefix(self, monkeypatch) -> None:
        def _raise(messages):
            raise llm.LlmTimeoutError("응답 없음")

        monkeypatch.setattr(llm, "chat", _raise)

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is False
        assert result.reason.startswith("TIMEOUT:")

    def test_non_sql_output_is_dependency_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: "죄송합니다. 해당 정보를 찾을 수 없습니다.",
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is False
        assert result.reason.startswith("DEPENDENCY_ERROR:")

    def test_write_statement_output_is_not_accepted_as_sql(self, monkeypatch) -> None:
        # LLM 이 쓰기 구문을 내놓으면 Tool 단계에서 SELECT/WITH 아님으로 거른다.
        # (통과했더라도 validator 가 2차로 막는다.)
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: "DELETE FROM action_history",
        )

        result = tools.generate_analysis_plan(_ask("action 지워줘"))

        assert result.ok is False
        assert result.reason.startswith("DEPENDENCY_ERROR:")

    def test_prompt_contains_allowlist_but_not_ground_truth(self, monkeypatch) -> None:
        captured: dict[str, list[dict[str, str]]] = {}

        def _capture(messages):
            captured["messages"] = messages
            return "SELECT count(*) AS cnt FROM trace_alarm_history"

        monkeypatch.setattr(llm, "chat", _capture)

        tools.generate_analysis_plan(_ask())

        system_prompt = captured["messages"][0]["content"]
        assert "trace_alarm_history" in system_prompt
        assert "ground_truth" not in system_prompt


class TestResolveEndpoint:
    def test_external_provider_without_key_raises_not_ready(self, monkeypatch) -> None:
        monkeypatch.setattr(llm, "LLM_PROVIDER", "openai")
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(llm.LlmNotReadyError):
            llm._resolve_endpoint()

    def test_ollama_needs_no_key(self, monkeypatch) -> None:
        monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        base_url, api_key = llm._resolve_endpoint()

        assert base_url.endswith("/v1")
        assert api_key  # dummy 라도 값은 있다
