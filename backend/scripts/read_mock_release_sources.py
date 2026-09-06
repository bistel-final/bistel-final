"""Private stdout projections from current E2E DB/Kafka/application trail.

All operations are read-only. Callers must capture stdout privately; no DSN,
credentials, payload text or workflow bodies are returned.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    from app.agent.release_artifacts import EvidenceError, canonical_json, parse_json
    from app.agent.u10_cli import Parser, failure_code

    parser = Parser(description=__doc__)
    parser.add_argument(
        "operation", choices=("database", "offsets", "records", "trail", "trail-probe")
    )
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument("--started-at")
    parser.add_argument("--frozen-at")
    parser.add_argument("--historical-trail-run-id")
    try:
        args = parser.parse_args(argv)
        from app.agent.release_context import read_context
        from app.agent.release_database import read_delivery_database
        from app.agent.release_mock_capture import (
            KafkaEvidenceReader,
            read_callback_trail,
        )
        from app.common import config
        from app.common.db import get_app_engine

        engine = get_app_engine()
        identity = read_context(engine, config).identity
        if (
            identity.database != "kosa_agent_e2e"
            or config.AGENT_ACTION_POLICY != "MOCK-NOTIFY-V1"
        ):
            raise EvidenceError("MOCK_SOURCE_TARGET_INVALID")
        if args.operation == "database":
            result = read_delivery_database(
                engine, expected_identity=identity, action_policy="MOCK-NOTIFY-V1"
            ).model_dump(mode="json")
        elif args.operation in {"offsets", "records"}:
            with KafkaEvidenceReader() as kafka:
                if args.operation == "offsets":
                    result = kafka.offsets()
                else:
                    result = [
                        r.model_dump()
                        for r in kafka.records(
                            parse_json(args.before.encode()),
                            parse_json(args.after.encode()),
                        )
                    ]
        else:
            directory, run_id = (
                config.DELIVERY_CALLBACK_TRAIL_DIR,
                config.DELIVERY_CALLBACK_TRAIL_RUN_ID,
            )
            if not directory or not run_id:
                raise EvidenceError("MOCK_CALLBACK_TRAIL_UNAVAILABLE")
            if args.historical_trail_run_id is not None:
                import re

                previous = args.historical_trail_run_id
                if (
                    args.operation != "trail"
                    or re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", previous)
                    is None
                    or run_id != previous + "_published"
                ):
                    raise EvidenceError("MOCK_CALLBACK_TRAIL_BINDING_INVALID")
                run_id = previous
            path = Path(directory) / f"trail-{run_id}.jsonl"
            if args.operation == "trail-probe":
                # The app created this append-only file during composition.
                # Opening/validating it does not append a synthetic callback.
                read_callback_trail(
                    path,
                    started_at="1970-01-01T00:00:00Z",
                    frozen_at="9999-01-01T00:00:00Z",
                )
                result = dict(callback_trail_writable=os.access(path, os.W_OK))
            else:
                result = [
                    r.model_dump()
                    for r in read_callback_trail(
                        path, started_at=args.started_at, frozen_at=args.frozen_at
                    )
                ]
        print(canonical_json(result).decode())
        return 0
    except Exception as exc:
        print(json.dumps(dict(status="FAIL", code=failure_code(exc))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
