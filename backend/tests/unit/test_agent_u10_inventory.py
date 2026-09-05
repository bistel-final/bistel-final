"""Execute the readback SQL on local in-memory rows, not a live CF benchmark."""

import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import postgresql

from app.agent import u10_inventory as subject
from app.agent.release_artifacts import EvidenceError
from app.agent.u10_comparison import Dimensions
from app.common.tool_contracts import DocumentSearchToolResult
from tests.unit.test_agent_react import NOW, _level3_route
from tests.unit.test_agent_u10_attempt import setup
from tests.unit.test_agent_u10_hypothesis import docs


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(
            text("""CREATE TABLE lot_history (
            lot_hist_id TEXT PRIMARY KEY, lot_id TEXT, wafer_id TEXT,
            chamber_id TEXT, step_id TEXT, track_in_at TEXT)""")
        )
        conn.execute(
            text("""CREATE TABLE metrology (
            metrology_id TEXT PRIMARY KEY, lot_id TEXT, wafer_id TEXT, step_id TEXT)""")
        )
        yield conn
    engine.dispose()


def history(
    db,
    identity,
    *,
    lot="LOT001",
    chamber="EQP01-PM1",
    step="CT-PHOTO",
    when=NOW,
    wafer="LOT001W001",
):
    db.execute(
        text(
            "INSERT INTO lot_history VALUES (:id, :lot, :wafer, :chamber, :step, :when)"
        ),
        dict(
            id=identity,
            lot=lot,
            wafer=wafer,
            chamber=chamber,
            step=step,
            when=when.isoformat() if when else None,
        ),
    )


def metrology(db, identity, *, lot="LOT001", step="CT-PHOTO"):
    db.execute(
        text("INSERT INTO metrology VALUES (:id, :lot, 'LOT001W001', :step)"),
        dict(id=identity, lot=lot, step=step),
    )


def params(db, *, direction=None, sibling=None, two_wafers=False):
    route = _level3_route()
    graph = replace(
        route.graph_evidence[0], sibling_chamber_ids=(sibling,) if sibling else ()
    )
    routes = []
    current_ids = []
    for n in range(1, 3 if two_wafers else 2):
        wafer = route.wafer_routes[0]
        current = replace(wafer.steps[0], lot_hist_id=f"LH-{n}", wafer_id=f"W{n}")
        current_ids.append(current.lot_hist_id)
        steps = [current]
        if direction:
            adjacent = replace(
                current,
                lot_hist_id=f"ADJ-{n}",
                step_id="ADJ-STEP",
                chamber_id="EQP02-PM1",
                track_in_at=NOW + timedelta(days=-1 if direction == "UPSTREAM" else 1),
            )
            steps = (
                [adjacent, current] if direction == "UPSTREAM" else [current, adjacent]
            )
        routes.append(replace(wafer, wafer_id=current.wafer_id, steps=tuple(steps)))
        for s in steps:
            history(
                db,
                s.lot_hist_id,
                wafer=s.wafer_id,
                lot=s.lot_id,
                chamber=s.chamber_id,
                step=s.step_id,
                when=s.track_in_at,
            )
    return dict(
        route=replace(route, graph_evidence=(graph,), wafer_routes=tuple(routes)),
        current_lot_hist_ids=current_ids,
        document_probe=DocumentSearchToolResult(ok=True, hits=[]),
    )


@pytest.mark.parametrize("direction", [None, "UPSTREAM", "DOWNSTREAM"])
def test_actual_rows_recount_candidate_wafers_and_scoped_metrology(db, direction):
    args = params(db, direction=direction, sibling="EQP01-PM2", two_wafers=True)
    metrology(db, "M1")
    metrology(db, "M2")
    metrology(db, "WRONG-LOT", lot="OTHER")
    metrology(db, "WRONG-STEP", step="OTHER")
    statements = []
    event.listen(db, "before_cursor_execute", lambda *args: statements.append(args[2]))
    inv = subject.read_inventory(db, **args)
    assert inv.current_wafers == 2
    assert inv.adjacent.relation == (direction or "NONE")
    assert inv.adjacent.wafers == (2 if direction else 0)
    assert inv.sibling_chamber_id == "EQP01-PM2"
    assert inv.metrology_samples == 2 and inv.history_prior_lots == 0
    assert inv.dimensions()["history"] == "AVAILABLE" and inv.documents is True
    assert len(statements) == 1 and statements[0].lstrip().startswith("WITH")
    assert (
        db.in_transaction()
    )  # The reader did not commit/close its caller's transaction.
    assert "fault_code" not in statements[0] and "alarm_result" not in statements[0]


@pytest.mark.parametrize("count", [0, 1, 2, 3, 5])
def test_prior_lots_are_distinct_and_capped_at_three(db, count):
    args = params(db)
    for n in range(count):
        for w in range(2):
            history(
                db,
                f"P{n}-{w}",
                lot=f"PRIOR-{n}",
                wafer=f"W{w}",
                when=NOW - timedelta(days=n + 1),
            )
    inv = subject.read_inventory(db, **args)
    assert inv.history_prior_lots == min(count, 3)
    assert inv.dimensions()["history"] == "AVAILABLE"


def test_history_strict_latest_before_and_current_lot_exclusion(db):
    args = params(db)
    history(db, "ELIGIBLE", lot="P0", when=NOW - timedelta(days=2))
    history(db, "EQUAL", lot="P1")
    history(db, "FUTURE", lot="P2", when=NOW + timedelta(days=1))
    history(db, "OVERLAP-1", lot="P3", when=NOW - timedelta(days=1))
    history(db, "OVERLAP-2", lot="P3", when=NOW + timedelta(days=1))
    history(db, "OTHER-CH", lot="P4", chamber="OTHER", when=NOW - timedelta(days=1))
    history(db, "OTHER-STEP", lot="P5", step="OTHER", when=NOW - timedelta(days=1))
    history(db, "NULL", lot="P6", when=None)
    assert subject.read_inventory(db, **args).history_prior_lots == 1


