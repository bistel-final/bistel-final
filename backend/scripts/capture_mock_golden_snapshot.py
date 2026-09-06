"""Capture one current new-policy boundary, never reconstruct an earlier phase.

Private O_EXCL output, read-only E2E DB and Kafka operations. No workload, reset,
mail or workflow execution. PREFLIGHT continues to use the post-reset raw
snapshot writer required by the prepared SHA contract.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def capture(*, engine, read_offsets, phase, now):
    from app.agent.release_artifacts import EvidenceError
    from app.agent.release_golden import GoldenSnapshotV2
    from scripts.capture_golden_flow_snapshot import read_snapshot_payload

    before = read_offsets()
    data = read_snapshot_payload(engine, database="kosa_agent_e2e")
    prediction_hash = None
    if phase == "BATCH_BASELINE":
        from app.evaluation.fault_5class import freeze_predictions
        from app.evaluation.predictions_repository import (
            read_runtime_evaluation_snapshot,
        )

        predictions = read_runtime_evaluation_snapshot(
            engine,
            database="kosa_agent_e2e",
            run_ids=tuple(r["agent_run_id"] for r in data["runs"]),
        )
        prediction_hash = freeze_predictions(predictions.records).prediction_hash
        if data != read_snapshot_payload(engine, database="kosa_agent_e2e"):
            raise EvidenceError("GOLDEN_MOCK_CAPTURE_DRIFT")
    after = read_offsets()
    if before != after:
        raise EvidenceError("GOLDEN_MOCK_CAPTURE_DRIFT")
    return GoldenSnapshotV2(
        schema_version="mock-notify-golden-snapshot-v1",
        action_policy_version="MOCK-NOTIFY-V1",
        database="kosa_agent_e2e",
        phase=phase,
        recorded_at=now(),
        snapshot=data,
        kafka_offsets=after,
        prediction_hash=prediction_hash,
    )


def main(argv=None):
    from app.agent.release_artifacts import EvidenceError, write_private
    from app.agent.release_mock_capture import KafkaEvidenceReader
    from app.agent.u10_cli import Parser, failure_code

    parser = Parser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("BATCH_BASELINE", "MOCK_RESULTS", "NO_DECISIONS", "SECOND_BATCH"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if not args.output.is_absolute():
            raise EvidenceError("GOLDEN_MOCK_OUTPUT_INVALID")
        from app.common import config
        from app.common.db import get_app_engine

        if (
            config.AGENT_ACTION_POLICY != "MOCK-NOTIFY-V1"
            or config.AGENT_AUTONOMY_LEVEL != 3
        ):
            raise EvidenceError("GOLDEN_MOCK_POLICY_INVALID")
        with KafkaEvidenceReader() as kafka:
            result = capture(
                engine=get_app_engine(),
                read_offsets=kafka.offsets,
                phase=args.phase,
                now=lambda: datetime.now(UTC).isoformat(),
            )
        ref = write_private(args.output.parent, args.output.name, result)
        print('{"status":"PASS","sha256":"' + ref.sha256 + '"}')
        return 0
    except Exception as exc:
        import json

        print(json.dumps(dict(status="FAIL", code=failure_code(exc))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
