"""U10 CF-1..8 code-owned scenarios over pinned, label-free final source.

Only experiment Tool observations are counterfactual. Source identities, alarm
membership, route, sibling availability and prior-lot counts remain factual.
Oracle is assembled separately and is never passed to ReadPorts or providers.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.release_artifacts import EvidenceError, canonical_json, digest

SCENARIOS = (
    ("CF-1", "LOT006", "EQP06-PM1", "DIRECTION"),
    ("CF-2", "LOT006", "EQP06-PM2", "EXTRA_FDC"),
    ("CF-3", "LOT007", "EQP01-PM1", "DOCUMENT_RECOVERY"),
    ("CF-4", "LOT004", "EQP04-PM2", "EARLY_STOP"),
    ("CF-5", "LOT009", "EQP03-PM1", "BELOW"),
    ("CF-6", "LOT006", "EQP06-PM1", "UPSTREAM"),
    ("CF-7", "LOT006", "EQP06-PM2", "SIBLING_NORMAL"),
    ("CF-8", "LOT009", "EQP03-PM1", "HISTORY_DRIFT"),
)


def _time(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo("Asia/Seoul")
    )


def _r03(tables):
    from app.common.enums import AlarmType
    from app.detection.rules import (
        R03GroupMember,
        build_r03_alarm_record,
        derive_r03_events,
    )

    by_id = {r["lot_hist_id"]: r for r in tables["lot_history"]}
    groups = defaultdict(list)
    for e in tables["evaluation"]:
        r = by_id[e["lot_hist_id"]]
        groups[(r["chamber_id"], e["parameter"], int(e["step_no"]))].append(
            R03GroupMember(
                r["lot_hist_id"],
                r["lot_id"],
                int(r["wafer_no"]),
                r["wafer_id"],
                int(r["chamber_wafer_cum"]),
                _time(r["track_in_at"]),
                AlarmType(e["alarm_type"]),
            )
        )
    records = []
    for (chamber, parameter, step), members in groups.items():
        for event in derive_r03_events(
            members,
            chamber_id=chamber,
            parameter_id=parameter,
            recipe_step_no=step,
            equipment_id=by_id[members[0].lot_hist_id]["equipment_id"],
        ):
            refs = []
            for member in event.members:
                alarms = [
                    a
                    for a in tables["trace_alarm_history"]
                    if a["wafer"] == member.wafer_id
                    and a["chamber"] == chamber
                    and a["parameter"] == parameter
                    and int(a["step_no"]) == step
                ]
                refs.extend(
                    a["alarm_id"]
                    for a in sorted(
                        alarms, key=lambda a: (int(a["seq_no"]), a["alarm_id"])
                    )
                )
            if len(refs) != 9:
                raise EvidenceError("U10_SOURCE_R03_INVALID")
            records.append(build_r03_alarm_record(event, member_alarm_ids=refs))
    if len(records) != 3:
        raise EvidenceError("U10_SOURCE_R03_INVALID")
    return records


def build_route(source, fixture_id):
    from app.agent.incident import ResolvedIncident
    from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute, WaferRoute
    from app.agent.routing_repository import RouteStep
    from app.agent.u10_fixture_source import verify_source_projection
    from app.common.schemas import AlarmRef
    from scripts.master_cypher import graph_fingerprint, parse_master_cypher

    verify_source_projection(source)
    matches = [s for s in SCENARIOS if s[0] == fixture_id]
    if not matches:
        raise EvidenceError("U10_FIXTURE_UNKNOWN")
    _, lot, chamber, _ = matches[0]
    tables = source["tables"]
    graph = parse_master_cypher(source["graph_source"])
    if len(graph.nodes) != 44 or len(graph.relationships) != 85:
        raise EvidenceError("U10_SOURCE_GRAPH_INVALID")
    rows = tables["lot_history"]
    current_rows = [r for r in rows if (r["lot_id"], r["chamber_id"]) == (lot, chamber)]
    if not current_rows:
        raise EvidenceError("U10_SOURCE_INCIDENT_INVALID")
    equipment = current_rows[0]["equipment_id"]
    node = next(
        n
        for n in graph.nodes
        if n.label == "Equipment" and n.properties["equipment_id"] == equipment
    )
    step_id = current_rows[0]["step_id"]
    next_edges = [r for r in graph.relationships if r.relation_type == "NEXT_STEP"]
    upstream = tuple(
        r.from_node.properties["step_id"]
        for r in next_edges
        if r.to_node.properties["step_id"] == step_id
    )
    downstream = tuple(
        r.to_node.properties["step_id"]
        for r in next_edges
        if r.from_node.properties["step_id"] == step_id
    )
    siblings = tuple(
        sorted(
            r.from_node.properties["chamber_id"]
            for r in graph.relationships
            if r.relation_type == "PART_OF"
            and r.to_node.properties["equipment_id"] == equipment
            and r.from_node.properties["chamber_id"] != chamber
        )
    )
    records = []
    wafer_alarms = defaultdict(list)
    for table, kind, priority in [
        ("trace_alarm_history", "TRACE", 1),
        ("summary_alarm_history", "SUMMARY", 2),
    ]:
        for alarm in tables[table]:
            if (alarm["lot"], alarm["chamber"]) == (lot, chamber):
                ref = AlarmRef(source=kind, alarm_id=alarm["alarm_id"])
                records.append(
                    (_time(alarm["occurred_at"]), priority, ref.alarm_id, ref)
                )
                wafer_alarms[alarm["wafer"]].append(ref)
    r03 = [r for r in _r03(tables) if (r.lot_id, r.chamber_id) == (lot, chamber)]
    for record in r03:
        ref = AlarmRef(source="R03", alarm_id=record.alarm_id)
        records.append((record.occurred_at, 0, ref.alarm_id, ref))
        for member in record.member_wafer_refs:
            wafer_alarms[member["wafer_id"]].append(ref)
    ordered = tuple(r[3] for r in sorted(records, key=lambda r: r[:3]))
    if not ordered:
        raise EvidenceError("U10_SOURCE_INCIDENT_INVALID")
    # Same existing target ceiling; R03's three wafers take precedence.
    chosen = (
        [m["wafer_id"] for r in r03 for m in r.member_wafer_refs]
        if r03
        else sorted(wafer_alarms)
    )
    chosen = list(dict.fromkeys(chosen))[:3]
    wafer_routes, current_ids = [], []
    for wafer in chosen:
        route_rows = sorted(
            [r for r in rows if r["lot_id"] == lot and r["wafer_id"] == wafer],
            key=lambda r: (_time(r["track_in_at"]), r["lot_hist_id"]),
        )
        if len(route_rows) != 2 or {r["step_id"] for r in route_rows} != {
            "CT-PHOTO",
            "CT-ETCH",
        }:
            raise EvidenceError("U10_SOURCE_ROUTE_INVALID")
        steps = []
        for r in route_rows:
            boundary = min(
                _time(p["track_in_at"])
                for p in rows
                if (p["lot_id"], p["chamber_id"], p["step_id"])
                == (lot, r["chamber_id"], r["step_id"])
            )
            steps.append(
                RouteStep(
                    r["lot_hist_id"],
                    lot,
                    wafer,
                    int(r["wafer_no"]),
                    r["step_id"],
                    r["area_id"],
                    r["equipment_id"],
                    r["chamber_id"],
                    r["recipe_id"],
                    _time(r["track_in_at"]),
                    _time(r["track_out_at"]),
                    boundary,
                )
            )
            if r["chamber_id"] == chamber:
                current_ids.append(r["lot_hist_id"])
        wafer_routes.append(WaferRoute(wafer, tuple(wafer_alarms[wafer]), tuple(steps)))
    relations = tuple(
        sorted(
            r.relation_id
            for r in graph.relationships
            if r.from_node == node or r.to_node == node or r in next_edges
        )
    )
    route = ResolvedIncidentRoute(
        ResolvedIncident(lot, chamber, ordered[0], ordered[0], ordered),
        tuple(wafer_routes),
        (
            GraphRouteEvidence(
                chamber,
                equipment,
                str(node.properties["model_code"]),
                step_id,
                upstream,
                downstream,
                relations,
                graph_fingerprint(graph),
                siblings,
            ),
        ),
        True,
        (),
    )
    return route, current_ids


def snapshot_payload(source, fixture_id):
    if fixture_id not in {s[0] for s in SCENARIOS}:
        raise EvidenceError("U10_FIXTURE_UNKNOWN")
    return {
        "schema_version": "u10-counterfactual-snapshot-v1",
        "fixture_id": fixture_id,
        "source": source,
        "source_projection_sha256": digest(canonical_json(source)),
        "scenario_sha256": digest(canonical_json(SCENARIOS)),
    }
