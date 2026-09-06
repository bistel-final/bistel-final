"""V65 release qualification receipt: schema and protected mounted-file reader.

Full recount belongs to the qualification issuer and actual preflight. This
runtime reader establishes byte bindings, NOT evidence authenticity.
The caller supplies the independently trusted /reports mount, not a grant path.
Host-owned 0700/0600 files need not be owned by the container reader's UID.
"""

import os
import stat
from pathlib import Path
from typing import Literal

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    Sha256,
    digest,
    parse_json,
)
from app.agent.release_prepared import Attempt, ImageId, Revision, UtcTime, utc

GRANT_NAME = "release-grant.json"
QUALIFICATION_NAME = "qualification-output.json"
MAX_BYTES = 16 * 1024 * 1024


class GrantBundle(EvidenceModel):
    relative_path: Literal["robustness"]
    aggregate_sha256: Sha256
    manifest_sha256: Sha256
    round1_sha256: Sha256
    round1_completion_sha256: Sha256
    prepared_attempt_sha256: Sha256


class GrantPublications(EvidenceModel):
    attempt_json_sha256: Sha256
    golden_flow_sha256: Sha256
    fault_5class_sha256: Sha256


class GrantImages(EvidenceModel):
    backend: ImageId
    frontend: ImageId


class GrantVerdicts(EvidenceModel):
    integrity: Literal["PASS"]
    robustness: Literal["PASS"]
    delivery_integrity: Literal["PASS"]


class ReleaseGrant(EvidenceModel):
    schema_version: Literal["level3-release-grant-v1"]
    attempt_id: Attempt
    R: Revision
    action_policy_version: Literal["MOCK-NOTIFY-V1"]
    bundle: GrantBundle
    publications: GrantPublications
    images: GrantImages
    verdicts: GrantVerdicts
    qualification_output_sha256: Sha256
    issued_by: Literal["enable_production_level3"]
    issued_at: UtcTime


def _require(value):
    if not value:
        raise EvidenceError("RELEASE_GRANT_MISMATCH")


def _directory(fd: int, owner: int):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode))
    _require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == owner)


def _read(parent: int, name: str, owner: int, limit=MAX_BYTES) -> bytes:
    # Every name is code-owned; all ancestors are already opened directory FDs.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        _require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == owner)
        _require(info.st_size <= limit)
        payload = stream.read(limit + 1)
        _require(len(payload) <= limit)
        return payload


