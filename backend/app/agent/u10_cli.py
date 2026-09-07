"""Shared CLI parsing and non-secret error projection for U10 commands."""

import argparse
import re

from app.agent.release_artifacts import EvidenceError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default includes user input and local paths on stderr.
        raise EvidenceError("U10_CLI_ARGUMENT_INVALID")


def failure_code(error: Exception) -> str:
    if isinstance(error, EvidenceError) and re.fullmatch(
        r"[A-Z][A-Z0-9_]{0,95}", str(error)
    ):
        return str(error)
    return "U10_CLI_FAILED"


def pins(values: list[str], *, image: bool) -> dict[str, str]:
    result = {}
    pattern = r"sha256:[0-9a-f]{64}" if image else r"[0-9a-f]{64}"
    for value in values:
        role, separator, identifier = value.partition("=")
        if (
            not separator
            or role not in ("backend", "frontend", "runner")
            or role in result
            or not re.fullmatch(pattern, identifier)
        ):
            raise EvidenceError("U10_CLI_PIN_INVALID")
        result[role] = identifier
    return result
