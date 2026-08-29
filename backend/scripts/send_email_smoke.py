"""C-4.3 공용 EMAIL 2건 smoke의 확인형 runner.

기본 실행은 preview만 출력하고 외부효과가 없다. 실제 실행은 e2e DB·수신자 1명·정확한
confirmation을 모두 만족해야 하며 WARNING 1건과 EQP_HOLD 승인요청 1건만 호출한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.email_delivery import (  # noqa: E402
    EmailDeliveryConfigError,
    EmailDeliveryOutcome,
    load_email_delivery_config,
    production_ports,
)
from app.common import config  # noqa: E402
from app.common.db import get_app_engine  # noqa: E402

TARGET_DATABASE = "kosa_agent_e2e"
CONFIRMATION = "SEND-2-EMAILS"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3


def _emit(status: str, reason_code: str, **safe: Any) -> None:
    print(
        json.dumps(
            {"reason_code": reason_code, "status": status, **safe},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=sys.stderr,
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _emit("FAILED", "ARG_INVALID")
        raise SystemExit(EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--warning-action-id", required=True)
    parser.add_argument("--approval-action-id", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--target", default=TARGET_DATABASE)
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    completed_calls = 0
    try:
        delivery_config = load_email_delivery_config(config)
    except EmailDeliveryConfigError:
        _emit("FAILED", "EMAIL_DELIVERY_CONFIG_INVALID")
        return EXIT_USAGE
    if len(delivery_config.recipients) != 1:
        _emit(
            "FAILED",
            "SMOKE_RECIPIENT_COUNT_INVALID",
            recipient_count=len(delivery_config.recipients),
        )
        return EXIT_USAGE
    if args.target != TARGET_DATABASE:
        _emit("FAILED", "TARGET_NOT_ALLOWED")
        return EXIT_USAGE
    if args.warning_action_id == args.approval_action_id:
        _emit("FAILED", "SMOKE_ACTIONS_MUST_DIFFER")
        return EXIT_USAGE
    if args.confirm != CONFIRMATION:
        _emit(
            "CONFIRM_REQUIRED",
            "CONFIRM_REQUIRED",
            recipient_count=1,
            send_count=2,
            target=TARGET_DATABASE,
        )
        return EXIT_CONFIRM_REQUIRED

    try:
        engine = get_app_engine()
        with engine.connect() as connection:
            actual_database = connection.execute(text("SELECT current_database()"))
            if actual_database.scalar_one() != TARGET_DATABASE:
                _emit("FAILED", "CONNECTED_DATABASE_NOT_ALLOWED")
                return EXIT_USAGE
        ports = production_ports(config, engine.begin)
        first = ports.service.send_warning(args.warning_action_id)
        completed_calls = 1
        second = ports.service.send_approval(args.approval_action_id, args.approval_id)
        completed_calls = 2
    except Exception:  # noqa: BLE001 - CLI 경계 밖으로 raw 오류를 내보내지 않는다.
        _emit("FAILED", "SMOKE_EXECUTION_FAILED", completed_calls=completed_calls)
        return EXIT_FAILED

    results = (first, second)
    if any(
        result.response_status != 200
        or result.outcome is not EmailDeliveryOutcome.ACCEPTED
        for result in results
    ):
        _emit(
            "FAILED",
            "SMOKE_RESPONSE_NOT_ACCEPTED",
            completed_calls=2,
            response_codes=[result.response_status for result in results],
        )
        return EXIT_FAILED
    _emit(
        "PASSED",
        "EMAIL_SMOKE_PASSED",
        completed_calls=2,
        recipient_count=1,
        response_codes=[200, 200],
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
