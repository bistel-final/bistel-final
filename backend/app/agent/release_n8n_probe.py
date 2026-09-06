"""Explicitly authorized temporary n8n configuration observation.

This operator-only adapter MUTATES temporary n8n metadata, unlike the read-only
evidence API. It never publishes a webhook or invokes existing workflows. Each
call creates only two code-owned nodes, runs once, then deletes the exact new
workflow. No SMTP/Kafka/HTTP/credential node is accepted. No cached env fallback.
"""

import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    parse_json,
)
from app.agent.release_n8n_session import N8nSessionEvidenceApi, decode_execution_data
from app.agent.release_prepared import SmtpConfigSnapshot, canonical_recipients

CODE = (
    "return [{ json: { backend_base_url: "
    "String($env.BACKEND_BASE_URL ?? '').trim() } }];"
)


class CallbackObserver:
    def __init__(
        self,
        base_url,
        username,
        password,
        *,
        allow_temporary_workflow=False,
        allow_insecure_http=False,
        transport=None,
        sleep=time.sleep,
    ):
        if allow_temporary_workflow is not True:
            raise EvidenceError("N8N_TEMPORARY_PROBE_NOT_AUTHORIZED")
        # Authentication/version/HTTP policy is shared, but the mutation methods
        # remain solely on this explicitly authorized operator adapter.
        self.session = N8nSessionEvidenceApi(
            base_url,
            username,
            password,
            allow_insecure_http=allow_insecure_http,
            transport=transport,
        )
        self.sleep = sleep
        self.last_temporary_workflow_id = None
        self.last_execution_id = None
        self.cleanup_verified = False

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _request(self, method, path, payload=None, params=None):
        # All paths are constructed below from our generated ID or returned
        # integer execution ID. No caller-supplied arbitrary mutation endpoint.
        with self.session._client.stream(
            method, path, json=payload, params=params
        ) as r:
            if r.status_code != 200:
                raise EvidenceError("N8N_TEMPORARY_PROBE_REQUEST_FAILED")
            raw = bytearray()
            for chunk in r.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 4 * 1024 * 1024:
                    raise EvidenceError("N8N_TEMPORARY_PROBE_RESPONSE_LIMIT")
        return parse_json(bytes(raw))

    def read_base_url(self):
        self.session._settings()
        wid = "c7p" + uuid.uuid4().hex[:13]
        self.last_temporary_workflow_id = wid
        self.last_execution_id, self.cleanup_verified = None, False
        name = "TEMP-C71-callback-env-read-" + wid
        nodes = [
            {
                "id": str(uuid.uuid4()),
                "name": "Manual Trigger",
                "type": "n8n-nodes-base.manualTrigger",
                "typeVersion": 1,
                "position": [0, 0],
                "parameters": {},
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Read Callback Base URL",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [240, 0],
                "parameters": {"mode": "runOnceForAllItems", "jsCode": CODE},
            },
        ]
        connections = {
            "Manual Trigger": {
                "main": [
                    [{"node": "Read Callback Base URL", "type": "main", "index": 0}]
                ]
            }
        }

        def owned(value):
            return (
                value.get("id") == wid
                and value.get("name") == name
                and value.get("nodes") == nodes
                and value.get("connections") == connections
                and value.get("active") is False
                and value.get("activeVersionId") is None
            )

        def data(method, path, payload=None):
            return self._request(method, path, payload)["data"]

        path = "workflows/" + wid
        # Do not clean up an already-existing collision, even on a read error.
        if data("GET", path + "/exists").get("exists") is not False:
            raise EvidenceError("N8N_TEMPORARY_PROBE_ID_COLLISION")
        try:
            payload = {
                "id": wid,
                "name": name,
                "nodes": nodes,
                "connections": connections,
                "settings": {
                    "executionOrder": "v1",
                    "saveManualExecutions": True,
                    "saveDataSuccessExecution": "all",
                    "saveDataErrorExecution": "all",
                    "executionTimeout": 15,
                },
                "pinData": {},
            }
            value = data("POST", "workflows", payload)
            if not owned(value) or not owned(data("GET", path)):
                raise EvidenceError("N8N_TEMPORARY_PROBE_TARGET_CHANGED")
            run = data(
                "POST",
                path + "/run",
                {
                    "triggerToStartFrom": {"name": "Manual Trigger"},
                    "destinationNode": {
                        "nodeName": "Read Callback Base URL",
                        "mode": "inclusive",
                    },
                },
            )
            eid = run.get("executionId")
            if type(eid) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}", eid):
                raise ValueError
            self.last_execution_id = eid
            for _ in range(25):
                execution = data("GET", "executions/" + eid)
                if execution.get("id") != eid or execution.get("workflowId") != wid:
                    raise ValueError
                if execution.get("status") in {
                    "success",
                    "error",
                    "crashed",
                    "canceled",
                }:
                    break
                self.sleep(1)
            if execution.get("status") != "success":
                raise EvidenceError("N8N_TEMPORARY_PROBE_EXECUTION_FAILED")
            runs = decode_execution_data(execution["data"])["resultData"]["runData"]
            if set(runs) != {"Manual Trigger", "Read Callback Base URL"}:
                raise ValueError
            output = runs["Read Callback Base URL"][0]["data"]["main"][0]
            if len(output) != 1 or set(output[0]["json"]) != {"backend_base_url"}:
                raise ValueError
            base = output[0]["json"]["backend_base_url"]
            parsed = urlsplit(base)
            if (
                type(base) is not str
                or not 0 < len(base) <= 2048
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or re.search(r"[\s\\]", base)
            ):
                raise ValueError
            return base.rstrip("/")
        except Exception:
            raise EvidenceError("N8N_TEMPORARY_PROBE_FAILED") from None
        finally:
            try:
                if data("GET", path + "/exists")["exists"]:
                    value = data("GET", path)
                    if not owned(value):
                        raise ValueError
                    data(
                        "POST",
                        path + "/archive",
                        {"expectedChecksum": value["checksum"]},
                    )
                    if data("DELETE", path) is not True:
                        raise ValueError
                if data("GET", path + "/exists")["exists"] is not False:
                    raise ValueError
                listing = self._request(
                    "GET",
                    "executions",
                    params={"filter": json.dumps({"workflowId": wid}), "limit": "1"},
                )["data"]
                if (
                    listing.get("count") != 0
                    or listing.get("results") != []
                    or listing.get("estimated") is not False
                ):
                    raise ValueError
                self.cleanup_verified = True
            except Exception:
                # Caller must report last_temporary_workflow_id for manual cleanup;
                # never return a config value when cleanup cannot be proven.
                raise EvidenceError("N8N_TEMPORARY_PROBE_CLEANUP_FAILED") from None


