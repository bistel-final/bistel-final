"""Version-pinned editor reads; MockTransport only, no live login/email."""

import json
from copy import deepcopy

import httpx
import pytest

from app.agent import release_n8n as evidence
from app.agent import release_n8n_session as subject
from app.agent.release_artifacts import EvidenceError
from tests.unit.test_agent_release_n8n import (
    ADDRESS,
    NOW,
    SECRET,
    VERSION,
    WF,
    execution,
    workflow,
)


def flatted(value):
    # Minimal spec encoder for acyclic JSON fixture; runtime decoder is separate.
    pool = []

    def encode(item):
        if type(item) not in (dict, list, str):
            return item
        index = len(pool)
        pool.append(None)
        if isinstance(item, dict):
            pool[index] = {k: encode(v) for k, v in item.items()}
        elif isinstance(item, list):
            pool[index] = [encode(v) for v in item]
        else:
            pool[index] = item
        return str(index)

    encode(value)
    return json.dumps(pool)


@pytest.fixture
def server():
    wf = workflow()
    wf["activeVersionId"] = VERSION
    wf["settings"] = {}
    email = next(n for n in wf["nodes"] if n["name"] == "Send Email")
    email.update(
        credentials={"smtp": {"id": "smtp-test"}},
        parameters={"fromEmail": "FDC Agent <from@example.invalid>"},
    )
    run = execution()
    run["workflowVersionId"] = run["workflowData"].pop("versionId")
    run["data"] = flatted(run["data"])
    data = {
        "settings": {
            "versionCli": subject.SUPPORTED_VERSION,
            "saveDataSuccessExecution": "all",
            "saveDataErrorExecution": "all",
        },
        "workflows/" + WF: wf,
        "executions/1": run,
        "executions": {
            "count": 1,
            "estimated": False,
            "results": [
                {
                    k: run[k]
                    for k in ("id", "workflowId", "startedAt", "stoppedAt", "status")
                }
            ],
        },
        "credentials/smtp-test": {
            "id": "smtp-test",
            "type": "smtp",
            "data": {
                "host": "smtp.example.invalid",
                "password": SECRET,
                "user": ADDRESS,
            },
        },
    }
    requests = []

    def handler(req):
        requests.append(req)
        path = req.url.path.removeprefix("/rest/")
        if req.method == "POST":
            assert path == "login"
            assert json.loads(req.content) == {
                "emailOrLdapLoginId": ADDRESS,
                "password": SECRET,
            }
            return httpx.Response(
                200,
                json={"data": {"id": "owner"}},
                headers={"Set-Cookie": "n8n-auth=test; Path=/"},
            )
        assert req.method == "GET"
        return httpx.Response(200, json={"data": deepcopy(data[path])})

    return data, requests, httpx.MockTransport(handler)


def client(server, **kwargs):
    return subject.N8nSessionEvidenceApi(
        "https://n8n.example.invalid", ADDRESS, SECRET, transport=server[2], **kwargs
    )


def test_real_probe_uses_session_inherited_retention_and_top_level_version(server):
    with client(server) as api:
        result = evidence.probe_evidence(
            api,
            workflow_id=WF,
            workflow_version=VERSION,
            sample_execution_id="1",
            clock=lambda: NOW,
        )
        assert result.verdict == "PASS"
        assert api.executions(WF, None)["nextCursor"] is None
    assert [r.method for r in server[1]].count("POST") == 1
    assert all("X-N8N-API-KEY" not in r.headers for r in server[1])
    assert all(r.headers.get("Cookie") == "n8n-auth=test" for r in server[1][1:])


def test_smtp_defaults_and_nonsecret_projection(server):
    with client(server) as api:
        value = api.smtp_transport(WF)
    assert value == {
        "workflow_id": WF,
        "workflow_version": VERSION,
        "smtp_host": "smtp.example.invalid",
        "smtp_port": 465,
        "smtp_from": "from@example.invalid",
        "smtp_secure": True,
    }
    assert SECRET not in json.dumps(value) and ADDRESS not in json.dumps(value)
    assert "wf2_callback_endpoint" not in value
    assert server[1][-1].url.params["includeData"] == "true"


@pytest.mark.parametrize(
    "field,value",
    [
        ("port", 587),
        ("secure", False),
    ],
)
def test_explicit_smtp_values_override_pinned_defaults(server, field, value):
    server[0]["credentials/smtp-test"]["data"][field] = value
    with client(server) as api:
        result = api.smtp_transport(WF)
    assert result["smtp_" + field] == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("port", True),
        ("port", "465"),
        ("port", 0),
        ("port", None),
        ("host", "={{$env.SMTP_HOST}}"),
        ("host", "https://smtp.invalid"),
        ("secure", "true"),
    ],
)
def test_invalid_smtp_config_does_not_fall_back(server, field, value):
    server[0]["credentials/smtp-test"]["data"][field] = value
    with (
        client(server) as api,
        pytest.raises(EvidenceError, match="N8N_SESSION_SMTP_INVALID"),
    ):
        api.smtp_transport(WF)


@pytest.mark.parametrize(
    "sender",
    ["=expression", "", "one@example.invalid,two@example.invalid", "x\r\nInjected:yes"],
)
def test_sender_must_be_one_static_address(server, sender):
    server[0]["workflows/" + WF]["nodes"][1]["parameters"]["fromEmail"] = sender
    with (
        client(server) as api,
        pytest.raises(EvidenceError, match="N8N_SESSION_SMTP_INVALID"),
    ):
        api.smtp_transport(WF)


