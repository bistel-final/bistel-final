"""Read-only U10 preflight. Bundle A: production remains blocked (NOT_RUN axes)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.u10_cli import Parser, failure_code, pins  # noqa: E402
from app.agent.u10_deployment import _now, _stamp  # noqa: E402
from app.agent.u10_images import docker_inspect  # noqa: E402
from app.agent.u10_integrity import verify_preflight_integrity  # noqa: E402
from app.agent.u10_preflight_report import preflight_report  # noqa: E402
from app.agent.u10_readiness import fetch_gateway  # noqa: E402
from app.agent.u10_runtime import docker_readback  # noqa: E402


def main(
    argv=None,
    *,
    inspect=docker_inspect,
    read=docker_readback,
    fetch=fetch_gateway,
    clock=_now,
) -> int:
    parser = Parser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["production_level2", "e2e_level3", "production_level3"],
        required=True,
    )
    parser.add_argument(
        "--phase", choices=["pre_u9", "post_start_pre_enable"], required=True
    )
    for name in ("repository", "artifact", "evaluation-receipt", "benchmark"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--benchmark-sha256", required=True)
    parser.add_argument("--image-id", action="append", default=[], required=True)
    parser.add_argument("--container-id", action="append", default=[], required=True)
    parser.add_argument("--expected-attempt-id")
    args = None
    try:
        args = parser.parse_args(argv)
        result = verify_preflight_integrity(
            repository=args.repository,
            artifact=args.artifact,
            evaluation_receipt=args.evaluation_receipt,
            benchmark=args.benchmark,
            pinned_benchmark_sha256=args.benchmark_sha256,
            profile=args.profile,
            phase=args.phase,
            expected_image_ids=pins(args.image_id, image=True),
            container_ids=pins(args.container_id, image=False),
            expected_attempt_id=args.expected_attempt_id,
            inspect=inspect,
            read=read,
            fetch=fetch,
            clock=clock,
        )
        report = preflight_report(
            result,
            profile=args.profile,
            phase=args.phase,
            checked_at=result.checked_at,
            failed_checks=[],
        )
    except Exception as exc:
        report = preflight_report(
            None,
            profile=args.profile if args else None,
            phase=args.phase if args else None,
            checked_at=_stamp(_now()),
            failed_checks=[failure_code(exc)],
        )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["integrity"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
