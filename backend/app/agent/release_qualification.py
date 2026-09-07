"""V65 host-only qualification and no-clobber grant issuance under CLOSED fence.

Pure file recount precedes actual L3 recreation/preflight. This never claims that
an L2 runtime passed L3 checks. No mail, Kafka, reset, container or network calls.
"""

import os
from pathlib import Path

from app.agent.investigation_budget import persisted_profile, resolve_run_budget
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    parse_json,
    read_private,
    validate_report_root,
    write_private,
)
from app.agent.release_budget import profile_fields
from app.agent.release_fence import _read as read_fence
from app.agent.release_grant import (
    GRANT_NAME,
    QUALIFICATION_NAME,
    ReleaseGrant,
    read_release_grant,
)
from app.agent.release_seal import verify_seal
from app.agent.u10_evaluation import verify_evaluation

_UNSPECIFIED_PROFILE = object()


def qualify_release(
    *,
    root: Path,
    published_root: Path,
    repository: Path,
    expected_revision: str,
    expected_attempt_id: str,
    image_ids: dict,
    artifact: Path,
    evaluation_receipt: Path,
    benchmark: Path,
    pinned_benchmark_sha256: str,
    expected_investigation_budget_profile: str | None | object = _UNSPECIFIED_PROFILE,
) -> dict:
    """Recount current input bytes; caller-supplied verdicts are never accepted."""
    validate_report_root(root, root, repository)
    validate_report_root(published_root, published_root, repository)
    if root != published_root / "robustness":
        raise EvidenceError("RELEASE_GRANT_LAYOUT_INVALID")
    aggregate, files = verify_seal(
        root=root,
        published_root=published_root,
        expected_revision=expected_revision,
        expected_attempt_id=expected_attempt_id,
    )
    if aggregate.schema_version != "level3-aggregate-v2" or (
        aggregate.robustness_verdict,
        aggregate.delivery_integrity,
    ) != ("PASS", "PASS"):
        raise EvidenceError("RELEASE_THREE_GATE_DENIED")
    round1 = parse_json(files["round1.json"])
    prepared = parse_json(files["prepared-attempt.json"])
    bound_profile = persisted_profile(3, round1)
    if expected_investigation_budget_profile is not _UNSPECIFIED_PROFILE:
        resolve_run_budget(3, expected_investigation_budget_profile)
        if bound_profile != expected_investigation_budget_profile:
            raise EvidenceError("RELEASE_QUALIFICATION_BINDING_MISMATCH")
    if (
        round1["action_policy_version"] != "MOCK-NOTIFY-V1"
        or prepared["effective_env"]["AGENT_ACTION_POLICY"] != "MOCK-NOTIFY-V1"
        or persisted_profile(3, prepared["effective_env"]) != bound_profile
        or set(image_ids) != {"backend", "frontend"}
        or any(
            round1["images"][role]["image_id"] != value
            for role, value in image_ids.items()
        )
        or round1["images"] != prepared["images"]
    ):
        raise EvidenceError("RELEASE_QUALIFICATION_BINDING_MISMATCH")
    # Runner identity was pinned in the captured round and prepared state;
    # BF above are independently pinned operator production image IDs.
    evaluation = verify_evaluation(
        artifact=artifact,
        evaluation_receipt=evaluation_receipt,
        benchmark=benchmark,
        pinned_benchmark_sha256=pinned_benchmark_sha256,
    )
    if evaluation.receipt.evaluated_revision != expected_revision:
        raise EvidenceError("RELEASE_QUALIFICATION_BINDING_MISMATCH")
    public = {
        name: read_private(published_root, name)
        for name in ("attempt.json", "golden-flow.json", "fault-5class.json")
    }
    output = dict(
        **profile_fields(bound_profile),
        schema_version="level3-qualification-output-v1",
        attempt_id=expected_attempt_id,
        R=expected_revision,
        action_policy_version="MOCK-NOTIFY-V1",
        bundle=dict(
            relative_path="robustness",
            aggregate_sha256=digest(files["aggregate.json"]),
            manifest_sha256=digest(files["MANIFEST.sha256"]),
            round1_sha256=digest(files["round1.json"]),
            round1_completion_sha256=digest(files["round1-completion.json"]),
            prepared_attempt_sha256=digest(files["prepared-attempt.json"]),
        ),
        publications=dict(
            attempt_json_sha256=digest(public["attempt.json"]),
            golden_flow_sha256=digest(public["golden-flow.json"]),
            fault_5class_sha256=digest(public["fault-5class.json"]),
        ),
        images=image_ids,
        verdicts=dict(integrity="PASS", robustness="PASS", delivery_integrity="PASS"),
        u10=dict(
            artifact_sha256=evaluation.artifact_sha256,
            receipt_sha256=evaluation.receipt_sha256,
            benchmark_sha256=evaluation.benchmark_sha256,
            agent_verdict=evaluation.agent_verdict,
        ),
    )
    # A second recount prevents a cached successful first read hiding drift.
    after, again = verify_seal(
        root=root,
        published_root=published_root,
        expected_revision=expected_revision,
        expected_attempt_id=expected_attempt_id,
    )
    if (
        after != aggregate
        or again != files
        or any(read_private(published_root, n) != raw for n, raw in public.items())
        or verify_evaluation(
            artifact=artifact,
            evaluation_receipt=evaluation_receipt,
            benchmark=benchmark,
            pinned_benchmark_sha256=pinned_benchmark_sha256,
        )
        != evaluation
    ):
        raise EvidenceError("RELEASE_QUALIFICATION_DRIFT")
    return output


