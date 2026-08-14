"""V4-CM-1.2 corrected dataset pipeline 계약을 DB 없이 검증한다."""

from __future__ import annotations

import csv
import errno
import hashlib
import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_corrected_dataset as corrected  # noqa: E402
import manifest_v3 as mv3  # noqa: E402


def _csv_payload(table: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    columns = list(mv3.SOURCE_EXPECTED_COLUMNS[table])
    writer.writerow(columns)
    row_count = 48 if table == "action_history" else 1
    for index in range(row_count):
        writer.writerow([f"{table}-{index + 1}", *([""] * (len(columns) - 1))])
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _source_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    archive_path = tmp_path / "kosa_0813.zip"
    files = {
        file_id: _csv_payload(table)
        for table, file_id in mv3.SOURCE_TABLE_FILES.items()
    }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(files.items()):
            member = zipfile.ZipInfo(name)
            member.date_time = (2026, 8, 13, 0, 0, 0)
            member.compress_type = zipfile.ZIP_STORED
            archive.writestr(member, payload)

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
        "dataset_epoch": mv3.DATASET_EPOCH,
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

    source_manifest = mv3.build_source_manifest(archive_path, epoch_path=epoch_path)
    source_manifest_path = tmp_path / "source-data-manifest.json"
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    return archive_path, epoch_path, source_manifest_path


def _execute(
    tmp_path: Path,
    *,
    revision: str = corrected.GENERATOR_REVISION,
    stages: tuple[corrected.StageSpec, ...] = (),
    confirm: bool = False,
    check: bool = False,
) -> tuple[int, Path, Path]:
    archive_path, epoch_path, source_manifest_path = _source_bundle(tmp_path)
    output_root = tmp_path / "allowed" / "corrected"
    result = corrected.execute(
        archive_path=archive_path,
        output_root=output_root,
        allowed_root=tmp_path / "allowed",
        epoch_path=epoch_path,
        source_manifest_path=source_manifest_path,
        stages=stages,
        generator_revision=revision,
        confirm=confirm,
        check=check,
    )
    return result, output_root, archive_path


def _active(output_root: Path) -> dict:
    return json.loads((output_root / "v1" / "active.json").read_text())


def _build_path(output_root: Path) -> Path:
    active = _active(output_root)
    return output_root / "v1" / "builds" / active["build_id"]


class TestBuildLifecycle:
    def test_first_build_is_deterministic_and_preserves_source(
        self, tmp_path: Path
    ) -> None:
        result, output_root, archive_path = _execute(tmp_path)
        source_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        build_path = _build_path(output_root)

        assert result == mv3.EXIT_OK
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == source_hash
        assert {path.name for path in (build_path / "postgres").iterdir()} == {
            f"{table}.csv" for table in mv3.SOURCE_TABLE_FILES
        }
        for csv_path in (build_path / "postgres").iterdir():
            payload = csv_path.read_bytes()
            assert not payload.startswith(b"\xef\xbb\xbf")
            assert b"\r" not in payload
            assert payload.endswith(b"\n")

        receipt = json.loads((build_path / "build-receipt.json").read_text())
        report = json.loads((build_path / "correction-report.json").read_text())
        assert receipt["registration_status"] == "UNREGISTERED"
        assert receipt["applied_stages"] == []
        assert set(receipt["tables"]) == set(mv3.SOURCE_TABLE_FILES)
        assert all(entry["cells_changed"] == 0 for entry in report["tables"].values())

    def test_repeat_and_check_are_noop(self, tmp_path: Path) -> None:
        _, output_root, _ = _execute(tmp_path)
        before = {
            path.relative_to(output_root): path.read_bytes()
            for path in output_root.rglob("*")
            if path.is_file()
        }

        repeated, _, _ = _execute(tmp_path)
        checked, _, _ = _execute(tmp_path, check=True)
        after = {
            path.relative_to(output_root): path.read_bytes()
            for path in output_root.rglob("*")
            if path.is_file()
        }

        assert repeated == mv3.EXIT_OK
        assert checked == mv3.EXIT_OK
        assert after == before

    def test_revision_change_requires_confirm_then_activates(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, output_root, _ = _execute(tmp_path)
        original = _active(output_root)

        declined, _, _ = _execute(tmp_path, revision="corrected-builder-v2")
        output = capsys.readouterr().out
        assert declined == mv3.EXIT_CONFIRM_REQUIRED
        assert _active(output_root) == original
        assert f"active build_id: {original['build_id']}" in output
        assert "candidate build_id:" in output
        assert "변경 identity: generator_revision" in output
        assert "변경 table: 없음" in output

        accepted, _, _ = _execute(
            tmp_path,
            revision="corrected-builder-v2",
            confirm=True,
        )
        assert accepted == mv3.EXIT_OK
        assert _active(output_root)["build_id"] != original["build_id"]

    def test_check_detects_current_identity_mismatch(self, tmp_path: Path) -> None:
        _execute(tmp_path)

        result, _, _ = _execute(
            tmp_path,
            revision="corrected-builder-v2",
            check=True,
        )

        assert result == mv3.EXIT_MISMATCH


class TestSourceAndPathGuards:
    @pytest.mark.parametrize(
        "field", ["file_id", "columns", "row_count", "content_hash"]
    )
    def test_source_manifest_table_mismatch_fails_before_write(
        self, tmp_path: Path, field: str
    ) -> None:
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)
        manifest = json.loads(manifest_path.read_text())
        entry = manifest["tables"]["fdc_trace"]
        if field == "columns":
            entry[field] = list(reversed(entry[field]))
        elif field == "row_count":
            entry[field] += 1
        else:
            entry[field] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        output_root = tmp_path / "allowed" / "corrected"

        with pytest.raises(mv3.VerificationError):
            corrected.execute(
                archive_path=archive_path,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )

        assert not output_root.exists()

    @pytest.mark.parametrize("mutation", ["missing", "extra"])
    def test_source_manifest_table_set_is_exact(
        self, tmp_path: Path, mutation: str
    ) -> None:
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)
        manifest = json.loads(manifest_path.read_text())
        if mutation == "missing":
            manifest["tables"].pop("metrology")
        else:
            manifest["tables"]["extra"] = manifest["tables"]["metrology"]
        manifest_path.write_text(json.dumps(manifest))
        output_root = tmp_path / "allowed" / "corrected"

        with pytest.raises(mv3.ManifestSchemaError):
            corrected.execute(
                archive_path=archive_path,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )

        assert not output_root.exists()

    def test_output_outside_allowed_root_is_rejected(self, tmp_path: Path) -> None:
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)

        with pytest.raises(mv3.VerificationError, match="allowed_root 밖"):
            corrected.execute(
                archive_path=archive_path,
                output_root=tmp_path / "outside",
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )

    def test_archive_inside_output_is_rejected(self, tmp_path: Path) -> None:
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)
        output_root = tmp_path / "allowed" / "corrected"
        output_root.mkdir(parents=True)
        nested_archive = output_root / archive_path.name
        nested_archive.write_bytes(archive_path.read_bytes())

        with pytest.raises(mv3.VerificationError, match="corrected output"):
            corrected.execute(
                archive_path=nested_archive,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )


