"""V4-CM-1.1 manifest v3의 file/profile 계약을 DB 없이 검증한다."""

import copy
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3 as mv3  # noqa: E402

DIGEST = "a" * 64
EMPTY_DIGEST = mv3._hash_canonical_rows([])


def _source_manifest() -> dict:
    tables = {}
    for table in sorted(mv3.SOURCE_TABLE_FILES):
        tables[table] = {
            "file_id": mv3.SOURCE_TABLE_FILES[table],
            "columns": list(mv3.SOURCE_EXPECTED_COLUMNS[table]),
            "row_count": 48 if table == "action_history" else 1,
            "content_hash": DIGEST,
        }
    return {
        "format_version": 3,
        "artifact_type": "source_files",
        "dataset_epoch": "kosa_0813",
        "source_archive_sha256": DIGEST,
        "correction_version": "none",
        "hash_algorithm": mv3.HASH_ALGORITHM,
        "tables": tables,
    }


def _corrected_manifest() -> dict:
    tables = {
        table: {
            "file_id": f"corrected/postgres/{table}.csv",
            "columns": ["id"],
            "row_count": 48 if table == "action_history" else 1,
            "content_hash": DIGEST,
        }
        for table in sorted(mv3.CORRECTED_TABLES)
    }
    return {
        "format_version": 3,
        "artifact_type": "corrected_files",
        "dataset_epoch": "kosa_0813",
        "source_archive_sha256": DIGEST,
        "correction_version": "v1",
        "hash_algorithm": mv3.HASH_ALGORITHM,
        "tables": tables,
    }


def _db_manifest(profile: str, stage: str) -> dict:
    contract = mv3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
    action_entry = {
        "columns": ["action_id"],
        "verification_policy": "bootstrap_empty",
        "row_count": 0,
        "content_hash": EMPTY_DIGEST,
    }
    if (profile, stage) == ("evaluation", "evaluation_mock"):
        action_entry = {
            "columns": ["action_id"],
            "verification_policy": "immutable_content",
            "row_count": 48,
            "content_hash": DIGEST,
            "fixture_type": "MOCK",
        }
    return {
        "format_version": 3,
        "artifact_type": "db_bootstrap",
        "dataset_epoch": "kosa_0813",
        "source_archive_sha256": DIGEST,
        "correction_version": "v1",
        "hash_algorithm": mv3.HASH_ALGORITHM,
        "value_normalization_version": mv3.VALUE_NORMALIZATION_VERSION,
        "profile": profile,
        "applies_to": list(mv3.PROFILE_APPLIES_TO[profile]),
        "bootstrap_stage": stage,
        "schema_stage": contract.schema_stage,
        "applied_migrations": list(contract.applied_migrations),
        "tables": {"action_history": action_entry},
    }


def _synthetic_manifest() -> dict:
    return {
        "format_version": 3,
        "artifact_type": "synthetic_evaluation",
        "dataset_epoch": "kosa_0813",
        "source_archive_sha256": DIGEST,
        "correction_version": "synthetic-v1",
        "hash_algorithm": mv3.HASH_ALGORITHM,
        "usage_scope": "EVALUATION_ONLY",
        "ground_truth_available": True,
        "label_source": "SYNTHETIC_GENERATOR",
        "production_ground_truth_available": False,
    }


def _csv_payload(table: str, *, bom: bool = True) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    columns = list(mv3.SOURCE_EXPECTED_COLUMNS[table])
    writer.writerow(columns)
    row_count = 48 if table == "action_history" else 1
    for index in range(row_count):
        writer.writerow([f"{table}-{index + 1}", *([""] * (len(columns) - 1))])
    payload = stream.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + payload) if bom else payload


