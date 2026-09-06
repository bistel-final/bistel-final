"""Offline PASS-completion lineage and published-byte verification.

This is a component check, not aggregate robustness or deployment permission.
The aggregate caller must independently validate round1 and its run population.
"""

from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    digest,
    parse_json,
    read_private,
)
from app.agent.release_lifecycle import (
    LifecycleClaim,
    classify_state,
    parse_completion,
    parse_outcome,
    read_lifecycle,
)
from app.agent.release_prepared import (
    Attempt,
    Revision,
    parse_prepared,
    utc,
)


class CompletionVerification(EvidenceModel):
    completion_integrity: Literal["PASS"]
    attempt_id: Attempt
    evaluated_revision: Revision
    round1: Component
    round1_completion: Component


def verify_completion(
    *,
    root: Path,
    published_root: Path,
    round1: Component,
    round1_completion: Component,
    prepared_attempt: Component,
    evaluated_revision: str,
    expected_attempt_id: str,
    captured_at: str,
) -> CompletionVerification:
    """Resolve completion → claims/outcome → same prepared and actual publication.

    published_root is explicitly supplied by the caller, never taken from an
    artifact-controlled absolute path. Only three code-owned filenames are read.
    Claims' historic TTL is irrelevant at publish; a HELD lineage is required.
    """
    try:
        files = read_lifecycle(root)
        for component, name in (
            (round1, "round1.json"),
            (round1_completion, "round1-completion.json"),
            (prepared_attempt, "prepared-attempt.json"),
        ):
            if (
                component.relative_path != name
                or name not in files
                or digest(files[name]) != component.sha256
            ):
                raise EvidenceError("COMPLETION_COMPONENT_MISMATCH")
        if classify_state(files) != "TERMINAL":
            raise EvidenceError("COMPLETION_TERMINAL_REQUIRED")
        prepared = parse_prepared(parse_json(files["prepared-attempt.json"]))
        completion = parse_completion(parse_json(files["round1-completion.json"]))
        resume = LifecycleClaim.model_validate(
            parse_json(files["lifecycle-claim.resume_workload.json"])
        )
        held = parse_outcome(
            parse_json(files["lifecycle-outcome.resume_workload.json"])
        )
        publish = LifecycleClaim.model_validate(
            parse_json(files["lifecycle-claim.publish.json"])
        )
        if (
            completion.final_status != "PASS"
            or completion.round1 != round1
            or prepared.R != evaluated_revision
            or completion.cm52_attempt_id != prepared.attempt_id
            or prepared.attempt_id != expected_attempt_id
            or resume.prepared_attempt != prepared_attempt
            or publish.prepared_attempt != prepared_attempt
            or held.outcome != "HELD"
        ):
            raise EvidenceError("COMPLETION_BINDING_MISMATCH")
        if not (
            utc(resume.claimed_at)
            <= utc(captured_at)
            <= utc(held.finished_at)
            <= utc(publish.claimed_at)
            <= utc(completion.completed_at)
        ):
            raise EvidenceError("COMPLETION_TIME_INVALID")
        publications = {
            "attempt.json": completion.attempt_artifact_sha256,
            "golden-flow.json": completion.golden_flow_sha256,
            "fault-5class.json": completion.fault_5class_sha256,
        }
        first = {name: read_private(published_root, name) for name in publications}
        if any(digest(raw) != publications[name] for name, raw in first.items()):
            raise EvidenceError("COMPLETION_PUBLICATION_MISMATCH")
        # No new lifecycle file or changed public bytes during this observation.
        if read_lifecycle(root) != files or any(
            read_private(published_root, name) != raw for name, raw in first.items()
        ):
            raise EvidenceError("COMPLETION_EVIDENCE_DRIFT")
        return CompletionVerification(
            completion_integrity="PASS",
            attempt_id=expected_attempt_id,
            evaluated_revision=evaluated_revision,
            round1=round1,
            round1_completion=round1_completion,
        )
    except ValidationError:
        raise EvidenceError("COMPLETION_SCHEMA_INVALID") from None
    except KeyError:
        raise EvidenceError("COMPLETION_COMPONENT_MISMATCH") from None
