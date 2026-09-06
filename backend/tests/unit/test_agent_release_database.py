"""Synthetic SQLite executes production SELECTs; PG identity/txn are fakes.

HTTP uses MockTransport. This is not a shared PG/n8n integration result.
"""

import json
import sqlite3
import subprocess
import sys
import traceback
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from app.agent import release_database as db
from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS
from app.agent.release_approval_evidence import APPROVAL_EXECUTIONS, APPROVAL_READY
from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    write_private,
    write_private_bytes,
)
from app.agent.release_delivery import EmailCallback, verify_delivery
from app.agent.release_prepared import PreparedAttempt, config_digest
from app.agent.release_round import CapturedDelivery
from app.common.enums import DeliveryStatus
from tests.unit.test_agent_release import AT, REV, prepared_payload
from tests.unit.test_agent_release_n8n import (
    SECRET,
    VERSION,
    WF,
    Server,
    run_data,
    send_result,
)

START = datetime(2026, 9, 5, 1, 0, 1, 123456, UTC)
DONE = datetime(2026, 9, 5, 1, 0, 2, 123456, UTC)
TABLES = dict(
    runs="agent_run",
    actions="action_history",
    links="agent_run_action",
    approvals="approval_request",
    deliveries="action_delivery",
)


def data():
    rows = {k: [] for k in TABLES}
    for i, (lot, chamber) in enumerate(sorted(CANONICAL_INCIDENT_KEYS)):
        action = "MONITORING" if i < 5 else "WARNING" if i < 9 else "EQP_HOLD"
        rows["runs"].append(
            dict(
                agent_run_id=f"run-{i}",
                lot_id=lot,
                chamber_id=chamber,
                status="COMPLETED" if i < 9 else "WAITING_APPROVAL",
                autonomy_level=3,
                action=action,
                retry_of_run_id=None,
                started_at=START,
            )
        )
        rows["actions"].append(
            dict(
                action_id=f"action-{i}",
                lot_id=lot,
                chamber_id=chamber,
                action_code=action,
            )
        )
        rows["links"].append(
            dict(
                agent_run_id=f"run-{i}",
                action_id=f"action-{i}",
                lot_id=lot,
                chamber_id=chamber,
                link_role="CREATED",
            )
        )
        if i >= 9:
            rows["approvals"].append(
                dict(
                    approval_id=f"approval-{i}",
                    action_id=f"action-{i}",
                    agent_run_id=f"run-{i}",
                    status="PENDING",
                    decided_at=None,
                )
            )
        for channel in () if i < 5 else ("EMAIL",) if i < 9 else ("EMAIL", "MES_MOCK"):
            email = channel == "EMAIL"
            rows["deliveries"].append(
                dict(
                    action_id=f"action-{i}",
                    channel=channel,
                    status="SENT" if email else "BLOCKED",
                    request_hash=f"{i*2+(1 if email else 2):064x}",
                    attempt_count=1 if email else 0,
                    provider_message_id=f"smtp-{i}" if email else None,
                    started_at=START if email else None,
                    completed_at=DONE if email else None,
                )
            )
    return rows


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]


class Connection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.engine.events.append("ROLLBACK_CLOSE")
        self.engine.sql.execute("PRAGMA query_only=OFF")

    def exec_driver_sql(self, sql):
        assert sql in {
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
            "SET LOCAL statement_timeout = '10s'",
            "SET LOCAL lock_timeout = '2s'",
        }
        self.engine.events.append(sql)
        if sql.startswith("BEGIN"):
            self.engine.sql.execute("PRAGMA query_only=ON")

    def execute(self, statement):
        sql = str(statement)
        self.engine.events.append(sql)
        if statement is db.IDENTITY_SQL:
            return Result([deepcopy(self.engine.identity)])
        assert sql.lstrip().startswith("SELECT ")
        rows = [dict(row) for row in self.engine.sql.execute(sql).fetchall()]
        # Only emulate the PostgreSQL driver's aware timestamp decoding.
        for row in rows:
            for key, value in row.items():
                if key.endswith("_at") and value is not None:
                    row[key] = datetime.fromisoformat(value)
        return Result(rows)


