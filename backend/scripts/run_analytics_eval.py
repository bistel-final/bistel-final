"""V5-D-2.5 Text2SQL 평가 runner — FR-D-08.

질문셋(analytics_eval/questionset_fdc_final.json)의 자연어 질문을 실제
파이프라인(run_analysis_query: LLM 계획 → 검증 → readonly 실행)에 넣고,
같은 경로의 SQL passthrough 로 실행한 gold_sql 결과와 비교해 채점한다.

채점 기준 (질문셋 grading_criteria 와 동일 — artifact 에도 기록된다)
    값 정규화  Decimal→float, date→ISO 날짜, 자정 datetime→해당 tz 의 ISO
              날짜로 접기(그 외 datetime 은 UTC ISO). date_trunc 답과
              CAST(.. AS DATE) 답이 같은 값으로 비교된다.
    행 비교    행 다중집합. 컬럼 이름·순서는 채점하지 않되, 하나의 컬럼
              순열을 전 행에 일관 적용해야 한다(행마다 다른 뒤섞임은 오답).
    수치 오차  실수 rel/abs 1e-6 (math.isclose)
    ordered   다중집합 일치 + sort_check 정렬축 단조성(동률 내 순서 미채점)
    차트       expected_chart 명시 질문만 chart_type 일치 채점
    통과선     12건 이상 중 10건 이상 정답
    실패 기록  컬럼 구성 차이 → 처음 어긋난 행 쌍 순으로 원인을 명시한다

실행 (backend/ 에서, LLM·평가 DSN 필요):
    .venv/bin/python scripts/run_analytics_eval.py
결과: artifacts/analytics_eval/result_<UTC타임스탬프>.json
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import permutations
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.eval_fingerprint import compute_fingerprint  # noqa: E402
from app.analytics.service import run_analysis_query  # noqa: E402
from app.analytics.tools import PROMPT_VERSION  # noqa: E402
from app.common.config import (  # noqa: E402
    LLM_MODEL_MAIN,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
)

QUESTIONSET_PATH = (
    BACKEND_ROOT / "artifacts" / "analytics_eval" / "questionset_fdc_final.json"
)
RESULT_DIR = BACKEND_ROOT / "artifacts" / "analytics_eval"
PASS_THRESHOLD = 10
_TOL = 1e-6
_MAX_PERM_COLUMNS = 6


# ── 값 정규화 ──────────────────────────────────────────────────────────
def _norm_value(value: object) -> object:
    """의미 비교를 위한 정규화. 타입 표기가 아니라 값을 비교한다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        # 자정이면 그 timezone 기준 날짜로 접는다 — date_trunc('day') 답과
        # ::date 답이 같은 값이 된다. 자정이 아니면 UTC ISO 로 통일한다.
        if value.hour == value.minute == value.second == value.microsecond == 0:
            return value.date().isoformat()
        base = value if value.tzinfo is None else value.astimezone(UTC)
        return base.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int | float):
        return value
    return str(value) if value is not None else None


def _values_close(a: object, b: object) -> bool:
    na, nb = _norm_value(a), _norm_value(b)
    if (
        isinstance(na, int | float)
        and isinstance(nb, int | float)
        and not isinstance(na, bool)
        and not isinstance(nb, bool)
    ):
        return math.isclose(float(na), float(nb), rel_tol=_TOL, abs_tol=_TOL)
    return na == nb


# ── 행 비교 (일관된 컬럼 순열 허용) ────────────────────────────────────
def _tuples_of(rows: list[dict]) -> list[tuple]:
    return [tuple(row.values()) for row in rows]


