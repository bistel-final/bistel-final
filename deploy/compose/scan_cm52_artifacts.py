#!/usr/bin/env python3
"""CM-5.2 attempt 산출물에서 secret·질문·SQL 원문을 값 출력 없이 차단한다."""

from __future__ import annotations

import argparse
import re
import stat
import sys
from pathlib import Path

from preflight_team_env import SECRET_KEYS, parse_env_file

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.e2e_analytics_questions import QUESTIONS  # noqa: E402

EXTRA_SECRET_KEYS = frozenset(
    {
        "NEO4J_URI",
        "LLM_BASE_URL",
        "AGENT_EMAIL_RECIPIENTS",
    }
)
# Stage 2의 현재 입력·출력 계약은 질문 원문을 어떤 JSON object key에도 쓰지 않는다.
# digest receipt는 ``[[nl_query_log_id, sha256], ...]`` 배열이다. 따라서 ``question``
# key가 추가되면 신규 정상 스키마가 아니라 원문 누수 가능성이므로 의도적으로 차단한다.
FORBIDDEN_PATTERNS = (
    re.compile(rb"authorization\s*[:=]", re.IGNORECASE),
    re.compile(rb"bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(rb"cookie\s*[:=]", re.IGNORECASE),
    re.compile(rb'"generated_sql"\s*:\s*"(?!null)', re.IGNORECASE),
    re.compile(rb'"question"\s*:\s*"', re.IGNORECASE),
)


def scan(root: Path, env_file: Path) -> bool:
    try:
        root_metadata = root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
            return False
        values, findings = parse_env_file(env_file)
        if findings:
            return False
        forbidden_values = [
            values[key].encode("utf-8")
            for key in SECRET_KEYS | EXTRA_SECRET_KEYS
            if values.get(key)
        ] + [question.encode("utf-8") for question in QUESTIONS]
        for path in root.rglob("*"):
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return False
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if path.name.startswith(".env") or path.suffix.lower() == ".env":
                return False
            raw = path.read_bytes()
            if any(value in raw for value in forbidden_values):
                return False
            if any(pattern.search(raw) for pattern in FORBIDDEN_PATTERNS):
                return False
    except (OSError, UnicodeError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.root.is_absolute() or not scan(args.root, args.env_file):
        print("EVIDENCE_SECRET_SCAN_FAILED", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