class TestStageContract:
    def test_stage_can_read_existing_table_and_add_declared_table(self) -> None:
        source = {
            "source": corrected.TableData(("id",), ({"id": "1"},)),
        }

        def add_lookup(dataset: corrected.Dataset) -> corrected.TablePatch:
            assert dataset["source"].rows[0]["id"] == "1"
            return {
                "lookup": corrected.TableData(
                    ("id", "name"),
                    ({"id": "L1", "name": "lookup"},),
                )
            }

        stage = corrected.StageSpec(
            "add.lookup",
            "1",
            frozenset({"source"}),
            frozenset({"lookup"}),
            add_lookup,
        )

        dataset, touched = corrected.run_stages(source, (stage,))

        assert set(dataset) == {"source", "lookup"}
        assert touched["lookup"] == ["add.lookup"]

    def test_undeclared_patch_is_rejected(self) -> None:
        source = {"source": corrected.TableData(("id",), ({"id": "1"},))}

        def bad_patch(_: corrected.Dataset) -> corrected.TablePatch:
            return {"other": corrected.TableData(("id",), ({"id": "2"},))}

        stage = corrected.StageSpec(
            "bad.patch",
            "1",
            frozenset({"source"}),
            frozenset({"source"}),
            bad_patch,
        )

        with pytest.raises(mv3.ManifestSchemaError, match="writes 밖"):
            corrected.run_stages(source, (stage,))

    def test_mutating_restricted_copy_without_patch_is_rejected(self) -> None:
        source = {"source": corrected.TableData(("id",), ({"id": "1"},))}

        def mutate_local(dataset: corrected.Dataset) -> corrected.TablePatch:
            dataset["source"].rows[0]["id"] = "changed"
            return {"output": corrected.TableData(("id",), ({"id": "2"},))}

        stage = corrected.StageSpec(
            "copy.guard",
            "1",
            frozenset({"source"}),
            frozenset({"output"}),
            mutate_local,
        )

        with pytest.raises(mv3.ManifestSchemaError, match="patch로 반환"):
            corrected.run_stages(source, (stage,))
        assert source["source"].rows[0]["id"] == "1"

    def test_duplicate_stage_id_is_rejected(self) -> None:
        stage = corrected.StageSpec(
            "duplicate",
            "1",
            frozenset(),
            frozenset({"output"}),
            lambda _: {"output": corrected.TableData(("id",), ({"id": "1"},))},
        )

        with pytest.raises(mv3.ManifestSchemaError, match="중복"):
            corrected.run_stages({}, (stage, stage))

    def test_stage_order_and_revision_change_build_id(self) -> None:
        first = corrected.StageSpec(
            "first",
            "1",
            frozenset(),
            frozenset({"a"}),
            lambda _: {"a": corrected.TableData(("id",), ({"id": "1"},))},
        )
        second = corrected.StageSpec(
            "second",
            "1",
            frozenset(),
            frozenset({"b"}),
            lambda _: {"b": corrected.TableData(("id",), ({"id": "2"},))},
        )
        kwargs = {
            "source_archive_sha256": "a" * 64,
            "generator_revision": "v1",
            "generator_sha256": "b" * 64,
        }

        ordered = corrected.compute_build_id(stages=(first, second), **kwargs)
        reversed_order = corrected.compute_build_id(stages=(second, first), **kwargs)
        revised = corrected.compute_build_id(
            stages=(first, second),
            **{**kwargs, "generator_revision": "v2"},
        )

        assert len({ordered, reversed_order, revised}) == 3

    def test_stages_execute_in_registry_order(self) -> None:
        source = {"source": corrected.TableData(("id",), ({"id": "1"},))}

        def first(_: corrected.Dataset) -> corrected.TablePatch:
            return {"chain": corrected.TableData(("value",), ({"value": "A"},))}

        def second(dataset: corrected.Dataset) -> corrected.TablePatch:
            value = dataset["chain"].rows[0]["value"]
            return {
                "chain": corrected.TableData(
                    ("value",),
                    ({"value": f"{value}B"},),
                )
            }

        stages = (
            corrected.StageSpec(
                "order.first",
                "1",
                frozenset(),
                frozenset({"chain"}),
                first,
            ),
            corrected.StageSpec(
                "order.second",
                "1",
                frozenset({"chain"}),
                frozenset({"chain"}),
                second,
            ),
        )

        dataset, touched = corrected.run_stages(source, stages)

        assert dataset["chain"].rows == ({"value": "AB"},)
        assert touched["chain"] == ["order.first", "order.second"]

    def test_generator_component_change_changes_generator_hash(self) -> None:
        before = corrected._generator_sha256(
            [{"logical_id": "builder.py", "sha256": "a" * 64}]
        )
        after = corrected._generator_sha256(
            [{"logical_id": "builder.py", "sha256": "b" * 64}]
        )

        assert before != after