def _source_zip(tmp_path: Path) -> tuple[Path, Path]:
    archive_path = tmp_path / "kosa_0813.zip"
    files = {
        file_id: _csv_payload(table)
        for table, file_id in mv3.SOURCE_TABLE_FILES.items()
    }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)
    inventory = [
        {
            "path": name,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, payload in sorted(files.items())
    ]
    epoch = {
        "format_version": 1,
        "artifact_type": "dataset_epoch_registration",
        "dataset_epoch": "kosa_0813",
        "received_date": "2026-08-13",
        "archive": {
            "filename": archive_path.name,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        },
        "public_fault_ground_truth_available": False,
        "inventory_scope": "non_directory_zip_members",
        "file_count": len(inventory),
        "file_inventory": inventory,
    }
    epoch_path = tmp_path / "dataset-epoch.json"
    epoch_path.write_text(json.dumps(epoch), encoding="utf-8")
    return archive_path, epoch_path


class TestCanonicalRows:
    def test_input_order_does_not_change_hash(self) -> None:
        rows_a = [{"id": "2", "value": "나"}, {"id": "1", "value": "가"}]
        rows_b = list(reversed(rows_a))

        assert mv3._hash_canonical_rows(rows_a) == mv3._hash_canonical_rows(rows_b)

    def test_nfc_equivalent_strings_have_same_hash(self) -> None:
        assert mv3._hash_canonical_rows([{"v": "é"}]) == mv3._hash_canonical_rows(
            [{"v": "e\u0301"}]
        )

    def test_numeric_looking_strings_are_not_numbers(self) -> None:
        assert mv3._hash_canonical_rows([{"v": "01"}]) != mv3._hash_canonical_rows(
            [{"v": 1}]
        )


class TestCsvContract:
    def test_start_bom_and_no_bom_have_same_result(self) -> None:
        table = "fdc_trace"

        with_bom = mv3.parse_csv_bytes(
            _csv_payload(table),
            table=table,
            expected_columns=mv3.SOURCE_EXPECTED_COLUMNS[table],
        )
        without_bom = mv3.parse_csv_bytes(
            _csv_payload(table, bom=False),
            table=table,
            expected_columns=mv3.SOURCE_EXPECTED_COLUMNS[table],
        )

        assert with_bom == without_bom
        assert mv3._hash_canonical_rows(with_bom[1]) == mv3._hash_canonical_rows(
            without_bom[1]
        )

    @pytest.mark.parametrize(
        "payload",
        [
            b"id,id\n1,2\n",
            b"id,\n1,2\n",
            b"id,name\n1\n",
            "id,name\n1,\ufeffbad\n".encode(),
            "id,\ufeffname\n1,bad\n".encode(),
        ],
    )
    def test_invalid_csv_shape_or_internal_bom_is_rejected(
        self, payload: bytes
    ) -> None:
        with pytest.raises(mv3.ManifestSchemaError):
            mv3.parse_csv_bytes(
                payload, table="fixture", expected_columns=("id", "name")
            )

    def test_empty_cell_is_preserved_as_empty_string(self) -> None:
        _, rows = mv3.parse_csv_bytes(
            b"id,name\n1,\n", table="fixture", expected_columns=("id", "name")
        )

        assert rows == [{"id": "1", "name": ""}]


class TestArchivePreflight:
    def test_build_source_manifest_checks_all_members_before_csv(
        self, tmp_path: Path
    ) -> None:
        archive_path, epoch_path = _source_zip(tmp_path)

        manifest = mv3.build_source_manifest(archive_path, epoch_path=epoch_path)

        assert manifest["format_version"] == 3
        assert manifest["artifact_type"] == "source_files"
        assert set(manifest["tables"]) == set(mv3.SOURCE_TABLE_FILES)
        assert manifest["tables"]["action_history"]["row_count"] == 48
        assert all(
            entry["row_count"] == 1
            for table, entry in manifest["tables"].items()
            if table != "action_history"
        )

    def test_archive_sha_mismatch_fails_before_parsing(self, tmp_path: Path) -> None:
        archive_path, epoch_path = _source_zip(tmp_path)
        epoch = json.loads(epoch_path.read_text())
        epoch["archive"]["sha256"] = "0" * 64
        epoch_path.write_text(json.dumps(epoch))

        with pytest.raises(mv3.ArtifactMismatchError, match="ZIP SHA-256"):
            mv3.build_source_manifest(archive_path, epoch_path=epoch_path)

    def test_inventory_member_hash_mismatch_is_rejected(self, tmp_path: Path) -> None:
        archive_path, epoch_path = _source_zip(tmp_path)
        epoch = json.loads(epoch_path.read_text())
        epoch["file_inventory"][0]["sha256"] = "0" * 64
        epoch_path.write_text(json.dumps(epoch))

        with pytest.raises(mv3.ArtifactMismatchError, match="member SHA-256"):
            mv3.build_source_manifest(archive_path, epoch_path=epoch_path)

    def test_missing_inventory_member_is_rejected(self, tmp_path: Path) -> None:
        archive_path, epoch_path = _source_zip(tmp_path)
        epoch = json.loads(epoch_path.read_text())
        epoch["file_inventory"].pop()
        epoch["file_count"] -= 1
        epoch_path.write_text(json.dumps(epoch))

        with pytest.raises(mv3.ArtifactMismatchError, match="inventory"):
            mv3.build_source_manifest(archive_path, epoch_path=epoch_path)

    def test_archive_read_error_does_not_leak_local_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive_path, epoch_path = _source_zip(tmp_path)
        epoch = mv3.load_dataset_epoch(epoch_path)

        def deny_read(_path: Path) -> str:
            raise PermissionError("/Users/person/private/kosa_0813.zip")

        monkeypatch.setattr(mv3, "_sha256_file", deny_read)
        with pytest.raises(mv3.ArtifactMismatchError) as error:
            mv3.verify_archive_inventory(archive_path, epoch)

        assert "/Users/person" not in str(error.value)


class TestManifestSchemas:
    def test_source_and_corrected_contracts_are_separate(self) -> None:
        mv3.validate_manifest_schema(
            _source_manifest(), expected_artifact_type="source_files"
        )
        mv3.validate_manifest_schema(
            _corrected_manifest(), expected_artifact_type="corrected_files"
        )

    @pytest.mark.parametrize("profile,stage", sorted(mv3.BOOTSTRAP_STAGE_CONTRACTS))
    def test_all_six_profile_stage_contracts_are_valid(
        self, profile: str, stage: str
    ) -> None:
        manifest = _db_manifest(profile, stage)

        mv3.validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile=profile,
            expected_stage=stage,
        )

    def test_impossible_profile_stage_is_rejected(self) -> None:
        manifest = _db_manifest("runtime", "runtime_clean")
        manifest["profile"] = "evaluation"
        manifest["applies_to"] = ["kosa_text2sql"]

        with pytest.raises(mv3.ManifestMetadataError, match="허용되지 않은"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    def test_profile_applies_to_requires_exact_ordered_set(self) -> None:
        manifest = _db_manifest("runtime", "base_schema")
        manifest["applies_to"] = ["kosa_agent"]

        with pytest.raises(mv3.ManifestMetadataError, match="applies_to"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize("applies_to", [[], ["kosa_agent", "kosa_agent"]])
    def test_profile_applies_to_rejects_empty_or_duplicate(
        self, applies_to: list[str]
    ) -> None:
        manifest = _db_manifest("runtime", "base_schema")
        manifest["applies_to"] = applies_to

        with pytest.raises(mv3.ManifestMetadataError, match="applies_to"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    def test_expected_profile_and_stage_prevent_cross_contamination(self) -> None:
        manifest = _db_manifest("runtime", "base_schema")

        with pytest.raises(mv3.ManifestMetadataError, match="profile"):
            mv3.validate_manifest_schema(
                manifest,
                expected_artifact_type="db_bootstrap",
                expected_profile="evaluation",
            )
        with pytest.raises(mv3.ManifestMetadataError, match="bootstrap_stage"):
            mv3.validate_manifest_schema(
                manifest,
                expected_artifact_type="db_bootstrap",
                expected_stage="corrected_base",
            )

    def test_action_history_counts_are_independent_between_artifacts(self) -> None:
        source = _source_manifest()
        corrected = _corrected_manifest()
        runtime = _db_manifest("runtime", "runtime_clean")
        evaluation = _db_manifest("evaluation", "evaluation_mock")

        assert source["tables"]["action_history"]["row_count"] == 48
        assert corrected["tables"]["action_history"]["row_count"] == 48
        assert runtime["tables"]["action_history"]["row_count"] == 0
        assert evaluation["tables"]["action_history"]["row_count"] == 48

        mv3.validate_manifest_schema(source, expected_artifact_type="source_files")
        mv3.validate_manifest_schema(
            corrected, expected_artifact_type="corrected_files"
        )
        mv3.validate_manifest_schema(runtime, expected_artifact_type="db_bootstrap")
        mv3.validate_manifest_schema(evaluation, expected_artifact_type="db_bootstrap")

    def test_evaluation_mock_requires_fixture_metadata(self) -> None:
        manifest = _db_manifest("evaluation", "evaluation_mock")
        manifest["tables"]["action_history"].pop("fixture_type")

        with pytest.raises(mv3.ManifestSchemaError, match="MOCK immutable 48행"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    def test_mock_fixture_metadata_is_limited_to_evaluation_action(self) -> None:
        manifest = _db_manifest("runtime", "base_schema")
        manifest["tables"]["reference_table"] = {
            "columns": ["id"],
            "verification_policy": "immutable_content",
            "row_count": 1,
            "content_hash": DIGEST,
            "fixture_type": "MOCK",
        }

        with pytest.raises(mv3.ManifestSchemaError, match="evaluation_mock"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize(
        "profile,stage",
        [("runtime", "runtime_clean"), ("evaluation", "evaluation_mock")],
    )
    @pytest.mark.parametrize("policy", ["immutable_content", "bootstrap_empty"])
    def test_nl_query_log_cannot_use_content_hash_policy(
        self, profile: str, stage: str, policy: str
    ) -> None:
        manifest = _db_manifest(profile, stage)
        manifest["tables"]["nl_query_log"] = {
            "columns": ["query_id"],
            "verification_policy": policy,
            "row_count": 0,
            "content_hash": EMPTY_DIGEST,
        }

        with pytest.raises(mv3.ManifestSchemaError, match="schema_only"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize(
        "profile,stage",
        [("runtime", "runtime_clean"), ("evaluation", "evaluation_mock")],
    )
    def test_nl_query_log_schema_only_is_valid(self, profile: str, stage: str) -> None:
        manifest = _db_manifest(profile, stage)
        manifest["tables"]["nl_query_log"] = {
            "columns": ["query_id"],
            "verification_policy": "schema_only",
        }

        mv3.validate_manifest_schema(manifest, expected_artifact_type="db_bootstrap")

    @pytest.mark.parametrize(
        "field", ["format_version", "hash_algorithm", "dataset_epoch"]
    )
    def test_common_metadata_mismatch_is_rejected(self, field: str) -> None:
        manifest = _source_manifest()
        manifest[field] = "wrong"

        with pytest.raises(mv3.ManifestMetadataError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

    def test_db_value_normalization_version_is_required_and_fixed(self) -> None:
        manifest = _db_manifest("runtime", "base_schema")
        manifest.pop("value_normalization_version")
        with pytest.raises(mv3.ManifestSchemaError, match="누락"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

        manifest = _db_manifest("runtime", "base_schema")
        manifest["value_normalization_version"] = "db-value-v0"
        with pytest.raises(mv3.ManifestMetadataError, match="normalization"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize("field", sorted(mv3.COMMON_ENVELOPE_KEYS))
    def test_missing_common_envelope_field_is_rejected(self, field: str) -> None:
        manifest = _source_manifest()
        manifest.pop(field)

        with pytest.raises(mv3.ManifestSchemaError, match="누락"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

    def test_source_correction_version_must_be_none(self) -> None:
        manifest = _source_manifest()
        manifest["correction_version"] = "v1"

        with pytest.raises(mv3.ManifestMetadataError, match="none"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

    def test_registered_archive_hash_mismatch_is_rejected(self) -> None:
        manifest = _source_manifest()

        with pytest.raises(mv3.ManifestMetadataError, match="source_archive_sha256"):
            mv3.validate_manifest_schema(
                manifest,
                expected_artifact_type="source_files",
                expected_archive_sha256="b" * 64,
            )

    @pytest.mark.parametrize(
        "artifact", [_corrected_manifest(), _db_manifest("runtime", "base_schema")]
    )
    def test_corrected_and_db_correction_version_cannot_be_none(
        self, artifact: dict
    ) -> None:
        artifact["correction_version"] = "none"

        with pytest.raises(mv3.ManifestMetadataError, match="revision"):
            mv3.validate_manifest_schema(
                artifact, expected_artifact_type=artifact["artifact_type"]
            )

    def test_source_file_id_must_equal_epoch_inventory_path(self) -> None:
        manifest = _source_manifest()
        manifest["tables"]["lot_history"]["file_id"] = (
            "클린데이터셋/postgres/lot_history.csv"
        )

        with pytest.raises(mv3.ManifestSchemaError, match="canonical file_id"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

    def test_boolean_is_not_accepted_as_row_count(self) -> None:
        manifest = _source_manifest()
        manifest["tables"]["lot_history"]["row_count"] = True

        with pytest.raises(mv3.ManifestSchemaError, match="row_count"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

    def test_extra_key_is_rejected(self) -> None:
        manifest = _source_manifest()
        manifest["comment"] = "not allowed"

        with pytest.raises(mv3.ManifestSchemaError, match="추가"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

    def test_verification_mode_is_cli_only(self) -> None:
        manifest = _db_manifest("runtime", "base_schema")
        manifest["verification_mode"] = "bootstrap"

        with pytest.raises(mv3.ManifestSchemaError, match="추가"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    def test_registry_uses_profile_and_bootstrap_stage_in_path(self) -> None:
        paths = {
            key: path.relative_to(mv3.MANIFEST_REGISTRY_ROOT).as_posix()
            for key, path in mv3.BOOTSTRAP_MANIFEST_REGISTRY.items()
        }

        assert paths == {
            key: f"{key[0]}.{key[1]}.json" for key in mv3.BOOTSTRAP_STAGE_CONTRACTS
        }

    def test_synthetic_is_evaluation_only_and_not_db_profile(self) -> None:
        manifest = _synthetic_manifest()
        mv3.validate_manifest_schema(
            manifest, expected_artifact_type="synthetic_evaluation"
        )
        manifest["profile"] = "evaluation"

        with pytest.raises(mv3.ManifestSchemaError, match="추가"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="synthetic_evaluation"
            )

    @pytest.mark.parametrize(
        "policy,valid",
        [
            ("immutable_content", True),
            ("bootstrap_empty", True),
            ("schema_only", True),
            ("mutable_hash", False),
        ],
    )
    def test_db_table_verification_policy(self, policy: str, valid: bool) -> None:
        manifest = _db_manifest("runtime", "base_schema")
        entry = manifest["tables"]["action_history"]
        entry["verification_policy"] = policy
        if policy == "immutable_content":
            manifest["tables"]["reference_table"] = entry
            manifest["tables"]["action_history"] = {
                "columns": ["action_id"],
                "verification_policy": "bootstrap_empty",
                "row_count": 0,
                "content_hash": EMPTY_DIGEST,
            }
            entry = manifest["tables"]["reference_table"]
        if policy == "schema_only":
            manifest["tables"]["reference_table"] = entry
            manifest["tables"]["action_history"] = {
                "columns": ["action_id"],
                "verification_policy": "bootstrap_empty",
                "row_count": 0,
                "content_hash": EMPTY_DIGEST,
            }
            entry = manifest["tables"]["reference_table"]
            entry.pop("row_count")
            entry.pop("content_hash")

        if valid:
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )
        else:
            with pytest.raises(mv3.ManifestSchemaError):
                mv3.validate_manifest_schema(
                    manifest, expected_artifact_type="db_bootstrap"
                )


class TestSensitiveValues:
    @pytest.mark.parametrize(
        "key", ["password", "username", "dsn", "database_url", "credential", "secret"]
    )
    def test_forbidden_keys_are_rejected_without_value_leak(self, key: str) -> None:
        manifest = _source_manifest()
        manifest[key] = "do-not-print-this-value"

        with pytest.raises(mv3.ManifestSchemaError) as error:
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )

        assert "do-not-print-this-value" not in str(error.value)

    @pytest.mark.parametrize(
        "unsafe",
        [
            "postgresql://user:pw@example.test/db",
            "postgresql://example.test/db",
            "file:///Users/person/Downloads/data.csv",
            "/Users/person/Downloads/data.csv",
            r"C:\\Users\\person\\data.csv",
            r"\\server\share\data.csv",
        ],
    )
    def test_credential_uri_and_absolute_paths_are_rejected(self, unsafe: str) -> None:
        manifest = _source_manifest()
        manifest["tables"]["lot_history"]["file_id"] = unsafe

        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="source_files"
            )


class TestGenerationGuard:
    def test_first_generation_requires_confirm(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"

        result = mv3.write_manifest_with_confirmation(
            path, _source_manifest(), confirm=False
        )

        assert result == mv3.EXIT_CONFIRM_REQUIRED
        assert not path.exists()

    def test_confirm_atomically_writes_and_identical_run_is_noop(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "manifest.json"
        manifest = _source_manifest()

        assert (
            mv3.write_manifest_with_confirmation(path, manifest, confirm=True)
            == mv3.EXIT_OK
        )
        before = path.read_bytes()
        assert (
            mv3.write_manifest_with_confirmation(path, manifest, confirm=False)
            == mv3.EXIT_OK
        )
        assert path.read_bytes() == before

    def test_diff_lists_locations_but_not_values(self) -> None:
        actual = {"token": "secret-a"}
        expected = {"token": "secret-b"}

        differences = mv3.compare_manifests(actual, expected)

        assert differences == ["$.token: 값 불일치"]
        assert "secret" not in " ".join(differences)

    def test_atomic_write_error_does_not_leak_local_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def deny_write(_path: Path, _payload: dict) -> None:
            raise PermissionError("/Users/person/private/manifest.json")

        monkeypatch.setattr(mv3, "_atomic_save_json", deny_write)
        with pytest.raises(mv3.VerificationError) as error:
            mv3.write_manifest_with_confirmation(
                tmp_path / "manifest.json", _source_manifest(), confirm=True
            )

        assert "/Users/person" not in str(error.value)


class TestCliContract:
    def _args(self, *values: str):
        return mv3._parser().parse_args(list(values))

    def test_source_requires_archive_and_rejects_db_options(self) -> None:
        with pytest.raises(mv3.VerificationError, match="--archive"):
            mv3.validate_cli_args(self._args("--artifact", "source-files"))
        with pytest.raises(mv3.VerificationError, match="DB/corrected"):
            mv3.validate_cli_args(
                self._args(
                    "--artifact",
                    "source-files",
                    "--archive",
                    "fixture.zip",
                    "--profile",
                    "runtime",
                )
            )

    def test_db_requires_profile_stage_mode_target(self) -> None:
        with pytest.raises(mv3.VerificationError, match="--profile"):
            mv3.validate_cli_args(self._args("--artifact", "db-bootstrap"))

    def test_db_rejects_target_outside_profile_before_lookup(self) -> None:
        args = self._args(
            "--artifact",
            "db-bootstrap",
            "--profile",
            "runtime",
            "--stage",
            "base_schema",
            "--mode",
            "bootstrap",
            "--target",
            "kosa_text2sql",
        )

        with pytest.raises(mv3.ManifestMetadataError, match="target"):
            mv3.validate_cli_args(args)

    def test_confirm_without_generate_is_rejected(self) -> None:
        args = self._args(
            "--artifact", "source-files", "--archive", "fixture.zip", "--confirm"
        )

        with pytest.raises(mv3.VerificationError, match="--generate"):
            mv3.validate_cli_args(args)

    def test_unregistered_db_stage_fails_without_connector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing = tmp_path / "not-registered.json"
        monkeypatch.setitem(
            mv3.BOOTSTRAP_MANIFEST_REGISTRY,
            ("runtime", "base_schema"),
            missing,
        )

        result = mv3.main(
            [
                "--artifact",
                "db-bootstrap",
                "--profile",
                "runtime",
                "--stage",
                "base_schema",
                "--mode",
                "bootstrap",
                "--target",
                "kosa_agent",
            ]
        )

        assert result == mv3.EXIT_NOT_REGISTERED

    def test_confirm_cannot_enable_unregistered_corrected_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            mv3, "CORRECTED_MANIFEST_PATH", tmp_path / "not-registered.json"
        )

        result = mv3.main(
            [
                "--artifact",
                "corrected-files",
                "--data-dir",
                "corrected",
                "--generate",
                "--confirm",
            ]
        )

        assert result == mv3.EXIT_NOT_REGISTERED


def test_source_manifest_fixture_is_not_mutated_by_validation() -> None:
    manifest = _source_manifest()
    before = copy.deepcopy(manifest)

    mv3.validate_manifest_schema(manifest, expected_artifact_type="source_files")

    assert manifest == before
