"""CM-5.2에서 실행할 고정 Text2SQL 질문의 원문 비노출 digest receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path

QUESTIONS: tuple[str, ...] = (
    "가장 최근에 발생한 TRACE 알람 5건의 alarm_id를 보여줘",
    "일자별 TRACE 알람 발생 추이를 보여줘",
    "TRACE 알람 측정값(value)의 분포를 히스토그램으로 보여줘",
)


def normalize_question(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def digest(value: str) -> str:
    return hashlib.sha256(normalize_question(value).encode("utf-8")).hexdigest()


def expected_digests(ids: Sequence[int]) -> list[list[int | str]]:
    if (
        len(ids) != len(QUESTIONS)
        or len(set(ids)) != len(ids)
        or any(i < 1 for i in ids)
    ):
        raise ValueError("ANALYTICS_IDS_INVALID")
    return [
        [identifier, digest(question)]
        for identifier, question in zip(ids, QUESTIONS, strict=True)
    ]


def _write_exclusive(path: Path, payload: object) -> str:
    if not path.is_absolute():
        raise ValueError("OUTPUT_PATH_INVALID")
    raw = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return hashlib.sha256(raw).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        identifiers = [int(value) for value in args.ids.split(",")]
        payload = expected_digests(identifiers)
        _write_exclusive(args.output, payload)
    except (OSError, ValueError):
        print("ANALYTICS_DIGEST_RECEIPT_FAILED", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
