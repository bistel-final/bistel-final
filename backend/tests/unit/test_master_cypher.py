"""V4-CM-1.6 strict parser and deterministic graph artifact tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

# V5-CM-1.2 epoch 발급으로 kosa_0813 artifact가 격리돼 깨지는 테스트의 개별 skip.
# 해제 경로는 사유에 적힌 후속 Task가 소유한다(작업계획 §2.5·§6).
SKIP_KOSA_0813 = pytest.mark.skip(
    reason=(
        "kosa_0813 폐기(V5-CM-1.2)"
        " — V5-B-1.1 graph 독립 검증이 재기준화(선행 V5-CM-1.3)"
    )
)


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "master_cypher.py"
_spec = importlib.util.spec_from_file_location("master_cypher", MODULE_PATH)
master = importlib.util.module_from_spec(_spec)
sys.modules["master_cypher"] = master
assert _spec.loader is not None
_spec.loader.exec_module(master)


BOOTSTRAP_ROOT = Path(__file__).resolve().parents[3] / "infra" / "bootstrap"
CORRECTED_PATH = BOOTSTRAP_ROOT / "master_graph.cypher"
MANIFEST_PATH = BOOTSTRAP_ROOT / "manifests" / "neo4j.graph.json"


def _raw_source() -> str:
    corrected = CORRECTED_PATH.read_text(encoding="utf-8")
    seed = re.sub(r" \{relation_id:'REL-[0-9a-f]{20}'\}", "", corrected)
    return "MATCH (n) DETACH DELETE n;\n" + seed


@pytest.fixture(scope="module")
def parsed():
    return master.parse_master_cypher(_raw_source())


def test_registered_artifact_counts_and_first_relation_id(parsed) -> None:
    assert parsed.destructive_statement == "MATCH (n) DETACH DELETE n;"
    assert len(parsed.seed_statements) == 93
    assert len(parsed.corrected_statements) == 93
    assert len(parsed.nodes) == 38
    assert len(parsed.relationships) == 81
    first = parsed.relationships[0]
    assert first.canonical_tuple == (
        "STEP_OF|RecipeStep:recipe_id=s:RECIPE01+recipe_step_no=i:1|"
        "Recipe:recipe_id=s:RECIPE01"
    )
    assert first.relation_id == "REL-cdd155dfdc189254606b"


def test_corrected_artifact_never_contains_destructive_statement(parsed) -> None:
    assert "DETACH DELETE" not in parsed.corrected_text
    assert "DETACH DELETE" not in CORRECTED_PATH.read_text(encoding="utf-8")
    assert CORRECTED_PATH.read_text(encoding="utf-8") == parsed.corrected_text


def test_relationship_ids_are_unique_and_deterministic(parsed) -> None:
    relation_ids = [item.relation_id for item in parsed.relationships]
    assert len(relation_ids) == len(set(relation_ids)) == 81
    assert master.parse_master_cypher(_raw_source()).corrected_text == (
        parsed.corrected_text
    )


def test_direction_changes_relation_id(parsed) -> None:
    relation = parsed.relationships[0]
    reversed_id = master.build_relation_id(
        relation.relation_type,
        relation.to_node,
        relation.from_node,
    )
    assert reversed_id != relation.relation_id


def test_business_id_orders_keys_and_round_trips_escaped_text() -> None:
    value = {"recipe_step_no": 1, "recipe_id": "R|:+\\=한글"}
    serialized = master.serialize_business_id("RecipeStep", value)
    assert serialized.startswith("recipe_id=s:")
    assert master.parse_business_id(serialized) == {
        "recipe_id": "R|:+\\=한글",
        "recipe_step_no": 1,
    }


def test_nfc_equivalent_business_values_have_same_id() -> None:
    composed = master.NodeSpec("Area", {"area_id": "é"})
    decomposed = master.NodeSpec("Area", {"area_id": "e\u0301"})
    other = master.NodeSpec("Recipe", {"recipe_id": "R"})
    assert composed.business_id == decomposed.business_id
    assert master.build_relation_id("X", composed, other) == master.build_relation_id(
        "X", decomposed, other
    )


@pytest.mark.parametrize("value", [True, 1.2, [], {}, None])
def test_unsupported_business_key_type_is_rejected(value) -> None:
    with pytest.raises(master.GraphContractError):
        master.serialize_business_value(value)


def test_string_one_and_integer_one_have_different_business_ids() -> None:
    string = master.serialize_business_value("1")
    integer = master.serialize_business_value(1)
    assert string == "s:1"
    assert integer == "i:1"
    assert string != integer


def test_unknown_label_and_missing_business_key_are_rejected() -> None:
    with pytest.raises(master.GraphContractError):
        master.serialize_business_id("Unknown", {"id": "x"})
    with pytest.raises(master.GraphContractError):
        master.serialize_business_id("Area", {})


@pytest.mark.parametrize(
    "bad_statement",
    [
        "DELETE n;",
        "DROP DATABASE neo4j;",
        "CALL db.info();",
        "CALL apoc.help('x');",
        "LOAD CSV FROM 'file:///x';",
        "CREATE INDEX idx FOR (n:Area) ON (n.area_id);",
    ],
)
def test_forbidden_seed_statements_are_rejected(bad_statement: str) -> None:
    source = _raw_source().replace(
        "MERGE (:Area {area_id:'photo', area_name:'Photolithography'});",
        bad_statement,
    )
    with pytest.raises(master.CypherGrammarError):
        master.parse_master_cypher(source)


def test_forbidden_words_in_string_and_comment_are_not_substring_matches() -> None:
    source = _raw_source().replace(
        "area_name:'Photolithography'});",
        "area_name:'DELETE DROP'}); // CALL db.info()",
    )
    result = master.parse_master_cypher(source)
    assert result.nodes[0].properties["area_name"] == "DELETE DROP"


def test_graph_fingerprint_is_independent_of_input_order(parsed) -> None:
    expected = master.graph_fingerprint(parsed)
    reordered = master.ParsedMasterCypher(
        parsed.destructive_statement,
        parsed.seed_statements,
        parsed.corrected_statements,
        tuple(reversed(parsed.nodes)),
        tuple(reversed(parsed.relationships)),
    )
    assert master.graph_fingerprint(reordered) == expected


def test_expected_and_legacy_fingerprint_are_distinct(parsed) -> None:
    assert master.graph_fingerprint(parsed) != master.graph_fingerprint(
        parsed, legacy=True
    )


@SKIP_KOSA_0813
def test_registered_graph_manifest_is_exact_and_canonical(parsed) -> None:
    actual_bytes = MANIFEST_PATH.read_bytes()
    actual = json.loads(actual_bytes)
    expected = master.build_graph_manifest(parsed)
    assert actual == expected
    assert actual_bytes == master.canonical_json_bytes(expected) + b"\n"
    master.validate_graph_manifest(actual)
    assert actual["node_count"] == 38
    assert actual["relationship_count"] == 81


def test_manifest_extra_key_and_fixed_value_drift_are_rejected(parsed) -> None:
    manifest = master.build_graph_manifest(parsed)
    extra = copy.deepcopy(manifest)
    extra["comment"] = "not allowed"
    with pytest.raises(master.GraphManifestError):
        master.validate_graph_manifest(extra)
    drift = copy.deepcopy(manifest)
    drift["hash_algorithm"] = "sha256"
    with pytest.raises(master.GraphManifestError):
        master.validate_graph_manifest(drift)


def test_generated_artifacts_are_atomic_and_reproducible(
    parsed, tmp_path: Path
) -> None:
    cypher = tmp_path / "master_graph.cypher"
    manifest = tmp_path / "neo4j.graph.json"
    first = master.write_generated_artifacts(
        parsed, cypher_path=cypher, manifest_path=manifest
    )
    first_bytes = (cypher.read_bytes(), manifest.read_bytes())
    second = master.write_generated_artifacts(
        parsed, cypher_path=cypher, manifest_path=manifest
    )
    assert second == first
    assert (cypher.read_bytes(), manifest.read_bytes()) == first_bytes


@SKIP_KOSA_0813
def test_wrong_archive_is_rejected_before_parse(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(master.SOURCE_MEMBER_PATH, _raw_source())
    with pytest.raises(master.SourceGuardError, match="archive SHA"):
        master.load_registered_source(archive)


def test_registered_member_hash_matches_reconstructed_source_shape() -> None:
    # The delivered member is CRLF. Reconstructing it proves the committed
    # corrected artifact still corresponds to the registered source member.
    raw_crlf = _raw_source().replace("\n", "\r\n").encode()
    assert master.sha256_bytes(raw_crlf) == master.SOURCE_MEMBER_SHA256
