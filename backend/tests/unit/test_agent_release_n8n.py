"""Synthetic n8n public API responses; no live server, email or saved secrets."""

import json
import subprocess
import sys
import traceback
from collections import Counter
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from app.agent import release_n8n as n8n
from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.release_delivery import EmailTarget
from tests.unit import test_agent_release_evidence as evidence_tests
from tests.unit.test_agent_release_evidence import replace

delivery = evidence_tests.delivery

WF = "wf2-test"
VERSION = "version-test"
RESUME = "2026-09-05T01:00:00Z"
NOW = "2026-09-05T01:02:00.123Z"
SECRET = "synthetic-secret-must-not-escape"
ADDRESS = "Operator@example.invalid"


def target(index):
    return EmailTarget(
        action_id=f"action-{index}",
        request_hash=f"{index+1:064x}",
        action_code="WARNING" if index < 4 else "EQP_HOLD",
        email_kind="WARNING_NOTIFY" if index < 4 else "APPROVAL_REQUEST",
    )


def node(payload):
    return [{"data": {"main": [[{"json": payload}]]}}]


def workflow():
    return dict(
        id=WF,
        versionId=VERSION,
        active=True,
        settings=dict(saveDataSuccessExecution="all", saveDataErrorExecution="all"),
        nodes=[dict(name=name, type=kind) for name, kind in n8n._NODE_TYPES.items()],
        credentials={"token": SECRET},
    )


def execution(index=0):
    payload = {
        **target(index).model_dump(),
        "schema": "email-request-v1",
        "channel": "EMAIL",
        "recipients": [ADDRESS],
        "summary": SECRET,
    }
    return dict(
        id=str(index + 1),
        workflowId=WF,
        workflowData=dict(id=WF, versionId=VERSION),
        mode="webhook",
        status="success",
        startedAt="2026-09-05T01:00:01.001Z",
        stoppedAt="2026-09-05T01:01:00.123Z",
        retryOf=None,
        retrySuccessId=None,
        data={
            "resultData": {
                "runData": {
                    "Validate Email Payload": node(
                        {"schema_ok": True, "payload": payload}
                    ),
                    "Send Email": node(
                        {
                            "messageId": f"<smtp-{index}@provider.invalid>",
                            "envelope": {"to": [ADDRESS]},
                            "accepted": [ADDRESS],
                            "rejected": [],
                        }
                    ),
                    "Email Webhook": node({"headers": {"signature": SECRET}}),
                }
            }
        },
    )


def run_data(value):
    return value["data"]["resultData"]["runData"]


def send_result(value):
    return run_data(value)["Send Email"][0]["data"]["main"][0][0]["json"]


def project(value):
    return n8n.project_execution(
        value,
        workflow_id=WF,
        workflow_version=VERSION,
        execution_id="1",
        observed_at=NOW,
    )


class Server:
    def __init__(self):
        self.workflow = workflow()
        self.values = {str(i + 1): execution(i) for i in range(7)}
        self.requests = []
        self.hook = lambda request, payload: payload
        self.page_size = 4

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        assert request.headers["X-N8N-API-KEY"] == SECRET
        assert request.url.host == "n8n.test"
        assert SECRET not in str(request.url)
        assert "status" not in request.url.params
        path = request.url.path
        if path.endswith("/workflows/" + WF):
            payload = self.workflow
        elif path.endswith("/executions"):
            assert request.url.params["workflowId"] == WF
            assert request.url.params["includeData"] == "false"
            offset = int(request.url.params.get("cursor", "0"))
            rows = list(self.values.values())[offset : offset + self.page_size]
            payload = dict(
                data=[
                    {
                        k: v[k]
                        for k in (
                            "id",
                            "workflowId",
                            "startedAt",
                            "stoppedAt",
                            "status",
                        )
                    }
                    for v in rows
                ],
                nextCursor=str(offset + self.page_size)
                if offset + self.page_size < len(self.values)
                else None,
            )
        else:
            assert request.url.params["includeData"] == "true"
            payload = self.values[path.rsplit("/", 1)[-1]]
        return httpx.Response(200, json=self.hook(request, deepcopy(payload)))

    def client(self):
        return n8n.N8nEvidenceApi(
            "https://n8n.test/prefix",
            SECRET,
            transport=httpx.MockTransport(self.handle),
        )


