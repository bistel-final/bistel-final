"""V5-CM-5.3 repository gate and deterministic report regressions."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import final_nonfunctional_gate as gate


def test_current_repository_contracts_pass_without_external_connections() -> None:
    report = gate.run_repository_gates()

    assert report["secret_scan"]["tracked"] >= 695
    assert report["secret_scan"]["exact_binary_allowlisted"] == 10
    assert report["python_requirements"] == 34
    assert report["node_resolved_packages"] > 300
    assert report["timestamp_columns"] == 10
    assert "apache/kafka:3.9.1" in report["container_images"]


@pytest.mark.parametrize(
    "payload, rule_id",
    [
        ("dsn=postgresql://cm53-user:cm53-pass@host/db", "CREDENTIAL_URI"),
        ("-----BEGIN PRIVATE KEY-----", "PRIVATE_KEY_HEADER"),
        ("password=cm53-unique-sentinel-9137", "SENSITIVE_ASSIGNMENT"),
    ],
)
def test_secret_mutations_are_red_without_echoing_values(
    tmp_path: Path, payload: str, rule_id: str
) -> None:
    relative = "fixture.txt"
    (tmp_path / relative).write_text(payload, encoding="utf-8")

    with pytest.raises(gate.FinalGateError) as caught:
        gate.scan_tracked_repository(
            tmp_path,
            paths=(relative,),
            binary_baseline=(),
            secret_allowlist=(),
        )

    message = str(caught.value)
    assert rule_id in message
    assert payload not in message
    assert "value redacted" in message


def test_standalone_sha256_is_not_a_secret() -> None:
    digest = "a" * 64
    assert gate.scan_text("manifest.json", f'{{"sha256":"{digest}"}}') == ()


def test_binary_baseline_is_exact_and_digest_pinned(tmp_path: Path) -> None:
    relative = "binary.bin"
    raw = b"prefix\0payload"
    (tmp_path / relative).write_bytes(raw)
    baseline = (
        gate.BinaryBaseline(
            relative,
            gate._sha256_bytes(raw),
            "BINARY_OR_LARGE",
            "fixture",
        ),
    )

    assert gate.scan_tracked_repository(
        tmp_path,
        paths=(relative,),
        binary_baseline=baseline,
        secret_allowlist=(),
    ) == {
        "tracked": 1,
        "scanned_text": 0,
        "exact_binary_allowlisted": 1,
        "allowed_findings": 0,
    }
    (tmp_path / relative).write_bytes(raw + b"drift")
    with pytest.raises(gate.FinalGateError, match="digest drift"):
        gate.scan_tracked_repository(
            tmp_path,
            paths=(relative,),
            binary_baseline=baseline,
            secret_allowlist=(),
        )


def test_unreviewed_binary_and_broad_allowlist_are_red(tmp_path: Path) -> None:
    (tmp_path / "new.bin").write_bytes(b"x\0y")
    with pytest.raises(gate.FinalGateError, match="unreviewed binary"):
        gate.scan_tracked_repository(
            tmp_path,
            paths=("new.bin",),
            binary_baseline=(),
            secret_allowlist=(),
        )
    with pytest.raises(gate.FinalGateError, match="exact and owned"):
        gate.scan_tracked_repository(
            tmp_path,
            paths=("new.bin",),
            binary_baseline=(),
            secret_allowlist=(
                gate.SecretAllowlist("tests/*", "RULE", "fixture", "owner"),
            ),
        )


@pytest.mark.parametrize(
    "requirement",
    [
        "demo>=1.0",
        "demo~=1.0",
        "demo==1.*",
        "demo @ https://example.invalid/demo.whl",
        "git+https://example.invalid/demo.git",
        "-e ./demo",
    ],
)
def test_python_requirement_non_exact_forms_are_red(requirement: str) -> None:
    with pytest.raises(gate.FinalGateError):
        gate.validate_python_requirements(requirement)


def test_python_requirement_parser_accepts_extras_and_inline_comment() -> None:
    assert gate.validate_python_requirements(
        "psycopg[binary]==3.2.3  # driver\nhttpx==0.28.1\n"
    ) == ("psycopg", "httpx")


def test_node_lock_accepts_root_ranges_but_rejects_resolved_drift() -> None:
    payload = {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"react": "^19.0.0"}},
            "node_modules/react": {
                "version": "19.2.8",
                "resolved": "https://registry.npmjs.org/react/-/react-19.2.8.tgz",
                "integrity": "sha512-QUJDRA==",
            },
        },
    }
    assert gate.validate_node_lock(payload) == 1
    mutated = copy.deepcopy(payload)
    mutated["packages"]["node_modules/react"].pop("integrity")
    with pytest.raises(gate.FinalGateError, match="integrity"):
        gate.validate_node_lock(mutated)


@pytest.mark.parametrize("tag", ["", "latest", "repo:tag"])
def test_team_image_tag_must_render_to_one_non_latest_tag(tag: str) -> None:
    with pytest.raises(gate.FinalGateError, match="TEAM_IMAGE_TAG"):
        gate.validate_container_pins(gate.REPOSITORY_ROOT, team_image_tag=tag)


def test_timestamp_registry_and_naive_ddl_are_exact() -> None:
    manifest = json.loads(
        (gate.REPOSITORY_ROOT / "infra/bootstrap/source-manifest-v4.json").read_text(
            encoding="utf-8"
        )
    )
    ddl = (gate.REPOSITORY_ROOT / "infra/bootstrap/001_base_schema.sql").read_text(
        encoding="utf-8"
    )
    assert (
        sum(
            len(columns)
            for columns in gate.validate_timestamp_contract(manifest, ddl).values()
        )
        == 10
    )
    with pytest.raises(gate.FinalGateError, match="DDL type drift"):
        gate.validate_timestamp_contract(
            manifest,
            ddl.replace(
                "approved_at               timestamp", "approved_at timestamptz"
            ),
        )


def test_api_datetime_inventory_and_offset_mutation() -> None:
    from app.main import app

    fields = gate.collect_openapi_datetime_fields(app.openapi())
    public_fields = gate.validate_openapi_datetime_inventory(app.openapi())
    assert set(fields) == gate.OPENAPI_DATETIME_FIELDS
    assert len(public_fields) == 16
    assert not (set(public_fields) & gate.INTERNAL_DATETIME_EXCEPTIONS)
    gate.validate_api_datetime_samples(
        {
            "AlarmItem.occurred_at": (
                datetime(2026, 8, 4, 6, 52, 29),
                "2026-08-04T06:52:29+09:00",
            ),
            "InternalHmac.ts": (
                datetime(2026, 8, 3, 21, 52, 29, tzinfo=UTC),
                "2026-08-03T21:52:29+00:00",
            ),
        },
        exceptions=("InternalHmac.ts",),
    )
    with pytest.raises(gate.FinalGateError, match=r"not \+09:00"):
        gate.validate_api_datetime_samples(
            {
                "AlarmItem.occurred_at": (
                    datetime(2026, 8, 4, 6, 52, 29),
                    "2026-08-03T21:52:29+00:00",
                )
            }
        )
    with pytest.raises(gate.FinalGateError, match="wall-time drift"):
        gate.validate_api_datetime_samples(
            {
                "ActionItem.created_at": (
                    datetime(2026, 8, 4, 6, 52, 29),
                    "2026-08-04T15:52:29+09:00",
                )
            }
        )


def test_report_schema_aggregation_and_markdown_projection_are_exact() -> None:
    report = gate.build_skeleton_report()
    assert report["stage"] == "SKELETON"
    assert report["overall_verdict"] == "INCOMPLETE"
    gate.validate_report(report)
    markdown = gate.project_markdown(report)
    assert gate._sha256_bytes(gate.canonical_report_bytes(report)) in markdown
    assert "NFR-03" in markdown
    assert "EVIDENCE_MISSING" in markdown

    final = copy.deepcopy(report)
    final["stage"] = "FINAL"
    final["overall_verdict"] = gate.aggregate_verdict("FINAL", final["rules"])
    assert final["overall_verdict"] == "BLOCKED"
    nfr14 = next(rule for rule in final["rules"] if rule["rule_id"] == "NFR-14")
    nfr14["status"] = "PASS"
    final["overall_verdict"] = gate.aggregate_verdict("FINAL", final["rules"])
    assert final["overall_verdict"] == "PASS_WITH_RESIDUALS"


def test_committed_report_pair_is_exact_generator_projection() -> None:
    """Bind both committed deliverables to the current stage-1 generator."""

    gate.verify_report_pair(gate.build_skeleton_report())


def test_report_rejects_rule_level_sha_and_projection_drift(tmp_path: Path) -> None:
    report = gate.build_skeleton_report()
    mutated = copy.deepcopy(report)
    mutated["rules"][0]["sha256"] = "a" * 64
    with pytest.raises(gate.FinalGateError, match="rule schema"):
        gate.validate_report(mutated)

    original_json = gate.REPORT_JSON_PATH
    original_markdown = gate.REPORT_MARKDOWN_PATH
    try:
        gate.REPORT_JSON_PATH = tmp_path / "report.json"
        gate.REPORT_MARKDOWN_PATH = tmp_path / "report.md"
        gate.write_report_pair(report)
        gate.REPORT_MARKDOWN_PATH.write_text("drift\n", encoding="utf-8")
        with pytest.raises(gate.FinalGateError, match="Markdown projection drift"):
            gate.verify_report_pair(report)
    finally:
        gate.REPORT_JSON_PATH = original_json
        gate.REPORT_MARKDOWN_PATH = original_markdown


def test_postgres_historical_validator_calls_read_only_collector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import postgres_backup
    import transition_public_postgres as transition

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        transition,
        "read_approval",
        lambda _path: ({"change_ref": "GH-108"}, "a" * 64),
    )
    monkeypatch.setattr(
        postgres_backup,
        "backup_root_trust",
        lambda _root, change_ref: ("0700", None),
    )

    def collect(root: Path, change_ref: str, **kwargs: object):
        seen.update(root=root, change_ref=change_ref, **kwargs)
        return {"kosa_agent": {"archive": "b" * 64}}

    monkeypatch.setattr(transition, "collect_closure_digests", collect)
    monkeypatch.setattr(
        transition,
        "record_closure_evidence",
        lambda **_kwargs: pytest.fail("writer must not be reachable"),
    )
    result = gate.validate_postgres_historical_evidence(
        tmp_path / "approval.json", tmp_path
    )
    assert result == {"kosa_agent": {"archive": "b" * 64}}
    assert seen["change_ref"] == "GH-108"


def test_neo4j_historical_validator_ignores_ttl_but_cross_binds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import bootstrap_neo4j_graph as bootstrap

    target = "c" * 64
    graph = "d" * 64
    schema = "e" * 64
    backup_file = "f" * 64
    recorded = datetime(2026, 8, 24, tzinfo=UTC)
    preflight = {
        "database": "neo4j",
        "target_fingerprint_sha256": target,
        "schema_fingerprint_sha256": schema,
        "existing_graph_fingerprint_sha256": graph,
        "recorded_at": recorded.isoformat(),
    }
    manifest = {
        "database": "neo4j",
        "target_fingerprint_sha256": target,
        "schema_fingerprint_sha256": schema,
        "backup_graph_fingerprint_sha256": graph,
        "backup_file_sha256": backup_file,
        "node_count": 44,
        "relationship_count": 85,
    }
    paths = {
        "preflight": tmp_path / "preflight.json",
        "manifest": tmp_path / "neo4j.manifest.json",
        "restore": tmp_path / "restore.json",
        "marker": tmp_path / "marker.json",
    }
    manifest_raw = json.dumps(manifest).encode()
    manifest_sha = gate._sha256_bytes(manifest_raw)
    restore = {
        "database": "neo4j",
        "target_fingerprint_sha256": target,
        "schema_fingerprint_sha256": schema,
        "backup_graph_fingerprint_sha256": graph,
        "backup_file_sha256": backup_file,
        "backup_manifest_sha256": manifest_sha,
    }
    marker = {
        "database": "neo4j",
        "approval_ref": "GH-128",
        "status": "ADOPTED_EXISTING",
        "node_count": 44,
        "relationship_count": 85,
        "actual_graph_fingerprint_sha256": graph,
    }
    for name, payload in (
        ("preflight", preflight),
        ("restore", restore),
        ("marker", marker),
    ):
        paths[name].write_text(json.dumps(payload), encoding="utf-8")
    paths["manifest"].write_bytes(manifest_raw)
    monkeypatch.setattr(bootstrap, "validate_receipt", lambda *_args: None)
    monkeypatch.setattr(
        bootstrap,
        "load_backup_bundle",
        lambda *_args: (
            manifest,
            {},
            SimpleNamespace(node_count=44, relationship_count=85),
            tmp_path / "backup.json",
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "validate_replace_evidence",
        lambda *_args, **_kwargs: pytest.fail("24-hour replace TTL must not be used"),
    )
    result = gate.validate_neo4j_historical_evidence(
        preflight_path=paths["preflight"],
        backup_manifest_path=paths["manifest"],
        restore_receipt_path=paths["restore"],
        backup_root=tmp_path,
        tracked_marker_path=paths["marker"],
        now=recorded + timedelta(days=30),
    )
    assert result["preflight_age_seconds"] == 30 * 24 * 60 * 60
    mutated = dict(marker, approval_ref="GH-130")
    paths["marker"].write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(gate.FinalGateError, match="cross-binding"):
        gate.validate_neo4j_historical_evidence(
            preflight_path=paths["preflight"],
            backup_manifest_path=paths["manifest"],
            restore_receipt_path=paths["restore"],
            backup_root=tmp_path,
            tracked_marker_path=paths["marker"],
            now=recorded + timedelta(days=30),
        )
