"""최종 패키지 `project.zip`의 source member intake 검증·등록기 (V5-CM-1.1).

ZIP 전체 SHA-256과 기준표 §8 고정 해시 12종을 대조한 뒤, source artifact로 쓸 선별
member 15개만 `infra/bootstrap/final-zip-intake.json`에 등록한다.
참고 Backend·Frontend와 `node_modules`는 읽지 않는다.

epoch 발급·격리는 `V5-CM-1.2`, canonical content hash manifest는 `V5-CM-1.3` 소관이며
이 스크립트는 `dataset-epoch.json`을 비롯한 기존 artifact를 수정하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
INTAKE_ARTIFACT_PATH = BOOTSTRAP_ROOT / "final-zip-intake.json"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

ARTIFACT_FORMAT_VERSION = 1
ARTIFACT_TYPE = "final_zip_intake"
ARCHIVE_FILENAME = "project.zip"
RECEIVED_DATE = "2026-08-18"
INVENTORY_SCOPE = "selected_source_members"
REFERENCE_DOCUMENT = "docs/reference/mentor-final-20260818/README.md"
REFERENCE_SECTION = "8"

# epoch "등록"이 아니라 intake 대상 "선언"이다. 실제 등록과 kosa_0813 격리는 V5-CM-1.2가
# 수행하며 그때까지 dataset-epoch.json의 현행 epoch는 kosa_0813으로 유지된다.
DECLARED_TARGET_EPOCH = "fdc_final_20260818"

# --- 테스트 주입 seam (모듈 상수) -------------------------------------------------
# 단위 테스트는 합성 mini-ZIP을 쓰므로 실제 해시로는 정상 경로를 통과할 수 없다.
# monkeypatch.setattr(intake_final_zip, "<상수>", ...)로 대체한다.

EXPECTED_ARCHIVE_SHA256 = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)

SELECTED_PREFIXES = (
    "project/repository/sample/data/",
    "project/repository/sample/schema/",
    "project/repository/sample/ontology/",
    "project/repository/sample/rag/",
)
SELECTED_FILES = ("project/repository/mvp/gen_sample_data.py",)
SELECTED_MEMBER_COUNT = 15

# 기준표 docs/reference/mentor-final-20260818/README.md §8 그대로.
PINNED_MEMBER_HASHES = {
    "project/repository/sample/schema/03_schema_clean.sql": (
        "4a437efc6d853d911c5f82613b4756fafa6368fd144d6cedfb4f81908af8ca8c"
    ),
    "project/repository/sample/ontology/master.cypher": (
        "51604707c9a0f3bc97b21773b7bd43d0049f2dacf322042c36f090ec63c74eea"
    ),
    "project/repository/sample/data/dim_parameter.csv": (
        "977f4c95bd63750a025cd44dbb8ea08897eb523225894e92f65d668f593041ea"
    ),
    "project/repository/sample/data/lot_history.csv": (
        "d0e2d84cd2b268278873bb963cd67445b5f80434a5d71fe8c3926e4896c13118"
    ),
    "project/repository/sample/data/fdc_trace.csv": (
        "9840c86f459f4da83aca42cf2f5938d36ef4fc843e40f288097f760cab435545"
    ),
    "project/repository/sample/data/summary_data.csv": (
        "1b30af260cb66fd79c43b4777b59dedaf2be58b8f3249d1a15a4e83e153d0c66"
    ),
    "project/repository/sample/data/evaluation.csv": (
        "d6495071d18179fff811e995d6d1fc9e683d8bb2f7f030a06993ca1dba2aa9e7"
    ),
    "project/repository/sample/data/trace_alarm_history.csv": (
        "aaa43f9e6af5d45d3cdc4c813f0a07691a426130a09dfdf93a3c2fe9edac6686"
    ),
    "project/repository/sample/data/summary_alarm_history.csv": (
        "cf16301cb5f03f0213fdb816f4ad15b935c0c6c7e6ed6ef20f63eb30c8121d88"
    ),
    "project/repository/sample/data/metrology.csv": (
        "b6d88cd5fb07f8e69189e2e19ff84beb2279cc238238d7e726b547cff3597be2"
    ),
    "project/repository/sample/data/action_history.csv": (
        "174e8fd71fab0e716e3d8585057e997d17dc03bb9fbedec957df3a146ca213a1"
    ),
    "project/repository/mvp/gen_sample_data.py": (
        "e42e66c84f3c12357f126132f81451ef7e0e8a88e5fc4f080db664331670a24d"
    ),
}

# --------------------------------------------------------------------------------


class IntakeError(Exception):
    """sanitized 사유와 exit code를 함께 전달한다."""

    def __init__(self, reason: str, exit_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_members(names: list[str]) -> list[str]:
    """선별 규칙에 맞는 member 경로를 정렬해 돌려준다."""
    selected = [
        name
        for name in names
        if name in SELECTED_FILES or name.startswith(SELECTED_PREFIXES)
    ]
    return sorted(selected)


def _classify_excluded(names: list[str], selected: set[str]) -> dict[str, int]:
    node_modules = sum(1 for name in names if "node_modules/" in name)
    other = len(names) - node_modules - len(selected)
    return {"node_modules": node_modules, "reference_app_and_docs": other}


def read_archive(archive_path: Path) -> dict[str, Any]:
    """ZIP 전체 해시를 먼저 확인한 뒤 선별 member만 판독한다."""
    if not archive_path.is_file():
        raise IntakeError("등록 ZIP 파일을 찾을 수 없습니다", EXIT_USAGE)
    try:
        archive_sha256 = _sha256_file(archive_path)
    except OSError as exc:
        raise IntakeError("등록 ZIP을 안전하게 읽을 수 없습니다", EXIT_USAGE) from exc

    # 1. member를 한 건도 읽기 전에 전체 무결성을 확정한다.
    if archive_sha256 != EXPECTED_ARCHIVE_SHA256:
        raise IntakeError(
            "ZIP 전체 SHA-256이 기준값과 다릅니다"
            f" (기대 {EXPECTED_ARCHIVE_SHA256[:8]} / 실측 {archive_sha256[:8]})",
            EXIT_MISMATCH,
        )

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise IntakeError("ZIP member 경로가 중복됐습니다", EXIT_MISMATCH)

            # 2. 선별 규칙 적용과 개수 확정.
            selected = select_members(names)
            if len(selected) != SELECTED_MEMBER_COUNT:
                raise IntakeError(
                    "선별 member 수가 기대와 다릅니다"
                    f" (기대 {SELECTED_MEMBER_COUNT} / 실측 {len(selected)})",
                    EXIT_MISMATCH,
                )

            # 3. 선별 member만 판독한다. node_modules는 읽지 않는다.
            members = []
            for name in selected:
                payload = archive.read(name)
                members.append(
                    {
                        "path": name,
                        "size_bytes": len(payload),
                        "sha256": _sha256_bytes(payload),
                        "pinned": name in PINNED_MEMBER_HASHES,
                    }
                )
    except zipfile.BadZipFile as exc:
        raise IntakeError("등록 ZIP 형식이 손상됐습니다", EXIT_USAGE) from exc
    except OSError as exc:
        raise IntakeError(
            "등록 ZIP member를 안전하게 읽을 수 없습니다", EXIT_USAGE
        ) from exc

    # 4. 고정 해시 대조 — 누락과 불일치를 모두 잡되 사유는 구분해 보고한다.
    measured = {member["path"]: member["sha256"] for member in members}
    missing = [path for path in PINNED_MEMBER_HASHES if path not in measured]
    mismatched = [
        f"{path} (기대 {expected[:8]} / 실측 {measured[path][:8]})"
        for path, expected in PINNED_MEMBER_HASHES.items()
        if path in measured and measured[path] != expected
    ]
    if missing or mismatched:
        detail = []
        if missing:
            detail.append("누락 " + ", ".join(sorted(missing)))
        if mismatched:
            detail.append("불일치 " + ", ".join(sorted(mismatched)))
        raise IntakeError("고정 해시 대조 실패 — " + " / ".join(detail), EXIT_MISMATCH)

    return {
        "archive_sha256": archive_sha256,
        "member_total": len(names),
        "excluded_summary": _classify_excluded(names, set(selected)),
        "selected_members": members,
    }


def build_payload(scan: dict[str, Any]) -> dict[str, Any]:
    """등록 artifact 본문을 만든다. 키 순서가 파일 형태를 결정한다."""
    return {
        "format_version": ARTIFACT_FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "declared_target_epoch": DECLARED_TARGET_EPOCH,
        "received_date": RECEIVED_DATE,
        "archive": {
            "filename": ARCHIVE_FILENAME,
            "sha256": scan["archive_sha256"],
        },
        "inventory_scope": INVENTORY_SCOPE,
        "reference_basis": {
            "document": REFERENCE_DOCUMENT,
            "section": REFERENCE_SECTION,
        },
        "member_total": scan["member_total"],
        "excluded_summary": scan["excluded_summary"],
        "selected_count": len(scan["selected_members"]),
        "selected_members": scan["selected_members"],
    }


def serialize(payload: dict[str, Any]) -> str:
    """manifest_v3.py:901과 동일 규약. sort_keys는 해시 계산용이므로 쓰지 않는다."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _changed_keys(existing: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    keys = sorted(set(existing) | set(payload))
    return [key for key in keys if existing.get(key) != payload.get(key)]