def collect(server, **overrides):
    args = dict(
        workflow_id=WF,
        workflow_version=VERSION,
        targets=[target(i) for i in range(7)],
        resume_at=RESUME,
        clock=lambda: NOW,
    )
    args.update(overrides)
    with server.client() as api:
        return n8n.collect_acceptances(api, **args)


def probe(server):
    with server.client() as api:
        return n8n.probe_evidence(
            api,
            workflow_id=WF,
            workflow_version=VERSION,
            sample_execution_id="1",
            clock=lambda: NOW,
        )


def test_probe_reads_existing_execution_without_sending_or_listing():
    server = Server()
    assert probe(server).verdict == "PASS"
    assert [r.url.path for r in server.requests] == [
        "/prefix/api/v1/workflows/wf2-test",
        "/prefix/api/v1/executions/1",
        "/prefix/api/v1/workflows/wf2-test",
    ]


def test_complete_paginated_collection_and_private_projection():
    server = Server()
    rows = collect(server)
    assert len(rows) == 7
    assert [r.email_kind for r in rows].count("APPROVAL_REQUEST") == 3
    assert rows[0].observed_at == "2026-09-05T01:02:00Z"
    assert rows[0].provider_message_id == "<smtp-0@provider.invalid>"
    assert SECRET not in canonical_json([r.model_dump() for r in rows]).decode()
    assert len(server.requests) == 20  # workflow2 + inventory4 + detail14


def test_current_clock_projects_canonical_seconds():
    value = execution()
    row = n8n.project_execution(
        value,
        workflow_id=WF,
        workflow_version=VERSION,
        execution_id="1",
        observed_at=n8n._now(),
    )
    assert row.observed_at.endswith("Z") and "." not in row.observed_at


@pytest.mark.parametrize(
    "field,value",
    [
        ("saveDataSuccessExecution", "none"),
        ("saveDataErrorExecution", "none"),
        ("saveDataSuccessExecution", None),
        ("saveDataErrorExecution", True),
    ],
)
def test_retention_not_assumed_or_mutated(field, value):
    server = Server()
    server.workflow["settings"][field] = value
    with pytest.raises(EvidenceError, match="RETENTION_REQUIRED"):
        probe(server)
    assert len(server.requests) == 1


def test_repository_default_retention_blocks_probe():
    server = Server()
    repo = Path(__file__).resolve().parents[3]
    local = json.loads((repo / "deploy/n8n/WF2-notify-email.json").read_text())
    server.workflow["settings"] = local["settings"]
    with pytest.raises(EvidenceError, match="RETENTION_REQUIRED"):
        probe(server)


@pytest.mark.parametrize(
    "field,value",
    [("active", False), ("active", 1), ("versionId", "old"), ("id", "other")],
)
def test_workflow_identity_before_execution(field, value):
    server = Server()
    server.workflow[field] = value
    with pytest.raises(EvidenceError, match="WORKFLOW_MISMATCH"):
        collect(server)
    assert len(server.requests) == 1


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong_type"])
def test_workflow_named_nodes(kind):
    server = Server()
    if kind == "missing":
        server.workflow["nodes"].pop()
    elif kind == "duplicate":
        server.workflow["nodes"].append(server.workflow["nodes"][0])
    else:
        server.workflow["nodes"][0]["type"] = "n8n-nodes-base.noOp"
    with pytest.raises(EvidenceError, match="WORKFLOW_INVALID"):
        probe(server)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "wrong"),
        ("workflowId", "wrong"),
        ("mode", "manual"),
        ("retryOf", "old"),
        ("retrySuccessId", "new"),
    ],
)
def test_execution_identity_and_retries(field, value):
    value_dict = execution()
    value_dict[field] = value
    with pytest.raises(EvidenceError, match="EXECUTION_MISMATCH"):
        project(value_dict)


