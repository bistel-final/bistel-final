"""V5-C-4.1 n8n workflow repository contracts.

The workflows are intentionally tested without importing them into the shared n8n.
Static graph checks pin the import JSON while a small Node.js harness executes every
security-sensitive Code node with deterministic inputs.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPOSITORY_ROOT / "deploy" / "n8n"

EXPECTED_FILES = {
    "WF2-notify-email.json",
    "WF3-mes-hold.json",
    "WF4-result-writeback.json",
}
EXPECTED_ROOT_ENTRIES = EXPECTED_FILES | {"runtime-manifest.json", "schemas"}
EXPECTED_TYPES = {
    "n8n-nodes-base.webhook": 2,
    "n8n-nodes-base.code": 2,
    "n8n-nodes-base.if": 2.2,
    "n8n-nodes-base.respondToWebhook": 1.1,
    "n8n-nodes-base.emailSend": 2.1,
    "n8n-nodes-base.kafka": 1,
    "n8n-nodes-base.kafkaTrigger": 1.3,
    "n8n-nodes-base.httpRequest": 4.2,
}
ALLOWED_ENV_REFERENCES = {"N8N_WEBHOOK_SECRET", "BACKEND_BASE_URL"}
EXPECTED_SETTINGS = {
    "saveDataSuccessExecution": "none",
    "saveDataErrorExecution": "none",
    "saveManualExecutions": False,
}
EXPECTED_WEBHOOK_IDS = {
    "Email": "9c8970ad-0b74-4f67-8b63-21d5ee63ec02",
    "MES": "3563b644-54f4-4df1-94ca-c8ae1dc5ca03",
}
HMAC_COMPARE = (
    "authOk = supplied.length === expected.length && "
    "crypto.timingSafeEqual(supplied, expected);"
)
INVERTED_HMAC_COMPARE = (
    "authOk = !(supplied.length === expected.length && "
    "crypto.timingSafeEqual(supplied, expected));"
)

NODE_HARNESS = r"""
const fs = require('fs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
if (payload.now_ms !== null) Date.now = () => payload.now_ms;
const input = { first: () => payload.input, all: () => [payload.input] };
const lookup = (name) => {
  if (!Object.prototype.hasOwnProperty.call(payload.nodes, name)) {
    throw new Error(`HARNESS_NODE_MISSING:${name}`);
  }
  return { first: () => payload.nodes[name], item: payload.nodes[name] };
};
(async () => {
  try {
    // n8n JS Task Runner 샌드박스와 같이 URL·URLSearchParams 전역을 노출하지 않는다.
    const execute = new Function(
      '$input', '$env', '$', 'require', 'URL', 'URLSearchParams',
      `return (async () => {\n${payload.code}\n})()`
    );
    const result = await execute(
      input, payload.env, lookup, require, undefined, undefined
    );
    process.stdout.write(JSON.stringify({ ok: true, result }));
  } catch (error) {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: String(error.message || error),
    }));
  }
})();
"""


def _load_workflows() -> dict[str, dict[str, Any]]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(WORKFLOW_ROOT.glob("WF*.json"))
    }


def _nodes(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["name"]: node for node in workflow["nodes"]}


def _targets(
    workflow: dict[str, Any], node_name: str, output_index: int = 0
) -> list[str]:
    outputs = workflow.get("connections", {}).get(node_name, {}).get("main", [])
    if output_index >= len(outputs):
        return []
    return [edge["node"] for edge in outputs[output_index]]


def _reachable(workflow: dict[str, Any], root: str) -> set[str]:
    seen: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for output in workflow.get("connections", {}).get(current, {}).get("main", []):
            pending.extend(edge["node"] for edge in output)
    return seen


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _all_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_strings(nested)


def _code(workflow: dict[str, Any], node_name: str) -> str:
    return _nodes(workflow)[node_name]["parameters"]["jsCode"]


def _run_code(
    workflow: dict[str, Any],
    node_name: str,
    *,
    input_item: dict[str, Any],
    env: dict[str, str] | None = None,
    nodes: dict[str, dict[str, Any]] | None = None,
    now_seconds: int = 1_800_000_000,
) -> dict[str, Any]:
    node_binary = shutil.which("node")
    assert node_binary is not None, "Node.js is required for the n8n Code-node contract"
    payload = {
        "code": _code(workflow, node_name),
        "input": input_item,
        "env": env or {},
        "nodes": nodes or {},
        "now_ms": now_seconds * 1000,
    }
    completed = subprocess.run(
        [node_binary, "-e", NODE_HARNESS],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout, completed.stderr
    return json.loads(completed.stdout)


def _result_json(execution: dict[str, Any]) -> dict[str, Any]:
    assert execution["ok"] is True, execution
    return execution["result"][0]["json"]


def _signed_webhook_item(
    raw: bytes,
    *,
    secret: str = "unit-test-secret",
    timestamp: int = 1_800_000_000,
    signed_raw: bytes | None = None,
) -> dict[str, Any]:
    signature = hmac.new(
        secret.encode(),
        str(timestamp).encode()
        + b"."
        + (signed_raw if signed_raw is not None else raw),
        hashlib.sha256,
    ).hexdigest()
    return {
        "json": {
            "headers": {
                "x-delivery-timestamp": str(timestamp),
                "x-delivery-signature": f"sha256={signature}",
            }
        },
        "binary": {"data": {"data": base64.b64encode(raw).decode()}},
    }


def _valid_email_payload() -> dict[str, Any]:
    return {
        "schema": "email-request-v1",
        "action_id": "ACT-0001",
        "channel": "EMAIL",
        "action_code": "WARNING",
        "email_kind": "WARNING_NOTIFY",
        "request_hash": "a" * 64,
        "recipients": ["operator@example.invalid"],
        "lot_id": "LOT-001",
        "chamber_id": "CH-01",
        "summary": "FDC 경고 요약",
        "approval_id": None,
    }


def _valid_mes_payload() -> dict[str, Any]:
    action_id = "ACT-0002"
    request_hash = "b" * 64
    event_digest = hashlib.sha256(
        f"{action_id}\0MES_MOCK\0{request_hash}".encode()
    ).hexdigest()
    return {
        "schema": "mes-hold-request-v1",
        "event_id": f"MES:{event_digest}",
        "action_id": action_id,
        "action_code": "EQP_HOLD",
        "equipment_id": "EQP01",
        "chamber_id": "CH-02",
        "command": "HOLD",
        "decided_by": "operator-1",
        "decided_at": "2026-08-28T09:00:00+09:00",
        "request_hash": request_hash,
        "occurred_at": "2026-08-28T08:55:00+09:00",
    }


def _valid_result_record(status: str = "SENT") -> dict[str, Any]:
    return {
        "json": {
            "topic": "fdc.actions.result",
            "partition": 0,
            "offset": "42",
            "key": "ACT-0002",
            "message": {
                "action_id": "ACT-0002",
                "request_hash": "b" * 64,
                "status": status,
                "error_code": None if status == "SENT" else "MES_DEVICE_REJECTED",
            },
        }
    }


def _http_contract(node: dict[str, Any]) -> bool:
    parameters = node.get("parameters", {})
    options = parameters.get("options", {})
    response = options.get("response", {}).get("response", {})
    headers = {
        entry.get("name"): entry.get("value")
        for entry in parameters.get("headerParameters", {}).get("parameters", [])
    }
    return (
        node.get("type") == "n8n-nodes-base.httpRequest"
        and node.get("typeVersion") == 4.2
        and node.get("onError") == "continueErrorOutput"
        and parameters.get("method") == "POST"
        and parameters.get("contentType") == "raw"
        and parameters.get("rawContentType") == "application/json"
        and parameters.get("body") == "={{ $json.callback_raw_body }}"
        and response == {"fullResponse": True, "neverError": True}
        and options.get("timeout") == 10000
        and headers
        == {
            "Content-Type": "application/json",
            "X-Delivery-Timestamp": "={{ $json.callback_timestamp }}",
            "X-Delivery-Signature": "={{ $json.callback_signature }}",
        }
    )


def _contract_errors(workflows: dict[str, dict[str, Any]]) -> list[str]:
    """Return static contract errors; mutation tests prove each guard can turn red."""

    errors: list[str] = []
    if set(workflows) != EXPECTED_FILES:
        errors.append("workflow-file-set")
        return errors

    for filename, workflow in workflows.items():
        nodes = _nodes(workflow)
        names = [node.get("name") for node in workflow.get("nodes", [])]
        ids = [node.get("id") for node in workflow.get("nodes", [])]
        if len(names) != len(set(names)) or len(ids) != len(set(ids)):
            errors.append(f"{filename}:node-uniqueness")
        roots = [
            node["name"]
            for node in workflow.get("nodes", [])
            if node.get("type")
            in {"n8n-nodes-base.webhook", "n8n-nodes-base.kafkaTrigger"}
        ]
        if len(roots) != 1 or (roots and _reachable(workflow, roots[0]) != set(names)):
            errors.append(f"{filename}:reachability")
        if (
            workflow.get("settings") != EXPECTED_SETTINGS
            or workflow.get("active") is not False
        ):
            errors.append(f"{filename}:settings")
        for node in workflow.get("nodes", []):
            if EXPECTED_TYPES.get(node.get("type")) != node.get("typeVersion"):
                errors.append(f"{filename}:node-version:{node.get('name')}")
            if "credentials" in node:
                errors.append(f"{filename}:credential")
            for key in node.get("parameters", {}):
                if re.search(r"credential|password|api.?key|secret|token", key, re.I):
                    errors.append(f"{filename}:literal-sensitive-key:{key}")
        text = "\n".join(_all_strings(workflow))
        env_refs = set(re.findall(r"\$env\.([A-Z0-9_]+)", text))
        if not env_refs <= ALLOWED_ENV_REFERENCES:
            errors.append(f"{filename}:env-reference")
        if re.search(r"https?://[^\s'\"}`]+", text):
            errors.append(f"{filename}:literal-host")

    email = workflows["WF2-notify-email.json"]
    email_nodes = _nodes(email)
    mes = workflows["WF3-mes-hold.json"]
    mes_nodes = _nodes(mes)
    result = workflows["WF4-result-writeback.json"]
    result_nodes = _nodes(result)

    for workflow, prefix in ((email, "Email"), (mes, "MES")):
        nodes = _nodes(workflow)
        webhook = nodes.get(f"{prefix} Webhook", {})
        parameters = webhook.get("parameters", {})
        if (
            parameters.get("httpMethod") != "POST"
            or parameters.get("responseMode") != "responseNode"
            or parameters.get("options", {}).get("rawBody") is not True
            or webhook.get("webhookId") != EXPECTED_WEBHOOK_IDS[prefix]
        ):
            errors.append(f"{prefix}:webhook")
        verify_name = f"Verify {prefix} Auth"
        verify = nodes.get(verify_name, {}).get("parameters", {}).get("jsCode", "")
        required_verify_snippets = (
            "/^sha256=[0-9a-f]{64}$/",
            "crypto.timingSafeEqual",
            "Buffer.from(base64, 'base64')",
            "candidate.toString('base64') === base64",
            "new TextDecoder('utf-8', { fatal: true })",
            "Math.abs(now - timestamp) <= 300",
            HMAC_COMPARE,
        )
        if not all(snippet in verify for snippet in required_verify_snippets):
            errors.append(f"{prefix}:verify-auth")
        if _targets(workflow, verify_name) != [f"{prefix} Authenticated"]:
            errors.append(f"{prefix}:verify-edge")

    if _targets(email, "Email Authenticated", 1) != ["Respond Email Unauthorized"]:
        errors.append("email:unauthorized-edge")
    if _targets(email, "Email Payload Valid", 0) != ["Send Email"]:
        errors.append("email:effect-edge")
    if _targets(email, "Email Payload Valid", 1) != ["Respond Email Invalid"]:
        errors.append("email:invalid-edge")
    if (
        email_nodes.get("Respond Email Invalid", {})
        .get("parameters", {})
        .get("options", {})
        .get("responseCode")
        != 422
    ):
        errors.append("email:422")
    send_email = email_nodes.get("Send Email", {})
    if send_email.get("onError") != "continueErrorOutput":
        errors.append("email:error-output")
    if (
        send_email.get("parameters", {}).get("fromEmail")
        != "FDC Agent <no-reply@example.invalid>"
    ):
        errors.append("email:sender-placeholder")
    if r"const emailAddress = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;" not in _code(
        email, "Validate Email Payload"
    ):
        errors.append("email:recipient-format")
    if _targets(email, "Send Email", 0) != ["Build Email Callback"] or _targets(
        email, "Send Email", 1
    ) != ["Build Email Callback"]:
        errors.append("email:callback-edges")
    if not _http_contract(email_nodes.get("Post Email Callback", {})):
        errors.append("email:http-contract")
    if _targets(email, "Post Email Callback", 1) != ["Classify Email Callback"]:
        errors.append("email:http-error-edge")
    if _targets(email, "Email Callback Configured", 1) != [
        "Respond Email Callback Failed"
    ]:
        errors.append("email:missing-config-edge")

    if _targets(mes, "MES Authenticated", 1) != ["Respond MES Unauthorized"]:
        errors.append("mes:unauthorized-edge")
    if _targets(mes, "MES Payload Valid", 0) != ["Prepare Kafka Event"]:
        errors.append("mes:effect-edge")
    if _targets(mes, "MES Payload Valid", 1) != ["Respond MES Invalid"]:
        errors.append("mes:invalid-edge")
    if (
        mes_nodes.get("Respond MES Invalid", {})
        .get("parameters", {})
        .get("options", {})
        .get("responseCode")
        != 422
    ):
        errors.append("mes:422")
    producer = mes_nodes.get("Publish MES Hold", {})
    producer_parameters = producer.get("parameters", {})
    producer_options = producer_parameters.get("options", {})
    if (
        producer.get("onError") != "continueErrorOutput"
        or producer_parameters.get("topic") != "fdc.actions"
        or producer_parameters.get("useKey") is not True
        or producer_parameters.get("key") != "={{ $json.action_id }}"
        or producer_parameters.get("sendInputData") is not True
        or producer_options.get("acks") is not True
        or producer_options.get("timeout") != 10000
    ):
        errors.append("mes:kafka-producer")
    accepted_response = mes_nodes.get("Respond MES Accepted", {}).get("parameters", {})
    failure_recorded_response = mes_nodes.get("Respond MES Failure Recorded", {}).get(
        "parameters", {}
    )
    if (
        accepted_response.get("responseBody") != '={"ok":true,"published":true}'
        or accepted_response.get("options", {}).get("responseCode") != 200
        or failure_recorded_response.get("responseBody")
        != '={"ok":true,"published":false}'
        or failure_recorded_response.get("options", {}).get("responseCode") != 200
    ):
        errors.append("mes:response-semantics")
    if _targets(mes, "Publish MES Hold", 0) != ["Respond MES Accepted"]:
        errors.append("mes:success-without-callback")
    if _targets(mes, "Publish MES Hold", 1) != ["Build Kafka Failure Callback"]:
        errors.append("mes:failure-callback")
    if not _http_contract(mes_nodes.get("Post Kafka Failure Callback", {})):
        errors.append("mes:http-contract")
    if _targets(mes, "Post Kafka Failure Callback", 1) != [
        "Classify Kafka Failure Callback"
    ]:
        errors.append("mes:http-error-edge")

    trigger = result_nodes.get("MES Result Trigger", {})
    trigger_parameters = trigger.get("parameters", {})
    trigger_options = trigger_parameters.get("options", {})
    if (
        trigger_parameters.get("topic") != "fdc.actions.result"
        or trigger_parameters.get("groupId") != "kosa-fdc-wf4-writeback"
        or trigger_parameters.get("resolveOffset") != "onSuccess"
        or "resolveOffset" in trigger_options
        or trigger_options.get("errorRetryDelay") != 5000
        or trigger_options.get("eachBatchAutoResolve") is not False
    ):
        errors.append("result:kafka-trigger")
    if _targets(result, "MES Result Valid", 0) != ["Build Result Callback"]:
        errors.append("result:valid-edge")
    if _targets(result, "MES Result Valid", 1) != ["Discard Invalid Result"]:
        errors.append("result:discard-edge")
    if _targets(result, "Discard Invalid Result"):
        errors.append("result:discard-terminal")
    if _targets(result, "Result Callback Configured", 1) != [
        "Fail Result Callback Configuration"
    ]:
        errors.append("result:missing-config-fails")
    if not _http_contract(result_nodes.get("Post Result Callback", {})):
        errors.append("result:http-contract")
    if _targets(result, "Post Result Callback", 0) != [
        "Classify Result Callback"
    ] or _targets(result, "Post Result Callback", 1) != ["Classify Result Callback"]:
        errors.append("result:http-edges")
    result_classifier = _code(result, "Classify Result Callback")
    if not all(
        snippet in result_classifier
        for snippet in (
            "throw new Error('CALLBACK_UNAUTHORIZED')",
            "throw new Error(`CALLBACK_REJECTED_${statusCode}`)",
            "throw new Error(`CALLBACK_FAILED_${Number.isInteger(statusCode) ? "
            "statusCode : 'TRANSPORT'}`)",
        )
    ):
        errors.append("result:non-2xx-must-fail")
    if "throw new Error('CALLBACK_CONFIG_MISSING')" not in _code(
        result, "Fail Result Callback Configuration"
    ):
        errors.append("result:missing-config-terminal")

    return errors


def test_r01_exact_workflow_allowlist_and_no_wf1() -> None:
    workflows = _load_workflows()
    assert {path.name for path in WORKFLOW_ROOT.iterdir()} == EXPECTED_ROOT_ENTRIES
    assert set(workflows) == EXPECTED_FILES
    assert all(not name.startswith("WF1") for name in workflows)
    assert {workflow["name"] for workflow in workflows.values()} == {
        "WF2-notify-email",
        "WF3-mes-hold",
        "WF4-result-writeback",
    }


def test_r02_every_node_is_unique_and_reachable() -> None:
    for workflow in _load_workflows().values():
        names = [node["name"] for node in workflow["nodes"]]
        ids = [node["id"] for node in workflow["nodes"]]
        roots = [
            node["name"]
            for node in workflow["nodes"]
            if node["type"] in {"n8n-nodes-base.webhook", "n8n-nodes-base.kafkaTrigger"}
        ]
        assert len(names) == len(set(names))
        assert len(ids) == len(set(ids))
        assert len(roots) == 1
        assert _reachable(workflow, roots[0]) == set(names)


def test_r03_webhooks_require_raw_body_and_response_nodes() -> None:
    workflows = _load_workflows()
    webhook_ids: list[str] = []
    for filename, node_name, prefix, path in (
        ("WF2-notify-email.json", "Email Webhook", "Email", "fdc-notify-email"),
        ("WF3-mes-hold.json", "MES Webhook", "MES", "fdc-mes-hold"),
    ):
        webhook = _nodes(workflows[filename])[node_name]
        webhook_id = webhook["webhookId"]
        webhook_ids.append(webhook_id)
        assert webhook_id == EXPECTED_WEBHOOK_IDS[prefix]
        assert UUID(webhook_id).version == 4
        parameters = webhook["parameters"]
        assert parameters == {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "responseNode",
            "options": {"rawBody": True},
        }
    assert len(webhook_ids) == len(set(webhook_ids))


def test_r04_authentication_precedes_schema_validation() -> None:
    workflows = _load_workflows()
    assert _targets(workflows["WF2-notify-email.json"], "Email Webhook") == [
        "Verify Email Auth"
    ]
    assert _targets(workflows["WF2-notify-email.json"], "Email Authenticated") == [
        "Validate Email Payload"
    ]
    assert _targets(workflows["WF3-mes-hold.json"], "MES Webhook") == [
        "Verify MES Auth"
    ]
    assert _targets(workflows["WF3-mes-hold.json"], "MES Authenticated") == [
        "Validate MES Payload"
    ]


def test_r05_rejected_inputs_cannot_reach_external_effects() -> None:
    workflows = _load_workflows()
    email = workflows["WF2-notify-email.json"]
    mes = workflows["WF3-mes-hold.json"]
    assert _targets(email, "Email Authenticated", 1) == ["Respond Email Unauthorized"]
    assert _targets(email, "Email Payload Valid", 1) == ["Respond Email Invalid"]
    assert _targets(mes, "MES Authenticated", 1) == ["Respond MES Unauthorized"]
    assert _targets(mes, "MES Payload Valid", 1) == ["Respond MES Invalid"]


def test_r06_email_effect_and_callback_terminals_are_explicit() -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    assert _targets(workflow, "Send Email", 0) == ["Build Email Callback"]
    assert _targets(workflow, "Send Email", 1) == ["Build Email Callback"]
    assert _targets(workflow, "Email Callback Succeeded", 0) == [
        "Respond Email Accepted"
    ]
    assert _targets(workflow, "Email Callback Succeeded", 1) == [
        "Respond Email Callback Failed"
    ]


def test_r07_mes_success_has_no_sent_callback_and_failure_does() -> None:
    workflow = _load_workflows()["WF3-mes-hold.json"]
    nodes = _nodes(workflow)
    assert _targets(workflow, "Publish MES Hold", 0) == ["Respond MES Accepted"]
    assert _targets(workflow, "Publish MES Hold", 1) == ["Build Kafka Failure Callback"]
    assert nodes["Publish MES Hold"]["parameters"]["options"] == {
        "acks": True,
        "timeout": 10000,
    }
    assert (
        nodes["Respond MES Accepted"]["parameters"]["responseBody"]
        == '={"ok":true,"published":true}'
    )
    assert (
        nodes["Respond MES Failure Recorded"]["parameters"]["responseBody"]
        == '={"ok":true,"published":false}'
    )
    assert "callback" not in _code(workflow, "Prepare Kafka Event").lower()


def test_r08_no_credentials_or_unapproved_environment_references() -> None:
    workflows = _load_workflows()
    text = "\n".join(_all_strings(workflows))
    assert "credentials" not in text
    assert set(re.findall(r"\$env\.([A-Z0-9_]+)", text)) == ALLOWED_ENV_REFERENCES
    assert not re.search(r"https?://[^\s'\"}`]+", text)
    for workflow in workflows.values():
        assert workflow["active"] is False
        assert workflow["settings"] == EXPECTED_SETTINGS
    assert (
        _nodes(workflows["WF2-notify-email.json"])["Send Email"]["parameters"][
            "fromEmail"
        ]
        == "FDC Agent <no-reply@example.invalid>"
    )


def test_r09_result_offsets_resolve_only_on_success_with_retry_delay() -> None:
    workflow = _load_workflows()["WF4-result-writeback.json"]
    trigger = _nodes(workflow)["MES Result Trigger"]
    assert trigger["parameters"]["groupId"] == "kosa-fdc-wf4-writeback"
    assert trigger["parameters"]["resolveOffset"] == "onSuccess"
    assert "resolveOffset" not in trigger["parameters"]["options"]
    assert trigger["parameters"]["options"]["errorRetryDelay"] == 5000
    assert trigger["parameters"]["options"]["eachBatchAutoResolve"] is False
    classifier = _code(workflow, "Classify Result Callback")
    assert "throw new Error('CALLBACK_UNAUTHORIZED')" in classifier
    assert "throw new Error(`CALLBACK_REJECTED_${statusCode}`)" in classifier
    assert "'TRANSPORT'" in classifier


def test_r10_callbacks_use_one_raw_body_and_signed_headers() -> None:
    workflows = _load_workflows()
    for filename, node_name in (
        ("WF2-notify-email.json", "Post Email Callback"),
        ("WF3-mes-hold.json", "Post Kafka Failure Callback"),
        ("WF4-result-writeback.json", "Post Result Callback"),
    ):
        assert _http_contract(_nodes(workflows[filename])[node_name])


def test_r11_exact_node_types_versions_and_critical_parameters() -> None:
    workflows = _load_workflows()
    assert _contract_errors(workflows) == []


def test_j01_golden_hmac_verifies_exact_raw_bytes() -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    raw = json.dumps(
        _valid_email_payload(), ensure_ascii=False, separators=(",", ":")
    ).encode()
    result = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=_signed_webhook_item(raw),
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
        )
    )
    assert result == {"auth_ok": True, "body_ok": True, "body": _valid_email_payload()}


def test_j02_signature_is_over_raw_body_not_reserialized_json() -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    payload = _valid_email_payload()
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    accepted = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=_signed_webhook_item(raw),
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
        )
    )
    rejected = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=_signed_webhook_item(raw, signed_raw=compact),
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
        )
    )
    assert accepted["auth_ok"] is True
    assert rejected["auth_ok"] is False


def test_j03_any_raw_byte_change_invalidates_signature() -> None:
    workflow = _load_workflows()["WF3-mes-hold.json"]
    raw = json.dumps(_valid_mes_payload(), separators=(",", ":")).encode()
    changed = raw.replace(b"EQP01", b"EQP02")
    result = _result_json(
        _run_code(
            workflow,
            "Verify MES Auth",
            input_item=_signed_webhook_item(changed, signed_raw=raw),
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
        )
    )
    assert result["auth_ok"] is False


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [(300, True), (301, False), (-300, True), (-301, False)],
)
def test_j04_timestamp_window_boundary(age_seconds: int, expected: bool) -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    now = 1_800_000_000
    raw = json.dumps(_valid_email_payload(), separators=(",", ":")).encode()
    result = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=_signed_webhook_item(raw, timestamp=now - age_seconds),
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
            now_seconds=now,
        )
    )
    assert result["auth_ok"] is expected


@pytest.mark.parametrize(
    "item",
    [
        {"json": {"headers": {}}, "binary": {"data": {"data": "e30="}}},
        {
            "json": {
                "headers": {
                    "x-delivery-timestamp": "not-an-integer",
                    "x-delivery-signature": "sha256=" + "0" * 64,
                }
            },
            "binary": {"data": {"data": "e30="}},
        },
        {
            "json": {
                "headers": {
                    "x-delivery-timestamp": "1800000000",
                    "x-delivery-signature": "sha256=" + "A" * 64,
                }
            },
            "binary": {"data": {"data": "e30="}},
        },
        {
            "json": {
                "headers": {
                    "x-delivery-timestamp": "1800000000",
                    "x-delivery-signature": "sha256=" + "0" * 64,
                }
            },
            "binary": {"data": {"data": "%%%="}},
        },
    ],
)
def test_j04_malformed_auth_inputs_fail_without_throwing(item: dict[str, Any]) -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    execution = _run_code(
        workflow,
        "Verify Email Auth",
        input_item=item,
        env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
    )
    assert execution["ok"] is True
    assert _result_json(execution)["auth_ok"] is False


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"{bad json", b"[]"])
def test_j04_authenticated_non_object_bodies_reach_schema_rejection(raw: bytes) -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    result = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=_signed_webhook_item(raw),
            env={"N8N_WEBHOOK_SECRET": "unit-test-secret"},
        )
    )
    assert result["auth_ok"] is True
    assert result["body_ok"] is False


def test_j05_callback_signature_matches_the_exact_unicode_raw_body() -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    payload = _valid_email_payload()
    payload["action_id"] = "ACT-한글"
    result = _result_json(
        _run_code(
            workflow,
            "Build Email Callback",
            input_item={"json": {"messageId": "smtp-message-1"}},
            env={
                "N8N_WEBHOOK_SECRET": "unit-test-secret",
                "BACKEND_BASE_URL": "https://backend.example.invalid/",
            },
            nodes={"Validate Email Payload": {"json": {"payload": payload}}},
        )
    )
    expected = hmac.new(
        b"unit-test-secret",
        f"{result['callback_timestamp']}.{result['callback_raw_body']}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert result["callback_signature"] == f"sha256={expected}"
    assert result["callback_url"].endswith("/ACT-%ED%95%9C%EA%B8%80/delivery")
    reparsed = json.dumps(
        json.loads(result["callback_raw_body"]), ensure_ascii=True, sort_keys=True
    )
    assert reparsed != result["callback_raw_body"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(request_hash="A" * 64),
        lambda payload: payload.update(recipients=[]),
        lambda payload: payload.update(recipients=["not-an-email"]),
        lambda payload: payload.update(
            recipients=["operator@example.invalid\nBcc:attacker@example.invalid"]
        ),
        lambda payload: payload.update(approval_id="unexpected"),
        lambda payload: payload.update(channel="MES_MOCK"),
        lambda payload: payload.update(email_kind="APPROVAL_REQUEST"),
        lambda payload: payload.update(extra="unexpected"),
        lambda payload: payload.update(summary="bad\u0000summary"),
    ],
)
def test_j06_email_schema_negative_cases(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    payload = _valid_email_payload()
    mutate(payload)
    result = _result_json(
        _run_code(
            workflow,
            "Validate Email Payload",
            input_item={"json": {"body_ok": True, "body": payload}},
        )
    )
    assert result == {"schema_ok": False, "payload": None}


def test_j06_approval_email_and_smtp_message_id_contracts() -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    payload = _valid_email_payload()
    payload.update(
        action_code="EQP_HOLD",
        email_kind="APPROVAL_REQUEST",
        approval_id="APR-001",
    )
    validated = _result_json(
        _run_code(
            workflow,
            "Validate Email Payload",
            input_item={"json": {"body_ok": True, "body": payload}},
        )
    )
    assert validated["schema_ok"] is True

    missing_message_id = _result_json(
        _run_code(
            workflow,
            "Build Email Callback",
            input_item={"json": {}},
            env={
                "N8N_WEBHOOK_SECRET": "unit-test-secret",
                "BACKEND_BASE_URL": "https://backend.example.invalid",
            },
            nodes={"Validate Email Payload": {"json": {"payload": payload}}},
        )
    )
    callback = json.loads(missing_message_id["callback_raw_body"])
    assert callback["status"] == "FAILED"
    assert callback["error_code"] == "SMTP_MESSAGE_ID_MISSING"
    assert callback["provider_message_id"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("event_id"),
        lambda payload: payload.update(action_code="WARNING"),
        lambda payload: payload.update(command="START"),
        lambda payload: payload.update(decided_at="2026-08-28T09:00:00"),
        lambda payload: payload.update(request_hash="b" * 63),
        lambda payload: payload.update(extra="unexpected"),
    ],
)
def test_j06_mes_schema_negative_cases(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    workflow = _load_workflows()["WF3-mes-hold.json"]
    payload = _valid_mes_payload()
    mutate(payload)
    result = _result_json(
        _run_code(
            workflow,
            "Validate MES Payload",
            input_item={"json": {"body_ok": True, "body": payload}},
        )
    )
    assert result == {"schema_ok": False, "payload": None}


@pytest.mark.parametrize("secret", ["", "   "])
def test_j07_missing_or_blank_secret_fails_authentication(secret: str) -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    raw = json.dumps(_valid_email_payload(), separators=(",", ":")).encode()
    result = _result_json(
        _run_code(
            workflow,
            "Verify Email Auth",
            input_item=_signed_webhook_item(raw),
            env={"N8N_WEBHOOK_SECRET": secret},
        )
    )
    assert result["auth_ok"] is False


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (200, True),
        (299, True),
        (401, False),
        (404, False),
        (409, False),
        (422, False),
        (503, False),
    ],
)
def test_j08_webhook_callback_classifier(status_code: int, expected: bool) -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    result = _result_json(
        _run_code(
            workflow,
            "Classify Email Callback",
            input_item={"json": {"statusCode": status_code}},
        )
    )
    assert result["callback_ok"] is expected


def test_j08_webhook_transport_failure_is_not_success() -> None:
    workflow = _load_workflows()["WF2-notify-email.json"]
    result = _result_json(
        _run_code(
            workflow,
            "Classify Email Callback",
            input_item={"json": {"error": "connection refused"}},
        )
    )
    assert result == {"callback_ok": False, "status_code": None}


@pytest.mark.parametrize(
    ("input_json", "expected_error"),
    [
        ({"statusCode": 401}, "CALLBACK_UNAUTHORIZED"),
        ({"statusCode": 404}, "CALLBACK_REJECTED_404"),
        ({"statusCode": 409}, "CALLBACK_REJECTED_409"),
        ({"statusCode": 422}, "CALLBACK_REJECTED_422"),
        ({"statusCode": 503}, "CALLBACK_FAILED_503"),
        ({"error": "timeout"}, "CALLBACK_FAILED_TRANSPORT"),
    ],
)
def test_j08_result_non_2xx_or_transport_failure_keeps_offset_unresolved(
    input_json: dict[str, Any],
    expected_error: str,
) -> None:
    workflow = _load_workflows()["WF4-result-writeback.json"]
    execution = _run_code(
        workflow,
        "Classify Result Callback",
        input_item={"json": input_json},
    )
    assert execution == {"ok": False, "error": expected_error}


def test_j08_result_2xx_completes_successfully() -> None:
    workflow = _load_workflows()["WF4-result-writeback.json"]
    result = _result_json(
        _run_code(
            workflow,
            "Classify Result Callback",
            input_item={"json": {"statusCode": 200}},
        )
    )
    assert result == {"callback_ok": True, "status_code": 200}


@pytest.mark.parametrize(
    ("filename", "node_name", "nodes"),
    [
        (
            "WF2-notify-email.json",
            "Build Email Callback",
            {"Validate Email Payload": {"json": {"payload": _valid_email_payload()}}},
        ),
        (
            "WF3-mes-hold.json",
            "Build Kafka Failure Callback",
            {"Prepare Kafka Event": {"json": _valid_mes_payload()}},
        ),
    ],
)
@pytest.mark.parametrize(
    "env",
    [
        {
            "N8N_WEBHOOK_SECRET": "",
            "BACKEND_BASE_URL": "https://backend.example.invalid",
        },
        {"N8N_WEBHOOK_SECRET": "unit-test-secret", "BACKEND_BASE_URL": ""},
        {"N8N_WEBHOOK_SECRET": "unit-test-secret", "BACKEND_BASE_URL": "ftp://invalid"},
    ],
)
def test_j09_invalid_outbound_configuration_never_builds_an_http_request(
    filename: str,
    node_name: str,
    nodes: dict[str, dict[str, Any]],
    env: dict[str, str],
) -> None:
    workflow = _load_workflows()[filename]
    result = _result_json(
        _run_code(
            workflow,
            node_name,
            input_item={"json": {"error": "effect failed"}},
            env=env,
            nodes=nodes,
        )
    )
    assert result == {"config_ok": False}
    configured_node = (
        "Email Callback Configured"
        if filename.startswith("WF2")
        else "Kafka Failure Callback Configured"
    )
    assert "Post" not in " ".join(_targets(workflow, configured_node, 1))


def test_j09_result_callback_configuration_failure_is_fail_closed() -> None:
    workflow = _load_workflows()["WF4-result-writeback.json"]
    validated = _result_json(
        _run_code(
            workflow,
            "Validate MES Result",
            input_item=_valid_result_record(),
        )
    )
    result = _result_json(
        _run_code(
            workflow,
            "Build Result Callback",
            input_item={"json": validated},
            env={"N8N_WEBHOOK_SECRET": "", "BACKEND_BASE_URL": ""},
        )
    )
    assert result == {"config_ok": False}
    assert _targets(workflow, "Result Callback Configured", 1) == [
        "Fail Result Callback Configuration"
    ]


def test_result_validator_accepts_exact_sent_and_failed_shapes() -> None:
    workflow = _load_workflows()["WF4-result-writeback.json"]
    for status in ("SENT", "FAILED"):
        result = _result_json(
            _run_code(
                workflow,
                "Validate MES Result",
                input_item=_valid_result_record(status),
            )
        )
        assert result["valid"] is True
        assert result["evidence"]["reason_code"] is None


def _real_n8n_result_record(status: str = "SENT") -> dict[str, Any]:
    """n8n 2.32.7 Kafka Trigger 실측 출력 — `message`·`topic`만 있다."""

    return {
        "json": {
            "message": {
                "action_id": "ACT-0002",
                "error_code": None if status == "SENT" else "MES_DEVICE_REJECTED",
                "request_hash": "b" * 64,
                "status": status,
            },
            "topic": "fdc.actions.result",
        }
    }


@pytest.mark.parametrize("status", ["SENT", "FAILED"])
def test_result_validator_accepts_real_kafka_trigger_record_without_metadata(
    status: str,
) -> None:
    """공용 n8n에서 partition·offset·key 부재로 거부되던 결함 회귀."""

    workflow = _load_workflows()["WF4-result-writeback.json"]
    result = _result_json(
        _run_code(
            workflow,
            "Validate MES Result",
            input_item=_real_n8n_result_record(status),
        )
    )
    assert result["valid"] is True
    assert result["evidence"]["reason_code"] is None
    assert result["metadata"] == {
        "topic": "fdc.actions.result",
        "partition": None,
        "offset": None,
    }

    callback_item = _result_json(
        _run_code(
            workflow,
            "Build Result Callback",
            input_item={"json": result},
            env={
                "N8N_WEBHOOK_SECRET": "unit-test-secret",
                "BACKEND_BASE_URL": "https://backend.example.invalid",
            },
        )
    )
    assert callback_item["config_ok"] is True
    callback = json.loads(callback_item["callback_raw_body"])
    assert callback["status"] == status
    if status == "SENT":
        assert callback["provider_message_id"] == (
            "kafka:fdc.actions.result:ACT-0002:" + "b" * 64
        )
    else:
        assert callback["provider_message_id"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record["json"].update(topic="other"),
        lambda record: record["json"].update(partition=-1),
        lambda record: record["json"].update(offset="4x2"),
        lambda record: record["json"].update(key="ACT-other"),
        lambda record: record["json"].update(key=""),
        lambda record: record["json"]["message"].update(request_hash="B" * 64),
        lambda record: record["json"]["message"].update(error_code="unexpected"),
        lambda record: record["json"]["message"].update(extra="unexpected"),
    ],
)
def test_result_validator_discards_malformed_records(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    workflow = _load_workflows()["WF4-result-writeback.json"]
    record = _valid_result_record()
    mutate(record)
    result = _result_json(
        _run_code(
            workflow,
            "Validate MES Result",
            input_item=record,
        )
    )
    assert result["valid"] is False
    assert result["evidence"]["reason_code"] in {
        "RESULT_PAYLOAD_INVALID",
        "RESULT_METADATA_INVALID",
        "RESULT_IDENTITY_MISMATCH",
    }


MUTATIONS = (
    "raw-body-off",
    "immediate-webhook-response",
    "invalid-to-effect",
    "signature-shape-check-removed",
    "effect-error-output-removed",
    "graph-edge-deleted",
    "http-object-body",
    "kafka-key-event-id",
    "secret-literal",
    "wf1-added",
    "respond-422-removed",
    "unauthorized-to-effect",
    "callback-error-output-removed",
    "callback-before-response",
    "resolve-on-completion",
    "hmac-comparison-inverted",
    "base64-decode-removed",
    "non-2xx-resolved",
    "malformed-reconsumed",
    "resolve-offset-nested",
    "group-id-blank",
    "kafka-key-disabled",
    "kafka-acks-disabled",
    "mes-response-undifferentiated",
    "real-smtp-sender",
    "recipient-format-check-removed",
    "webhook-id-missing",
)


def _mutate(workflows: dict[str, dict[str, Any]], mutation: str) -> None:
    email = workflows["WF2-notify-email.json"]
    email_nodes = _nodes(email)
    mes = workflows["WF3-mes-hold.json"]
    mes_nodes = _nodes(mes)
    result = workflows["WF4-result-writeback.json"]
    result_nodes = _nodes(result)

    if mutation == "raw-body-off":
        email_nodes["Email Webhook"]["parameters"]["options"]["rawBody"] = False
    elif mutation == "immediate-webhook-response":
        email_nodes["Email Webhook"]["parameters"]["responseMode"] = "onReceived"
    elif mutation == "invalid-to-effect":
        email["connections"]["Email Payload Valid"]["main"][1][0]["node"] = "Send Email"
    elif mutation == "signature-shape-check-removed":
        code = _code(email, "Verify Email Auth")
        email_nodes["Verify Email Auth"]["parameters"]["jsCode"] = code.replace(
            "/^sha256=[0-9a-f]{64}$/.test(signature)", "true"
        )
    elif mutation == "effect-error-output-removed":
        email_nodes["Send Email"].pop("onError")
    elif mutation == "graph-edge-deleted":
        email["connections"].pop("Verify Email Auth")
    elif mutation == "http-object-body":
        email_nodes["Post Email Callback"]["parameters"]["contentType"] = "json"
    elif mutation == "kafka-key-event-id":
        mes_nodes["Publish MES Hold"]["parameters"]["key"] = "={{ $json.event_id }}"
    elif mutation == "secret-literal":
        email_nodes["Send Email"]["parameters"]["apiSecret"] = "hard-coded"
    elif mutation == "wf1-added":
        workflows["WF1-alarm-to-agent.json"] = copy.deepcopy(email)
    elif mutation == "respond-422-removed":
        email["nodes"] = [
            node for node in email["nodes"] if node["name"] != "Respond Email Invalid"
        ]
    elif mutation == "unauthorized-to-effect":
        email["connections"]["Email Authenticated"]["main"][1][0]["node"] = "Send Email"
    elif mutation == "callback-error-output-removed":
        email_nodes["Post Email Callback"].pop("onError")
    elif mutation == "callback-before-response":
        email["connections"]["Send Email"]["main"][0][0]["node"] = (
            "Respond Email Accepted"
        )
    elif mutation == "resolve-on-completion":
        result_nodes["MES Result Trigger"]["parameters"]["resolveOffset"] = (
            "onCompletion"
        )
    elif mutation == "hmac-comparison-inverted":
        code = _code(email, "Verify Email Auth")
        email_nodes["Verify Email Auth"]["parameters"]["jsCode"] = code.replace(
            HMAC_COMPARE,
            INVERTED_HMAC_COMPARE,
        )
    elif mutation == "base64-decode-removed":
        code = _code(email, "Verify Email Auth")
        email_nodes["Verify Email Auth"]["parameters"]["jsCode"] = code.replace(
            "Buffer.from(base64, 'base64')", "Buffer.from(base64)"
        )
    elif mutation == "non-2xx-resolved":
        result_nodes["Classify Result Callback"]["parameters"]["jsCode"] = (
            "return [{ json: { callback_ok: false } }];"
        )
    elif mutation == "malformed-reconsumed":
        result["connections"]["MES Result Valid"]["main"][1][0]["node"] = (
            "Fail Result Callback Configuration"
        )
    elif mutation == "resolve-offset-nested":
        trigger = result_nodes["MES Result Trigger"]["parameters"]
        trigger["options"]["resolveOffset"] = trigger.pop("resolveOffset")
    elif mutation == "group-id-blank":
        result_nodes["MES Result Trigger"]["parameters"]["groupId"] = ""
    elif mutation == "kafka-key-disabled":
        mes_nodes["Publish MES Hold"]["parameters"]["useKey"] = False
    elif mutation == "kafka-acks-disabled":
        mes_nodes["Publish MES Hold"]["parameters"]["options"]["acks"] = False
    elif mutation == "mes-response-undifferentiated":
        mes_nodes["Respond MES Accepted"]["parameters"]["responseBody"] = '={"ok":true}'
        mes_nodes["Respond MES Failure Recorded"]["parameters"]["responseBody"] = (
            '={"ok":true}'
        )
    elif mutation == "real-smtp-sender":
        email_nodes["Send Email"]["parameters"]["fromEmail"] = (
            "FDC Agent <fdc@example.com>"
        )
    elif mutation == "recipient-format-check-removed":
        code = _code(email, "Validate Email Payload")
        email_nodes["Validate Email Payload"]["parameters"]["jsCode"] = code.replace(
            r"const emailAddress = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;",
            "const emailAddress = /^.*$/;",
        )
    elif mutation == "webhook-id-missing":
        email_nodes["Email Webhook"].pop("webhookId")
    else:  # pragma: no cover - the parametrized allowlist owns all mutation names
        raise AssertionError(f"unknown mutation: {mutation}")


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_each_planned_mutation_turns_the_static_contract_red(mutation: str) -> None:
    workflows = copy.deepcopy(_load_workflows())
    _mutate(workflows, mutation)
    assert _contract_errors(workflows), f"mutation stayed green: {mutation}"


def test_r12_code_nodes_do_not_depend_on_sandbox_missing_globals() -> None:
    """공용 n8n 2.32.7 Code node에서 `URL is not defined`가 실측됐다(2026-09-02)."""

    for filename, workflow in _load_workflows().items():
        for name, node in _nodes(workflow).items():
            if not node["type"].endswith(".code"):
                continue
            code = node["parameters"]["jsCode"]
            assert "new URL(" not in code, f"{filename}:{name} uses the URL global"
            assert (
                "URLSearchParams" not in code
            ), f"{filename}:{name} uses URLSearchParams"
