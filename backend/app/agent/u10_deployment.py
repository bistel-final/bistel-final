"""Compose preflight checks 4–8 into one read-only observation window.

Not the full preflight: receipt/artifact, robustness, delivery and permissions
remain separate. No lifecycle operation, workload or evidence publication.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.agent.release_artifacts import EvidenceError, EvidenceModel
from app.agent.u10_images import (
    ImageBindings,
    Inspect,
    Profile,
    docker_inspect,
    verify_image_bindings,
)
from app.agent.u10_readiness import (
    Fetch,
    ReadinessObservation,
    fetch_gateway,
    verify_readiness,
)
from app.agent.u10_revision import RevisionTrees
from app.agent.u10_runtime import (
    Read,
    RuntimeObservation,
    docker_readback,
    verify_runtime_readbacks,
)

Phase = Literal["pre_u9", "post_start_pre_enable"]


class DeploymentObservation(EvidenceModel):
    profile: Profile
    phase: Phase
    started_at: str
    checked_at: str
    image_bindings: ImageBindings
    runtime: RuntimeObservation
    readiness: ReadinessObservation


def _now() -> datetime:
    return datetime.now(UTC)


def _time(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceError("U10_OBSERVATION_TIME_INVALID")
    return value.astimezone(UTC)


def observe_deployment(
    *,
    repository: Path,
    evaluated_revision: str,
    expected_trees: RevisionTrees,
    profile: Profile,
    phase: Phase,
    expected_image_ids: dict[str, str],
    container_ids: dict[str, str],
    expected_attempt_id: str | None = None,
    inspect: Inspect = docker_inspect,
    read: Read = docker_readback,
    fetch: Fetch = fetch_gateway,
    clock: Callable[[], datetime] = _now,
) -> DeploymentObservation:
    """Bind the same pinned roles across images → runtime → HTTP → images.

    Callers supply independently verified receipt revision/trees, build image
    IDs and preparation container IDs. This does not establish their provenance.
    Reinspection detects observable drift; it is not an atomic snapshot or lock.
    """
    if type(phase) is not str or phase not in ("pre_u9", "post_start_pre_enable"):
        raise EvidenceError("U10_OBSERVATION_PHASE_INVALID")
    if type(expected_image_ids) is not dict or type(container_ids) is not dict:
        raise EvidenceError("U10_IMAGE_ROLES_INVALID")
    # Snapshot caller-owned maps before injected IO can mutate them.
    images = expected_image_ids.copy()
    containers = container_ids.copy()
    trees = expected_trees.model_copy(deep=True)
    started = _time(clock)
    before = verify_image_bindings(
        repository=repository,
        evaluated_revision=evaluated_revision,
        expected_trees=trees,
        profile=profile,
        expected_image_ids=images,
        container_ids=containers,
        inspect=inspect,
    )
    runtime_ids = {
        role: containers[role]
        for role in (("backend", "runner") if profile == "e2e_level3" else ("backend",))
    }
    runtime = verify_runtime_readbacks(
        profile=profile,
        container_ids=runtime_ids,
        expected_attempt_id=expected_attempt_id,
        read=read,
    )
    readiness = verify_readiness(fetch=fetch)
    after = verify_image_bindings(
        repository=repository,
        evaluated_revision=evaluated_revision,
        expected_trees=trees,
        profile=profile,
        expected_image_ids=images,
        container_ids=containers,
        inspect=inspect,
    )
    if before != after:
        raise EvidenceError("U10_DEPLOYMENT_DRIFT")
    checked = _time(clock)
    if checked < started:
        raise EvidenceError("U10_OBSERVATION_TIME_INVALID")
    return DeploymentObservation(
        profile=profile,
        phase=phase,
        started_at=started.isoformat().replace("+00:00", "Z"),
        checked_at=checked.isoformat().replace("+00:00", "Z"),
        image_bindings=after,
        runtime=runtime,
        readiness=readiness,
    )