@pytest.mark.parametrize(
    "change",
    [
        "workflow_version",
        "missing_data",
        "missing_node",
        "multiple_runs",
        "multiple_items",
        "multiple_branches",
        "node_error",
        "send_error",
        "schema_false",
        "wrong_kind",
        "missing_message",
        "blank_message",
        "bad_status",
        "bad_time",
        "future",
        "stop_before_start",
        "envelope_extra",
        "accepted_partial",
        "rejected",
        "local_case",
        "duplicate_address",
    ],
)
def test_projection_rejects_missing_ambiguous_or_false_evidence(change):
    value = execution()
    data = run_data(value)
    result = send_result(value)
    if change == "workflow_version":
        value["workflowData"]["versionId"] = "wrong"
    elif change == "missing_data":
        value["data"] = None
    elif change == "missing_node":
        data.pop("Send Email")
    elif change == "multiple_runs":
        data["Send Email"] *= 2
    elif change == "multiple_items":
        data["Send Email"][0]["data"]["main"][0] *= 2
    elif change == "multiple_branches":
        data["Send Email"][0]["data"]["main"] *= 2
    elif change == "node_error":
        data["Send Email"][0]["error"] = SECRET
    elif change == "send_error":
        result["error"] = SECRET
    elif change == "schema_false":
        data["Validate Email Payload"][0]["data"]["main"][0][0]["json"]["schema_ok"] = 1
    elif change == "wrong_kind":
        data["Validate Email Payload"][0]["data"]["main"][0][0]["json"]["payload"][
            "email_kind"
        ] = "APPROVAL_REQUEST"
    elif change == "missing_message":
        result.pop("messageId")
    elif change == "blank_message":
        result["messageId"] = " "
    elif change == "bad_status":
        value["status"] = SECRET
    elif change == "bad_time":
        value["stoppedAt"] = SECRET
    elif change == "future":
        value["stoppedAt"] = "2026-09-05T02:00:00Z"
    elif change == "stop_before_start":
        value["stoppedAt"] = RESUME
    elif change == "envelope_extra":
        result["envelope"]["to"].append("bcc@example.invalid")
    elif change == "accepted_partial":
        result["accepted"] = []
    elif change == "rejected":
        result["rejected"] = [ADDRESS]
    elif change == "local_case":
        result["accepted"] = [ADDRESS.lower()]
    elif change == "duplicate_address":
        result["envelope"]["to"] *= 2
    with pytest.raises(EvidenceError) as caught:
        project(value)
    assert SECRET not in str(caught.value)
    assert ADDRESS not in str(caught.value)


def test_domain_normalization_preserves_real_acceptance():
    value = execution()
    send_result(value)["envelope"]["to"] = [" Operator@EXAMPLE.INVALID "]
    assert project(value).recipients == [ADDRESS]


def test_non_success_status_preserved_not_upgraded_to_pass():
    server = Server()
    server.values["1"]["status"] = "error"
    assert collect(server)[0].n8n_status == "error"
    with pytest.raises(EvidenceError, match="PROBE_FAILED"):
        probe(server)


