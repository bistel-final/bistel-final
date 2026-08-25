"""V5-D-2.5 Text2SQL 평가 runner — FR-D-08.

질문셋(analytics_eval/questionset_fdc_final.json)의 자연어 질문을 실제
파이프라인(run_analysis_query: LLM 계획 → 검증 → readonly 실행)에 넣고,
같은 경로의 SQL passthrough 로 실행한 gold_sql 결과와 비교해 채점한다.

채점 기준 (질문셋 grading_criteria 와 동일 — artifact 에도 기록된다)
    행 비교   행 다중집합. 컬럼 순서 미채점(행 내 값 다중집합).
             mode=ordered 만 행 순서 채점
    수치 오차 실수 rel/abs 1e-6 (math.isclose)
    차트     expected_chart 명시 질문만 chart_type 일치 채점
    통과선   12건 이상 중 10건 이상 정답

실행 (backend/ 에서, LLM·평가 DSN 필요):
    .venv/bin/python scripts/run_analytics_eval.py
결과: artifacts/analytics_eval/result_<UTC타임스탬프>.json
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.service import run_analysis_query  # noqa: E402

QUESTIONSET_PATH = (
    BACKEND_ROOT / "artifacts" / "analytics_eval" / "questionset_fdc_final.json"
)
RESULT_DIR = BACKEND_ROOT / "artifacts" / "analytics_eval"
PASS_THRESHOLD = 10
_TOL = 1e-6


def _norm_value(value: object) -> object:
    """비교용 정규화 — 실수는 근사 비교를 위해 그대로 두고 타입만 정돈한다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return str(value) if value is not None else None