class Engine:
    def __init__(self):
        self.url = SimpleNamespace(
            database="kosa_agent_e2e", username="kosa_app", host="postgres"
        )
        self.identity = dict(
            database_name="kosa_agent_e2e",
            role_name="kosa_app",
            read_only="on",
            isolation="repeatable read",
            system_identifier="12345",
        )
        self.events = []
        self.sql = sqlite3.connect(":memory:", isolation_level=None)
        self.sql.row_factory = sqlite3.Row
        self.sql.execute("ATTACH DATABASE ':memory:' AS public")
        for name, rows in data().items():
            table = TABLES[name]
            columns = list(rows[0])
            self.sql.execute(f"CREATE TABLE public.{table} ({', '.join(columns)})")
            for row in rows:
                values = [
                    v.isoformat() if isinstance(v, datetime) else v
                    for v in row.values()
                ]
                placeholders = ", ".join("?" for _ in columns)
                self.sql.execute(
                    f"INSERT INTO public.{table} VALUES ({placeholders})",
                    values,
                )

    def connect(self):
        self.events.append("CONNECT")
        return Connection(self)


@pytest.fixture
def engine(tmp_path):
    engine = Engine()
    (tmp_path / "cm-5.2").mkdir(mode=0o700)
    engine.evidence_root = tmp_path / "cm-5.2" / prepared_payload()["attempt_id"]
    engine.evidence_root.mkdir(mode=0o700)
    parent = engine.evidence_root
    for name in APPROVAL_EXECUTIONS.split("/")[:-1]:
        parent = parent / name
        parent.mkdir(mode=0o700)
    yield engine
    engine.sql.close()


def prepared():
    return PreparedAttempt.model_validate(prepared_payload())


def pins():
    return {f"run-{i}": f"action-{i}" for i in range(12)}


def read(engine):
    return db.read_delivery_database(engine, expected_identity=prepared().db_identity)


def bind(snapshot):
    return db.bind_email_targets(snapshot, expected_run_actions=pins(), resume_at=AT)


def test_real_select_projection_and_transaction_order(engine):
    snapshot = read(engine)
    assert [len(getattr(snapshot, k)) for k in TABLES] == [12, 12, 12, 3, 10]
    assert snapshot.deliveries[0].completed_at == DONE
    assert engine.events[:4] == [
        "CONNECT",
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SET LOCAL statement_timeout = '10s'",
        "SET LOCAL lock_timeout = '2s'",
    ]
    assert engine.events[4] == str(db.IDENTITY_SQL)
    assert engine.events[-1] == "ROLLBACK_CLOSE"
    targets = bind(snapshot)
    assert len(targets) == 7
    assert [t.email_kind for t in targets].count("WARNING_NOTIFY") == 4
    assert [t.email_kind for t in targets].count("APPROVAL_REQUEST") == 3


@pytest.mark.parametrize(
    "field,value",
    [("database", "kosa_agent"), ("username", "postgres"), ("host", "other")],
)
def test_configured_target_mismatch_before_connect(engine, field, value):
    setattr(engine.url, field, value)
    with pytest.raises(EvidenceError, match="TARGET_MISMATCH"):
        read(engine)
    assert engine.events == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_name", "kosa_agent"),
        ("role_name", "postgres"),
        ("system_identifier", "54321"),
        ("read_only", "off"),
        ("isolation", "read committed"),
    ],
)
def test_observed_identity_before_business_select(engine, field, value):
    engine.identity[field] = value
    with pytest.raises(EvidenceError, match="IDENTITY_MISMATCH"):
        read(engine)
    assert not any("FROM public." in e for e in engine.events)
    assert engine.events[-1] == "ROLLBACK_CLOSE"


@pytest.mark.parametrize("name", list(TABLES))
def test_cap_plus_one_detects_extra_rows(engine, name):
    table = TABLES[name]
    engine.sql.execute(
        f"INSERT INTO public.{table} SELECT * FROM public.{table} LIMIT 1"
    )
    with pytest.raises(EvidenceError, match="READ_FAILED"):
        read(engine)
    assert engine.events[-1] == "ROLLBACK_CLOSE"


