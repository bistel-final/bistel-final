"""Text2SQL 평가 구성 지문 (#304).

"지금 평가 artifact 가 지금 코드 기준인가"를 기계가 판단할 수 있게, 평가 결과에 영향을 주는
구성 요소를 하나의 지문으로 만든다. 러너가 artifact 에 기록하고, 유닛 테스트·CI 워크플로·
GET /analytics/evaluations 가 같은 함수로 현재 지문을 계산해 비교한다.

지문 구성 (하나라도 바뀌면 재평가 필요):
    prompt_version          — tools.PROMPT_VERSION (규칙 개정 표식)
    prompt_template_sha256  — 시스템 프롬프트 원문 해시. 버전 bump 를 잊어도 잡는다
    questionset_sha256      — 질문셋 파일(정답 SQL 포함)
    schema_manifest_sha256  — 스키마 매니페스트(허용 테이블·컬럼)
    llm_model               — 지문에 넣지 않고 정보로만 기록. .env 환경 설정이라 CI(기본값 ollama)와 로컬이 다르고,
                              이 게이트는 "코드가 바뀌었는데 재평가를 안 했나"를 보는 것이다

포함하지 않는 것: 값 도메인 힌트(DB 데이터라 코드가 아니다), temperature(0.1 고정 정책),
데이터 epoch(questionset 에 이미 기록). dataset 자체가 바뀌면 questionset 이 바뀐다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.analytics import tools
from app.analytics.sql_validator import RUNTIME_MANIFEST_PATH
from app.common.config import LLM_MODEL_MAIN

BACKEND_ROOT = Path(__file__).resolve().parents[2]
QUESTIONSET_PATH = BACKEND_ROOT / "artifacts" / "analytics_eval" / "questionset_fdc_final.json"

FINGERPRINT_FIELDS: tuple[str, ...] = (
    "prompt_version",
    "prompt_template_sha256",
    "questionset_sha256",
    "schema_manifest_sha256",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(parts: dict[str, str]) -> str:
    """필드 순서를 고정해 하나의 해시로 — 비교는 이 값 하나로 한다."""
    canonical = json.dumps({k: parts[k] for k in FINGERPRINT_FIELDS}, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical)


def compute_fingerprint(
    *,
    questionset_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, str]:
    """현재 코드·파일 기준 지문. 경로 인자는 테스트용."""
    qs = questionset_path or QUESTIONSET_PATH
    mf = manifest_path or RUNTIME_MANIFEST_PATH
    parts = {
        "prompt_version": str(tools.PROMPT_VERSION),
        "prompt_template_sha256": _sha256_text(tools._SYSTEM_PROMPT),  # noqa: SLF001 — 같은 패키지의 정본
        "questionset_sha256": _sha256_file(qs),
        "schema_manifest_sha256": _sha256_file(mf),
    }
    return {**parts, "digest": _digest(parts), "llm_model": str(LLM_MODEL_MAIN)}


def fingerprint_of_artifact(payload: dict[str, Any]) -> dict[str, str] | None:
    """artifact 에 기록된 지문. 지문 이전(#304 전) artifact 는 None."""
    fp = payload.get("fingerprint")
    if not isinstance(fp, dict):
        return None
    if not all(isinstance(fp.get(k), str) and fp.get(k) for k in FINGERPRINT_FIELDS):
        return None
    parts = {k: fp[k] for k in FINGERPRINT_FIELDS}
    return {**parts, "digest": _digest(parts), "llm_model": str(fp.get("llm_model") or "")}


def is_current(artifact_fp: dict[str, str] | None, current_fp: dict[str, str] | None = None) -> bool:
    """artifact 가 현재 구성 기준인가. 지문이 없는 옛 artifact 는 항상 False."""
    if artifact_fp is None:
        return False
    cur = current_fp or compute_fingerprint()
    return artifact_fp["digest"] == cur["digest"]


def diff_fingerprint(artifact_fp: dict[str, str] | None, current_fp: dict[str, str] | None = None) -> list[str]:
    """무엇이 달라졌는지 — 테스트 실패 메시지·CI 로그용."""
    cur = current_fp or compute_fingerprint()
    if artifact_fp is None:
        return list(FINGERPRINT_FIELDS)
    return [k for k in FINGERPRINT_FIELDS if artifact_fp.get(k) != cur.get(k)]
