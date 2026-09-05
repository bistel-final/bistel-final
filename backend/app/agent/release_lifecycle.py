"""V5-C-7.1 v58 canonical lifecycle, shared by executor and offline validators.

No workload/cleanup is executed here. A caller must hold the attempt lock from
classification through cleanup and terminal write; denial permits no mutation.
"""

from __future__ import annotations

import fcntl
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    Sha256,
    component_parent,
    digest,
    parse_json,
    read_private,
)
from app.agent.release_prepared import Attempt, PreparedAttempt, UtcTime, utc

Phase = Literal["RESUME_WORKLOAD", "PUBLISH", "ABORT"]
Result = Literal["NOT_ATTEMPTED", "OK", "FAILED"]
State = Literal["PREPARED", "UNRESOLVED", "HELD", "TERMINAL"]
Issuer = Literal["RESUME_WORKLOAD", "PUBLISH", "ABORT", "RECOVER"]
Primary = Literal[
    "STAGE2_STEP_FAILED",
    "WORKLOAD_ABORTED",
    "PUBLISH_PRECONDITION_FAILED",
    "ARTIFACT_PUBLISH_FAILED",
    "STALE_CLAIM_RECOVERED",
    "PUBLISH_PHASE_RECOVERED",
]
Reason = Literal[
    "OPERATOR_ABORT",
    "EXTERNAL_EFFECT_APPROVAL_MISSING",
    "PREPARED_ATTEMPT_EXPIRED",
    "PREPARED_RUNTIME_DRIFT",
]
Failure = Literal[
    "STAGE2_STEP_FAILED",
    "WORKLOAD_ABORTED",
    "PUBLISH_PRECONDITION_FAILED",
    "ARTIFACT_PUBLISH_FAILED",
    "STALE_CLAIM_RECOVERED",
    "PUBLISH_PHASE_RECOVERED",
    "CLEANUP_FAILED",
    "RESTORE_FAILED",
]
PHASES = ("RESUME_WORKLOAD", "PUBLISH", "ABORT")
ALLOWED_TRANSITIONS = {
    "PREPARED": frozenset({"RESUME_WORKLOAD", "ABORT"}),
    "UNRESOLVED": frozenset({"RECOVER"}),
    "HELD": frozenset({"PUBLISH"}),
    "TERMINAL": frozenset(),
}
PRIMARY_BY_ISSUER = {
    "RESUME_WORKLOAD": frozenset({None, "STAGE2_STEP_FAILED", "WORKLOAD_ABORTED"}),
    "PUBLISH": frozenset(
        {None, "PUBLISH_PRECONDITION_FAILED", "ARTIFACT_PUBLISH_FAILED"}
    ),
    "ABORT": frozenset({None}),
    "RECOVER": frozenset({"STALE_CLAIM_RECOVERED", "PUBLISH_PHASE_RECOVERED"}),
}


def resolve_failure_code(
    issued_by: Issuer,
    primary_failure_code: Primary | None,
    cleanup_result: Result,
    restore_result: Result,
) -> Failure | None:
    if (
        issued_by not in PRIMARY_BY_ISSUER
        or primary_failure_code not in PRIMARY_BY_ISSUER[issued_by]
        or cleanup_result not in ("OK", "FAILED", "NOT_ATTEMPTED")
        or restore_result not in ("OK", "FAILED", "NOT_ATTEMPTED")
    ):
        raise EvidenceError("LIFECYCLE_RESULT_INVALID")
    if restore_result == "FAILED":
        return "RESTORE_FAILED"
    if cleanup_result == "FAILED":
        return "CLEANUP_FAILED"
    return primary_failure_code


class ExternalEffects(EvidenceModel):
    state: Literal["KNOWN", "INDETERMINATE"]
    # Preserve even a violated cap in FAILED evidence; only HELD/PASS assert 7/3.
    email_sent: int | None = Field(ge=0)
    mes_blocked: int | None = Field(ge=0)
    basis: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def null_matrix(self) -> ExternalEffects:
        if any(
            (value is None) != (self.state == "INDETERMINATE")
            for value in (self.email_sent, self.mes_blocked)
        ):
            raise ValueError("EXTERNAL_EFFECTS_NULL_MATRIX")
        return self


class LifecycleClaim(EvidenceModel):
    schema_version: Literal["level3-lifecycle-claim-v1"]
    phase: Phase
    prepared_attempt: Component
    invocation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    host: str = Field(min_length=1, max_length=253)
    pid: int = Field(gt=0)
    boot_id: str = Field(min_length=1, max_length=128)
    process_start_time: str = Field(min_length=1, max_length=128)
    claimed_at: UtcTime

    @model_validator(mode="after")
    def binding(self) -> LifecycleClaim:
        utc(self.claimed_at)
        if self.prepared_attempt.relative_path != "prepared-attempt.json":
            raise ValueError("PREPARED_COMPONENT_INVALID")
        return self


