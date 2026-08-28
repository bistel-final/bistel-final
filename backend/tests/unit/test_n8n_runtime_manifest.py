"""V5-C-4.2 n8n runtime pin schema and source-hash contracts."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "verify_n8n_runtime_manifest.py"
)
_spec = importlib.util.spec_from_file_location(
    "verify_n8n_runtime_manifest", MODULE_PATH
)
runtime_manifest = importlib.util.module_from_spec(_spec)
sys.modules["verify_n8n_runtime_manifest"] = runtime_manifest
_spec.loader.exec_module(runtime_manifest)
rm = runtime_manifest

FAKE_DIGEST = "sha256:" + "a" * 64
RUNNER_AMD64_DIGEST = "sha256:" + "d" * 64
RUNNER_ARM64_DIGEST = "sha256:" + "e" * 64
RUNNER_MANIFEST_LIST_DIGEST = "sha256:" + "f" * 64


def _write_workflows(root: Path) -> None:
    for index, name in enumerate(rm.WORKFLOW_FILES, start=1):
        (root / name).write_text(f'{{"workflow":{index}}}\n', encoding="utf-8")


def _valid_manifest(root: Path) -> dict:
    return {
        "format_version": 1,
        "n8n": {
            "repository": "n8nio/n8n",
            "version": "2.32.7",
            "manifest_list_digest": rm.EXPECTED_N8N_MANIFEST_LIST_DIGEST,
            "platform_digests": {
                "linux/amd64": rm.EXPECTED_N8N_PLATFORM_DIGESTS["linux/amd64"],
                "linux/arm64": rm.EXPECTED_N8N_PLATFORM_DIGESTS["linux/arm64"],
            },
        },
        "runtime": {
            "database_type": "postgresdb",
            "execution_mode": "regular",
            "main_worker": "single",
        },
        "task_runner": {"mode": "internal", "image": None},
        "workflow_source_sha256": rm.workflow_source_hashes(root),
    }


def _external_runner() -> dict:
    return {
        "mode": "external",
        "image": {
            "repository": "n8nio/runners",
            "version": "2.32.7",
            "manifest_list_digest": RUNNER_MANIFEST_LIST_DIGEST,
            "platform_digests": {
                "linux/amd64": RUNNER_AMD64_DIGEST,
                "linux/arm64": RUNNER_ARM64_DIGEST,
            },
        },
    }


def test_valid_internal_runner_manifest_passes(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)

    validated = rm.validate_runtime_manifest(payload, workflow_root=tmp_path)

    assert validated.n8n.version == "2.32.7"
    assert validated.task_runner.mode == "internal"


def test_valid_external_runner_manifest_passes(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["task_runner"] = _external_runner()

    validated = rm.validate_runtime_manifest(payload, workflow_root=tmp_path)

    assert validated.task_runner.mode == "external"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("n8n", "version"), "latest"),
        (("n8n", "version"), "2.32"),
        (("n8n", "manifest_list_digest"), "sha256:pending"),
        (("runtime", "database_type"), "unknown"),
        (("runtime", "execution_mode"), "pending"),
        (("runtime", "main_worker"), "unknown"),
    ],
)
def test_placeholder_or_incoherent_runtime_values_fail(
    tmp_path: Path,
    path: tuple[str, str],
    value: str,
) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload[path[0]][path[1]] = value

    with pytest.raises(rm.RuntimeManifestError):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


@pytest.mark.parametrize(
    ("execution_mode", "main_worker"),
    [("regular", "separated"), ("queue", "single")],
)
def test_execution_mode_and_topology_mismatch_fails(
    tmp_path: Path,
    execution_mode: str,
    main_worker: str,
) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["runtime"]["execution_mode"] = execution_mode
    payload["runtime"]["main_worker"] = main_worker

    with pytest.raises(rm.RuntimeManifestError, match="topology"):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_external_runner_version_must_match_n8n(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["task_runner"] = _external_runner()
    payload["task_runner"]["image"]["version"] = "2.31.0"

    with pytest.raises(rm.RuntimeManifestError, match="version"):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_well_formed_but_unreviewed_n8n_digest_fails(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["n8n"]["manifest_list_digest"] = FAKE_DIGEST

    with pytest.raises(rm.RuntimeManifestError, match="reviewed target pin"):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_running_image_must_match_allowlisted_platform_digest(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    manifest = rm.validate_runtime_manifest(payload, workflow_root=tmp_path)

    rm.validate_running_image(
        manifest,
        platform="linux/amd64",
        digest=rm.EXPECTED_N8N_PLATFORM_DIGESTS["linux/amd64"],
    )
    with pytest.raises(rm.RuntimeManifestError, match="child digest"):
        rm.validate_running_image(
            manifest,
            platform="linux/amd64",
            digest=rm.EXPECTED_N8N_PLATFORM_DIGESTS["linux/arm64"],
        )
    with pytest.raises(rm.RuntimeManifestError, match="platform"):
        rm.validate_running_image(manifest, platform="linux/s390x", digest=FAKE_DIGEST)


def test_internal_runner_rejects_image_details(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["task_runner"]["image"] = _external_runner()["image"]

    with pytest.raises(rm.RuntimeManifestError):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_external_runner_requires_image_details(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["task_runner"] = {"mode": "external", "image": None}

    with pytest.raises(rm.RuntimeManifestError):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_unknown_field_fails_closed(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["public_url"] = "https://must-not-be-recorded.invalid"

    with pytest.raises(rm.RuntimeManifestError, match="Extra inputs"):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


@pytest.mark.parametrize("filename", rm.WORKFLOW_FILES)
def test_workflow_source_hash_drift_fails(tmp_path: Path, filename: str) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    (tmp_path / filename).write_text('{"changed":true}\n', encoding="utf-8")

    with pytest.raises(rm.RuntimeManifestError, match=filename):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_workflow_source_set_is_exact(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    del payload["workflow_source_sha256"]["WF4-result-writeback.json"]
    payload["workflow_source_sha256"]["WF5-unplanned.json"] = "sha256:" + "1" * 64

    with pytest.raises(rm.RuntimeManifestError):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_missing_workflow_source_fails_without_hashing(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    (tmp_path / "WF3-mes-hold.json").unlink()

    with pytest.raises(rm.RuntimeManifestError, match="WF3-mes-hold.json"):
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)


def test_validation_error_does_not_echo_unknown_value(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    payload["n8n"]["version"] = "do-not-echo-this-value"

    with pytest.raises(rm.RuntimeManifestError) as raised:
        rm.validate_runtime_manifest(payload, workflow_root=tmp_path)

    assert "do-not-echo-this-value" not in str(raised.value)


def test_tracked_schema_matches_generated_schema() -> None:
    rm.check_schema()


def test_tracked_runtime_manifest_matches_workflow_sources() -> None:
    payload = rm.load_json_object(rm.MANIFEST_PATH)

    manifest = rm.validate_runtime_manifest(payload)

    assert manifest.n8n.version == "2.32.7"
    assert manifest.n8n.manifest_list_digest == rm.EXPECTED_N8N_MANIFEST_LIST_DIGEST
    assert (
        manifest.n8n.platform_digests.model_dump(by_alias=True)
        == rm.EXPECTED_N8N_PLATFORM_DIGESTS
    )
    assert manifest.runtime.database_type == "sqlite"
    assert manifest.runtime.execution_mode == "regular"
    assert manifest.runtime.main_worker == "single"
    assert manifest.task_runner.mode == "internal"


def test_load_json_object_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(rm.RuntimeManifestError, match="root"):
        rm.load_json_object(path)


def test_schema_round_trip_is_deterministic(tmp_path: Path) -> None:
    schema_path = tmp_path / "runtime-manifest.schema.json"
    rm.write_schema(schema_path)

    assert schema_path.read_text(encoding="utf-8") == rm.schema_text()
    assert json.loads(rm.schema_text())["$id"].endswith("n8n-runtime-manifest-v1.json")


def test_mutation_fixture_does_not_change_base_manifest(tmp_path: Path) -> None:
    _write_workflows(tmp_path)
    payload = _valid_manifest(tmp_path)
    original = copy.deepcopy(payload)
    mutated = copy.deepcopy(payload)
    mutated["n8n"]["version"] = "latest"

    with pytest.raises(rm.RuntimeManifestError):
        rm.validate_runtime_manifest(mutated, workflow_root=tmp_path)

    assert payload == original