def test_history_cutoff_uses_full_current_lot_not_only_candidate_wafer(db):
    args = params(db)
    history(db, "EARLIER-CURRENT", when=NOW - timedelta(days=2), wafer="OTHER-WAFER")
    history(db, "BETWEEN", lot="PRIOR", when=NOW - timedelta(days=1))
    assert subject.read_inventory(db, **args).history_prior_lots == 0


def test_upstream_is_preferred_when_both_adjacent_directions_exist(db):
    args = params(db, direction="UPSTREAM")
    route = args["route"]
    wafer = route.wafer_routes[0]
    down = replace(
        wafer.steps[-1],
        lot_hist_id="DOWN",
        chamber_id="OTHER-CH",
        step_id="OTHER-STEP",
        track_in_at=NOW + timedelta(days=1),
    )
    args["route"] = replace(
        route, wafer_routes=(replace(wafer, steps=(*wafer.steps, down)),)
    )
    history(
        db,
        down.lot_hist_id,
        wafer=down.wafer_id,
        chamber=down.chamber_id,
        step=down.step_id,
        when=down.track_in_at,
    )
    assert subject.read_inventory(db, **args).adjacent.relation == "UPSTREAM"


@pytest.mark.parametrize("field", ["lot_id", "wafer_id", "chamber_id", "step_id"])
def test_candidate_identity_drift_is_rejected(db, field):
    args = params(db)
    # Field comes from this exact test parametrization, not any user input.
    db.execute(
        text(f"UPDATE lot_history SET {field} = 'OTHER' WHERE lot_hist_id = 'LH-1'")
    )
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_CANDIDATE_DRIFT$"):
        subject.read_inventory(db, **args)


def test_missing_candidate_is_not_counted_from_declared_route(db):
    args = params(db, direction="DOWNSTREAM")
    db.execute(text("DELETE FROM lot_history WHERE lot_hist_id = 'ADJ-1'"))
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_CANDIDATE_DRIFT$"):
        subject.read_inventory(db, **args)


@pytest.mark.parametrize(
    "probe", [None, {}, DocumentSearchToolResult(ok=False, reason="TIMEOUT: test")]
)
def test_document_probe_failure_is_not_available_and_prevents_query(db, probe):
    args = params(db)
    args["document_probe"] = probe

    class NoRead:
        def execute(self, *_):
            pytest.fail("query before valid document probe")

    with pytest.raises(EvidenceError, match="^U10_INVENTORY_DOCUMENT_PROBE_INVALID$"):
        subject.read_inventory(NoRead(), **args)


def test_document_probe_for_another_model_is_rejected(db):
    args = params(db)
    probe = docs(("C1", 0.5))
    probe.hits[0].model_code = "OTHER"
    args["document_probe"] = probe
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_DOCUMENT_PROBE_INVALID$"):
        subject.read_inventory(db, **args)


def test_inconsistent_route_is_rejected_before_read(db):
    args = params(db)
    args["route"] = replace(args["route"], route_consistency=False)
    with pytest.raises(EvidenceError, match="^U10_SNAPSHOT_SCOPE_INVALID$"):
        subject.read_inventory(db, **args)


@pytest.mark.parametrize("siblings", [("EQP01-PM1",), ("S1", "S2")])
def test_self_or_ambiguous_sibling_is_not_invented(db, siblings):
    args = params(db)
    route = args["route"]
    args["route"] = replace(
        route,
        graph_evidence=(
            replace(route.graph_evidence[0], sibling_chamber_ids=siblings),
        ),
    )
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_SIBLING_INVALID$"):
        subject.read_inventory(db, **args)


def test_database_error_propagates_instead_of_zero_inventory(db):
    args = params(db)

    class FailedRead:
        def execute(self, *_):
            raise TimeoutError("test-only timeout")

    with pytest.raises(TimeoutError):
        subject.read_inventory(FailedRead(), **args)


def test_fixture_readback_matches_without_rewriting_oracle(db):
    args = params(db)
    fixture = setup()["fixture"]
    original = fixture.model_dump()
    actual = subject.verify_fixture_inventory(fixture, db, **args)
    assert actual == fixture.candidate_inventory and fixture.model_dump() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_wafers", 2),
        ("history_prior_lots", 1),
        ("metrology_samples", 1),
        ("sibling_chamber_id", "OTHER"),
    ],
)
def test_coherent_but_false_inventory_declaration_is_rejected(db, field, value):
    args = params(db)
    fixture = setup()["fixture"]
    inv = fixture.candidate_inventory.model_copy(update={field: value})
    fixture = fixture.model_copy(
        update={
            "candidate_inventory": inv,
            "expected_compared": Dimensions.model_validate(inv.dimensions()),
        }
    )
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_MISMATCH$"):
        subject.verify_fixture_inventory(fixture, db, **args)


def test_sql_compiles_for_postgres_and_keeps_identifiers_bound():
    statement = subject._INVENTORY.params(
        candidate_ids=["x'); DROP TABLE lot_history; --"],
        chamber_id="CH",
        step_id="STEP",
        lot_id="LOT",
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    assert "DROP TABLE" not in str(compiled)
    assert "x'); DROP TABLE lot_history; --" in compiled.params.values()


def test_import_does_not_load_providers():
    code = """
import sys
import app.agent.u10_inventory
assert 'app.agent.graph' not in sys.modules
assert 'app.common.llm' not in sys.modules
assert 'app.knowledge.service' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
