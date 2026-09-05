"""B preparation seams with synthetic private inputs, never research evidence."""

import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from app.agent import u10_source as source
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    write_private,
)
from app.agent.u10_batch import BatchBinding, execute_batch
from app.agent.u10_comparison import Benchmark
from app.agent.u10_export import ExportAuthorization, ExportGrant, guarded_call
from app.agent.u10_preparation import SnapshotSession, verified_preparer
from app.common.tool_contracts import DocumentSearchToolResult
from tests.unit.test_agent_u10_batch import inputs
from tests.unit.test_agent_u10_comparison import seal_benchmark
from tests.unit.test_agent_u10_fixed_attempt import parameters

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def binding():
    return BatchBinding("a" * 40, "b" * 64, "c" * 64, "d" * 64, "e" * 64)


def grant_payload(bound):
    return dict(
        schema_version="u10-data-export-grant-v1",
        purpose="U10_DATA_EXPORT_GRANT",
        approved_by="방대혁",
        binding=asdict(bound),
        issued_at="2026-09-05T11:00:00Z",
        expires_at="2026-09-05T13:00:00Z",
    )


def grant(tmp_path, bound=None, tweak=lambda p: None):
    tmp_path.chmod(0o700)
    bound = bound or binding()
    payload = grant_payload(bound)
    tweak(payload)
    path = tmp_path / "export.json"
    write_private(tmp_path, path.name, payload)
    return path, ExportAuthorization(path, digest(path.read_bytes()), clock=lambda: NOW)


def test_grant_exact_binding_and_no_rewrite(tmp_path):
    path, auth = grant(tmp_path)
    before = path.read_bytes()
    assert auth(binding()) is True and auth(binding()) is True
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("evaluated_revision", "f" * 40),
        ("benchmark_sha256", "f" * 64),
        ("llm_config_sha256", "f" * 64),
        ("tool_contract_sha256", "f" * 64),
        ("fixed_policy_sha256", "f" * 64),
        ("attempt_count", 20),
        ("attempt_count", True),
    ],
)
def test_grant_rejects_other_execution(tmp_path, field, value):
    _, auth = grant(tmp_path)
    with pytest.raises(EvidenceError, match="^U10_DATA_EXPORT_NOT_AUTHORIZED$"):
        auth(replace(binding(), **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "SMTP_SEND_GRANT"),
        ("purpose", "IMPLEMENTATION_BUDGET"),
        ("approved_by", "someone"),
        ("issued_at", "2026-09-05T12:00:01Z"),
        ("expires_at", "2026-09-05T12:00:00Z"),
        ("issued_at", "2026-02-30T11:00:00Z"),
        ("expires_at", "2026-09-05T10:00:00Z"),
    ],
)
def test_grant_purpose_owner_and_time_fail_closed(tmp_path, field, value):
    _, auth = grant(tmp_path, tweak=lambda p: p.update({field: value}))
    with pytest.raises(EvidenceError, match="^U10_DATA_EXPORT_NOT_AUTHORIZED$"):
        auth(binding())


@pytest.mark.parametrize("change", ["delete", "replace", "mode", "extra", "pin"])
def test_private_grant_is_revalidated_every_time(tmp_path, change):
    path, auth = grant(tmp_path)
    assert auth(binding())
    if change == "delete":
        path.unlink()
    elif change == "mode":
        path.chmod(0o644)
    elif change in {"extra", "replace"}:
        payload = grant_payload(binding())
        payload["password" if change == "extra" else "approved_by"] = "secret"
        path.write_bytes(canonical_json(payload))
    else:
        auth = ExportAuthorization(path, "0" * 64, clock=lambda: NOW)
    with pytest.raises(EvidenceError, match="^U10_DATA_EXPORT_NOT_AUTHORIZED$"):
        auth(binding())


def test_guard_rechecks_each_provider_entry(tmp_path):
    path, auth = grant(tmp_path)
    calls = []
    operation = guarded_call(auth, binding(), lambda value: calls.append(value))
    operation(1)
    path.unlink()
    with pytest.raises(EvidenceError, match="U10_DATA_EXPORT_NOT_AUTHORIZED"):
        operation(2)
    assert calls == [1]


def test_source_contract_uses_real_schemas_and_projection():
    spec = source.tool_contract_spec()
    assert len(spec["tools"]) == 5
    assert spec["projection_sha256"] == source.projection_sha256()
    assert (
        "history_candidate_id"
        in spec["selector"]["schema"]["properties"]["arguments"]["properties"]
    )
    assert set(spec["source_sha256"]) == set(source.SOURCE_FILES)
    assert source.tool_contract_sha256() == digest(canonical_json(spec))
    spec["selector"]["schema"]["type"] = "null"
    assert source.tool_contract_spec()["selector"]["schema"]["type"] == "object"


