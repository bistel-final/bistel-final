"""Read current non-secret model hashes from a pinned backend; no LLM call."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.release_model import collect_model_context  # noqa: E402


def main(argv=None):
    if sys.argv[1:] if argv is None else argv:
        print(json.dumps({"status": "FAIL", "code": "RELEASE_MODEL_ARGUMENT_INVALID"}))
        return 2
    try:
        result = collect_model_context()
        print(result.model_dump_json())
        return 0
    except Exception:
        print(
            json.dumps({"status": "FAIL", "code": "RELEASE_MODEL_CONTEXT_UNAVAILABLE"})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
