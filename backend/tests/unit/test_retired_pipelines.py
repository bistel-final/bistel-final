"""폐기 pipeline **tombstone** 계약 (`V5-CM-1.6`).

runtime 차단 테스트가 아니다. `V5-CM-1.5`가 구 corrected entry point 3종을 폐기로
등록했고 `V5-CM-1.6`이 그 구현을 물리 삭제했다. 남은 것은
`infra/bootstrap/retired-pipelines.json` 하나뿐이며, 그것이 "무엇이 왜 폐기됐는지"의
유일한 machine-readable 기록이다.

**삭제를 되돌리는 변경이 들어오면 실패해야 한다.** registry가 가리키는 세 경로가 다시
생기거나 `retired_pipelines.py`가 부활하면 그 자리에서 잡는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPOSITORY_ROOT / "infra/bootstrap/retired-pipelines.json"

_TOP_LEVEL_KEYS = {
    "format_version",
    "artifact_type",
    "dataset_epoch",
    "retired_in_task",
    "entries",
}
_ENTRY_KEYS = {
    "entry_id",
    "script",
    "retired_epoch",
    "reason",
    "correction_stages",
    "replacement_task",
    "removal_task",
}
#: **entry_id → (script, correction_stages, replacement_task).**
#:
#: 구현리뷰 필수 3: `startswith("V5-")`만 보면 script 경로가 바뀌거나 후속 Task가
#: 다른 Task로 슬쩍 옮겨가도 통과한다. registry는 "무엇을 어디서 왜 지웠고 누가
#: 대신하는가"의 유일한 기록이므로 세 값 모두 **정확히** 고정한다.
_RETIRED = {
    "build_corrected_dataset": (
        "backend/scripts/build_corrected_dataset.py",
        ("dim_parameter_seed", "trace_seq_no", "summary_alarm_time"),
        "V5-CM-2.1",
    ),
    "load_corrected_base": (
        "backend/scripts/load_corrected_base.py",
        (),
        "V5-CM-2.3",
    ),
    "load_evaluation_mock": (
        "backend/scripts/load_evaluation_mock.py",
        (),
        "V5-CM-2.3",
    ),
}
_RETIRED_ENTRY_IDS = frozenset(_RETIRED)

WBS = REPOSITORY_ROOT / "docs/planning/Task분해_WBS_v5_작업본.md"


def _wbs_task_ids() -> frozenset[str]:
    """WBS 표에 실재하는 Task ID."""

    return frozenset(
        line.split("|")[1].strip()
        for line in WBS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| V5-")
    )


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_the_tombstone_survives_the_removal(registry: dict) -> None:
    """구현을 지워도 폐기 기록은 남는다."""

    assert set(registry) == _TOP_LEVEL_KEYS
    assert registry["format_version"] == 1
    assert registry["artifact_type"] == "retired_pipeline_registry"
    # **폐기 기록은 최종 epoch 소유다.** 폐기된 구현이 구 epoch 것이라는 사실과
    # 이 artifact가 어느 epoch에서 관리되는지는 다른 값이다.
    assert registry["dataset_epoch"] == "fdc_final_20260818"
    assert registry["retired_in_task"] == "V5-CM-1.5"

    # **set이 아니라 순서 있는 목록으로 센다**(재리뷰 필수 1). set 비교만 하면 같은
    # entry를 하나 더 복제한 4-entry payload도 통과하고, 나머지 테스트는 `next()`로
    # 첫 항목만 보므로 중복을 못 잡는다.
    entry_ids = [entry["entry_id"] for entry in registry["entries"]]
    assert len(entry_ids) == len(_RETIRED) == 3
    assert sorted(entry_ids) == sorted(_RETIRED_ENTRY_IDS)


@pytest.mark.parametrize("entry_id", sorted(_RETIRED_ENTRY_IDS))
def test_each_entry_records_why_and_who(entry_id: str, registry: dict) -> None:
    script, stages, replacement = _RETIRED[entry_id]
    entry = next(e for e in registry["entries"] if e["entry_id"] == entry_id)
    assert set(entry) == _ENTRY_KEYS
    assert entry["script"] == script
    assert tuple(entry["correction_stages"]) == stages
    assert entry["retired_epoch"] == "kosa_0813"
    assert entry["reason"].strip()
    assert entry["replacement_task"] == replacement
    assert entry["removal_task"] == "V5-CM-1.6"


def test_every_referenced_task_exists_in_the_wbs(registry: dict) -> None:
    """**후속·삭제 Task는 실재해야 한다.**

    registry가 가리키는 Task가 WBS에 없으면 "누가 대신하는가"가 빈 약속이 된다.
    """

    known = _wbs_task_ids()
    referenced = {registry["retired_in_task"]}
    for entry in registry["entries"]:
        referenced |= {entry["replacement_task"], entry["removal_task"]}
    assert referenced <= known, sorted(referenced - known)


def test_every_retired_script_is_actually_gone(registry: dict) -> None:
    """**되살리면 여기서 실패한다.**

    registry가 "지웠다"고 기록하는데 파일이 있으면 둘 중 하나가 거짓말이다.
    """

    for entry in registry["entries"]:
        script = REPOSITORY_ROOT / entry["script"]
        assert not script.exists(), f"폐기된 script가 되살아났다: {entry['script']}"


def test_the_runtime_blocker_is_gone_too() -> None:
    """차단기 자체도 삭제 대상이었다 — 호출자가 모두 사라졌기 때문이다.

    없는 파일의 존재를 요구하는 validator를 남기면 계약이 약해진다(계획 §4.1).
    """

    assert not (REPOSITORY_ROOT / "backend/scripts/retired_pipelines.py").exists()
    assert not (REPOSITORY_ROOT / "backend/scripts/corrections").exists()


def test_no_module_still_imports_the_deleted_pipelines() -> None:
    """저장소 어디에도 삭제된 module을 import하는 코드가 없다."""

    deleted = ("build_corrected_dataset", "load_corrected_base", "load_evaluation_mock")
    roots = (
        REPOSITORY_ROOT / "backend" / "scripts",
        REPOSITORY_ROOT / "backend" / "app",
        REPOSITORY_ROOT / "backend" / "tests",
    )
    import ast

    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            # **문자열 리터럴이 아니라 실제 import만 본다.** grep으로 세면
            # "이 모듈을 import하지 않는다"고 단언하는 회귀 자체가 잡힌다.
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name.split(".")[0] in deleted:
                        offenders.append(f"{path.relative_to(REPOSITORY_ROOT)}: {name}")
    assert not offenders, offenders


def test_the_tombstone_carries_no_secret(registry: dict) -> None:
    """폐기 사유에 DSN·비밀번호·절대경로가 없다."""

    payload = json.dumps(registry, ensure_ascii=False)
    for marker in ("postgresql://", "password", "@", "/Users/", "C:\\"):
        assert marker not in payload, marker