def _atomic_write(path: Path, text: str) -> None:
    """같은 디렉터리에 임시 파일을 만든 뒤 교체한다.

    중단돼도 잘린 JSON이 남지 않는다. 이 등록물은 `V5-CM-1.2`·`1.3`의 입력이므로
    부분 기록 상태를 만들지 않는 것이 중요하다(`manifest_v3.py`의 `_atomic_save_json`과
    같은 패턴이며, 결정 2의 독립 스크립트 취지에 따라 import 대신 복제했다).
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


def _load_existing(out_path: Path) -> dict[str, Any]:
    try:
        raw = out_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # UnicodeDecodeError는 ValueError 계열이라 아래 OSError에도, JSONDecodeError에도
        # 걸리지 않는다. 손상 JSON과 같은 사유이므로 EXIT_MISMATCH로 맞춘다.
        # 디코딩 실패 위치·원문 바이트는 sanitized 유지를 위해 메시지에 넣지 않는다.
        raise IntakeError(
            "기존 등록 artifact가 UTF-8이 아닙니다", EXIT_MISMATCH
        ) from exc
    except OSError as exc:
        raise IntakeError("기존 등록 artifact를 읽을 수 없습니다", EXIT_USAGE) from exc
    try:
        existing = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntakeError(
            "기존 등록 artifact가 올바른 JSON이 아닙니다", EXIT_MISMATCH
        ) from exc
    # 유효한 JSON이라도 object가 아니면 key 비교가 성립하지 않는다. 손상 JSON과 같은
    # 사유로 처리해 traceback 대신 sanitized 한 줄로 끝낸다.
    if not isinstance(existing, dict):
        raise IntakeError(
            "기존 등록 artifact가 JSON object가 아닙니다"
            f" (최상위 타입 {type(existing).__name__})",
            EXIT_MISMATCH,
        )
    return {"raw": raw, "payload": existing}


def write_artifact(
    out_path: Path, payload: dict[str, Any], *, verify_only: bool, confirm: bool
) -> str:
    """계획 §2.1 5단계의 5-case 규칙을 그대로 구현한다."""
    serialized = serialize(payload)

    def _write(outcome: str) -> str:
        try:
            _atomic_write(out_path, serialized)
        except OSError as exc:
            raise IntakeError("등록 artifact를 기록할 수 없습니다", EXIT_USAGE) from exc
        return outcome

    if not out_path.exists():
        if verify_only:
            raise IntakeError("대조할 등록 artifact가 없습니다", EXIT_USAGE)
        return _write("생성")

    existing_state = _load_existing(out_path)
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
        raise IntakeError(
            f"등록 artifact가 현재 ZIP과 다릅니다 — {changed}", EXIT_MISMATCH
        )
    if not confirm:
        raise IntakeError(
            f"기존 등록 artifact를 덮어쓰려면 --confirm이 필요합니다 — 차이: {changed}",
            EXIT_CONFIRM_REQUIRED,
        )
    return _write("덮어쓰기")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=INTAKE_ARTIFACT_PATH)
    parser.add_argument("--verify-only", action="store_true")
    # 승인 플래그 이름은 저장소 규약(`manifest_v3.py` 등 4개)의 `--confirm`을 정식으로
    # 쓰고, 계획 v1~v5가 규정한 `--force`를 별칭으로 남긴다.
    parser.add_argument(
        "--confirm", "--force", action="store_true", dest="confirm"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verify_only and args.confirm:
            # 입력하지 않은 플래그를 지목하지 않도록 두 표기를 함께 안내한다.
            raise IntakeError(
                "--verify-only와 승인 플래그(--confirm/--force)는 함께 쓸 수 없습니다",
                EXIT_USAGE,
            )
        scan = read_archive(args.archive)
        payload = build_payload(scan)
        outcome = write_artifact(
            args.out, payload, verify_only=args.verify_only, confirm=args.confirm
        )
    except IntakeError as exc:
        print(f"[intake] 실패: {exc.reason}", file=sys.stderr)
        return exc.exit_code

    pinned = sum(1 for member in payload["selected_members"] if member["pinned"])
    print(
        f"[intake] {outcome} · member {payload['member_total']}"
        f" · 선별 {payload['selected_count']} · pinned {pinned}"
        f" · 대상 {args.out.name}"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