@pytest.mark.parametrize("value", ["none", "all", "DEFAULT", None])
def test_actual_retention_never_blindly_assumes_all(server, value):
    wf = server[0]["workflows/" + WF]
    wf["settings"]["saveDataSuccessExecution"] = value
    server[0]["settings"]["saveDataSuccessExecution"] = "none"
    with client(server) as api:
        projected = api.workflow(WF)
        assert projected["settings"]["saveDataSuccessExecution"] == (
            "all" if value == "all" else "none"
        )
        if value != "all":
            with pytest.raises(EvidenceError, match="N8N_EVIDENCE_RETENTION_REQUIRED"):
                evidence._workflow(projected, WF, VERSION)


def test_workflow_draft_does_not_replace_active_published_version(server):
    server[0]["workflows/" + WF]["activeVersionId"] = "old-version"
    with (
        client(server) as api,
        pytest.raises(EvidenceError, match="N8N_SESSION_WORKFLOW_INVALID"),
    ):
        api.workflow(WF)


@pytest.mark.parametrize(
    "mutation", ["estimated", "truncated", "boolean_count", "over_limit"]
)
def test_inventory_must_be_complete_not_estimated_or_truncated(server, mutation):
    page = server[0]["executions"]
    if mutation == "estimated":
        page["estimated"] = True
    elif mutation == "truncated":
        page["count"] = 2
    elif mutation == "boolean_count":
        page["count"] = True
    else:
        page["results"] *= 101
        page["count"] = 101
    with (
        client(server) as api,
        pytest.raises(EvidenceError, match="N8N_SESSION_INVENTORY_LIMIT"),
    ):
        api.executions(WF, None)


@pytest.mark.parametrize(
    "endpoint,method",
    [
        ("credentials/test", "POST"),
        ("workflows/x/activate", "POST"),
        ("executions/1/retry", "POST"),
        ("credentials/x", "DELETE"),
        ("../settings", "GET"),
    ],
)
def test_mutating_and_escape_endpoints_rejected_before_transport(
    server, endpoint, method
):
    with client(server) as api:
        count = len(server[1])
        with pytest.raises(EvidenceError, match="N8N_SESSION_ENDPOINT_INVALID"):
            api._request(method, endpoint)
        assert len(server[1]) == count


@pytest.mark.parametrize(
    "url",
    [
        "http://n8n.invalid",
        "https://u:p@n8n.invalid",
        "https://n8n.invalid?key=x",
        "https://n8n.invalid/../x",
    ],
)
def test_no_login_before_origin_and_http_authorization_validation(server, url):
    with pytest.raises(EvidenceError, match="N8N_SESSION_CONFIG_INVALID"):
        subject.N8nSessionEvidenceApi(url, ADDRESS, SECRET, transport=server[2])
    assert not server[1]


def test_explicit_insecure_http_authorization(server):
    with subject.N8nSessionEvidenceApi(
        "http://n8n.invalid",
        ADDRESS,
        SECRET,
        allow_insecure_http=True,
        transport=server[2],
    ):
        pass
    assert server[1][0].method == "POST"


def test_server_version_change_fails_before_credential_read(server):
    with client(server) as api:
        server[0]["settings"]["versionCli"] = "99.0.0"
        with pytest.raises(EvidenceError, match="N8N_SESSION_VERSION_UNSUPPORTED"):
            api.smtp_transport(WF)
    assert not any("credentials" in str(r.url) for r in server[1])


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_error_responses_do_not_leak_or_retry(status):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(
            status, text=SECRET, headers={"Location": "https://evil.invalid"}
        )

    with pytest.raises(EvidenceError, match="^N8N_SESSION_READ_FAILED$"):
        subject.N8nSessionEvidenceApi(
            "https://n8n.invalid",
            ADDRESS,
            SECRET,
            transport=httpx.MockTransport(handle),
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "{}",
        '[{"x":"0"}]',
        '[{"x":"9"}]',
        '[{"x":"-1"}]',
        '[{"x":"01"}]',
        '[{"x":{}}]',
        '[{"x":"not-a-ref"}]',
    ],
)
def test_malformed_or_cyclic_flatted_rejected(raw):
    with pytest.raises(EvidenceError):
        subject.decode_execution_data(raw)


def test_flatted_preserves_shared_values_numeric_strings_and_primitives():
    assert subject.decode_execution_data(
        '[{"a":"1","b":"1","n":7,"f":false,"v":null},"123"]'
    ) == {
        "a": "123",
        "b": "123",
        "n": 7,
        "f": False,
        "v": None,
    }


def test_flatted_shared_string_expansion_is_bounded_before_encoding(monkeypatch):
    monkeypatch.setattr(subject, "MAX_RESPONSE_BYTES", 300)
    raw = json.dumps([{"a": "1", "b": "1", "c": "1"}, "x" * 110])
    assert len(raw) < 300
    with pytest.raises(EvidenceError, match="N8N_SESSION_DATA_LIMIT"):
        subject.decode_execution_data(raw)


def test_flatted_excessive_depth_rejected():
    pool = [{"x": str(i + 1)} for i in range(102)] + ["end"]
    with pytest.raises(EvidenceError, match="N8N_SESSION_DATA_LIMIT"):
        subject.decode_execution_data(json.dumps(pool))


def test_conflicting_execution_version_sources_rejected():
    data = execution()
    data["workflowVersionId"] = "different"
    with pytest.raises(EvidenceError, match="N8N_EVIDENCE_EXECUTION_MISMATCH"):
        evidence.project_execution(
            data,
            workflow_id=WF,
            workflow_version=VERSION,
            execution_id="1",
            observed_at=NOW,
        )
