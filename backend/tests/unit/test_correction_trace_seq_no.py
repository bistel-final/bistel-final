"""V4-CM-1.3 Trace seq_no correction stage 계약을 검증한다."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_corrected_dataset as corrected  # noqa: E402
import manifest_v3 as mv3  # noqa: E402
from corrections import (  # noqa: E402
    dim_parameter_seed,
    summary_alarm_time,
    trace_seq_no,
)


def _trace_table(
    *,
    group_ids: tuple[str, ...] = ("LH-TEST-1",),
    global_groups: frozenset[str] = frozenset(),
) -> corrected.TableData:
    rows = []
    for group_index, lot_hist_id in enumerate(group_ids):
        for step in (1, 2):
            start = 3 if step == 2 and lot_hist_id in global_groups else 0
            for point in range(3):
                second = group_index * 10 + (step - 1) * 3 + point
                rows.append(
                    {
                        "lot_hist_id": lot_hist_id,
                        "parameter_id": "PH_DOSE",
                        "seq_no": str(start + point),
                        "recipe_step_no": str(step),
                        "step_seq": str(step),
                        "measured_at": f"2026-08-01 00:00:{second:02d}",
                        "value": str(10 + point),
                    }
                )
    return corrected.TableData(mv3.SOURCE_EXPECTED_COLUMNS["fdc_trace"], tuple(rows))


def _alarm_table(*, step: str = "1", seq_no: str = "0") -> corrected.TableData:
    row = {column: "" for column in mv3.SOURCE_EXPECTED_COLUMNS["trace_alarm_history"]}
    row.update(
        {
            "alarm_id": "TAL-TEST-1",
            "step_no": step,
            "step_seq": step,
            "seq_no": seq_no,
            "alarm_type": "OOS",
        }
    )
    return corrected.TableData(
        mv3.SOURCE_EXPECTED_COLUMNS["trace_alarm_history"], (row,)
    )


def _dataset(
    *,
    trace: corrected.TableData | None = None,
    alarm: corrected.TableData | None = None,
) -> dict[str, corrected.TableData]:
    return {
        "fdc_trace": trace or _trace_table(),
        "trace_alarm_history": alarm or _alarm_table(),
    }


def _csv_payload(columns: tuple[str, ...], rows: tuple[dict[str, str], ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(columns), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _pipeline_bundle(
    tmp_path: Path,
    *,
    alarm: corrected.TableData | None = None,
    summary_lot: str = "LOT001",
) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    template = _trace_table()
    trace = replace(
        template,
        rows=tuple(
            {**row, "parameter_id": parameter_id}
            for parameter_id in dim_parameter_seed.PARAMETER_ORDER
            for row in template.rows
        ),
    )
    alarm = alarm or _alarm_table()
    payloads: dict[str, bytes] = {}
    for table, file_id in mv3.SOURCE_TABLE_FILES.items():
        columns = mv3.SOURCE_EXPECTED_COLUMNS[table]
        if table == "fdc_trace":
            rows = trace.rows
        elif table == "trace_alarm_history":
            rows = alarm.rows
        elif table == "lot_history":
            row = {column: "" for column in columns}
            row.update(
                {
                    "lot_hist_id": "LH-TEST-1",
                    "lot_id": "LOT001",
                    "wafer_no": "1",
                    "area_id": "photo",
                    "equipment_id": "EQP01",
                    "chamber_id": "EQP01-PM1",
                    "track_in_at": "2026-08-01 00:00:00",
                }
            )
            rows = (row,)
        elif table == "summary_alarm_history":
            row = {column: "" for column in columns}
            row.update(
                {
                    "alarm_id": "SAL-TEST-1",
                    "area": "photo",
                    "equipment": "EQP01",
                    "chamber": "EQP01-PM1",
                    "lot": summary_lot,
                    "wafer": "1",
                    "alarm_type": "OOC",
                }
            )
            rows = (row,)
        else:
            row_count = 48 if table == "action_history" else 1
            rows = tuple({column: "" for column in columns} for _ in range(row_count))
        payloads[file_id] = _csv_payload(columns, rows)

    archive_path = tmp_path / "kosa_0813.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for file_id, payload in sorted(payloads.items()):
            member = zipfile.ZipInfo(file_id)
            member.date_time = (2026, 8, 13, 0, 0, 0)
            member.compress_type = zipfile.ZIP_STORED
            archive.writestr(member, payload)

    inventory = [
        {
            "path": file_id,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for file_id, payload in sorted(payloads.items())
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


class TestTraceSeqCorrection:
    def test_raw_table_is_converted_without_mutating_input(self) -> None:
        source = _trace_table()
        patch = trace_seq_no.apply(_dataset(trace=source))
        result = patch["fdc_trace"]

        assert [row["seq_no"] for row in source.rows] == ["0", "1", "2"] * 2
        assert [row["seq_no"] for row in result.rows] == [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
        ]
        assert result.columns == source.columns
        assert len(result.rows) == len(source.rows)
        for before, after in zip(source.rows, result.rows, strict=True):
            assert {**before, "seq_no": after["seq_no"]} == after

    def test_already_global_returns_empty_patch_and_no_table_touch(self) -> None:
        source = _dataset(trace=_trace_table(global_groups=frozenset({"LH-TEST-1"})))
        transformed, touched = corrected.run_stages(
            source, (corrected.CORRECTION_STAGES[0],)
        )
        report = corrected.build_correction_report(
            source, transformed, touched=touched, build_id="0" * 64
        )

        assert trace_seq_no.apply(source) == {}
        assert transformed == source
        assert touched["fdc_trace"] == []
        assert report["tables"]["fdc_trace"]["cells_changed"] == 0
        assert report["tables"]["fdc_trace"]["stage_ids"] == []

    def test_mixed_raw_and_global_groups_are_rejected(self) -> None:
        trace = _trace_table(
            group_ids=("LH-TEST-1", "LH-TEST-2"),
            global_groups=frozenset({"LH-TEST-2"}),
        )

        with pytest.raises(mv3.ManifestSchemaError, match="상태가 섞여"):
            trace_seq_no.apply(_dataset(trace=trace))

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda table: replace(table, rows=()),
            lambda table: replace(table, rows=table.rows[:-1]),
            lambda table: replace(
                table,
                rows=(
                    {**table.rows[0], "recipe_step_no": "3", "step_seq": "3"},
                    *table.rows[1:],
                ),
            ),
            lambda table: replace(
                table,
                rows=({**table.rows[0], "step_seq": "2"}, *table.rows[1:]),
            ),
            lambda table: replace(
                table,
                rows=({**table.rows[0], "seq_no": "-1"}, *table.rows[1:]),
            ),
            lambda table: replace(
                table,
                rows=({**table.rows[0], "seq_no": "x"}, *table.rows[1:]),
            ),
            lambda table: replace(
                table,
                rows=({**table.rows[0], "seq_no": "1"}, *table.rows[1:]),
            ),
        ],
    )
    def test_invalid_epoch_patterns_are_rejected(self, mutate: object) -> None:
        source = _trace_table()
        invalid = mutate(source)  # type: ignore[operator]

        with pytest.raises(mv3.ManifestSchemaError):
            trace_seq_no.apply(_dataset(trace=invalid))

    def test_step_two_trace_alarm_is_rejected(self) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="Step 2 Trace 알람"):
            trace_seq_no.apply(_dataset(alarm=_alarm_table(step="2", seq_no="0")))

    def test_registry_contract_is_exact(self) -> None:
        assert [stage.stage_id for stage in corrected.CORRECTION_STAGES] == [
            trace_seq_no.STAGE_ID,
            dim_parameter_seed.STAGE_ID,
            summary_alarm_time.STAGE_ID,
        ]
        expected = (
            (
                trace_seq_no,
                frozenset({"fdc_trace", "trace_alarm_history"}),
                frozenset({"fdc_trace"}),
            ),
            (
                dim_parameter_seed,
                frozenset({"fdc_trace"}),
                frozenset({"dim_parameter"}),
            ),
            (
                summary_alarm_time,
                frozenset({"summary_alarm_history", "lot_history"}),
                frozenset({"summary_alarm_history"}),
            ),
        )
        for stage, (module, reads, writes) in zip(
            corrected.CORRECTION_STAGES, expected, strict=True
        ):
            assert stage.version == module.STAGE_VERSION
            assert stage.reads == reads
            assert stage.writes == writes
            assert stage.transform is module.apply

    def test_actual_registry_pipeline_records_provenance_and_report(
        self, tmp_path: Path
    ) -> None:
        archive_path, epoch_path, manifest_path = _pipeline_bundle(tmp_path)
        output_root = tmp_path / "allowed" / "corrected"
        common = {
            "archive_path": archive_path,
            "output_root": output_root,
            "allowed_root": tmp_path / "allowed",
            "epoch_path": epoch_path,
            "source_manifest_path": manifest_path,
            "stages": corrected.CORRECTION_STAGES,
        }

        assert corrected.execute(**common) == mv3.EXIT_OK
        active = json.loads((output_root / "v1" / "active.json").read_text())
        build_path = output_root / "v1" / "builds" / active["build_id"]
        receipt = json.loads((build_path / "build-receipt.json").read_text())
        report = json.loads((build_path / "correction-report.json").read_text())

        assert receipt["applied_stages"] == [
            {"stage_id": "trace_seq_no", "stage_version": "1"},
            {"stage_id": "dim_parameter_seed", "stage_version": "1"},
            {"stage_id": "summary_alarm_time", "stage_version": "1"},
        ]
        assert {item["logical_id"] for item in receipt["generator_components"]} == {
            "backend/scripts/build_corrected_dataset.py",
            "backend/scripts/manifest_v3.py",
            "backend/scripts/corrections/trace_seq_no.py",
            "backend/scripts/corrections/dim_parameter_seed.py",
            "backend/scripts/corrections/summary_alarm_time.py",
        }
        assert all(
            mv3.HEX_SHA256_PATTERN.fullmatch(item["sha256"])
            for item in receipt["generator_components"]
        )
        trace_report = report["tables"]["fdc_trace"]
        assert trace_report["cells_changed"] == 24
        assert trace_report["rows_added"] == 24
        assert trace_report["rows_removed"] == 24
        assert trace_report["stage_ids"] == ["trace_seq_no"]
        assert report["tables"]["dim_parameter"] == {
            "row_count_before": None,
            "row_count_after": 8,
            "rows_added": 8,
            "rows_removed": 0,
            "cells_changed": 0,
            "columns_before": None,
            "columns_after": list(dim_parameter_seed.DIM_PARAMETER_COLUMNS),
            "stage_ids": ["dim_parameter_seed"],
        }
        summary_report = report["tables"]["summary_alarm_history"]
        assert summary_report["cells_changed"] == 1
        assert summary_report["rows_added"] == 1
        assert summary_report["rows_removed"] == 1
        assert summary_report["stage_ids"] == ["summary_alarm_time"]
        assert set(receipt["tables"]) == mv3.CORRECTED_TABLES
        for table in mv3.SOURCE_TABLE_FILES.keys() - {
            "fdc_trace",
            "summary_alarm_history",
        }:
            if table == "dim_parameter":
                continue
            table_report = report["tables"][table]
            assert table_report["cells_changed"] == 0
            assert table_report["stage_ids"] == []
        assert corrected.execute(**common, check=True) == mv3.EXIT_OK

    def test_stage_contract_error_returns_cli_schema_exit_without_traceback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        archive_path, epoch_path, manifest_path = _pipeline_bundle(
            tmp_path, alarm=_alarm_table(step="2", seq_no="0")
        )
        output_root = tmp_path / "allowed" / "corrected"
        real_execute = corrected.execute

        def execute_with_test_paths(**kwargs: object) -> int:
            return real_execute(
                **kwargs,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=epoch_path,
                source_manifest_path=manifest_path,
            )

        monkeypatch.setattr(corrected, "execute", execute_with_test_paths)

        exit_code = corrected.main(["--archive", str(archive_path)])
        captured = capsys.readouterr()

        assert exit_code == mv3.EXIT_SCHEMA
        assert "Step 2 Trace 알람" in captured.err
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out

    def test_summary_match_error_preserves_existing_active_build(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        valid_archive, valid_epoch, valid_manifest = _pipeline_bundle(
            tmp_path / "valid"
        )
        output_root = tmp_path / "allowed" / "corrected"
        common = {
            "output_root": output_root,
            "allowed_root": tmp_path / "allowed",
            "stages": corrected.CORRECTION_STAGES,
        }
        assert (
            corrected.execute(
                archive_path=valid_archive,
                epoch_path=valid_epoch,
                source_manifest_path=valid_manifest,
                **common,
            )
            == mv3.EXIT_OK
        )
        active_path = output_root / "v1" / "active.json"
        active_before = active_path.read_bytes()
        builds_root = output_root / "v1" / "builds"
        builds_before = sorted(path.name for path in builds_root.iterdir())

        invalid_archive, invalid_epoch, invalid_manifest = _pipeline_bundle(
            tmp_path / "invalid", summary_lot="LOT-MISSING"
        )
        real_execute = corrected.execute

        def execute_with_invalid_paths(**kwargs: object) -> int:
            return real_execute(
                **kwargs,
                output_root=output_root,
                allowed_root=tmp_path / "allowed",
                epoch_path=invalid_epoch,
                source_manifest_path=invalid_manifest,
            )

        monkeypatch.setattr(corrected, "execute", execute_with_invalid_paths)
        exit_code = corrected.main(["--archive", str(invalid_archive)])
        captured = capsys.readouterr()

        assert exit_code == mv3.EXIT_SCHEMA
        assert "매칭되는 lot_history가 없습니다" in captured.err
        assert "Traceback" not in captured.err
        assert active_path.read_bytes() == active_before
        assert sorted(path.name for path in builds_root.iterdir()) == builds_before
        staging_root = output_root / "v1" / ".staging"
        assert not staging_root.exists() or not any(staging_root.iterdir())
