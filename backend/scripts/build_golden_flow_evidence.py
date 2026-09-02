"""C-6.1 golden-flow evidence manifest(`golden-flow-e2e.md` §6)를 디렉터리에서 만든다.

레이아웃(exact): ``<root>/artifacts/<PHASE>/<kind-prefix>-<name>.(json|ndjson)``

| kind 접두어 | ArtifactKind | media_type |
|---|---|---|
| ``snapshot-`` | DB_SNAPSHOT | application/json |
| ``batch-`` | BATCH_NDJSON | application/x-ndjson |
| ``http-`` | HTTP_RESULTS | application/json |
| ``n8n-`` | N8N_EXECUTIONS | application/json |
| ``kafka-`` | KAFKA_OFFSETS | application/json |
| ``smtp-`` | SMTP_RECEIPT | application/json |

phase 디렉터리 이름은 7 phase exact, scope는 ``MANUAL_RETRY``만 ``ISOLATED_CONTAINER``
(``--manual-retry-public``으로 PUBLIC_E2E 선택), 나머지는 ``PUBLIC_E2E``다. SHA-256은
파일 bytes로 계산하며 manifest는 ``<root>/evidence.json``에 0600으로 쓴다(덮어쓰기
금지).
artifact 내용은 검증하지 않는다 — 계약 검증은 ``verify_golden_flow.py`` 몫이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

try:
    from . import e2e_reset_evidence as evidence
except ImportError:  # pragma: no cover - 컨테이너에서는 scripts/ 가 sys.path 루트다.
    import e2e_reset_evidence as evidence

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

DATASET_EPOCH = "fdc_final_20260818"
GATE_KIND = "PUBLIC_GOLDEN_FLOW"
LEVEL_ROUND = 2
PHASES = (
    "PREFLIGHT",
    "BATCH_BASELINE",
    "PRE_APPROVAL",
    "DECISIONS",
    "UNKNOWN",
    "MANUAL_RETRY",
    "SECOND_BATCH",
)
KIND_BY_PREFIX = {
    "snapshot": ("DB_SNAPSHOT", "application/json", ".json"),
    "batch": ("BATCH_NDJSON", "application/x-ndjson", ".ndjson"),
    "http": ("HTTP_RESULTS", "application/json", ".json"),
    "n8n": ("N8N_EXECUTIONS", "application/json", ".json"),
    "kafka": ("KAFKA_OFFSETS", "application/json", ".json"),
    "smtp": ("SMTP_RECEIPT", "application/json", ".json"),
}


class EvidenceLayoutError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _emit("BLOCKED", "ARG_INVALID")
        raise SystemExit(EXIT_USAGE)


def _emit(status: str, reason_code: str, **safe: Any) -> None:
    print(
        json.dumps(
            {"reason_code": reason_code, "status": status, **safe},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manual-retry-public", action="store_true")
    return parser


def _artifact_entry(phase: str, path: Path, root: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceLayoutError("ARTIFACT_NOT_REGULAR_FILE")
    prefix, _, remainder = path.name.partition("-")
    if prefix not in KIND_BY_PREFIX or not remainder:
        raise EvidenceLayoutError("ARTIFACT_PREFIX_INVALID")
    kind, media_type, suffix = KIND_BY_PREFIX[prefix]
    if path.suffix != suffix:
        raise EvidenceLayoutError("ARTIFACT_SUFFIX_INVALID")
    relative = path.relative_to(root).as_posix()
    return {
        "artifact_id": f"{phase.lower()}-{path.stem}",
        "kind": kind,
        "relative_path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "phase": phase,
        "level_round": LEVEL_ROUND,
        "media_type": media_type,
    }


def build_manifest(root: Path, *, manual_retry_public: bool = False) -> dict[str, Any]:
    artifacts_root = root / "artifacts"
    if root.is_symlink() or not root.is_dir() or not artifacts_root.is_dir():
        raise EvidenceLayoutError("EVIDENCE_ROOT_INVALID")
    unexpected = {child.name for child in artifacts_root.iterdir()} - set(PHASES)
    if unexpected:
        raise EvidenceLayoutError("PHASE_DIRECTORY_UNKNOWN")
    phases: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    for phase in PHASES:
        directory = artifacts_root / phase
        if not directory.is_dir():
            raise EvidenceLayoutError("PHASE_DIRECTORY_MISSING")
        entries = [
            _artifact_entry(phase, path, root) for path in sorted(directory.iterdir())
        ]
        if not any(item["kind"] == "DB_SNAPSHOT" for item in entries):
            raise EvidenceLayoutError("PHASE_SNAPSHOT_MISSING")
        scope = "PUBLIC_E2E"
        if phase == "MANUAL_RETRY" and not manual_retry_public:
            scope = "ISOLATED_CONTAINER"
        phases[phase] = {
            "execution_scope": scope,
            "artifact_ids": [item["artifact_id"] for item in entries],
        }
        artifacts.extend(entries)
    ids = [item["artifact_id"] for item in artifacts]
    if len(ids) != len(set(ids)):
        raise EvidenceLayoutError("ARTIFACT_ID_DUPLICATE")
    return {
        "format_version": 1,
        "dataset_epoch": DATASET_EPOCH,
        "gate_kind": GATE_KIND,
        "level_round": LEVEL_ROUND,
        "phases": phases,
        "artifacts": artifacts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.root.is_absolute():
        _emit("BLOCKED", "ROOT_NOT_ABSOLUTE")
        return EXIT_USAGE
    try:
        manifest = build_manifest(
            args.root, manual_retry_public=args.manual_retry_public
        )
        sha256 = evidence.write_atomic_receipt(args.root / "evidence.json", manifest)
    except EvidenceLayoutError as exc:
        _emit("BLOCKED", exc.reason)
        return EXIT_BLOCKED
    except evidence.EvidenceError as exc:
        _emit("BLOCKED", exc.reason_code)
        return EXIT_BLOCKED
    _emit(
        "PASSED",
        "GOLDEN_EVIDENCE_MANIFEST_WRITTEN",
        artifact_count=len(manifest["artifacts"]),
        sha256=sha256,
        output=str(args.root / "evidence.json"),
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
