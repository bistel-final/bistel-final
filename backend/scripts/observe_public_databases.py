"""CM-5.2 공개 DB 불변성과 허용된 Text2SQL 로그 증가를 검증한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

if __package__:
    from . import e2e_reset_evidence as evidence
    from .e2e_analytics_questions import digest
    from .orchestrate_e2e_reset_evidence import snapshot_observer
else:
    import e2e_reset_evidence as evidence
    from e2e_analytics_questions import digest
    from orchestrate_e2e_reset_evidence import snapshot_observer

FORMAT_VERSION = 1
ARTIFACT_TYPE = "cm52_public_database_observer"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ObserverError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _one(result: Any) -> Mapping[str, Any]:
    try:
        row = result.mappings().one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise ObserverError("OBSERVER_READ_FAILED") from exc
    return row


def _rows(result: Any) -> list[Mapping[str, Any]]:
    try:
        return list(result.mappings().all())
    except (AttributeError, TypeError) as exc:
        raise ObserverError("OBSERVER_READ_FAILED") from exc


def _log_summary_probe(connection: Any) -> dict[str, int]:
    row = _one(
        connection.exec_driver_sql(
            """
            SELECT count(*)::bigint AS row_count,
                   coalesce(max(nl_query_log_id), 0)::bigint AS max_id,
                   coalesce(
                     pg_sequence_last_value(
                       'public.nl_query_log_nl_query_log_id_seq'
                     ), 0
                   )::bigint AS sequence_last_value
            FROM public.nl_query_log
            """
        )
    )
    return {
        "row_count": int(row["row_count"]),
        "max_id": int(row["max_id"]),
        "sequence_last_value": int(row["sequence_last_value"]),
    }


def _log_verify_probe(baseline_max: int) -> Callable[[Any], Mapping[str, Any]]:
    def probe(connection: Any) -> Mapping[str, Any]:
        summary = _log_summary_probe(connection)
        rows = _rows(
            connection.execute(
                text(
                    """
                    SELECT nl_query_log_id, question
                    FROM public.nl_query_log
                    WHERE nl_query_log_id > :baseline_max
                    ORDER BY nl_query_log_id
                    """
                ),
                {"baseline_max": baseline_max},
            )
        )
        summary["new_entries"] = [
            [int(row["nl_query_log_id"]), digest(str(row["question"]))] for row in rows
        ]
        return summary

    return probe


def _capture_state(
    *,
    environ: Mapping[str, str],
    observer: Callable[..., dict[str, Any]] = snapshot_observer,
    baseline_max: int | None = None,
) -> dict[str, Any]:
    """Capture each database axis in its own read-only repeatable-read transaction.

    This is intentionally not an atomic cross-database snapshot.  The runbook
    requires external traffic to remain stopped between capture and verify.
    """
    immutable_agent = observer("kosa_agent", environ=environ)
    text_snapshot = observer(
        "kosa_text2sql",
        environ=environ,
        extra_probe=(
            _log_summary_probe
            if baseline_max is None
            else _log_verify_probe(baseline_max)
        ),
    )
    text_snapshot = dict(text_snapshot)
    log_probe = text_snapshot.pop("extra_probe", None)
    if not isinstance(log_probe, Mapping):
        raise ObserverError("OBSERVER_READ_FAILED")
    strict_agent = observer("kosa_agent", environ=environ, strict=True)
    return {
        "immutable": {
            "kosa_agent": immutable_agent,
            "kosa_text2sql": text_snapshot,
        },
        "strict_kosa_agent": strict_agent,
        "text2sql_log": dict(log_probe),
    }


def _base_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": ARTIFACT_TYPE,
        "format_version": FORMAT_VERSION,
        "dataset_epoch": evidence.DATASET_EPOCH,
        "recorded_at": datetime.now(UTC).isoformat(),
        **dict(state),
    }


def _read_object(path: Path) -> Mapping[str, Any]:
    if not path.is_absolute():
        raise ObserverError("OBSERVER_INPUT_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ObserverError("OBSERVER_INPUT_INVALID") from exc
    if not isinstance(value, Mapping):
        raise ObserverError("OBSERVER_INPUT_INVALID")
    return value


def _read_expected(path: Path) -> list[list[int | str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ObserverError("LOG_DELTA_MISMATCH") from exc
    if not isinstance(value, list):
        raise ObserverError("LOG_DELTA_MISMATCH")
    normalized: list[list[int | str]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or type(item[0]) is not int
            or item[0] < 1
            or not isinstance(item[1], str)
            or not SHA256_PATTERN.fullmatch(item[1])
        ):
            raise ObserverError("LOG_DELTA_MISMATCH")
        normalized.append([item[0], item[1]])
    if normalized != sorted(normalized) or len({item[0] for item in normalized}) != len(
        normalized
    ):
        raise ObserverError("LOG_DELTA_MISMATCH")
    return normalized


def _snapshot_sha(snapshot: object) -> str:
    if not isinstance(snapshot, Mapping):
        raise ObserverError("OBSERVER_INPUT_INVALID")
    value = snapshot.get("sha256")
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ObserverError("OBSERVER_INPUT_INVALID")
    return value


def verify_state(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    expected: list[list[int | str]],
) -> dict[str, Any]:
    try:
        baseline_immutable = baseline["immutable"]
        current_immutable = current["immutable"]
        if not isinstance(baseline_immutable, Mapping) or not isinstance(
            current_immutable, Mapping
        ):
            raise ObserverError("OBSERVER_INPUT_INVALID")
        immutable_match = all(
            _snapshot_sha(baseline_immutable[database])
            == _snapshot_sha(current_immutable[database])
            for database in ("kosa_agent", "kosa_text2sql")
        )
        if not immutable_match:
            raise ObserverError("OBSERVER_DRIFT")
        if _snapshot_sha(baseline["strict_kosa_agent"]) != _snapshot_sha(
            current["strict_kosa_agent"]
        ):
            raise ObserverError("PUBLIC_RUNTIME_WRITTEN")
        before_log = baseline["text2sql_log"]
        after_log = current["text2sql_log"]
        if not isinstance(before_log, Mapping) or not isinstance(after_log, Mapping):
            raise ObserverError("OBSERVER_INPUT_INVALID")
        actual = after_log.get("new_entries")
        count_delta = int(after_log["row_count"]) - int(before_log["row_count"])
        sequence_delta = int(after_log["sequence_last_value"]) - int(
            before_log["sequence_last_value"]
        )
        if (
            actual != expected
            or count_delta != len(expected)
            or sequence_delta != len(expected)
        ):
            raise ObserverError("LOG_DELTA_MISMATCH")
    except (KeyError, TypeError, ValueError) as exc:
        raise ObserverError("OBSERVER_INPUT_INVALID") from exc
    return {
        "status": "PASS",
        "immutable_sha256": {
            database: _snapshot_sha(current_immutable[database])
            for database in ("kosa_agent", "kosa_text2sql")
        },
        "strict_kosa_agent_sha256": _snapshot_sha(current["strict_kosa_agent"]),
        "text2sql_log_delta": {
            "count": len(expected),
            "digests": expected,
            "sequence_delta": sequence_delta,
        },
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    observer: Callable[..., dict[str, Any]] = snapshot_observer,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--baseline", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--expected-digests", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.output.is_absolute():
            raise ObserverError("OBSERVER_INPUT_INVALID")
        if args.command == "capture":
            payload = _base_payload(
                _capture_state(environ=os.environ, observer=observer)
            )
        else:
            baseline = _read_object(args.baseline)
            expected = _read_expected(args.expected_digests)
            baseline_log = baseline.get("text2sql_log")
            if not isinstance(baseline_log, Mapping):
                raise ObserverError("OBSERVER_INPUT_INVALID")
            state = _capture_state(
                environ=os.environ,
                observer=observer,
                baseline_max=int(baseline_log["max_id"]),
            )
            payload = _base_payload(verify_state(baseline, state, expected))
        evidence.write_exclusive_receipt(args.output, payload)
    except ObserverError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - DB/credential 원문을 숨긴다.
        print("OBSERVER_FAILED", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
