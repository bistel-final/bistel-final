"""V5-C-7.1 live-read preparation assembly, not the Stage2 lifecycle owner.

One A preflight plus an existing-execution n8n probe, bounded by actual context
and runtime rereads. No saved PASS input, file write, grant, reset or workload.
The live non-secret SMTP reader is mandatory; this module cannot derive SMTP
credentials/config from the n8n public API or use a prepared allowlist as truth.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.agent.release_artifacts import EvidenceError, digest, parse_json, read_private
from app.agent.release_context import PreparationContext, docker_context
from app.agent.release_n8n import N8nEvidenceApi, probe_evidence
from app.agent.release_prepare import (
    OriginalRetention,
    PreparationCapture,
    PreparationCaptureV2,
    RestoreContext,
)
from app.agent.release_prepared import (
    SmtpConfigSnapshot,
    canonical_recipients,
    config_digest,
)
from app.agent.release_runtime import ComposeRuntime, RuntimeSnapshot
from app.agent.u10_deployment import _now, _stamp, _time
from app.agent.u10_evaluation import verify_evaluation
from app.agent.u10_images import Inspect, docker_inspect
from app.agent.u10_integrity import verify_preflight_integrity
from app.agent.u10_readiness import Fetch, fetch_gateway
from app.agent.u10_revision import verify_execution_revision
from app.agent.u10_runtime import Read, docker_readback


def collect_preparation(
    *,
    runtime_adapter: ComposeRuntime,
    running: RuntimeSnapshot,
    repository: Path,
    artifact: Path,
    evaluation_receipt: Path,
    benchmark: Path,
    pinned_benchmark_sha256: str,
    previous: RestoreContext,
    api: N8nEvidenceApi,
    workflow_id: str,
    sample_execution_id: str,
    read_smtp_config: Callable[[], dict],
    read_context: Callable[[str], PreparationContext] = docker_context,
    inspect: Inspect = docker_inspect,
    read: Read = docker_readback,
    fetch: Fetch = fetch_gateway,
    clock: Callable[[], datetime] = _now,
    preflight_snapshot: Path | None = None,
    mock_workflows: dict | None = None,
    mock_samples: dict | None = None,
    read_trail_probe=None,
    n8n_original_retention: dict | None = None,
) -> PreparationCapture:
    """Return private in-memory transport; Stage2 must own lock/cleanup.

    R/images/IDs come from the pinned running adapter observation. Clean-main
    validation here is local only, not proof of merge/remote CI. Repeated reads
    detect observed drift, not atomicity or changes that revert between reads.
    """
    last_time: datetime | None = None

    def checked_time() -> datetime:
        nonlocal last_time
        value = _time(clock)
        if last_time is not None and value < last_time:
            raise EvidenceError("PREPARATION_CAPTURE_TIME_INVALID")
        last_time = value
        return value

    def context() -> dict[str, PreparationContext]:
        try:
            values = {
                role: PreparationContext.model_validate(
                    read_context(ids[role]).model_dump()
                ).model_copy(deep=True)
                for role in ("backend", "runner")
            }
            if values["backend"] != values["runner"]:
                raise ValueError
            addresses = values["backend"].recipients
            if canonical_recipients(addresses) != addresses:
                raise ValueError
            return values
        except Exception:
            raise EvidenceError("PREPARATION_CAPTURE_CONTEXT_INVALID") from None

    def smtp() -> SmtpConfigSnapshot:
        try:
            value = SmtpConfigSnapshot.model_validate(read_smtp_config()).model_copy(
                deep=True
            )
            config_digest(value.model_dump())
            if workflow_id not in value.n8n_workflow_versions:
                raise ValueError
            return value
        except Exception:
            raise EvidenceError("PREPARATION_CAPTURE_SMTP_UNAVAILABLE") from None

    try:
        if not callable(read_smtp_config):
            raise ValueError
        running = RuntimeSnapshot.model_validate(running.model_dump()).model_copy(
            deep=True
        )
        previous = RestoreContext.model_validate(previous.model_dump()).model_copy(
            deep=True
        )
        if running.phase != "running" or set(running.containers) != {
            "backend",
            "frontend",
            "runner",
        }:
            raise ValueError
    except Exception:
        raise EvidenceError("PREPARATION_CAPTURE_ARGUMENT_INVALID") from None
    checked_time()
    revision = verify_execution_revision(repository, running.revision)
    runtime_adapter.verify_running(running)
    ids = {r: c.container_id for r, c in running.containers.items()}
    images = {r: getattr(running.images, r).image_id for r in ids}
    before_context, before_smtp = context(), smtp()
    if (
        canonical_recipients(before_smtp.recipient_allowlist)
        != before_context["backend"].recipients
    ):
        raise EvidenceError("PREPARATION_CAPTURE_RECIPIENT_MISMATCH")
    preflight = verify_preflight_integrity(
        repository=repository,
        artifact=artifact,
        evaluation_receipt=evaluation_receipt,
        benchmark=benchmark,
        pinned_benchmark_sha256=pinned_benchmark_sha256,
        profile="e2e_level3",
        phase="pre_u9",
        expected_image_ids=images,
        container_ids=ids,
        inspect=inspect,
        read=read,
        fetch=fetch,
        clock=checked_time,
    )
    bindings = preflight.deployment.image_bindings
    if (
        preflight.head != running.revision
        or preflight.evaluation.receipt.evaluated_revision != running.revision
        or bindings.images != {r: getattr(running.images, r) for r in ids}
        or bindings.containers
        != {r: getattr(running.prepared_containers(), r) for r in ids}
        or any(
            v.demo_ack not in (None, "") or v.ack_matches_receipt
            for v in preflight.deployment.runtime.readbacks.values()
        )
    ):
        raise EvidenceError("PREPARATION_CAPTURE_PREFLIGHT_MISMATCH")
    probe = probe_evidence(
        api,
        workflow_id=workflow_id,
        workflow_version=before_smtp.n8n_workflow_versions[workflow_id],
        sample_execution_id=sample_execution_id,
        clock=lambda: checked_time().isoformat().replace("+00:00", "Z"),
    )
    mock = (
        getattr(
            preflight.deployment.runtime.readbacks["backend"], "action_policy", None
        )
        == "MOCK-NOTIFY-V1"
    )
    snapshot_bytes = None
    if mock:
        from app.agent.golden_flow import snapshot_from_mapping
        from app.agent.release_mock_capture import probe_mock_evidence

        if (
            preflight_snapshot is None
            or not callable(read_trail_probe)
            or n8n_original_retention is None
        ):
            raise EvidenceError("PREPARATION_MOCK_PROBE_REQUIRED")
        try:
            n8n_original_retention = OriginalRetention.model_validate(
                n8n_original_retention
            ).model_copy(deep=True)
        except Exception:
            raise EvidenceError("PREPARATION_MOCK_PROBE_REQUIRED") from None
        snapshot_bytes = read_private(
            preflight_snapshot.parent, preflight_snapshot.name
        )
        snapshot = snapshot_from_mapping(parse_json(snapshot_bytes))
        if any(
            getattr(snapshot, key)
            for key in ("runs", "actions", "approvals", "deliveries", "tools", "audits")
        ):
            raise EvidenceError("PREPARATION_PREFLIGHT_NOT_EMPTY")
        probe = probe_mock_evidence(
            api,
            email_probe=probe,
            workflows=mock_workflows,
            samples=mock_samples,
            read_trail_probe=read_trail_probe,
            observed_at=checked_time().isoformat().replace("+00:00", "Z"),
        )
    after_context, after_smtp = context(), smtp()
    if before_context != after_context or config_digest(
        before_smtp.model_dump()
    ) != config_digest(after_smtp.model_dump()):
        raise EvidenceError("PREPARATION_CAPTURE_DRIFT")
    runtime_adapter.verify_running(running)
    # Extend A's private-file observation window through the probe without
    # running a second deployment/preflight or using saved PASS as a shortcut.
    if (
        verify_evaluation(
            artifact=artifact,
            evaluation_receipt=evaluation_receipt,
            benchmark=benchmark,
            pinned_benchmark_sha256=pinned_benchmark_sha256,
        )
        != preflight.evaluation
    ):
        raise EvidenceError("PREPARATION_CAPTURE_EVALUATION_DRIFT")
    if verify_execution_revision(repository, running.revision) != revision:
        raise EvidenceError("PREPARATION_CAPTURE_REVISION_DRIFT")
    if (
        mock
        and read_private(preflight_snapshot.parent, preflight_snapshot.name)
        != snapshot_bytes
    ):
        raise EvidenceError("PREPARATION_CAPTURE_DRIFT")
    return (PreparationCaptureV2 if mock else PreparationCapture)(
        schema_version="level3-preparation-capture-v2"
        if mock
        else "level3-preparation-capture-v1",
        **({"preflight_snapshot_sha256": digest(snapshot_bytes)} if mock else {}),
        **(
            {"n8n_original_retention": n8n_original_retention}
            if mock
            else {}
        ),
        preflight=preflight,
        runtime=running,
        db_identities={r: c.identity for r, c in before_context.items()},
        recipients={r: c.recipients for r, c in before_context.items()},
        smtp_config=before_smtp,
        n8n_evidence_probe=probe,
        previous=previous,
        captured_at=_stamp(checked_time()),
    )
