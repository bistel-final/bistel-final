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
FINAL_MEMBER_SHA256 = "51604707c9a0f3bc97b21773b7bd43d0049f2dacf322042c36f090ec63c74eea"
FINAL_GRAPH_FINGERPRINT = (
    "3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb"
)
FINAL_LEGACY_FINGERPRINT = (
    "1da0087e7a7c02182e446a220f173d8819071a6353d818926e6ff2acf9e7278f"
)
FINAL_CORRECTED_SHA256 = (
    "0fb10c62b5443efdbc794c55e91df2589261872ff99f9ddb5ab31b30679a84f4"
)
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "master.cypher"

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


def _actual_source() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def parsed():
    return master.parse_master_cypher(_actual_source())


def test_final_source_contract_constants() -> None:
    assert master.DATASET_EPOCH == FINAL_EPOCH
    assert master.SOURCE_ARCHIVE_SHA256 == FINAL_ARCHIVE_SHA256
    assert master.SOURCE_MEMBER_PATH == FINAL_MEMBER_PATH
    assert master.SOURCE_MEMBER_SHA256 == FINAL_MEMBER_SHA256


#: tracked fixture의 **raw bytes** SHA-256. archive member와 다르다.
FIXTURE_RAW_SHA256 = "bb1febc0894ec566bb22aff9f28a6258789aa2361e8c804f5ff00c7d633dc1be"
FIXTURE_RAW_BYTES = 11_021
MEMBER_RAW_BYTES = 11_121


def test_fixture_matches_registered_master_cypher_member_hash() -> None:
    source = _actual_source()
    assert master.sha256_bytes(source.replace("\n", "\r\n").encode()) == (
        FINAL_MEMBER_SHA256
    )


def test_the_fixture_and_the_archive_member_are_not_byte_identical() -> None:
    """**두 파일의 byte 경계를 고정한다.**

    `.gitattributes`의 `*.cypher text eol=lf` 때문에 tracked fixture는 **구조적으로
    영원히 LF**다. 원본 ZIP member는 100개 CRLF 줄바꿈으로 정확히 100 bytes 더 크다.

    따라서 provenance의 `SOURCE_MEMBER_SHA256`은 **오직 archive member bytes**로
    계산한다. fixture bytes로 계산하면 다른 값이 나오는데, 그때 상수를 fixture 값으로
    "고치면" **실제 아카이브 경로가 거부된다.** 그 사고를 이 회귀가 막는다.

    기존 `test_fixture_matches_registered_master_cypher_member_hash`는 `read_text()`로
    읽어 newline을 정규화하므로, fixture raw bytes가 CRLF로 바뀌어도 통과한다.
    여기서는 **raw bytes**를 본다.
    """

    raw = FIXTURE_PATH.read_bytes()
    assert len(raw) == FIXTURE_RAW_BYTES
    assert master.sha256_bytes(raw) == FIXTURE_RAW_SHA256

    # **member hash와 같지 않다.** 같아지면 둘 중 하나가 오염된 것이다.
    assert master.sha256_bytes(raw) != FINAL_MEMBER_SHA256

    # CRLF로 복원하면 member와 같다 — 내용은 동일하다는 증명이다.
    restored = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    assert len(restored) == MEMBER_RAW_BYTES
    assert master.sha256_bytes(restored) == FINAL_MEMBER_SHA256


ACTIVE_MANIFEST_SHA256 = (
    "1fd92a73186d6ae7d7ee8d7a7a2e1bf4a5fa5016491f4b5df29a93cd6ca6f3d3"
)


