"""`Task분해_WBS_v5_작업본.md`의 선행 그래프 계약.

구현리뷰 18차 필수 1이 4-node 순환(`CM-1.8 → B-1.1 → CM-3.5 → CM-3.2 → CM-1.8`)을
잡았다. 선행 한 줄을 고칠 때 눈으로 두 단계만 따라가면 놓친다 — 기계가 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WBS = REPO_ROOT / "docs/planning/Task분해_WBS_v5_작업본.md"
B_TASKS = REPO_ROOT / "docs/ai-context/tasks/B-knowledge.md"

#: 선행 없음을 뜻하는 표기.
_NO_PREDECESSOR = {"—", "-", ""}

TaskRow = tuple[str, list[str]]


def _rows() -> dict[str, TaskRow]:
    """`| V5-… | 우선순위 | 완료기준 | 요구사항 | 선행 | 시간 |` 행만 읽는다."""

    rows: dict[str, TaskRow] = {}
    for line in WBS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| V5-"):
            continue
        fields = [cell.strip() for cell in line.split("|")]
        task_id, priority, predecessors = fields[1], fields[2], fields[-3]
        rows[task_id] = (
            priority,
            [
                p.strip()
                for p in predecessors.split(",")
                if p.strip() not in _NO_PREDECESSOR
            ],
        )
    return rows


@pytest.fixture(scope="module")
def rows() -> dict[str, TaskRow]:
    parsed = _rows()
    assert len(parsed) >= 90, f"WBS 행을 제대로 못 읽었다: {len(parsed)}"
    return parsed


def test_every_predecessor_exists(rows: dict[str, TaskRow]) -> None:
    missing = {
        task: [p for p in preds if p not in rows]
        for task, (_prio, preds) in rows.items()
    }
    assert not {k: v for k, v in missing.items() if v}


def test_the_dependency_graph_has_no_cycle(rows: dict[str, TaskRow]) -> None:
    """**순환이 있으면 어느 Task도 선행을 만족시키지 못한다**(구현리뷰 18차 필수 1)."""

    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(rows, WHITE)
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(task: str) -> None:
        color[task] = GRAY
        stack.append(task)
        for predecessor in rows[task][1]:
            if predecessor not in rows:
                continue
            if color[predecessor] == GRAY:
                cycles.append(stack[stack.index(predecessor) :] + [predecessor])
            elif color[predecessor] == WHITE:
                visit(predecessor)
        stack.pop()
        color[task] = BLACK

    for task in rows:
        if color[task] == WHITE:
            visit(task)
    assert not cycles, " / ".join(" → ".join(c) for c in cycles)


def test_no_p0_task_waits_on_a_p1_task(rows: dict[str, TaskRow]) -> None:
    inverted = [
        (task, predecessor)
        for task, (priority, preds) in rows.items()
        if priority == "P0"
        for predecessor in preds
        if rows.get(predecessor, ("", []))[0] == "P1"
    ]
    assert not inverted


def test_the_reference_chain_is_ordered(rows: dict[str, TaskRow]) -> None:
    """`CM-3.1 → B-1.1 → CM-1.8 → CM-3.2 → CM-3.5 → B-1.3` (구현리뷰 18차 §88).

    B가 legacy를 정리한 **뒤** CM-1.8이 final 22/13 manifest를 발급하고, 그 뒤
    Runtime migration과 role이 온다. B-1.1이 `CM-3.5`를 선행으로 두면 순환이 된다.
    """

    order: list[str] = []
    seen: set[str] = set()

    def visit(task: str) -> None:
        if task in seen:
            return
        seen.add(task)
        for predecessor in rows.get(task, ("", []))[1]:
            visit(predecessor)
        order.append(task)

    for task in rows:
        visit(task)

    chain = [
        "V5-CM-3.1",
        "V5-B-1.1",
        "V5-CM-1.8",
        "V5-CM-3.2",
        "V5-CM-3.5",
        "V5-B-1.3",
    ]
    positions = [order.index(task) for task in chain]
    assert positions == sorted(positions), dict(zip(chain, positions, strict=True))
    # schema 생성과 최소권한 적용의 소유가 갈려 있어야 순환이 안 생긴다.
    assert "V5-CM-3.5" not in rows["V5-B-1.1"][1]


def test_the_b_task_document_matches_the_wbs() -> None:
    """WBS와 B Task 문서의 `V5-B-1.1` 행이 **exact** 같아야 한다."""

    pattern = re.compile(r"^\| V5-B-1\.1 \|.*$", re.MULTILINE)
    wbs_row = pattern.search(WBS.read_text(encoding="utf-8"))
    doc_row = pattern.search(B_TASKS.read_text(encoding="utf-8"))
    assert wbs_row is not None and doc_row is not None
    assert wbs_row.group(0) == doc_row.group(0)
