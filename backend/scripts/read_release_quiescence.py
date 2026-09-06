"""No writes or LLM calls; no DB URL overrides."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    if sys.argv[1:] if argv is None else argv:
        print('{"status":"FAIL","code":"RELEASE_QUIESCENCE_ARGUMENT_INVALID"}')
        return 2
    try:
        from app.agent.release_quiescence import read_quiescence
        from app.common.db import get_app_engine

        print(json.dumps(read_quiescence(get_app_engine())))
        return 0
    except Exception:
        print('{"status":"FAIL","code":"RELEASE_QUIESCENCE_UNAVAILABLE"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
