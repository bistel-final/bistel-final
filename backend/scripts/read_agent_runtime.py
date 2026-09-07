"""Backend container 안에서만 실행하는 allowlist JSON readback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.runtime_readback import (  # noqa: E402
    PROFILES,
    collect_readback,
    validate_readback,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    args = parser.parse_args(argv)
    try:
        payload = collect_readback()
        validate_readback(payload, args.profile)
    except ValueError as exc:
        print(json.dumps({"status": "FAIL", "code": str(exc)}))
        return 1
    print(
        json.dumps(
            {"status": "PASS", "profile": args.profile, **payload}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
