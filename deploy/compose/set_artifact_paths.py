#!/usr/bin/env python3
""".env.team의 Agent 평가 artifact 경로 두 값만 원자적으로 조회·교체한다."""

from __future__ import annotations

import os
import re
import stat
import sys
import uuid
from pathlib import Path

FAULT_KEY = "AGENT_FAULT_EVAL_ARTIFACT_PATH"
GOLDEN_KEY = "AGENT_GOLDEN_FLOW_SUMMARY_PATH"
ALLOWED_KEYS = (FAULT_KEY, GOLDEN_KEY)
ATTEMPT = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}"
VALUE_PATTERNS = {
    FAULT_KEY: re.compile(rf"^/reports/cm-5\.2/{ATTEMPT}/fault-5class\.json$"),
    GOLDEN_KEY: re.compile(rf"^/reports/cm-5\.2/{ATTEMPT}/golden-flow\.json$"),
}


class UpdateError(RuntimeError):
    pass


def _read_lines(path: Path) -> tuple[list[bytes], int]:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UpdateError
        return path.read_bytes().splitlines(keepends=True), stat.S_IMODE(
            metadata.st_mode
        )
    except OSError as exc:
        raise UpdateError from exc


def _values(lines: list[bytes]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        for key in ALLOWED_KEYS:
            prefix = f"{key}=".encode()
            if line.startswith(prefix):
                if key in found:
                    raise UpdateError
                try:
                    found[key] = line[len(prefix) :].rstrip(b"\r\n").decode("utf-8")
                except UnicodeError as exc:
                    raise UpdateError from exc
    return found


def get_value(path: Path, key: str) -> str:
    if key not in ALLOWED_KEYS:
        raise UpdateError
    lines, _mode = _read_lines(path)
    return _values(lines).get(key, "")


def _validated(key: str, value: str) -> bytes:
    if "\n" in value or "\r" in value:
        raise UpdateError
    if value and not VALUE_PATTERNS[key].fullmatch(value):
        raise UpdateError
    return value.encode("utf-8")


def set_values(path: Path, fault_value: str, golden_value: str) -> None:
    replacements = {
        FAULT_KEY: _validated(FAULT_KEY, fault_value),
        GOLDEN_KEY: _validated(GOLDEN_KEY, golden_value),
    }
    lines, mode = _read_lines(path)
    existing = _values(lines)
    output: list[bytes] = []
    for line in lines:
        replaced = False
        for key, value in replacements.items():
            prefix = f"{key}=".encode()
            if line.startswith(prefix):
                ending = (
                    b"\r\n"
                    if line.endswith(b"\r\n")
                    else (b"\n" if line.endswith(b"\n") else b"")
                )
                output.append(prefix + value + ending)
                replaced = True
                break
        if not replaced:
            output.append(line)
    missing = [key for key in ALLOWED_KEYS if key not in existing]
    if missing:
        if output and not output[-1].endswith((b"\n", b"\r")):
            output[-1] += b"\n"
        output.extend(f"{key}=".encode() + replacements[key] + b"\n" for key in missing)

    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(b"".join(output))
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 3 and args[0] == "--get":
            print(get_value(Path(args[1]), args[2]))
            return 0
        if len(args) == 3 and args[0] != "--get":
            set_values(Path(args[0]), args[1], args[2])
            return 0
    except (OSError, UpdateError):
        pass
    print("ARTIFACT_PATH_UPDATE_INVALID", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
