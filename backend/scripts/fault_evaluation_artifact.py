"""C-6.2 metric artifact의 배타·불변 저장 adapter."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from bootstrap_common import (  # noqa: E402
    ReferenceArtifactError,
    _exclusive_artifact_lock,
)
from e2e_reset_evidence import (  # noqa: E402
    EvidenceError,
    write_atomic_receipt,
)
from manifest_v3 import ManifestSchemaError  # noqa: E402


class FaultArtifactWriteError(RuntimeError):
    """artifact를 안전하게 게시하지 못했다."""


def write_fault_evaluation_artifact(
    path: Path,
    payload: Mapping[str, Any],
) -> str:
    """목적지별 lock 안에서 fsync된 inode를 no-clobber 방식으로 게시한다."""

    lock_path = path.parent / f".{path.name}.lock"
    try:
        with _exclusive_artifact_lock(lock_path):
            return write_atomic_receipt(path, payload)
    except (
        EvidenceError,
        ManifestSchemaError,
        ReferenceArtifactError,
        OSError,
    ) as exc:
        raise FaultArtifactWriteError("ARTIFACT_WRITE_FAILED") from exc


__all__ = ["FaultArtifactWriteError", "write_fault_evaluation_artifact"]
