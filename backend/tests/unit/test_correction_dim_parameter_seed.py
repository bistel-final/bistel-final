"""V4-CM-1.4 dim_parameter seed stage 계약을 검증한다."""

from __future__ import annotations

import csv
import io
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_corrected_dataset as corrected  # noqa: E402
import manifest_v3 as mv3  # noqa: E402
from corrections import dim_parameter_seed  # noqa: E402


def _trace_table(
    parameter_ids: tuple[str, ...] = dim_parameter_seed.PARAMETER_ORDER,
) -> corrected.TableData:
    rows = tuple(
        {
            "lot_hist_id": "LH-TEST-1",
            "parameter_id": parameter_id,
            "seq_no": "0",
            "recipe_step_no": "1",
            "step_seq": "1",
            "measured_at": "2026-08-01 00:00:00",
            "value": "1.0",
        }
        for parameter_id in parameter_ids
    )
    return corrected.TableData(mv3.SOURCE_EXPECTED_COLUMNS["fdc_trace"], rows)


def _serialize_seed(rows: tuple[dict[str, str], ...]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=list(dim_parameter_seed.DIM_PARAMETER_COLUMNS),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


class TestDimParameterSeed:
    def test_creates_exact_seed_without_mutating_trace(self) -> None:
        trace = _trace_table()
        original_rows = tuple(dict(row) for row in trace.rows)

        patch = dim_parameter_seed.apply({"fdc_trace": trace})
        result = patch["dim_parameter"]

        assert trace.rows == original_rows
        assert isinstance(result, corrected.TableData)
        assert result.columns == dim_parameter_seed.DIM_PARAMETER_COLUMNS
        assert result.rows == dim_parameter_seed.DIM_PARAMETER_ROWS
        assert tuple(row["parameter_id"] for row in result.rows) == (
            dim_parameter_seed.PARAMETER_ORDER
        )

    def test_seed_values_are_numeric_ordered_and_ascii_safe(self) -> None:
        rows = dim_parameter_seed.DIM_PARAMETER_ROWS

        assert [row["area"] for row in rows] == ["photo"] * 4 + ["etch"] * 4
        assert [row["parameter_id"] for row in rows if row["upper_only"] == "true"] == [
            "ET_REFL"
        ]
        for row in rows:
            limits = [
                Decimal(row[column])
                for column in (
                    "spec_lower",
                    "ctrl_lower",
                    "target_value",
                    "ctrl_upper",
                    "spec_upper",
                )
            ]
            assert limits == sorted(limits)

        focus = next(row for row in rows if row["parameter_id"] == "PH_FOCUS")
        assert focus["spec_lower"] == "-60.0"
        assert focus["ctrl_lower"] == "-36.0"
        assert "\u2212" not in _serialize_seed(rows)

    def test_exact_existing_seed_is_idempotent(self) -> None:
        trace = _trace_table()
        existing = replace(
            trace,
            columns=dim_parameter_seed.DIM_PARAMETER_COLUMNS,
            rows=dim_parameter_seed.DIM_PARAMETER_ROWS,
        )

        assert (
            dim_parameter_seed.apply({"fdc_trace": trace, "dim_parameter": existing})
            == {}
        )

    @pytest.mark.parametrize("case", ["columns", "missing", "extra", "value"])
    def test_partial_or_conflicting_existing_seed_is_rejected(self, case: str) -> None:
        trace = _trace_table()
        columns = dim_parameter_seed.DIM_PARAMETER_COLUMNS
        rows = dim_parameter_seed.DIM_PARAMETER_ROWS
        if case == "columns":
            existing = replace(trace, columns=tuple(reversed(columns)), rows=rows)
        elif case == "missing":
            existing = replace(trace, columns=columns, rows=rows[:-1])
        elif case == "extra":
            existing = replace(trace, columns=columns, rows=(*rows, dict(rows[0])))
        else:
            changed = dict(rows[0])
            changed["target_value"] = "25.1"
            existing = replace(trace, columns=columns, rows=(changed, *rows[1:]))

        with pytest.raises(mv3.ManifestSchemaError, match="기존 dim_parameter"):
            dim_parameter_seed.apply({"fdc_trace": trace, "dim_parameter": existing})

    @pytest.mark.parametrize(
        "parameter_ids",
        [
            dim_parameter_seed.PARAMETER_ORDER[:-1],
            (*dim_parameter_seed.PARAMETER_ORDER, "UNKNOWN_PARAMETER"),
        ],
    )
    def test_trace_fk_set_must_match_seed_in_both_directions(
        self, parameter_ids: tuple[str, ...]
    ) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="양방향 집합"):
            dim_parameter_seed.apply({"fdc_trace": _trace_table(parameter_ids)})

    def test_missing_or_wrong_trace_contract_is_rejected(self) -> None:
        with pytest.raises(mv3.ManifestSchemaError, match="table이 없습니다"):
            dim_parameter_seed.apply({})

        trace = replace(_trace_table(), columns=("parameter_id",))
        with pytest.raises(mv3.ManifestSchemaError, match="column 계약"):
            dim_parameter_seed.apply({"fdc_trace": trace})
