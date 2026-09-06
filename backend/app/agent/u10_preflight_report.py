"""Four independent axes and allowlisted output. No IO or gate verification."""

from app.agent.release_artifacts import EvidenceError
from app.agent.release_gate import ReleaseAxes
from app.agent.u10_integrity import IntegrityObservation


def allowed_actions(
    integrity: str, robustness: str, delivery_integrity: str
) -> dict[str, bool]:
    if (
        integrity not in ("PASS", "FAIL")
        or robustness not in ("PASS", "FAIL", "NOT_RUN")
        or delivery_integrity not in ("PASS", "FAIL", "NOT_RUN")
    ):
        raise EvidenceError("U10_GATE_AXIS_INVALID")
    valid = integrity == "PASS"
    return {
        "u9": valid,
        "e2e": valid,
        "production_level3": valid
        and robustness == "PASS"
        and delivery_integrity == "PASS",
    }


def preflight_report(
    observation: IntegrityObservation | None,
    *,
    profile: str | None,
    phase: str | None,
    checked_at: str,
    failed_checks: list[str],
    release: ReleaseAxes | None = None,
) -> dict:
    """Project independent release results; never infer lineage from ACK alone."""
    valid = observation is not None and not failed_checks
    robustness = release.robustness if release else "NOT_RUN"
    delivery = release.delivery_integrity if release else "NOT_RUN"
    result = {
        "profile": observation.profile if valid else profile,
        "phase": observation.phase if valid else phase,
        "checked_at": observation.checked_at if valid else checked_at,
        "repository_root": observation.repository_root if valid else None,
        "head": observation.head if valid else None,
        "evaluated_revision": observation.evaluation.receipt.evaluated_revision
        if valid
        else None,
        "integrity": "PASS" if valid else "FAIL",
        "failed_checks": failed_checks,
        "agent_verdict": observation.evaluation.agent_verdict if valid else None,
        "verdict_reason": observation.evaluation.verdict_reason if valid else None,
        "robustness": robustness,
        "delivery_integrity": delivery,
        "allowed_actions": allowed_actions(
            "PASS" if valid else "FAIL", robustness, delivery
        ),
        "image_ids": {
            k: v.image_id
            for k, v in observation.deployment.image_bindings.images.items()
        }
        if valid
        else {},
        "reset_attempt_id": release.reset_attempt_id if release else None,
    }
    if release is not None:
        result.update(
            robustness_artifact_sha256=release.artifact_sha256,
            robustness_failed_checks=list(release.robustness_failed_checks),
            delivery_failed_checks=list(release.delivery_failed_checks),
        )
    return result
