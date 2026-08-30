"""Canonical API model and offline generator contracts for V5-CM-5.1."""

from __future__ import annotations

import ast
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPOSITORY_ROOT / "docs" / "deliverables" / "api"
CANONICAL = API_ROOT / "api_spec_v3.json"
GENERATOR = API_ROOT / "build_api_spec.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_api_spec", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_model_is_sorted_full_schema_and_has_three_populations() -> None:
    raw = CANONICAL.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert raw == json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert len(value["operations"]) == 35
    assert sum(item["release_required"] for item in value["operations"]) == 19
    assert sum(item["semantic"] is not None for item in value["operations"]) == 24
    assert sum(item["implemented"] for item in value["operations"]) == 23
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    assert "discriminated_union" in encoded
    assert "^[0-9a-f]{64}$" in encoded
    assert '"additional_properties"' in encoded
    assert {item["event"] for item in value["audit_events"]} >= {
        "AGENT_RUN_STARTED",
        "ACTION_SEND_FAILED",
    }
    assert all(
        operation["rules"] == sorted(set(operation["rules"]))
        for operation in value["operations"]
    )
    for operation in value["operations"]:
        semantic = operation["semantic"]
        if semantic is not None:
            assert '"rules"' not in json.dumps(semantic, ensure_ascii=False)

    def assert_sorted_enums(node: object) -> None:
        if isinstance(node, dict):
            if "enum" in node:
                assert node["enum"] == sorted(set(node["enum"]))
            for child in node.values():
                assert_sorted_enums(child)
        elif isinstance(node, list):
            for child in node:
                assert_sorted_enums(child)

    assert_sorted_enums(value)


def test_generator_is_offline_and_summary_projection_is_exact() -> None:
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert {"app", "fastapi", "pydantic", "sqlalchemy"}.isdisjoint(imports)

    generator = _load_generator()
    canonical = generator.load_spec()
    generator.validate_summary_projection(canonical)
    rendered = generator.render_csv(canonical)
    rows = list(csv.DictReader(io.StringIO(rendered.removeprefix("\ufeff"))))
    assert len(rows) == 35
    assert rows[-7]["Path"] == "/analytics/graph-query"


def test_generated_markdown_keeps_schema_errors_audit_and_deferred_inventory() -> None:
    generator = _load_generator()
    rendered = generator.render_markdown(generator.load_spec())
    assert "API inventory — 35개" in rendered
    assert "discriminated_union" in rendered
    assert "^[0-9a-f]{64}$" in rendered
    assert "ACTION_SEND_FAILED" in rendered
    assert "계약 규칙:" in rendered
    assert "deferred inventory" in rendered
    assert "v2.1" not in rendered


def test_validator_rejects_nested_schema_loss() -> None:
    generator = _load_generator()
    value = generator.load_spec()
    value["operations"][0]["semantic"]["responses"]["200"]["schema"] = {}
    try:
        generator.validate_spec(value)
    except generator.ApiSpecError as exc:
        assert "schema type" in str(exc)
    else:
        raise AssertionError("nested schema loss must be rejected")


def test_validator_rejects_unsorted_enum_storage() -> None:
    generator = _load_generator()
    value = generator.load_spec()
    mutated = copy.deepcopy(value)
    enum = mutated["operations"][0]["semantic"]["request"]["query"]["area"]["schema"][
        "enum"
    ]
    enum.reverse()
    try:
        generator.validate_spec(mutated)
    except generator.ApiSpecError as exc:
        assert "sorted unique string list" in str(exc)
    else:
        raise AssertionError("unsorted enum storage must be rejected")


def test_validator_rejects_rules_nested_inside_semantic_contract() -> None:
    generator = _load_generator()
    value = generator.load_spec()
    mutated = copy.deepcopy(value)
    mutated["operations"][0]["semantic"]["responses"]["200"]["schema"]["rules"] = [
        "documentation-only rule"
    ]
    try:
        generator.validate_spec(mutated)
    except generator.ApiSpecError as exc:
        assert "operation-level metadata" in str(exc)
    else:
        raise AssertionError("semantic rules must be rejected")
