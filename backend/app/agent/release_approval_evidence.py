"""V5-C-7.1 independent PRE_APPROVAL execution evidence, before HELD.

Reads the existing golden-flow N8N_EXECUTIONS format, not SMTP_RECEIPT.
No API inventory sampling, inbox access, workflow, send, grant or artifact
writer exists here. The operator observes three execution IDs separately.
Stage2 owns the lifecycle lock and any cleanup after this bounded file barrier.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    EvidenceModel,
    component_parent,
    digest,
    parse_json,
    read_private,
)
from app.agent.release_delivery import EmailTarget, Identifier, ProviderAcceptance

APPROVAL_EXECUTIONS = "evidence/artifacts/PRE_APPROVAL/n8n-wf2.json"
APPROVAL_READY = "approval-execution-evidence.ready"


def _present(root: Path, relative: str = APPROVAL_READY) -> bool:
    # Missing parents are setup errors, not a missing producer file to poll.
    with component_parent(root, relative) as (parent, name):
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True


class ApprovalExecution(EvidenceModel):
    workflow: Literal["WF2"]
    action_id: Identifier
    status: Literal["SUCCESS"]
    execution_id: Identifier


class ApprovalExecutions(EvidenceModel):
    format_version: Literal[1]
    executions: list[ApprovalExecution] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def unique(self):
        if (
            len({row.action_id for row in self.executions}) != 3
            or len({row.execution_id for row in self.executions}) != 3
        ):
            raise ValueError("APPROVAL_EVIDENCE_DUPLICATE")
        return self


def read_approval_evidence(root: Path, reference: Component) -> ApprovalExecutions:
    """Read a SHA-pinned exact file under the protected attempt directory."""
    try:
        if reference.relative_path != APPROVAL_EXECUTIONS:
            raise EvidenceError("APPROVAL_EVIDENCE_PATH_INVALID")
        if read_private(root, APPROVAL_READY) != b"":
            raise EvidenceError("APPROVAL_EVIDENCE_READY_INVALID")
        raw = read_private(root, APPROVAL_EXECUTIONS)
        if len(raw) > 65536:
            raise EvidenceError("APPROVAL_EVIDENCE_TOO_LARGE")
        if digest(raw) != reference.sha256:
            raise EvidenceError("APPROVAL_EVIDENCE_SHA_MISMATCH")
        return ApprovalExecutions.model_validate(parse_json(raw))
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("APPROVAL_EVIDENCE_INVALID") from None


def bind_approval_actions(
    evidence: ApprovalExecutions, targets: list[EmailTarget]
) -> None:
    expected = [t.action_id for t in targets if t.email_kind == "APPROVAL_REQUEST"]
    if len(expected) != 3 or set(expected) != {
        row.action_id for row in evidence.executions
    }:
        raise EvidenceError("APPROVAL_EVIDENCE_ACTION_MISMATCH")


def bind_approval_acceptances(
    evidence: ApprovalExecutions, acceptances: list[ProviderAcceptance]
) -> list[str]:
    """Compare each action/ID pair; equal sets alone miss a swapped mapping."""
    rows = [r for r in acceptances if r.email_kind == "APPROVAL_REQUEST"]
    expected = {r.action_id: r.execution_id for r in evidence.executions}
    if (
        len(rows) != 3
        or {r.action_id: r.n8n_execution_id for r in rows} != expected
        or any(r.n8n_status != "success" for r in rows)
    ):
        raise EvidenceError("APPROVAL_EVIDENCE_EXECUTION_MISMATCH")
    return [expected[action] for action in sorted(expected)]


def wait_approval_evidence(
    root: Path,
    *,
    check_owner: Callable[[], None],
    timeout_seconds: int = 300,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Component, ApprovalExecutions]:
    """Only a missing ready marker is polled. Published bad files fail immediately.

    check_owner must recheck Stage2's inherited lock and unresolved resume claim.
    No new state/mode/claim is introduced, and this function never unlocks or
    cleans up. Production remains down; the existing owner handles any failure.
    There is no automatic retry of the 12-run workload or evidence collection.
    """
    if type(timeout_seconds) is not int or not 0 <= timeout_seconds <= 300:
        raise EvidenceError("APPROVAL_EVIDENCE_WAIT_INVALID")
    with component_parent(root, APPROVAL_EXECUTIONS):
        pass  # Producer directories must already exist and be private.
    start = monotonic()
    last = start
    if not math.isfinite(start):
        raise EvidenceError("APPROVAL_EVIDENCE_CLOCK_INVALID")
    while True:
        check_owner()
        now = monotonic()
        if not math.isfinite(now) or now < last:
            raise EvidenceError("APPROVAL_EVIDENCE_CLOCK_INVALID")
        last = now
        # A positive bounded wait never accepts an arrival at/after its deadline.
        # timeout=0 is one immediate read, useful for inspection/tests.
        if timeout_seconds and now >= start + timeout_seconds:
            raise EvidenceError("APPROVAL_EVIDENCE_TIMEOUT")
        if not _present(root):
            if not timeout_seconds:
                raise EvidenceError("APPROVAL_EVIDENCE_TIMEOUT")
            sleep(min(1, start + timeout_seconds - now))
            continue
        raw = read_private(root, APPROVAL_EXECUTIONS)
        reference = Component(relative_path=APPROVAL_EXECUTIONS, sha256=digest(raw))
        value = read_approval_evidence(root, reference)
        check_owner()
        now = monotonic()
        if not math.isfinite(now) or now < last:
            raise EvidenceError("APPROVAL_EVIDENCE_CLOCK_INVALID")
        if timeout_seconds and now >= start + timeout_seconds:
            raise EvidenceError("APPROVAL_EVIDENCE_TIMEOUT")
        # File bytes are checked once more after owner validation; a changed
        # producer input never silently becomes a new pin during this invocation.
        if read_approval_evidence(root, reference) != value:
            raise EvidenceError("APPROVAL_EVIDENCE_DRIFT")
        return reference, value
