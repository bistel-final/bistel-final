"""평가 artifact → API 계약 projection (V5-D-2.6, read-only).

`run_analytics_eval.py` 가 남긴 immutable artifact(artifacts/analytics_eval/
result_*.json)를 명세 계약(EvaluationListResponse)으로 **형태만 변환**한다.
채점 로직은 재구현하지 않는다 — 러너가 정본이고, 여기는 adapter 다.

계약 이행 방식:
    validate    — 파일명 규격·JSON·필수 키·pydantic 계약을 통과한 실행만 노출.
                  깨진 파일은 경고 로그 후 제외한다 (fail-closed: 의심스러운
                  artifact 를 그럴싸하게 보여주지 않는다)
    stable sort — executed_at DESC, run_id DESC (명세 정렬 그대로)
    page        — page·size 로 자른다. latest=true 면 최신 1건만

DEFENSE 판정: artifact 는 거부 여부를 generated_rejected 로만 기록하므로,
거부가 채점된 케이스를 DEFENSE, 나머지를 GOLD 로 본다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.analytics.schemas import (
    EvaluationItem,
    EvaluationListResponse,
    EvaluationResponse,
)
from app.common.tool_contracts import VisualizationPlan

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = BACKEND_ROOT / "artifacts" / "analytics_eval"

_RUN_FILE_RE = re.compile(r"^result_(\d{8}T\d{6}Z)\.json$")
_UNKNOWN = "unknown"  # llm 메타가 기록되기 전(구 artifact)의 표기


def _visualization(chart: Any) -> VisualizationPlan | None:
    if not chart:
        return None
    try:
        return VisualizationPlan(chart_type=chart)
    except ValidationError:
        return None


def _project_item(result: dict[str, Any]) -> EvaluationItem:
    rejected = bool(result.get("generated_rejected"))
    passed = bool(result.get("pass"))
    detail = result.get("detail") or []
    reason = "; ".join(str(line) for line in detail) if detail and not passed else None
    return EvaluationItem(
        case_type="DEFENSE" if rejected else "GOLD",
        case_id=str(result["id"]),
        question=result.get("question"),
        passed=passed,
        generated_sql=result.get("generated_sql"),
        attempt_count=int(result.get("attempt_count", 1)),
        expected_result=result.get("gold_sql"),
        actual_result=detail or None,
        expected_visualization=_visualization(result.get("expected_chart")),
        actual_visualization=_visualization(result.get("generated_chart")),
        reason=reason,
        latency_ms=result.get("latency_ms"),
    )


def project_run(run_id: str, payload: dict[str, Any]) -> EvaluationResponse:
    """artifact 한 건을 계약으로 투영한다. 계약 위반은 ValidationError 로 올라간다."""
    llm = payload.get("llm") or {}
    items = [_project_item(result) for result in payload["results"]]
    total = int(payload["total"])
    correct = int(payload["passed"])
    defense = [item for item in items if item.case_type == "DEFENSE"]
    return EvaluationResponse(
        run_id=run_id,
        executed_at=datetime.fromisoformat(payload["executed_at"]),
        provider=str(llm.get("provider") or _UNKNOWN),
        model=str(llm.get("model") or _UNKNOWN),
        temperature=float(llm.get("temperature", 0.0)),
        prompt_version=str(llm.get("prompt_version") or _UNKNOWN),
        correct=correct,
        total=total,
        accuracy=(correct / total) if total else 0.0,
        defense_passed=sum(1 for item in defense if item.passed),
        defense_total=len(defense),
        items=items,
    )


def load_runs(result_dir: Path | None = None) -> list[EvaluationResponse]:
    """디렉터리의 유효한 artifact 를 전부 읽어 정렬한다 (stable, 명세 순).

    result_dir 기본은 호출 시점의 RESULT_DIR — 테스트가 경로를 바꿀 수 있게 한다.
    """
    if result_dir is None:
        result_dir = RESULT_DIR
    runs: list[EvaluationResponse] = []
    if not result_dir.exists():
        return runs
    for path in result_dir.iterdir():
        match = _RUN_FILE_RE.match(path.name)
        if match is None:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            runs.append(project_run(match.group(1), payload))
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            logger.warning("평가 artifact 제외 — 계약 불일치 %s: %s", path.name, exc)
    runs.sort(key=lambda run: (run.executed_at, run.run_id), reverse=True)
    return runs


def list_evaluations(
    *, latest: bool = False, page: int = 1, size: int = 20
) -> EvaluationListResponse:
    runs = load_runs()
    if latest:
        runs = runs[:1]
    start = (page - 1) * size
    return EvaluationListResponse(
        items=runs[start : start + size], total=len(runs), page=page, size=size
    )
