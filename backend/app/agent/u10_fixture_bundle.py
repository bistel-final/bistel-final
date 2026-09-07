"""Build/revalidate CF8 inputs and separate evaluation oracle, before observation.

Inventory comes from restored SQL rows; oracle never enters the snapshot or tools.
These synthetic observations are not an attestation of real production behavior.
"""

from pathlib import Path

from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    parse_json,
    read_private,
    write_private,
)
from app.agent.u10_comparison import Benchmark, EvidenceIds, Fixture
from app.agent.u10_counterfactual import SCENARIOS, snapshot_payload
from app.agent.u10_fixture_source import verify_source_projection
from app.agent.u10_fixture_tools import FixtureTools
from app.agent.u10_inventory import read_inventory
from app.agent.u10_read_execution import fixed_policy_sha256
from app.agent.u10_source import tool_contract_sha256, verify_source_binding


def load_snapshot(raw: bytes, fixture_id: str) -> dict:
    value = parse_json(raw)
    if not isinstance(value, dict) or not isinstance(value.get("source"), dict):
        raise EvidenceError("U10_SNAPSHOT_INVALID")
    verify_source_projection(value["source"])
    expected = snapshot_payload(value["source"], fixture_id)
    if value != expected or raw != canonical_json(expected) + b"\n":
        raise EvidenceError("U10_SNAPSHOT_INVALID")
    return value["source"]


def _ids(values):
    values = sorted(set(values))
    return EvidenceIds(values=values, sha256=digest(canonical_json(values)))


def oracle_for(tools: FixtureTools):
    """Code-owned oracle built separately, not an argument to either policy."""
    scenario = tools.scenario
    parameter = tools.extra_parameter if scenario == "EXTRA_FDC" else tools.parameter
    if scenario == "UPSTREAM":
        upstream = next(c for c in tools.candidates.fdc if c.relation == "UPSTREAM")
        parameter = (
            tools.fdc({"lot_hist_id": upstream.lot_hist_id}).parameters[0].parameter_id
        )
    refs = [f"PARAMETER:{parameter}", "CHUNK:u10-" + scenario.lower()]
    dimensions = {
        "UPSTREAM": ["upstream"],
        "SIBLING_NORMAL": ["sibling"],
        "HISTORY_DRIFT": ["history"],
    }.get(scenario, [])
    if scenario == "UPSTREAM":
        upstream = next(c for c in tools.candidates.fdc if c.relation == "UPSTREAM")
        refs.append("LOT_HIST:" + upstream.lot_hist_id)
    return _ids(refs), dimensions


def check_treatments(tools: FixtureTools) -> None:
    """Reject missing CF treatments independently of inventory/oracle claims."""
    bound, _ = tools.fixed_inputs()
    current = tools.fdc(bound["CURRENT_FDC"]).parameters[0]
    scenario = tools.scenario
    valid = True
    if scenario == "UPSTREAM":
        adjacent = [c for c in tools.candidates.fdc if c.relation == "UPSTREAM"]
        valid = bool(adjacent) and current.oos_point_cnt == 0
        valid = valid and all(
            tools.fdc({"lot_hist_id": c.lot_hist_id}).parameters[0].oos_point_cnt > 0
            for c in adjacent
        )
    elif scenario in {"SIBLING_NORMAL", "HISTORY_DRIFT"}:
        slot, scope = (
            ("SIBLING", "SIBLING")
            if scenario == "SIBLING_NORMAL"
            else ("HISTORY", "CURRENT")
        )
        if slot not in bound:
            raise EvidenceError("U10_CF_TREATMENT_INVALID")
        history = tools.history(
            {
                **bound[slot],
                "_context": {
                    "scope": scope,
                    "current_lot_id": tools.route.incident.lot_id,
                },
            }
        )
        valid = (
            history.sample_count > 0
            and history.current.oos_wafers == 0
            and history.current.ooc_wafers == 0
            and current.oos_point_cnt > 0
            if scenario == "SIBLING_NORMAL"
            else len(history.prior) >= 2 and history.trend in {"DRIFT_UP", "DRIFT_DOWN"}
        )
    elif scenario == "EXTRA_FDC":
        valid = current.oos_point_cnt == 0 and any(
            p.parameter_id == tools.extra_parameter and p.oos_point_cnt > 0
            for c in tools.candidates.fdc
            for p in tools.fdc({"lot_hist_id": c.lot_hist_id}).parameters
        )
    elif scenario == "BELOW":
        valid = current.value_max < current.spec_lower
    else:
        valid = current.value_min > current.spec_upper
    # Use a separate instance: probing must not consume CF-3's per-attempt fault.
    probe = FixtureTools(
        tools.source, next(s[0] for s in SCENARIOS if s[3] == scenario)
    )
    query = {
        "query": "upper above lower below " + tools.extra_parameter,
        "model_code": tools.graph.model_code,
    }
    first = probe.documents(query)
    second = probe.documents(query)
    valid = valid and second.ok and bool(second.hits)
    valid = valid and (not first.ok if scenario == "DOCUMENT_RECOVERY" else first.ok)
    if not valid:
        raise EvidenceError("U10_CF_TREATMENT_INVALID")