def read_release_grant(
    *,
    reports_root: Path,
    expected_attempt_id: str,
    expected_revision: str,
    expected_policy: str,
) -> ReleaseGrant:
    """Fail closed, preserving host issuer ownership and rejecting path indirection.

    reports_root must be the trusted read-only mount established by deployment.
    Its owner anchors child ownership, rather than os.getuid() of container root.
    Never use this rule to relax the host writer's existing owner checks.
    """
    descriptors = []
    try:
        # Validate caller binding BEFORE it becomes a path component.
        from pydantic import TypeAdapter

        TypeAdapter(Attempt).validate_python(expected_attempt_id, strict=True)
        TypeAdapter(Revision).validate_python(expected_revision, strict=True)
        _require(expected_policy == "MOCK-NOTIFY-V1")
        _require(reports_root.is_absolute())
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        # Reject symlink ancestors as well as the final root; don't silently
        # resolve a user-selected path into a different trusted mount.
        _require(reports_root.resolve(strict=True) == reports_root)
        anchor = os.open(reports_root, flags)
        descriptors.append(anchor)
        owner = os.fstat(anchor).st_uid
        _directory(anchor, owner)
        parent = anchor
        for name in ("cm-5.2", expected_attempt_id):
            parent = os.open(name, flags, dir_fd=parent)
            descriptors.append(parent)
            _directory(parent, owner)
        attempt_fd = parent
        bundle_fd = os.open("robustness", flags, dir_fd=parent)
        descriptors.append(bundle_fd)
        _directory(bundle_fd, owner)
        grant_bytes = _read(attempt_fd, GRANT_NAME, owner, 64 * 1024)
        grant = ReleaseGrant.model_validate(parse_json(grant_bytes))
        utc(grant.issued_at)
        _require(
            (grant.attempt_id, grant.R, grant.action_policy_version)
            == (
                expected_attempt_id,
                expected_revision,
                expected_policy,
            )
        )
        expected = {
            "aggregate.json": grant.bundle.aggregate_sha256,
            "MANIFEST.sha256": grant.bundle.manifest_sha256,
            "round1.json": grant.bundle.round1_sha256,
            "round1-completion.json": grant.bundle.round1_completion_sha256,
            "prepared-attempt.json": grant.bundle.prepared_attempt_sha256,
        }
        public = {
            "attempt.json": grant.publications.attempt_json_sha256,
            "golden-flow.json": grant.publications.golden_flow_sha256,
            "fault-5class.json": grant.publications.fault_5class_sha256,
            QUALIFICATION_NAME: grant.qualification_output_sha256,
        }
        files = {}
        for directory, pins in ((bundle_fd, expected), (attempt_fd, public)):
            for name, sha in pins.items():
                raw = _read(directory, name, owner)
                _require(digest(raw) == sha)
                files[name] = raw
        aggregate = parse_json(files["aggregate.json"])
        completion = parse_json(files["round1-completion.json"])
        round1 = parse_json(files["round1.json"])
        prepared = parse_json(files["prepared-attempt.json"])
        _require(aggregate["schema_version"] == "level3-aggregate-v2")
        _require(round1["schema_version"] == "level3-round1-v2")
        _require(prepared["schema_version"] == "level3-prepared-attempt-v2")
        _require(completion["schema_version"] == "level3-round1-completion-v2")
        for value in (aggregate, prepared):
            _require(value["attempt_id"] == grant.attempt_id and value["R"] == grant.R)
        _require(
            round1["reset_attempt_id"] == grant.attempt_id and round1["R"] == grant.R
        )
        _require(round1["action_policy_version"] == grant.action_policy_version)
        _require(
            round1["prepared_attempt"]
            == {
                "relative_path": "prepared-attempt.json",
                "sha256": grant.bundle.prepared_attempt_sha256,
            }
        )
        for role, image_id in grant.images.model_dump().items():
            _require(round1["images"][role]["image_id"] == image_id)
            _require(round1["images"][role]["label_revision"] == grant.R)
        _require(
            prepared["effective_env"]["AGENT_ACTION_POLICY"]
            == grant.action_policy_version
        )
        _require(
            aggregate["robustness_verdict"] == aggregate["delivery_integrity"] == "PASS"
        )
        _require(
            aggregate["round1"]
            == {
                "relative_path": "round1.json",
                "sha256": grant.bundle.round1_sha256,
            }
        )
        _require(
            aggregate["round1_completion"]
            == {
                "relative_path": "round1-completion.json",
                "sha256": grant.bundle.round1_completion_sha256,
            }
        )
        _require(completion["final_status"] == "PASS")
        _require(completion["cm52_attempt_id"] == grant.attempt_id)
        _require(completion["round1"] == aggregate["round1"])
        for field, value in (
            ("attempt_artifact_sha256", grant.publications.attempt_json_sha256),
            ("golden_flow_sha256", grant.publications.golden_flow_sha256),
            ("fault_5class_sha256", grant.publications.fault_5class_sha256),
        ):
            _require(completion[field] == value)
        # Re-read all pins after parsing; do not cache mutable artifact reads.
        for directory, pins in ((bundle_fd, expected), (attempt_fd, public)):
            for name in pins:
                _require(_read(directory, name, owner) == files[name])
        _require(_read(attempt_fd, GRANT_NAME, owner, 64 * 1024) == grant_bytes)
        return grant
    except Exception:
        # File paths, payloads and OS errors must never enter API diagnostics.
        raise EvidenceError("RELEASE_GRANT_MISMATCH") from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def release_grant_matches(**kwargs) -> bool:
    """Fail-closed runtime/readback adapter; the fence still owns admission."""
    try:
        read_release_grant(**kwargs)
        return True
    except EvidenceError:
        return False
