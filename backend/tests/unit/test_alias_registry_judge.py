"""Compatibility alias scanner and removal-judge truth tables."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts import judge_alias_removal as judge
from scripts.alias_registry_scan import source_reads_any_alias

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = (
    REPOSITORY_ROOT
    / "docs"
    / "deliverables"
    / "api"
    / "compatibility_alias_registry.json"
)
SCANNER = REPOSITORY_ROOT / "backend" / "scripts" / "alias_registry_scan.py"


def test_scanner_module_has_no_application_import_side_effect() -> None:
    tree = ast.parse(SCANNER.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert {"app", "fastapi", "pydantic", "sqlalchemy"}.isdisjoint(imports)
    assert source_reads_any_alias("row.doc_id", ["doc_id"])
    assert source_reads_any_alias("row?.doc_id", ["doc_id"])
    assert source_reads_any_alias("row['doc_id']", ["doc_id"])
    assert not source_reads_any_alias("const doc_id = 'local'", ["doc_id"])


def test_entry_truth_table_and_sanitized_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "canonical_fields": ["canonical"],
        "compatibility_fields": ["alias"],
        "consumer_scan_ignores": [],
        "consumers": [],
        "id": "a-test",
        "kind": "dto_field",
        "symbol": "backend.app.Example",
    }
    monkeypatch.setattr(
        judge, "derived_feature_consumer_paths", lambda *args, **kwargs: set()
    )
    monkeypatch.setattr(
        judge,
        "replacement_live",
        lambda *args, **kwargs: (True, "canonical_fields_exist"),
    )
    result = judge.evaluate_entry(entry)
    assert result["status"] == "BLOCKED"
    assert result["checks"]["consumer_zero"] == {
        "evidence": "declared:0;scan:0",
        "status": "PASS",
    }
    assert "/" not in result["checks"]["consumer_zero"]["evidence"]

    entry["consumers"] = ["frontend/src/features/example.js#Example"]
    result = judge.evaluate_entry(entry)
    assert result["status"] == "OPEN"
    assert result["checks"]["consumer_zero"]["status"] == "FAIL"


def test_exit_code_priority_is_open_then_blocked_then_ready() -> None:
    assert judge.exit_code({"status_counts": {"OPEN": 1, "BLOCKED": 1}}) == 1
    assert judge.exit_code({"status_counts": {"OPEN": 0, "BLOCKED": 1}}) == 2
    assert judge.exit_code({"status_counts": {"OPEN": 0, "BLOCKED": 0}}) == 0


def test_real_registry_is_reproducible_and_never_implicitly_ready() -> None:
    registry, digest = judge._load_registry(REGISTRY)
    report = judge.judge_registry(
        registry,
        registry_sha256=digest,
        generated_from_revision="0" * 40,
    )
    assert [item["id"] for item in report["entries"]] == sorted(
        item["id"] for item in report["entries"]
    )
    assert sum(report["status_counts"].values()) == 25
    assert report["status_counts"]["READY"] == 0
    assert all(
        item["checks"]["canonical_contract_green"]["status"] == "MANUAL"
        and item["checks"]["baseline_revision_ready"]["status"] == "MANUAL"
        for item in report["entries"]
    )
    assert json.loads(json.dumps(report, sort_keys=True)) == report


def test_atomic_report_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "judge.json"
    judge.write_atomic_receipt(path, {"first": True})
    with pytest.raises(judge.EvidenceError, match="RECEIPT_ALREADY_EXISTS"):
        judge.write_atomic_receipt(path, {"second": True})