@pytest.mark.parametrize("change", ["projection", "source", "schema", "fixed"])
def test_source_change_invalidates_pin(monkeypatch, change):
    params, _, _, _ = inputs()
    b = params["benchmark"].model_copy(
        update={"tool_contract_sha256": source.tool_contract_sha256()}
    )
    assert source.verify_source_binding(b) == b.tool_contract_sha256
    if change == "projection":
        monkeypatch.setattr(source, "projection_sha256", lambda: "0" * 64)
    elif change == "source":
        actual = source.source_hashes()
        actual["app/agent/react.py"] = "0" * 64
        monkeypatch.setattr(source, "source_hashes", lambda: actual)
    elif change == "fixed":
        b = b.model_copy(update={"fixed_policy_sha256": "0" * 64})
    else:
        from app.agent import react

        monkeypatch.setattr(react, "REACT_SELECT_SCHEMA", {"type": "null"})
    with pytest.raises(
        EvidenceError, match="TOOL_CONTRACT_MISMATCH|FIXED_POLICY_MISMATCH"
    ):
        source.verify_source_binding(b)


def batch_setup(tmp_path, *, tweak=lambda session: session, engine_factory=None):
    """Replicated DTO is test-only; NOT the eight scientific CF scenarios."""
    tmp_path.chmod(0o700)
    params, _, _, _ = inputs()
    payload = params["benchmark"].model_dump()
    payload["tool_contract_sha256"] = source.tool_contract_sha256()
    snapshots = {}
    for fixture in payload["fixtures"]:
        raw_payload = {"test_fixture": fixture["fixture_id"], "candidate": "LH-REP"}
        path = tmp_path / (fixture["fixture_id"] + ".json")
        write_private(tmp_path, path.name, raw_payload)
        snapshots[fixture["fixture_id"]] = path
        fixture["initial_snapshot_sha256"] = digest(path.read_bytes())
    seal_benchmark(payload)
    b = Benchmark.model_validate(payload)
    params.update(
        benchmark=b,
        expected_tool_contract_sha256=b.tool_contract_sha256,
        expected_benchmark_sha256=digest(canonical_json(b)),
    )
    bound = BatchBinding(
        params["evaluated_revision"],
        params["expected_benchmark_sha256"],
        digest(canonical_json(params["llm"])),
        b.tool_contract_sha256,
        b.fixed_policy_sha256,
    )
    path, authorize = grant(tmp_path, bound)
    events = []
    template_prepare = params["prepare"]

    @contextmanager
    def open_snapshot(key, raw):
        data = json.loads(raw)
        assert set(data) == {"test_fixture", "candidate"}
        events.append(("open", key.execution_order))
        engine = (
            engine_factory() if engine_factory else create_engine("sqlite:///:memory:")
        )
        try:
            with engine.connect() as conn, template_prepare(key) as env:
                conn.execute(
                    text(
                        "CREATE TABLE lot_history (lot_hist_id TEXT, lot_id TEXT, "
                        "wafer_id TEXT, chamber_id TEXT, step_id TEXT, "
                        "track_in_at TEXT)"
                    )
                )
                conn.execute(
                    text(
                        "CREATE TABLE metrology (metrology_id TEXT, lot_id TEXT, "
                        "wafer_id TEXT, step_id TEXT)"
                    )
                )
                route = env.context.hypothesis_inputs()["route"]
                step = route.wafer_routes[0].steps[0]
                conn.execute(
                    text(
                        "INSERT INTO lot_history VALUES "
                        "(:id,:lot,:wafer,:chamber,:step,:at)"
                    ),
                    dict(
                        id=data["candidate"],
                        lot=step.lot_id,
                        wafer=step.wafer_id,
                        chamber=step.chamber_id,
                        step=step.step_id,
                        at=step.track_in_at.isoformat(),
                    ),
                )
                yield tweak(
                    SnapshotSession(
                        conn,
                        route,
                        [data["candidate"]],
                        DocumentSearchToolResult(ok=True, hits=[]),
                        env,
                    )
                )
        finally:
            engine.dispose()
            events.append(("close", key.execution_order))

    params["authorize"] = authorize
    prep = dict(
        benchmark=b,
        snapshots=snapshots,
        binding=bound,
        authorize=authorize,
        open_snapshot=open_snapshot,
    )
    return params, prep, events, path


