"""V5-C-7.1 private evidence: bounded reads, relative SHA links, no clobber.

This module performs no deployment or external-effect operation. The private
canonical bundle is never an input to public CI artifact upload.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_COMPONENT_BYTES = 16 * 1024 * 1024


class EvidenceError(ValueError):
    """Only code-owned error strings may cross the private CLI boundary."""


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def strict_literals(cls, value: Any) -> Any:
        # Literal[True]/Literal[2] otherwise accepts 1/2.0 even in strict mode.
        if isinstance(value, dict):
            for name, field in cls.model_fields.items():
                if name in value and get_origin(field.annotation) is Literal:
                    if not any(
                        type(value[name]) is type(item) and value[name] == item
                        for item in get_args(field.annotation)
                    ):
                        raise ValueError("EVIDENCE_LITERAL_TYPE_INVALID")
        return value


def relative_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if (
        not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
        or any(part in ("", ".", "..") for part in parts)
        or value.startswith("/")
    ):
        raise EvidenceError("COMPONENT_PATH_INVALID")
    return parts


class Component(EvidenceModel):
    relative_path: str
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        relative_parts(value)
        return value


def canonical_json(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_json(payload: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise EvidenceError("COMPONENT_JSON_INVALID")
            result[key] = value
        return result

    def constant(_value: str) -> Any:
        raise EvidenceError("COMPONENT_JSON_INVALID")

    try:
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EvidenceError("COMPONENT_JSON_INVALID") from exc


def validate_report_root(report: Path, mounted: Path, repository: Path) -> Path:
    """Call before team down; do not silently relocate or chmod user data."""
    resolved = report.resolve(strict=True)
    repo = repository.resolve(strict=True)
    if resolved.is_relative_to(repo):
        code = (
            "LEVEL3_REPORT_ROOT_INSIDE_REPO"
            if report.absolute().is_relative_to(repo)
            else "REPORT_ROOT_SYMLINK_REENTRY"
        )
        raise EvidenceError(code)
    if mounted.resolve(strict=True) != resolved:
        raise EvidenceError("REPORT_ROOT_MOUNT_MISMATCH")
    _check_directory(resolved.stat())
    return resolved


def _check_directory(info: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise EvidenceError("BUNDLE_DIRECTORY_INVALID")


def _check_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_size > MAX_COMPONENT_BYTES
    ):
        raise EvidenceError("COMPONENT_FILE_INVALID")


@contextmanager
def component_parent(root: Path, relative_path: str) -> Iterator[tuple[int, str]]:
    """Resolve via directory FDs, not a check-then-open symlink-prone path."""
    parts = relative_parts(relative_path)
    descriptors: list[int] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptors.append(os.open(root, flags))
        _check_directory(os.fstat(descriptors[-1]))
        for part in parts[:-1]:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
            _check_directory(os.fstat(descriptors[-1]))
        yield descriptors[-1], parts[-1]
    except OSError as exc:
        raise EvidenceError("COMPONENT_IO_INVALID") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_private(root: Path, relative_path: str) -> bytes:
    with component_parent(root, relative_path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            _check_file(os.fstat(stream.fileno()))
            payload = stream.read(MAX_COMPONENT_BYTES + 1)
            if len(payload) > MAX_COMPONENT_BYTES:
                raise EvidenceError("COMPONENT_FILE_INVALID")
            return payload


def resolve_component(root: Path, component: Component) -> Any:
    payload = read_private(root, component.relative_path)
    if digest(payload) != component.sha256:
        raise EvidenceError("COMPONENT_SHA_MISMATCH")
    return parse_json(payload)


def component_ref(root: Path, relative_path: str) -> Component:
    return Component(
        relative_path=relative_path, sha256=digest(read_private(root, relative_path))
    )


def write_private(root: Path, relative_path: str, value: Any) -> Component:
    payload = canonical_json(value) + b"\n"
    if len(payload) > MAX_COMPONENT_BYTES:
        raise EvidenceError("COMPONENT_FILE_INVALID")
    return write_private_bytes(root, relative_path, payload)


def write_private_bytes(root: Path, relative_path: str, payload: bytes) -> Component:
    """A failed/partial write is preserved and fail-closed, never overwritten."""
    if len(payload) > MAX_COMPONENT_BYTES:
        raise EvidenceError("COMPONENT_FILE_INVALID")
    with component_parent(root, relative_path) as (parent, name):
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
        except FileExistsError as exc:
            raise EvidenceError("ARTIFACT_EXISTS") from exc
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    return Component(relative_path=relative_path, sha256=digest(payload))
