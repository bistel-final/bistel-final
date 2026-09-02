"""CM-5.2 Agent 12건의 실제 FDC 진단 wafer 대상을 불변 receipt로 발급한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import e2e_reset_evidence as evidence
from sqlalchemy import text

from app.common.enums import ToolCallStatus

TARGET_DATABASE = "kosa_agent_e2e"
ATTEMPT_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
EXPECTED_DISTRIBUTION = {1: 5, 2: 4, 3: 3}

TARGET_SQL = text(
    f"""
    SELECT tool.agent_run_id,
           tool.input ->> 'lot_hist_id' AS lot_hist_id,
           history.wafer_id,
           history.wafer_no
    FROM agent_tool_call AS tool
    JOIN lot_history AS history
      ON history.lot_hist_id = tool.input ->> 'lot_hist_id'
    WHERE tool.tool_name = 'get_fdc_summary'
      AND tool.status = '{ToolCallStatus.SUCCESS.value}'
    ORDER BY tool.agent_run_id, tool.call_seq
    """
)


class DiagnosticTargetError(RuntimeError):
    def __init__(self, reason: str, exit_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


def _wafer_order(value: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", value)
    return (int(match.group(1)) if match is not None else 2_147_483_647, value)


def _load_rows(engine: Any, database: str) -> list[Mapping[str, Any]]:
    if database != TARGET_DATABASE:
        raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1)
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            identity = (
                connection.exec_driver_sql("SELECT current_database() AS database_name")
                .mappings()
                .one()
            )
            if identity["database_name"] != TARGET_DATABASE:
                raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1)
            return list(connection.execute(TARGET_SQL).mappings().all())
    except DiagnosticTargetError:
        raise
    except Exception as exc:
        raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1) from exc


def build_receipt(
    rows: Sequence[Mapping[str, Any]],
    *,
    database: str,
    attempt_id: str,
) -> dict[str, Any]:
    if database != TARGET_DATABASE or not ATTEMPT_PATTERN.fullmatch(attempt_id):
        raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1)
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_pairs: set[tuple[str, str]] = set()
    for raw in rows:
        try:
            run_id = str(raw["agent_run_id"])
            lot_hist_id = str(raw["lot_hist_id"])
            wafer_id = str(raw["wafer_id"])
            wafer_no = int(raw["wafer_no"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1) from exc
        if (
            not run_id
            or not lot_hist_id
            or not wafer_id
            or wafer_no < 1
            or (run_id, lot_hist_id) in seen_pairs
        ):
            raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1)
        seen_pairs.add((run_id, lot_hist_id))
        by_run[run_id].append(
            {
                "lot_hist_id": lot_hist_id,
                "wafer_id": wafer_id,
                "wafer_no": wafer_no,
            }
        )

    target_count = sum(len(items) for items in by_run.values())
    if (
        len(by_run) != 12
        or target_count != 22
        or any(not 1 <= len(items) <= 3 for items in by_run.values())
        or any(
            len({item["wafer_id"] for item in items}) != len(items)
            for items in by_run.values()
        )
    ):
        raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1)
    distribution = Counter(len(items) for items in by_run.values())
    if dict(distribution) != EXPECTED_DISTRIBUTION:
        raise DiagnosticTargetError("TARGET_DISTRIBUTION_MISMATCH", 3)

    runs: list[dict[str, Any]] = []
    for run_id in sorted(by_run):
        ordered = sorted(
            by_run[run_id], key=lambda item: _wafer_order(item["wafer_id"])
        )
        runs.append(
            {
                "agent_run_id": run_id,
                "targets": [
                    {**item, "target_order": index}
                    for index, item in enumerate(ordered, start=1)
                ],
            }
        )
    return {
        "artifact_type": "cm52_diagnostic_targets",
        "format_version": 1,
        "dataset_epoch": evidence.DATASET_EPOCH,
        "attempt_id": attempt_id,
        "database": database,
        "run_count": len(runs),
        "target_count": target_count,
        "targets_per_run_distribution": {
            str(size): distribution[size] for size in sorted(distribution)
        },
        "runs": runs,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: Callable[[], Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-database", required=True, choices=(TARGET_DATABASE,))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.output.is_absolute():
            raise DiagnosticTargetError("TARGET_STRUCTURE_INVALID", 1)
        if engine_factory is None:
            from app.common.db import get_app_engine

            engine_factory = get_app_engine
        rows = _load_rows(engine_factory(), args.agent_database)
        payload = build_receipt(
            rows,
            database=args.agent_database,
            attempt_id=args.attempt_id,
        )
        artifact_sha256 = evidence.write_exclusive_receipt(args.output, payload)
    except DiagnosticTargetError as exc:
        print(exc.reason, file=sys.stderr)
        return exc.exit_code
    except Exception:  # noqa: BLE001 - DB·경로 원문을 숨긴다.
        print("TARGET_STRUCTURE_INVALID", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"status": "PASS", "artifact_sha256": artifact_sha256},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