def test_the_active_artifacts_match_the_final_source_exactly(parsed) -> None:
    """**저장소에 등록된 실물 두 본**을 최종 source와 exact 대조한다.

    지금까지 회귀는 메모리에서 새 manifest를 만들어 비교했을 뿐
    `infra/bootstrap/manifests/neo4j.graph.json`을 **읽지 않았다.** 그래서 active
    manifest를 임의의 다른 내용으로 바꿔도 history와만 다르면 통과했다
    (구현리뷰 필수 1).

    `validate_generated_artifacts()`는 loader가 실행 시 쓰는 바로 그 판정이다.
    게시 전 회귀 Gate에서도 같은 것을 돌린다.
    """

    manifest = master.validate_generated_artifacts(parsed)
    assert manifest["node_count"] == master.EXPECTED_NODE_COUNT
    assert manifest["relationship_count"] == master.EXPECTED_RELATIONSHIP_COUNT
    # canonical JSON 기준이다 — 파일 raw bytes 기준과 다르다.
    assert master.graph_manifest_sha256(manifest) == ACTIVE_MANIFEST_SHA256
    assert manifest["expected_graph_fingerprint_sha256"] == FINAL_GRAPH_FINGERPRINT
    assert manifest["source_member_sha256"] == FINAL_MEMBER_SHA256


def test_a_tampered_active_manifest_is_refused(parsed, tmp_path) -> None:
    """active manifest를 변이하면 위 판정이 실제로 실패한다."""

    import json

    good = json.loads(master.GRAPH_MANIFEST_PATH.read_text(encoding="utf-8"))
    cypher = tmp_path / "master_graph.cypher"
    cypher.write_bytes(master.CORRECTED_CYPHER_PATH.read_bytes())

    for key, value in (
        ("node_count", 43),
        ("relationship_count", 84),
        ("expected_graph_fingerprint_sha256", "0" * 64),
        ("dataset_epoch", "kosa_0813"),
    ):
        tampered = tmp_path / f"{key}.json"
        tampered.write_text(
            json.dumps({**good, key: value}, ensure_ascii=False), encoding="utf-8"
        )
        with pytest.raises(master.GraphManifestError):
            master.validate_generated_artifacts(
                parsed, cypher_path=cypher, manifest_path=tampered
            )


def test_the_active_manifest_holds_no_secret() -> None:
    """active manifest에도 secret scan을 돌린다.

    `test_dataset_epoch.py`의 scan은 이름과 달리 **marker에만** 실행된다
    (구현리뷰 필수 1).
    """

    import json
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "scripts"))
    import manifest_v3

    manifest_v3.scan_for_sensitive_values(
        json.loads(master.GRAPH_MANIFEST_PATH.read_text(encoding="utf-8"))
    )


#: PostgreSQL `lot_history.area_id`·`evaluation.area`·`dim_parameter.area`가 쓰는 표기.
#: 공용 3 DB 실측값이며 Neo4j `Area.area_id`·`Equipment.area`와 같아야 한다.
CROSS_STORE_AREA_IDS = frozenset({"Photo", "Etch"})


def test_area_id_matches_the_postgresql_representation(parsed) -> None:
    """**store를 넘나드는 join key의 표기를 고정한다**(팀 리뷰 확인 1).

    최종 `master.cypher`가 `photo/etch` → `Photo/Etch`로 바꿨다. 이 값은 Neo4j
    안에서만 쓰이지 않는다 — `V5-B-3.3`의 `get_equipment_context`가 chamber →
    equipment → area를 반환하고, PostgreSQL `lot_history.area_id`와 대조하는 경로가
    생긴다.

    한쪽이 소문자로 매칭하면 **예외가 아니라 빈 결과**가 나온다. CI에서 안 잡힌다.

    공용 3 DB 실측: `lot_history.area_id`·`evaluation.area`·`dim_parameter.area`가
    전부 `['Etch', 'Photo']`다. 이 회귀가 그 일치를 계약으로 고정한다.
    """

    areas = {
        node.properties["area_id"] for node in parsed.nodes if node.label == "Area"
    }
    assert areas == set(CROSS_STORE_AREA_IDS)

    # Equipment의 area 속성도 같은 표기를 쓴다.
    equipment_areas = {
        node.properties["area"]
        for node in parsed.nodes
        if node.label == "Equipment" and "area" in node.properties
    }
    assert equipment_areas <= set(CROSS_STORE_AREA_IDS)

    # 소문자 표기가 섞이면 실패한다.
    assert not any(value.islower() for value in areas | equipment_areas)


