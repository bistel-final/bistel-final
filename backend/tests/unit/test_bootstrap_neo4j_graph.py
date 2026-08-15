"""V4-CM-1.6 loader guards using an in-memory fake Neo4j driver."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


master = _load("master_cypher")
target_mod = _load("neo4j_target")
bootstrap = _load("bootstrap_neo4j_graph")

BOOTSTRAP_ROOT = Path(__file__).resolve().parents[3] / "infra" / "bootstrap"
CORRECTED_PATH = BOOTSTRAP_ROOT / "master_graph.cypher"
MANIFEST_PATH = BOOTSTRAP_ROOT / "manifests" / "neo4j.graph.json"


def _lookup_index_rows():
    return [
        {
            "name": "index_nodes",
            "type": "LOOKUP",
            "entityType": "NODE",
            "labelsOrTypes": None,
            "properties": None,
            "owningConstraint": None,
        },
        {
            "name": "index_relationships",
            "type": "LOOKUP",
            "entityType": "RELATIONSHIP",
            "labelsOrTypes": None,
            "properties": None,
            "owningConstraint": None,
        },
    ]


def _default_schema_fingerprint():
    payload = {
        "indexes": [
            {
                "name": row["name"],
                "type": row["type"],
                "entity_type": row["entityType"],
                "labels_or_types": None,
                "properties": None,
                "owning_constraint": None,
            }
            for row in _lookup_index_rows()
        ],
        "constraints": [],
    }
    return master.canonical_sha256(payload)


def _raw_source() -> str:
    corrected = CORRECTED_PATH.read_text(encoding="utf-8")
    seed = re.sub(r" \{relation_id:'REL-[0-9a-f]{20}'\}", "", corrected)
    return "MATCH (n) DETACH DELETE n;\n" + seed


@pytest.fixture(scope="module")
def parsed():
    return master.parse_master_cypher(_raw_source())


@pytest.fixture()
def fake_target(tmp_path: Path):
    uri = "bolt://neo4j.example.invalid:7687"
    database = "kosa_graph"
    return target_mod.Neo4jBootstrapTarget(
        uri=uri,
        username="bootstrap",
        password="do-not-print",
        database=database,
        target_fingerprint_sha256=target_mod.target_fingerprint(uri, database),
        backup_root=str(tmp_path / "backup-root"),
    )


@pytest.fixture()
def context(parsed, fake_target):
    manifest = master.build_graph_manifest(parsed)
    return bootstrap.LoaderContext(
        fake_target,
        parsed,
        manifest,
        master.graph_manifest_sha256(manifest),
    )


def _snapshot_rows(snapshot):
    node_rows = [
        {"labels": [node.label], "properties": dict(node.properties)}
        for node in snapshot.nodes
    ]
    relation_rows = [
        {
            "from_labels": [item.from_node.label],
            "from_properties": dict(item.from_node.properties),
            "relationship_type": item.relation_type,
            "relationship_properties": dict(item.properties),
            "to_labels": [item.to_node.label],
            "to_properties": dict(item.to_node.properties),
        }
        for item in snapshot.relationships
    ]
    return node_rows, relation_rows


class FakeTx:
    def __init__(self, driver, working, expected):
        self.driver = driver
        self.snapshot = working
        self.expected = expected
        self.seed_count = 0

    def run(self, query, **params):
        if query.startswith("CALL db.info"):
            return [{"name": self.driver.database}]
        if query == bootstrap.NODE_QUERY:
            return _snapshot_rows(self.snapshot)[0]
        if query == bootstrap.RELATIONSHIP_QUERY:
            return _snapshot_rows(self.snapshot)[1]
        if query == bootstrap.INDEX_QUERY:
            return copy.deepcopy(self.driver.index_rows)
        if query == bootstrap.CONSTRAINT_QUERY:
            return copy.deepcopy(self.driver.constraint_rows)
        if query == bootstrap.DELETE_QUERY:
            self.snapshot = bootstrap.GraphSnapshot((), ())
            return []
        if "WHERE r.relation_id IS NULL SET r.relation_id" in query:
            wanted = params["relation_id"]
            relationships = []
            updated = 0
            for item in self.snapshot.relationships:
                expected_id = master.build_relation_id(
                    item.relation_type, item.from_node, item.to_node
                )
                if expected_id == wanted and item.relation_id is None:
                    properties = dict(item.properties)
                    properties["relation_id"] = wanted
                    item = bootstrap.RelationshipSnapshot(
                        item.relation_type,
                        item.from_node,
                        item.to_node,
                        properties,
                    )
                    updated += 1
                relationships.append(item)
            self.snapshot = bootstrap.GraphSnapshot(
                self.snapshot.nodes, tuple(relationships)
            )
            return [{"updated": updated}]
        if query.startswith("CREATE (n:"):
            label = re.search(r"CREATE \(n:([A-Za-z0-9_]+)\)", query).group(1)
            node = master.NodeSpec(label, dict(params["properties"]))
            self.snapshot = bootstrap.GraphSnapshot(
                self.snapshot.nodes + (node,), self.snapshot.relationships
            )
            return []
        if "CREATE (a)-[r:" in query:
            match = re.search(
                r"MATCH \(a:([A-Za-z0-9_]+).*\(b:([A-Za-z0-9_]+).*"
                r"CREATE \(a\)-\[r:([A-Za-z0-9_]+)\]->\(b\)",
                query,
            )
            from_label, to_label, relation_type = match.groups()

            def endpoint(label, prefix):
                keys = master.BUSINESS_KEYS[label]
                values = {key: params[f"{prefix}_{key}"] for key in keys}
                matches = [
                    node
                    for node in self.snapshot.nodes
                    if node.label == label
                    and all(
                        node.properties[key] == value for key, value in values.items()
                    )
                ]
                assert len(matches) == 1
                return matches[0]

            relation = bootstrap.RelationshipSnapshot(
                relation_type,
                endpoint(from_label, "f"),
                endpoint(to_label, "t"),
                dict(params["properties"]),
            )
            self.snapshot = bootstrap.GraphSnapshot(
                self.snapshot.nodes, self.snapshot.relationships + (relation,)
            )
            return []
        if query.rstrip(";") in {
            statement.rstrip(";") for statement in self.expected.corrected_statements
        }:
            self.seed_count += 1
            if self.seed_count == len(self.expected.corrected_statements):
                self.snapshot = bootstrap.expected_snapshot(self.expected)
            return []
        raise AssertionError(f"unexpected fake query: {query}")


class FakeSession:
    def __init__(self, driver):
        self.driver = driver
        self.tx = FakeTx(driver, copy.deepcopy(driver.snapshot), driver.parsed)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, query, **params):
        return self.tx.run(query, **params)

    def execute_read(self, callback):
        return callback(self.tx)

    def execute_write(self, callback):
        original = copy.deepcopy(self.driver.snapshot)
        self.tx = FakeTx(self.driver, copy.deepcopy(original), self.driver.parsed)
        try:
            result = callback(self.tx)
        except Exception:
            self.driver.snapshot = original
            raise
        self.driver.snapshot = self.tx.snapshot
        return result


class FakeDriver:
    def __init__(self, database, snapshot, parsed):
        self.database = database
        self.snapshot = snapshot
        self.parsed = parsed
        self.index_rows = _lookup_index_rows()
        self.constraint_rows = []
        self.closed = False
        self.session_access_modes = []

    def session(self, *, database, default_access_mode=None):
        assert database == self.database
        self.session_access_modes.append(default_access_mode)
        return FakeSession(self)

    def close(self):
        self.closed = True


def _factory(driver):
    return lambda _: driver


def _empty_snapshot():
    return bootstrap.GraphSnapshot((), ())


def _legacy_snapshot(parsed):
    expected = bootstrap.expected_snapshot(parsed)
    relationships = tuple(
        bootstrap.RelationshipSnapshot(
            item.relation_type, item.from_node, item.to_node, {}
        )
        for item in expected.relationships
    )
    return bootstrap.GraphSnapshot(expected.nodes, relationships)


def test_target_uri_normalization_and_default_port() -> None:
    expected = "bolt://example.invalid:7687/kosa_graph"
    assert (
        target_mod.canonical_target_text("BOLT://EXAMPLE.INVALID", "kosa_graph")
        == expected
    )
    assert (
        target_mod.canonical_target_text("neo4j://[2001:DB8::1]", "kosa_graph")
        == "neo4j://[2001:db8::1]:7687/kosa_graph"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "bolt+ssc://host",
        "http://host",
        "bolt://user:pw@host",
        "bolt://host/path",
        "bolt://host?x=1",
        "bolt://host#fragment",
        "bolt://:7687",
        "bolt://host:70000",
    ],
)
def test_target_uri_rejects_unsafe_forms(uri: str) -> None:
    with pytest.raises(target_mod.Neo4jTargetError):
        target_mod.canonical_target_text(uri, "kosa_graph")


@pytest.mark.parametrize("database", ["Neo4j", "ab", "../neo4j", "neo 4j"])
def test_database_name_must_be_lowercase_safe(database: str) -> None:
    with pytest.raises(target_mod.Neo4jTargetError):
        target_mod.validate_database_name(database)


def test_loader_uses_only_five_bootstrap_keys(tmp_path: Path) -> None:
    uri = "neo4j+s://graph.example.invalid"
    database = "kosa_graph"
    env = {
        "NEO4J_BOOTSTRAP_URI": uri,
        "NEO4J_BOOTSTRAP_USER": "bootstrap",
        "NEO4J_BOOTSTRAP_PASSWORD": "secret",
        "NEO4J_BOOTSTRAP_ALLOWED_TARGET_SHA256": target_mod.target_fingerprint(
            uri, database
        ),
        "NEO4J_BOOTSTRAP_BACKUP_ROOT": str(tmp_path),
        "NEO4J_URI": "bolt://wrong.example.invalid",
        "NEO4J_PASSWORD": "legacy-secret",
    }
    target = target_mod.load_neo4j_bootstrap_target(database, environ=env)
    assert target.uri == uri
    assert target.password == "secret"
    rendered = repr(target)
    assert uri not in rendered
    assert "bootstrap" not in rendered
    assert "secret" not in rendered
    assert str(tmp_path) not in rendered


def test_target_fingerprint_mismatch_is_rejected(tmp_path: Path) -> None:
    env = {key: "x" for key in target_mod.BOOTSTRAP_ENV_KEYS}
    env["NEO4J_BOOTSTRAP_URI"] = "bolt://host"
    env["NEO4J_BOOTSTRAP_ALLOWED_TARGET_SHA256"] = "0" * 64
    env["NEO4J_BOOTSTRAP_BACKUP_ROOT"] = str(tmp_path)
    with pytest.raises(target_mod.Neo4jTargetError, match="allowlist"):
        target_mod.load_neo4j_bootstrap_target("kosa_graph", environ=env)


def test_backup_root_must_be_absolute(tmp_path: Path) -> None:
    uri = "bolt://graph.example.invalid"
    database = "kosa_graph"
    env = {
        "NEO4J_BOOTSTRAP_URI": uri,
        "NEO4J_BOOTSTRAP_USER": "bootstrap",
        "NEO4J_BOOTSTRAP_PASSWORD": "secret",
        "NEO4J_BOOTSTRAP_ALLOWED_TARGET_SHA256": target_mod.target_fingerprint(
            uri, database
        ),
        "NEO4J_BOOTSTRAP_BACKUP_ROOT": "relative/backup",
    }
    with pytest.raises(target_mod.Neo4jTargetError, match="절대경로"):
        target_mod.load_neo4j_bootstrap_target(database, environ=env)


def test_connected_database_requires_exactly_one_matching_row() -> None:
    class Session:
        def __init__(self, rows):
            self.rows = rows

        def run(self, _):
            return self.rows

    target_mod.validate_connected_database(Session([{"name": "neo4j"}]), "neo4j")
    with pytest.raises(target_mod.Neo4jTargetError):
        target_mod.validate_connected_database(Session([]), "neo4j")
    with pytest.raises(target_mod.Neo4jTargetError):
        target_mod.validate_connected_database(Session([{"name": "other"}]), "neo4j")


def test_snapshot_exact_and_legacy_fingerprints_match_manifest(context, parsed) -> None:
    exact = bootstrap.expected_snapshot(parsed)
    legacy = _legacy_snapshot(parsed)
    assert (
        bootstrap.snapshot_fingerprint(exact)
        == context.manifest["expected_graph_fingerprint_sha256"]
    )
    assert (
        bootstrap.snapshot_fingerprint(legacy, legacy=True)
        == context.manifest["expected_legacy_fingerprint_sha256"]
    )


def test_snapshot_rejects_unknown_multilabel_and_duplicate_business_key() -> None:
    with pytest.raises(bootstrap.GraphStateError):
        bootstrap.snapshot_from_rows(
            [{"labels": ["Area", "Recipe"], "properties": {"area_id": "a"}}],
            [],
        )
    rows = [
        {"labels": ["Area"], "properties": {"area_id": "a"}},
        {"labels": ["Area"], "properties": {"area_id": "a"}},
    ]
    with pytest.raises(bootstrap.GraphStateError, match="중복"):
        bootstrap.snapshot_from_rows(rows, [])


@pytest.mark.parametrize(
    ("snapshot_name", "marker", "expected"),
    [
        ("empty", None, "EMPTY"),
        ("exact", None, "EXACT_WITHOUT_MARKER"),
        ("legacy", None, "LEGACY_EXACT"),
    ],
)
def test_graph_state_core_paths(
    context, parsed, snapshot_name, marker, expected
) -> None:
    snapshots = {
        "empty": _empty_snapshot(),
        "exact": bootstrap.expected_snapshot(parsed),
        "legacy": _legacy_snapshot(parsed),
    }
    assert (
        bootstrap.graph_state(snapshots[snapshot_name], context.manifest, marker=marker)
        == expected
    )


def test_graph_state_rejects_marker_contamination(context, parsed) -> None:
    exact = bootstrap.expected_snapshot(parsed)
    marker = bootstrap.build_marker(context, exact, "APPLIED")
    modified_node = master.NodeSpec(
        exact.nodes[0].label,
        {**exact.nodes[0].properties, "area_name": "changed"},
    )
    conflict = bootstrap.GraphSnapshot(
        (modified_node,) + exact.nodes[1:], exact.relationships
    )
    with pytest.raises(bootstrap.GraphStateError, match="marker와 실제"):
        bootstrap.graph_state(conflict, context.manifest, marker=marker)


def test_apply_empty_and_adopt_existing_use_transaction(context, parsed) -> None:
    empty_driver = FakeDriver(context.target.database, _empty_snapshot(), parsed)
    applied = bootstrap.mutate_graph(
        context, "apply-empty", driver_factory=_factory(empty_driver)
    )
    assert (
        bootstrap.snapshot_fingerprint(applied)
        == context.manifest["expected_graph_fingerprint_sha256"]
    )
    assert empty_driver.closed

    legacy_driver = FakeDriver(
        context.target.database, _legacy_snapshot(parsed), parsed
    )
    adopted = bootstrap.mutate_graph(
        context, "adopt-existing", driver_factory=_factory(legacy_driver)
    )
    assert (
        bootstrap.snapshot_fingerprint(adopted)
        == context.manifest["expected_graph_fingerprint_sha256"]
    )
    assert all(item.relation_id for item in adopted.relationships)


def test_apply_empty_populated_graph_writes_nothing(context, parsed) -> None:
    original = bootstrap.expected_snapshot(parsed)
    driver = FakeDriver(context.target.database, original, parsed)
    with pytest.raises(bootstrap.GraphStateError, match="empty graph"):
        bootstrap.mutate_graph(context, "apply-empty", driver_factory=_factory(driver))
    assert driver.snapshot == original
    assert driver.closed


def test_adopt_legacy_mismatch_rolls_back(context, parsed) -> None:
    legacy = _legacy_snapshot(parsed)
    modified_node = master.NodeSpec(
        legacy.nodes[0].label,
        {**legacy.nodes[0].properties, "area_name": "changed"},
    )
    conflict = bootstrap.GraphSnapshot(
        (modified_node,) + legacy.nodes[1:], legacy.relationships
    )
    driver = FakeDriver(context.target.database, conflict, parsed)
    with pytest.raises(bootstrap.GraphStateError):
        bootstrap.mutate_graph(
            context, "adopt-existing", driver_factory=_factory(driver)
        )
    assert driver.snapshot == conflict


def test_replace_and_restore_recheck_fingerprint_and_rollback(context, parsed) -> None:
    legacy = _legacy_snapshot(parsed)
    legacy_fingerprint = bootstrap.snapshot_fingerprint(legacy)
    driver = FakeDriver(context.target.database, legacy, parsed)
    replaced = bootstrap.mutate_graph(
        context,
        "replace",
        expected_existing_fingerprint=legacy_fingerprint,
        expected_schema_fingerprint=_default_schema_fingerprint(),
        driver_factory=_factory(driver),
    )
    expected_fingerprint = context.manifest["expected_graph_fingerprint_sha256"]
    assert bootstrap.snapshot_fingerprint(replaced) == expected_fingerprint

    restored = bootstrap.mutate_graph(
        context,
        "restore-backup",
        expected_existing_fingerprint=expected_fingerprint,
        expected_schema_fingerprint=_default_schema_fingerprint(),
        restore_snapshot=legacy,
        driver_factory=_factory(driver),
    )
    assert bootstrap.snapshot_fingerprint(restored) == legacy_fingerprint

    original = copy.deepcopy(driver.snapshot)
    with pytest.raises(bootstrap.GraphStateError, match="transaction 시점"):
        bootstrap.mutate_graph(
            context,
            "replace",
            expected_existing_fingerprint="0" * 64,
            expected_schema_fingerprint=_default_schema_fingerprint(),
            driver_factory=_factory(driver),
        )
    assert driver.snapshot == original

    with pytest.raises(bootstrap.GraphStateError, match="schema fingerprint"):
        bootstrap.mutate_graph(
            context,
            "replace",
            expected_existing_fingerprint=legacy_fingerprint,
            expected_schema_fingerprint="0" * 64,
            driver_factory=_factory(driver),
        )
    assert driver.snapshot == original


def test_constraint_backed_indexes_are_fingerprinted_and_standalone_rejected(
    parsed,
) -> None:
    snapshot = bootstrap.expected_snapshot(parsed)
    driver = FakeDriver("kosa_graph", snapshot, parsed)
    tx = FakeTx(driver, snapshot, parsed)
    driver.constraint_rows = [
        {
            "name": "area_id",
            "type": "UNIQUENESS",
            "entityType": "NODE",
            "labelsOrTypes": ["Area"],
            "properties": ["area_id"],
            "ownedIndex": "area_id",
        }
    ]
    driver.index_rows.append(
        {
            "name": "area_id",
            "type": "RANGE",
            "entityType": "NODE",
            "labelsOrTypes": ["Area"],
            "properties": ["area_id"],
            "owningConstraint": "area_id",
        }
    )
    fingerprint = bootstrap.validate_supported_schema(tx)
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)

    driver.index_rows[-1]["owningConstraint"] = None
    with pytest.raises(bootstrap.GraphStateError, match="독립 사용자 index"):
        bootstrap.validate_supported_schema(tx)


def test_unsupported_constraint_still_requires_official_dump(parsed) -> None:
    snapshot = bootstrap.expected_snapshot(parsed)
    driver = FakeDriver("kosa_graph", snapshot, parsed)
    driver.constraint_rows = [
        {
            "name": "area_exists",
            "type": "NODE_PROPERTY_EXISTENCE",
            "entityType": "NODE",
            "labelsOrTypes": ["Area"],
            "properties": ["area_id"],
            "ownedIndex": "area_exists",
        }
    ]
    tx = FakeTx(driver, snapshot, parsed)
    with pytest.raises(bootstrap.GraphStateError, match="공식 Neo4j dump"):
        bootstrap.validate_supported_schema(tx)


def test_marker_status_contract_and_readiness(context, parsed) -> None:
    snapshot = bootstrap.expected_snapshot(parsed)
    applied = bootstrap.build_marker(context, snapshot, "APPLIED")
    assert bootstrap.marker_is_readiness_success(applied)
    broken = dict(applied)
    broken["actual_graph_fingerprint_sha256"] = "0" * 64
    assert not bootstrap.marker_is_readiness_success(broken)

    restored = bootstrap.build_marker(
        context,
        snapshot,
        "RESTORED",
        extra={
            "backup_file_sha256": "1" * 64,
            "backup_graph_fingerprint_sha256": "2" * 64,
            "schema_fingerprint_sha256": "6" * 64,
            "backup_manifest_sha256": "3" * 64,
            "restore_receipt_sha256": "4" * 64,
            "approval_ref": "KOSA-123",
            "pre_restore_graph_fingerprint_sha256": "5" * 64,
            "post_restore_graph_fingerprint_sha256": bootstrap.snapshot_fingerprint(
                snapshot
            ),
        },
    )
    assert not bootstrap.marker_is_readiness_success(restored)


def test_marker_extra_or_forbidden_field_is_rejected(context, parsed) -> None:
    marker = bootstrap.build_marker(
        context, bootstrap.expected_snapshot(parsed), "VERIFIED_EXISTING"
    )
    marker["approval_ref"] = "KOSA-1"
    with pytest.raises(bootstrap.MarkerError, match="key 집합"):
        bootstrap.validate_marker(marker)


def test_backup_round_trip_keeps_file_and_graph_hash_distinct(
    context, parsed, tmp_path: Path
) -> None:
    root = tmp_path / "external"
    snapshot = bootstrap.expected_snapshot(parsed)
    backup_path, manifest_path, manifest = bootstrap.create_backup_artifacts(
        snapshot,
        context.target,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
        backup_root=root,
        now=datetime(2026, 8, 16, tzinfo=UTC),
    )
    loaded_manifest, _, loaded_snapshot, loaded_path = bootstrap.load_backup_bundle(
        manifest_path, root
    )
    assert loaded_path == backup_path
    assert loaded_manifest == manifest
    assert bootstrap.snapshot_fingerprint(
        loaded_snapshot
    ) == bootstrap.snapshot_fingerprint(snapshot)
    assert manifest["backup_file_sha256"] != manifest["backup_graph_fingerprint_sha256"]
    with pytest.raises(bootstrap.BackupError, match="이미 존재"):
        bootstrap.create_backup_artifacts(
            snapshot,
            context.target,
            schema_fingerprint_sha256=_default_schema_fingerprint(),
            backup_root=root,
            now=datetime(2026, 8, 16, tzinfo=UTC),
        )


def test_backup_payload_and_manifest_target_are_cross_checked(
    context, parsed, tmp_path: Path
) -> None:
    root = tmp_path / "external"
    snapshot = bootstrap.expected_snapshot(parsed)
    backup_path, manifest_path, _ = bootstrap.create_backup_artifacts(
        snapshot,
        context.target,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
        backup_root=root,
        now=datetime(2026, 8, 16, tzinfo=UTC),
    )
    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    payload["database"] = "other_graph"
    bootstrap.atomic_write_bytes(
        backup_path, master.canonical_json_bytes(payload) + b"\n"
    )
    with pytest.raises(bootstrap.BackupError, match="target"):
        bootstrap.load_backup_bundle(manifest_path, root)


def test_backup_round_trip_supports_legacy_sensor_float(
    context, tmp_path: Path
) -> None:
    root = tmp_path / "external"
    sensor = bootstrap.SnapshotNode(
        "Sensor",
        {
            "sensor_id": "OLD-SENSOR-01",
            "sensor_name": "legacy",
            "spec_upper": 1.25,
            "spec_lower": -1.25,
        },
    )
    snapshot = bootstrap.GraphSnapshot((sensor,), ())
    _, manifest_path, _ = bootstrap.create_backup_artifacts(
        snapshot,
        context.target,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
        backup_root=root,
        now=datetime(2026, 8, 16, tzinfo=UTC),
    )
    _, _, restored, _ = bootstrap.load_backup_bundle(manifest_path, root)
    assert bootstrap.snapshot_payload(restored) == bootstrap.snapshot_payload(snapshot)


def test_backup_payload_rejects_unknown_keys(context, parsed) -> None:
    payload = bootstrap._backup_payload(
        bootstrap.expected_snapshot(parsed),
        context.target,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
    )
    payload["nodes"][0]["extra"] = "forbidden"
    with pytest.raises(bootstrap.BackupError, match="node schema"):
        bootstrap.snapshot_from_backup(payload)


def test_receipt_output_inside_repository_is_rejected(tmp_path: Path) -> None:
    repository_path = Path(__file__).resolve().parents[3] / "infra" / "receipt.json"
    repository_root = Path(__file__).resolve().parents[3]
    with pytest.raises(bootstrap.BackupError, match="저장소 밖"):
        bootstrap.validate_backup_path(repository_path, repository_root)


def test_receipt_exact_schema_and_ttl(context, parsed, tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    receipt = bootstrap.build_preflight_receipt(
        context,
        bootstrap.expected_snapshot(parsed),
        schema_fingerprint_sha256=_default_schema_fingerprint(),
        now=now,
    )
    bootstrap.validate_receipt(receipt, "neo4j_preflight_receipt")
    extra = dict(receipt)
    extra["comment"] = "no"
    with pytest.raises(bootstrap.EvidenceError):
        bootstrap.validate_receipt(extra, "neo4j_preflight_receipt")

    root = tmp_path / "external"
    root.mkdir()
    preflight = root / "preflight.json"
    backup_manifest = root / "graph.manifest.json"
    restore = root / "restore.json"
    old = dict(receipt)
    old["recorded_at"] = bootstrap.utc_text(now - timedelta(hours=25))
    bootstrap.atomic_write_bytes(preflight, master.canonical_json_bytes(old) + b"\n")
    # Missing evidence is rejected before any DB connection; TTL is asserted
    # separately so this test does not need to construct an unrelated backup.
    assert (
        now - bootstrap._parse_utc(old["recorded_at"], field="recorded_at")
        > bootstrap.PREFLIGHT_TTL
    )
    assert backup_manifest != restore


def test_replace_evidence_rejects_expired_preflight(
    context, parsed, tmp_path: Path
) -> None:
    root = Path(context.target.backup_root)
    root.mkdir(parents=True)
    snapshot = bootstrap.expected_snapshot(parsed)
    now = datetime(2026, 8, 16, tzinfo=UTC)
    preflight = bootstrap.build_preflight_receipt(
        context,
        snapshot,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
        now=now,
    )
    preflight_path = root / "receipts" / "preflight.json"
    bootstrap.save_external_artifact(preflight_path, preflight, root)
    _, manifest_path, _ = bootstrap.create_backup_artifacts(
        snapshot,
        context.target,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
        backup_root=root,
        now=now,
    )
    restore = bootstrap.build_restore_receipt(
        manifest_path, root, context.target, now=now
    )
    restore_path = root / "receipts" / "restore.json"
    bootstrap.save_external_artifact(restore_path, restore, root)
    with pytest.raises(bootstrap.EvidenceError, match="24시간"):
        bootstrap.validate_replace_evidence(
            context,
            expected_existing_fingerprint=bootstrap.snapshot_fingerprint(snapshot),
            preflight_path=preflight_path,
            backup_manifest_path=manifest_path,
            restore_receipt_path=restore_path,
            approval_ref="KOSA-123",
            now=now + timedelta(hours=25),
        )


@pytest.mark.parametrize("approval", [None, "", "issue-1", "KOSA", "KOSA 1"])
def test_approval_reference_pattern(approval) -> None:
    with pytest.raises(bootstrap.EvidenceError):
        bootstrap.validate_approval_ref(approval)
    assert bootstrap.validate_approval_ref("KOSA-123") == "KOSA-123"


def _namespace(**overrides):
    base = {
        "database": None,
        "confirm_target": None,
        "archive": None,
        "dry_run": False,
        "preflight": False,
        "apply_empty": False,
        "adopt_existing": False,
        "recover_marker": False,
        "backup": False,
        "verify_backup": False,
        "replace": False,
        "restore_backup": False,
        "receipt_out": None,
        "expected_existing_fingerprint": None,
        "expected_current_fingerprint": None,
        "preflight_receipt": None,
        "backup_manifest": None,
        "restore_receipt": None,
        "approval_ref": None,
    }
    base.update(overrides)
    return Namespace(**base)


def test_no_mode_and_confirm_mismatch_fail_before_connection() -> None:
    with pytest.raises(bootstrap.Neo4jBootstrapError, match="접속하지"):
        bootstrap.resolve_mode(_namespace())
    with pytest.raises(bootstrap.Neo4jBootstrapError, match="다릅니다"):
        bootstrap.resolve_mode(
            _namespace(
                database="kosa_graph",
                confirm_target="other_graph",
                apply_empty=True,
            )
        )
    with pytest.raises(bootstrap.Neo4jBootstrapError, match="하나만"):
        bootstrap.resolve_mode(
            _namespace(
                database="kosa_graph",
                confirm_target="kosa_graph",
                preflight=True,
                backup=True,
                receipt_out="/tmp/preflight.json",
            )
        )


def test_apply_and_adopt_modes_require_explicit_arguments() -> None:
    assert (
        bootstrap.resolve_mode(
            _namespace(
                database="kosa_graph",
                confirm_target="kosa_graph",
                apply_empty=True,
            )
        )
        == "apply-empty"
    )
    with pytest.raises(bootstrap.EvidenceError):
        bootstrap.resolve_mode(
            _namespace(
                database="kosa_graph",
                confirm_target="kosa_graph",
                adopt_existing=True,
            )
        )


def test_file_lock_uses_posix_and_windows_adapters(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with path.open("a+b") as stream:
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        bootstrap._acquire_file_lock(stream)
        bootstrap._release_file_lock(stream)

    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd, mode, count):
            calls.append((fd, mode, count))

    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.setattr(bootstrap, "_msvcrt", FakeMsvcrt)
    with path.open("a+b") as stream:
        bootstrap._acquire_file_lock(stream)
        bootstrap._release_file_lock(stream)
    assert [call[1] for call in calls] == [1, 2]


def test_driver_is_closed_on_read_success_and_failure(context, parsed) -> None:
    driver = FakeDriver(
        context.target.database, bootstrap.expected_snapshot(parsed), parsed
    )
    snapshot = bootstrap.read_current_snapshot(
        context.target, driver_factory=_factory(driver)
    )
    assert snapshot.node_count == 38
    assert driver.session_access_modes == ["READ"]
    assert driver.closed

    bad = FakeDriver("wrong_database", _empty_snapshot(), parsed)
    with pytest.raises(AssertionError):
        bootstrap.read_current_snapshot(context.target, driver_factory=_factory(bad))
    assert bad.closed


def test_secrets_are_not_present_in_marker_or_receipt(context, parsed) -> None:
    snapshot = bootstrap.expected_snapshot(parsed)
    marker = bootstrap.build_marker(context, snapshot, "APPLIED")
    receipt = bootstrap.build_preflight_receipt(
        context,
        snapshot,
        schema_fingerprint_sha256=_default_schema_fingerprint(),
    )
    serialized = json.dumps([marker, receipt])
    assert "do-not-print" not in serialized
    assert "neo4j.example.invalid" not in serialized
    assert "bolt://" not in serialized
