"""Four independent axes and allowlisted output. No IO or gate verification."""

from app.agent.release_artifacts import EvidenceError
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
) -> dict:
    """Bundle A has no robustness/delivery verifier: never turn either axis PASS.

    The reset attempt is not inferred from the production ACK. Bundle C must
    supply verified reset lineage, so this version explicitly outputs null.
    """
    valid = observation is not None and not failed_checks
    return {
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
        "robustness": "NOT_RUN",
        "delivery_integrity": "NOT_RUN",
        "allowed_actions": allowed_actions(
            "PASS" if valid else "FAIL", "NOT_RUN", "NOT_RUN"
        ),
        "image_ids": {
            k: v.image_id
            for k, v in observation.deployment.image_bindings.images.items()
        }
        if valid
        else {},
        "reset_attempt_id": None,
    }
