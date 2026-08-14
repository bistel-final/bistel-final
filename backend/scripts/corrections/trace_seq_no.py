"""kosa_0813 Trace의 Step-local seq_no를 전역 순번으로 보정한다."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from enum import StrEnum
from typing import Any

from manifest_v3 import SOURCE_EXPECTED_COLUMNS, ManifestSchemaError

STAGE_ID = "trace_seq_no"
STAGE_VERSION = "1"

_EXPECTED_STEPS = frozenset({"1", "2"})
_RAW_STEP_SEQUENCES = frozenset({"0", "1", "2"})
_GLOBAL_STEP_TWO_SEQUENCES = frozenset({"3", "4", "5"})


class TraceSeqState(StrEnum):
    """table 전체에서 허용하는 두 정상 상태."""

    RAW_STEP_LOCAL = "RAW_STEP_LOCAL"
    ALREADY_GLOBAL = "ALREADY_GLOBAL"


def _require_table(dataset: Mapping[str, Any], table_name: str) -> Any:
    try:
        table = dataset[table_name]
    except KeyError as exc:
        raise ManifestSchemaError(f"{STAGE_ID}: {table_name} table이 없습니다") from exc

    expected_columns = SOURCE_EXPECTED_COLUMNS[table_name]
    if tuple(getattr(table, "columns", ())) != expected_columns:
        raise ManifestSchemaError(f"{STAGE_ID}: {table_name} column 계약이 다릅니다")
    rows = getattr(table, "rows", None)
    if not isinstance(rows, tuple):
        raise ManifestSchemaError(f"{STAGE_ID}: {table_name} row 계약이 다릅니다")
    return table


def _group_label(key: tuple[str, str]) -> str:
    lot_hist_id, parameter_id = key
    return f"lot_hist_id={lot_hist_id[:32]}, parameter_id={parameter_id[:32]}"


def classify_state(table: Any) -> TraceSeqState:
    """fdc_trace 전체를 RAW 또는 ALREADY_GLOBAL로 판정한다."""

    rows = table.rows
    if not rows:
        raise ManifestSchemaError(f"{STAGE_ID}: fdc_trace가 비어 있습니다")

    groups: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        lot_hist_id = row["lot_hist_id"]
        parameter_id = row["parameter_id"]
        step = row["recipe_step_no"]
        step_seq = row["step_seq"]
        seq_no = row["seq_no"]
        if not lot_hist_id or not parameter_id:
            raise ManifestSchemaError(f"{STAGE_ID}: Trace group ID가 비어 있습니다")
        if step not in _EXPECTED_STEPS or step_seq != step:
            raise ManifestSchemaError(f"{STAGE_ID}: Step 또는 step_seq 계약이 다릅니다")
        groups[(lot_hist_id, parameter_id)][step].append(seq_no)

    states: set[TraceSeqState] = set()
    for key, step_rows in groups.items():
        label = _group_label(key)
        if set(step_rows) != _EXPECTED_STEPS:
            raise ManifestSchemaError(f"{STAGE_ID}: Step 집합이 다릅니다: {label}")
        if any(len(values) != 3 for values in step_rows.values()):
            raise ManifestSchemaError(f"{STAGE_ID}: Step별 행 수가 다릅니다: {label}")

        step_one = frozenset(step_rows["1"])
        step_two = frozenset(step_rows["2"])
        if step_one != _RAW_STEP_SEQUENCES:
            raise ManifestSchemaError(
                f"{STAGE_ID}: Step 1 seq 계약이 다릅니다: {label}"
            )
        if len(step_two) != 3:
            raise ManifestSchemaError(f"{STAGE_ID}: Step 2 seq가 중복됐습니다: {label}")
        if step_two == _RAW_STEP_SEQUENCES:
            states.add(TraceSeqState.RAW_STEP_LOCAL)
        elif step_two == _GLOBAL_STEP_TWO_SEQUENCES:
            states.add(TraceSeqState.ALREADY_GLOBAL)
        else:
            raise ManifestSchemaError(
                f"{STAGE_ID}: Step 2 seq 계약이 다릅니다: {label}"
            )

    if len(states) != 1:
        raise ManifestSchemaError(
            f"{STAGE_ID}: RAW와 ALREADY_GLOBAL 상태가 섞여 있습니다"
        )
    return states.pop()


def _validate_trace_alarms(table: Any) -> None:
    for row in table.rows:
        if row["step_no"] != "1" or row["step_seq"] != "1":
            raise ManifestSchemaError(
                f"{STAGE_ID}: Step 2 Trace 알람은 현재 보정 계약에서 허용하지 않습니다"
            )
        if row["seq_no"] not in _RAW_STEP_SEQUENCES:
            raise ManifestSchemaError(f"{STAGE_ID}: Trace 알람 seq_no 계약이 다릅니다")


def apply(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """허용 상태만 보정하고 입력 table 객체의 dataclass 타입을 보존한다."""

    trace = _require_table(dataset, "fdc_trace")
    alarms = _require_table(dataset, "trace_alarm_history")
    _validate_trace_alarms(alarms)

    state = classify_state(trace)
    if state is TraceSeqState.ALREADY_GLOBAL:
        return {}

    corrected_rows = []
    for row in trace.rows:
        corrected = dict(row)
        corrected["seq_no"] = str(
            (int(row["recipe_step_no"]) - 1) * 3 + int(row["seq_no"])
        )
        corrected_rows.append(corrected)
    return {"fdc_trace": replace(trace, rows=tuple(corrected_rows))}
