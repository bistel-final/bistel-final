"""Acquire the attempt lock then exec the SAME Stage2 Bash/PID with its FD.

This leaf cannot execute an arbitrary command or create a claim. Bash continues
to own classification, service actions, cleanup and terminal issuance.
"""

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agent.release_artifacts import EvidenceError  # noqa: E402
from app.agent.release_entry import inspect_entry  # noqa: E402
from app.agent.release_lifecycle import lifecycle_lock  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def main(argv=None, *, execute=os.execve):
    parser = Parser(description=__doc__)
    for key in ("report-root", "repository", "prepared-attempt"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--mode",
        choices=("prepare", "resume_workload", "publish", "abort", "recover"),
        required=True,
    )
    parser.add_argument("--approval-record", type=Path)
    try:
        args = parser.parse_args(argv)
        repository = BACKEND.parent
        if args.repository.resolve() != repository:
            raise EvidenceError("RELEASE_REPOSITORY_INVALID")
        entry_args = dict(
            report_root=args.report_root,
            repository=repository,
            attempt_id=args.attempt_id,
            mode=args.mode,
            prepared_path=args.prepared_attempt,
        )

        def inspect():
            if args.mode == "prepare":
                from app.agent.release_artifacts import component_ref
                from app.agent.release_stage2_inputs import (
                    n8n_settings,
                    preparation_inputs,
                )

                preparation_inputs(
                    repository=repository,
                    report_root=args.report_root,
                    attempt_id=args.attempt_id,
                )
                n8n_settings()
                a = args.report_root / "cm-5.2" / args.attempt_id
                if any(
                    (a / n).exists()
                    for n in ("prepare-intent.json", "robustness/prepared-attempt.json")
                ):
                    raise EvidenceError("PREPARATION_ALREADY_STARTED")
                return dict(
                    prepared_sha256="",
                    attempt_sha256=component_ref(a, "attempt.json").sha256,
                )
            result = inspect_entry(**entry_args)
            if args.mode in ("resume_workload", "publish"):
                from app.agent.release_artifacts import parse_json, read_private
                from app.agent.release_prepared import parse_prepared

                prepared = parse_prepared(
                    parse_json(
                        read_private(
                            args.prepared_attempt.parent, args.prepared_attempt.name
                        )
                    )
                )
                if prepared.schema_version != "level3-prepared-attempt-v2":
                    raise EvidenceError("STAGE2_POLICY_MISMATCH")
            return result

        before = inspect()
        with lifecycle_lock(
            args.report_root,
            relative_directory=f"cm-5.2/{args.attempt_id}/robustness",
        ) as fd:
            if inspect() != before:
                raise EvidenceError("STAGE2_ENTRY_DRIFT")
            os.set_inheritable(fd, True)
            environment = dict(os.environ)
            environment.update(
                CM52_STAGE2_LOCK_FD=str(fd),
                CM52_STAGE2_PREPARED_SHA=before["prepared_sha256"],
            )
            options = (
                ["--hold-after", "5d", "--prepare-only"]
                if args.mode == "prepare"
                else [
                    "--hold-after",
                    "5d",
                    "--resume-workload",
                    "--prepared-attempt",
                    str(args.prepared_attempt),
                    "--approval-record",
                    str(args.approval_record),
                ]
                if args.mode == "resume_workload"
                else ["--resume-from", "6"]
                if args.mode == "publish"
                else ["--" + args.mode + "-prepared", str(args.prepared_attempt)]
            )
            execute(
                "/bin/bash",
                [
                    "/bin/bash",
                    str(repository / "deploy/compose/cm52_stage2.sh"),
                    "--attempt-id",
                    args.attempt_id,
                    *options,
                ],
                environment,
            )
            raise EvidenceError("STAGE2_LOCK_EXEC_FAILED")
    except (Exception, KeyboardInterrupt) as exc:
        code = (
            "WORKLOAD_ABORTED"
            if isinstance(exc, KeyboardInterrupt)
            else failure_code(exc)
        )
        print(json.dumps({"status": "FAIL", "code": code}, sort_keys=True))
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
