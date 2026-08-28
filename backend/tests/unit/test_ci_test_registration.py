"""PR CI가 신규 Backend test 파일을 조용히 누락하지 않는지 검증한다."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_TEST_ROOT = REPOSITORY_ROOT / "backend" / "tests"
PR_POLICY = REPOSITORY_ROOT / ".github" / "workflows" / "pr-policy.yml"

# CI에 직접 등록하지 않은 예외는 소유자와 이유를 반드시 남긴다. 예외 파일을 등록하거나
# 삭제하면 stale allowlist 검증이 실패하므로 이 목록도 함께 정리해야 한다.
UNREGISTERED_ALLOWLIST = {
    "tests/unit/test_rehearsal_image_manifest.py": (
        "Docker registry manifest 조회가 필요한 container/network 전용 계약이다."
    ),
}

_TEST_PATH = re.compile(
    r"(?:backend/)?(tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_]+\.py)"
)
_STEP_START = re.compile(r"^\s+- name:")
_CONTAINER_SELECTION = re.compile(r"(?:^|\s)-m\s+container(?:\s|$)")
_CONTAINER_MODULE_MARKER = re.compile(
    r"^pytestmark\s*=\s*pytest\.mark\.container\s*$", re.MULTILINE
)


def _repository_backend_tests() -> set[str]:
    return {
        path.relative_to(REPOSITORY_ROOT / "backend").as_posix()
        for path in BACKEND_TEST_ROOT.rglob("test_*.py")
        if path.is_file()
    }


def _registered_backend_tests(workflow_text: str | None = None) -> set[str]:
    """실행 가능한 workflow step에 적힌 test path만 수집한다.

    module 전체가 ``container``인 파일은 ``-m container``가 있는 step에서만 등록으로
    센다. 이름만 일반 unit step에 적혀 전부 deselect되는 경우는 등록이 아니다.
    """

    registered: set[str] = set()
    current_step: list[str] = []
    steps: list[list[str]] = []
    text = (
        PR_POLICY.read_text(encoding="utf-8")
        if workflow_text is None
        else workflow_text
    )
    for line in text.splitlines():
        if _STEP_START.match(line):
            if current_step:
                steps.append(current_step)
            current_step = [line]
        elif current_step:
            current_step.append(line)
    if current_step:
        steps.append(current_step)

    for step in steps:
        step_text = "\n".join(step)
        selects_container = _CONTAINER_SELECTION.search(step_text) is not None
        for line in step:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _TEST_PATH.fullmatch(stripped)
            if match is None:
                continue
            test_path = match.group(1)
            source = REPOSITORY_ROOT / "backend" / test_path
            container_only = (
                source.is_file()
                and _CONTAINER_MODULE_MARKER.search(source.read_text(encoding="utf-8"))
                is not None
            )
            if not container_only or selects_container:
                registered.add(test_path)
    return registered


def test_every_backend_test_is_registered_or_explicitly_allowlisted() -> None:
    repository_tests = _repository_backend_tests()
    registered_tests = _registered_backend_tests()
    allowlisted_tests = set(UNREGISTERED_ALLOWLIST)

    missing = repository_tests - registered_tests - allowlisted_tests
    stale_allowlist = allowlisted_tests - (repository_tests - registered_tests)

    assert not missing, f"PR CI 미등록 Backend test: {sorted(missing)}"
    assert not stale_allowlist, (
        "삭제됐거나 이미 CI에 등록된 allowlist 항목: " f"{sorted(stale_allowlist)}"
    )
    assert all(reason.strip() for reason in UNREGISTERED_ALLOWLIST.values())


def test_container_only_files_are_owned_by_container_selected_steps() -> None:
    """일반 step의 이름뿐인 등록으로 container 회귀가 숨지 않는다."""

    registered_tests = _registered_backend_tests()
    container_only_tests = {
        path.relative_to(REPOSITORY_ROOT / "backend").as_posix()
        for path in BACKEND_TEST_ROOT.rglob("test_*.py")
        if _CONTAINER_MODULE_MARKER.search(path.read_text(encoding="utf-8")) is not None
    }

    assert container_only_tests - set(UNREGISTERED_ALLOWLIST) <= registered_tests


def test_container_only_name_in_an_unselected_step_is_not_registration() -> None:
    test_path = "tests/unit/test_rehearsal_container.py"
    unselected = f"""
      - name: Wrong unit step
        run: >-
          python -m pytest -q
          {test_path}
    """
    selected = unselected.replace(
        "python -m pytest -q", "python -m pytest -q -m container"
    )

    assert test_path not in _registered_backend_tests(unselected)
    assert test_path in _registered_backend_tests(selected)
