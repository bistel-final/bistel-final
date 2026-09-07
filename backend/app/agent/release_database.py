"""V5-C-7.1 bounded pre-HITL DB capture and WF2 receipt assembly.

No runtime config/engine is constructed here. The Stage2 caller supplies its
pinned engine and live SMTP-config reader. No write, grant, retry, workload,
publication or deployment operation exists. Returned data is private evidence,
not a verdict; the existing offline validators own all qualification decisions.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from sqlalchemy import text

from app.agent.release_approval_evidence import (
    bind_approval_acceptances,
    bind_approval_actions,
    read_approval_evidence,
)
from app.agent.release_artifacts import Component, EvidenceError, EvidenceModel, Sha256
from app.agent.release_delivery import (
    DeliveryReceipts,
    DeliveryReceiptsV2,
    EmailCallback,
    EmailTarget,
    EmailTargetV2,
    Identifier,
)
from app.agent.release_n8n import N8nEvidenceApi, collect_acceptances
from app.agent.release_prepared import (
    DbIdentity,
    PreparedAttempt,
    config_digest,
    parse_prepared,
    utc,
)


class RunRow(EvidenceModel):
    run_id: Identifier
    lot_id: Identifier
    chamber_id: Identifier
    status: Literal["RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED"]
    autonomy_level: int
    action_code: Literal["MONITORING", "WARNING", "EQP_HOLD"] | None
    retry_of_run_id: Identifier | None
    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("CAPTURE_DB_TIME_INVALID")
        return value.astimezone(UTC)


class ActionRow(EvidenceModel):
    action_id: Identifier
    lot_id: Identifier
    chamber_id: Identifier
    action_code: Literal["MONITORING", "WARNING", "EQP_HOLD"]


class LinkRow(EvidenceModel):
    run_id: Identifier
    action_id: Identifier
    lot_id: Identifier
    chamber_id: Identifier
    link_role: Literal["CREATED", "REUSED"]


class ApprovalRow(EvidenceModel):
    approval_id: Identifier
    action_id: Identifier
    run_id: Identifier
    status: Literal["AUTO", "PENDING", "APPROVED", "REJECTED", "EXPIRED"]
    decided_at: datetime | None


class DeliveryRow(EvidenceModel):
    action_id: Identifier
    channel: Literal["EMAIL", "MES_MOCK"]
    status: Literal[
        "WAITING", "SENDING", "SENT", "FAILED", "UNKNOWN", "BLOCKED", "CANCELED"
    ]
    request_hash: Sha256
    attempt_count: int = Field(ge=0)
    provider_message_id: Identifier | None
    started_at: datetime | None
    completed_at: datetime | None

    @field_validator("started_at", "completed_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            return RunRow.aware(value)
        return None


class DeliveryDatabaseSnapshot(EvidenceModel):
    identity: DbIdentity
    runs: list[RunRow] = Field(max_length=12)
    actions: list[ActionRow] = Field(max_length=12)
    links: list[LinkRow] = Field(max_length=12)
    approvals: list[ApprovalRow] = Field(max_length=3)
    deliveries: list[DeliveryRow] = Field(max_length=10)


class RunRowV2(RunRow):
    action_policy_version: Literal["MOCK-NOTIFY-V1"]


class DeliveryDatabaseSnapshotV2(DeliveryDatabaseSnapshot):
    runs: list[RunRowV2] = Field(max_length=12)


def parse_mock_database_transport(value):
    """Strict JSON transport: only timestamp fields are decoded, no coercion."""
    from copy import deepcopy

    from app.agent.release_mock import instant

    try:
        value = deepcopy(value)
        for kind, fields in (
            ("runs", ("started_at",)),
            ("deliveries", ("started_at", "completed_at")),
            ("approvals", ("decided_at",)),
        ):
            for row in value[kind]:
                for field in fields:
                    if row.get(field) is not None:
                        if type(row[field]) is not str:
                            raise ValueError
                        row[field] = instant(row[field])
        return DeliveryDatabaseSnapshotV2.model_validate(value)
    except Exception:
        raise EvidenceError("CAPTURE_DB_SNAPSHOT_INVALID") from None


IDENTITY_SQL = text("""
    SELECT current_database() AS database_name, current_user AS role_name,
           current_setting('transaction_read_only') AS read_only,
           current_setting('transaction_isolation') AS isolation,
           system_identifier::text AS system_identifier
    FROM pg_catalog.pg_control_system()