class TestActiveAndBuildIntegrity:
    def test_windows_lock_backend_initializes_byte_and_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int | str] = []

        class FakeMsvcrt:
            LK_NBLCK = 1
            LK_UNLCK = 2
            attempts = 0

            @classmethod
            def locking(cls, _fd: int, mode: int, size: int) -> None:
                assert size == 1
                calls.append(mode)
                if mode == cls.LK_NBLCK and cls.attempts == 0:
                    cls.attempts += 1
                    raise OSError(errno.EACCES, "locked")

        monkeypatch.setattr(corrected, "_IS_WINDOWS", True)
        monkeypatch.setattr(corrected, "_msvcrt", FakeMsvcrt)
        monkeypatch.setattr(
            corrected.time, "sleep", lambda _seconds: calls.append("sleep")
        )

        lock_path = tmp_path / ".active.lock"
        with corrected._exclusive_lock(lock_path):
            assert lock_path.read_bytes() == b"\0"

        assert calls == [
            FakeMsvcrt.LK_NBLCK,
            "sleep",
            FakeMsvcrt.LK_NBLCK,
            FakeMsvcrt.LK_UNLCK,
        ]

    def test_active_extra_key_is_rejected(self, tmp_path: Path) -> None:
        _, output_root, _ = _execute(tmp_path)
        active_path = output_root / "v1" / "active.json"
        active = json.loads(active_path.read_text())
        active["extra"] = True
        active_path.write_text(json.dumps(active))

        with pytest.raises(corrected.ActivePointerError):
            _execute(tmp_path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("build_id", "../"),
            ("build_id", "/tmp/not-a-build"),
            ("build_id", "A" * 64),
            ("receipt_sha256", "short"),
        ],
    )
    def test_unsafe_active_value_is_rejected_before_path_access(
        self, tmp_path: Path, field: str, value: str
    ) -> None:
        _, output_root, _ = _execute(tmp_path)
        active_path = output_root / "v1" / "active.json"
        active = json.loads(active_path.read_text())
        active[field] = value
        active_path.write_text(json.dumps(active))

        with pytest.raises(corrected.ActivePointerError):
            _execute(tmp_path)

    def test_symlink_build_is_rejected(self, tmp_path: Path) -> None:
        _, output_root, _ = _execute(tmp_path)
        build_path = _build_path(output_root)
        moved = output_root / "real-build"
        build_path.rename(moved)
        build_path.symlink_to(moved, target_is_directory=True)

        with pytest.raises(corrected.ActivePointerError, match="symlink"):
            _execute(tmp_path)

    def test_missing_build_and_receipt_hash_mismatch_are_rejected(
        self, tmp_path: Path
    ) -> None:
        _, output_root, _ = _execute(tmp_path)
        active_path = output_root / "v1" / "active.json"
        active = json.loads(active_path.read_text())
        active["receipt_sha256"] = "0" * 64
        active_path.write_text(json.dumps(active))

        with pytest.raises(corrected.ActivePointerError, match="receipt hash"):
            _execute(tmp_path)

        active_path.write_text(json.dumps(_active_payload_from_build(output_root)))
        build_path = _build_path(output_root)
        build_path.rename(output_root / "missing-build")

        with pytest.raises(corrected.ActivePointerError, match="없거나"):
            _execute(tmp_path)

    def test_tampered_csv_is_rejected(self, tmp_path: Path) -> None:
        _, output_root, _ = _execute(tmp_path)
        csv_path = _build_path(output_root) / "postgres" / "fdc_trace.csv"
        csv_path.write_bytes(csv_path.read_bytes().replace(b"\n", b"\r\n"))

        with pytest.raises(mv3.ManifestSchemaError, match="쓰기 형식"):
            _execute(tmp_path)

    def test_existing_build_collision_with_different_contents_is_rejected(
        self, tmp_path: Path
    ) -> None:
        _, output_root, _ = _execute(tmp_path)
        build_path = _build_path(output_root)
        active_path = output_root / "v1" / "active.json"
        active_path.unlink()
        report_path = build_path / "correction-report.json"
        report = json.loads(report_path.read_text())
        report["tables"]["fdc_trace"]["cells_changed"] = 1
        report_path.write_text(json.dumps(report))

        with pytest.raises(mv3.ManifestSchemaError, match="report 내용"):
            _execute(tmp_path)

    def test_parallel_same_build_converges_on_one_active_pointer(
        self, tmp_path: Path
    ) -> None:
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)
        output_root = tmp_path / "allowed" / "corrected"

        def run_once() -> int:
            return corrected.execute(
                archive_path=archive_path,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run_once(), range(2)))

        assert results == [mv3.EXIT_OK, mv3.EXIT_OK]
        active = _active(output_root)
        assert len(list((output_root / "v1" / "builds").iterdir())) == 1
        assert (output_root / "v1" / "builds" / active["build_id"]).is_dir()

    def test_parallel_different_builds_do_not_overwrite_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, output_root, _ = _execute(tmp_path)
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)
        barrier = Barrier(2)
        original_activate = corrected._activate_build

        def synchronized_activate(**kwargs: object) -> None:
            barrier.wait(timeout=5)
            original_activate(**kwargs)

        monkeypatch.setattr(corrected, "_activate_build", synchronized_activate)

        def run_revision(revision: str) -> int | Exception:
            try:
                return corrected.execute(
                    archive_path=archive_path,
                    output_root=output_root,
                    allowed_root=tmp_path / "allowed",
                    epoch_path=epoch_path,
                    source_manifest_path=manifest_path,
                    generator_revision=revision,
                    confirm=True,
                )
            except Exception as exc:  # noqa: BLE001 - 경쟁 결과 자체를 검증한다.
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run_revision, ("revision-a", "revision-b")))

        assert sum(result == mv3.EXIT_OK for result in results) == 1
        assert (
            sum(isinstance(result, mv3.ArtifactMismatchError) for result in results)
            == 1
        )
        active = _active(output_root)
        assert active["build_id"] in {
            path.name for path in (output_root / "v1" / "builds").iterdir()
        }

    def test_staging_cleanup_rejects_root_and_symlink(self, tmp_path: Path) -> None:
        context = corrected.build_context(
            output_root=tmp_path / "allowed" / "corrected",
            allowed_root=tmp_path / "allowed",
        )
        context.staging_root.mkdir(parents=True)

        with pytest.raises(mv3.VerificationError):
            corrected._safe_remove_staging(context.staging_root, context)

        target = tmp_path / "target"
        target.mkdir()
        link = context.staging_root / ("a" * 32)
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(mv3.VerificationError):
            corrected._safe_remove_staging(link, context)
        assert target.is_dir()

    def test_cleanup_failure_does_not_mask_original_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        archive_path, epoch_path, manifest_path = _source_bundle(tmp_path)
        output_root = tmp_path / "allowed" / "corrected"

        def fail_write(*_: object, **__: object) -> None:
            raise mv3.ManifestSchemaError("original failure")

        def fail_cleanup(*_: object, **__: object) -> None:
            raise mv3.VerificationError("cleanup failure")

        monkeypatch.setattr(corrected, "_write_staging", fail_write)
        monkeypatch.setattr(corrected, "_safe_remove_staging", fail_cleanup)

        with pytest.raises(mv3.ManifestSchemaError, match="original failure"):
            corrected.execute(
                archive_path=archive_path,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )
        assert "원래 오류를 유지" in capsys.readouterr().err


