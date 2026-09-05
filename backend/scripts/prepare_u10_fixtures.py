"""No-LLM CF8/32-preparation rehearsal on an owned, local-only PostgreSQL.

Writes ONLY synthetic inputs and the separate oracle, never a run artifact or
evaluation receipt. This command does not grant or exercise data-export consent.
"""

import json
import sys
from contextlib import contextmanager
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_artifacts import EvidenceError, canonical_json  # noqa: E402
from app.agent.u10_batch import execution_plan  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402
from app.agent.u10_fixture_bundle import (  # noqa: E402
    build_benchmark,
    write_bundle,
)
from app.agent.u10_fixture_database import (  # noqa: E402
    isolated_database,
    restored_inventory,
)
from app.agent.u10_fixture_source import load_final_source  # noqa: E402
from app.agent.u10_inventory import verify_fixture_inventory  # noqa: E402
from app.agent.u10_preparation import RuntimePorts, counterfactual_loader  # noqa: E402


class _NoRuntime:
    def call(self, *args, **kwargs):
        raise EvidenceError("U10_DRY_RUN_RUNTIME_FORBIDDEN")


@contextmanager
def _dry_runtime(key):
    # No success/zero-effect stub: any accidental execution fails immediately.
    disabled = _NoRuntime()
    yield RuntimePorts(disabled, disabled.call, disabled.call, disabled.call)


def prepare(archive, output, postgres_image_id):
    if output.exists() or output.is_symlink():
        raise EvidenceError("U10_OUTPUT_EXISTS")
    source = load_final_source(archive)
    contexts = []
    with isolated_database(postgres_image_id) as engine:
        with restored_inventory(engine, source) as connection:
            benchmark, snapshots = build_benchmark(source, connection)
        fixtures = {f.fixture_id: f for f in benchmark.fixtures}
        loader = counterfactual_loader(engine, _dry_runtime)
        for key in execution_plan():
            raw = canonical_json(snapshots[key.fixture_id]) + b"\n"
            with loader(key, raw) as session:
                inventory = verify_fixture_inventory(
                    fixtures[key.fixture_id],
                    session.connection,
                    route=session.route,
                    current_lot_hist_ids=session.current_lot_hist_ids,
                    document_probe=session.document_probe,
                )
                env = session.environment
                env.context.validate_fixed_inputs(inventory, env.bound_inputs)
                inputs = env.context.hypothesis_inputs()
                if inputs["fdc_evidence"] or inputs["document_evidence"] is not None:
                    raise EvidenceError("U10_SNAPSHOT_STATE_LEAK")
                contexts.append(env.context)
        if len({id(c) for c in contexts}) != 32:
            raise EvidenceError("U10_SNAPSHOT_STATE_LEAK")
    # Cleanup must succeed before any successful bundle publication.
    sha = write_bundle(output, benchmark, snapshots)
    return {
        "status": "PASS",
        "mode": "DRY_RUN_INPUTS_ONLY",
        "fixture_count": 8,
        "preparations_verified": len(contexts),
        "attempts_executed": 0,
        "llm_calls": 0,
        "artifact_issued": False,
        "evaluation_receipt_issued": False,
        "benchmark_sha256": sha,
        "source_projection_sha256": snapshots["CF-1"]["source_projection_sha256"],
        "synthetic_counterfactual_benchmark": True,
        "production_performance_not_claimed": True,
    }


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--postgres-image-id", required=True)
    parser.add_argument("--dry-run", action="store_true", required=True)
    try:
        args = parser.parse_args(argv)
        result = prepare(args.source_archive, args.output, args.postgres_image_id)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "code": failure_code(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
