"""Private container readback for Stage2; stdout contains exact recipients."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_context import collect_current_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    # No profile/DSN/env/recipient override or public-output mode.
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(
            json.dumps(
                {"status": "FAIL", "code": "PREPARATION_CONTEXT_ARGUMENT_INVALID"}
            )
        )
        return 2
    try:
        payload = collect_current_context()
    except Exception:
        print(json.dumps({"status": "FAIL", "code": "PREPARATION_CONTEXT_READ_FAILED"}))
        return 1
    print(payload.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
