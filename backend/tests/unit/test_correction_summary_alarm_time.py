"""V4-CM-1.4 Summary alarm time overlay stage 계약을 검증한다."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_corrected_dataset as corrected  # noqa: E402
import manifest_v3 as mv3  # noqa: E402
from corrections import summary_alarm_time  # noqa: E402


def _lot_row(
    *,
    lot_id: str = "LOT001",
    wafer_no: str = "1",
    chamber_id: str = "EQP01-PM1",
    area_id: str = "photo",
    equipment_id: str = "EQP01",
    track_in_at: str = "2026-08-01 00:00:00",
) -> dict[str, str]:
    row = {column: "" for column in mv3.SOURCE_EXPECTED_COLUMNS["lot_history"]}
    row.update(
        {
            "lot_hist_id": f"LH-{lot_id}-{wafer_no}-{chamber_id}",
            "lot_id": lot_id,
            "wafer_no": wafer_no,
            "area_id": area_id,
            "equipment_id": equipment_id,
            "chamber_id": chamber_id,
            "track_in_at": track_in_at,
        }
    )
    return row


def _summary_row(
    *,
    alarm_id: str = "SAL-TEST-1",
    lot: str = "LOT001",
    wafer: str = "1",
    chamber: str = "EQP01-PM1",
    area: str = "photo",
    equipment: str = "EQP01",
    occurred_at: str = "",
) -> dict[str, str]:
    row = {
        column: "" for column in mv3.SOURCE_EXPECTED_COLUMNS["summary_alarm_history"]
    }
    row.update(
        {
            "alarm_id": alarm_id,
            "lot": lot,
            "wafer": wafer,
            "chamber": chamber,
            "area": area,
            "equipment": equipment,
            "occurred_at": occurred_at,
            "alarm_type": "OOC",
        }
    )
    return row


def _table(table_name: str, rows: tuple[dict[str, str], ...]) -> corrected.TableData:
    return corrected.TableData(mv3.SOURCE_EXPECTED_COLUMNS[table_name], rows)


def _dataset(
    *,
    summary_rows: tuple[dict[str, str], ...] | None = None,
    lot_rows: tuple[dict[str, str], ...] | None = None,
) -> dict[str, corrected.TableData]:
    return {
        "summary_alarm_history": _table(
            "summary_alarm_history", summary_rows or (_summary_row(),)
        ),
        "lot_history": _table("lot_history", lot_rows or (_lot_row(),)),
    }


class TestSummaryAlarmTime:
    def test_fills_only_blank_time_and_preserves_order_and_input(self) -> None:
        source_rows = (
            _summary_row(),
            _summary_row(
                alarm_id="SAL-TEST-2",
                lot="LOT002",
                wafer="2",
                chamber="EQP02-PM1",
                equipment="EQP02",
                occurred_at="2026-08-02 01:02:03",
            ),
        )
        lots = (
            _lot_row(),
            _lot_row(
                lot_id="LOT002",
                wafer_no="2",
                chamber_id="EQP02-PM1",
                equipment_id="EQP02",
                track_in_at="2026-08-02 01:02:03",
            ),
        )
        source = _dataset(summary_rows=source_rows, lot_rows=lots)

        result = summary_alarm_time.apply(source)["summary_alarm_history"]

        assert source["summary_alarm_history"].rows == source_rows
        assert [row["alarm_id"] for row in result.rows] == [
            "SAL-TEST-1",
            "SAL-TEST-2",
        ]
        assert [row["occurred_at"] for row in result.rows] == [
            "2026-08-01 00:00:00",
            "2026-08-02 01:02:03",
        ]
        assert result.columns == source["summary_alarm_history"].columns

    def test_already_matching_times_are_idempotent(self) -> None:
        source = _dataset(
            summary_rows=(_summary_row(occurred_at="2026-08-01 00:00:00"),)
        )

        assert summary_alarm_time.apply(source) == {}

    def test_missing_or_duplicate_lot_match_is_rejected(self) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="매칭되는 lot_history"):
            summary_alarm_time.apply(_dataset(lot_rows=(_lot_row(lot_id="LOT999"),)))

        duplicate = _lot_row()
        with pytest.raises(mv3.ManifestSchemaError, match="key가 중복"):
            summary_alarm_time.apply(_dataset(lot_rows=(duplicate, dict(duplicate))))

    def test_blank_track_in_is_rejected(self) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="track_in_at이 비어"):
            summary_alarm_time.apply(_dataset(lot_rows=(_lot_row(track_in_at=" "),)))

    @pytest.mark.parametrize(
        ("summary", "message"),
        [
            (_summary_row(area="etch"), "area"),
            (_summary_row(equipment="EQP99"), "equipment"),
        ],
    )
    def test_cross_check_fields_must_match(
        self, summary: dict[str, str], message: str
    ) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match=message):
            summary_alarm_time.apply(_dataset(summary_rows=(summary,)))

    def test_existing_nonblank_conflict_is_rejected(self) -> None:
        source = _dataset(
            summary_rows=(_summary_row(occurred_at="2026-08-01 00:00:01"),)
        )
        with pytest.raises(mv3.ManifestSchemaError, match="기존 occurred_at"):
            summary_alarm_time.apply(source)

    @pytest.mark.parametrize("table_name", ["summary_alarm_history", "lot_history"])
    def test_source_column_contract_is_exact(self, table_name: str) -> None:
        source = _dataset()
        source[table_name] = replace(source[table_name], columns=("invalid",))
        with pytest.raises(mv3.ManifestSchemaError, match="column 계약"):
            summary_alarm_time.apply(source)

    def test_missing_source_table_is_rejected(self) -> None:
        source = _dataset()
        del source["lot_history"]
        with pytest.raises(mv3.ManifestSchemaError, match="table이 없습니다"):
            summary_alarm_time.apply(source)
