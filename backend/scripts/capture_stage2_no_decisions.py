"""HELD-only read observation. Not a human confirmation/approval/action API."""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    component_ref,
    validate_report_root,
)
from app.agent.release_entry import inspect_entry  # noqa: E402
from app.agent.release_lifecycle import lifecycle_lock  # noqa: E402
from app.agent.release_stage2_workload import current_runtime, snapshot  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def main(argv=None):
    parser = Parser(description=__doc__)
    for key in ("report-root", "env-file"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    try:
        args = parser.parse_args(argv)
        report = validate_report_root(
            args.report_root, args.report_root, BACKEND.parent
        )
        relative = f"cm-5.2/{args.attempt_id}/robustness"
        inputs = dict(
            report_root=report,
            repository=BACKEND.parent,
            attempt_id=args.attempt_id,
            mode="publish",
            prepared_path=report / relative / "prepared-attempt.json",
        )
        before = inspect_entry(**inputs)
        with lifecycle_lock(report, relative_directory=relative):
            if inspect_entry(**inputs) != before:
                raise EvidenceError("STAGE2_ENTRY_DRIFT")
            a, _, running, runtime = current_runtime(
                repository=BACKEND.parent,
                report_root=report,
                env_file=args.env_file,
                attempt_id=args.attempt_id,
            )
            snapshot(runtime, running, a, "NO_DECISIONS")
            reference = component_ref(a, "evidence/artifacts/NO_DECISIONS/db.json")
            if inspect_entry(**inputs) != before:
                raise EvidenceError("STAGE2_ENTRY_DRIFT")
        print(json.dumps(dict(status="CAPTURED", artifact=reference.model_dump())))
        return 0
    except Exception as exc:
        print(json.dumps(dict(status="FAIL", code=failure_code(exc))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
