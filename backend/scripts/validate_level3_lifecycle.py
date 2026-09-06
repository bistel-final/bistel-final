"""Read-only lifecycle inspection. This is NOT the production enable gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_artifacts import EvidenceError  # noqa: E402
from app.agent.release_lifecycle import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    authorize_transition,
    classify_state,
    read_lifecycle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=["RESUME_WORKLOAD", "PUBLISH", "ABORT", "RECOVER"]
    )
    args = parser.parse_args(argv)
    try:
        state = classify_state(read_lifecycle(args.bundle_root))
        if args.mode:
            authorize_transition(state, args.mode)
    except (ValueError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "code": str(exc)
                    if isinstance(exc, EvidenceError)
                    else "LIFECYCLE_SCHEMA_INVALID",
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "state": state,
                "allowed_modes": sorted(ALLOWED_TRANSITIONS[state]),
                "warning": "production is DOWN" if state == "PREPARED" else None,
                "inspection_only": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
