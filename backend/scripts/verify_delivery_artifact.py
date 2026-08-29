"""C-4.6 public delivery artifact를 fail-closed 판정한다."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
SCHEMA = "delivery-artifact-v1"
CHANNELS = frozenset({"EMAIL", "MES_MOCK"})
TERMINAL_STATUSES = frozenset({"SENT", "FAILED"})
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TRAIL_FIELDS = frozenset(
    {"ts", "action_id", "channel", "status", "duplicate", "http_status"}
)
ARTIFACT_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "action_id",
        "channel",
        "injected_count",
        "expected_first_count",
        "expected_duplicate_count",
        "expected_conflict_count",
        "smtp_received_count",
        "actions_offset_before",
        "actions_offset_after",
        "result_offset_before",
        "result_offset_after",
        "result_key",
        "wf4_lag_before",
        "wf4_lag_after",
    }
)


class ArtifactBlocked(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _emit(status: str, reason_code: str, **safe: Any) -> None:
    print(
        json.dumps(
            {"reason_code": reason_code, "status": status, **safe},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _emit("BLOCKED", "ARG_INVALID")
        raise SystemExit(EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--trail", type=Path, required=True)
    return parser


def _read_object(path: Path, *, reason: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ArtifactBlocked(reason)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactBlocked(reason) from exc
    if not isinstance(payload, dict):
        raise ArtifactBlocked(reason)
    return payload


def _nonnegative_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise ArtifactBlocked("ARTIFACT_FIELD_INVALID")
    return value


def _optional_nonnegative_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ArtifactBlocked("ARTIFACT_FIELD_INVALID")
    return value


def _validate_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != ARTIFACT_FIELDS:
        raise ArtifactBlocked("ARTIFACT_FIELDS_INVALID")
    if payload.get("schema") != SCHEMA:
        raise ArtifactBlocked("ARTIFACT_SCHEMA_INVALID")
    run_id = payload.get("run_id")
    action_id = payload.get("action_id")
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        raise ArtifactBlocked("ARTIFACT_FIELD_INVALID")
    if (
        not isinstance(action_id, str)
        or not action_id.strip()
        or action_id != action_id.strip()
        or len(action_id) > 20
    ):
        raise ArtifactBlocked("ARTIFACT_FIELD_INVALID")
    channel = payload.get("channel")
    if channel not in CHANNELS:
        raise ArtifactBlocked("ARTIFACT_FIELD_INVALID")

    injected = _nonnegative_int(payload, "injected_count")
    expected_first = _nonnegative_int(payload, "expected_first_count")
    expected_duplicate = _nonnegative_int(payload, "expected_duplicate_count")
    expected_conflict = _nonnegative_int(payload, "expected_conflict_count")
    if (
        injected != 1
        or expected_first != 1
        or expected_duplicate != 1
        or expected_conflict != 1
    ):
        raise ArtifactBlocked("ARTIFACT_COUNT_INVALID")

    kafka_keys = (
        "actions_offset_before",
        "actions_offset_after",
        "result_offset_before",
        "result_offset_after",
        "wf4_lag_before",
        "wf4_lag_after",
    )
    kafka_values = {key: _optional_nonnegative_int(payload, key) for key in kafka_keys}
    if channel == "EMAIL":
        if _optional_nonnegative_int(payload, "smtp_received_count") != 1:
            raise ArtifactBlocked("SMTP_EFFECT_COUNT_MISMATCH")
        if any(value is not None for value in kafka_values.values()):
            raise ArtifactBlocked("EMAIL_KAFKA_FIELDS_NOT_NULL")
        if payload.get("result_key") is not None:
            raise ArtifactBlocked("EMAIL_KAFKA_FIELDS_NOT_NULL")
    else:
        if payload.get("smtp_received_count") is not None:
            raise ArtifactBlocked("MES_SMTP_FIELD_NOT_NULL")
        if any(value is None for value in kafka_values.values()):
            raise ArtifactBlocked("MES_OFFSET_FIELD_MISSING")
        actions_before = kafka_values["actions_offset_before"]
        actions_after = kafka_values["actions_offset_after"]
        result_before = kafka_values["result_offset_before"]
        result_after = kafka_values["result_offset_after"]
        assert actions_before is not None and actions_after is not None
        assert result_before is not None and result_after is not None
        if actions_after - actions_before != injected:
            raise ArtifactBlocked("MES_INPUT_OFFSET_MISMATCH")
        if result_after - result_before != 1:
            raise ArtifactBlocked("MES_EFFECT_COUNT_MISMATCH")
        if payload.get("result_key") != payload["action_id"]:
            raise ArtifactBlocked("MES_RESULT_KEY_MISMATCH")
        if kafka_values["wf4_lag_after"] != 0:
            raise ArtifactBlocked("WF4_LAG_NOT_CONVERGED")

    return {
        "channel": channel,
        "action_id": payload["action_id"],
        "run_id": payload["run_id"],
        "expected_first": expected_first,
        "expected_duplicate": expected_duplicate,
        "expected_conflict": expected_conflict,
    }


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str):
        raise ArtifactBlocked("TRAIL_TYPE_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactBlocked("TRAIL_TYPE_INVALID") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ArtifactBlocked("TRAIL_TYPE_INVALID")


def _read_trail(path: Path) -> list[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ArtifactBlocked("TRAIL_NOT_FOUND")
        lines = path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        raise ArtifactBlocked("TRAIL_NOT_FOUND") from exc
    if not lines:
        raise ArtifactBlocked("TRAIL_EMPTY")
    records: list[dict[str, Any]] = []
    for raw_line in lines:
        if len(raw_line) > 512 or not raw_line.endswith(b"\n"):
            raise ArtifactBlocked("TRAIL_LINE_INVALID")
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactBlocked("TRAIL_LINE_INVALID") from exc
        if not isinstance(record, dict) or set(record) != TRAIL_FIELDS:
            raise ArtifactBlocked("TRAIL_FIELDS_INVALID")
        _validate_timestamp(record["ts"])
        if (
            not isinstance(record["action_id"], str)
            or not record["action_id"]
            or record["action_id"] != record["action_id"].strip()
            or len(record["action_id"]) > 20
            or record["channel"] not in CHANNELS
            or type(record["http_status"]) is not int
        ):
            raise ArtifactBlocked("TRAIL_TYPE_INVALID")
        shape = (record["http_status"], record["status"], record["duplicate"])
        if not (
            (
                shape[0] == 200
                and shape[1] in TERMINAL_STATUSES
                and type(shape[2]) is bool
            )
            or shape == (409, None, None)
        ):
            raise ArtifactBlocked("TRAIL_RESULT_SHAPE_INVALID")
        records.append(record)
    return records


def verify(artifact_path: Path, trail_path: Path) -> dict[str, Any]:
    expected = _validate_artifact(
        _read_object(artifact_path, reason="ARTIFACT_NOT_FOUND")
    )
    if trail_path.name != f"trail-{expected['run_id']}.jsonl":
        raise ArtifactBlocked("TRAIL_RUN_MISMATCH")
    matching = [
        row
        for row in _read_trail(trail_path)
        if row["action_id"] == expected["action_id"]
        and row["channel"] == expected["channel"]
    ]
    first = 0
    duplicate = 0
    conflict = 0
    for row in matching:
        shape = (row["http_status"], row["status"], row["duplicate"])
        if shape[0] == 200 and shape[1] in TERMINAL_STATUSES and shape[2] is False:
            first += 1
        elif shape[0] == 200 and shape[1] in TERMINAL_STATUSES and shape[2] is True:
            duplicate += 1
        elif shape == (409, None, None):
            conflict += 1
        else:
            raise ArtifactBlocked("TRAIL_RESULT_SHAPE_INVALID")
    observed = (first, duplicate, conflict)
    wanted = (
        expected["expected_first"],
        expected["expected_duplicate"],
        expected["expected_conflict"],
    )
    if observed != wanted:
        raise ArtifactBlocked("TRAIL_COUNT_MISMATCH")
    return {
        "run_id": expected["run_id"],
        "channel": expected["channel"],
        "first_count": first,
        "duplicate_count": duplicate,
        "conflict_count": conflict,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify(args.artifact, args.trail)
    except ArtifactBlocked as exc:
        _emit("BLOCKED", exc.reason)
        return EXIT_BLOCKED
    _emit("PASSED", "DELIVERY_ARTIFACT_VERIFIED", **result)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
