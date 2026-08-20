"""최종 패키지 epoch `fdc_final_20260818` 발급기 (V5-CM-1.2).

`V5-CM-1.1`이 등록한 intake artifact(`infra/bootstrap/final-zip-intake.json`)가 발급
기준과 일치하는지 확인한 뒤 `infra/bootstrap/dataset-epoch.json`을 v2 계약으로 발급한다.

v2 payload는 v1의 전수 `file_inventory`를 담지 않는다. 선별 15 member의 경로·크기·해시는
intake artifact가, 컬럼·행 수·content hash는 `V5-CM-1.3` manifest v4가 원천이다.
`intake_artifact`는 경로 참조만 하고 해시는 넣지 않는다 — 넣으면 intake 재발급마다
epoch도 재발급해야 하는 이중 결합이 생긴다(작업계획 결정 7). 무결성은 계약 테스트의
3자 대조가 맡는다.

이 발급으로 구 epoch `kosa_0813`을 소비하던 파이프라인은
`manifest_v3.load_dataset_epoch()`의 **키 집합 검사**(`manifest_v3.py:425-438`)에서
`ManifestSchemaError`로 fail-fast한다.
그것이 완료 기준 "동시 참조 금지"의 구현이며 의도된 파괴다(팀 결정 2026-08-19).
격리한 구 epoch artifact는 `infra/bootstrap/history/kosa_0813/`에 있다.

exit 규약은 `intake_final_zip.py`와 같다 — 0 정상 · 1 기준 불일치 ·
2 사용법·입출력 오류 · 3 승인 필요.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
INTAKE_ARTIFACT_PATH = BOOTSTRAP_ROOT / "final-zip-intake.json"
EPOCH_ARTIFACT_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

ARTIFACT_FORMAT_VERSION = 2
ARTIFACT_TYPE = "dataset_epoch_registration"
ARCHIVE_FILENAME = "project.zip"
RECEIVED_DATE = "2026-08-18"
INVENTORY_SCOPE = "selected_source_members"

# payload가 담는 것은 CLI `--intake` 인자가 아니라 저장소 상대 경로 참조다. 실행 인자가
# 무엇이든 발급물은 정본 위치를 가리킨다.
INTAKE_ARTIFACT_REFERENCE = "infra/bootstrap/final-zip-intake.json"

SUPERSEDED_EPOCH = "kosa_0813"
SUPERSEDED_HISTORY_ROOT = "infra/bootstrap/history/kosa_0813/"

INTAKE_ARTIFACT_TYPE = "final_zip_intake"
EXPECTED_SELECTED_COUNT = 15

# --- 테스트 주입 seam (모듈 상수) -------------------------------------------------
# 단위 테스트는 합성 intake fixture를 쓰므로 실제 해시로는 정상 경로를 통과할 수 없다.
# monkeypatch.setattr(issue_final_epoch, "<상수>", ...)로 대체한다(작업계획 §2.1).

TARGET_EPOCH = "fdc_final_20260818"

EXPECTED_ARCHIVE_SHA256 = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)

# --------------------------------------------------------------------------------


class EpochIssueError(Exception):
    """sanitized 사유와 exit code를 함께 전달한다."""

    def __init__(self, reason: str, exit_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


def _short(value: Any, limit: int = 24) -> str:
    """파일에서 읽은 값을 사유 메시지에 넣기 전에 한 줄로 무해화한다.

    손상 입력의 값이 그대로 나가면 개행·제어문자가 섞여 "sanitized 한 줄" 규약이 깨진다.
    """
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    text = "".join(char if char.isprintable() else "?" for char in text)
    return text[:limit] + "…" if len(text) > limit else text


def _read_json_object(path: Path, *, label: str, corrupt_exit: int) -> dict[str, Any]:
    """UTF-8 JSON object 하나를 읽는다. 손상은 traceback 없이 사유 한 줄로 끝낸다.

    `corrupt_exit`이 입력별로 다른 이유:
    - intake artifact는 **판독 자체가 불가능한 입력**이므로 EXIT_USAGE다. 대조에
      도달하지 못했으므로 "기준과 다르다"고 말할 수 없다.
    - 기존 epoch artifact는 `intake_final_zip.py:277-303`의 [승계] 규약대로
      EXIT_MISMATCH다. 쓰기 대상의 손상은 "우리가 쓰려는 것과 다르다"는 대조 결과에
      해당한다.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # UnicodeDecodeError는 ValueError 계열이라 아래 OSError에도, JSONDecodeError에도
        # 걸리지 않는다. 디코딩 실패 위치·원문 바이트는 사유에 넣지 않는다.
        raise EpochIssueError(f"{label}가 UTF-8이 아닙니다", corrupt_exit) from exc
    except OSError as exc:
        raise EpochIssueError(f"{label}를 읽을 수 없습니다", EXIT_USAGE) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EpochIssueError(
            f"{label}가 올바른 JSON이 아닙니다", corrupt_exit
        ) from exc
    # 유효한 JSON이라도 object가 아니면 key 비교가 성립하지 않는다.
    if not isinstance(payload, dict):
        raise EpochIssueError(
            f"{label}가 JSON object가 아닙니다 (최상위 타입 {type(payload).__name__})",
            corrupt_exit,
        )
    return {"raw": raw, "payload": payload}