@pytest.mark.parametrize(
    "table,key,field,value,code",
    [
        ("agent_run", "agent_run_id", "agent_run_id", "other", "RUN_BINDING"),
        ("agent_run", "agent_run_id", "lot_id", "other", "INCIDENT_POPULATION"),
        ("agent_run", "agent_run_id", "autonomy_level", 2, "RUN_BINDING"),
        ("agent_run", "agent_run_id", "retry_of_run_id", "old", "RUN_BINDING"),
        (
            "agent_run",
            "agent_run_id",
            "started_at",
            "2026-09-04T00:00:00+00:00",
            "RUN_BINDING",
        ),
        ("agent_run", "agent_run_id", "action", "WARNING", "RUN_BINDING"),
        ("action_history", "action_id", "lot_id", "other", "RUN_BINDING"),
        ("agent_run_action", "agent_run_id", "link_role", "REUSED", "RUN_BINDING"),
        ("agent_run_action", "agent_run_id", "action_id", "missing", "RUN_BINDING"),
    ],
)
def test_wrong_run_lineage(engine, table, key, field, value, code):
    selected = "action-0" if key == "action_id" else "run-0"
    engine.sql.execute(
        f"UPDATE public.{table} SET {field}=? WHERE {key}=?", (value, selected)
    )
    with pytest.raises(EvidenceError, match=code):
        bind(read(engine))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "APPROVED"),
        ("agent_run_id", "run-0"),
        ("decided_at", "2026-09-05T01:00:01+00:00"),
        ("approval_id", "approval-10"),
    ],
)
def test_pre_hitl_approval_lineage(engine, field, value):
    engine.sql.execute(
        f"UPDATE public.approval_request SET {field}=? WHERE approval_id='approval-9'",
        (value,),
    )
    with pytest.raises(EvidenceError, match="PRE_HITL"):
        bind(read(engine))


@pytest.mark.parametrize(
    "table,code",
    [
        ("agent_run", "RUN_BINDING"),
        ("action_history", "RUN_BINDING"),
        ("agent_run_action", "RUN_BINDING"),
        ("approval_request", "PRE_HITL"),
        ("action_delivery", "DELIVERY_POPULATION"),
    ],
)
def test_missing_rows_not_hidden(engine, table, code):
    engine.sql.execute(f"DELETE FROM public.{table}")
    with pytest.raises(EvidenceError, match=code):
        bind(read(engine))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("action_id", "action-0", "DELIVERY_POPULATION"),
        ("request_hash", "f" * 64, "DELIVERY_POPULATION"),
        ("completed_at", "2026-09-04T00:00:00+00:00", "DB_TIME"),
        ("completed_at", "2026-09-05T01:00:01+00:00", "DB_TIME"),
    ],
)
def test_delivery_topology_and_times(engine, field, value, code):
    engine.sql.execute(f"UPDATE public.action_delivery SET {field}=?", (value,))
    with pytest.raises(EvidenceError, match=code):
        bind(read(engine))


def test_run_failure_and_unsent_status_preserved(engine):
    engine.sql.execute(
        "UPDATE public.agent_run SET status='FAILED' WHERE agent_run_id='run-5'"
    )
    engine.sql.execute(
        "UPDATE public.action_delivery SET status='WAITING', completed_at=NULL "
        "WHERE action_id='action-5'"
    )
    snapshot = read(engine)
    assert len(bind(snapshot)) == 7
    assert next(r for r in snapshot.runs if r.run_id == "run-5").status == "FAILED"
    assert (
        next(d for d in snapshot.deliveries if d.action_id == "action-5").status
        == "WAITING"
    )


def test_action_population_after_valid_run_action_binding(engine):
    engine.sql.execute(
        "UPDATE public.agent_run SET action='WARNING' WHERE agent_run_id='run-0'"
    )
    engine.sql.execute(
        "UPDATE public.action_history SET action_code='WARNING' "
        "WHERE action_id='action-0'"
    )
    with pytest.raises(EvidenceError, match="CAPTURE_ACTION_POPULATION_INVALID"):
        bind(read(engine))


