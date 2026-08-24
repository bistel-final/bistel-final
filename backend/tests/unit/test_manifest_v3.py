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
FINAL_CORRECTION_VERSION = mv3.FINAL_CORRECTION_VERSION

#: **폐기 epoch.** `V5-CM-1.8`이 active 기준을 최종 epoch으로 옮겼다.
#:
#: 정상 fixture는 `mv3.DATASET_EPOCH`를 따르고, 이 상수는 **"구 epoch을 거부하는가"를
#: 검증하는 곳에만** 쓴다. 일괄 치환으로 두 의미를 섞으면 epoch drift를 잡던 음성
#: 테스트가 조용히 뒤집힌다(계획 §3.1).
DEPRECATED_DATASET_EPOCH = "kosa_0813"


def _source_manifest(*, dataset_epoch: str | None = None) -> dict:
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
        "dataset_epoch": dataset_epoch or mv3.DATASET_EPOCH,
        "source_archive_sha256": DIGEST,
        "correction_version": "none",
        "hash_algorithm": mv3.HASH_ALGORITHM,
        "tables": tables,
    }


def _db_manifest(profile: str, stage: str, *, dataset_epoch: str | None = None) -> dict:
    contract = mv3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
    # **stage 계약에서 파생한다.** stage 이름으로 분기하면 새 stage가 등록될 때마다
    # fixture가 따라가지 못한다(`V5-CM-1.8`).
    action_entry: dict = {
        "columns": ["action_id"],
        "verification_policy": contract.action_policy,
        "row_count": contract.action_rows,
        "content_hash": EMPTY_DIGEST if contract.action_rows == 0 else DIGEST,
    }
    if contract.action_fixture_type is not None:
        action_entry["fixture_type"] = contract.action_fixture_type
    return {
        "format_version": 3,
        "artifact_type": "db_bootstrap",
        "dataset_epoch": dataset_epoch or mv3.DATASET_EPOCH,
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


def _synthetic_manifest(*, dataset_epoch: str | None = None) -> dict:
    return {
        "format_version": 3,
        "artifact_type": "synthetic_evaluation",
        "dataset_epoch": dataset_epoch or mv3.DATASET_EPOCH,
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


class TestRetiredSourcePath:
    """구 v1 source 경로는 **fail-closed**다(계획 §3.1).

    `build_source_manifest()`는 epoch v1의 `file_inventory`로 ZIP member를 대조하고
    `source-data-manifest.json`을 만들던 경로다. 최종 epoch v2는 member 목록을
    `intake_artifact`가 가리키는 별도 artifact에 위임하며, source manifest 정본은
    `V5-CM-1.3`·`V5-CM-1.4`의 `build_source_manifest_v4`가 발급한다.

    **물리 삭제는 `V5-CM-1.7` 소관이다.** 여기서는 최종 epoch에서 이 경로가 열리지
    않는다는 것만 잠근다.
    """

    def test_building_a_v3_source_manifest_is_refused(self, tmp_path: Path) -> None:
        archive_path, _epoch_path = _source_zip(tmp_path)

        with pytest.raises(mv3.RetiredSourcePathError):
            mv3.build_source_manifest(archive_path)

    def test_the_refusal_names_the_successor_without_leaking_paths(
        self, tmp_path: Path
    ) -> None:
        archive_path, _epoch_path = _source_zip(tmp_path)

        with pytest.raises(mv3.RetiredSourcePathError) as error:
            mv3.build_source_manifest(archive_path)

        message = str(error.value)
        assert "build_source_manifest_v4" in message
        for marker in ("/Users/", "C:\\", "postgresql://", str(tmp_path)):
            assert marker not in message, marker

    def test_the_v1_target_artifact_no_longer_exists(self) -> None:
        """`source-data-manifest.json`은 실물이 없다.

        v4가 `source-manifest-v4.json`으로 대체했다. 구 경로가 살아 있으면 존재하지
        않는 정본을 향해 쓰게 된다.
        """

        assert not mv3.SOURCE_MANIFEST_PATH.exists()

        import build_source_manifest_v4 as v4

        assert v4.MANIFEST_V4_PATH.exists()
        assert v4.MANIFEST_V4_PATH != mv3.SOURCE_MANIFEST_PATH

    def test_the_cli_source_files_branch_is_refused_too(self, tmp_path: Path) -> None:
        """CLI로 우회할 수 없다."""

        archive_path, _epoch_path = _source_zip(tmp_path)

        with pytest.raises(mv3.RetiredSourcePathError):
            mv3.run(["--artifact", "source-files", "--archive", str(archive_path)])

    def test_the_final_epoch_carries_no_v1_member_inventory(self) -> None:
        """v2는 member 목록을 갖지 않는다 — 그래서 구 경로가 성립하지 않는다."""

        epoch = mv3.load_dataset_epoch()

        assert "file_inventory" not in epoch
        assert "file_count" not in epoch
        assert epoch["intake_artifact"]


class TestManifestSchemas:
    def test_source_contract_validates(self) -> None:
        mv3.validate_manifest_schema(
            _source_manifest(), expected_artifact_type="source_files"
        )

    @pytest.mark.parametrize("profile,stage", sorted(mv3.BOOTSTRAP_STAGE_CONTRACTS))
    def test_all_registered_profile_stage_contracts_are_valid(
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
                expected_stage="evaluation_reference",
            )

    def test_action_history_counts_are_independent_between_artifacts(self) -> None:
        source = _source_manifest()
        runtime = _db_manifest("runtime", "runtime_clean")
        evaluation = _db_manifest("evaluation", "evaluation_reference")

        assert source["tables"]["action_history"]["row_count"] == 48
        assert runtime["tables"]["action_history"]["row_count"] == 0
        assert evaluation["tables"]["action_history"]["row_count"] == 12

        mv3.validate_manifest_schema(source, expected_artifact_type="source_files")
        mv3.validate_manifest_schema(runtime, expected_artifact_type="db_bootstrap")
        mv3.validate_manifest_schema(evaluation, expected_artifact_type="db_bootstrap")

    def test_the_final_evaluation_stage_requires_fixture_metadata(self) -> None:
        manifest = _db_manifest("evaluation", "evaluation_reference")
        manifest["tables"]["action_history"].pop("fixture_type")

        with pytest.raises(mv3.ManifestSchemaError, match="action_history"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize("fixture_type", ["REFERENCE", "MOCK"])
    def test_fixture_metadata_is_limited_to_the_action_history(
        self, fixture_type: str
    ) -> None:
        """다른 table에는 `fixture_type`을 붙일 수 없다."""

        manifest = _db_manifest("runtime", "base_schema")
        manifest["tables"]["reference_table"] = {
            "columns": ["id"],
            "verification_policy": "immutable_content",
            "row_count": 1,
            "content_hash": DIGEST,
            "fixture_type": fixture_type,
        }

        with pytest.raises(mv3.ManifestSchemaError, match="fixture_type"):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    def test_the_retired_mock_fixture_type_is_not_accepted(self) -> None:
        """구 `MOCK` 표기는 최종 stage에서 쓰지 않는다."""

        manifest = _db_manifest("evaluation", "evaluation_reference")
        manifest["tables"]["action_history"]["fixture_type"] = "MOCK"

        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize(
        "profile,stage",
        [("runtime", "runtime_clean"), ("evaluation", "evaluation_reference")],
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
        [("runtime", "runtime_clean"), ("evaluation", "evaluation_reference")],
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

    @pytest.mark.parametrize("artifact", [_db_manifest("runtime", "base_schema")])
    def test_db_correction_version_cannot_be_none(self, artifact: dict) -> None:
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
        with pytest.raises(mv3.VerificationError, match="DB 옵션"):
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


def test_source_manifest_fixture_is_not_mutated_by_validation() -> None:
    manifest = _source_manifest()
    before = copy.deepcopy(manifest)

    mv3.validate_manifest_schema(manifest, expected_artifact_type="source_files")

    assert manifest == before


class TestCorrectedSurfaceIsGone:
    """`V5-CM-1.6`이 구 corrected surface를 제거했다(계획 §5.1 · §9.2).

    **되살리면 여기서 실패한다.** 삭제 Task가 회귀를 남기는 방법이다.
    """

    def test_corrected_artifact_type_is_not_registered(self) -> None:
        assert "corrected_files" not in mv3.ARTIFACT_TYPES
        assert "corrected-files" not in mv3.CLI_ARTIFACT_TYPES
        assert not hasattr(mv3, "CORRECTED_MANIFEST_PATH")
        assert not hasattr(mv3, "CORRECTED_TABLES")

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_corrected_base_stage_is_not_registered(self, profile: str) -> None:
        assert (profile, "corrected_base") not in mv3.BOOTSTRAP_STAGE_CONTRACTS
        with pytest.raises(mv3.ManifestMetadataError):
            mv3.resolve_bootstrap_manifest_path(profile, "corrected_base")

    def test_registered_stages_are_exactly_four(self) -> None:
        """`V5-CM-1.8`이 `evaluation_mock`을 `evaluation_reference`로 교체했다."""

        assert set(mv3.BOOTSTRAP_STAGE_CONTRACTS) == {
            ("runtime", "base_schema"),
            ("evaluation", "base_schema"),
            ("evaluation", "evaluation_reference"),
            ("runtime", "runtime_clean"),
        }
        # 구 stage는 active 등록부에서 사라지고 history 계보로만 남는다.
        assert ("evaluation", "evaluation_mock") not in mv3.BOOTSTRAP_STAGE_CONTRACTS
        assert ("evaluation", "evaluation_mock") in mv3.HISTORICAL_CONTRACTS

    def test_the_final_evaluation_stage_replaces_the_mock_one(self) -> None:
        """**역방향 회귀.**

        구 계약은 "`V5-CM-1.8`이 교체할 때까지 `evaluation_mock`이 남는다"였다.
        교체가 끝났으므로 그 전제를 뒤집는다 — 삭제하면 교체가 실제로 일어났다는
        사실을 아무도 지키지 않는다.

        최종 evaluation은 실제 12행이라 구 stage의 48행 MOCK 특례로는 표현할 수 없다.
        """

        contract = mv3.BOOTSTRAP_STAGE_CONTRACTS[("evaluation", "evaluation_reference")]
        assert contract.schema_stage == "reference_final"
        assert contract.applied_migrations == (mv3.FINAL_MIGRATION_ID,)
        assert contract.action_policy == "immutable_content"
        assert contract.action_rows == 12
        assert contract.action_fixture_type == "REFERENCE"

        manifest = _db_manifest("evaluation", "evaluation_reference")
        assert manifest["tables"]["action_history"]["row_count"] == 12
        mv3.validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile="evaluation",
            expected_stage="evaluation_reference",
        )

    def test_the_registered_final_migration_matches_the_v5_runner(self) -> None:
        """migration ID를 두 모듈이 각각 선언한다 — 갈리면 여기서 잡는다."""

        import apply_reference_extensions_v5 as v5

        assert mv3.FINAL_MIGRATION_ID == v5.MIGRATION_ID
        for profile, migrations in v5.PROFILE_MIGRATIONS.items():
            stage = {
                "runtime": "runtime_clean",
                "evaluation": "evaluation_reference",
            }[profile]
            contract = mv3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]
            assert contract.applied_migrations == tuple(migrations)


class TestHistoricalValidator:
    """**대체된 구 계보 전용 검증기**(`V5-CM-1.8` 구현리뷰 필수 1).

    generic validator에 epoch 인자를 열어 두면 어떤 artifact type이든 구 epoch으로
    통과시킬 수 있고, archive SHA-256이 유효한 64자리이기만 하면 계보가 변조돼도
    통과한다. 그래서 history 검증을 `db_bootstrap` + exact `(profile, stage)` 전용
    API로 분리했다.
    """

    @staticmethod
    def _registered(profile: str) -> dict:
        """**history 경로에서 읽는다.**

        `V5-CM-1.8`이 active를 final로 교체하면서 구 계보는
        `history/kosa_0813/manifests/`로 옮겨졌다. active에서 읽으면 파일이 없거나
        final manifest를 history로 오인한다.
        """

        stage = mv3.HISTORICAL_STAGES[profile]
        path = (
            mv3.BOOTSTRAP_ROOT
            / "history"
            / mv3.SUPERSEDED_DATASET_EPOCH
            / "manifests"
            / f"{profile}.{stage}.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_history_survives_the_active_registry_being_rebased(
        self, profile: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**active registry가 사라져도 과거 lineage는 바뀌지 않는다**(계획 §3.8).

        이 Task의 후속 구현이 active `evaluation_mock`을 제거하고 `runtime_clean`을
        final 값으로 갱신한다. history 검증이 `BOOTSTRAP_STAGE_CONTRACTS`를 읽으면
        그 순간 과거 manifest를 검증하지 못한다(구현리뷰 2차 필수 1).
        """

        # active registry를 비우고 final stage만 남긴 상태를 모의한다.
        monkeypatch.setattr(mv3, "BOOTSTRAP_STAGE_CONTRACTS", {})

        mv3.validate_historical_bootstrap_manifest(
            self._registered(profile),
            profile=profile,
            stage=mv3.HISTORICAL_STAGES[profile],
        )

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_pinned_contract_matches_the_real_history_manifest(
        self, profile: str
    ) -> None:
        """**계보의 개별 사실을 여기서 지킨다.**

        검증기 본문은 canonical payload SHA-256 하나로 전부를 덮는다 — epoch·archive
        SHA·envelope·table 수를 따로 비교하는 코드는 어떤 변이로도 독립적으로 실패하지
        않아 검증되지 않는 검사가 된다. 그래서 개별 사실은 상수와 실물 파일을 직접
        대조하는 이 회귀가 소유한다.
        """

        stage = mv3.HISTORICAL_STAGES[profile]
        contract = mv3.HISTORICAL_CONTRACTS[(profile, stage)]
        manifest = self._registered(profile)

        assert mv3.canonical_payload_sha256(manifest) == contract.payload_sha256
        assert manifest["dataset_epoch"] == mv3.SUPERSEDED_DATASET_EPOCH
        assert manifest["source_archive_sha256"] == mv3.SUPERSEDED_ARCHIVE_SHA256
        assert manifest["correction_version"] == mv3.SUPERSEDED_CORRECTION_VERSION
        assert manifest["artifact_type"] == "db_bootstrap"
        assert manifest["profile"] == profile
        assert manifest["bootstrap_stage"] == stage
        assert manifest["schema_stage"] == contract.schema_stage
        assert tuple(manifest["applied_migrations"]) == contract.applied_migrations
        assert tuple(manifest["applies_to"]) == contract.applies_to
        assert len(manifest["tables"]) == contract.table_count

    def test_the_superseded_lineage_is_not_the_final_one(self) -> None:
        """폐기 계보와 최종 계보가 실제로 다른 ZIP이다."""

        assert mv3.SUPERSEDED_ARCHIVE_SHA256 != mv3.FINAL_ARCHIVE_SHA256
        assert mv3.SUPERSEDED_DATASET_EPOCH != mv3.DATASET_EPOCH
        assert mv3.SUPERSEDED_CORRECTION_VERSION != FINAL_CORRECTION_VERSION

    def test_history_table_counts_are_the_superseded_inventory(self) -> None:
        """23/14는 폐기 epoch 값이다. 최종은 22/13이며 `V5-CM-3.1`이 봉인한다."""

        counts = {
            profile: mv3.HISTORICAL_CONTRACTS[
                (profile, mv3.HISTORICAL_STAGES[profile])
            ].table_count
            for profile in mv3.HISTORICAL_STAGES
        }

        assert counts == {"runtime": 23, "evaluation": 14}

    @pytest.mark.parametrize(
        ("label", "mutate"),
        [
            (
                "content_hash 한 글자",
                lambda m: m["tables"]["dim_parameter"].__setitem__(
                    "content_hash",
                    "b" + m["tables"]["dim_parameter"]["content_hash"][1:],
                ),
            ),
            ("table 삭제", lambda m: m["tables"].pop("dim_parameter")),
            (
                "table 추가",
                lambda m: m["tables"].__setitem__(
                    "extra_table", dict(m["tables"]["dim_parameter"])
                ),
            ),
            (
                "columns 변조",
                lambda m: m["tables"]["dim_parameter"].__setitem__(
                    "columns", ["arbitrary_column"]
                ),
            ),
            (
                "row_count 변조",
                lambda m: m["tables"]["dim_parameter"].__setitem__("row_count", 99999),
            ),
            ("schema_stage 변조", lambda m: m.__setitem__("schema_stage", "other")),
            (
                "applies_to 확장",
                lambda m: m.__setitem__(
                    "applies_to", [*m["applies_to"], "kosa_text2sql"]
                ),
            ),
            (
                "applied_migrations 축소",
                lambda m: m.__setitem__(
                    "applied_migrations", m["applied_migrations"][:1]
                ),
            ),
        ],
    )
    def test_any_payload_mutation_is_refused(self, label: str, mutate) -> None:
        """**archive 계보만 맞으면 내용은 바뀔 수 있었다**(구현리뷰 2차 필수 2).

        23/14 불변 inventory와 content identity를 바꾼 manifest를 `supersedes` 입력으로
        쓸 수 있으면 NFR-06 계보 재현성이 무너진다.
        """

        manifest = self._registered("runtime")
        mutate(manifest)

        with pytest.raises(mv3.ManifestMetadataError):
            mv3.validate_historical_bootstrap_manifest(
                manifest, profile="runtime", stage="runtime_clean"
            )

    @pytest.mark.parametrize("profile", ["runtime", "evaluation"])
    def test_the_registered_history_manifest_passes(self, profile: str) -> None:
        mv3.validate_historical_bootstrap_manifest(
            self._registered(profile),
            profile=profile,
            stage=mv3.HISTORICAL_STAGES[profile],
        )

    def test_generic_validator_exposes_no_epoch_override(self) -> None:
        """active 검증기로는 구 epoch을 선택할 수 없다."""

        assert (
            "expected_dataset_epoch"
            not in mv3.validate_manifest_schema.__code__.co_varnames
        )

    @pytest.mark.parametrize("digit", ["b", "0", "f"])
    def test_a_tampered_archive_lineage_is_refused(self, digit: str) -> None:
        """**한 글자만 바꿔도 거부한다.**

        epoch 이름만 맞추면 임의의 유효한 64자리 값이 "대체된 계보"로 통과했다.
        계보 재현성(NFR-06)이 깨지는 지점이다.
        """

        manifest = self._registered("runtime")
        manifest["source_archive_sha256"] = digit * 64

        with pytest.raises(mv3.ManifestMetadataError):
            mv3.validate_historical_bootstrap_manifest(
                manifest, profile="runtime", stage="runtime_clean"
            )

    def test_a_tampered_correction_version_is_refused(self) -> None:
        manifest = self._registered("runtime")
        manifest["correction_version"] = "final-v1"

        with pytest.raises(mv3.ManifestMetadataError):
            mv3.validate_historical_bootstrap_manifest(
                manifest, profile="runtime", stage="runtime_clean"
            )

    def test_a_final_manifest_cannot_be_laundered_as_superseded(self) -> None:
        """최종 manifest를 "대체 대상"으로 세탁할 수 없다."""

        manifest = self._registered("runtime")
        manifest["dataset_epoch"] = mv3.DATASET_EPOCH
        manifest["source_archive_sha256"] = mv3.FINAL_ARCHIVE_SHA256

        with pytest.raises(mv3.ManifestMetadataError):
            mv3.validate_historical_bootstrap_manifest(
                manifest, profile="runtime", stage="runtime_clean"
            )

    @pytest.mark.parametrize(
        ("profile", "stage"),
        [
            ("runtime", "evaluation_mock"),
            ("evaluation", "runtime_clean"),
            ("runtime", "base_schema"),
            ("neo4j", "graph"),
        ],
    )
    def test_only_exact_historical_pairs_are_accepted(
        self, profile: str, stage: str
    ) -> None:
        with pytest.raises(mv3.ManifestMetadataError):
            mv3.validate_historical_bootstrap_manifest(
                self._registered("runtime"), profile=profile, stage=stage
            )

    @pytest.mark.parametrize(
        "name", ["source-data-manifest.json", "corrected-data-manifest.json"]
    )
    def test_old_source_and_synthetic_artifacts_are_not_accepted(
        self, name: str
    ) -> None:
        """`db_bootstrap` 하나만 받는다.

        구 epoch을 공유한다는 이유로 다른 artifact type이 history 경로로 들어오면
        안 된다. key 집합에서 먼저 걸리므로 `VerificationError`로 받는다.
        """

        payload = json.loads(
            (mv3.BOOTSTRAP_ROOT / "history" / "kosa_0813" / name).read_text(
                encoding="utf-8"
            )
        )

        with pytest.raises(mv3.VerificationError):
            mv3.validate_historical_bootstrap_manifest(
                payload, profile="runtime", stage="runtime_clean"
            )


class TestRelativePathContract:
    """저장소 상대 경로 allowlist(`V5-CM-1.8` 구현리뷰 필수 2).

    `PurePosixPath(...).parts`만 보는 blocklist는 POSIX `../`만 잡았다.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "..\\secret.json",  # Windows traversal
            "postgresql://user:pass@host/db",  # credential DSN
            "file:///etc/passwd",
            "../secret.json",
            "a/../b",
            "/absolute.json",
            "C:\\x.json",
            "//server/share/x.json",
            "\\\\server\\share\\x.json",
            "",
            "  ",
            "a b/c.json",
        ],
    )
    def test_unsafe_values_are_refused(self, value: str) -> None:
        with pytest.raises(mv3.ManifestSchemaError):
            mv3._require_relative_path(value, context="intake_artifact")

    @pytest.mark.parametrize(
        "value",
        [
            "infra/bootstrap/final-zip-intake.json",
            "infra/bootstrap/history/kosa_0813/",
            "a.json",
        ],
    )
    def test_canonical_relative_paths_pass(self, value: str) -> None:
        assert mv3._require_relative_path(value, context="intake_artifact") == value


class TestFinalEpochCanonicalValues:
    """형식만 보면 다른 패키지가 최종으로 등록된다(구현리뷰 필수 2)."""

    @staticmethod
    def _epoch() -> dict:
        return json.loads(mv3.DATASET_EPOCH_PATH.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        ("mutation", "value"),
        [
            (("archive", "filename"), "other.zip"),
            (("intake_artifact",), "infra/bootstrap/other-intake.json"),
            (("received_date",), "2026-01-01"),
            (("inventory_scope",), "non_directory_zip_members"),
        ],
    )
    def test_a_non_canonical_field_is_refused(
        self, tmp_path: Path, mutation: tuple, value: str
    ) -> None:
        epoch = self._epoch()
        target = epoch
        for key in mutation[:-1]:
            target = target[key]
        target[mutation[-1]] = value
        path = tmp_path / "dataset-epoch.json"
        path.write_text(json.dumps(epoch), encoding="utf-8")

        with pytest.raises(mv3.ManifestMetadataError):
            mv3.load_dataset_epoch(path)

    def test_a_credential_in_the_epoch_is_refused(self, tmp_path: Path) -> None:
        """epoch artifact는 manifest validator를 거치지 않는다 — 직접 훑는다."""

        epoch = self._epoch()
        epoch["archive"]["filename"] = "postgresql://user:pw@host/db"
        path = tmp_path / "dataset-epoch.json"
        path.write_text(json.dumps(epoch), encoding="utf-8")

        with pytest.raises(mv3.VerificationError):
            mv3.load_dataset_epoch(path)


class TestActionFixtureKeyContract:
    """`fixture_type` **key 존재 자체가 계약이다**(`V5-CM-1.8` 구현리뷰 4차 필수 1).

    `.get()`으로 비교하면 JSON `null`과 key 부재가 같아져 extra-forbid exact schema가
    후퇴한다.
    """

    @pytest.mark.parametrize(
        ("profile", "stage"), sorted(mv3.BOOTSTRAP_STAGE_CONTRACTS)
    )
    def test_an_explicit_null_fixture_type_is_refused(
        self, profile: str, stage: str
    ) -> None:
        manifest = _db_manifest(profile, stage)
        manifest["tables"]["action_history"]["fixture_type"] = None

        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize(
        ("profile", "stage"),
        [
            (profile, stage)
            for (profile, stage), contract in sorted(
                mv3.BOOTSTRAP_STAGE_CONTRACTS.items()
            )
            if contract.action_fixture_type is None
        ],
    )
    def test_a_fixture_type_key_is_refused_where_the_contract_has_none(
        self, profile: str, stage: str
    ) -> None:
        manifest = _db_manifest(profile, stage)
        manifest["tables"]["action_history"]["fixture_type"] = "REFERENCE"

        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize("value", [None, "MOCK", "", "reference"])
    def test_the_final_evaluation_requires_the_exact_fixture_type(
        self, value: str | None
    ) -> None:
        manifest = _db_manifest("evaluation", "evaluation_reference")
        manifest["tables"]["action_history"]["fixture_type"] = value

        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    def test_a_missing_fixture_type_is_refused_where_required(self) -> None:
        manifest = _db_manifest("evaluation", "evaluation_reference")
        manifest["tables"]["action_history"].pop("fixture_type")

        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                manifest, expected_artifact_type="db_bootstrap"
            )

    @pytest.mark.parametrize(
        ("profile", "stage"), sorted(mv3.BOOTSTRAP_STAGE_CONTRACTS)
    )
    def test_the_canonical_shape_passes(self, profile: str, stage: str) -> None:
        mv3.validate_manifest_schema(
            _db_manifest(profile, stage), expected_artifact_type="db_bootstrap"
        )


