"""Collector assembly with source IO fakes and real strict schemas/join/recount."""

from copy import deepcopy

import pytest

from app.agent import release_collection as subject
from app.agent.release_artifacts import EvidenceError, parse_json, read_private
from app.agent.release_delivery import parse_delivery_receipts
from app.agent.release_mock import MockSources
from app.agent.release_prepared import parse_prepared
from app.agent.release_round import verify_round
from tests.unit.test_agent_release import AT
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_bundle_v2 import mock_bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery  # noqa: F401
from tests.unit.test_agent_release_mock import evidence as mock_evidence  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401


@pytest.fixture
def capture(mock_bundle, monkeypatch):  # noqa: F811
    root, published = mock_bundle["root"], mock_bundle["published_root"]

    def read(name):
        return parse_json(read_private(root, name))

    prepared = parse_prepared(read("prepared-attempt.json"))
    round1 = read("round1.json")
    sources = MockSources.model_validate(read("mock-sources.round1.json"))
    receipts = parse_delivery_receipts(read("delivery-receipts.round1.json"))
    db = dict(
        identity=prepared.db_identity.model_dump(),
        runs=[],
        actions=[],
        links=[],
        approvals=[],
        deliveries=[],
    )
    callbacks = {r.action_id: r for r in receipts.callbacks}
    mes = {r.action_id: r for r in sources.db_mes_rows}
    for r in round1["runs"]:
        identity = {k: r["route"]["incident"][k] for k in ("lot_id", "chamber_id")}
        db["runs"].append(
            dict(
                run_id=r["run_id"],
                **identity,
                status="COMPLETED",
                autonomy_level=3,
                action_code=r["action_code"],
                retry_of_run_id=None,
                started_at=AT,
                action_policy_version="MOCK-NOTIFY-V1",
            )
        )
        db["actions"].append(
            dict(action_id=r["action_id"], **identity, action_code=r["action_code"])
        )
        db["links"].append(
            dict(
                run_id=r["run_id"],
                action_id=r["action_id"],
                **identity,
                link_role="CREATED",
            )
        )
        for d in r["deliveries"]:
            if d["channel"] == "MES":
                db["deliveries"].append(mes[r["action_id"]].model_dump())
            else:
                callback = callbacks[r["action_id"]]
                db["deliveries"].append(
                    dict(
                        action_id=r["action_id"],
                        channel="EMAIL",
                        status="SENT",
                        request_hash=d["request_hash"],
                        attempt_count=1,
                        provider_message_id=callback.provider_message_id,
                        started_at=AT,
                        completed_at=AT,
                    )
                )
    captured = dict(
        schema_version="level3-captured-runs-v2",
        runs=round1["runs"],
        model=dict(
            schema_version="level3-model-context-v1",
            llm=round1["llm"],
            endpoint_sha256=round1["model_endpoint_sha256"],
            model_config_digest=round1["model_config_digest"],
            published_attempt_id=None,
        ),
    )
    config = dict(
        n8n_workflow_versions={"wf2": "v2", "wf3": "v3", "wf4": "v4"},
        smtp_host="smtp.invalid",
        smtp_port=587,
        smtp_from="a@example.invalid",
        recipient_allowlist=prepared.recipient.canonical_addresses,
        wf2_callback_endpoint="http://backend:8000/internal/actions/{action_id}/delivery",
    )
    from app.agent.release_prepared import config_digest

    # Transport fixture pins are inputs; the grant/round are rebuilt below.
    prepared.approved_config_digest_allowlist[:] = [config_digest(config)]
    from app.agent.release_artifacts import component_ref
    from tests.unit.test_agent_release_evidence import replace

    replace(root, "prepared-attempt.json", prepared)
    grant = read("smtp-approval-grant.json")
    grant["prepared_attempt"] = component_ref(
        root, "prepared-attempt.json"
    ).model_dump()
    replace(root, "smtp-approval-grant.json", grant)
    monkeypatch.setattr(subject, "workflow_pin", lambda *a, **kw: b"retained-workflow")
    monkeypatch.setattr(
        subject, "collect_mes_executions", lambda *a, **kw: sources.n8n_executions
    )
    monkeypatch.setattr(
        subject, "collect_acceptances", lambda *a, **kw: receipts.executions
    )
    for name in (
        "round1.json",
        "mock-sources.round1.json",
        "mock-results.round1.json",
        "delivery-receipts.round1.json",
    ):
        (root / name).unlink()
    calls = []
    args = dict(
        root=root,
        attempt_root=published,
        prepared=prepared,
        resume_at=AT,
        kafka_before=round1["kafka_before"],
        read_database=lambda: deepcopy(db),
        read_runs=lambda: deepcopy(captured),
        read_offsets=lambda: round1["kafka_after"],
        read_records=lambda *_: [r.model_dump() for r in sources.kafka_records],
        read_trail=lambda *_: [r.model_dump() for r in sources.callback_trail],
        api=None,
        workflows={
            w: dict(workflow_id=w.lower(), version="v" + w[-1])
            for w in ("WF2", "WF3", "WF4")
        },
        read_smtp_config=lambda: config,
        capture_mock_snapshot=lambda: calls.append("MOCK_RESULTS"),
        clock=lambda: AT,
        monotonic=lambda: 0,
        sleep=lambda _: pytest.fail("successful fixture must not poll"),
    )
    return args, db, calls


def test_all_sources_produce_verified_round_without_new_action(capture):
    args, _, calls = capture
    ref = subject.collect_round(**args)
    round1, summary, _ = verify_round(args["root"], ref)
    assert summary.status_counts == {"COMPLETED": 12}
    assert summary.robustness_verdict == "PASS"
    assert round1.action_policy_version == "MOCK-NOTIFY-V1"
    assert calls == ["MOCK_RESULTS"]


def test_unknown_never_becomes_wait_or_pass(capture):
    args, db, calls = capture
    db["deliveries"][-1]["status"] = "UNKNOWN"
    with pytest.raises(EvidenceError, match="MOCK_CONVERGENCE_FAILED"):
        subject.collect_round(**args)
    assert not calls and not (args["root"] / "round1.json").exists()


def test_convergence_timeout_preserves_partial_database(capture):
    args, db, calls = capture
    db["runs"][-1]["status"] = "RUNNING"
    times = iter([0, 181])
    args["monotonic"] = lambda: next(times)
    with pytest.raises(EvidenceError, match="MOCK_CONVERGENCE_TIMEOUT"):
        subject.collect_round(**args)
    assert (args["attempt_root"] / "partial-mock-database.json").exists()
    assert not calls and not (args["root"] / "round1.json").exists()
