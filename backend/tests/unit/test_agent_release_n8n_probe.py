"""Mock HTTP only: explicitly authorized temporary metadata, no real send."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.agent import release_n8n_probe as subject
from app.agent.release_artifacts import EvidenceError
from tests.unit.test_agent_release_n8n_session import flatted


@pytest.mark.parametrize("fault", [None, "session", "builder", "workflow", "smtp"])
def test_smtp_snapshot_binds_same_session_and_unchanged_workflows(fault):
    source = json.loads(
        (
            Path(__file__).resolve().parents[3] / "deploy/n8n/WF2-notify-email.json"
        ).read_text()
    )
    flows = {"two": source, "three": {"nodes": []}, "four": {"nodes": []}}
    for flow in flows.values():
        flow["versionId"] = "test-version"
    observed = False
    calls = []

    def workflow(wid):
        value = deepcopy(flows[wid])
        if fault == "builder" and wid == "two":
            value["nodes"] = []
        if fault == "workflow" and observed and wid == "four":
            value["versionId"] = "changed"
        return value

    def smtp(wid):
        assert wid == "two"
        return dict(
            smtp_host="smtp.invalid",
            smtp_port=465,
            smtp_from="Sender@example.invalid",
            smtp_secure=not (fault == "smtp" and observed),
        )

    session = SimpleNamespace(workflow=workflow, smtp_transport=smtp)

    def base():
        nonlocal observed
        calls.append("observe")
        observed = True
        return "https://backend.invalid/api"

    probe = SimpleNamespace(
        session=object() if fault == "session" else session, read_base_url=base
    )
    kwargs = dict(
        session=session,
        observer=probe,
        workflow_ids={"wf2": "two", "wf3": "three", "wf4": "four"},
        recipients=["Team@EXAMPLE.invalid"],
    )
    if fault:
        with pytest.raises(EvidenceError):
            subject.read_smtp_snapshot(**kwargs)
        if fault in {"session", "builder"}:
            assert calls == []
    else:
        value = subject.read_smtp_snapshot(**kwargs)
        assert value.recipient_allowlist == ["Team@example.invalid"]
        assert value.wf2_callback_endpoint == (
            "https://backend.invalid/api/internal/actions/{action_id}/delivery"
        )
        assert value.n8n_workflow_versions == {wid: "test-version" for wid in flows}


@pytest.fixture
def server():
    state = {
        "workflow": None,
        "calls": [],
        "base": "http://backend.invalid:8080/api",
        "status": "success",
        "tamper": False,
        "delete_fail": False,
        "extra_output": False,
    }

    def handler(req):
        path = req.url.path.removeprefix("/rest/")
        state["calls"].append((req.method, path))
        if path == "login":
            return httpx.Response(200, json={"data": {}})
        if path == "settings":
            return httpx.Response(200, json={"data": {"versionCli": "2.32.7"}})
        if path == "workflows" and req.method == "POST":
            value = json.loads(req.content)
            assert (
                len(value["nodes"]) == 2
                and value["nodes"][1]["parameters"]["jsCode"] == subject.CODE
            )
            assert all("credentials" not in n for n in value["nodes"])
            value.update(active=False, activeVersionId=None, checksum="test-checksum")
            state["workflow"] = value
            return httpx.Response(200, json={"data": value})
        if path.endswith("/exists"):
            return httpx.Response(
                200, json={"data": {"exists": state["workflow"] is not None}}
            )
        if path.endswith("/run"):
            assert set(json.loads(req.content)) == {
                "triggerToStartFrom",
                "destinationNode",
            }
            return httpx.Response(200, json={"data": {"executionId": "90"}})
        if path == "executions/90":
            out = {"backend_base_url": state["base"]}
            if state["extra_output"]:
                out["secret"] = "must-not-escape"
            runs = {
                "Manual Trigger": [],
                "Read Callback Base URL": [{"data": {"main": [[{"json": out}]]}}],
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "90",
                        "workflowId": state["workflow"]["id"],
                        "status": state["status"],
                        "data": flatted({"resultData": {"runData": runs}}),
                    }
                },
            )
        if path == "executions":
            assert json.loads(req.url.params["filter"])["workflowId"].startswith("c7p")
            return httpx.Response(
                200, json={"data": {"count": 0, "results": [], "estimated": False}}
            )
        if path.endswith("/archive"):
            return httpx.Response(200, json={"data": {}})
        if path.startswith("workflows/"):
            assert path.split("/")[1] == state["workflow"]["id"]
            if req.method == "DELETE":
                if state["delete_fail"]:
                    return httpx.Response(500, json={})
                state["workflow"] = None
                return httpx.Response(200, json={"data": True})
            value = deepcopy(state["workflow"])
            if state["tamper"]:
                value["nodes"].append({"name": "not ours"})
            return httpx.Response(200, json={"data": value})
        pytest.fail("unexpected endpoint")

    return state, httpx.MockTransport(handler)


def observer(server):
    return subject.CallbackObserver(
        "https://n8n.invalid",
        "operator",
        "secret",
        allow_temporary_workflow=True,
        transport=server[1],
        sleep=lambda _: None,
    )


def test_no_authorization_means_no_login_or_mutation(server):
    with pytest.raises(EvidenceError, match="N8N_TEMPORARY_PROBE_NOT_AUTHORIZED"):
        subject.CallbackObserver(
            "https://n8n.invalid", "operator", "secret", transport=server[1]
        )
    assert server[0]["calls"] == []


def test_single_run_cleanup_and_no_existing_workflow_mutation(server):
    with observer(server) as api:
        assert api.read_base_url() == server[0]["base"]
        assert api.cleanup_verified and api.last_execution_id == "90"
        wid = api.last_temporary_workflow_id
    writes = [(m, p) for m, p in server[0]["calls"] if m != "GET"]
    assert writes == [
        ("POST", "login"),
        ("POST", "workflows"),
        ("POST", f"workflows/{wid}/run"),
        ("POST", f"workflows/{wid}/archive"),
        ("DELETE", f"workflows/{wid}"),
    ]
    assert server[0]["workflow"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "status",
        "tamper",
        "delete_fail",
        "extra_output",
        "empty",
        "auth_url",
        "query_url",
        "invalid_scheme",
    ],
)
def test_failure_never_returns_cached_config_and_cleanup_is_scoped(server, fault):
    state = server[0]
    if fault == "status":
        state["status"] = "error"
    elif fault in {"tamper", "delete_fail", "extra_output"}:
        state[fault] = True
    else:
        state["base"] = {
            "empty": "",
            "auth_url": "https://u:secret@host.invalid",
            "query_url": "https://host.invalid?key=secret",
            "invalid_scheme": "ftp://host.invalid",
        }[fault]
    with observer(server) as api:
        with pytest.raises(EvidenceError) as error:
            api.read_base_url()
        assert "secret" not in str(error.value)
        if fault in {"tamper", "delete_fail"}:
            assert not api.cleanup_verified
        else:
            assert api.cleanup_verified and state["workflow"] is None
    if fault == "tamper":
        assert not any(
            p.endswith(("/run", "/archive")) or m == "DELETE" for m, p in state["calls"]
        )
