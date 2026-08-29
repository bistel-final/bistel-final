"""UNKNOWN 확정·FAILED retry를 위한 확인형 운영 CLI (V5-C-4.6)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.repository import (  # noqa: E402
    AgentRepositoryError,
    DeliveryRecoveryReason,
    RepositoryConflict,
    RepositoryNotFound,
    list_stale_deliveries,
    mark_delivery_unknown,
    retry_failed_delivery,
)
from app.common import config  # noqa: E402
from app.common.db import get_app_engine  # noqa: E402
from app.common.enums import DeliveryChannel  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_CHANGE = 3
ALLOWED_TARGETS = frozenset({"kosa_agent", "kosa_agent_e2e"})


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
        _emit("FAILED", "ARG_INVALID")
        raise SystemExit(EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--target", required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list-stale")

    confirm = commands.add_parser("confirm-unknown")
    confirm.add_argument("--action-id", required=True)
    confirm.add_argument(
        "--channel",
        required=True,
        choices=[channel.value for channel in DeliveryChannel],
    )
    confirm.add_argument("--confirm", required=True)
    confirm.add_argument("--provider-checked", action="store_true")

    retry = commands.add_parser("retry")
    retry.add_argument("--action-id", required=True)
    retry.add_argument(
        "--channel",
        required=True,
        choices=[channel.value for channel in DeliveryChannel],
    )
    retry.add_argument("--confirm", required=True)
    return parser


def _expected_confirmation(args: argparse.Namespace) -> str:
    return f"{args.command} {args.target} {args.action_id} {args.channel}"


def _target_is_current(connection: Any, target: str) -> bool:
    actual = connection.execute(text("SELECT current_database()"))
    return actual.scalar_one() == target


def _safe_row(row: Any) -> dict[str, str]:
    return {
        "action_id": row.action_id,
        "channel": row.channel.value,
        "delivery_status": row.status.value,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: Callable[[], Any] = get_app_engine,
) -> int:
    args = _parser().parse_args(argv)
    if args.target not in ALLOWED_TARGETS:
        _emit("FAILED", "TARGET_NOT_ALLOWED")
        return EXIT_USAGE
    if args.command != "list-stale":
        if args.confirm != _expected_confirmation(args):
            _emit("NO_CHANGE", "CONFIRMATION_MISMATCH")
            return EXIT_NO_CHANGE
        if args.command == "confirm-unknown" and not args.provider_checked:
            _emit("NO_CHANGE", "PROVIDER_CHECK_REQUIRED")
            return EXIT_NO_CHANGE

    try:
        engine = engine_factory()
        context = engine.connect() if args.command == "list-stale" else engine.begin()
        with context as connection:
            if not _target_is_current(connection, args.target):
                _emit("NO_CHANGE", "TARGET_DB_MISMATCH")
                return EXIT_NO_CHANGE

            if args.command == "list-stale":
                rows = list_stale_deliveries(
                    connection,
                    stale_after_seconds=config.DELIVERY_UNKNOWN_AFTER_SEC,
                )
                _emit(
                    "PASSED",
                    "STALE_DELIVERIES_LISTED",
                    count=len(rows),
                    deliveries=[_safe_row(row) for row in rows],
                    target=args.target,
                )
                return EXIT_OK

            channel = DeliveryChannel(args.channel)
            if args.command == "confirm-unknown":
                result = mark_delivery_unknown(
                    connection,
                    action_id=args.action_id,
                    channel=channel,
                    stale_after_seconds=config.DELIVERY_UNKNOWN_AFTER_SEC,
                )
                if result.reason is not DeliveryRecoveryReason.APPLIED:
                    _emit(
                        "NO_CHANGE",
                        result.reason.value,
                        action_id=args.action_id,
                        channel=channel.value,
                    )
                    return EXIT_NO_CHANGE
                assert result.delivery is not None
                _emit(
                    "PASSED",
                    result.reason.value,
                    **_safe_row(result.delivery),
                )
                return EXIT_OK

            retried = retry_failed_delivery(
                connection,
                action_id=args.action_id,
                channel=channel,
            )
            _emit("PASSED", "APPLIED", **_safe_row(retried))
            return EXIT_OK
    except RepositoryNotFound:
        _emit("NO_CHANGE", DeliveryRecoveryReason.NO_TARGET.value)
        return EXIT_NO_CHANGE
    except RepositoryConflict as exc:
        reason = (
            DeliveryRecoveryReason.STATE_NOT_ALLOWED.value
            if exc.code == "DELIVERY_RETRY_STATE_NOT_ALLOWED"
            else "DELIVERY_STATE_CHANGED"
        )
        _emit("NO_CHANGE", reason)
        return EXIT_NO_CHANGE
    except AgentRepositoryError:
        _emit("FAILED", "DELIVERY_RECOVERY_FAILED")
        return EXIT_FAILED
    except Exception:  # noqa: BLE001 - raw driver/config details never leave the CLI.
        _emit("FAILED", "DELIVERY_RECOVERY_FAILED")
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