@pytest.mark.parametrize(
    "kind",
    [
        "eighth",
        "missing",
        "old",
        "wrong_target",
        "duplicate_key",
        "duplicate_message",
        "running",
    ],
)
def test_closed_population(kind):
    server = Server()
    if kind == "eighth":
        server.values["8"] = execution(7)
    elif kind == "missing":
        server.values.pop("1")
    elif kind == "old":
        server.values["1"]["startedAt"] = "2026-09-04T00:00:00Z"
    elif kind == "wrong_target":
        run_data(server.values["1"])["Validate Email Payload"][0]["data"]["main"][0][0][
            "json"
        ]["payload"]["action_id"] = "wrong"
    elif kind == "duplicate_key":
        run_data(server.values["2"])["Validate Email Payload"] = deepcopy(
            run_data(server.values["1"])["Validate Email Payload"]
        )
    elif kind == "duplicate_message":
        send_result(server.values["2"])["messageId"] = send_result(server.values["1"])[
            "messageId"
        ]
    elif kind == "running":
        server.values["1"].update(status="running", stoppedAt=None)
    with pytest.raises(EvidenceError):
        collect(server)


def test_old_execution_metadata_does_not_export_old_mail_body():
    server = Server()
    server.values["8"] = execution(7)
    server.values["8"]["startedAt"] = "2026-09-04T00:00:00Z"
    assert len(collect(server)) == 7
    assert not any(r.url.path.endswith("/executions/8") for r in server.requests)


@pytest.mark.parametrize(
    "kind",
    [
        "workflow",
        "detail",
        "metadata",
        "new_execution",
        "pruned",
        "missing_start",
        "duplicate_page",
        "cursor_loop",
        "page_limit",
    ],
)
def test_drift_and_incomplete_inventory(kind, monkeypatch):
    server = Server()
    counts = Counter()

    def hook(request, value):
        path = request.url.path
        counts[path] += 1
        if kind == "workflow" and "/workflows/" in path and counts[path] == 2:
            value["name"] = "changed"
        elif kind == "detail" and path.endswith("/executions/1") and counts[path] == 2:
            send_result(value)["messageId"] = "different"
        elif kind == "metadata" and path.endswith("/executions/1"):
            value["startedAt"] = "2026-09-05T01:00:02Z"
        elif path.endswith("/executions"):
            if kind == "new_execution" and counts[path] == 3:
                value["data"].append({k: execution(8)[k] for k in value["data"][0]})
            elif kind == "pruned" and counts[path] == 3:
                value["data"].pop()
            elif kind == "missing_start":
                value["data"][0]["startedAt"] = None
            elif kind == "duplicate_page":
                value["data"].append(value["data"][0])
            elif kind == "cursor_loop":
                value["data"] = []
                value["nextCursor"] = "0"
        return value

    if kind == "page_limit":
        monkeypatch.setattr(n8n, "MAX_PAGES", 1)
    server.hook = hook
    with pytest.raises(EvidenceError):
        collect(server)


@pytest.mark.parametrize(
    "kind", ["short", "duplicate", "bad_id", "bad_revision", "bad_time"]
)
def test_bad_caller_inputs_before_network(kind):
    server = Server()
    args = {}
    if kind == "short":
        args["targets"] = [target(0)]
    elif kind == "duplicate":
        args["targets"] = [target(0)] * 7
    elif kind == "bad_id":
        args["workflow_id"] = "../credentials"
    elif kind == "bad_revision":
        args["workflow_version"] = "../credentials"
    elif kind == "bad_time":
        args["resume_at"] = SECRET
    with pytest.raises(EvidenceError):
        collect(server, **args)
    assert not server.requests


@pytest.mark.parametrize(
    "url,key",
    [
        ("https://user:pass@n8n.test", SECRET),
        ("https://n8n.test?token=secret", SECRET),
        ("https://n8n.test#secret", SECRET),
        ("file:///tmp/a", SECRET),
        ("https://n8n.test/../other", SECRET),
        ("https://n8n.test/%2e", SECRET),
        ("https://n8n.test", "key\nsecret"),
        ("https://n8n.test", ""),
    ],
)
def test_client_config_errors_do_not_echo_secrets(url, key):
    with pytest.raises(EvidenceError, match="^N8N_EVIDENCE_CONFIG_INVALID$"):
        n8n.N8nEvidenceApi(url, key)


