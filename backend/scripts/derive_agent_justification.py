#!/usr/bin/env python3
"""V5-C-7.1 v1 observational artifact의 fail-closed v2 재파생기."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import comparison  # noqa: E402


def _git(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise comparison.ComparisonArtifactError("DERIVATION_SOURCE_CHANGED") from exc
    return completed.stdout


def _verify_clean_revision(expected: str) -> str:
    if not comparison.REVISION.fullmatch(expected):
        raise comparison.ComparisonArtifactError("REVISION_MISMATCH")
    head = _git("rev-parse", "HEAD").strip()
    dirty = _git("status", "--porcelain")
    if head != expected or dirty:
        raise comparison.ComparisonArtifactError("REVISION_MISMATCH")
    return head


def _changed_lines(diff: str) -> list[str]:
    forbidden_metadata = (
        "old mode ",
        "new mode ",
        "deleted file mode ",
        "new file mode ",
        "similarity index ",
        "dissimilarity index ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "Binary files ",
        "GIT binary patch",
    )
    if any(
        line.startswith(forbidden_metadata) or line == "GIT binary patch"
        for line in diff.splitlines()
    ):
        raise comparison.ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
    return [
        line
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    ]


def inspect_derivation_source(
    *,
    run_revision: str,
    source_revision: str,
) -> dict[str, Any]:
    changed = sorted(
        line
        for line in _git(
            "diff", "--name-only", f"{run_revision}..{source_revision}", "--", "backend"
        ).splitlines()
        if line
    )
    script_hunks = {
        path: _changed_lines(
            _git(
                "diff",
                "--no-ext-diff",
                "--unified=0",
                f"{run_revision}..{source_revision}",
                "--",
                path,
            )
        )
        for path in comparison.DERIVATION_STRICT_SCRIPTS
    }
    return comparison.build_derivation_source_check(
        base_revision=run_revision,
        head_revision=source_revision,
        changed_backend_files=changed,
        script_hunks=script_hunks,
    )


def execute(
    *,
    source: Path,
    level_comparison: Path,
    revision: str,
    artifact: Path,
) -> tuple[str, Mapping[str, Any]]:
    current_revision = _verify_clean_revision(revision)
    level_payload = comparison.load_json(level_comparison)
    comparison.validate_level_comparison(level_payload)
    level_sha256 = comparison.file_sha256(level_comparison)
    level_revision = str(level_payload.get("source_revision", ""))
    source_payload = comparison.load_json(source)
    source_sha256 = comparison.file_sha256(source)
    comparison.validate_agent_justification(
        source_payload,
        level_comparison_sha256=level_sha256,
        level_comparison_revision=level_revision,
    )
    run_revision = str(source_payload.get("source_revision", ""))
    source_check = inspect_derivation_source(
        run_revision=run_revision,
        source_revision=current_revision,
    )
    payload = comparison.derive_agent_justification_v2(
        source_payload,
        source_revision=current_revision,
        derived_from_sha256=source_sha256,
        level_comparison_sha256=level_sha256,
        level_comparison_revision=level_revision,
        derivation_source_check=source_check,
    )
    digest = comparison.write_immutable_json(artifact, payload)
    return digest, source_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="derived_from", type=Path, required=True)
    parser.add_argument("--level-comparison", type=Path, required=True)
    parser.add_argument("--expect-revision", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest, source_check = execute(
            source=args.derived_from,
            level_comparison=args.level_comparison,
            revision=args.expect_revision,
            artifact=args.artifact,
        )
    except comparison.ComparisonArtifactError as exc:
        print(json.dumps({"reason_code": exc.code}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"reason_code": "DERIVATION_FAILED"}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"event": "DERIVATION_SOURCE_CHECK", **source_check},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    print(f"AGENT_JUSTIFICATION_V2_WRITTEN sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