class TerminalResult(EvidenceModel):
    issued_by: Issuer
    reason_code: Reason | None
    primary_failure_code: Primary | None
    failure_code: Failure | None
    external_effects: ExternalEffects
    cleanup_result: Result
    restore_result: Result

    def validate_results(self, *, held: bool = False) -> None:
        expected = resolve_failure_code(
            self.issued_by,
            self.primary_failure_code,
            self.cleanup_result,
            self.restore_result,
        )
        if self.failure_code != expected:
            raise ValueError("FAILURE_CODE_MISMATCH")
        if not held:
            # NA is an observation, never permission to skip required recovery.
            basis = self.external_effects.basis.split()
            if (
                self.cleanup_result == "NOT_ATTEMPTED"
                and "CLEANUP_E2E_ABSENT" not in basis
            ):
                raise ValueError("CLEANUP_BASIS_REQUIRED")
            if self.restore_result == "NOT_ATTEMPTED" and not {
                "RESTORE_PREV_EMPTY",
                "RESTORE_ALREADY_TARGET",
            }.intersection(basis):
                raise ValueError("RESTORE_BASIS_REQUIRED")


class LifecycleOutcome(TerminalResult):
    schema_version: Literal["level3-lifecycle-outcome-v1"]
    phase: Literal["RESUME_WORKLOAD", "ABORT"]
    outcome: Literal["HELD", "ABORTED", "FAILED"]
    issued_by: Literal["RESUME_WORKLOAD", "ABORT", "RECOVER"]
    claim: Component
    recovery_started_workload: Literal[False]
    finished_at: UtcTime

    @model_validator(mode="after")
    def matrix(self) -> LifecycleOutcome:
        utc(self.finished_at)
        self.validate_results(held=self.outcome == "HELD")
        if self.claim.relative_path != claim_filename(self.phase):
            raise ValueError("LIFECYCLE_PHASE_MISMATCH")
        expected_outcomes = {
            "RESUME_WORKLOAD": {"HELD", "FAILED"},
            "ABORT": {"ABORTED", "FAILED"},
        }
        if self.outcome not in expected_outcomes[self.phase]:
            raise ValueError("LIFECYCLE_PHASE_MISMATCH")
        if self.issued_by == "RECOVER":
            if (
                self.outcome != "FAILED"
                or self.reason_code is not None
                or self.primary_failure_code != "STALE_CLAIM_RECOVERED"
            ):
                raise ValueError("LIFECYCLE_RECOVERY_INVALID")
        elif self.issued_by != self.phase:
            raise ValueError("LIFECYCLE_ISSUER_INVALID")
        elif self.phase == "ABORT":
            if self.reason_code is None or self.primary_failure_code is not None:
                raise ValueError("LIFECYCLE_ABORT_REASON_REQUIRED")
            if self.external_effects.state != "KNOWN" or (
                self.external_effects.email_sent,
                self.external_effects.mes_blocked,
            ) != (0, 0):
                raise ValueError("LIFECYCLE_ABORT_EFFECT_INVALID")
        elif self.reason_code is not None:
            raise ValueError("LIFECYCLE_REASON_INVALID")
        if self.outcome == "HELD":
            if (
                self.issued_by != "RESUME_WORKLOAD"
                or self.primary_failure_code is not None
                or self.failure_code is not None
                or (self.cleanup_result, self.restore_result)
                != ("NOT_ATTEMPTED", "NOT_ATTEMPTED")
                or self.external_effects.state != "KNOWN"
                or (self.external_effects.email_sent, self.external_effects.mes_blocked)
                != (7, 3)
            ):
                raise ValueError("LIFECYCLE_HELD_INVALID")
        elif self.outcome == "ABORTED":
            if self.failure_code is not None or (
                self.cleanup_result,
                self.restore_result,
            ) != ("OK", "OK"):
                raise ValueError("LIFECYCLE_ABORTED_INVALID")
        elif self.failure_code is None:
            raise ValueError("LIFECYCLE_FAILURE_REQUIRED")
        if (
            self.issued_by == "RESUME_WORKLOAD"
            and self.outcome == "FAILED"
            and self.primary_failure_code
            not in {"STAGE2_STEP_FAILED", "WORKLOAD_ABORTED"}
        ):
            raise ValueError("LIFECYCLE_PRIMARY_REQUIRED")
        return self