def _values_close(a: object, b: object) -> bool:
    if (
        isinstance(a, int | float)
        and isinstance(b, int | float)
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        return math.isclose(float(a), float(b), rel_tol=_TOL, abs_tol=_TOL)
    return a == b


def _row_multiset(row: dict) -> tuple:
    """행 내 값 다중집합 — 컬럼 순서·이름을 채점하지 않기 위한 표현."""
    return tuple(
        sorted((repr(type(v).__name__), repr(_norm_value(v))) for v in row.values())
    )


def _rows_match_unordered(gold: list[dict], got: list[dict]) -> bool:
    if len(gold) != len(got):
        return False
    # 1차: 표현 다중집합 완전 일치
    if Counter(map(_row_multiset, gold)) == Counter(map(_row_multiset, got)):
        return True
    # 2차: 실수 오차 허용 매칭 (greedy)
    remaining = [list(r.values()) for r in got]
    for grow in gold:
        gvals = list(grow.values())
        hit = next(
            (i for i, cand in enumerate(remaining) if _row_values_close(gvals, cand)),
            None,
        )
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _row_values_close(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    used = [False] * len(b)
    for x in a:
        hit = next(
            (
                i
                for i, y in enumerate(b)
                if not used[i] and _values_close(_norm_value(x), _norm_value(y))
            ),
            None,
        )
        if hit is None:
            return False
        used[hit] = True
    return True


def _rows_match_ordered(gold: list[dict], got: list[dict]) -> bool:
    if len(gold) != len(got):
        return False
    return all(
        _row_values_close(list(g.values()), list(h.values()))
        for g, h in zip(gold, got, strict=True)
    )


def _is_numeric(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _sort_axis_ok(rows: list[dict], sort_check: str) -> bool:
    """정렬축 단조성 검증 — 동률 내 순서는 채점하지 않는다.

    numeric_desc: (유일한) 숫자 컬럼 값이 비증가순
    string_asc  : (유일한) 비숫자 컬럼 값이 문자열 비감소순 (ISO 시각 포함)
    """
    if not rows:
        return True
    keys = list(rows[0].keys())
    if sort_check == "numeric_desc":
        axis = [k for k in keys if _is_numeric(rows[0][k])]
    else:
        axis = [k for k in keys if not _is_numeric(rows[0][k])]
    if len(axis) != 1:
        return False  # 축을 특정할 수 없으면 보수적으로 실패 처리
    values = [row[axis[0]] for row in rows]
    if sort_check == "numeric_desc":
        return all(
            float(a) >= float(b) - _TOL
            for a, b in zip(values, values[1:], strict=False)
        )
    svals = [str(v) for v in values]
    return all(a <= b for a, b in zip(svals, svals[1:], strict=False))


def _grade(question: dict, gold, gen) -> dict:
    detail: list[str] = []
    ok = True

    if gen.is_rejected or gen.error_msg:
        return {
            "pass": False,
            "detail": [f"생성 질의 실패: {gen.reject_reason or gen.error_msg}"],
        }
    if gold.is_rejected or gold.error_msg:
        return {
            "pass": False,
            "detail": [
                f"gold 실행 실패(질문셋 결함): {gold.reject_reason or gold.error_msg}"
            ],
        }

    mode = question["mode"]
    if mode == "scalar":
        gv = list(gold.rows[0].values())[0] if gold.rows else None
        hv = list(gen.rows[0].values())[0] if gen.rows else None
        # metric_result 로 답한 경우도 인정한다 (계획이 집계를 metric 으로 옮긴 경우)
        if hv is None and gen.metric_result is not None:
            hv = gen.metric_result
        if not _values_close(_norm_value(gv), _norm_value(hv)):
            ok = False
            detail.append(f"scalar 불일치: gold={gv!r} generated={hv!r}")
    elif mode == "ordered":
        sort_check = question.get("sort_check")
        if not _rows_match_unordered(gold.rows, gen.rows):
            ok = False
            detail.append(
                f"ordered(다중집합) 불일치: gold {len(gold.rows)}행"
                f" vs generated {len(gen.rows)}행"
            )
        elif sort_check and not _sort_axis_ok(gen.rows, sort_check):
            ok = False
            detail.append(f"정렬축({sort_check}) 단조성 위반")
        elif not sort_check and not _rows_match_ordered(gold.rows, gen.rows):
            ok = False
            detail.append(
                f"ordered 불일치: gold {len(gold.rows)}행"
                f" vs generated {len(gen.rows)}행"
            )
    else:  # set
        if not _rows_match_unordered(gold.rows, gen.rows):
            ok = False
            detail.append(
                f"set 불일치: gold {len(gold.rows)}행 vs generated {len(gen.rows)}행"
            )

    expected_chart = question.get("expected_chart")
    if expected_chart:
        got_chart = gen.visualization.chart_type.value if gen.visualization else None
        if got_chart != expected_chart:
            ok = False
            detail.append(f"chart 불일치: expected={expected_chart} got={got_chart}")

    return {"pass": ok, "detail": detail}


def main() -> int:
    spec = json.loads(QUESTIONSET_PATH.read_text(encoding="utf-8"))
    questions = spec["questions"]

    results = []
    passed = 0
    for q in questions:
        gold = run_analysis_query(q["gold_sql"])  # SQL passthrough — LLM 미사용
        gen = run_analysis_query(q["question"])  # LLM 계획 경로
        grade = _grade(q, gold, gen)
        passed += 1 if grade["pass"] else 0
        results.append(
            {
                "id": q["id"],
                "question": q["question"],
                "mode": q["mode"],
                "expected_chart": q.get("expected_chart"),
                "gold_sql": q["gold_sql"],
                "generated_sql": gen.generated_sql,
                "generated_chart": gen.visualization.chart_type.value
                if gen.visualization
                else None,
                "generated_rejected": gen.is_rejected,
                "pass": grade["pass"],
                "detail": grade["detail"],
                "latency_ms": gen.latency_ms,
            }
        )
        mark = "PASS" if grade["pass"] else "FAIL"
        print(f"[{mark}] {q['id']} {q['question']}")
        for line in grade["detail"]:
            print(f"       - {line}")

    total = len(questions)
    summary = {
        "questionset_id": spec["questionset_id"],
        "dataset_epoch": spec["dataset_epoch"],
        "executed_at": datetime.now(UTC).isoformat(),
        "grading_criteria": spec["grading_criteria"],
        "total": total,
        "passed": passed,
        "pass_threshold": PASS_THRESHOLD,
        "meets_threshold": passed >= PASS_THRESHOLD and total >= 12,
        "results": results,
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULT_DIR / f"result_{stamp}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = "통과" if summary["meets_threshold"] else "미달"
    print(
        f"\n== 결과: {passed}/{total} 정답 (기준 {PASS_THRESHOLD}/{total}) → {verdict}"
    )
    print(f"== artifact: {out.relative_to(BACKEND_ROOT)}")
    return 0 if summary["meets_threshold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