@pytest.mark.parametrize(
    "kind",
    [
        "redirect",
        "auth",
        "timeout",
        "invalid_json",
        "duplicate_json",
        "too_large",
        "array",
    ],
)
def test_client_limits_and_error_redaction(kind, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if kind == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://other.test/" + SECRET}
            )
        if kind == "auth":
            return httpx.Response(401, text=SECRET)
        if kind == "invalid_json":
            return httpx.Response(200, text=SECRET)
        if kind == "duplicate_json":
            return httpx.Response(200, content=b'{"x":1,"x":2}')
        if kind == "too_large":
            return httpx.Response(200, content=b"x" * 33)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(n8n, "MAX_RESPONSE_BYTES", 32)
    with n8n.N8nEvidenceApi(
        "https://n8n.test", SECRET, transport=httpx.MockTransport(handler)
    ) as api:
        with pytest.raises(EvidenceError) as caught:
            api.execution("1")
    assert SECRET not in str(caught.value)
    assert len(requests) == 1


def test_import_does_not_initialize_runtime_or_make_io():
    code = """
import sys
def guard(event, args):
    if event in {'socket.__new__', 'socket.connect', 'subprocess.Popen'}:
        raise AssertionError('IO forbidden')
sys.addaudithook(guard)
import app.agent.release_n8n
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_collected_rows_pass_existing_offline_delivery_chain(delivery):
    from app.agent.release_delivery import verify_delivery

    args, receipts, _grant = delivery
    server = Server()
    for i, row in enumerate(server.values.values()):
        payload = run_data(row)["Validate Email Payload"][0]["data"]["main"][0][0][
            "json"
        ]["payload"]
        payload.update(args["targets"][i].model_dump())
        payload["recipients"] = ["Team@example.invalid"]
        result = send_result(row)
        result["messageId"] = receipts["callbacks"][i]["provider_message_id"]
        result["envelope"]["to"] = result["accepted"] = payload["recipients"]
    rows = collect(server, targets=args["targets"])
    receipts["executions"] = [r.model_dump() for r in rows]
    # Fixture's independently captured approval IDs, not inferred from row order.
    receipts["approval_execution_ids"] = ["5", "6", "7"]
    args["delivery_receipts"] = replace(
        args["root"], "delivery-receipts.round1.json", receipts
    )
    args["captured_at"] = rows[0].observed_at
    assert verify_delivery(**args).provider_acceptances == 7
    # A DB callback mismatch must still fail even after successful n8n collection.
    receipts["callbacks"][0]["provider_message_id"] = "other"
    args["delivery_receipts"] = replace(
        args["root"], "delivery-receipts.round1.json", receipts
    )
    with pytest.raises(EvidenceError, match="PROVIDER_ACCEPTANCE_INVALID"):
        verify_delivery(**args)


def test_missing_next_cursor_cannot_claim_complete_population():
    server = Server()
    server.page_size = 7

    def hook(request, value):
        if request.url.path.endswith("/executions"):
            value.pop("nextCursor")
        return value

    server.hook = hook
    with pytest.raises(EvidenceError, match="PAGE_INVALID"):
        collect(server)


def test_invalid_time_traceback_has_no_private_value():
    with pytest.raises(EvidenceError) as caught:
        collect(Server(), resume_at=SECRET)
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_non_read_endpoint_rejected_without_transport_call():
    server = Server()
    with server.client() as api:
        for endpoint in (
            "executions/1/retry",
            "credentials",
            "../workflows/x",
            "https://other.test",
        ):
            with pytest.raises(EvidenceError, match="ENDPOINT_INVALID"):
                api._get(endpoint, {})
    assert server.requests == []


def test_malformed_private_workflow_is_sanitized():
    value = workflow()
    value["invalid"] = "\ud800"
    with pytest.raises(EvidenceError, match="WORKFLOW_INVALID") as caught:
        n8n._workflow(value, WF, VERSION)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