class RoundCompletion(TerminalResult):
    schema_version: Literal["level3-round1-completion-v1"]
    round1: Component
    cm52_attempt_id: Attempt
    final_status: Literal["PASS", "FAIL"]
    lifecycle_claim_resume_workload: Component
    lifecycle_outcome_resume_workload: Component
    lifecycle_claim_publish: Component
    issued_by: Literal["PUBLISH", "RECOVER"]
    reason_code: None
    attempt_artifact_sha256: Sha256 | None
    golden_flow_sha256: Sha256 | None
    fault_5class_sha256: Sha256 | None
    completed_at: UtcTime

    @model_validator(mode="after")
    def matrix(self) -> RoundCompletion:
        utc(self.completed_at)
        self.validate_results()
        for key, expected in (
            ("round1", "round1.json"),
            ("lifecycle_claim_resume_workload", claim_filename("RESUME_WORKLOAD")),
            ("lifecycle_outcome_resume_workload", terminal_filename("RESUME_WORKLOAD")),
            ("lifecycle_claim_publish", claim_filename("PUBLISH")),
        ):
            if getattr(self, key).relative_path != expected:
                raise ValueError("LIFECYCLE_PHASE_MISMATCH")
        if self.issued_by == "RECOVER":
            if (
                self.final_status != "FAIL"
                or self.primary_failure_code != "PUBLISH_PHASE_RECOVERED"
            ):
                raise ValueError("LIFECYCLE_RECOVERY_INVALID")
        elif self.external_effects.state != "KNOWN":
            raise ValueError("PUBLISH_EFFECTS_REQUIRED")
        if self.final_status == "PASS":
            if (
                self.issued_by != "PUBLISH"
                or self.failure_code is not None
                or self.primary_failure_code is not None
                or (self.cleanup_result, self.restore_result) != ("OK", "OK")
                or any(
                    value is None
                    for value in (
                        self.attempt_artifact_sha256,
                        self.golden_flow_sha256,
                        self.fault_5class_sha256,
                    )
                )
                or (self.external_effects.email_sent, self.external_effects.mes_blocked)
                != (7, 3)
            ):
                raise ValueError("COMPLETION_PASS_INVALID")
        elif self.failure_code is None:
            raise ValueError("COMPLETION_FAILURE_REQUIRED")
        return self


def claim_filename(phase: str) -> str:
    if phase not in PHASES:
        raise EvidenceError("LIFECYCLE_PHASE_INVALID")
    return f"lifecycle-claim.{phase.lower()}.json"


def terminal_filename(phase: str) -> str:
    if phase not in PHASES:
        raise EvidenceError("LIFECYCLE_PHASE_INVALID")
    return (
        "round1-completion.json"
        if phase == "PUBLISH"
        else f"lifecycle-outcome.{phase.lower()}.json"
    )


def _bound(files: Mapping[str, bytes], component: Component) -> None:
    if (
        component.relative_path not in files
        or digest(files[component.relative_path]) != component.sha256
    ):
        raise EvidenceError("LIFECYCLE_COMPONENT_MISMATCH")


def classify_state(files: Mapping[str, bytes]) -> State:
    """Pure classification of validated bytes; never trust filename presence alone."""
    phase_files = {claim_filename(phase) for phase in PHASES}
    phase_files.update(terminal_filename(phase) for phase in PHASES)
    if any(name.startswith("lifecycle-") and name not in phase_files for name in files):
        raise EvidenceError("LIFECYCLE_PHASE_INVALID")
    if "lifecycle-outcome.publish.json" in files:
        raise EvidenceError("LIFECYCLE_PHASE_INVALID")
    if "prepared-attempt.json" not in files:
        raise EvidenceError("LIFECYCLE_PREPARED_REQUIRED")
    prepared = PreparedAttempt.model_validate(
        parse_json(files["prepared-attempt.json"])
    )
    claims: dict[str, LifecycleClaim] = {}
    terminals: dict[str, LifecycleOutcome | RoundCompletion] = {}
    for phase in PHASES:
        filename = claim_filename(phase)
        if filename in files:
            claim = LifecycleClaim.model_validate(parse_json(files[filename]))
            if claim.phase != phase:
                raise EvidenceError("LIFECYCLE_PHASE_MISMATCH")
            _bound(files, claim.prepared_attempt)
            if utc(claim.claimed_at) < utc(prepared.prepared_at):
                raise EvidenceError("LIFECYCLE_TIME_INVALID")
            claims[phase] = claim
        filename = terminal_filename(phase)
        if filename in files:
            if phase not in claims:
                raise EvidenceError("LIFECYCLE_CLAIM_REQUIRED")
            if phase == "PUBLISH":
                completion = RoundCompletion.model_validate(parse_json(files[filename]))
                if completion.cm52_attempt_id != prepared.attempt_id:
                    raise EvidenceError("LIFECYCLE_ATTEMPT_MISMATCH")
                for component in (
                    completion.round1,
                    completion.lifecycle_claim_resume_workload,
                    completion.lifecycle_outcome_resume_workload,
                    completion.lifecycle_claim_publish,
                ):
                    _bound(files, component)
                finished = completion.completed_at
                terminals[phase] = completion
            else:
                outcome = LifecycleOutcome.model_validate(parse_json(files[filename]))
                if outcome.phase != phase:
                    raise EvidenceError("LIFECYCLE_PHASE_MISMATCH")
                _bound(files, outcome.claim)
                finished = outcome.finished_at
                terminals[phase] = outcome
            if utc(finished) < utc(claims[phase].claimed_at):
                raise EvidenceError("LIFECYCLE_TIME_INVALID")
    unresolved = set(claims) - set(terminals)
    if len(unresolved) > 1:
        raise EvidenceError("LIFECYCLE_MULTIPLE_UNRESOLVED")
    if "ABORT" in claims and len(claims) != 1:
        raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
    resume = terminals.get("RESUME_WORKLOAD")
    if "PUBLISH" in claims and (
        not isinstance(resume, LifecycleOutcome) or resume.outcome != "HELD"
    ):
        raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
    if "PUBLISH" in claims and resume is not None:
        if utc(claims["PUBLISH"].claimed_at) < utc(resume.finished_at):
            raise EvidenceError("LIFECYCLE_TIME_INVALID")
    completion = terminals.get("PUBLISH")
    if {"aggregate.json", "MANIFEST.sha256"}.intersection(files) and (
        not isinstance(completion, RoundCompletion) or completion.final_status != "PASS"
    ):
        raise EvidenceError("LIFECYCLE_SEAL_FORBIDDEN")
    if unresolved:
        return "UNRESOLVED"
    if completion is not None or "ABORT" in terminals:
        return "TERMINAL"
    if isinstance(resume, LifecycleOutcome):
        return "HELD" if resume.outcome == "HELD" else "TERMINAL"
    return "PREPARED"