def _tuple_close(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(
        _values_close(x, y) for x, y in zip(a, b, strict=True)
    )


def _multiset_match(gold: list[tuple], got: list[tuple]) -> bool:
    """다중집합 일치 — 실수 오차를 허용하는 greedy 매칭."""
    if len(gold) != len(got):
        return False
    remaining = list(got)
    for g in gold:
        hit = next((i for i, c in enumerate(remaining) if _tuple_close(g, c)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _match_with_permutation(
    gold_rows: list[dict], got_rows: list[dict]
) -> tuple[bool, tuple[int, ...] | None]:
    """하나의 컬럼 순열을 전 행에 일관 적용해 다중집합 일치를 찾는다.

    (matched, permutation) 을 돌려준다. 행마다 다른 뒤섞임은 허용하지
    않는다 — {cnt:3,total:5} 와 {cnt:5,total:3} 는 두 행 이상에서 일관
    순열이 없으면 오답이다.
    """
    gold = _tuples_of(gold_rows)
    got = _tuples_of(got_rows)
    if len(gold) != len(got):
        return False, None
    if not gold:
        return True, ()
    width = len(gold[0])
    if width != len(got[0]) or width > _MAX_PERM_COLUMNS:
        return False, None
    for perm in permutations(range(width)):
        permuted = [tuple(row[i] for i in perm) for row in got]
        if _multiset_match(gold, permuted):
            return True, perm
    return False, None


def _first_mismatch_pair(gold_rows: list[dict], got_rows: list[dict]) -> str:
    """처음 어긋난 행 한 쌍을 정규화 값으로 보여준다 (원인 판독용)."""
    got_left = [tuple(row.values()) for row in got_rows]
    for idx, grow in enumerate(gold_rows):
        gvals = tuple(grow.values())
        hit = next((i for i, c in enumerate(got_left) if _tuple_close(gvals, c)), None)
        if hit is None:
            gen_repr = (
                {k: _norm_value(v) for k, v in got_rows[idx].items()}
                if idx < len(got_rows)
                else "(대응 행 없음)"
            )
            return (
                f"처음 어긋난 행: gold[{idx}]="
                f"{ {k: _norm_value(v) for k, v in grow.items()} }"
                f" ↔ generated[{idx}]={gen_repr}"
            )
        got_left.pop(hit)
    return "어긋난 행을 특정하지 못했다"


def _column_note(gold_rows: list[dict], got_rows: list[dict]) -> str | None:
    g_cols = list(gold_rows[0].keys()) if gold_rows else []
    h_cols = list(got_rows[0].keys()) if got_rows else []
    if len(g_cols) != len(h_cols):
        return (
            f"컬럼 수 불일치: gold {len(g_cols)}열{g_cols}"
            f" vs generated {len(h_cols)}열{h_cols}"
        )
    if set(g_cols) != set(h_cols):
        return f"컬럼 이름 차이(채점 무관, 참고): gold {g_cols} vs generated {h_cols}"
    return None


def _is_numeric(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _sort_axis_ok(rows: list[dict], sort_check: str) -> bool:
    """정렬축 단조성 검증 — 동률 내 순서는 채점하지 않는다."""
    if not rows:
        return True
    keys = list(rows[0].keys())
    if sort_check == "numeric_desc":
        axis = [k for k in keys if _is_numeric(rows[0][k])]
    else:
        axis = [k for k in keys if not _is_numeric(rows[0][k])]
    if len(axis) != 1:
        return False  # 축을 특정할 수 없으면 보수적으로 실패 처리
    values = [_norm_value(row[axis[0]]) for row in rows]
    if sort_check == "numeric_desc":
        return all(
            float(a) >= float(b) - _TOL
            for a, b in zip(values, values[1:], strict=False)
        )
    svals = [str(v) for v in values]
    return all(a <= b for a, b in zip(svals, svals[1:], strict=False))


def _diagnose_rows(
    gold_rows: list[dict], got_rows: list[dict], label: str
) -> list[str]:
    """실패 원인을 스스로 설명하는 detail 을 만든다 (필수 1)."""
    lines: list[str] = []
    if len(gold_rows) != len(got_rows):
        lines.append(
            f"{label} 불일치: 행 수 gold {len(gold_rows)} vs generated {len(got_rows)}"
        )
        return lines
    note = _column_note(gold_rows, got_rows)
    if note and "컬럼 수 불일치" in note:
        lines.append(f"{label} 불일치 — {note}")
        return lines
    lines.append(f"{label} 불일치 (행 수 {len(gold_rows)} 동일, 값 차이)")
    if note:
        lines.append(note)
    lines.append(_first_mismatch_pair(gold_rows, got_rows))
    return lines


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
    if mode == "chart":
        # 결과 셰이프가 다중 정답인 질문 — 미거부·행 존재·차트만 채점한다
        if not gen.rows:
            ok = False
            detail.append("chart 모드: 생성 질의 결과가 0행이다")
    elif mode == "scalar":
        gv = list(gold.rows[0].values())[0] if gold.rows else None
        hv = list(gen.rows[0].values())[0] if gen.rows else None
        # metric_result 로 답한 경우도 인정한다 (계획이 집계를 metric 으로 옮긴 경우)
        if hv is None and gen.metric_result is not None:
            hv = gen.metric_result
        if not _values_close(gv, hv):
            ok = False
            detail.append(
                f"scalar 불일치: gold={_norm_value(gv)!r} generated={_norm_value(hv)!r}"
            )
    else:
        matched, _perm = _match_with_permutation(gold.rows, gen.rows)
        if not matched:
            ok = False
            detail.extend(_diagnose_rows(gold.rows, gen.rows, mode))
        elif mode == "ordered":
            sort_check = question.get("sort_check")
            if sort_check and not _sort_axis_ok(gen.rows, sort_check):
                ok = False
                detail.append(f"정렬축({sort_check}) 단조성 위반")

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
                "sort_check": q.get("sort_check"),
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
        # 실행 환경 미터 — GET /analytics/evaluations 계약(provider·model·temperature·
        # prompt_version)의 근거. 이 단계 이전 artifact 는 adapter 가 unknown 으로 본다.
        "llm": {
            "provider": LLM_PROVIDER,
            "model": LLM_MODEL_MAIN,
            "temperature": LLM_TEMPERATURE,
            "prompt_version": PROMPT_VERSION,
        },
        # 구성 지문 (#304) — "이 성적표가 지금 코드 기준인가"를 기계가 판단하는 근거.
        # 프롬프트 원문·질문셋·매니페스트·모델 중 하나라도 바뀜면 지문이 달라지고 재평가 대상이 된다.
        "fingerprint": compute_fingerprint(),
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
