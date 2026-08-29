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


def _task_fields() -> list[list[str]]:
    return [
        [cell.strip() for cell in line.split("|")]
        for line in WBS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| V5-")
    ]


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


def test_wbs_hours_and_cm_3_5_contract_are_aligned() -> None:
    text = WBS.read_text(encoding="utf-8")
    fields = _task_fields()
    non_p2 = [row for row in fields if row[2] != "P2"]
    p0 = sum(float(row[-2].removesuffix("h")) for row in fields if row[2] == "P0")
    p1 = sum(float(row[-2].removesuffix("h")) for row in fields if row[2] == "P1")
    total = sum(float(row[-2].removesuffix("h")) for row in non_p2)
    common = sum(
        float(row[-2].removesuffix("h"))
        for row in fields
        if row[1].startswith("V5-CM-")
    )
    c_total = sum(
        float(row[-2].removesuffix("h"))
        for row in fields
        if row[1].startswith("V5-C-") and row[2] != "P2"
    )
    cm_3_5 = next(row for row in fields if row[1] == "V5-CM-3.5")

    assert (p0, p1, total, common) == (145.0, 46.5, 191.5, 61.5)
    summary = re.search(
        r"\| Common \|[^\n]*\| (?P<common>[0-9.]+)h \|[^\n]*\n"
        r"(?:\|[^\n]*\n){4}"
        r"\| \*\*합계\*\* \| \| \*\*(?P<total>[0-9.]+)h\*\* \|",
        text,
    )
    priorities = re.search(
        r"우선순위별 공수는 \*\*P0 (?P<p0>[0-9.]+)h / P1 " r"(?P<p1>[0-9.]+)h\*\*",
        text,
    )
    common_prose = re.search(r"\*\*Common 합계: (?P<common>[0-9.]+)h\*\*", text)
    c_summary = re.search(r"\| C Agent/HITL \|[^\n]*\| (?P<c>[0-9.]+)h \|", text)
    c_prose = re.search(r"\*\*C 합계: (?P<c>[0-9.]+)h\*\*", text)
    assert (
        summary is not None
        and priorities is not None
        and common_prose is not None
        and c_summary is not None
        and c_prose is not None
    )
    assert float(summary["common"]) == common
    assert float(summary["total"]) == total
    assert float(priorities["p0"]) == p0
    assert float(priorities["p1"]) == p1
    assert float(common_prose["common"]) == common
    assert float(c_summary["c"]) == c_total
    assert float(c_prose["c"]) == c_total
    assert cm_3_5[-2] == "4.0h"
    assert {item.strip() for item in cm_3_5[-3].split(",")} == {
        "V5-CM-3.3",
        "V5-CM-3.4",
    }
    for requirement in ("NFR-01", "NFR-02", "NFR-05", "NFR-19", "FR-D-03"):
        assert requirement in cm_3_5[-4]
    for phrase in (
        "5-role",
        "role_core → role_checkpointed",
        "kosa_app",
        "NOLOGIN",
        "PUBLIC",
    ):
        assert phrase in cm_3_5[3]


def test_effort_exception_prose_matches_the_task_rows() -> None:
    """2h 초과 Task를 추가하고 예외 서술을 빠뜨리는 회귀를 막는다(PR #196 필수 1)."""

    text = WBS.read_text(encoding="utf-8")
    exception_section = re.search(
        r"예외가 (?P<count_word>[가-힣]+) 개다\.\n\n"
        r"(?P<items>(?:- `V5-[\s\S]*?))\n\n---",
        text,
    )
    assert exception_section is not None
    listed = {
        task_id: float(hours)
        for task_id, hours in re.findall(
            r"^- `(V5-[^`]+)` \*\*([0-9.]+)h\*\*",
            exception_section["items"],
            re.MULTILINE,
        )
    }
    actual = {
        row[1]: float(row[-2].removesuffix("h"))
        for row in _task_fields()
        if float(row[-2].removesuffix("h")) > 2.0
    }

    assert exception_section["count_word"] == "열네"
    assert len(listed) == 14
    assert listed == actual


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


def test_tool_hard_timeout_followup_reaches_the_final_gate(
    rows: dict[str, TaskRow],
) -> None:
    """C-2.2의 NFR-03 후속을 생략해도 최종 Gate가 닫히는 구멍을 막는다."""

    assert rows["V5-CM-4.8"][0] == "P1"
    assert rows["V5-CM-4.8"][1] == ["V5-C-2.2"]
    assert "V5-CM-4.8" in rows["V5-CM-5.3"][1]