""")

# Query each table independently: inner joins/WHERE run_id IN could hide orphan
# or extra rows after a wrong reset. Read at most cap+1, then reject overflow.
QUERIES = {
    "runs": """SELECT agent_run_id AS run_id, lot_id, chamber_id, status,
        autonomy_level, action AS action_code, retry_of_run_id, started_at
        FROM public.agent_run ORDER BY agent_run_id LIMIT 13""",
    "actions": """SELECT action_id, lot_id, chamber_id, action_code
        FROM public.action_history ORDER BY action_id LIMIT 13""",
    "links": """SELECT agent_run_id AS run_id, action_id, lot_id, chamber_id, link_role
        FROM public.agent_run_action ORDER BY agent_run_id, action_id LIMIT 13""",
    "approvals": """SELECT approval_id, action_id, agent_run_id AS run_id,
        status, decided_at
        FROM public.approval_request ORDER BY approval_id LIMIT 4""",
    "deliveries": """SELECT action_id, channel, status, request_hash, attempt_count,
        provider_message_id, started_at, completed_at FROM public.action_delivery
        ORDER BY action_id, channel LIMIT 11""",
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceError(code)


def _resume_time(value: str) -> datetime:
    try:
        return utc(value)
    except (ValueError, TypeError):
        raise EvidenceError("CAPTURE_RESUME_TIME_INVALID") from None


def read_delivery_database(
    engine: Any, *, expected_identity: DbIdentity, action_policy="ACTION-POLICY-V1"
) -> DeliveryDatabaseSnapshot:
    """Fresh read-only transaction → observed identity → bounded projections.

    Match configured host alias before connect, then actual database/role/cluster
    identity before business data. Missing pg_control_system access is an error,
    never a reason to use a privileged connection or silently omit the pin.
    Connection exit rolls back: this reader never commits even an empty txn.
    """
    try:
        _require(
            action_policy in {"ACTION-POLICY-V1", "MOCK-NOTIFY-V1"},
            "CAPTURE_POLICY_INVALID",
        )
        identity = DbIdentity.model_validate(expected_identity.model_dump())
        _require(
            engine.url.database == identity.database
            and engine.url.username == "kosa_app"
            and engine.url.host == identity.host_alias,
            "CAPTURE_DB_TARGET_MISMATCH",
        )
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            observed = dict(connection.execute(IDENTITY_SQL).mappings().one())
            _require(
                observed
                == {
                    "database_name": identity.current_database,
                    "role_name": "kosa_app",
                    "read_only": "on",
                    "isolation": "repeatable read",
                    "system_identifier": identity.system_identifier,
                },
                "CAPTURE_DB_IDENTITY_MISMATCH",
            )
            queries = dict(QUERIES)
            if action_policy == "MOCK-NOTIFY-V1":
                queries["runs"] = queries["runs"].replace(
                    "started_at\n",
                    "started_at, evidence -> 'action_provenance' "
                    "->> 'action_policy_version' AS action_policy_version\n",
                )
            rows = {
                name: [
                    dict(row) for row in connection.execute(text(sql)).mappings().all()
                ]
                for name, sql in queries.items()
            }
            model = (
                DeliveryDatabaseSnapshotV2
                if action_policy == "MOCK-NOTIFY-V1"
                else DeliveryDatabaseSnapshot
            )
            return model(identity=identity, **rows)
    except EvidenceError:
        raise
    except Exception:
        # Driver errors can contain DSN, credentials, SQL and bound values.
        raise EvidenceError("CAPTURE_DB_READ_FAILED") from None


def _pins(value: dict[str, str]) -> dict[str, str]:
    _require(
        type(value) is dict
        and len(value) == 12
        and all(
            type(v) is str and 1 <= len(v) <= 256 and not any(c.isspace() for c in v)
            for v in (*value.keys(), *value.values())
        )
        and len(set(value.values())) == 12,
        "CAPTURE_RUN_PINS_INVALID",
    )
    return dict(value)


def bind_email_targets(
    snapshot: DeliveryDatabaseSnapshot,
    *,
    expected_run_actions: dict[str, str],
    resume_at: str,
) -> list[EmailTarget]:
    """Bind the batch's independent run/action IDs, not just a 5/4/3 count.

    Preserve failed/pending delivery statuses for negative reports. Post-HITL
    approvals, reused actions and another reset's incident set are not baseline
    evidence. Action rules/investigation quality remain round validator concerns.
    """
    from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS

    try:
        pins = _pins(expected_run_actions)
        start = _resume_time(resume_at).replace(tzinfo=UTC)
        is_mock = isinstance(snapshot, DeliveryDatabaseSnapshotV2)
        model = DeliveryDatabaseSnapshotV2 if is_mock else DeliveryDatabaseSnapshot
        snapshot = model.model_validate(snapshot.model_dump())
        runs = {r.run_id: r for r in snapshot.runs}
        actions = {r.action_id: r for r in snapshot.actions}
        links = {r.run_id: r for r in snapshot.links}
        _require(
            len(snapshot.runs) == len(snapshot.actions) == len(snapshot.links) == 12
            and set(runs) == set(links) == set(pins)
            and set(actions) == set(pins.values())
            and {r.run_id: r.action_id for r in snapshot.links} == pins,
            "CAPTURE_RUN_BINDING_MISMATCH",
        )
        _require(
            {(r.lot_id, r.chamber_id) for r in runs.values()}
            == CANONICAL_INCIDENT_KEYS,
            "CAPTURE_INCIDENT_POPULATION_INVALID",
        )
        for run_id, action_id in pins.items():
            run, action, link = runs[run_id], actions[action_id], links[run_id]
            _require(
                run.autonomy_level == 3
                and run.retry_of_run_id is None
                and link.link_role == "CREATED"
                and run.action_code == action.action_code
                and (run.lot_id, run.chamber_id)
                == (action.lot_id, action.chamber_id)
                == (link.lot_id, link.chamber_id)
                and run.started_at >= start,
                "CAPTURE_RUN_BINDING_MISMATCH",
            )
        _require(
            Counter(r.action_code for r in actions.values())
            == {
                "MONITORING": 5,
                "WARNING": 4,
                "EQP_HOLD": 3,
            },
            "CAPTURE_ACTION_POPULATION_INVALID",
        )
        holds = {
            run_id: action_id
            for run_id, action_id in pins.items()
            if actions[action_id].action_code == "EQP_HOLD"
        }
        _require(
            (
                not snapshot.approvals
                and all(r.status == "COMPLETED" for r in runs.values())
            )
            if is_mock
            else (
                len(snapshot.approvals) == 3
                and len({a.approval_id for a in snapshot.approvals}) == 3
                and {a.run_id: a.action_id for a in snapshot.approvals} == holds
                and all(
                    a.status == "PENDING" and a.decided_at is None
                    for a in snapshot.approvals
                )
            ),
            "CAPTURE_PRE_HITL_REQUIRED",
        )
        expected = {
            (a.action_id, channel)
            for a in actions.values()
            for channel in (
                ()
                if a.action_code == "MONITORING"
                else ("EMAIL", "MES_MOCK")
                if a.action_code == "EQP_HOLD"
                else ("EMAIL",)
            )
        }
        _require(
            len(snapshot.deliveries) == 10
            and {(d.action_id, d.channel) for d in snapshot.deliveries} == expected
            and len({d.request_hash for d in snapshot.deliveries}) == 10,
            "CAPTURE_DELIVERY_POPULATION_INVALID",
        )
        for row in snapshot.deliveries:
            _require(
                (row.started_at is None or row.started_at >= start)
                and (row.completed_at is None or row.completed_at >= start)
                and (
                    row.started_at is None
                    or row.completed_at is None
                    or row.started_at <= row.completed_at
                ),
                "CAPTURE_DB_TIME_INVALID",
            )
        return [
            (EmailTargetV2 if is_mock else EmailTarget)(
                action_id=d.action_id,
                action_code=actions[d.action_id].action_code,
                email_kind="ACTION_NOTIFY"
                if is_mock
                else "WARNING_NOTIFY"
                if actions[d.action_id].action_code == "WARNING"
                else "APPROVAL_REQUEST",
                request_hash=d.request_hash,
            )
            for d in snapshot.deliveries
            if d.channel == "EMAIL"
        ]
    except EvidenceError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError):
        raise EvidenceError("CAPTURE_DB_SNAPSHOT_INVALID") from None


def collect_delivery_receipts(
    engine: Any,
    api: N8nEvidenceApi,
    *,
    prepared: PreparedAttempt,
    expected_run_actions: dict[str, str],
    resume_at: str,
    workflow_id: str,
    approval_evidence_root: Path,
    approval_evidence: Component,
    read_smtp_config: Callable[[], dict],
) -> tuple[DeliveryDatabaseSnapshot, DeliveryReceipts]:
    """DB → live config → WF2 → fresh DB/config, outside any long-held DB txn.

    The explicit config port must observe live non-secret SMTP/workflow settings;
    it must not reconstruct them from the prepared allowlist. Approval execution
    IDs come from the separately observed, SHA-pinned PRE_APPROVAL/n8n-wf2.json,
    not from SMTP receipt_id or the acceptance inventory being checked here.
    This does not validate consent nor issue an artifact; Stage2 owns the lock,
    grant, runtime pin checks, PRE_HITL boundary and eventual no-clobber writes.
    """
    try:
        prepared = parse_prepared(prepared.model_dump())
        pins = _pins(expected_run_actions)
        start = _resume_time(resume_at)
        _require(
            utc(prepared.prepared_at) <= start < utc(prepared.expires_at),
            "CAPTURE_RESUME_TIME_INVALID",
        )
        _require(
            type(workflow_id) is str
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workflow_id) is not None,
            "CAPTURE_APPROVAL_RECEIPTS_INVALID",
        )
        _require(
            isinstance(approval_evidence_root, Path)
            and approval_evidence_root.is_absolute()
            and approval_evidence_root.name == prepared.attempt_id
            and approval_evidence_root.parent.name == "cm-5.2",
            "APPROVAL_EVIDENCE_ATTEMPT_MISMATCH",
        )
        approvals = read_approval_evidence(approval_evidence_root, approval_evidence)
        before = read_delivery_database(engine, expected_identity=prepared.db_identity)
        targets = bind_email_targets(
            before, expected_run_actions=pins, resume_at=resume_at
        )
        bind_approval_actions(approvals, targets)
        config = read_smtp_config()
        config_sha = config_digest(config)
        _require(
            config_sha in prepared.approved_config_digest_allowlist,
            "CAPTURE_SMTP_CONFIG_MISMATCH",
        )
        version = config["n8n_workflow_versions"].get(workflow_id)
        _require(type(version) is str and bool(version), "CAPTURE_WORKFLOW_PIN_MISSING")
        rows = collect_acceptances(
            api,
            workflow_id=workflow_id,
            workflow_version=version,
            targets=targets,
            resume_at=resume_at,
        )
        approval_ids = bind_approval_acceptances(approvals, rows)
        after = read_delivery_database(engine, expected_identity=prepared.db_identity)
        _require(before == after, "CAPTURE_DB_DRIFT")
        _require(
            config_digest(read_smtp_config()) == config_sha, "CAPTURE_SMTP_CONFIG_DRIFT"
        )
        _require(
            read_approval_evidence(approval_evidence_root, approval_evidence)
            == approvals,
            "APPROVAL_EVIDENCE_DRIFT",
        )
        callbacks = [
            EmailCallback(
                action_id=d.action_id,
                request_hash=d.request_hash,
                channel="EMAIL",
                status=d.status,
                provider_message_id=d.provider_message_id,
                completed_at=d.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if d.completed_at
                else None,
            )
            for d in before.deliveries
            if d.channel == "EMAIL"
        ]
        return before, DeliveryReceipts(
            schema_version="level3-delivery-receipts-v1",
            attempt_id=prepared.attempt_id,
            capture_phase="PRE_HITL",
            recipient_hash_version=2,
            smtp_config_digest=config_sha,
            callbacks=callbacks,
            executions=rows,
            approval_execution_ids=approval_ids,
        )
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("CAPTURE_DELIVERY_READ_FAILED") from None


def collect_mock_delivery_receipts(
    engine,
    api,
    *,
    prepared,
    expected_run_actions,
    resume_at,
    workflow_id,
    read_smtp_config,
):
    """New policy has no approval source. Bind WF2 notifications to CREATED rows.

    Kafka, callback trail and WF3/WF4 remain independent MockSources; this
    collector cannot certify their success using email acknowledgements.
    """
    try:
        prepared = parse_prepared(prepared.model_dump())
        _require(
            prepared.schema_version == "level3-prepared-attempt-v2",
            "CAPTURE_POLICY_INVALID",
        )
        _require(
            utc(prepared.prepared_at)
            <= _resume_time(resume_at)
            < utc(prepared.expires_at),
            "CAPTURE_RESUME_TIME_INVALID",
        )

        def database():
            return read_delivery_database(
                engine,
                expected_identity=prepared.db_identity,
                action_policy="MOCK-NOTIFY-V1",
            )

        before = database()
        targets = bind_email_targets(
            before, expected_run_actions=expected_run_actions, resume_at=resume_at
        )
        config = read_smtp_config()
        config_sha = config_digest(config)
        _require(
            config_sha in prepared.approved_config_digest_allowlist,
            "CAPTURE_SMTP_CONFIG_MISMATCH",
        )
        version = config["n8n_workflow_versions"][workflow_id]
        rows = collect_acceptances(
            api,
            workflow_id=workflow_id,
            workflow_version=version,
            targets=targets,
            resume_at=resume_at,
        )
        _require(before == database(), "CAPTURE_DB_DRIFT")
        _require(
            config_digest(read_smtp_config()) == config_sha, "CAPTURE_SMTP_CONFIG_DRIFT"
        )
        holds = {t.action_id for t in targets if t.action_code == "EQP_HOLD"}
        return before, DeliveryReceiptsV2(
            schema_version="level3-delivery-receipts-v2",
            attempt_id=prepared.attempt_id,
            capture_phase="POST_MOCK_CONVERGENCE",
            recipient_hash_version=2,
            smtp_config_digest=config_sha,
            executions=rows,
            hold_notify_execution_ids=sorted(
                r.n8n_execution_id for r in rows if r.action_id in holds
            ),
            callbacks=[
                EmailCallback(
                    action_id=d.action_id,
                    request_hash=d.request_hash,
                    channel="EMAIL",
                    status=d.status,
                    provider_message_id=d.provider_message_id,
                    completed_at=d.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if d.completed_at
                    else None,
                )
                for d in before.deliveries
                if d.channel == "EMAIL"
            ],
        )
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("CAPTURE_DELIVERY_READ_FAILED") from None