def test_registered_artifact_counts_and_first_relation_id(parsed) -> None:
    assert parsed.destructive_statement == "MATCH (n) DETACH DELETE n;"
    # **수치는 `master_cypher`의 정본 상수에서 유도한다.**
    #
    # 같은 값이 이름을 달리해 여러 자리에 있으면 artifact를 다시 발급할 때 한 곳만
    # 고쳐도 나머지가 조용히 낡는다. dry-run의 38/81 literal이 정확히 그랬다
    # (팀 리뷰 필수 1).
    assert len(parsed.seed_statements) == master.EXPECTED_SEED_STATEMENT_COUNT
    assert len(parsed.corrected_statements) == master.EXPECTED_SEED_STATEMENT_COUNT
    assert len(parsed.nodes) == master.EXPECTED_NODE_COUNT
    assert len(parsed.relationships) == master.EXPECTED_RELATIONSHIP_COUNT
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
    assert (
        len(relation_ids)
        == len(set(relation_ids))
        == (master.EXPECTED_RELATIONSHIP_COUNT)
    )
    assert master.parse_master_cypher(_actual_source()).corrected_text == (
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
    source = _actual_source().replace(
        "MERGE (:Area {area_id:'Photo', area_name:'Photolithography'});",
        bad_statement,
    )
    with pytest.raises(master.CypherGrammarError):
        master.parse_master_cypher(source)


def test_forbidden_words_in_string_and_comment_are_not_substring_matches() -> None:
    source = _actual_source().replace(
        "area_name:'Photolithography'});",
        "area_name:'DELETE DROP'}); // CALL db.info()",
    )
    result = master.parse_master_cypher(source)
    assert result.nodes[0].properties["area_name"] == "DELETE DROP"


def test_unsupported_relationship_statement_is_rejected() -> None:
    source = _actual_source().replace(
        "MATCH (s:Parameter {parameter_id:'ET_ESC'}),"
        "(c:Chamber {chamber_id:'EQP06-PM2'}) MERGE (s)-[:MEASURED_ON]->(c);",
        "MATCH (a:Area {area_id:'Photo'}) MATCH (b:Area {area_id:'Etch'}) "
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
    assert master.graph_fingerprint(parsed) == FINAL_GRAPH_FINGERPRINT
    assert master.graph_fingerprint(parsed, legacy=True) == FINAL_LEGACY_FINGERPRINT
    assert master.graph_fingerprint(parsed) != master.graph_fingerprint(
        parsed, legacy=True
    )


def test_graph_manifest_is_exact_and_canonical(parsed) -> None:
    expected = master.build_graph_manifest(parsed)
    master.validate_graph_manifest(expected)
    assert expected["dataset_epoch"] == FINAL_EPOCH
    assert expected["source_archive_sha256"] == FINAL_ARCHIVE_SHA256
    assert expected["source_member_sha256"] == FINAL_MEMBER_SHA256
    assert expected["corrected_cypher_sha256"] == FINAL_CORRECTED_SHA256
    assert expected["expected_graph_fingerprint_sha256"] == FINAL_GRAPH_FINGERPRINT
    assert expected["expected_legacy_fingerprint_sha256"] == FINAL_LEGACY_FINGERPRINT
    assert expected["node_count"] == master.EXPECTED_NODE_COUNT
    assert expected["relationship_count"] == master.EXPECTED_RELATIONSHIP_COUNT
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
        bundle.writestr(FINAL_MEMBER_PATH, _actual_source())
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
        bundle.writestr(FINAL_MEMBER_PATH, _actual_source())
    archive_sha256 = master.sha256_file(archive)
    monkeypatch.setattr(master, "SOURCE_ARCHIVE_SHA256", archive_sha256)
    _write_epoch(
        epoch,
        archive_sha256=archive_sha256,
        member_sha256="0" * 64,
    )
    with pytest.raises(master.SourceGuardError, match="master.cypher"):
        master.load_registered_source(archive, epoch_path=epoch)
