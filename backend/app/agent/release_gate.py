"""Read-only production release axes, recomputed independently from U10.

No stored PASS authorizes deployment. Integrity checks 1–8 belong to U10; this
module never changes that axis or its exit status. All failures here are bounded
code-owned diagnostics and deny Level 3 without exposing private evidence.
"""

from dataclasses import dataclass
from pathlib import Path

from app.agent.release_aggregate import (
    PUBLICATION_NAMES,
    _snapshot,
    assess_aggregate,
    parse_aggregate,
)
from app.agent.release_artifacts import (
    EvidenceError,
    digest,
    parse_json,
    read_private,
    validate_report_root,
)
from app.agent.release_model import ModelContext, docker_model_context
from app.agent.release_round import verify_round
from app.agent.u10_cli import failure_code


@dataclass(frozen=True)
class ReleaseAxes:
    robustness: str = "FAIL"
    delivery_integrity: str = "FAIL"
    artifact_sha256: str | None = None
    reset_attempt_id: str | None = None
    robustness_failed_checks: tuple[str, ...] = ()
    delivery_failed_checks: tuple[str, ...] = ()


def verify_release_axes(
    *,
    artifact: Path | None,
    published_root: Path | None,
    repository: Path,
    revision: str,
    attempt_id: str,
    image_ids: dict[str, str],
    backend_container_id: str,
    read_model=docker_model_context,
) -> ReleaseAxes:
    sha = None
    try:
        if artifact is None or published_root is None:
            raise EvidenceError("ROBUSTNESS_ARTIFACT_REQUIRED")
        if artifact.name != "aggregate.json":
            raise EvidenceError("AGGREGATE_COMPONENT_INVALID")
        root = artifact.parent
        validate_report_root(root, root, repository)
        validate_report_root(published_root, published_root, repository)
        before = read_private(root, artifact.name)
        components_before = _snapshot(root, tolerate_delivery_unreadable=True)
        publications_before = {
            name: read_private(published_root, name) for name in PUBLICATION_NAMES
        }
        sha = digest(before)
        declared = parse_aggregate(parse_json(before))
        if declared.schema_version == "level3-aggregate-v2":
            from app.agent.release_seal import verify_seal

            verify_seal(
                root=root,
                published_root=published_root,
                expected_revision=revision,
                expected_attempt_id=attempt_id,
            )
        model_before = ModelContext.model_validate(read_model(backend_container_id))
        actual = assess_aggregate(
            root=root,
            published_root=published_root,
            round1=declared.round1,
            round1_completion=declared.round1_completion,
            expected_revision=revision,
            expected_attempt_id=attempt_id,
            tolerate_delivery_unreadable=True,
        )
        round_artifact, _, _ = verify_round(root, declared.round1)
        robust_errors = list(actual.batch_summary.failed_checks)
        delivery_errors = list(actual.delivery_failed_checks)
        delivery_fields = {
            "delivery_integrity",
            "delivery_failed_checks",
            "provider_acceptances",
        }
        # Do not promote a forged declaration, but don't mix an SMTP-only failure
        # into investigation robustness. Shared provenance affects both axes.
        declared_data, actual_data = declared.model_dump(), actual.model_dump()
        for key in declared_data:
            if declared_data[key] != actual_data[key]:
                (delivery_errors if key in delivery_fields else robust_errors).append(
                    "AGGREGATE_RECOUNT_MISMATCH"
                )
        if set(image_ids) != {"backend", "frontend"} or any(
            getattr(round_artifact.images, role).image_id != image_ids[role]
            for role in image_ids
        ):
            robust_errors.append("ROBUSTNESS_IMAGE_BINDING_MISMATCH")
        if (
            model_before.llm != round_artifact.llm
            or model_before.endpoint_sha256 != round_artifact.model_endpoint_sha256
            or model_before.model_config_digest != round_artifact.model_config_digest
            or model_before.published_attempt_id != attempt_id
            or model_before.published_artifact_sha256
            != {name: digest(raw) for name, raw in publications_before.items()}
        ):
            robust_errors.append("ROBUSTNESS_MODEL_BINDING_MISMATCH")
        if (
            ModelContext.model_validate(read_model(backend_container_id))
            != model_before
            or read_private(root, artifact.name) != before
            or _snapshot(root, tolerate_delivery_unreadable=True) != components_before
            or any(
                read_private(published_root, name) != raw
                for name, raw in publications_before.items()
            )
        ):
            raise EvidenceError("ROBUSTNESS_OBSERVATION_DRIFT")
        return ReleaseAxes(
            robustness="FAIL" if robust_errors else actual.robustness_verdict,
            delivery_integrity="FAIL" if delivery_errors else actual.delivery_integrity,
            artifact_sha256=sha,
            reset_attempt_id=actual.attempt_id,
            robustness_failed_checks=tuple(sorted(set(robust_errors))),
            delivery_failed_checks=tuple(sorted(set(delivery_errors))),
        )
    except Exception as exc:
        code = failure_code(exc)
        if code == "U10_CLI_FAILED":
            code = "ROBUSTNESS_VALIDATION_FAILED"
        return ReleaseAxes(
            artifact_sha256=sha,
            robustness_failed_checks=(code,),
            delivery_failed_checks=(code,),
        )
