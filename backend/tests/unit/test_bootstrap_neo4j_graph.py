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
manifest_v3 = _load("manifest_v3")

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


def test_directory_package_resolves_to_the_final_archive(tmp_path) -> None:
    """**폐기 epoch fallback이 되살아나면 실패한다.**

    directory `MENTOR_PACKAGE_DIR`는 `manifest_v3.FINAL_ARCHIVE_FILENAME`으로만
    해석한다. `kosa_0813.zip`을 남기면 최종 기준이 아닌 원본이 조용히 선택된다
    (구현리뷰 필수 3).
    """

    resolved = bootstrap._resolve_archive(None, {"MENTOR_PACKAGE_DIR": str(tmp_path)})
    assert resolved == tmp_path / manifest_v3.FINAL_ARCHIVE_FILENAME
    assert resolved.name == "project.zip"
    assert "kosa_0813" not in str(resolved)


def test_explicit_archive_wins_over_the_package_directory(tmp_path) -> None:
    """`--archive`를 주면 그대로 쓴다 — directory 해석보다 앞이다."""

    explicit = tmp_path / "elsewhere.zip"
    assert (
        bootstrap._resolve_archive(str(explicit), {"MENTOR_PACKAGE_DIR": str(tmp_path)})
        == explicit
    )


def test_missing_package_directory_fails_closed() -> None:
    with pytest.raises(bootstrap.Neo4jBootstrapError):
        bootstrap._resolve_archive(None, {})


def test_dry_run_counts_come_from_the_manifest(context, capsys, monkeypatch) -> None:
    """**dry-run 출력이 유도값인지 본다.**

    구 epoch의 `nodes=38 relationships=81`이 literal로 박혀 있어, source가 최종
    44/85로 바뀐 뒤에도 그대로 출력됐다(구현리뷰 필수 3).

    manifest count를 임의 값으로 바꾼 context에서도 출력이 따라가는지 확인해
    literal이 아님을 증명한다.
    """

    monkeypatch.setattr(bootstrap, "_resolve_archive", lambda *_a, **_k: Path("x.zip"))

    def _run(ctx):
        monkeypatch.setattr(bootstrap, "load_context", lambda *_a, **_k: ctx)
        bootstrap.main(["--dry-run", "--database", ctx.target.database])
        return capsys.readouterr().out

    # **token으로 본다.** 출력 전체 부분 문자열로 `38`·`81`을 부정하면
    # target fingerprint 안에 우연히 그 숫자가 들어갔을 때 count와 무관하게
    # 실패한다(구현리뷰 2차 권장 1).
    def _tokens(text: str) -> set[str]:
        return set(text.split())

    out = _run(context)
    assert "DRY_RUN_OK" in _tokens(out)
    assert "nodes=44" in _tokens(out)
    assert "relationships=85" in _tokens(out)
    assert "nodes=38" not in _tokens(out)
    assert "relationships=81" not in _tokens(out)

    # **유도값 증명** — manifest를 바꾸면 출력도 바뀐다.
    drifted = bootstrap.LoaderContext(
        context.target,
        context.parsed,
        {**context.manifest, "node_count": 7, "relationship_count": 9},
        context.corrected_manifest_sha256,
    )
    out = _run(drifted)
    assert "nodes=7" in _tokens(out)
    assert "relationships=9" in _tokens(out)


ACTIVE_GRAPH_FINGERPRINT = (
    "3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb"
)
ACTIVE_MANIFEST_SHA256 = (
    "1fd92a73186d6ae7d7ee8d7a7a2e1bf4a5fa5016491f4b5df29a93cd6ca6f3d3"
)


def _active_marker():
    marker = bootstrap.load_marker("neo4j")
    assert marker is not None, "active Neo4j marker가 없다"
    return marker


def _offline_context(marker, parsed):
    """공용 연결 없이 marker 계보를 검증할 synthetic context."""

    manifest = master.validate_generated_artifacts(parsed)
    target = target_mod.Neo4jBootstrapTarget(
        uri="bolt://offline.example.invalid:7687",
        username="offline",
        password="do-not-print",
        database=marker["database"],
        target_fingerprint_sha256=marker["target_fingerprint_sha256"],
        backup_root="/tmp/offline",
    )
    return bootstrap.LoaderContext(
        target, parsed, manifest, master.graph_manifest_sha256(manifest)
    )


