"""Private stdout ONLY: actual 12-run checkpoint/DB observations, never execute.

Stage2 must capture stdout into its protected report directory. Do not put the
payload in Git, public logs, API responses or workflow artifacts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    if sys.argv[1:] if argv is None else argv:
        print('{"status":"FAIL","code":"ROUND_CAPTURE_ARGUMENT_INVALID"}')
        return 2
    runtime = None
    try:
        from app.agent.release_context import read_context
        from app.agent.release_model import collect_model_context
        from app.agent.release_run_capture import collect_runs
        from app.agent.runtime_composition import get_agent_runtime
        from app.common import config
        from app.common.db import get_app_engine

        engine = get_app_engine()
        identity = read_context(engine, config).identity
        runtime = get_agent_runtime()
        resources = runtime.resources()  # config only, no LLM preflight/invoke
        result = collect_runs(
            engine,
            identity=identity,
            read_checkpoint=lambda thread: runtime._checkpoint_values(
                resources, thread
            )[0],
            read_model=collect_model_context,
            action_policy=config.AGENT_ACTION_POLICY,
        )
        payload = result.model_dump_json()
        if len(payload.encode()) > 4 * 1024 * 1024:
            raise ValueError
        runtime.close()
        runtime = None
        print(payload)
        return 0
    except Exception:
        print('{"status":"FAIL","code":"ROUND_CAPTURE_UNAVAILABLE"}')
        return 1
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
