"""V5-C-7.1 private prepared writer; not a live capture or SMTP grant issuer.

The Stage2 owner supplies its checked observations as private transport. This
writer cross-binds them and input bytes, but cannot prove how a saved observation
was collected. Live A preflight/n8n/context capture remains a separate boundary.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    canonical_json,
    digest,
    parse_json,
    read_private,
    validate_report_root,
    write_private,
)
from app.agent.release_lifecycle import lifecycle_lock, verify_lifecycle_lock
from app.agent.release_prepared import (
    Attempt,
    DbIdentity,
    EffectiveEnv,
    EffectiveEnvV2,
    N8nEvidenceProbe,
    N8nEvidenceProbeV2,
    PreparedAttempt,
    PreparedAttemptV2,
    Recipient,
    Revision,
    Sha256,
    SmtpConfigSnapshot,
    UtcTime,
    canonical_recipients,
    config_digest,
    recipient_hash,
    utc,
)
from app.agent.release_runtime import ImagePins, RuntimeSnapshot
from app.agent.u10_integrity import IntegrityObservation
from app.agent.u10_preflight_report import preflight_report
from app.agent.u10_revision import verify_execution_revision

CAPTURE = "preparation-capture.json"
PREFLIGHT = "e2e-level3-preflight.json"
BASELINE = "observer-baseline.json"
LOG = "stage2-log.jsonl"
PREPARED = "prepared-attempt.json"
EPOCH = "fdc_final_20260818"


class RestoreContext(EvidenceModel):
    prev_state: Literal["empty", "bound"]
    prev_fault_path: str | None
    prev_golden_path: str | None
    prev_fault_sha256: Sha256 | None
    prev_golden_sha256: Sha256 | None
    prev_rev: Revision | None
    prev_attempt: Attempt | None
    running_rev: Revision


class PreparationCapture(EvidenceModel):
    """Private transport, NOT canonical evidence or a grant. Never publish it."""

    schema_version: Literal["level3-preparation-capture-v1"]
    preflight: IntegrityObservation
    runtime: RuntimeSnapshot
    db_identities: dict[str, DbIdentity]
    recipients: dict[str, list[str]]
    smtp_config: SmtpConfigSnapshot
    n8n_evidence_probe: N8nEvidenceProbe
    previous: RestoreContext
    captured_at: UtcTime


class PreparationCaptureV2(PreparationCapture):
    schema_version: Literal["level3-preparation-capture-v2"]
    n8n_evidence_probe: N8nEvidenceProbeV2
    preflight_snapshot_sha256: Sha256


def parse_capture(value):
    model = (
        PreparationCaptureV2
        if value.get("schema_version") == "level3-preparation-capture-v2"
        else PreparationCapture
    )
    return model.model_validate(value)


def _require(value: bool, code: str) -> None:
    if not value:
        raise EvidenceError(code)


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed
    except (ValueError, AttributeError):
        raise EvidenceError("PREPARATION_TIME_INVALID") from None


def _reset(payload: dict, run_id: str, captured_at: str) -> None:
    """Check CM-4.7 final envelope, not re-run reset or claim full chain replay."""
    _require(
        payload.get("artifact_type") == "e2e_reset_final"
        and type(payload.get("format_version")) is int
        and payload["format_version"] == 1
        and payload.get("task_id") == "V5-CM-4.7"
        and payload.get("dataset_epoch") == EPOCH
        and payload.get("run_id") == run_id
        and re.fullmatch(r"[0-9a-f]{32}", run_id) is not None
        and payload.get("status") == payload.get("reason") == "PASS",
        "PREPARATION_RESET_INVALID",
    )
    for key in ("pre_receipt_sha256", "applied_receipt_sha256", "post_receipt_sha256"):
        _require(
            type(payload.get(key)) is str
            and re.fullmatch(r"[0-9a-f]{64}", payload[key]) is not None,
            "PREPARATION_RESET_INVALID",
        )
    before, after = (
        payload.get("observer_before_sha256"),
        payload.get("observer_after_sha256"),
    )
    _require(
        isinstance(before, dict)
        and set(before) == {"kosa_agent", "kosa_text2sql"}
        and before == after
        and all(
            type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v) for v in before.values()
        ),
        "PREPARATION_RESET_INVALID",
    )
    _require(
        _aware(payload.get("recorded_at")) <= _aware(captured_at),
        "PREPARATION_TIME_INVALID",
    )


def _baseline(payload: dict, reset: dict, captured_at: str) -> None:
    _require(
        payload.get("artifact_type") == "cm52_public_database_observer"
        and type(payload.get("format_version")) is int
        and payload["format_version"] == 1
        and payload.get("dataset_epoch") == EPOCH,
        "PREPARATION_BASELINE_INVALID",
    )
    immutable = payload.get("immutable")
    _require(
        isinstance(immutable, dict)
        and set(immutable) == {"kosa_agent", "kosa_text2sql"},
        "PREPARATION_BASELINE_INVALID",
    )
    for value in [*immutable.values(), payload.get("strict_kosa_agent")]:
        _require(
            isinstance(value, dict)
            and type(value.get("sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None,
            "PREPARATION_BASELINE_INVALID",
        )
    counters = payload.get("text2sql_log")
    _require(
        isinstance(counters, dict)
        and set(counters) == {"row_count", "max_id", "sequence_last_value"}
        and all(type(v) is int and v >= 0 for v in counters.values()),
        "PREPARATION_BASELINE_INVALID",
    )
    _require(
        _aware(reset["recorded_at"])
        <= _aware(payload.get("recorded_at"))
        <= _aware(captured_at),
        "PREPARATION_TIME_INVALID",
    )


def build_prepared(
    capture: PreparationCapture,
    *,
    attempt_id: str,
    revision: str,
    image_ids: dict[str, str],
    preflight_bytes: bytes,
    reset_bytes: bytes,
    reset_run_id: str,
    baseline_bytes: bytes,
    log_bytes: bytes,
    now: str,
) -> PreparedAttempt:
    """Derive fixed schema, SHA and lease from observations, never caller hashes."""
    capture = parse_capture(capture.model_dump()).model_copy(deep=True)
    images = ImagePins.model_validate(image_ids).model_dump()
    is_mock = isinstance(capture, PreparationCaptureV2)
    pf, runtime = capture.preflight, capture.runtime
    _require(
        runtime.phase == "running"
        and runtime.revision == revision
        and pf.profile
        == pf.deployment.profile
        == pf.deployment.image_bindings.profile
        == "e2e_level3"
        and pf.phase == pf.deployment.phase == "pre_u9"
        and pf.head == pf.evaluation.receipt.evaluated_revision == revision,
        "PREPARATION_PREFLIGHT_BINDING_INVALID",
    )
    bindings = pf.deployment.image_bindings
    _require(
        set(runtime.containers) == set(images)
        and all(
            c.image_id == images[r]
            and c.running is True
            and c.status == "running"
            and c.service == ("e2e-runner" if r == "runner" else r)
            for r, c in runtime.containers.items()
        ),
        "PREPARATION_RUNTIME_BINDING_INVALID",
    )
    ready = pf.deployment.readiness.backend_readiness
    checks = ready.checks.model_dump()
    _require(
        ready.status == "READY"
        and len(checks) == 6
        and all(c["status"] == "PASS" for c in checks.values()),
        "PREPARATION_READINESS_INVALID",
    )
    _require(
        bindings.evaluated_revision == revision
        and bindings.images == {r: getattr(runtime.images, r) for r in images}
        and bindings.containers
        == {r: getattr(runtime.prepared_containers(), r) for r in images}
        and {r: v.image_id for r, v in bindings.images.items()} == images
        and bindings.evaluated_tree_oid == pf.evaluation.receipt.evaluated_tree_oid,
        "PREPARATION_RUNTIME_BINDING_INVALID",
    )
    _require(
        set(capture.db_identities) == set(capture.recipients) == {"backend", "runner"}
        and capture.db_identities["backend"] == capture.db_identities["runner"],
        "PREPARATION_IDENTITY_INVALID",
    )
    addresses = canonical_recipients(capture.recipients["backend"])
    _require(
        addresses
        == canonical_recipients(capture.recipients["runner"])
        == canonical_recipients(capture.smtp_config.recipient_allowlist),
        "PREPARATION_RECIPIENT_INVALID",
    )
    reads = pf.deployment.runtime
    _require(
        reads.profile == "e2e_level3"
        and set(reads.readbacks) == {"backend", "runner"}
        and reads.container_ids
        == {r: bindings.containers[r].container_id for r in reads.readbacks},
        "PREPARATION_ENV_INVALID",
    )
    envs = []
    for value in reads.readbacks.values():
        _require(
            (getattr(value, "action_policy", "ACTION-POLICY-V1") == "MOCK-NOTIFY-V1")
            == is_mock,
            "PREPARATION_ENV_INVALID",
        )
        _require(
            value.profile == "e2e_level3"
            and value.database == "kosa_agent_e2e"
            and value.database_user == "kosa_app"
            and value.demo_ack in (None, "")
            and value.ack_matches_receipt is False,
            "PREPARATION_ENV_INVALID",
        )
        envs.append(
            (EffectiveEnvV2 if is_mock else EffectiveEnv)(
                **({"AGENT_ACTION_POLICY": value.action_policy} if is_mock else {}),
                AGENT_AUTONOMY_LEVEL=value.autonomy_level,
                AGENT_LEVEL3_ENABLED=value.level3_enabled,
                AGENT_LEVEL3_DEMO_ACK=value.demo_ack or "",
                **value.budget_policy,
            )
        )
    _require(envs[0] == envs[1], "PREPARATION_ENV_INVALID")
    expected_report = preflight_report(
        pf,
        profile=pf.profile,
        phase=pf.phase,
        checked_at=pf.checked_at,
        failed_checks=[],
    )
    _require(
        canonical_json(parse_json(preflight_bytes)) == canonical_json(expected_report),
        "PREPARATION_PREFLIGHT_OUTPUT_INVALID",
    )
    _require(
        _aware(pf.deployment.started_at)
        <= _aware(pf.deployment.checked_at)
        <= _aware(pf.checked_at)
        <= _aware(capture.captured_at),
        "PREPARATION_TIME_INVALID",
    )
    for container in bindings.containers.values():
        _require(
            _aware(container.started_at).replace(microsecond=0)
            <= _aware(pf.deployment.started_at),
            "PREPARATION_TIME_INVALID",
        )
    start = utc(capture.captured_at)
    # Writing an old transport must not renew the approval lease.
    _require(
        start <= utc(now) < start + timedelta(minutes=30), "PREPARED_ATTEMPT_EXPIRED"
    )
    reset, baseline = parse_json(reset_bytes), parse_json(baseline_bytes)
    _require(
        type(reset) is dict and type(baseline) is dict, "PREPARATION_INPUT_INVALID"
    )
    _reset(reset, reset_run_id, capture.captured_at)
    _baseline(baseline, reset, capture.captured_at)
    lines = log_bytes.splitlines()
    _require(bool(lines), "PREPARATION_LOG_INVALID")
    records = [parse_json(line) for line in lines]
    _require(
        all(
            type(r) is dict
            and set(r) == {"step", "status", "detail"}
            and r["step"] in ("3", "3a", "3b")
            and r["status"] == "PASS"
            and type(r["detail"]) is str
            for r in records
        )
        and records[-1]["step"] == "3b",
        "PREPARATION_LOG_INVALID",
    )
    return (PreparedAttemptV2 if is_mock else PreparedAttempt)(
        schema_version="level3-prepared-attempt-v2"
        if is_mock
        else "level3-prepared-attempt-v1",
        **(
            {"preflight_snapshot_sha256": capture.preflight_snapshot_sha256}
            if is_mock
            else {}
        ),
        attempt_id=attempt_id,
        R=revision,
        images=runtime.images,
        containers=runtime.prepared_containers(),
        effective_env=envs[0],
        db_identity=capture.db_identities["backend"],
        n8n_evidence_probe=capture.n8n_evidence_probe,
        recipient=Recipient(
            canonical_addresses=addresses,
            canonical_hash=recipient_hash(addresses),
            recipient_hash_version=2,
            count=len(addresses),
        ),
        reset_final_receipt_sha256=digest(reset_bytes),
        observer_baseline_sha256=digest(baseline_bytes),
        approved_config_digest_allowlist=[
            config_digest(capture.smtp_config.model_dump())
        ],
        max_external_emails=7,
        e2e_level3_preflight_output_sha256=digest(preflight_bytes),
        prepared_at=capture.captured_at,
        expires_at=(start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        stage2_log={
            "relative_path": LOG,
            "prefix_sha256": digest(log_bytes),
            "prefix_bytes": len(log_bytes),
        },
        last_ok_step="3b",
        **capture.previous.model_dump(),
    )


def issue_prepared(
    *,
    report_root: Path,
    mounted_report_root: Path,
    repository: Path,
    attempt_id: str,
    revision: str,
    image_ids: dict[str, str],
    capture_sha256: str,
    preflight_sha256: str,
    reset_receipt: Component,
    reset_run_id: str,
    lifecycle_lock_fd: int | None = None,
    clock=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
) -> dict:
    """Publish only prepared, independently locked or with a verified owner FD."""
    _require(
        type(attempt_id) is str
        and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", attempt_id) is not None,
        "ATTEMPT_ID_MISMATCH",
    )
    for value in (capture_sha256, preflight_sha256):
        _require(
            type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "PREPARATION_INPUT_PIN_INVALID",
        )
    image_ids = ImagePins.model_validate(image_ids).model_dump()
    reset_receipt = Component.model_validate(reset_receipt.model_dump())
    root = validate_report_root(report_root, mounted_report_root, repository)
    identity = verify_execution_revision(repository, revision)
    scope = f"cm-5.2/{attempt_id}"
    attempt = root / scope
    bundle = attempt / "robustness"

    def source(name):
        return root, f"{scope}/{name}"

    # Anchor every traversal at the protected root, not at an absolute attempt
    # path whose intermediate directories could hide a symlink.
    with lifecycle_lock(
        root,
        relative_directory=f"{scope}/robustness",
        inherited_fd=lifecycle_lock_fd,
    ) as lock_fd:
        _require(
            set(p.name for p in bundle.iterdir()) <= {".lifecycle.lock"},
            "PREPARATION_ALREADY_ISSUED",
        )
        paths = [source(name) for name in (CAPTURE, PREFLIGHT, BASELINE, LOG)]
        paths.append((root, reset_receipt.relative_path))
        before = {key: read_private(*key) for key in paths}
        _require(
            digest(before[source(CAPTURE)]) == capture_sha256
            and digest(before[source(PREFLIGHT)]) == preflight_sha256
            and digest(before[(root, reset_receipt.relative_path)])
            == reset_receipt.sha256,
            "PREPARATION_INPUT_PIN_MISMATCH",
        )
        capture = parse_capture(parse_json(before[source(CAPTURE)]))
        if isinstance(capture, PreparationCaptureV2):
            from app.agent.golden_flow import snapshot_from_mapping

            snapshot_key = source("evidence/artifacts/PREFLIGHT/db-snapshot.json")
            before[snapshot_key] = read_private(*snapshot_key)
            _require(
                digest(before[snapshot_key]) == capture.preflight_snapshot_sha256,
                "PREPARATION_PREFLIGHT_SNAPSHOT_MISMATCH",
            )
            snapshot = snapshot_from_mapping(parse_json(before[snapshot_key]))
            _require(
                not any(
                    getattr(snapshot, k)
                    for k in (
                        "runs",
                        "actions",
                        "approvals",
                        "deliveries",
                        "tools",
                        "audits",
                    )
                ),
                "PREPARATION_PREFLIGHT_NOT_EMPTY",
            )
        _require(
            capture.preflight.repository_root
            == capture.preflight.deployment.image_bindings.repository_root
            == identity.repository_root
            and capture.preflight.evaluation.receipt.evaluated_tree_oid
            == identity.evaluated_tree_oid,
            "PREPARATION_REPOSITORY_MISMATCH",
        )
        issuing_at = clock()
        prepared = build_prepared(
            capture,
            attempt_id=attempt_id,
            revision=revision,
            image_ids=image_ids,
            preflight_bytes=before[source(PREFLIGHT)],
            baseline_bytes=before[source(BASELINE)],
            reset_bytes=before[(root, reset_receipt.relative_path)],
            reset_run_id=reset_run_id,
            log_bytes=before[source(LOG)],
            now=issuing_at,
        )
        if prepared.prev_state == "bound":
            for path, expected in (
                (prepared.prev_fault_path, prepared.prev_fault_sha256),
                (prepared.prev_golden_path, prepared.prev_golden_sha256),
            ):
                key = (root, path.removeprefix("/reports/"))
                before[key] = read_private(*key)
                _require(
                    digest(before[key]) == expected,
                    "PREPARATION_RESTORE_BINDING_INVALID",
                )
        _require(
            verify_execution_revision(repository, revision) == identity,
            "PREPARATION_REPOSITORY_DRIFT",
        )
        _require(
            all(read_private(*key) == value for key, value in before.items()),
            "PREPARATION_INPUT_DRIFT",
        )
        _require(
            utc(prepared.prepared_at)
            <= utc(issuing_at)
            <= utc(clock())
            < utc(prepared.expires_at),
            "PREPARED_ATTEMPT_EXPIRED",
        )
        # Recheck names too, including unrecognized artifacts, before O_EXCL.
        _require(
            set(p.name for p in bundle.iterdir()) <= {".lifecycle.lock"},
            "PREPARATION_ALREADY_ISSUED",
        )
        verify_lifecycle_lock(root, lock_fd, relative_directory=f"{scope}/robustness")
        component = write_private(root, f"{scope}/robustness/{PREPARED}", prepared)
    return {
        "status": "PREPARED",
        "attempt_id": attempt_id,
        "R": revision,
        "prepared_sha256": component.sha256,
        "prepared_at": prepared.prepared_at,
        "expires_at": prepared.expires_at,
        "recipient_hash": prepared.recipient.canonical_hash,
        "recipient_hash_version": 2,
        "recipient_count": prepared.recipient.count,
        "max_external_emails": 7,
        "smtp_send_authorized": False,
        "deployment_authorized": False,
        "warning": "production is DOWN",
        "next_modes": ["--resume-workload", "--abort-prepared"],
    }