class TestContentColumnsContract:
    """`content_columns`는 hash 대상 **부분집합**이다(`V5-CM-1.8` 구현리뷰 16차 필수 1).

    `document.created_at`처럼 두 DB가 독립 적재해 항상 다른 컬럼을 hash에서 빼기 위한
    것이다. `schema_only`로 두면 3행이 0행이 돼도 통과한다 — 검증을 없애는 것이지
    해결이 아니다.
    """

    @staticmethod
    def _entry(**overrides):
        manifest = _db_manifest("runtime", "runtime_clean")
        entry = {
            "columns": ["a", "b", "c"],
            "verification_policy": "immutable_content",
            "row_count": 1,
            "content_hash": DIGEST,
            "content_columns": ["a", "b"],
        }
        entry.update(overrides)
        manifest["tables"]["reference_table"] = entry
        return manifest

    def test_a_proper_subset_is_accepted(self) -> None:
        mv3.validate_manifest_schema(
            self._entry(), expected_artifact_type="db_bootstrap"
        )

    @pytest.mark.parametrize(
        "content_columns",
        [
            ["a", "zzz"],  # columns에 없는 이름
            ["zzz"],
        ],
    )
    def test_a_non_subset_is_refused(self, content_columns: list[str]) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="부분집합"):
            mv3.validate_manifest_schema(
                self._entry(content_columns=content_columns),
                expected_artifact_type="db_bootstrap",
            )

    def test_a_reordered_subset_is_refused(self) -> None:
        """순서가 다르면 같은 내용에서 다른 hash가 나온다."""

        with pytest.raises(mv3.ManifestSchemaError, match="순서"):
            mv3.validate_manifest_schema(
                self._entry(content_columns=["b", "a"]),
                expected_artifact_type="db_bootstrap",
            )

    def test_a_full_copy_is_refused(self) -> None:
        """전체와 같으면 이 field가 있을 이유가 없다."""

        with pytest.raises(mv3.ManifestSchemaError, match="같습니다"):
            mv3.validate_manifest_schema(
                self._entry(content_columns=["a", "b", "c"]),
                expected_artifact_type="db_bootstrap",
            )

    def test_it_is_refused_on_non_immutable_policies(self) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="immutable_content"):
            mv3.validate_manifest_schema(
                self._entry(
                    verification_policy="bootstrap_empty",
                    row_count=0,
                    content_hash=EMPTY_DIGEST,
                ),
                expected_artifact_type="db_bootstrap",
            )

    @pytest.mark.parametrize("content_columns", [[], ["a", "a"], ["A"], [1]])
    def test_a_malformed_list_is_refused(self, content_columns: list) -> None:
        with pytest.raises(mv3.ManifestSchemaError):
            mv3.validate_manifest_schema(
                self._entry(content_columns=content_columns),
                expected_artifact_type="db_bootstrap",
            )
