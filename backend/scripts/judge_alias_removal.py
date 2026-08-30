"""Evidence-based V5 compatibility-alias removal judge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.alias_registry_scan import (  # noqa: E402
    REPOSITORY_ROOT,
    derived_feature_consumer_paths,
    replacement_live,
)

DEFAULT_REGISTRY = (
    REPOSITORY_ROOT
    / "docs"
    / "deliverables"
    / "api"
    / "compatibility_alias_registry.json"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "output" / "V5-CM-5.1_alias-removal.json"
CHECK_NAMES = {
    "baseline_revision_ready",
    "canonical_contract_green",
    "consumer_zero",
}


class AliasJudgeError(ValueError):
    """Registry input is invalid and no removal verdict can be issued."""


class EvidenceError(ValueError):
    """The no-clobber output contract was violated."""


def write_atomic_receipt(path: Path, payload: Mapping[str, Any]) -> str:
    """Publish a complete JSON inode without overwriting an existing verdict."""

    # Keep this CLI-local: importing the rebuild runner would couple an offline
    # alias judge to its execution registry.  ``os.link`` provides the same
    # cross-process no-clobber guarantee after the temporary file is fsynced.
    if path.exists():
        raise EvidenceError("RECEIPT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(raw)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise EvidenceError("RECEIPT_ALREADY_EXISTS") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(raw).hexdigest()


def _load_registry(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        registry = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AliasJudgeError(f"registry cannot be read: {path}") from exc
    if not isinstance(registry, dict) or registry.get("format_version") != 1:
        raise AliasJudgeError("registry format_version must be 1")
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AliasJudgeError("registry entries must be non-empty")
    ids = [entry.get("id") for entry in entries if isinstance(entry, Mapping)]
    if len(ids) != len(entries) or any(not isinstance(value, str) for value in ids):
        raise AliasJudgeError("every registry entry needs an id")
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise AliasJudgeError("registry ids must be sorted and unique")
    for entry in entries:
        checks = {
            item.get("check")
            for item in entry.get("removal_conditions", [])
            if isinstance(item, Mapping)
        }
        if checks != CHECK_NAMES:
            raise AliasJudgeError(f"removal checks drift: {entry['id']}")
    return registry, hashlib.sha256(raw).hexdigest()


def _revision(repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AliasJudgeError("git revision cannot be resolved") from exc
    revision = result.stdout.strip()
    if len(revision) != 40:
        raise AliasJudgeError("git revision must be a 40-character SHA")
    return revision


def evaluate_entry(
    entry: dict[str, Any], *, repository_root: Path = REPOSITORY_ROOT
) -> dict[str, Any]:
    ignored = {
        item["reference"]
        for item in entry.get("consumer_scan_ignores", [])
        if isinstance(item, Mapping) and isinstance(item.get("reference"), str)
    }
    detected = (
        derived_feature_consumer_paths(entry, repository_root=repository_root) - ignored
    )
    declared = entry.get("consumers", [])
    consumer_pass = isinstance(declared, list) and not declared and not detected
    live, basis = replacement_live(entry, repository_root=repository_root)
    declared_count = len(declared) if isinstance(declared, list) else -1
    checks = {
        "baseline_revision_ready": {"evidence": None, "status": "MANUAL"},
        "canonical_contract_green": {"evidence": None, "status": "MANUAL"},
        "consumer_zero": {
            "evidence": f"declared:{declared_count};scan:{len(detected)}",
            "status": "PASS" if consumer_pass else "FAIL",
        },
    }
    statuses = {item["status"] for item in checks.values()}
    if "FAIL" in statuses:
        status = "OPEN"
    elif not live or "MANUAL" in statuses:
        status = "BLOCKED"
    else:
        status = "READY"
    return {
        "checks": checks,
        "id": entry["id"],
        "prerequisite": {
            "prerequisite_basis": basis,
            "replacement_live": "PASS" if live else "FAIL",
        },
        "status": status,
    }


def judge_registry(
    registry: Mapping[str, Any],
    *,
    registry_sha256: str,
    generated_from_revision: str,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise AliasJudgeError("registry entries must be an array")
    judged = [
        evaluate_entry(entry, repository_root=repository_root)
        for entry in entries
        if isinstance(entry, dict)
    ]
    judged.sort(key=lambda item: item["id"])
    counts = Counter(item["status"] for item in judged)
    return {
        "entries": judged,
        "format_version": 1,
        "generated_from_revision": generated_from_revision,
        "registry_sha256": registry_sha256,
        "status_counts": {
            "BLOCKED": counts["BLOCKED"],
            "OPEN": counts["OPEN"],
            "READY": counts["READY"],
        },
    }


def exit_code(report: Mapping[str, Any]) -> int:
    counts = report["status_counts"]
    if counts["OPEN"]:
        return 1
    if counts["BLOCKED"]:
        return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        registry, digest = _load_registry(args.registry)
        report = judge_registry(
            registry,
            registry_sha256=digest,
            generated_from_revision=_revision(REPOSITORY_ROOT),
        )
        write_atomic_receipt(args.output, report)
    except EvidenceError as exc:
        parser.exit(2, f"ARTIFACT_EXISTS: {exc}\n")
    except (AliasJudgeError, OSError, ValueError) as exc:
        parser.exit(2, f"ALIAS_JUDGE_INPUT_ERROR: {exc}\n")
    counts = report["status_counts"]
    print(
        f"{args.output} READY={counts['READY']} OPEN={counts['OPEN']} "
        f"BLOCKED={counts['BLOCKED']}"
    )
    raise SystemExit(exit_code(report))


if __name__ == "__main__":
    main()
