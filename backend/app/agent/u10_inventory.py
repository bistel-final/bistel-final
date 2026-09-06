"""Read-only U10 inventory recount, not a snapshot/approval or oracle issuer.

The caller owns an isolated connection, pinned route/graph and document probe.
One statement reads candidate identities, prior lots and metrology sample IDs.
No fault labels, measurement values, LLM calls or runtime observations are used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import bindparam, text

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_comparison import Adjacent, Fixture, Inventory
from app.agent.u10_observations import ObservationContext

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from app.agent.routing import ResolvedIncidentRoute
    from app.common.tool_contracts import DocumentSearchToolResult


# Match production history's latest-track-in ordering, current-lot exclusion,
# strict before boundary and three-lot cap. Values remain bound parameters.
_INVENTORY = text(
    """
    WITH prior_lots AS (
        SELECT lot_id, max(track_in_at) AS latest_track_in
        FROM lot_history
        WHERE chamber_id = :chamber_id AND step_id = :step_id
          AND track_in_at IS NOT NULL
        GROUP BY lot_id
        HAVING max(track_in_at) < (
            SELECT min(track_in_at) FROM lot_history
            WHERE lot_id = :lot_id AND chamber_id = :chamber_id
              AND step_id = :step_id
        ) AND lot_id <> :lot_id
        ORDER BY latest_track_in DESC, lot_id DESC
        LIMIT 3
    )
    SELECT 'CANDIDATE' AS kind, lot_hist_id AS identity, lot_id, wafer_id,
           chamber_id, step_id
    FROM lot_history WHERE lot_hist_id IN :candidate_ids
    UNION ALL
    SELECT 'PRIOR', lot_id, lot_id, NULL, NULL, NULL FROM prior_lots
    UNION ALL
    SELECT 'METROLOGY', metrology_id, lot_id, wafer_id, NULL, step_id
    FROM metrology WHERE lot_id = :lot_id AND step_id = :step_id
    """
).bindparams(bindparam("candidate_ids", expanding=True))


def read_inventory(
    connection: Connection,
    *,
    route: ResolvedIncidentRoute,
    current_lot_hist_ids: list[str],
    document_probe: DocumentSearchToolResult,
) -> Inventory:
    """Recount from DB rows; never accept supplied counts or an oracle.

    Does not open/commit a transaction or create a connection. Connection profile,
    read-only privileges and route/graph/document snapshot provenance must be
    checked by the live runner before calling this function.
    """
    from app.common.tool_contracts import DocumentSearchToolResult

    graphs = [
        g for g in route.graph_evidence if g.chamber_id == route.incident.chamber_id
    ]
    if len(graphs) != 1 or not graphs[0].model_code:
        raise EvidenceError("U10_INVENTORY_SCOPE_INVALID")
    graph = graphs[0]
    state = ObservationContext(
        "u10-inventory",
        route,
        current_lot_hist_ids,
        document_model_code=graph.model_code,
    )
    context = state.build_context()
    candidates = context.candidates.fdc
    current = [c for c in candidates if c.relation == "CURRENT"]
    if len({c.step_id for c in current}) != 1:
        raise EvidenceError("U10_INVENTORY_SCOPE_INVALID")
    step_id = current[0].step_id
    if step_id != graph.process_step_id:
        raise EvidenceError("U10_INVENTORY_SCOPE_INVALID")
    siblings = graph.sibling_chamber_ids
    if len(siblings) > 1 or any(not s or s == context.chamber_id for s in siblings):
        # The fixed final graph has one other chamber; don't invent a tie-break.
        raise EvidenceError("U10_INVENTORY_SIBLING_INVALID")
    if not isinstance(document_probe, DocumentSearchToolResult):
        raise EvidenceError("U10_INVENTORY_DOCUMENT_PROBE_INVALID")
    probe = DocumentSearchToolResult.model_validate(document_probe.model_dump())
    if not probe.ok or any(
        h.model_code not in (None, graph.model_code) for h in probe.hits
    ):
        raise EvidenceError("U10_INVENTORY_DOCUMENT_PROBE_INVALID")
    expected = {
        c.lot_hist_id: (context.lot_id, c.wafer_id, c.chamber_id, c.step_id)
        for c in candidates
    }
    if len(expected) != len(candidates):
        raise EvidenceError("U10_INVENTORY_SCOPE_INVALID")
    rows = (
        connection.execute(
            _INVENTORY,
            dict(
                lot_id=context.lot_id,
                chamber_id=context.chamber_id,
                step_id=step_id,
                candidate_ids=list(expected),
            ),
        )
        .mappings()
        .all()
    )
    observed = {}
    prior, metrology = set(), set()
    for row in rows:
        identity = row["identity"]
        if not isinstance(identity, str) or not identity:
            raise EvidenceError("U10_INVENTORY_ROWS_INVALID")
        if row["kind"] == "CANDIDATE":
            if identity in observed:
                raise EvidenceError("U10_INVENTORY_ROWS_INVALID")
            observed[identity] = tuple(
                row[k] for k in ("lot_id", "wafer_id", "chamber_id", "step_id")
            )
        elif row["kind"] == "PRIOR":
            if (
                identity in prior
                or identity == context.lot_id
                or row["lot_id"] != identity
            ):
                raise EvidenceError("U10_INVENTORY_ROWS_INVALID")
            prior.add(identity)
        elif row["kind"] == "METROLOGY":
            if identity in metrology or (row["lot_id"], row["step_id"]) != (
                context.lot_id,
                step_id,
            ):
                raise EvidenceError("U10_INVENTORY_ROWS_INVALID")
            metrology.add(identity)
        else:
            raise EvidenceError("U10_INVENTORY_ROWS_INVALID")
    if observed != expected:
        raise EvidenceError("U10_INVENTORY_CANDIDATE_DRIFT")
    adjacent = [c for c in candidates if c.relation == "UPSTREAM"]
    if not adjacent:
        adjacent = [c for c in candidates if c.relation == "DOWNSTREAM"]
    return Inventory(
        current_wafers=len({c.wafer_id for c in current}),
        adjacent=Adjacent(
            relation=adjacent[0].relation if adjacent else "NONE",
            wafers=len({c.wafer_id for c in adjacent}),
        ),
        sibling_chamber_id=siblings[0] if siblings else None,
        history_prior_lots=len(prior),
        metrology_samples=len(metrology),
        documents=True,  # A successful empty search is still an available tool.
    )


def verify_fixture_inventory(
    fixture: Fixture,
    connection: Connection,
    *,
    route: ResolvedIncidentRoute,
    current_lot_hist_ids: list[str],
    document_probe: DocumentSearchToolResult,
) -> Inventory:
    """Compare the pinned declaration with an independent readback; no rewriting."""
    fixture = Fixture.model_validate(fixture.model_dump()).model_copy(deep=True)
    actual = read_inventory(
        connection,
        route=route,
        current_lot_hist_ids=current_lot_hist_ids,
        document_probe=document_probe,
    )
    if (
        actual != fixture.candidate_inventory
        or actual.dimensions() != fixture.expected_compared.model_dump()
    ):
        raise EvidenceError("U10_INVENTORY_MISMATCH")
    return actual