def issue_release_grant(
    *, fence, reports_root: Path, repository: Path, now: str, **inputs
) -> ReleaseGrant:
    """Only the lock-owning transition calls this; existing artifacts are immutable."""
    if fence.fd is None or fence.root != reports_root:
        raise EvidenceError("RELEASE_FENCE_NOT_LOCKED")
    observed = read_fence(fence.fd)
    if (
        observed.state != "CLOSED"
        or observed.revision != inputs["expected_revision"]
        or observed.attempt_id != inputs["expected_attempt_id"]
    ):
        raise EvidenceError("RELEASE_FENCE_NOT_LOCKED")
    published = reports_root / "cm-5.2" / inputs["expected_attempt_id"]
    if inputs["published_root"] != published:
        raise EvidenceError("RELEASE_GRANT_LAYOUT_INVALID")
    output = qualify_release(repository=repository, **inputs)
    qualification_bytes = canonical_json(output) + b"\n"
    if os.path.lexists(published / QUALIFICATION_NAME):
        if read_private(published, QUALIFICATION_NAME) != qualification_bytes:
            raise EvidenceError("RELEASE_GRANT_MISMATCH")
    else:
        write_private(published, QUALIFICATION_NAME, output)
    fields = {k: v for k, v in output.items() if k not in {"schema_version", "u10"}}
    fields.update(
        schema_version="level3-release-grant-v1",
        qualification_output_sha256=digest(qualification_bytes),
        issued_by="enable_production_level3",
        issued_at=now,
    )
    proposed = ReleaseGrant.model_validate(fields)
    args = dict(
        reports_root=reports_root,
        expected_attempt_id=output["attempt_id"],
        expected_revision=output["R"],
        expected_policy=output["action_policy_version"],
        expected_investigation_budget_profile=persisted_profile(3, output),
    )
    if os.path.lexists(published / GRANT_NAME):
        existing = read_release_grant(**args)
        if existing.model_dump(exclude={"issued_at"}) != proposed.model_dump(
            exclude={"issued_at"}
        ):
            raise EvidenceError("RELEASE_GRANT_MISMATCH")
        return existing
    write_private(published, GRANT_NAME, proposed)
    return read_release_grant(**args)
