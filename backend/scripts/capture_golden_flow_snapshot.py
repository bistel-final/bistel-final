"""C-6.1 golden-flow phase 종료 직후 DB snapshot을 evidence artifact로 기록한다.

`read_golden_flow_snapshot()`과 같은 순서(REPEATABLE READ READ ONLY → identity →
business SELECT)로 읽은 JSON을 그대로 `DB_SNAPSHOT` artifact(`application/json`)로
저장한다. 파일은 0600 · 기존 파일 덮어쓰기 금지이며 stdout에는 sha256과 행 수만 낸다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

from app.agent.golden_flow import GoldenFlowContractError, snapshot_from_mapping
from app.agent.golden_flow_repository import (
    GOLDEN_SNAPSHOT_SQL,
    IDENTITY_SQL,
    TARGET_DATABASE,
    TARGET_ROLE,
)

try:
    from . import e2e_reset_evidence as evidence
except ImportError:  # pragma: no cover - 컨테이너에서는 scripts/ 가 sys.path 루트다.
    import e2e_reset_evidence as evidence

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3

PHASES = (
    "PREFLIGHT",
    "BATCH_BASELINE",
    "PRE_APPROVAL",
    "DECISIONS",
    "UNKNOWN",
    "MANUAL_RETRY",
    "SECOND_BATCH",
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _emit("BLOCKED", "ARG_INVALID")
        raise SystemExit(EXIT_USAGE)


def _emit(status: str, reason_code: str, **safe: Any) -> None:
    print(
        json.dumps(
            {"reason_code": reason_code, "status": status, **safe},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--phase", required=True, choices=PHASES)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def read_snapshot_payload(engine: Any, *, database: str) -> dict[str, Any]:
    """Repository와 같은 transaction 규율로 raw snapshot mapping을 읽는다."""

    if database != TARGET_DATABASE:
        raise GoldenFlowContractError("TARGET_MISMATCH")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        identity = connection.execute(IDENTITY_SQL).one()
        if (
            identity.database_name != TARGET_DATABASE
            or identity.role_name != TARGET_ROLE
        ):
            raise GoldenFlowContractError("TARGET_MISMATCH")
        row = connection.execute(GOLDEN_SNAPSHOT_SQL).one()
    payload = row.snapshot
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise GoldenFlowContractError("SNAPSHOT_INVALID")
    # artifact parser와 같은 exact 계약으로 한 번 통과시켜 저장 전에 거른다.
    snapshot_from_mapping(payload)
    return payload


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: Callable[[], Any] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if not args.output.is_absolute():
        _emit("BLOCKED", "OUTPUT_NOT_ABSOLUTE")
        return EXIT_USAGE
    try:
        if engine_factory is None:
            from app.common.db import get_app_engine

            engine_factory = get_app_engine
        payload = read_snapshot_payload(engine_factory(), database=args.database)
    except GoldenFlowContractError as exc:
        _emit("BLOCKED", str(exc))
        return EXIT_BLOCKED
    except Exception:  # noqa: BLE001 - DSN·원문을 stdout으로 흘리지 않는다.
        _emit("FAILED", "SNAPSHOT_READ_FAILED")
        return EXIT_FAILED
    try:
        sha256 = evidence.write_atomic_receipt(args.output, payload)
    except evidence.EvidenceError as exc:
        _emit("BLOCKED", exc.reason_code)
        return EXIT_BLOCKED
    except OSError:
        _emit("FAILED", "SNAPSHOT_WRITE_FAILED")
        return EXIT_FAILED
    _emit(
        "PASSED",
        "GOLDEN_SNAPSHOT_WRITTEN",
        phase=args.phase,
        sha256=sha256,
        counts={key: len(payload[key]) for key in sorted(payload)},
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
