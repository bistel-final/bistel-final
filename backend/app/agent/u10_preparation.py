"""U10 batch preparation seam: private snapshot -> isolated PG inventory -> run.

The snapshot loader owns database isolation and restoration. This adapter does
not create that authority, download data, or supply fabricated provider/effect
observations. The loader receives bytes and an attempt key, never an oracle.
"""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    read_private,
)
from app.agent.u10_batch import AttemptEnvironment, BatchBinding, execution_plan
from app.agent.u10_comparison import Benchmark
from app.agent.u10_export import guarded_call
from app.agent.u10_inventory import verify_fixture_inventory
from app.agent.u10_source import verify_source_binding


@dataclass(frozen=True)
class SnapshotSession:
    """Loader-owned handles; no expected inventory/oracle may enter these ports."""

    connection: Any
    route: Any
    current_lot_hist_ids: list[str]
    document_probe: Any
    environment: AttemptEnvironment


@dataclass(frozen=True)
class RuntimePorts:
    """Required live capabilities. No fake provider/effect/deadline defaults."""

    deadline: Any
    generate: Any
    select: Any
    observe_effects: Any


def counterfactual_loader(engine, runtime_scope):
    """Restore source-pinned CF inputs and provide the concrete prepare session.

    The enclosing live runner must own engine via isolated_database and provide
    an independently instrumented runtime_scope. This function cannot certify
    an arbitrary injected engine or manufacture effect observations.
    """
    from app.agent.u10_fixture_bundle import load_snapshot
    from app.agent.u10_fixture_database import restored_inventory
    from app.agent.u10_fixture_tools import FixtureTools

    keys = tuple(execution_plan())

    @contextmanager
    def open_snapshot(key, raw):
        if key not in keys:
            raise EvidenceError("U10_SNAPSHOT_POPULATION_INVALID")
        source = load_snapshot(raw, key.fixture_id)
        tools = FixtureTools(source, key.fixture_id)
        bound, document_context = tools.fixed_inputs()
        probe = FixtureTools(source, key.fixture_id)
        query = {"query": "inventory", "model_code": tools.graph.model_code}
        probe.documents(query)
        document_probe = probe.documents(query)
        with restored_inventory(engine, source) as connection:
            with runtime_scope(key) as runtime:
                if not isinstance(runtime, RuntimePorts) or not all(
                    callable(port)
                    for port in (
                        getattr(runtime.deadline, "call", None),
                        runtime.generate,
                        runtime.select,
                        runtime.observe_effects,
                    )
                ):
                    raise EvidenceError("U10_RUNTIME_PORTS_INVALID")
                yield SnapshotSession(
                    connection,
                    tools.route,
                    tools.current_ids,
                    document_probe,
                    AttemptEnvironment(
                        context=tools.context,
                        verified_snapshot_sha256=digest(raw),
                        read_ports=tools.ports(),
                        deadline=runtime.deadline,
                        generate=runtime.generate,
                        select=runtime.select,
                        observe_effects=runtime.observe_effects,
                        bound_inputs=bound,
                        document_context=document_context,
                    ),
                )

    return open_snapshot


def verified_preparer(
    *,
    benchmark: Benchmark,
    snapshots: dict[str, Path],
    binding: BatchBinding,
    authorize,
    open_snapshot,
):
    benchmark = Benchmark.model_validate(benchmark.model_dump()).model_copy(deep=True)
    fixtures = {f.fixture_id: f for f in benchmark.fixtures}
    snapshots = snapshots.copy()
    if set(snapshots) != set(fixtures):
        raise EvidenceError("U10_SNAPSHOT_POPULATION_INVALID")
    if (
        binding.benchmark_sha256 != digest(canonical_json(benchmark))
        or binding.tool_contract_sha256 != benchmark.tool_contract_sha256
        or binding.fixed_policy_sha256 != benchmark.fixed_policy_sha256
        or type(binding.attempt_count) is not int
        or binding.attempt_count != 32
    ):
        raise EvidenceError("U10_PREPARATION_BINDING_MISMATCH")
    verify_source_binding(benchmark)
    keys = tuple(execution_plan())

    @contextmanager
    def prepare(key):
        if key not in keys:
            raise EvidenceError("U10_SNAPSHOT_POPULATION_INVALID")
        verify_source_binding(benchmark)
        if authorize(binding) is not True:
            raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
        fixture = fixtures[key.fixture_id]
        path = snapshots[key.fixture_id]
        raw = read_private(path.parent, path.name)
        snapshot_sha = digest(raw)
        if snapshot_sha != fixture.initial_snapshot_sha256:
            raise EvidenceError("SNAPSHOT_MISMATCH")
        with open_snapshot(key, raw) as session:
            if not isinstance(session, SnapshotSession):
                raise EvidenceError("U10_SNAPSHOT_SESSION_INVALID")
            verify_fixture_inventory(
                fixture,
                session.connection,
                route=session.route,
                current_lot_hist_ids=session.current_lot_hist_ids,
                document_probe=session.document_probe,
            )
            env = session.environment
            if not isinstance(env, AttemptEnvironment):
                raise EvidenceError("U10_ATTEMPT_ENVIRONMENT_INVALID")
            # Prevent the inventory route from proving a different run context.
            from app.agent.rehydration import route_to_snapshot
            from app.agent.u10_observations import ObservationContext

            if not isinstance(env.context, ObservationContext):
                raise EvidenceError("U10_ATTEMPT_ENVIRONMENT_INVALID")
            if canonical_json(route_to_snapshot(session.route)) != canonical_json(
                route_to_snapshot(env.context.hypothesis_inputs()["route"])
            ) or {
                c.lot_hist_id
                for c in env.context.build_context().candidates.fdc
                if c.relation == "CURRENT"
            } != set(session.current_lot_hist_ids):
                raise EvidenceError("U10_PREPARATION_CONTEXT_MISMATCH")
            guarded = replace(
                env,
                verified_snapshot_sha256=snapshot_sha,
                generate=guarded_call(authorize, binding, env.generate),
                select=(
                    None
                    if env.select is None
                    else guarded_call(authorize, binding, env.select)
                ),
            )
            yield guarded
            # Do not publish results after input drift, even after provider work.
            if read_private(path.parent, path.name) != raw:
                raise EvidenceError("U10_SNAPSHOT_DRIFT")
            verify_source_binding(benchmark)
        # Loader cleanup completes before execute_batch may proceed to next key.

    return prepare
