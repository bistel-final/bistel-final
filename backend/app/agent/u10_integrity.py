"""Compose checks 1–8 using receipt-owned revision/trees, not caller overrides.

No full release gate, file writes or lifecycle actions. Raises on integrity
failure; returns negative research verdicts unchanged on successful validation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.agent.release_artifacts import EvidenceError, EvidenceModel
from app.agent.release_prepared import UtcTime
from app.agent.u10_deployment import (
    DeploymentObservation,
    Phase,
    _now,
    _stamp,
    _time,
    observe_deployment,
)
from app.agent.u10_evaluation import EvaluationObservation, verify_evaluation
from app.agent.u10_images import Inspect, Profile, docker_inspect
from app.agent.u10_readiness import Fetch, fetch_gateway
from app.agent.u10_revision import read_repository_head
from app.agent.u10_runtime import Read, docker_readback


class IntegrityObservation(EvidenceModel):
    integrity: Literal["PASS"]
    repository_root: str
    head: str
    profile: Profile
    phase: Phase
    checked_at: UtcTime
    evaluation: EvaluationObservation
    deployment: DeploymentObservation


def verify_preflight_integrity(
    *,
    repository: Path,
    artifact: Path,
    evaluation_receipt: Path,
    benchmark: Path,
    pinned_benchmark_sha256: str,
    profile: Profile,
    phase: Phase,
    expected_image_ids: dict[str, str],
    container_ids: dict[str, str],
    expected_attempt_id: str | None = None,
    inspect: Inspect = docker_inspect,
    read: Read = docker_readback,
    fetch: Fetch = fetch_gateway,
    clock: Callable[[], datetime] = _now,
) -> IntegrityObservation:
    """Read files → HEAD → deployment → re-read files/HEAD; no saved state.

    Build/container pins and production attempt provenance remain caller-owned.
    R/tree cannot be overridden: they always come from the validated receipt.
    HEAD may differ from R but must be unchanged over this observation window.
    """
    if type(expected_image_ids) is not dict or type(container_ids) is not dict:
        raise EvidenceError("U10_IMAGE_ROLES_INVALID")
    images, containers = expected_image_ids.copy(), container_ids.copy()
    last_time: datetime | None = None

    def observation_clock() -> datetime:
        nonlocal last_time
        value = _time(clock)
        if last_time is not None and value < last_time:
            raise EvidenceError("U10_OBSERVATION_TIME_INVALID")
        last_time = value
        return value

    evaluation_args = dict(
        artifact=artifact,
        evaluation_receipt=evaluation_receipt,
        benchmark=benchmark,
        pinned_benchmark_sha256=pinned_benchmark_sha256,
    )
    evaluation = verify_evaluation(**evaluation_args)
    head = read_repository_head(repository)
    receipt = evaluation.receipt
    if head.git_object_format != receipt.git_object_format:
        raise EvidenceError("U10_RECEIPT_OBJECT_FORMAT_MISMATCH")
    deployment = observe_deployment(
        repository=Path(head.repository_root),
        evaluated_revision=receipt.evaluated_revision,
        expected_trees=receipt.evaluated_tree_oid,
        profile=profile,
        phase=phase,
        expected_image_ids=images,
        container_ids=containers,
        expected_attempt_id=expected_attempt_id,
        inspect=inspect,
        read=read,
        fetch=fetch,
        clock=observation_clock,
    )
    # Revalidate bytes and semantics after all runtime IO; no cached verdict use.
    if verify_evaluation(**evaluation_args) != evaluation:
        raise EvidenceError("U10_EVALUATION_DRIFT")
    if read_repository_head(repository) != head:
        raise EvidenceError("U10_REPOSITORY_HEAD_DRIFT")
    checked = observation_clock()
    return IntegrityObservation(
        integrity="PASS",
        repository_root=head.repository_root,
        head=head.head,
        profile=profile,
        phase=phase,
        checked_at=_stamp(checked),
        evaluation=evaluation,
        deployment=deployment,
    )
