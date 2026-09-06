"""Private nine-payload seal and deterministic, non-canonical public projection.

No deployment, approval, service access or upload. A manifest seals exact bytes,
not the authenticity of the original live capture. Recount remains mandatory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.agent.release_aggregate import (
    PAYLOAD_NAMES,
    Aggregate,
    payload_names,
    verify_aggregate,
)
from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    component_parent,
    digest,
    parse_json,
    read_private,
    validate_report_root,
    write_private,
    write_private_bytes,
)
from app.agent.release_lifecycle import lifecycle_lock
from app.agent.release_prepared import (
    Attempt,
    Revision,
    parse_prepared,
)
from app.agent.release_round import RoundAssessment

PRIVATE_PAYLOAD_NAMES = tuple(sorted((*PAYLOAD_NAMES, "aggregate.json")))
MANIFEST_NAME = "MANIFEST.sha256"
PUBLIC_NAME = "robustness-public.json"
PUBLIC_FIELDS = (
    "R",
    "attempt_id",
    "qualification_scope",
    "round_count",
    "run_count",
    "repeatability",
    "recipient_hash",
    "recipient_hash_version",
    "recipient_count",
    "robustness_verdict",
    "delivery_integrity",
    "batch_summary",
)
DERIVATION_RULES = {
    "version": "level3-public-derivation-v1",
    "schema_version": "level3-robustness-public-v1",
    "encoding": "UTF-8/canonical-json/sorted-keys/compact/LF",
    "private_payloads": list(PRIVATE_PAYLOAD_NAMES),
    "manifest": "sha256/two-spaces/relative-path/codepoint-order/LF/no-self/no-lock",
    "projection": list(PUBLIC_FIELDS),
    "scan": "all-json-strings/email/url/credential-markers/exact-recipients-v1",
}


class PublicReport(EvidenceModel):
    schema_version: Literal["level3-robustness-public-v1"]
    R: Revision
    attempt_id: Attempt
    qualification_scope: Literal["SINGLE_STAGE2_BATCH"]
    round_count: Literal[1]
    run_count: Literal[12]
    repeatability: Literal["NOT_MEASURED"]
    recipient_hash: Sha256
    recipient_hash_version: Literal[2]
    recipient_count: int = Field(ge=1)
    private_manifest_sha256: Sha256
    robustness_verdict: Literal["PASS", "FAIL"]
    delivery_integrity: Literal["PASS", "FAIL"]
    batch_summary: RoundAssessment
    derivation_rules_sha256: Sha256


class PublicReportV2(PublicReport):
    schema_version: Literal["level3-robustness-public-v2"]
    action_policy_version: Literal["MOCK-NOTIFY-V1"]


def derivation_rules(is_mock):
    if not is_mock:
        return DERIVATION_RULES
    return {
        **DERIVATION_RULES,
        "version": "level3-public-derivation-v2",
        "schema_version": "level3-robustness-public-v2",
        "private_payloads": sorted(
            (
                *PRIVATE_PAYLOAD_NAMES,
                "mock-sources.round1.json",
                "mock-results.round1.json",
            )
        ),
        "projection": [*PUBLIC_FIELDS, "action_policy_version"],
    }


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceError(code)


def _names(root: Path) -> set[str]:
    with component_parent(root, MANIFEST_NAME) as (parent, _):
        return set(os.listdir(parent))


def _snapshot(root: Path, *, sealed: bool) -> dict[str, bytes]:
    _require(
        set(PRIVATE_PAYLOAD_NAMES).issubset(_names(root)), "SEAL_PAYLOAD_SET_INVALID"
    )
    required = (
        set(payload_names(root))
        | {"aggregate.json"}
        | ({MANIFEST_NAME} if sealed else set())
    )
    _require(
        _names(root) - {".lifecycle.lock"} == required,
        "SEAL_PAYLOAD_SET_INVALID",
    )
    return {name: read_private(root, name) for name in sorted(required)}


def _manifest(files: dict[str, bytes]) -> bytes:
    return "".join(
        f"{digest(files[name])}  {name}\n"
        for name in sorted(set(files) - {MANIFEST_NAME})
    ).encode("utf-8")


def _recount(
    root: Path, published_root: Path, revision: str, attempt: str
) -> Aggregate:
    return verify_aggregate(
        root / "aggregate.json",
        published_root=published_root,
        expected_revision=revision,
        expected_attempt_id=attempt,
    )


def seal_bundle(
    *,
    root: Path,
    repository: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
    lifecycle_lock_fd: int | None = None,
) -> Component:
    """One O_EXCL manifest under the lifecycle lock; never overwrite or repair."""
    validate_report_root(root, root, repository)
    with lifecycle_lock(root, inherited_fd=lifecycle_lock_fd):
        before = _snapshot(root, sealed=False)
        _recount(root, published_root, expected_revision, expected_attempt_id)
        _require(before == _snapshot(root, sealed=False), "SEAL_EVIDENCE_DRIFT")
        return write_private_bytes(root, MANIFEST_NAME, _manifest(before))


def verify_seal(
    *,
    root: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
) -> tuple[Aggregate, dict[str, bytes]]:
    """Read only; no lock creation. Exact closure, raw hashes and transitive recount."""
    before = _snapshot(root, sealed=True)
    _require(before[MANIFEST_NAME] == _manifest(before), "SEAL_MANIFEST_MISMATCH")
    result = _recount(root, published_root, expected_revision, expected_attempt_id)
    _require(before == _snapshot(root, sealed=True), "SEAL_EVIDENCE_DRIFT")
    return result, before


def copy_sealed_bundle(
    *,
    root: Path,
    destination: Path,
    repository: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
) -> Component:
    """Protected whole-bundle copy. Partial failures stay visible; no rollback.

    Destination must already exist, be private, empty and outside both source
    and repository. The manifest is copied last and never includes either lock.
    """
    source = validate_report_root(root, root, repository)
    target = validate_report_root(destination, destination, repository)
    _require(
        not source.is_relative_to(target) and not target.is_relative_to(source),
        "SEAL_COPY_PATH_INVALID",
    )
    with lifecycle_lock(root), lifecycle_lock(destination):
        _require(
            _names(destination) == {".lifecycle.lock"},
            "SEAL_COPY_DESTINATION_NOT_EMPTY",
        )
        args = dict(
            published_root=published_root,
            expected_revision=expected_revision,
            expected_attempt_id=expected_attempt_id,
        )
        _, files = verify_seal(root=root, **args)
        for name in sorted(set(files) - {MANIFEST_NAME}):
            write_private_bytes(destination, name, files[name])
        _require(files == _snapshot(root, sealed=True), "SEAL_EVIDENCE_DRIFT")
        reference = write_private_bytes(
            destination, MANIFEST_NAME, files[MANIFEST_NAME]
        )
        _, copied = verify_seal(root=destination, **args)
        _require(files == copied, "SEAL_COPY_MISMATCH")
        _require(files == _snapshot(root, sealed=True), "SEAL_EVIDENCE_DRIFT")
        return reference


def _scan(value: dict, recipients: list[str]) -> None:
    """Allowlisted, recounted projection plus fail-closed string scanning.

    This is defense in depth, not a general-purpose secret detector. No raw
    workflow, arbitrary prompt, credential or delivery payload is projected.
    """
    text = canonical_json(value).decode("utf-8")
    _require(
        not any(address.casefold() in text.casefold() for address in recipients),
        "PUBLIC_RECIPIENT_DETECTED",
    )
    pattern = re.compile(
        r"@|https?://|postgres(?:ql)?://|-----BEGIN|"
        r"(?i:password|passwd|api[_-]?key|authorization|bearer\s|secret|credential)"
    )
    _require(pattern.search(text) is None, "PUBLIC_SECRET_DETECTED")


def derive_public(
    *,
    root: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
) -> PublicReport:
    result, files = verify_seal(
        root=root,
        published_root=published_root,
        expected_revision=expected_revision,
        expected_attempt_id=expected_attempt_id,
    )
    prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
    is_mock = prepared.schema_version == "level3-prepared-attempt-v2"
    data = {key: result.model_dump(mode="json")[key] for key in PUBLIC_FIELDS}
    data.update(
        schema_version="level3-robustness-public-v2"
        if is_mock
        else "level3-robustness-public-v1",
        **({"action_policy_version": "MOCK-NOTIFY-V1"} if is_mock else {}),
        private_manifest_sha256=digest(files[MANIFEST_NAME]),
        derivation_rules_sha256=digest(canonical_json(derivation_rules(is_mock))),
    )
    _scan(data, prepared.recipient.canonical_addresses)
    report = (PublicReportV2 if is_mock else PublicReport).model_validate(data)
    _require(files == _snapshot(root, sealed=True), "SEAL_EVIDENCE_DRIFT")
    return report


def emit_public(
    *,
    public_root: Path,
    repository: Path,
    root: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
) -> tuple[Component, PublicReport]:
    """Emit one public file to a pre-created 0700 revision directory, no upload."""
    validate_report_root(root, root, repository)
    expected = (
        repository.resolve(strict=True) / "output" / "v5-c-7.1" / expected_revision
    )
    _require(
        public_root.absolute() == expected
        and public_root.resolve(strict=True) == expected,
        "PUBLIC_OUTPUT_PATH_INVALID",
    )
    result = derive_public(
        root=root,
        published_root=published_root,
        expected_revision=expected_revision,
        expected_attempt_id=expected_attempt_id,
    )
    return write_private(public_root, PUBLIC_NAME, result), result


def verify_public(
    *,
    path: Path,
    root: Path,
    published_root: Path,
    expected_revision: str,
    expected_attempt_id: str,
) -> PublicReport:
    _require(path.name == PUBLIC_NAME, "PUBLIC_OUTPUT_PATH_INVALID")
    raw = read_private(path.parent, path.name)
    try:
        value = parse_json(raw)
        declared = (
            PublicReportV2
            if value.get("schema_version") == "level3-robustness-public-v2"
            else PublicReport
        ).model_validate(value)
    except ValidationError:
        raise EvidenceError("PUBLIC_SCHEMA_INVALID") from None
    actual = derive_public(
        root=root,
        published_root=published_root,
        expected_revision=expected_revision,
        expected_attempt_id=expected_attempt_id,
    )
    prepared = parse_prepared(parse_json(read_private(root, "prepared-attempt.json")))
    _scan(declared.model_dump(mode="json"), prepared.recipient.canonical_addresses)
    _require(declared == actual, "PUBLIC_DERIVATION_MISMATCH")
    _require(raw == canonical_json(actual) + b"\n", "PUBLIC_ENCODING_INVALID")
    _require(raw == read_private(path.parent, path.name), "PUBLIC_EVIDENCE_DRIFT")
    return actual
