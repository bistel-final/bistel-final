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
        # group by 컴럼이 추출되면 범주 축 메타데이터와 함께 bar 를 고른다
        assert result.group_by == ["eqp_id"]
        assert result.visualization is not None
        assert result.visualization.chart_type == "bar"

    def test_group_by_without_extractable_column_falls_back_to_table(
        self, monkeypatch
    ) -> None:
        # 위치 번호 GROUP BY 는 컴럼명을 줄 수 없다 — 차트 지정과 메타데이터가
        # 모순되지 않도록 TABLE 로 내려야 한다 (P2 리뷰 반영).
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: (
                "SELECT equipment, COUNT(*) FROM summary_alarm_history GROUP BY 1"
            ),
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is True
        assert result.group_by == []
        assert result.visualization is not None
        assert result.visualization.chart_type == "table"

    def test_group_by_returns_projection_alias_not_source_column(
        self, monkeypatch
    ) -> None:
        # rows 의 키는 alias(equipment)다 — group_by 도 같은 키여야 차트가
        # 범주 축을 찾는다 (P2 리뷰 반영).
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: (
                "SELECT eqp_id AS equipment, COUNT(*) AS cnt"
                " FROM trace_alarm_history GROUP BY eqp_id"
            ),
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is True
        assert result.group_by == ["equipment"]
        assert result.visualization is not None
        assert result.visualization.chart_type == "bar"

    def test_join_with_same_column_name_keeps_both_axes(self, monkeypatch) -> None:
        # 테이블 수식어를 버리면 a.equipment / b.equipment 가 뭉개져 뒤 alias
        # 가 앞을 덮는다 — 둘 다 제 키로 남아야 한다 (P2 리뷰 반영).
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: (
                "SELECT a.equipment AS trace_eqp, b.equipment AS summary_eqp,"
                " COUNT(*) AS cnt"
                " FROM trace_alarm_history AS a"
                " JOIN summary_alarm_history AS b ON a.lot = b.lot"
                " GROUP BY a.equipment, b.equipment"
            ),
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is True
        assert result.group_by == ["trace_eqp", "summary_eqp"]
        assert result.visualization is not None
        assert result.visualization.chart_type == "bar"

    def test_quoted_alias_preserves_case_in_group_by(self, monkeypatch) -> None:
        # PostgreSQL 은 인용 식별자의 대소문자를 보존한다 — rows 의 키는
        # "Equipment" 이므로 group_by 도 같은 표기여야 한다 (P2 리뷰 반영).
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: (
                'SELECT equipment AS "Equipment", COUNT(*) AS cnt'
                " FROM summary_alarm_history GROUP BY equipment"
            ),
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is True
        assert result.group_by == ["Equipment"]

    def test_group_by_column_missing_from_projection_is_excluded(
        self, monkeypatch
    ) -> None:
        # GROUP BY 컴럼이 SELECT 에 안 나오면 rows 에 그 키가 없다 —
        # 축으로 쓸 수 없으므로 제외하고 TABLE 로 내린다.
        monkeypatch.setattr(
            llm,
            "chat",
            lambda messages: (
                "SELECT COUNT(*) AS cnt" " FROM trace_alarm_history GROUP BY eqp_id"
            ),
        )

        result = tools.generate_analysis_plan(_ask())

        assert result.ok is True
        assert result.group_by == []
        assert result.visualization is not None
        assert result.visualization.chart_type == "table"

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

    def test_refused_marker_becomes_policy_rejected(self, monkeypatch) -> None:
        # 프롬프트 규칙 1: 비조회 요청은 REFUSED 마커로 온다 — 조회로 암묵
        # 변환하지 않고 "수행되지 않았다"를 명시해 사용자 혼란을 막는다.
        monkeypatch.setattr(
            llm, "chat", lambda messages: "REFUSED: 조회 질문만 처리한다"
        )

        result = tools.generate_analysis_plan(_ask("테이블 전부 지워줘"))

        assert result.ok is False
        assert result.reason.startswith("POLICY_REJECTED:")
        assert "수행되지 않았다" in result.reason
        assert result.sql is None

    def test_retry_feedback_is_appended_as_user_message(self, monkeypatch) -> None:
        captured: dict[str, list[dict[str, str]]] = {}

        def _capture(messages):
            captured["messages"] = messages
            return "SELECT count(*) AS cnt FROM trace_alarm_history"

        monkeypatch.setattr(llm, "chat", _capture)

        tools.generate_analysis_plan(
            _ask(), retry_feedback="직전 SQL: SELECT bad\n검증 실패 사유: 없는 컴럼"
        )

        assert len(captured["messages"]) == 3
        feedback = captured["messages"][2]
        assert feedback["role"] == "user"
        assert "검증 실패 사유" in feedback["content"]
        assert "다시 작성" in feedback["content"]


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