@pytest.mark.parametrize("model", [EmailCallback, CapturedDelivery, db.DeliveryRow])
def test_evidence_delivery_statuses_match_runtime(model):
    assert set(get_args(model.model_fields["status"].annotation)) == {
        v.value for v in DeliveryStatus
    }


def test_pending_is_approval_status_not_delivery_status():
    with pytest.raises(ValidationError):
        CapturedDelivery(channel="EMAIL", status="PENDING", request_hash="a" * 64)


def setup_bridge(engine):
    snapshot = read(engine)
    targets = bind(snapshot)
    server = Server()
    for raw, target in zip(server.values.values(), targets, strict=True):
        payload = run_data(raw)["Validate Email Payload"][0]["data"]["main"][0][0][
            "json"
        ]["payload"]
        payload.update(target.model_dump())
        payload["recipients"] = ["Team@example.invalid"]
        result = send_result(raw)
        result["messageId"] = next(
            d.provider_message_id
            for d in snapshot.deliveries
            if d.action_id == target.action_id and d.channel == "EMAIL"
        )
        result["envelope"]["to"] = result["accepted"] = payload["recipients"]
    approval_rows = [
        dict(
            workflow="WF2",
            action_id=t.action_id,
            status="SUCCESS",
            execution_id=raw["id"],
        )
        for raw, t in zip(server.values.values(), targets, strict=True)
        if t.email_kind == "APPROVAL_REQUEST"
    ]
    reference = write_private(
        engine.evidence_root,
        APPROVAL_EXECUTIONS,
        dict(format_version=1, executions=approval_rows),
    )
    write_private_bytes(engine.evidence_root, APPROVAL_READY, b"")
    config = dict(
        n8n_workflow_versions={WF: VERSION},
        smtp_host="smtp.example.invalid",
        smtp_port=587,
        smtp_from="from@example.invalid",
        recipient_allowlist=["Team@example.invalid"],
        wf2_callback_endpoint="https://backend.example.invalid/internal/actions",
    )
    payload = prepared_payload()
    payload["approved_config_digest_allowlist"] = [config_digest(config)]
    args = dict(
        prepared=PreparedAttempt.model_validate(payload),
        expected_run_actions=pins(),
        resume_at=AT,
        workflow_id=WF,
        approval_evidence_root=engine.evidence_root,
        approval_evidence=reference,
        read_smtp_config=lambda: deepcopy(config),
    )
    engine.events.clear()
    return server, args


def test_bridge_real_selects_http_and_offline_chain(engine, tmp_path):
    server, args = setup_bridge(engine)

    def no_open_db_tx(request, payload):
        assert engine.events[-1] == "ROLLBACK_CLOSE"
        return payload

    server.hook = no_open_db_tx
    with server.client() as api:
        snapshot, receipts = db.collect_delivery_receipts(engine, api, **args)
    assert engine.events.count("CONNECT") == 2
    assert len(receipts.callbacks) == len(receipts.executions) == 7
    assert all(c.completed_at == "2026-09-05T01:00:02Z" for c in receipts.callbacks)
    assert SECRET not in receipts.model_dump_json()
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    pre = args["prepared"]
    pre_ref = write_private(root, "prepared-attempt.json", pre)
    grant_ref = write_private(
        root,
        "smtp-approval-grant.json",
        dict(
            schema_version="smtp-send-grant-v1",
            grant_type="SMTP_SEND_GRANT",
            attempt_id=pre.attempt_id,
            prepared_attempt=pre_ref.model_dump(),
            approval_reference="SYNTHETIC-ONLY",
            approver="방대혁",
            recipient_canonical_addresses=pre.recipient.canonical_addresses,
            recipient_canonical_hash=pre.recipient.canonical_hash,
            recipient_hash_version=2,
            max_external_emails=7,
            approved_at=AT,
        ),
    )
    receipt_ref = write_private(root, "delivery-receipts.round1.json", receipts)
    assert (
        verify_delivery(
            root=root,
            delivery_receipts=receipt_ref,
            prepared_attempt=pre_ref,
            smtp_approval=grant_ref,
            targets=bind(snapshot),
            evaluated_revision=REV,
            expected_attempt_id=pre.attempt_id,
            resume_at=AT,
            captured_at=receipts.executions[0].observed_at,
        ).delivery_integrity
        == "PASS"
    )