def authorize_transition(state: State, mode: str) -> Literal["ALLOW"]:
    if mode not in ALLOWED_TRANSITIONS.get(state, frozenset()):
        raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
    return "ALLOW"


def read_lifecycle(root: Path) -> dict[str, bytes]:
    names = {
        "prepared-attempt.json",
        "round1.json",
        "aggregate.json",
        "MANIFEST.sha256",
        "lifecycle-outcome.publish.json",
    }
    names.update(claim_filename(phase) for phase in PHASES)
    names.update(terminal_filename(phase) for phase in PHASES)
    # Generic/unknown phase files cannot masquerade as "claim count = 0".
    if any(
        path.name.startswith("lifecycle-") and path.name not in names
        for path in root.iterdir()
    ):
        raise EvidenceError("LIFECYCLE_PHASE_INVALID")
    return {
        name: read_private(root, name)
        for name in sorted(names)
        if os.path.lexists(root / name)
    }


@contextmanager
def lifecycle_lock(root: Path) -> Iterator[None]:
    """Nonblocking OS flock. Keep held across cleanup AND terminal emission."""
    from app.agent.release_artifacts import _check_file

    with component_parent(root, ".lifecycle.lock") as (parent, name):
        descriptor = os.open(
            name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            _check_file(os.fstat(descriptor))
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EvidenceError("LIFECYCLE_LOCK_BUSY") from exc
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def owner_identity(pid: int) -> tuple[str, str | None]:
    """Return boot and process birth identity; PID reuse is not a live owner."""
    if pid < 1:
        raise EvidenceError("LIFECYCLE_OWNER_UNVERIFIABLE")
    try:
        if sys.platform.startswith("linux"):
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            try:
                process = Path(f"/proc/{pid}/stat").read_text()
                started = process.rsplit(")", 1)[1].split()[19]
            except FileNotFoundError:
                started = None
        elif sys.platform == "darwin":
            boot = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            ).stdout.strip()
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart="],
                capture_output=True,
                text=True,
                timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            )
            if result.returncode not in (0, 1):
                raise EvidenceError("LIFECYCLE_OWNER_UNVERIFIABLE")
            started = result.stdout.strip() or None
        else:
            raise EvidenceError("LIFECYCLE_OWNER_UNVERIFIABLE")
        if not boot:
            raise EvidenceError("LIFECYCLE_OWNER_UNVERIFIABLE")
        return boot, started
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise EvidenceError("LIFECYCLE_OWNER_UNVERIFIABLE") from exc


def assert_stale_owner(claim: LifecycleClaim) -> None:
    if claim.host != socket.gethostname():
        raise EvidenceError("LIFECYCLE_OWNER_UNVERIFIABLE")
    boot, started = owner_identity(claim.pid)
    if claim.boot_id == boot and claim.process_start_time == started:
        raise EvidenceError("LIFECYCLE_OWNER_ACTIVE")
