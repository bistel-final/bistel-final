"""Portable synthetic source tests. No final ZIP, Docker, DB or provider required.

The source generator is test-only; production always requires the final ZIP and
its independently pinned projection. Real-source PG checks are run separately.
"""

import copy
import csv
import io
import json
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.agent import u10_fixture_source as source_module
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_counterfactual import SCENARIOS, _r03, build_route, snapshot_payload
from app.agent.u10_evidence import project_read_evidence
from app.agent.u10_fixture_bundle import (
    build_benchmark,
    check_treatments,
    load_snapshot,
    oracle_for,
    write_bundle,
)
from app.agent.u10_fixture_database import (
    isolated_database,
    local_only_command,
    restored_inventory,
)
from app.agent.u10_fixture_tools import FixtureTools
from app.agent.u10_read_adapter import ReadAdapter


def synthetic_source():
    rows, evaluation, alarms, metrology = [], [], [], []
    counters = {}
    r03 = {
        (4, "EQP04-PM2"): "ET_REFL",
        (5, "EQP05-PM1"): "ET_PRES",
        (6, "EQP06-PM1"): "ET_ESC",
    }
    scopes = {(lot, chamber) for _, lot, chamber, _ in SCENARIOS}
    for lot_no in range(1, 13):
        lot = f"LOT{lot_no:03}"
        for wafer_no in range(1, 26):
            wafer = f"{lot}-W{wafer_no:02}"
            pm = 1 if wafer_no % 2 else 2
            for step_no, step in enumerate(("CT-PHOTO", "CT-ETCH")):
                eq = (lot_no - 1) % 3 + 1 + 3 * step_no
                equipment = f"EQP{eq:02}"
                chamber = f"{equipment}-PM{pm}"
                counters[chamber] = counters.get(chamber, 0) + 1
                lh = f"LH-{len(rows)+1:05}"
                at = f"2026-08-{lot_no:02} {10+step_no:02}:00:00"
                rows.append(
                    dict(
                        lot_hist_id=lh,
                        lot_id=lot,
                        wafer_no=str(wafer_no),
                        wafer_id=wafer,
                        step_id=step,
                        area_id="AREA",
                        equipment_id=equipment,
                        chamber_id=chamber,
                        recipe_id="RECIPE",
                        track_in_at=at,
                        track_out_at=at,
                        chamber_wafer_cum=str(counters[chamber]),
                    )
                )
                parameters = (
                    ("ET_ESC", "ET_PRES", "ET_REFL", "ET_RF")
                    if step_no
                    else ("PH_DOSE", "PH_FOCUS", "PH_TEMP", "PH_SPIN")
                )
                special = r03.get((lot_no, chamber)) if wafer_no <= 6 else None
                for parameter in parameters:
                    evaluation.append(
                        dict(
                            lot_hist_id=lh,
                            parameter=parameter,
                            step_no="1",
                            alarm_type="OOS" if parameter == special else "IN",
                        )
                    )
                if special or ((lot, chamber) in scopes and wafer_no <= 4):
                    parameter = special or parameters[0]
                    for seq in range(3 if special else 1):
                        alarms.append(
                            dict(
                                alarm_id=f"T-{len(alarms)+1:04}",
                                lot=lot,
                                chamber=chamber,
                                wafer=wafer,
                                parameter=parameter,
                                step_no="1",
                                seq_no=str(seq),
                                occurred_at=at,
                            )
                        )
                if wafer_no <= 2:
                    metrology.append(
                        dict(
                            metrology_id=f"M-{len(metrology)+1:04}",
                            lot_id=lot,
                            wafer_id=wafer,
                            step_id=step,
                            measure_type="CD",
                            measured_value="5",
                            spec_lower="0",
                            spec_upper="10",
                            alarm_result="PASS",
                            measured_at=at,
                        )
                    )
    return dict(
        schema_version="u10-final-source-v1",
        archive_sha256="a" * 64,
        member_sha256={},
        graph_source=(Path(__file__).parents[1] / "fixtures/master.cypher").read_text(),
        tables=dict(
            lot_history=rows,
            evaluation=evaluation,
            trace_alarm_history=alarms,
            summary_alarm_history=[],
            metrology=metrology,
        ),
    )


@pytest.fixture
def source(monkeypatch):
    value = synthetic_source()
    monkeypatch.setattr(
        source_module,
        "EXPECTED_PROJECTION_SHA256",
        source_module.source_projection_sha256(value),
    )
    return value


@pytest.fixture
def engine():
    value = create_engine("sqlite://", poolclass=NullPool)
    yield value
    value.dispose()


