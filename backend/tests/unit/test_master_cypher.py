"""V5-B-3.1 final master.cypher offline parser contract tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "master_cypher.py"
_spec = importlib.util.spec_from_file_location("master_cypher", MODULE_PATH)
master = importlib.util.module_from_spec(_spec)
sys.modules["master_cypher"] = master
assert _spec.loader is not None
_spec.loader.exec_module(master)


FINAL_EPOCH = "fdc_final_20260818"
FINAL_ARCHIVE_SHA256 = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)
FINAL_MEMBER_PATH = "project/repository/sample/ontology/master.cypher"
FINAL_MEMBER_SHA256 = (
    "51604707c9a0f3bc97b21773b7bd43d0049f2dacf322042c36f090ec63c74eea"
)

EXPECTED_LABEL_DISTRIBUTION = {
    "Area": 2,
    "Chamber": 12,
    "Equipment": 6,
    "EquipmentModel": 2,
    "Parameter": 8,
    "ProcessStep": 2,
    "Recipe": 4,
    "RecipeStep": 8,
}
EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION = {
    "IN_AREA": 4,
    "MEASURED_ON": 48,
    "NEXT_STEP": 1,
    "OF_MODEL": 6,
    "PART_OF": 12,
    "PERFORMS": 6,
    "STEP_OF": 8,
}


def _final_source() -> str:
    statements = ["MATCH (n) DETACH DELETE n;"]
    statements.extend(
        [
            "MERGE (:Area {area_id:'Photo', area_name:'Photolithography'});",
            "MERGE (:Area {area_id:'Etch', area_name:'Etching'});",
        ]
    )

    for recipe_no in range(1, 5):
        recipe_id = f"RECIPE{recipe_no:02d}"
        statements.append(
            f"MERGE (:Recipe {{recipe_id:'{recipe_id}', "
            f"recipe_name:'{recipe_id}'}});"
        )
        for step_no in range(1, 3):
            statements.append(
                f"MERGE (rs:RecipeStep {{recipe_id:'{recipe_id}', "
                f"recipe_step_no:{step_no}}}) "
                f"SET rs.step_name='{recipe_id}-Step-{step_no}' "
                f"WITH rs MATCH (r:Recipe {{recipe_id:'{recipe_id}'}}) "
                "MERGE (rs)-[:STEP_OF]->(r);"
            )

    process_steps = [
        ("CT-PHOTO", "Critical Photo", "Photo"),
        ("CT-ETCH", "Critical Etch", "Etch"),
    ]
    for step_id, step_name, area_id in process_steps:
        statements.append(
            f"MERGE (ps:ProcessStep {{step_id:'{step_id}'}}) "
            f"SET ps.step_name='{step_name}' "
            f"WITH ps MATCH (a:Area {{area_id:'{area_id}'}}) "
            "MERGE (ps)-[:IN_AREA]->(a);"
        )
    statements.append(
        "MATCH (a:ProcessStep {step_id:'CT-PHOTO'}) "
        "MATCH (b:ProcessStep {step_id:'CT-ETCH'}) "
        "MERGE (a)-[:NEXT_STEP]->(b);"
    )

    models = [
        ("PH-MODEL", "Photo Model", "Photo", "CT-PHOTO"),
        ("ET-MODEL", "Etch Model", "Etch", "CT-ETCH"),
    ]
    for model_code, model_name, area_id, _step_id in models:
        statements.append(
            f"MERGE (m:EquipmentModel {{model_code:'{model_code}'}}) "
            f"SET m.model_name='{model_name}' "
            f"WITH m MATCH (a:Area {{area_id:'{area_id}'}}) "
            "MERGE (m)-[:IN_AREA]->(a);"
        )

    equipment_by_step: dict[str, list[str]] = {"CT-PHOTO": [], "CT-ETCH": []}
    chamber_by_area: dict[str, list[str]] = {"Photo": [], "Etch": []}
    for prefix, model_code, step_id, area_id in [
        ("PH", "PH-MODEL", "CT-PHOTO", "Photo"),
        ("ET", "ET-MODEL", "CT-ETCH", "Etch"),
    ]:
        for equipment_no in range(1, 4):
            equipment_id = f"EQP-{prefix}-{equipment_no:02d}"
            equipment_by_step[step_id].append(equipment_id)
            statements.append(
                f"MERGE (e:Equipment {{equipment_id:'{equipment_id}'}}) "
                f"SET e.equipment_name='{equipment_id}' "
                f"WITH e MATCH (m:EquipmentModel {{model_code:'{model_code}'}}) "
                "MERGE (e)-[:OF_MODEL]->(m);"
            )
            statements.append(
                f"MATCH (e:Equipment {{equipment_id:'{equipment_id}'}}) "
                f"MATCH (ps:ProcessStep {{step_id:'{step_id}'}}) "
                "MERGE (e)-[:PERFORMS]->(ps);"
            )
            for chamber_suffix in ("A", "B"):
                chamber_id = f"CH-{prefix}-{equipment_no:02d}-{chamber_suffix}"
                chamber_by_area[area_id].append(chamber_id)
                statements.append(
                    f"MERGE (c:Chamber {{chamber_id:'{chamber_id}'}}) "
                    f"SET c.chamber_name='{chamber_id}' "
                    f"WITH c MATCH (e:Equipment {{equipment_id:'{equipment_id}'}}) "
                    "MERGE (c)-[:PART_OF]->(e);"
                )

    parameters_by_area = {
        "Photo": ["PHOTO-CD", "PHOTO-OVERLAY", "PHOTO-DOSE", "PHOTO-FOCUS"],
        "Etch": ["ETCH-RATE", "ETCH-UNIFORMITY", "ETCH-DEPTH", "ETCH-CD"],
    }
    for parameter_ids in parameters_by_area.values():
        for parameter_id in parameter_ids:
            statements.append(
                f"MERGE (:Parameter {{parameter_id:'{parameter_id}', "
                f"parameter_name:'{parameter_id}'}});"
            )
    for area_id, parameter_ids in parameters_by_area.items():
        for parameter_id in parameter_ids:
            for chamber_id in chamber_by_area[area_id]:
                statements.append(
                    f"MATCH (p:Parameter {{parameter_id:'{parameter_id}'}}) "
                    f"MATCH (c:Chamber {{chamber_id:'{chamber_id}'}}) "
                    "MERGE (p)-[:MEASURED_ON]->(c);"
                )

    # The delivered final source has 100 seed statements; this no-op locks that
    # count without changing the validated graph shape.
    statements.append("MATCH (a:Area {area_id:'Photo'});")
    return "\n".join(statements) + "\n"


@pytest.fixture(scope="module")
def parsed():
    return master.parse_master_cypher(_final_source())


def test_final_source_contract_constants() -> None:
    assert master.DATASET_EPOCH == FINAL_EPOCH
    assert master.SOURCE_ARCHIVE_SHA256 == FINAL_ARCHIVE_SHA256
    assert master.SOURCE_MEMBER_PATH == FINAL_MEMBER_PATH
    assert master.SOURCE_MEMBER_SHA256 == FINAL_MEMBER_SHA256


def test_registered_artifact_counts_and_first_relation_id(parsed) -> None:
    assert parsed.destructive_statement == "MATCH (n) DETACH DELETE n;"
    assert len(parsed.seed_statements) == 100
    assert len(parsed.corrected_statements) == 100
    assert len(parsed.nodes) == 44
    assert len(parsed.relationships) == 85
    first = parsed.relationships[0]
    assert first.canonical_tuple == (
        "STEP_OF|RecipeStep:recipe_id=s:RECIPE01+recipe_step_no=i:1|"
        "Recipe:recipe_id=s:RECIPE01"
    )
    assert first.relation_id == "REL-cdd155dfdc189254606b"


def test_final_label_and_relationship_type_distribution(parsed) -> None:
    assert dict(Counter(node.label for node in parsed.nodes)) == (
        EXPECTED_LABEL_DISTRIBUTION
    )
    assert dict(Counter(item.relation_type for item in parsed.relationships)) == (
        EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION
    )


def test_corrected_artifact_never_contains_destructive_statement(parsed) -> None:
    assert "DETACH DELETE" not in parsed.corrected_text
    assert "MATCH (n) DETACH DELETE n;" not in parsed.corrected_statements


def test_relationship_ids_are_unique_and_deterministic(parsed) -> None:
    relation_ids = [item.relation_id for item in parsed.relationships]
    assert len(relation_ids) == len(set(relation_ids)) == 85
    assert master.parse_master_cypher(_final_source()).corrected_text == (
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
    source = _final_source().replace(
        "MERGE (:Area {area_id:'Photo', area_name:'Photolithography'});",
        bad_statement,
    )
    with pytest.raises(master.CypherGrammarError):
        master.parse_master_cypher(source)


def test_forbidden_words_in_string_and_comment_are_not_substring_matches() -> None:
    source = _final_source().replace(
        "area_name:'Photolithography'});",
        "area_name:'DELETE DROP'}); // CALL db.info()",
    )
    result = master.parse_master_cypher(source)
    assert result.nodes[0].properties["area_name"] == "DELETE DROP"


def test_unsupported_relationship_statement_is_rejected() -> None:
    source = _final_source().replace(
        "MATCH (a:Area {area_id:'Photo'});",
        "MATCH (a:Area {area_id:'Photo'}) "
        "MATCH (b:Area {area_id:'Etch'}) "
        "MERGE (a)<-[:UNSUPPORTED]-(b);",
    )
    with pytest.raises(master.CypherGrammarError, match="relationship grammar"):
        master.parse_master_cypher(source)


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


def test_graph_manifest_is_exact_and_canonical(parsed) -> None:
    expected = master.build_graph_manifest(parsed)
    master.validate_graph_manifest(expected)
    assert expected["dataset_epoch"] == FINAL_EPOCH
    assert expected["source_archive_sha256"] == FINAL_ARCHIVE_SHA256
    assert expected["source_member_sha256"] == FINAL_MEMBER_SHA256
    assert expected["node_count"] == 44
    assert expected["relationship_count"] == 85
    assert expected["label_distribution"] == EXPECTED_LABEL_DISTRIBUTION
    assert expected["relationship_type_distribution"] == (
        EXPECTED_RELATIONSHIP_TYPE_DISTRIBUTION
    )
    assert json.loads(master.canonical_json_bytes(expected)) == expected


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


def _write_epoch(path: Path, *, archive_sha256: str, member_sha256: str) -> None:
    payload = {
        "dataset_epoch": FINAL_EPOCH,
        "archive": {
            "path": "project.zip",
            "sha256": archive_sha256,
        },
        "file_inventory": [
            {
                "path": FINAL_MEMBER_PATH,
                "sha256": member_sha256,
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_wrong_archive_is_rejected_before_parse(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.zip"
    epoch = tmp_path / "dataset-epoch.json"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(FINAL_MEMBER_PATH, _final_source())
    _write_epoch(
        epoch,
        archive_sha256=FINAL_ARCHIVE_SHA256,
        member_sha256=FINAL_MEMBER_SHA256,
    )
    with pytest.raises(master.SourceGuardError, match="archive SHA"):
        master.load_registered_source(archive, epoch_path=epoch)


def test_source_member_hash_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "source.zip"
    epoch = tmp_path / "dataset-epoch.json"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(FINAL_MEMBER_PATH, _final_source())
    archive_sha256 = master.sha256_file(archive)
    monkeypatch.setattr(master, "SOURCE_ARCHIVE_SHA256", archive_sha256)
    _write_epoch(
        epoch,
        archive_sha256=archive_sha256,
        member_sha256="0" * 64,
    )
    with pytest.raises(master.SourceGuardError, match="master.cypher"):
        master.load_registered_source(archive, epoch_path=epoch)