def test_the_active_marker_passes_the_strict_schema() -> None:
    """**저장소에 등록된 marker 실물**을 status별 strict schema로 검증한다.

    `test_dataset_epoch.py`의 재발급 검사는 `dataset_epoch`와 history 대비 차이만
    본다. final epoch만 맞춘 malformed marker도 그것만으로는 통과한다
    (구현리뷰 4차 필수 3).

    CI는 공용 Neo4j를 부를 수 없으므로 active 실물 회귀가 따로 필요하다.
    """

    marker = _active_marker()
    bootstrap.validate_marker(marker)
    assert bootstrap.marker_is_readiness_success(marker)

    assert marker["status"] == "ADOPTED_EXISTING"
    assert marker["database"] == "neo4j"
    assert marker["node_count"] == master.EXPECTED_NODE_COUNT
    assert marker["relationship_count"] == master.EXPECTED_RELATIONSHIP_COUNT
    assert marker["relation_id_duplicates"] == 0
    assert marker["expected_graph_fingerprint_sha256"] == ACTIVE_GRAPH_FINGERPRINT
    assert marker["actual_graph_fingerprint_sha256"] == ACTIVE_GRAPH_FINGERPRINT
    assert marker["corrected_manifest_sha256"] == ACTIVE_MANIFEST_SHA256
    assert marker["source_archive_sha256"] == bootstrap.SOURCE_ARCHIVE_SHA256
    assert marker["source_member_sha256"] == bootstrap.SOURCE_MEMBER_SHA256
    assert marker["dataset_epoch"] == bootstrap.DATASET_EPOCH
    bootstrap.validate_approval_ref(marker["approval_ref"])


def test_the_active_marker_matches_the_active_artifacts(parsed) -> None:
    """marker가 **active manifest·source와 같은 계보**를 가리키는지 본다.

    공용에 연결하지 않는다 — marker가 기록한 fingerprint로 synthetic target을
    만들어 `validate_marker_for_context()`를 그대로 태운다.
    """

    marker = _active_marker()
    context = _offline_context(marker, parsed)
    bootstrap.validate_marker_for_context(marker, context)


#: **두 층이 다른 것을 본다.**
#:
#: `validate_marker()`는 status별 key 집합·hash 형식·provenance 상수·success의
#: expected=actual 같은 **schema 수준**을 본다. count가 manifest와 맞는지는 보지
#: 않는다 — 그것은 `validate_marker_for_context()`가 active manifest와 대조한다.
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", "REPLACED"),
        ("actual_graph_fingerprint_sha256", "0" * 64),
        ("source_member_sha256", "0" * 64),
        ("source_archive_sha256", "0" * 64),
        ("dataset_epoch", "kosa_0813"),
        ("approval_ref", "not-a-ref"),
        ("relation_id_algorithm_version", "rel-id-v0"),
    ],
)
def test_a_tampered_active_marker_is_refused_by_the_schema(key, value) -> None:
    """schema 수준 변이는 `validate_marker()`가 잡는다."""

    tampered = {**_active_marker(), key: value}
    with pytest.raises((bootstrap.MarkerError, bootstrap.EvidenceError)):
        bootstrap.validate_marker(tampered)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("corrected_manifest_sha256", "0" * 64),
        ("corrected_cypher_sha256", "0" * 64),
        ("expected_graph_fingerprint_sha256", "0" * 64),
        ("target_fingerprint_sha256", "0" * 64),
        ("database", "other"),
    ],
)
def test_a_tampered_active_marker_is_refused_by_the_context(parsed, key, value) -> None:
    """계보 수준 변이는 `validate_marker_for_context()`가 잡는다.

    count·manifest hash·expected fingerprint는 active manifest와 대조해야 드러난다.
    """

    marker = _active_marker()
    context = _offline_context(marker, parsed)
    with pytest.raises((bootstrap.MarkerError, bootstrap.EvidenceError)):
        bootstrap.validate_marker_for_context({**marker, key: value}, context)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("node_count", 43),
        ("node_count", 999),
        ("relationship_count", 84),
        ("relationship_count", 999),
        ("relation_id_duplicates", 1),
    ],
)
def test_a_tampered_marker_count_is_refused(parsed, key, value) -> None:
    """**marker count도 manifest와 대조한다**(구현리뷰 5차 필수 1).

    이 세 값은 UI 메모가 아니라 시스템설계서 §5.2의 **적용 증적**이다.
    대조하기 전에는 `node_count: 999`인 marker가 두 validator를 다 통과했고,
    public verifier는 live와 manifest만 비교하므로 잘못된 count가 계속 노출됐다.

    초판에서 나는 이 공백을 "현재 계약"으로 **고정만 했다.** 닫았어야 했다.
    """

    marker = _active_marker()
    context = _offline_context(marker, parsed)
    with pytest.raises(bootstrap.MarkerError):
        bootstrap.validate_marker_for_context({**marker, key: value}, context)