class InlineDeadline:
    def call(self, fn, payload, *, seconds):
        assert seconds > 0
        return fn(payload)


@pytest.mark.parametrize("fixture_id", [s[0] for s in SCENARIOS])
def test_all_cf_routes_treatments_and_production_read_adapter(source, fixture_id):
    tools = FixtureTools(source, fixture_id)
    check_treatments(tools)
    assert tools.document_calls == 0
    bound, _ = tools.fixed_inputs()
    adapter = ReadAdapter(tools.context, tools.ports(), InlineDeadline())
    for slot, tool in (
        ("CURRENT_FDC", "get_fdc_summary"),
        ("ADJACENT_FDC", "get_fdc_summary"),
        ("EQUIPMENT", "get_equipment_context"),
        ("HISTORY", "get_chamber_parameter_history"),
        ("SIBLING", "get_chamber_parameter_history"),
        ("METROLOGY", "get_metrology_result"),
    ):
        internal = (
            tools.context.resolve_history_context(bound[slot])
            if slot in {"HISTORY", "SIBLING"}
            else None
        )
        assert adapter(tool, bound[slot], internal).status == "SUCCESS"
    query = {
        "query": "upper lower " + tools.extra_parameter,
        "model_code": tools.graph.model_code,
    }
    first = adapter("search_documents", query)
    assert first.status == ("TIMEOUT" if fixture_id == "CF-3" else "SUCCESS")
    if fixture_id == "CF-3":
        assert adapter("search_documents", query).status == "SUCCESS"
    ids, dimensions = oracle_for(tools)
    assert len(ids.values) >= 2
    assert dimensions == {
        "CF-6": ["upstream"],
        "CF-7": ["sibling"],
        "CF-8": ["history"],
    }.get(fixture_id, [])


def test_actual_r03_derivation_and_route_membership(source):
    records = _r03(source["tables"])
    assert len(records) == 3 and all(len(r.member_alarm_refs) == 9 for r in records)
    route, ids = build_route(source, "CF-4")
    assert len(ids) == 3
    expected = {
        a["alarm_id"]
        for a in source["tables"]["trace_alarm_history"]
        if a["lot"] == "LOT004" and a["chamber"] == "EQP04-PM2"
    }
    assert {
        a.alarm_id for a in route.incident.member_alarms if a.source == "TRACE"
    } == expected
    assert sum(a.source == "R03" for a in route.incident.member_alarms) == 1


def test_cf2_extra_current_not_baseline_adjacent_and_cf6_real_upstream_parameter(
    source,
):
    tools = FixtureTools(source, "CF-2")
    bound, _ = tools.fixed_inputs()
    assert tools.fdc(bound["CURRENT_FDC"]).parameters[0].oos_point_cnt == 0
    assert tools.fdc(bound["ADJACENT_FDC"]).parameters[0].oos_point_cnt == 0
    extra = tools.fdc({"lot_hist_id": tools.current_ids[1]}).parameters[0]
    assert extra.parameter_id == tools.extra_parameter and extra.oos_point_cnt > 0
    tools = FixtureTools(source, "CF-6")
    bound, _ = tools.fixed_inputs()
    assert tools.fdc(bound["ADJACENT_FDC"]).parameters[0].parameter_id.startswith("PH_")


@pytest.mark.parametrize("fixture_id", ["CF-6", "CF-7", "CF-8"])
def test_missing_treatment_cannot_be_sealed(source, fixture_id, monkeypatch):
    tools = FixtureTools(source, fixture_id)
    if fixture_id == "CF-6":
        original = tools.fdc

        def fdc(request):
            result = original(request)
            result.parameters[0].oos_point_cnt = 0
            return result

        monkeypatch.setattr(tools, "fdc", fdc)
    else:
        original = tools.history

        def history(request):
            result = original(request)
            if fixture_id == "CF-7":
                result.current.oos_wafers = 1
            else:
                result.trend = "STABLE"
            return result

        monkeypatch.setattr(tools, "history", history)
    with pytest.raises(EvidenceError, match="U10_CF_TREATMENT_INVALID"):
        check_treatments(tools)


def test_bundle_builder_rejects_missing_history_treatment(source, engine, monkeypatch):
    actual_history = FixtureTools.history
    corrupted = []

    def history(self, request):
        result = actual_history(self, request)
        if self.scenario == "HISTORY_DRIFT":
            result.trend = "STABLE"
            corrupted.append(self.scenario)
        return result

    monkeypatch.setattr(FixtureTools, "history", history)
    # Keep the source projection and SQL intact; exercise the builder itself,
    # not a direct call to check_treatments.
    with restored_inventory(engine, source) as conn:
        with pytest.raises(EvidenceError, match="^U10_CF_TREATMENT_INVALID$"):
            build_benchmark(source, conn)
    assert corrupted