def build_benchmark(source, connection):
    verify_source_projection(source)
    fixtures, snapshots = [], {}
    for fixture_id, _, _, _ in SCENARIOS:
        tools = FixtureTools(source, fixture_id)
        check_treatments(tools)
        # Probe a distinct instance so runtime fault state always starts at zero.
        probe = FixtureTools(source, fixture_id)
        query = {"query": "inventory", "model_code": tools.graph.model_code}
        probe.documents(query)
        inventory = read_inventory(
            connection,
            route=tools.route,
            current_lot_hist_ids=tools.current_ids,
            document_probe=probe.documents(query),
        )
        if inventory.history_prior_lots != len(tools.prior_lots()):
            raise EvidenceError("U10_INVENTORY_MISMATCH")
        payload = snapshot_payload(source, fixture_id)
        snapshots[fixture_id] = payload
        required, dimensions = oracle_for(tools)
        fixtures.append(
            Fixture(
                fixture_id=fixture_id,
                initial_snapshot_sha256=digest(canonical_json(payload) + b"\n"),
                initial_evidence_ids=tools.context.initial_evidence_ids(),
                candidate_inventory=inventory,
                expected_compared=inventory.dimensions(),
                required_evidence_ids=required,
                oracle_required_dimensions=dimensions,
            )
        )
    inventory = [
        {
            "fixture_id": f.fixture_id,
            "candidate_inventory": f.candidate_inventory.model_dump(),
            "expected_compared": f.expected_compared.model_dump(),
        }
        for f in fixtures
    ]
    oracle = [
        {
            "fixture_id": f.fixture_id,
            "required_evidence_ids": f.required_evidence_ids.model_dump(),
            "oracle_required_dimensions": f.oracle_required_dimensions,
        }
        for f in fixtures
    ]
    benchmark = Benchmark(
        schema_version="u10-benchmark-v1",
        fixture_sha256=digest(
            canonical_json(
                [
                    {
                        "fixture_id": f.fixture_id,
                        "initial_snapshot_sha256": f.initial_snapshot_sha256,
                        "initial_evidence_ids": f.initial_evidence_ids.model_dump(),
                    }
                    for f in fixtures
                ]
            )
        ),
        oracle_sha256=digest(canonical_json(oracle)),
        inventory_sha256=digest(canonical_json(inventory)),
        tool_contract_sha256=tool_contract_sha256(),
        fixed_policy_sha256=fixed_policy_sha256(),
        fixtures=fixtures,
    )
    return benchmark, snapshots


def write_bundle(root: Path, benchmark: Benchmark, snapshots: dict) -> str:
    """New private directory only; partial failure is kept, never overwritten."""
    benchmark = Benchmark.model_validate(benchmark.model_dump())
    verify_source_binding(benchmark)
    if set(snapshots) != {f.fixture_id for f in benchmark.fixtures}:
        raise EvidenceError("U10_SNAPSHOT_POPULATION_INVALID")
    for fixture in benchmark.fixtures:
        raw = canonical_json(snapshots[fixture.fixture_id]) + b"\n"
        load_snapshot(raw, fixture.fixture_id)
        if digest(raw) != fixture.initial_snapshot_sha256:
            raise EvidenceError("SNAPSHOT_MISMATCH")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    for fixture in benchmark.fixtures:
        payload = snapshots[fixture.fixture_id]
        if digest(canonical_json(payload) + b"\n") != fixture.initial_snapshot_sha256:
            raise EvidenceError("SNAPSHOT_MISMATCH")
        write_private(root, fixture.fixture_id + ".json", payload)
    write_private(root, "benchmark.json", benchmark)
    return digest(read_private(root, "benchmark.json"))
