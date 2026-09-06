"""Production transition ordering; operational ports are supplied explicitly.

The persistent fence, not this Python process's lifetime, controls admission.
Only a validated 3-gate report reopens L3. Restoration is attempted once and a
failed restore leaves admission CLOSED. No reset, approval, email or run starts.
"""

import signal
from contextlib import contextmanager

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_cli import failure_code


@contextmanager
def operator_signals():
    """First SIGTERM/SIGINT requests rollback; repeated abort stays fail-closed."""
    previous = {}

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def transition(*, ports, fence, revision, attempt_id, restore_only=False):
    """The same preflight JSON contract is used here and by the standalone CLI.

    Before side effects the concrete ports validate private evidence, env, pinned
    images and mounting. They must not accept a caller-provided PASS dictionary.
    """

    def verify(level):
        result = ports.preflight(level)
        if (
            type(result) is not dict
            or result.get("integrity") != "PASS"
            or result.get("evaluated_revision") != revision
            or result.get("profile") != f"production_level{level}"
        ):
            raise EvidenceError("RELEASE_PREFLIGHT_FAILED")
        if level == 3 and (
            result.get("allowed_actions", {}).get("production_level3") is not True
            or result.get("reset_attempt_id") != attempt_id
            or result.get("robustness") != "PASS"
            or result.get("delivery_integrity") != "PASS"
        ):
            raise EvidenceError("RELEASE_THREE_GATE_DENIED")
        return result

    ports.validate_inputs(restore_only=restore_only)
    with fence as owned:
        changed = False
        try:
            if ports.active_runs() != 0:
                raise EvidenceError("RELEASE_ACTIVE_RUNS")
            if restore_only:
                changed = True
                ports.configure(2)
                ports.recreate()
                verified = verify(2)
                owned.reopen(level=2)
                return {
                    "status": "PASS",
                    "level": 2,
                    "restored": True,
                    "preflight": verified,
                }
            # Closing the fence precedes this last L2 observation.
            verify(2)
            if getattr(ports, "release_policy", "ACTION-POLICY-V1") == "MOCK-NOTIFY-V1":
                from app.agent.release_grant import ReleaseGrant

                grant = ports.qualify(fence=owned)
                if (
                    not isinstance(grant, ReleaseGrant)
                    or grant.attempt_id != attempt_id
                    or grant.R != revision
                    or grant.action_policy_version != "MOCK-NOTIFY-V1"
                ):
                    raise EvidenceError("RELEASE_GRANT_MISMATCH")
            changed = True  # Includes ambiguous/partial env/config writes.
            ports.configure(3)
            ports.recreate()
            verify(3)
            # Required rollback rehearsal, still under the SAME CLOSED fence.
            ports.configure(2)
            ports.recreate()
            verify(2)
            ports.configure(3)
            ports.recreate()
            verified = verify(3)
            owned.reopen(level=3)
            return {
                "status": "PASS",
                "level": 3,
                "rollback_rehearsal": "PASS",
                "preflight": verified,
            }
        except (Exception, KeyboardInterrupt) as exc:
            code = (
                "RELEASE_INTERRUPTED"
                if isinstance(exc, KeyboardInterrupt)
                else failure_code(exc)
            )
            restored = False
            try:
                if changed:
                    # No L3 workload is admitted while this fence is closed.
                    # Existing active runs already caused denial before mutation.
                    ports.configure(2)
                    ports.recreate()
                verify(2)
                owned.reopen(level=2)
                restored = True
            except Exception:
                pass  # Leave persistent CLOSED state; do not claim recovery.
            return {
                "status": "FAIL",
                "code": code,
                "restored_level2": restored,
                "new_runs_blocked": not restored,
            }
