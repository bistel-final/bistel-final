"""LLM 계획 생성 개선(V5-D-2.3 잔여) 서비스·클라이언트 unit test.

- self-correction: 검증 실패 → 사유 피드백 재생성 → 성공/재실패 경로
- metric_result heuristic
- llm.chat 의 429 backoff 재시도
전부 네트워크·DB 없이 monkeypatch 로 고정한다.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.analytics import service
from app.analytics.repository import QueryExecutionError
from app.common import llm
from app.common.tool_contracts import MetricPlan


class _StubPoolFactory:
    def get_engine(self, *args, **kwargs):  # noqa: D102 - stub
        return None


class TestComputeMetricResult:
    _COUNT_SQL = "SELECT count(*) AS cnt FROM trace_alarm_history"

    def test_count_projection_single_row_returns_value(self) -> None:
        metric = MetricPlan(type="count")
        rows = [{"cnt": 138}]
        assert service._compute_metric_result(metric, self._COUNT_SQL, rows) == 138.0

    def test_decimal_value_is_converted(self) -> None:
        metric = MetricPlan(type="count")
        rows = [{"cnt": Decimal("138")}]
        assert service._compute_metric_result(
            metric, self._COUNT_SQL, rows
        ) == pytest.approx(138.0)

    def test_non_count_projection_returns_none(self) -> None:
        # P2 리뷰 반영: metric 계획이 count 여도 SQL 이 일반 값 조회면
        # 값을 count KPI 로 둘갑시키지 않는다.
        metric = MetricPlan(type="count")
        sql = "SELECT value FROM trace_alarm_history LIMIT 1"
        rows = [{"value": Decimal("86.45")}]
        assert service._compute_metric_result(metric, sql, rows) is None

    def test_group_rows_return_none_not_group_count(self) -> None:
        # 그룹 결과의 행 수(=그룹 수)는 KPI 가 아니다 → None (P1 리뷰).
        metric = MetricPlan(type="count")
        sql = (
            "SELECT equipment, COUNT(*) AS cnt FROM summary_alarm_history"
            " GROUP BY equipment"
        )
        rows = [
            {"equipment": "EQP05", "cnt": 14},
            {"equipment": "EQP04", "cnt": 9},
        ]
        assert service._compute_metric_result(metric, sql, rows) is None

    def test_none_metric_returns_none(self) -> None:
        assert (
            service._compute_metric_result(None, self._COUNT_SQL, [{"cnt": 1}]) is None
        )


class TestSelfCorrection:
    """run_analysis_query 의 재생성 분기. 실행(DB)은 stub 으로 격리한다."""

    @pytest.fixture(autouse=True)
    def _isolate_execution(self, monkeypatch):
        # unit test 가 실제 DB·네트워크에 닿지 않도록 실행부를 막는다.
        monkeypatch.setattr(service, "pool_factory", _StubPoolFactory())

        def _no_execute(engine, sql):
            raise QueryExecutionError("쿼리 실행에 실패했다 (StubExecution).")

        monkeypatch.setattr(service, "execute_validated_select", _no_execute)

    def _patch_plan_sequence(self, monkeypatch, outputs: list[str]) -> list[str]:
        """llm.chat 이 호출마다 outputs 를 순서대로 반환하게 한다."""
        calls: list[str] = []

        def _chat(messages):
            calls.append(messages[-1]["content"])
            return outputs[min(len(calls), len(outputs)) - 1]

        monkeypatch.setattr(llm, "chat", _chat)
        return calls

    def test_invalid_sql_triggers_one_retry_with_reason(self, monkeypatch) -> None:
        # 1차: 없는 컬럼 → 검증 실패, 2차: 정상 SQL. 실행은 오류로 끝나도
        # 재생성이 채택됐는지(generated_sql·is_valid)로 판정한다.
        calls = self._patch_plan_sequence(
            monkeypatch,
            [
                "SELECT no_such_column FROM trace_alarm_history",
                "SELECT count(*) AS cnt FROM trace_alarm_history",
            ],
        )

        response = service.run_analysis_query("알람 총 몇 건이야?")

        assert len(calls) == 2
        # 2차 호출 프롬프트에 실패 사유가 피드백됐다
        assert "검증 실패 사유" in calls[1]
        # 재생성 SQL 이 채택돼 검증을 통과했다 (실행은 stub 오류로 끝나도
        # is_rejected=False + generated_sql 로 판정 가능)
        assert response.is_rejected is False
        assert response.generated_sql is not None
        assert "count" in response.generated_sql.lower()
        assert response.error_msg is not None  # stub 실행 오류 경로 확인

    def test_retry_failure_returns_rejection(self, monkeypatch) -> None:
        self._patch_plan_sequence(
            monkeypatch,
            [
                "SELECT no_such_column FROM trace_alarm_history",
                "SELECT still_wrong FROM trace_alarm_history",
            ],
        )

        response = service.run_analysis_query("알람 총 몇 건이야?")

        assert response.is_rejected is True
        assert response.reject_reason is not None
        assert response.reject_reason.startswith("POLICY_REJECTED:")
        # P2 리뷰 반영: 거부 사유는 첫 시도가 아니라 마지막 시도의 것
        assert "still_wrong" in response.reject_reason
        assert "no_such_column" not in response.reject_reason

    def test_passthrough_is_rejected_without_retry(self, monkeypatch) -> None:
        # 사용자가 직접 준 SQL 은 재해석하지 않는다 — LLM 호출 0회.
        calls = self._patch_plan_sequence(monkeypatch, ["SELECT 1"])

        response = service.run_analysis_query("DELETE FROM action_history")

        assert response.is_rejected is True
        assert calls == []


class TestChatRetry:
    def _response(self, status: int) -> httpx.Response:
        return httpx.Response(
            status_code=status,
            json={"choices": [{"message": {"content": "SELECT 1"}}]},
        )

    def test_429_is_retried_then_succeeds(self, monkeypatch) -> None:
        statuses = iter([429, 200])
        posts: list[int] = []

        def _post(*args, **kwargs):
            status = next(statuses)
            posts.append(status)
            return self._response(status)

        monkeypatch.setattr(llm.httpx, "post", _post)
        monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")

        content = llm.chat([{"role": "user", "content": "q"}])

        assert posts == [429, 200]
        assert content == "SELECT 1"

    def test_retries_exhausted_raises_dependency_error(self, monkeypatch) -> None:
        def _post(*args, **kwargs):
            return self._response(429)

        monkeypatch.setattr(llm.httpx, "post", _post)
        monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_RETRY_MAX", "1")

        with pytest.raises(llm.LlmDependencyError, match="429"):
            llm.chat([{"role": "user", "content": "q"}])

    def test_client_error_is_not_retried(self, monkeypatch) -> None:
        posts: list[int] = []

        def _post(*args, **kwargs):
            posts.append(400)
            return self._response(400)

        monkeypatch.setattr(llm.httpx, "post", _post)
        monkeypatch.setattr(llm, "LLM_PROVIDER", "ollama")

        with pytest.raises(llm.LlmDependencyError):
            llm.chat([{"role": "user", "content": "q"}])

        assert posts == [400]
