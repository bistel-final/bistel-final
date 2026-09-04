"""평가 artifact 낡음 감지 (#304).

최신 평가 artifact 의 구성 지문이 현재 코드의 지문과 같아야 한다. 프롬프트·질문셋·스키마
매니페스트·모델을 바꾸고 평가를 다시 돌리지 않으면 이 테스트가 실패한다 — "잊고 넘어가는 것"을
CI 가 막는다. 고치는 방법은 실패 메시지에 있다.

지문 이전 artifact 만 있는 저장소 상태(#304 머지 직전)도 실패다: 첫 지문 artifact 를 만들어야 한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.analytics.eval_fingerprint import (
    FINGERPRINT_FIELDS,
    compute_fingerprint,
    diff_fingerprint,
    fingerprint_of_artifact,
    is_current,
)

RESULT_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "analytics_eval"
_RUN_FILE_RE = re.compile(r"^result_(\d{8}T\d{6}Z)\.json$")

RERUN_HINT = (
    "평가 artifact 가 현재 구성과 다릅니다. 백엔드 폴더에서 다시 돌려 커밋하세요:\n"
    "    .venv/bin/python scripts/run_analytics_eval.py\n"
    "    git add artifacts/analytics_eval/result_*.json"
)


def _latest_artifact() -> tuple[str, dict] | None:
    candidates = sorted(
        (p for p in RESULT_DIR.iterdir() if _RUN_FILE_RE.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    for path in candidates:
        try:
            return path.name, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def test_fingerprint_is_deterministic_and_complete():
    a = compute_fingerprint()
    b = compute_fingerprint()
    assert a == b
    assert set(FINGERPRINT_FIELDS) <= set(a)
    assert all(a[k] for k in FINGERPRINT_FIELDS)
    assert len(a["digest"]) == 64


def test_fingerprint_changes_when_prompt_changes(monkeypatch):
    from app.analytics import tools

    before = compute_fingerprint()
    monkeypatch.setattr(tools, "_SYSTEM_PROMPT", tools._SYSTEM_PROMPT + "\n-- changed")  # noqa: SLF001
    after = compute_fingerprint()
    assert before["digest"] != after["digest"]
    assert diff_fingerprint(before, after) == ["prompt_template_sha256"]


def test_fingerprint_changes_when_questionset_changes(tmp_path):
    from app.analytics.eval_fingerprint import QUESTIONSET_PATH

    altered = tmp_path / "qs.json"
    altered.write_text(QUESTIONSET_PATH.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert compute_fingerprint(questionset_path=altered)["questionset_sha256"] != compute_fingerprint()["questionset_sha256"]


def test_artifact_without_fingerprint_is_not_current():
    assert fingerprint_of_artifact({"llm": {"prompt_version": "text2sql-v4"}}) is None
    assert is_current(None) is False


def test_latest_artifact_matches_current_configuration():
    """실제 게이트. 실패하면 평가를 다시 돌려 artifact 를 갱신해야 한다."""
    latest = _latest_artifact()
    assert latest is not None, "평가 artifact 가 없습니다.\n" + RERUN_HINT
    name, payload = latest
    artifact_fp = fingerprint_of_artifact(payload)
    current_fp = compute_fingerprint()
    if artifact_fp is None:
        pytest.fail(f"{name}: 구성 지문이 없는 옛 artifact 입니다.\n{RERUN_HINT}")
    changed = diff_fingerprint(artifact_fp, current_fp)
    assert is_current(artifact_fp, current_fp), (
        f"{name} 의 지문과 현재 구성이 다릅니다 — 바뀐 항목: {', '.join(changed)}\n"
        f"  artifact: {[artifact_fp[k] for k in changed]}\n"
        f"  current : {[current_fp[k] for k in changed]}\n{RERUN_HINT}"
    )