def test_the_active_marker_counts_match_the_manifest(parsed) -> None:
    """양성 경로 — active marker 44/85/0이 manifest와 맞는다."""

    marker = _active_marker()
    bootstrap.validate_marker_for_context(marker, _offline_context(marker, parsed))
    assert (marker["node_count"], marker["relationship_count"]) == (44, 85)
    assert marker["relation_id_duplicates"] == 0


def test_the_full_preflight_path_is_a_read_only_no_op(
    context, parsed, tmp_path, capsys, monkeypatch
) -> None:
    """**계획 §5.6의 no-op을 `run()` 전체 경로로 고정한다.**

    `--adopt-existing` 재호출이 `MarkerError`로 죽는 것은 **잘못된 mode를 다시 부른
    fail-closed guard**이지 no-op 성공 경로가 아니다(구현리뷰 5차 필수 2).

    진짜 no-op은 같은 절차가 preflight에서 `EXACT_WITH_MARKER`를 보고 graph·marker·
    backup을 **아무것도 바꾸지 않고 정상 종료**하는 것이다. 기존 회귀는
    `graph_state()` 분류만 봤고 `run()` 전체가 read-only인지는 보지 않았다.
    """

    import json

    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    # **active marker 실물**을 fixture target에 맞춰 옮긴다. database와 target
    # fingerprint만 바꾸고 나머지(status·count·hash·approval_ref)는 그대로다.
    marker = {
        **_active_marker(),
        "database": context.target.database,
        "target_fingerprint_sha256": context.target.target_fingerprint_sha256,
    }
    marker_path = bootstrap.marker_path(context.target.database, root=marker_root)
    marker_path.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    before_bytes = marker_path.read_bytes()
    before_mtime_ns = marker_path.stat().st_mtime_ns

    driver = FakeDriver(
        context.target.database, bootstrap.expected_snapshot(parsed), parsed
    )
    monkeypatch.setattr(bootstrap, "_resolve_archive", lambda *_a, **_k: Path("x.zip"))
    monkeypatch.setattr(bootstrap, "load_context", lambda *_a, **_k: context)

    backup_root = Path(context.target.backup_root)
    receipt_out = backup_root / "noop_preflight.json"
    before_entries = {p.name for p in backup_root.rglob("*") if p.is_file()}

    code = bootstrap.run(
        [
            "--preflight",
            "--database",
            context.target.database,
            "--confirm-target",
            context.target.database,
            "--receipt-out",
            str(receipt_out),
        ],
        driver_factory=_factory(driver),
        marker_root=marker_root,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "state=EXACT_WITH_MARKER" in out

    # **read-only다.** session은 READ만 열리고 write callback은 0회다.
    assert driver.session_access_modes == ["READ"]
    assert driver.tx_writes == []

    # **marker를 재기록조차 하지 않는다.**
    #
    # bytes만 보면 같은 내용을 다시 쓰는 회귀가 생겨도 통과한다. 재기록은 감사
    # 시각과 운영 증적을 흔들므로 mtime도 함께 고정한다(구현리뷰 6차 필수 1).
    assert marker_path.read_bytes() == before_bytes
    assert marker_path.stat().st_mtime_ns == before_mtime_ns

    # backup manifest가 새로 생기지 않는다. preflight receipt와 advisory lock만
    # 허용된다 — 둘 다 graph·marker를 바꾸지 않는다.
    after_entries = {p.name for p in backup_root.rglob("*") if p.is_file()}
    assert after_entries - before_entries <= {
        receipt_out.name,
        ".neo4j-bootstrap.lock",
    }
    assert not any(
        name.endswith(".manifest.json") for name in after_entries - before_entries
    )
    assert json.loads(receipt_out.read_text())["artifact_type"] == (
        "neo4j_preflight_receipt"
    )
    receipt_out.unlink()


def test_a_marker_missing_a_required_key_is_refused() -> None:
    """key 하나가 빠져도 status별 exact 집합에서 걸린다."""

    marker = _active_marker()
    for key in ("approval_ref", "node_count", "actual_graph_fingerprint_sha256"):
        stripped = {k: v for k, v in marker.items() if k != key}
        with pytest.raises(bootstrap.MarkerError):
            bootstrap.validate_marker(stripped)


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
        self.driver.tx_writes.append("execute_write")
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
        #: `execute_write` 호출과 mutation query를 기록한다 — no-op 회귀가 본다.
        self.tx_writes = []

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
    assert snapshot.node_count == len(parsed.nodes)
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