def load_intake(intake_path: Path) -> dict[str, Any]:
    return _read_json_object(
        intake_path, label="intake artifact", corrupt_exit=EXIT_USAGE
    )["payload"]


def verify_intake(intake: dict[str, Any]) -> str:
    """작업계획 §2.1 1단계의 4개 대조. 하나라도 다르면 EXIT_MISMATCH다.

    확인한 archive SHA-256을 돌려주고, 그 값이 발급물에 들어간다.
    """
    problems: list[str] = []

    artifact_type = intake.get("artifact_type")
    if artifact_type != INTAKE_ARTIFACT_TYPE:
        problems.append(
            "artifact_type (기대"
            f" {INTAKE_ARTIFACT_TYPE} / 실측 {_short(artifact_type)})"
        )

    declared = intake.get("declared_target_epoch")
    if declared != TARGET_EPOCH:
        problems.append(
            f"declared_target_epoch (기대 {TARGET_EPOCH} / 실측 {_short(declared)})"
        )

    selected_count = intake.get("selected_count")
    if selected_count != EXPECTED_SELECTED_COUNT:
        problems.append(
            f"selected_count (기대 {EXPECTED_SELECTED_COUNT}"
            f" / 실측 {_short(selected_count)})"
        )

    archive = intake.get("archive")
    archive_sha256 = archive.get("sha256") if isinstance(archive, dict) else None
    if archive_sha256 != EXPECTED_ARCHIVE_SHA256:
        problems.append(
            f"archive.sha256 (기대 {EXPECTED_ARCHIVE_SHA256[:8]}"
            f" / 실측 {_short(archive_sha256, 8)})"
        )

    if problems:
        raise EpochIssueError(
            "intake artifact가 발급 기준과 다릅니다 — " + " / ".join(problems),
            EXIT_MISMATCH,
        )
    return archive_sha256


def build_payload(archive_sha256: str) -> dict[str, Any]:
    """epoch v2 본문을 만든다(작업계획 §2.2). 키 순서가 파일 형태를 결정한다."""
    return {
        "format_version": ARTIFACT_FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "dataset_epoch": TARGET_EPOCH,
        "received_date": RECEIVED_DATE,
        "archive": {
            "filename": ARCHIVE_FILENAME,
            "sha256": archive_sha256,
        },
        "inventory_scope": INVENTORY_SCOPE,
        "intake_artifact": INTAKE_ARTIFACT_REFERENCE,
        "supersedes": {
            "dataset_epoch": SUPERSEDED_EPOCH,
            "isolated_to": SUPERSEDED_HISTORY_ROOT,
        },
    }