class TestReportAndManifestBoundary:
    def test_new_table_report_and_manifest_candidate_are_explicit(self) -> None:
        source = {"source": corrected.TableData(("id",), ({"id": "1"},))}
        corrected_data = {
            **source,
            "lookup": corrected.TableData(("id", "name"), ({"id": "L1", "name": "x"},)),
        }

        report = corrected.build_correction_report(
            source,
            corrected_data,
            touched={"source": [], "lookup": ["add.lookup"]},
            build_id="a" * 64,
        )
        entries = corrected.build_corrected_table_entries(corrected_data)

        assert report["tables"]["lookup"] == {
            "row_count_before": None,
            "row_count_after": 1,
            "rows_added": 1,
            "rows_removed": 0,
            "cells_changed": 0,
            "columns_before": None,
            "columns_after": ["id", "name"],
            "stage_ids": ["add.lookup"],
        }
        assert entries["lookup"]["file_id"] == "corrected/v1/postgres/lookup.csv"


def _active_payload_from_build(output_root: Path) -> dict:
    active = _active(output_root)
    build_path = output_root / "v1" / "builds" / active["build_id"]
    receipt_hash = hashlib.sha256(
        (build_path / "build-receipt.json").read_bytes()
    ).hexdigest()
    return {**active, "receipt_sha256": receipt_hash}