def test_bundle_inventory_private_no_clobber_and_no_oracle_in_snapshot(
    source, engine, tmp_path
):
    with restored_inventory(engine, source) as conn:
        benchmark, snapshots = build_benchmark(source, conn)
        assert conn.execute(text("SELECT count(*) FROM lot_history")).scalar() == 600
        with pytest.raises(OperationalError):
            conn.execute(text("DELETE FROM lot_history"))
    root = tmp_path / "inputs"
    sha = write_bundle(root, benchmark, snapshots)
    assert sha == digest((root / "benchmark.json").read_bytes())
    assert root.stat().st_mode & 0o777 == 0o700
    for path in root.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    for fixture in benchmark.fixtures:
        assert fixture.candidate_inventory.metrology_samples == 2
        raw = (root / (fixture.fixture_id + ".json")).read_bytes()
        assert load_snapshot(raw, fixture.fixture_id) == source
        assert digest(raw) == fixture.initial_snapshot_sha256
        assert (
            b"fault_code" not in raw
            and b"oracle" not in raw
            and b"expected_action" not in raw
        )
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(FileExistsError):
        write_bundle(root, benchmark, snapshots)
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize(
    "change", ["source", "scenario", "fixture", "oracle", "self_hash"]
)
def test_snapshot_tamper_fails_even_if_caller_rehashes(source, change):
    payload = snapshot_payload(copy.deepcopy(source), "CF-1")
    if change in {"source", "self_hash"}:
        payload["source"]["tables"]["lot_history"][0]["chamber_id"] = "OTHER"
        payload["source_projection_sha256"] = source_module.source_projection_sha256(
            payload["source"]
        )
    elif change == "scenario":
        payload["scenario_sha256"] = "0" * 64
    elif change == "fixture":
        payload["fixture_id"] = "CF-2"
    else:
        payload["oracle"] = {"expected_action": "APPROVE"}
    with pytest.raises(EvidenceError):
        load_snapshot(canonical_json(payload) + b"\n", "CF-1")


def test_local_only_docker_wrapper_and_image_pin(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append(argv))
    local_only_command(["docker", "pull", "--quiet", "sha256:" + "a" * 64])
    local_only_command(["docker", "run", "-d", "image"])
    assert seen[0][:3] == ["docker", "image", "inspect"]
    assert seen[1][:3] == ["docker", "run", "--pull=never"]
    with pytest.raises(EvidenceError, match="U10_POSTGRES_IMAGE_PIN_INVALID"):
        with isolated_database("postgres:latest"):
            pytest.fail("must reject before Docker")


def test_prepare_cli_32_fresh_contexts_without_llm_or_artifacts(
    source, engine, tmp_path, monkeypatch, capsys
):
    from scripts import prepare_u10_fixtures as cli

    @contextmanager
    def database(image):
        yield engine

    monkeypatch.setattr(cli, "isolated_database", database)
    monkeypatch.setattr(cli, "load_final_source", lambda _: source)
    args = [
        "--source-archive",
        "unused",
        "--output",
        str(tmp_path / "inputs"),
        "--postgres-image-id",
        "sha256:" + "a" * 64,
        "--dry-run",
    ]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["preparations_verified"] == 32 and result["attempts_executed"] == 0
    assert result["llm_calls"] == 0 and result["artifact_issued"] is False
    assert result["evaluation_receipt_issued"] is False
    assert cli.main(args) == 1


