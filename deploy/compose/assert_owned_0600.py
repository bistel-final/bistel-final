#!/usr/bin/env python3
"""CM-5.2 artifact가 실행자 소유 regular 0600 파일인지 확인한다."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def valid(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and os.access(path, os.R_OK)
    )


def main(argv: list[str] | None = None) -> int:
    paths = [Path(value) for value in (sys.argv[1:] if argv is None else argv)]
    if not paths or any(not valid(path) for path in paths):
        print("ARTIFACT_OWNER_MODE_INVALID", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
