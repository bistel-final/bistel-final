from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import evaluate_rag_search as evaluator  # noqa: E402


def test_evaluate_hits_calculates_target_coverage_not_hit_rate() -> None:
    targets = [
        {"document_id": "DOC-A", "section": "1. 첫 절"},
        {"document_id": "DOC-B", "section": "2. 둘째 절"},
    ]
    hits = [
        SimpleNamespace(document_id="DOC-A", section="1. 첫 절"),
        SimpleNamespace(document_id="DOC-X", section="기타"),
    ]

    first_rank, recall_at_k = evaluator.evaluate_hits(hits, targets)

    assert first_rank == 1
    assert recall_at_k == 0.5