#: `CM-3.1 → B-1.1 → CM-1.8 → CM-3.2 → CM-3.3 → CM-3.4 → CM-3.5`
#: `→ B-1.3` (구현리뷰 18차 §88과 PR #168 팀 리뷰).
#:
#: **direct edge로 고정한다.** 위상 순서만 비교하면 파일의 행 배치가 우연히 그 순서라서
#: edge를 지워도 통과한다 — 19차가 3건을 그렇게 뚫었다(권장 1).
REFERENCE_CHAIN: tuple[tuple[str, str], ...] = (
    ("V5-B-1.1", "V5-CM-3.1"),
    ("V5-CM-1.8", "V5-B-1.1"),
    ("V5-CM-3.2", "V5-CM-1.8"),
    ("V5-CM-3.3", "V5-CM-3.2"),
    ("V5-CM-3.4", "V5-CM-3.3"),
    ("V5-CM-3.5", "V5-CM-3.3"),
    ("V5-CM-3.5", "V5-CM-3.4"),
    ("V5-B-1.3", "V5-CM-3.5"),
)


@pytest.mark.parametrize(("task", "predecessor"), REFERENCE_CHAIN)
def test_the_reference_chain_edge_is_direct(
    rows: dict[str, TaskRow], task: str, predecessor: str
) -> None:
    """각 edge가 **직접 선행**이어야 한다. 전이적으로 만족하는 것으로는 부족하다.

    B가 legacy를 정리한 뒤 CM-1.8이 final 22/13 manifest를 발급하고, 그 뒤 Runtime
    migration과 role이 온다. 중간 한 칸이 빠지면 그 순서 보장이 사라진다.
    """

    assert predecessor in rows[task][1], f"{task} 선행: {rows[task][1]}"


def test_schema_creation_and_grant_ownership_stay_split(
    rows: dict[str, TaskRow],
) -> None:
    """`B-1.1`이 `CM-3.5`를 선행으로 두면 4-node 순환이 된다(구현리뷰 18차 필수 1)."""

    assert "V5-CM-3.5" not in rows["V5-B-1.1"][1]
    assert rows["V5-B-1.1"][1] == ["V5-CM-3.1"]


def test_the_apply_order_prose_matches_the_dag() -> None:
    """**표만 고치고 서술을 두면 계약이 둘이 된다**(구현리뷰 19차 필수 1).

    §8 실행 안내가 구 순서를 말하면, 그대로 따라간 사람은 `CM-1.8`을 `B-1.1`보다 먼저
    시도하거나 B schema를 `CM-3.5`까지 기다린다.
    """

    text = WBS.read_text(encoding="utf-8")
    section = text[text.index("## 8. 적용 순서와 게이트") :]
    section = section[: section.index("```", section.index("```text") + 7)]

    def position(task: str) -> int:
        assert task in section, f"§8에 {task}가 없다"
        return section.index(task)

    # §8은 `V5-CM-3.2~3.5`처럼 범위로 묶여 한 단계가 여러 Task를 담는다. 그래서
    # 서술에서 **단계가 갈리는** 순서만 본다(구현리뷰 19차 §97.1).
    for predecessor, task in (
        ("V5-CM-3.1", "V5-B-1.1"),
        ("V5-B-1.1", "V5-CM-1.8"),
        ("V5-CM-1.8", "V5-CM-3.2"),
    ):
        assert position(predecessor) < position(task), f"{predecessor} → {task}"
    # B-1.1이 도메인 Task 묶음보다 앞에 있고, 그 예외가 글로 적혀 있다.
    assert position("V5-B-1.1") < position("나머지 A·B·C·D")
    assert "먼저 실행하는 유일한 도메인 Task" in section
    # CM-3.5가 RAG explicit GRANT를 소유한다는 것도 서술에 있다.
    assert "explicit GRANT" in section


def test_the_b_task_prose_splits_schema_from_loading() -> None:
    """B 선행조건이 schema 생성과 GRANT·적재를 나눠 설명한다(구현리뷰 19차 필수 1)."""

    text = B_TASKS.read_text(encoding="utf-8")
    section = text[text.index("## 선행조건·협업 주의") :]
    assert "`V5-CM-3.1` **직후**" in section
    assert "`V5-CM-3.5`를 기다리지 않는다" in section
    assert "explicit GRANT는 `V5-CM-3.5`가 소유한다" in section
    assert "`V5-B-1.3`" in section
    # 구 문장이 남아 있으면 안 된다.
    assert "RAG schema·적재는" not in section


def test_the_b_task_document_matches_the_wbs() -> None:
    """WBS와 B Task 문서의 `V5-B-1.1` 행이 **exact** 같아야 한다."""

    pattern = re.compile(r"^\| V5-B-1\.1 \|.*$", re.MULTILINE)
    wbs_row = pattern.search(WBS.read_text(encoding="utf-8"))
    doc_row = pattern.search(B_TASKS.read_text(encoding="utf-8"))
    assert wbs_row is not None and doc_row is not None
    assert wbs_row.group(0) == doc_row.group(0)
