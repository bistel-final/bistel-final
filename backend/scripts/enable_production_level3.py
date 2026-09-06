"""Explicit operator transition; never treats preflight exit 0 alone as PASS.

Use --restore-level2 after an interrupted transition. This command performs
real service recreation when invoked; it does not run Agent jobs or send mail.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import TypeAdapter  # noqa: E402

from app.agent.release_fence import ProductionFence  # noqa: E402
from app.agent.release_prepared import Attempt, Revision  # noqa: E402
from app.agent.release_production import ProductionPorts  # noqa: E402
from app.agent.release_transition import operator_signals, transition  # noqa: E402
from app.agent.u10_cli import Parser, failure_code, pins  # noqa: E402


def main(argv=None, *, ports_factory=ProductionPorts, fence_factory=ProductionFence):
    parser = Parser(description=__doc__)
    for name in (
        "repository",
        "env-file",
        "report-root",
        "artifact",
        "evaluation-receipt",
        "benchmark",
        "robustness-published-root",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--robustness-artifact", type=Path)
    parser.add_argument("--benchmark-sha256", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--image-id", action="append", required=True)
    parser.add_argument("--restore-level2", action="store_true")
    try:
        args = parser.parse_args(argv)
        TypeAdapter(Revision).validate_python(args.revision, strict=True)
        TypeAdapter(Attempt).validate_python(args.attempt_id, strict=True)
        if not args.attempt_id.endswith(args.revision[:12]):
            parser.error("binding")
        if args.robustness_artifact is None and not args.restore_level2:
            parser.error("required")
        base = []
        for key in (
            "repository",
            "artifact",
            "evaluation_receipt",
            "benchmark",
            "benchmark_sha256",
        ):
            base.extend(["--" + key.replace("_", "-"), str(getattr(args, key))])
        ports = ports_factory(
            repository=args.repository,
            env_file=args.env_file,
            report_root=args.report_root,
            artifact=args.robustness_artifact,
            published_root=args.robustness_published_root,
            revision=args.revision,
            attempt_id=args.attempt_id,
            image_ids=pins(args.image_id, image=True),
            preflight_arguments=base,
            action_policy="MOCK-NOTIFY-V1",
        )
        with operator_signals():
            result = transition(
                ports=ports,
                fence=fence_factory(args.report_root, args.revision, args.attempt_id),
                revision=args.revision,
                attempt_id=args.attempt_id,
                restore_only=args.restore_level2,
            )
    except Exception as exc:
        result = {"status": "FAIL", "code": failure_code(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
