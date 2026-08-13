"""BAAI/bge-m3 임베딩 모델을 고정 revision 으로 사전 다운로드한다. (CM-0.5)

런타임 문서 검색은 네트워크 다운로드를 하지 않고, 이 스크립트가 미리 만든
`backend/model-cache/bge-m3` 캐시만 읽는다.

사용:
    python backend/scripts/prefetch_embedding_model.py
    python backend/scripts/prefetch_embedding_model.py --dry-run
    python backend/scripts/prefetch_embedding_model.py --verify-only
    python backend/scripts/prefetch_embedding_model.py --generate-manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPOSITORY_ROOT / "backend" / "artifacts" / "embedding_model_manifest.json"
)
MANIFEST_FORMAT_VERSION = 1
HASH_ALGORITHM = "sha256"
EXPECTED_MODEL_ID = "BAAI/bge-m3"
EXPECTED_EMBEDDING_DIMENSION = 1024
EXPECTED_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def load_dotenv_file() -> None:
    load_dotenv(REPOSITORY_ROOT / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"필수 환경변수가 없습니다: {name}")
    return value.strip()


def resolve_revision() -> str:
    revision = require_env("EMBEDDING_MODEL_REVISION")
    if not COMMIT_HASH_PATTERN.fullmatch(revision):
        raise RuntimeError(
            "EMBEDDING_MODEL_REVISION 은 40자리 소문자 commit hash 여야 합니다: "
            f"{revision}"
        )
    if revision != EXPECTED_MODEL_REVISION:
        raise RuntimeError(
            f"EMBEDDING_MODEL_REVISION 이 공식 revision 과 다릅니다: {revision}"
        )
    return revision


def resolve_model_id() -> str:
    model_id = require_env("EMBEDDING_MODEL")
    if model_id != EXPECTED_MODEL_ID:
        raise RuntimeError(
            f"EMBEDDING_MODEL 은 {EXPECTED_MODEL_ID} 이어야 합니다: {model_id}"
        )
    return model_id


def resolve_embedding_dimension() -> int:
    raw_dimension = require_env("EMBEDDING_DIM")

    try:
        dimension = int(raw_dimension)
    except ValueError as exc:
        raise RuntimeError(
            f"EMBEDDING_DIM 은 정수여야 합니다: {raw_dimension}"
        ) from exc

    if dimension <= 0:
        raise RuntimeError(f"EMBEDDING_DIM 은 1 이상이어야 합니다: {dimension}")
    if dimension != EXPECTED_EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"EMBEDDING_DIM 은 {EXPECTED_EMBEDDING_DIMENSION} 이어야 합니다: "
            f"{dimension}"
        )
    return dimension


def resolve_cache_dir() -> Path:
    path = Path(require_env("EMBEDDING_MODEL_PATH"))
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path


def prefetch_model(model_id: str, revision: str, cache_dir: Path) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub 를 import 할 수 없습니다. "
            "backend/requirements.txt 의 sentence-transformers 의존성을 설치하세요."
        ) from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    downloaded_path = snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=cache_dir,
    )
    return Path(downloaded_path)


def _should_hash_file(path: Path, cache_dir: Path) -> bool:
    relative_parts = path.relative_to(cache_dir).parts
    if ".cache" in relative_parts:
        return False
    if path.suffix in {".lock", ".tmp"}:
        return False
    return path.is_file()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_model_files(cache_dir: Path) -> list[dict[str, Any]]:
    if not cache_dir.exists():
        raise RuntimeError(f"모델 캐시 디렉터리가 없습니다: {cache_dir}")

    files: list[dict[str, Any]] = []
    for path in sorted(cache_dir.rglob("*")):
        if not _should_hash_file(path, cache_dir):
            continue
        relative_path = path.relative_to(cache_dir).as_posix()
        stat = path.stat()
        files.append(
            {
                "path": relative_path,
                "size_bytes": stat.st_size,
                "sha256": _sha256_file(path),
            }
        )

    if not files:
        raise RuntimeError(f"hash 대상 모델 파일이 없습니다: {cache_dir}")
    return files


def _display_cache_path(cache_dir: Path) -> str:
    try:
        return str(cache_dir.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(cache_dir)


def build_manifest(
    *,
    model_id: str,
    revision: str,
    cache_dir: Path,
    embedding_dimension: int,
) -> dict[str, Any]:
    files = collect_model_files(cache_dir)
    return {
        "format_version": MANIFEST_FORMAT_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "model": {
            "id": model_id,
            "revision": revision,
            "embedding_dimension": embedding_dimension,
            "cache_path": _display_cache_path(cache_dir),
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "files": files,
    }


def save_manifest(
    manifest: dict[str, Any], manifest_path: Path = MANIFEST_PATH
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(manifest_path)


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not manifest_path.exists():
        raise RuntimeError(f"manifest 가 없습니다: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _is_invalid_manifest_path(path: str) -> bool:
    posix_path = PurePosixPath(path)
    windows_path = PureWindowsPath(path)
    return (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    )


def validate_manifest(
    *,
    manifest: dict[str, Any],
    model_id: str,
    revision: str,
    cache_dir: Path,
    embedding_dimension: int,
) -> list[str]:
    errors: list[str] = []

    if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
        errors.append("format_version 불일치")
    if manifest.get("hash_algorithm") != HASH_ALGORITHM:
        errors.append("hash_algorithm 불일치")

    model = manifest.get("model")
    if not isinstance(model, dict):
        errors.append("model 섹션 누락")
        model = {}

    if model.get("id") != model_id:
        errors.append("model.id 불일치")
    if model.get("revision") != revision:
        errors.append("model.revision 불일치")
    if model.get("embedding_dimension") != embedding_dimension:
        errors.append("model.embedding_dimension 불일치")

    expected_files = manifest.get("files")
    if not isinstance(expected_files, list) or not expected_files:
        errors.append("files 목록 누락")
        return errors

    try:
        actual_files = collect_model_files(cache_dir)
    except RuntimeError as exc:
        errors.append(str(exc))
        actual_files = []
    actual_paths = {file["path"] for file in actual_files}
    expected_paths: set[str] = set()

    for index, entry in enumerate(expected_files):
        if not isinstance(entry, dict):
            errors.append(f"files[{index}] 형식 오류")
            continue

        relative_path = entry.get("path")
        expected_size = entry.get("size_bytes")
        expected_sha256 = entry.get("sha256")
        if not isinstance(relative_path, str) or not relative_path:
            errors.append(f"files[{index}].path 형식 오류")
            continue
        if _is_invalid_manifest_path(relative_path):
            errors.append(f"{relative_path}: 상대 하위 경로만 허용")
            continue
        if relative_path in expected_paths:
            errors.append(f"{relative_path}: manifest path 중복")
            continue
        expected_paths.add(relative_path)
        if not isinstance(expected_size, int) or isinstance(expected_size, bool):
            errors.append(f"{relative_path}: size_bytes 형식 오류")
            continue
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            errors.append(f"{relative_path}: sha256 형식 오류")
            continue

        actual_path = cache_dir / relative_path
        if not actual_path.exists():
            errors.append(f"{relative_path}: 파일 누락")
            continue
        if not actual_path.is_file():
            errors.append(f"{relative_path}: 일반 파일이 아님")
            continue
        actual_size = actual_path.stat().st_size
        if actual_size != expected_size:
            errors.append(
                f"{relative_path}: 파일 크기 불일치 "
                f"(manifest {expected_size} / actual {actual_size})"
            )
        actual_sha256 = _sha256_file(actual_path)
        if actual_sha256 != expected_sha256:
            errors.append(f"{relative_path}: sha256 불일치")

    missing_from_manifest = sorted(actual_paths - expected_paths)
    for relative_path in missing_from_manifest:
        errors.append(f"{relative_path}: manifest 미등록 파일")

    return errors


def verify_manifest(
    *,
    model_id: str,
    revision: str,
    cache_dir: Path,
    embedding_dimension: int,
    manifest_path: Path = MANIFEST_PATH,
) -> int:
    manifest = load_manifest(manifest_path)
    errors = validate_manifest(
        manifest=manifest,
        model_id=model_id,
        revision=revision,
        cache_dir=cache_dir,
        embedding_dimension=embedding_dimension,
    )
    if errors:
        print(f"manifest 검증 실패 {len(errors)}건:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("manifest 검증 성공.")
    return 0


def run(
    *,
    model_id: str,
    revision: str,
    cache_dir: Path,
    embedding_dimension: int,
    dry_run: bool,
    verify_only: bool,
    generate_manifest: bool,
    manifest_path: Path = MANIFEST_PATH,
) -> int:
    print(f"model_id={model_id}")
    print(f"embedding_dimension={embedding_dimension}")
    print(f"revision={revision}")
    print(f"cache_dir={cache_dir}")

    if verify_only:
        return verify_manifest(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            embedding_dimension=embedding_dimension,
            manifest_path=manifest_path,
        )

    if dry_run:
        print("dry-run: 다운로드를 실행하지 않았습니다.")
        return 0

    downloaded_path = prefetch_model(model_id, revision, cache_dir)
    print(f"prefetch 완료: {downloaded_path}")

    if generate_manifest:
        manifest = build_manifest(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            embedding_dimension=embedding_dimension,
        )
        save_manifest(manifest, manifest_path)
        print(f"manifest 생성 완료: {manifest_path}")

    return verify_manifest(
        model_id=model_id,
        revision=revision,
        cache_dir=cache_dir,
        embedding_dimension=embedding_dimension,
        manifest_path=manifest_path,
    )


def main() -> None:
    load_dotenv_file()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="설정값만 확인하고 다운로드하지 않는다.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="다운로드하지 않고 기존 manifest 와 캐시 파일을 검증한다.",
    )
    parser.add_argument(
        "--generate-manifest",
        action="store_true",
        help="prefetch 후 manifest 를 새로 생성하고 저장한 뒤 검증한다.",
    )
    args = parser.parse_args()

    try:
        if args.dry_run and args.verify_only:
            raise RuntimeError("--dry-run 과 --verify-only 는 함께 사용할 수 없습니다.")
        if args.generate_manifest and (args.dry_run or args.verify_only):
            raise RuntimeError(
                "--generate-manifest 는 --dry-run 또는 --verify-only 와 "
                "함께 사용할 수 없습니다."
            )
        model_id = resolve_model_id()
        embedding_dimension = resolve_embedding_dimension()
        revision = resolve_revision()
        cache_dir = resolve_cache_dir()
        exit_code = run(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            embedding_dimension=embedding_dimension,
            dry_run=args.dry_run,
            verify_only=args.verify_only,
            generate_manifest=args.generate_manifest,
        )
    except RuntimeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