def test_zip_member_pins_projection_and_ground_truth_removal(
    source, tmp_path, monkeypatch
):
    from scripts import intake_final_zip as pins

    archive = tmp_path / "synthetic.zip"
    tables = copy.deepcopy(source["tables"])
    for row in tables["lot_history"]:
        row["fault_code"] = "DO_NOT_EXPORT"
    members = {}
    for table, rows in tables.items():
        stream = io.StringIO()
        fields = list(rows[0]) if rows else ["alarm_id"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        members[source_module.PREFIX + "data/" + table + ".csv"] = (
            stream.getvalue().encode()
        )
    members[source_module.PREFIX + "ontology/master.cypher"] = source[
        "graph_source"
    ].encode()
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, value in members.items():
            zipped.writestr(name, value)
    monkeypatch.setattr(pins, "EXPECTED_ARCHIVE_SHA256", digest(archive.read_bytes()))
    monkeypatch.setattr(
        pins, "PINNED_MEMBER_HASHES", {k: digest(v) for k, v in members.items()}
    )
    monkeypatch.setattr(source_module, "COUNTS", {k: len(v) for k, v in tables.items()})
    value = source_module.load_final_source(archive)
    assert b"DO_NOT_EXPORT" not in canonical_json(
        value
    ) and b"fault_code" not in canonical_json(value)
    pins.PINNED_MEMBER_HASHES[next(iter(members))] = "0" * 64
    with pytest.raises(EvidenceError, match="U10_SOURCE_MEMBER_MISMATCH"):
        source_module.load_final_source(archive)


def test_wrong_archive_fails_before_zip_parser(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(EvidenceError, match="U10_SOURCE_ARCHIVE_MISMATCH"):
        source_module.load_final_source(path)


@pytest.mark.parametrize("fixture_id", [s[0] for s in SCENARIOS])
def test_oracle_only_contains_obtainable_real_projection_ids(source, fixture_id):
    tools = FixtureTools(source, fixture_id)
    available = set(tools.context.initial_evidence_ids().values)
    for candidate in tools.candidates.fdc:
        result = tools.fdc({"lot_hist_id": candidate.lot_hist_id})
        available.update(project_read_evidence("get_fdc_summary", result).values)
    query = {
        "query": "above upper below lower " + tools.extra_parameter,
        "model_code": tools.graph.model_code,
    }
    tools.documents(query)
    available.update(
        project_read_evidence("search_documents", tools.documents(query)).values
    )
    assert set(oracle_for(tools)[0].values) <= available


@pytest.mark.parametrize("kind", ["missing", "extra", "drift"])
def test_invalid_bundle_fails_before_directory_creation(source, engine, tmp_path, kind):
    with restored_inventory(engine, source) as conn:
        benchmark, snapshots = build_benchmark(source, conn)
    if kind == "missing":
        snapshots.pop("CF-8")
    elif kind == "extra":
        snapshots["CF-9"] = snapshots["CF-8"]
    else:
        snapshots["CF-8"]["scenario_sha256"] = "f" * 64
    root = tmp_path / "inputs"
    with pytest.raises(EvidenceError):
        write_bundle(root, benchmark, snapshots)
    assert not root.exists()


def test_new_modules_import_without_provider_db_or_process_io():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys, socket, subprocess
def forbidden(*args, **kwargs):
    raise AssertionError('import IO')
socket.create_connection = subprocess.run = forbidden
from app.agent import u10_counterfactual, u10_fixture_source, u10_fixture_tools
from app.agent import u10_fixture_bundle, u10_fixture_database
from scripts import prepare_u10_fixtures
assert 'app.common.llm' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.agent.react' not in sys.modules
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_concrete_loader_to_verified_prepare_seam(source, engine, tmp_path):
    from app.agent.u10_batch import BatchBinding, execution_plan
    from app.agent.u10_preparation import (
        RuntimePorts,
        counterfactual_loader,
        verified_preparer,
    )

    with restored_inventory(engine, source) as conn:
        benchmark, snapshots = build_benchmark(source, conn)
    root = tmp_path / "inputs"
    write_bundle(root, benchmark, snapshots)
    events = []

    @contextmanager
    def runtime(key):
        events.append(("open", key.execution_order))

        def forbidden(*args, **kwargs):
            pytest.fail("prepare must not call provider or claim effects")

        try:
            yield RuntimePorts(InlineDeadline(), forbidden, forbidden, forbidden)
        finally:
            events.append(("close", key.execution_order))

    binding = BatchBinding(
        "a" * 40,
        digest(canonical_json(benchmark)),
        "b" * 64,
        benchmark.tool_contract_sha256,
        benchmark.fixed_policy_sha256,
    )
    prepare = verified_preparer(
        benchmark=benchmark,
        snapshots={s[0]: root / (s[0] + ".json") for s in SCENARIOS},
        binding=binding,
        authorize=lambda bound: bound == binding,  # Test authority, no export.
        open_snapshot=counterfactual_loader(engine, runtime),
    )
    contexts = []
    for key in execution_plan():
        with prepare(key) as env:
            contexts.append(env.context)
            assert env.context.hypothesis_inputs()["fdc_evidence"] == ()
            assert env.context.hypothesis_inputs()["document_evidence"] is None
    assert len({id(c) for c in contexts}) == 32
    assert events == [(phase, n) for n in range(1, 33) for phase in ("open", "close")]