@pytest.mark.parametrize("kind", ["callback", "approval", "new_run", "subsecond"])
def test_fresh_db_snapshot_detects_change_during_http(engine, kind):
    server, args = setup_bridge(engine)
    changes = dict(
        callback=(
            "UPDATE public.action_delivery SET provider_message_id='changed' "
            "WHERE channel='EMAIL'"
        ),
        approval="UPDATE public.approval_request SET status='APPROVED'",
        new_run="INSERT INTO public.agent_run SELECT * FROM public.agent_run LIMIT 1",
        subsecond=(
            "UPDATE public.action_delivery "
            "SET completed_at='2026-09-05T01:00:02.123457+00:00' "
            "WHERE channel='EMAIL'"
        ),
    )

    def hook(request, payload):
        if len(server.requests) == 1:
            engine.sql.execute(changes[kind])
        return payload

    server.hook = hook
    with (
        server.client() as api,
        pytest.raises(EvidenceError, match="DB_DRIFT|DB_READ_FAILED"),
    ):
        db.collect_delivery_receipts(engine, api, **args)


@pytest.mark.parametrize(
    "kind",
    [
        "config_pin",
        "config_drift",
        "config_secret",
        "missing_workflow",
        "provider_failure",
    ],
)
def test_config_and_network_fail_closed(engine, kind):
    server, args = setup_bridge(engine)
    config = args["read_smtp_config"]()
    calls = []

    def config_reader():
        calls.append(1)
        value = deepcopy(config)
        if kind == "config_pin" or (kind == "config_drift" and len(calls) == 2):
            value["smtp_port"] = 2525
        if kind == "config_secret":
            value["password"] = SECRET
        if kind == "provider_failure":
            raise RuntimeError(SECRET)
        return value

    args["read_smtp_config"] = config_reader
    if kind == "missing_workflow":
        args["workflow_id"] = "different"
    with server.client() as api, pytest.raises(EvidenceError) as caught:
        db.collect_delivery_receipts(engine, api, **args)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    if kind != "config_drift":
        assert server.requests == []


@pytest.mark.parametrize(
    "kind",
    [
        "short_pins",
        "duplicate_pins",
        "wrong_time",
        "expired",
        "bad_workflow",
        "bad_approval_ids",
    ],
)
def test_bridge_invalid_inputs_before_io(engine, kind):
    server, args = setup_bridge(engine)
    if kind == "short_pins":
        args["expected_run_actions"].pop("run-0")
    elif kind == "duplicate_pins":
        args["expected_run_actions"]["run-0"] = "action-1"
    elif kind == "wrong_time":
        args["resume_at"] = SECRET
    elif kind == "expired":
        args["resume_at"] = "2026-09-05T01:30:00Z"
    elif kind == "bad_workflow":
        args["workflow_id"] = "../credentials"
    elif kind == "bad_approval_ids":
        path = engine.evidence_root / APPROVAL_EXECUTIONS
        payload = json.loads(path.read_bytes())
        payload["executions"][0]["execution_id"] = "has space"
        path.write_text(json.dumps(payload))
        args["approval_evidence"] = component_ref(
            engine.evidence_root, APPROVAL_EXECUTIONS
        )
    with server.client() as api, pytest.raises(EvidenceError) as caught:
        db.collect_delivery_receipts(engine, api, **args)
    assert engine.events == [] and server.requests == []
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_driver_error_sanitized_and_connection_closed(engine, monkeypatch):
    def broken(self, statement):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(Connection, "execute", broken)
    with pytest.raises(EvidenceError) as caught:
        read(engine)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert engine.events[-1] == "ROLLBACK_CLOSE"


def test_import_has_no_engine_or_runtime_side_effects():
    code = """
import sys
def guard(event, args):
    if event in {'socket.__new__','socket.connect','subprocess.Popen'}:
        raise AssertionError('IO forbidden')
sys.addaudithook(guard)
import app.agent.release_database
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