def serialize(payload: dict[str, Any]) -> str:
    """manifest_v3.py:901·intake_final_zip.py:242와 동일 규약."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _changed_keys(existing: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    keys = sorted(set(existing) | set(payload))
    return [key for key in keys if existing.get(key) != payload.get(key)]


def _atomic_write(path: Path, text: str) -> None:
    """같은 디렉터리에 임시 파일을 만든 뒤 교체한다.

    중단돼도 잘린 JSON이 남지 않는다. 이 발급물은 구 파이프라인 전체가 바라보는 단일
    스위치이므로 부분 기록 상태를 만들지 않는 것이 특히 중요하다([승계]).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def write_artifact(
    out_path: Path, payload: dict[str, Any], *, verify_only: bool, confirm: bool
) -> str:
    """5-case 보호 규칙([승계] `intake_final_zip.py:306-348`)."""
    serialized = serialize(payload)

    def _write(outcome: str) -> str:
        try:
            _atomic_write(out_path, serialized)
        except OSError as exc:
            raise EpochIssueError(
                "epoch artifact를 기록할 수 없습니다", EXIT_USAGE
            ) from exc
        return outcome

    if not out_path.exists():
        if verify_only:
            raise EpochIssueError("대조할 epoch artifact가 없습니다", EXIT_USAGE)
        return _write("생성")

    existing_state = _read_json_object(
        out_path, label="기존 epoch artifact", corrupt_exit=EXIT_MISMATCH
    )
    raw = existing_state["raw"]
    existing = existing_state["payload"]

    if existing == payload:
        if raw == serialized:
            return "변경 없음"
        if verify_only:
            return "객체 동일 (바이트 상이 — 검증 모드라 재작성하지 않음)"
        return _write("규약 형태로 재작성")

    changed = (
        ", ".join(_changed_keys(existing, payload))
        or "(최상위 키 동일, 하위 값 상이)"
    )
    if verify_only:
        raise EpochIssueError(
            f"epoch artifact가 발급 기준과 다릅니다 — {changed}", EXIT_MISMATCH
        )
    if not confirm:
        raise EpochIssueError(
            "기존 epoch artifact를 덮어쓰려면 --confirm이 필요합니다"
            f" — 차이: {changed}",
            EXIT_CONFIRM_REQUIRED,
        )
    return _write("덮어쓰기")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", type=Path, default=INTAKE_ARTIFACT_PATH)
    parser.add_argument("--out", type=Path, default=EPOCH_ARTIFACT_PATH)
    parser.add_argument("--verify-only", action="store_true")
    # 승인 플래그는 저장소 규약의 `--confirm`을 정식으로 쓰고 `--force`를 별칭으로 둔다
    # ([승계] `intake_final_zip.py:356-360`).
    parser.add_argument("--confirm", "--force", action="store_true", dest="confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    # 비-UTF-8 stdout 환경(PYTHONIOENCODING=ascii 등)에서 성공 출력의 한국어·'·'가
    # UnicodeEncodeError로 죽으면, 파일은 이미 원자적으로 교체된 뒤라 "부작용 완료 +
    # exit 1(=EXIT_MISMATCH) 오독"이 된다. stderr는 기본 errors가 backslashreplace라
    # 실패 경로는 살아남지만 stdout은 strict라 성공 경로만 죽는다(구현리뷰 1차 필수 1).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = _parser().parse_args(argv)
    try:
        if args.verify_only and args.confirm:
            raise EpochIssueError(
                "--verify-only와 승인 플래그(--confirm/--force)는 함께 쓸 수 없습니다",
                EXIT_USAGE,
            )
        intake = load_intake(args.intake)
        archive_sha256 = verify_intake(intake)
        payload = build_payload(archive_sha256)
        outcome = write_artifact(
            args.out, payload, verify_only=args.verify_only, confirm=args.confirm
        )
    except EpochIssueError as exc:
        print(f"[epoch] 실패: {exc.reason}", file=sys.stderr)
        return exc.exit_code

    print(
        f"[epoch] {outcome} · epoch {payload['dataset_epoch']}"
        f" · archive {payload['archive']['sha256'][:8]}"
        f" · 대상 {args.out.name}"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
