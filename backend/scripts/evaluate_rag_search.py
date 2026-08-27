"""V5-B-4.3 RAG 검색 품질 평가 runner.

Tool과 API가 공통으로 사용하는 ``DocumentSearchService``를 직접 호출한다.
따라서 이 runner는 SQL·임베딩·model_code 필터를 별도로 복제하지 않는다.

실행 (backend/에서):
  .venv/Scripts/python scripts/evaluate_rag_search.py
  .venv/Scripts/python scripts/evaluate_rag_search.py --validate-only

결과: backend/artifacts/rag_eval/result_<UTC timestamp>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory  # noqa: E402
from app.knowledge.document_search import DocumentSearchRepository  # noqa: E402
from app.knowledge.service import DocumentSearchService  # noqa: E402

QUESTIONSET_PATH = BACKEND_ROOT / "artifacts" / "rag_eval" / "questionset_cs2.json"
RESULT_DIR = BACKEND_ROOT / "artifacts" / "rag_eval"


class RagEvaluationError(ValueError):
    """질문셋 또는 평가 실행 오류."""


def load_questionset(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RagEvaluationError(f"질문셋 파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RagEvaluationError(f"질문셋 JSON 형식 오류: {exc}") from exc

    questions = payload.get("questions")
    criteria = payload.get("grading_criteria")
    if not isinstance(questions, list) or not questions:
        raise RagEvaluationError("questions는 비어 있지 않은 배열이어야 합니다")
    if not isinstance(criteria, dict):
        raise RagEvaluationError("grading_criteria 객체가 필요합니다")

    required = {
        "id",
        "audience",
        "query",
        "model_code",
        "expected_targets",
    }
    ids: set[str] = set()
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise RagEvaluationError(f"questions[{index}]는 객체여야 합니다")
        missing = required - set(question)
        if missing:
            raise RagEvaluationError(f"questions[{index}] 필수 field 누락: {sorted(missing)}")
        if question["id"] in ids:
            raise RagEvaluationError(f"질문 ID 중복: {question['id']}")
        ids.add(question["id"])
        if question["audience"] not in {"user", "agent"}:
            raise RagEvaluationError(f"{question['id']}: audience는 user 또는 agent여야 합니다")
        if not isinstance(question["query"], str) or not question["query"].strip():
            raise RagEvaluationError(f"{question['id']}: query는 비어 있을 수 없습니다")
        if question["model_code"] is not None and not isinstance(question["model_code"], str):
            raise RagEvaluationError(f"{question['id']}: model_code는 문자열 또는 null이어야 합니다")
        targets = question["expected_targets"]
        if not isinstance(targets, list) or not targets:
            raise RagEvaluationError(f"{question['id']}: expected_targets는 비어 있지 않은 배열이어야 합니다")
        for target in targets:
            if not isinstance(target, dict) or set(target) != {"document_id", "section"}:
                raise RagEvaluationError(
                    f"{question['id']}: 각 expected_target은 document_id와 section만 가져야 합니다"
                )
            if not all(isinstance(target[field], str) and target[field] for field in target):
                raise RagEvaluationError(
                    f"{question['id']}: expected_target의 document_id와 section은 비어 있을 수 없습니다"
                )

    minimum = int(criteria.get("minimum_question_count", 0))
    if len(questions) < minimum:
        raise RagEvaluationError(f"질문 수가 부족합니다: {len(questions)} < {minimum}")
    if {question["audience"] for question in questions} != {"user", "agent"}:
        raise RagEvaluationError("user와 agent 질문을 모두 포함해야 합니다")
    return payload


def is_relevant(hit: Any, target: dict[str, str]) -> bool:
    document_ok = hit.document_id == target["document_id"]
    section = hit.section or ""
    section_ok = target["section"] in section
    return document_ok and section_ok


def evaluate_hits(hits: list[Any], targets: list[dict[str, str]]) -> tuple[int | None, float]:
    matched_targets: set[int] = set()
    first_relevant_rank: int | None = None
    for index, hit in enumerate(hits, start=1):
        matched_at_rank = [
            target_index
            for target_index, target in enumerate(targets)
            if is_relevant(hit, target)
        ]
        if matched_at_rank and first_relevant_rank is None:
            first_relevant_rank = index
        matched_targets.update(matched_at_rank)
    return first_relevant_rank, len(matched_targets) / len(targets)


def summarize(results: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups["overall"] = results
    for result in results:
        groups[f"audience:{result['audience']}"] .append(result)
        model_group = result["model_code"] or "NONE"
        groups[f"model_code:{model_group}"].append(result)

    summary: dict[str, dict[str, float | int]] = {}
    for name, entries in groups.items():
        total = len(entries)
        recall = sum(entry["recall_at_k"] for entry in entries) / total
        mrr = sum(entry["reciprocal_rank"] for entry in entries) / total
        summary[name] = {"question_count": total, "recall_at_k": recall, "mrr": mrr}
    return summary


def evaluate(questionset: dict[str, Any]) -> dict[str, Any]:
    criteria = questionset["grading_criteria"]
    top_k = int(criteria["top_k"])
    engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
    service = DocumentSearchService(DocumentSearchRepository(engine))
    results: list[dict[str, Any]] = []

    try:
        for question in questionset["questions"]:
            hits = service.search(
                question["query"],
                top_k=top_k,
                model_code=question["model_code"],
            )
            first_relevant_rank, recall_at_k = evaluate_hits(hits, question["expected_targets"])
            results.append(
                {
                    "id": question["id"],
                    "audience": question["audience"],
                    "query": question["query"],
                    "model_code": question["model_code"],
                    "expected_targets": question["expected_targets"],
                    "top_k": top_k,
                    "first_relevant_rank": first_relevant_rank,
                    "recall_at_k": recall_at_k,
                    "reciprocal_rank": 1.0 / first_relevant_rank if first_relevant_rank else 0.0,
                    "passed": recall_at_k == 1.0,
                    "hits": [hit.model_dump() for hit in hits],
                }
            )
    finally:
        pool_factory.dispose_all()

    summary = summarize(results)
    overall = summary["overall"]
    warning_thresholds = criteria.get("group_warning_thresholds", {})
    group_warnings = [
        {
            "group": name,
            "recall_at_k": metrics["recall_at_k"],
            "mrr": metrics["mrr"],
        }
        for name, metrics in summary.items()
        if name != "overall"
        and (
            metrics["recall_at_k"] < float(warning_thresholds.get("recall_at_k", 0.0))
            or metrics["mrr"] < float(warning_thresholds.get("mrr", 0.0))
        )
    ]
    passed = (
        overall["question_count"] >= int(criteria["minimum_question_count"])
        and overall["recall_at_k"] >= float(criteria["recall_at_k_threshold"])
        and overall["mrr"] >= float(criteria["mrr_threshold"])
    )
    return {
        "questionset_id": questionset["questionset_id"],
        "chunk_schema_version": questionset["chunk_schema_version"],
        "executed_at": datetime.now(UTC).isoformat(),
        "grading_criteria": criteria,
        "summary": summary,
        "group_warnings": group_warnings,
        "passed": passed,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questionset", type=Path, default=QUESTIONSET_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    try:
        questionset = load_questionset(args.questionset)
        print(f"질문셋 검증 완료: {len(questionset['questions'])}문항")
        if args.validate_only:
            return 0

        load_dotenv(REPOSITORY_ROOT / ".env")
        report = evaluate(questionset)
    except (RagEvaluationError, RuntimeError, ValueError) as exc:
        print(f"평가 실패: {exc}", file=sys.stderr)
        return 2

    output = args.output
    if output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = RESULT_DIR / f"result_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    overall = report["summary"]["overall"]
    verdict = "PASS" if report["passed"] else "FAIL"
    print(
        f"RAG 평가 {verdict}: Recall@{report['grading_criteria']['top_k']}="
        f"{overall['recall_at_k']:.3f}, MRR={overall['mrr']:.3f}"
    )
    print(f"artifact: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