def read_smtp_snapshot(*, session, observer, workflow_ids, recipients):
    """Observe current transport and environment, bound to unchanged WF2/3/4."""
    if session is not observer.session:
        raise EvidenceError("N8N_CONFIG_SESSION_MISMATCH")
    if (
        set(workflow_ids) != {"wf2", "wf3", "wf4"}
        or len(set(workflow_ids.values())) != 3
    ):
        raise EvidenceError("N8N_CONFIG_WORKFLOWS_INVALID")
    first = {
        role: session.workflow(identifier) for role, identifier in workflow_ids.items()
    }
    # WF2 must still use the checked-in callback builder; a different code node
    # cannot silently be attested as using BACKEND_BASE_URL in the same way.
    source = json.loads(
        (
            Path(__file__).resolve().parents[3] / "deploy/n8n/WF2-notify-email.json"
        ).read_text()
    )
    expected = [
        n["parameters"] for n in source["nodes"] if n["name"] == "Build Email Callback"
    ]
    actual = [
        n["parameters"]
        for n in first["wf2"]["nodes"]
        if n["name"] == "Build Email Callback"
    ]
    if expected != actual or len(actual) != 1:
        raise EvidenceError("N8N_CALLBACK_BUILDER_CHANGED")
    transport = session.smtp_transport(workflow_ids["wf2"])
    base = observer.read_base_url()
    if transport != session.smtp_transport(workflow_ids["wf2"]) or any(
        digest(canonical_json(first[role]))
        != digest(canonical_json(session.workflow(identifier)))
        for role, identifier in workflow_ids.items()
    ):
        raise EvidenceError("N8N_CONFIG_OBSERVATION_DRIFT")
    return SmtpConfigSnapshot(
        n8n_workflow_versions={
            identifier: first[role]["versionId"]
            for role, identifier in workflow_ids.items()
        },
        smtp_host=transport["smtp_host"],
        smtp_port=transport["smtp_port"],
        smtp_from=transport["smtp_from"],
        recipient_allowlist=canonical_recipients(recipients),
        wf2_callback_endpoint=base + "/internal/actions/{action_id}/delivery",
    )