def test_private_grant_snapshot_sql_prepare_and_32_attempt_seam(tmp_path):
    params, prep, events, path = batch_setup(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    params["prepare"] = verified_preparer(**prep)
    artifact = execute_batch(**params)
    assert len(artifact.attempts) == 32
    assert events == [(e, n) for n in range(1, 33) for e in ("open", "close")]
    assert all(a.completion for a in artifact.attempts)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert (
        len(
            ExportGrant.model_validate(
                json.loads(path.read_bytes())
            ).binding.model_dump()
        )
        == 6
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ("snapshot", "SNAPSHOT_MISMATCH"),
        ("population", "U10_SNAPSHOT_POPULATION_INVALID"),
        ("binding", "U10_PREPARATION_BINDING_MISMATCH"),
        ("grant", "U10_DATA_EXPORT_NOT_AUTHORIZED"),
    ],
)
def test_prepare_rejects_before_open(tmp_path, change, code):
    params, prep, events, path = batch_setup(tmp_path)
    if change == "snapshot":
        prep["snapshots"]["CF-1"].write_bytes(b"{}")
    elif change == "population":
        del prep["snapshots"]["CF-8"]
    elif change == "binding":
        prep["binding"] = replace(prep["binding"], benchmark_sha256="0" * 64)
    else:
        path.unlink()
    with pytest.raises(EvidenceError, match="^" + code + "$"):
        params["prepare"] = verified_preparer(**prep)
        execute_batch(**params)
    assert events == []


def test_inventory_sql_mismatch_closes_before_provider(tmp_path):
    def tweak(s):
        s.connection.execute(text("DELETE FROM lot_history"))
        return s

    params, prep, events, _ = batch_setup(tmp_path, tweak=tweak)
    params["prepare"] = verified_preparer(**prep)
    with pytest.raises(EvidenceError, match="^U10_INVENTORY_CANDIDATE_DRIFT$"):
        execute_batch(**params)
    assert events == [("open", 1), ("close", 1)]


def test_different_context_cannot_borrow_inventory(tmp_path):
    def tweak(s):
        p, _ = parameters(sibling=True)
        return replace(s, environment=replace(s.environment, context=p["context"]))

    params, prep, events, _ = batch_setup(tmp_path, tweak=tweak)
    params["prepare"] = verified_preparer(**prep)
    with pytest.raises(EvidenceError, match="^U10_PREPARATION_CONTEXT_MISMATCH$"):
        execute_batch(**params)
    assert events == [("open", 1), ("close", 1)]


def test_snapshot_drift_after_work_discards_batch(tmp_path):
    def tweak(s):
        real = s.environment.generate

        def generate(**kwargs):
            result = real(**kwargs)
            (tmp_path / "CF-1.json").write_bytes(b"{}")
            return result

        return replace(s, environment=replace(s.environment, generate=generate))

    params, prep, events, _ = batch_setup(tmp_path, tweak=tweak)
    params["prepare"] = verified_preparer(**prep)
    with pytest.raises(EvidenceError, match="^U10_SNAPSHOT_DRIFT$"):
        execute_batch(**params)
    assert events == [("open", 1), ("close", 1)]


def test_import_no_provider_or_db():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from app.agent import u10_source, u10_export, u10_preparation
assert 'app.common.llm' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.agent.react' not in sys.modules
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("kind", ["missing", "symlink"])
def test_source_cannot_be_missing_or_linked(tmp_path, monkeypatch, kind):
    target = tmp_path / "source.py"
    if kind == "symlink":
        (tmp_path / "other.py").write_text("pass\n")
        target.symlink_to(tmp_path / "other.py")
    monkeypatch.setattr(source, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(source, "SOURCE_FILES", ("source.py",))
    with pytest.raises(EvidenceError, match="^U10_SOURCE_FILE_INVALID$"):
        source.source_hashes()


def test_prepare_invalid_key_never_opens(tmp_path):
    from app.agent.u10_batch import execution_plan

    _, prep, events, _ = batch_setup(tmp_path)
    prepare = verified_preparer(**prep)
    bad = replace(next(execution_plan()), execution_order=32)
    with pytest.raises(EvidenceError, match="^U10_SNAPSHOT_POPULATION_INVALID$"):
        with prepare(bad):
            pytest.fail("unexpected scope")
    assert not events


def test_grant_revoked_during_prepare_prevents_generation(tmp_path):
    calls = []

    def tweak(s):
        (tmp_path / "export.json").unlink()
        return replace(
            s,
            environment=replace(s.environment, generate=lambda **kw: calls.append(kw)),
        )

    params, prep, events, _ = batch_setup(tmp_path, tweak=tweak)
    params["prepare"] = verified_preparer(**prep)
    with pytest.raises(EvidenceError):
        execute_batch(**params)
    assert not calls
    assert events == [("open", 1), ("close", 1)]
