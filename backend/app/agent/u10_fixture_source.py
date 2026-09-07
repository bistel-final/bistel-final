"""Read the pinned final ZIP into a label-free, bounded experiment projection.

Never extracts or executes archive members. No runtime DB, graph or provider IO.
Canonical source hashes are reused from Common intake, not a second manifest.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from app.agent.release_artifacts import EvidenceError, canonical_json, digest

PREFIX = "project/repository/sample/"
COUNTS = {
    "lot_history": 600,
    "trace_alarm_history": 138,
    "summary_alarm_history": 51,
    "evaluation": 4800,
    "metrology": 48,
}
LOT_COLUMNS = (
    "lot_hist_id",
    "lot_id",
    "wafer_no",
    "wafer_id",
    "step_id",
    "area_id",
    "equipment_id",
    "chamber_id",
    "recipe_id",
    "track_in_at",
    "track_out_at",
    "chamber_wafer_cum",
)
# Independently measured from the pinned ZIP after LOT_COLUMNS projection.
# A snapshot's self-declared source hash cannot replace this code-owned pin.
EXPECTED_PROJECTION_SHA256 = (
    "178e72098f16978d208d7ee45db9f28ed828cb55735c91126d67169f126013c8"
)


def load_final_source(archive: Path) -> dict:
    from scripts.intake_final_zip import EXPECTED_ARCHIVE_SHA256, PINNED_MEMBER_HASHES

    with archive.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024 or digest(raw) != EXPECTED_ARCHIVE_SHA256:
        raise EvidenceError("U10_SOURCE_ARCHIVE_MISMATCH")
    tables, hashes = {}, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        names = zipped.namelist()
        for name, count in COUNTS.items():
            member = PREFIX + "data/" + name + ".csv"
            content = _member(zipped, names, member, PINNED_MEMBER_HASHES[member])
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
            if len(rows) != count:
                raise EvidenceError("U10_SOURCE_POPULATION_INVALID")
            # Only this raw source member contains a fault label. Project it out
            # immediately, before creating any snapshot or provider input.
            if name == "lot_history":
                rows = [{k: row[k] for k in LOT_COLUMNS} for row in rows]
            tables[name] = rows
            hashes[member] = digest(content)
        member = PREFIX + "ontology/master.cypher"
        graph = _member(zipped, names, member, PINNED_MEMBER_HASHES[member])
        hashes[member] = digest(graph)
    return {
        "schema_version": "u10-final-source-v1",
        "archive_sha256": digest(raw),
        "member_sha256": hashes,
        "tables": tables,
        "graph_source": graph.decode("utf-8"),
    }


def _member(zipped, names, member, expected):
    if names.count(member) != 1 or zipped.getinfo(member).file_size > 8 * 1024 * 1024:
        raise EvidenceError("U10_SOURCE_MEMBER_INVALID")
    value = zipped.read(member)
    if digest(value) != expected:
        raise EvidenceError("U10_SOURCE_MEMBER_MISMATCH")
    return value


def source_projection_sha256(source: dict) -> str:
    return digest(canonical_json(source))


def verify_source_projection(source: dict) -> str:
    actual = source_projection_sha256(source)
    if actual != EXPECTED_PROJECTION_SHA256:
        raise EvidenceError("U10_SOURCE_PROJECTION_MISMATCH")
    return actual
