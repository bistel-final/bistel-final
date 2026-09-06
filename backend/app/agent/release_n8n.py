"""V5-C-7.1 read-only WF2 evidence adapter, not a Stage2 controller.

Only public API GETs are possible. Raw execution data (headers, signatures,
mail text and credentials) stays in memory; callers receive a narrow private
projection. No file writer, workflow activation, retry or SMTP operation exists.
The caller still owns DB callbacks, SMTP config, grant and lifecycle binding.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.agent.release_artifacts import EvidenceError, canonical_json, parse_json
from app.agent.release_delivery import (
    EmailTarget,
    EmailTargetV2,
    ProviderAcceptance,
    ProviderAcceptanceV2,
)
from app.agent.release_prepared import (
    N8nEvidenceProbe,
    canonical_recipients,
    recipient_hash,
    utc,
)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 20
PAGE_SIZE = 100
_NODE_TYPES = {
    "Validate Email Payload": "n8n-nodes-base.code",
    "Send Email": "n8n-nodes-base.emailSend",
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceError(code)


def _id(value: str) -> str:
    _require(
        type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None,
        "N8N_EVIDENCE_ID_INVALID",
    )
    return value


def _timestamp(value: str) -> datetime:
    # n8n uses RFC3339 milliseconds; our persisted evidence uses UTC seconds.
    _require(
        type(value) is str
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value)
        is not None,
        "N8N_EVIDENCE_TIME_INVALID",
    )
    try:
        return datetime.fromisoformat(value.removesuffix("Z"))
    except ValueError:
        raise EvidenceError("N8N_EVIDENCE_TIME_INVALID") from None


class N8nEvidenceApi:
    """Bounded same-origin GET client. Never follows redirects or environment proxies.

    base_url is the explicitly selected n8n origin (optional installation prefix),
    NOT a webhook URL. API keys are passed in memory, never argv or URL parameters.
    Use a read-only API key and HTTPS on the shared host. HTTP remains available
    for the existing isolated LAN deployment; this does not approve that access.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            url = urlsplit(base_url)
            _require(
                url.scheme in {"https", "http"}
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and not re.search(r"[\s\\%]", base_url)
                and all(p not in {".", ".."} for p in url.path.split("/"))
                and type(api_key) is str
                and 1 <= len(api_key) <= 8192
                and api_key.isascii()
                and not re.search(r"\s", api_key),
                "N8N_EVIDENCE_CONFIG_INVALID",
            )
            self._client = httpx.Client(
                base_url=base_url.rstrip("/") + "/api/v1/",
                headers={"X-N8N-API-KEY": api_key, "Accept": "application/json"},
                timeout=15,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            )
        except (ValueError, TypeError):
            raise EvidenceError("N8N_EVIDENCE_CONFIG_INVALID") from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> N8nEvidenceApi:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        _require(
            re.fullmatch(
                r"(?:workflows/[A-Za-z0-9_-]+|executions(?:/[A-Za-z0-9_-]+)?)", path
            )
            is not None,
            "N8N_EVIDENCE_ENDPOINT_INVALID",
        )
        try:
            with self._client.stream("GET", path, params=params) as response:
                _require(response.status_code == 200, "N8N_EVIDENCE_READ_FAILED")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    _require(
                        len(raw) + len(chunk) <= MAX_RESPONSE_BYTES,
                        "N8N_EVIDENCE_RESPONSE_TOO_LARGE",
                    )
                    raw.extend(chunk)
                value = parse_json(bytes(raw))
                _require(type(value) is dict, "N8N_EVIDENCE_RESPONSE_INVALID")
                return value
        except (httpx.HTTPError, ValueError, TypeError) as error:
            # Do not propagate bodies, URLs, header values or provider exceptions.
            code = (
                str(error)
                if isinstance(error, EvidenceError)
                else "N8N_EVIDENCE_READ_FAILED"
            )
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code):
                code = "N8N_EVIDENCE_READ_FAILED"
            raise EvidenceError(code) from None

    def workflow(self, identifier: str) -> dict[str, Any]:
        return self._get(f"workflows/{_id(identifier)}", {})

    def execution(self, identifier: str) -> dict[str, Any]:
        return self._get(f"executions/{_id(identifier)}", {"includeData": "true"})

    def executions(self, workflow_id: str, cursor: str | None) -> dict[str, Any]:
        params = {
            "workflowId": _id(workflow_id),
            "includeData": "false",
            "limit": str(PAGE_SIZE),
        }
        if cursor is not None:
            _require(
                type(cursor) is str
                and 1 <= len(cursor) <= 2048
                and not any(ord(c) < 32 for c in cursor),
                "N8N_EVIDENCE_CURSOR_INVALID",
            )
            params["cursor"] = cursor
        return self._get("executions", params)


def _workflow(value: dict, workflow_id: str, version: str) -> bytes:
    _require(
        value.get("id") == workflow_id
        and value.get("versionId") == version
        and value.get("active") is True,
        "N8N_EVIDENCE_WORKFLOW_MISMATCH",
    )
    settings = value.get("settings")
    _require(
        type(settings) is dict
        and settings.get("saveDataSuccessExecution") == "all"
        and settings.get("saveDataErrorExecution") == "all",
        "N8N_EVIDENCE_RETENTION_REQUIRED",
    )
    # No assumption about inherited instance defaults or active-but-pruned data.
    nodes = value.get("nodes")
    _require(type(nodes) is list, "N8N_EVIDENCE_WORKFLOW_INVALID")
    for name, kind in _NODE_TYPES.items():
        matches = [n for n in nodes if type(n) is dict and n.get("name") == name]
        _require(
            len(matches) == 1 and matches[0].get("type") == kind,
            "N8N_EVIDENCE_WORKFLOW_INVALID",
        )
    # Compare raw workflow bytes only in memory; do not export secrets/node code.
    try:
        return canonical_json(value)
    except (ValueError, TypeError):
        raise EvidenceError("N8N_EVIDENCE_WORKFLOW_INVALID") from None


def _node(run_data: dict, name: str) -> dict:
    """Reject retries/multiple items instead of choosing the first successful send."""
    try:
        runs = run_data[name]
        _require(type(runs) is list and len(runs) == 1, "N8N_EVIDENCE_NODE_AMBIGUOUS")
        run = runs[0]
        _require(type(run) is dict and "error" not in run, "N8N_EVIDENCE_NODE_FAILED")
        main = run["data"]["main"]
        _require(type(main) is list, "N8N_EVIDENCE_NODE_INVALID")
        _require(
            all(type(branch) is list for branch in main), "N8N_EVIDENCE_NODE_INVALID"
        )
        items = [item for branch in main for item in branch]
        _require(len(items) == 1, "N8N_EVIDENCE_NODE_AMBIGUOUS")
        result = items[0]["json"]
        _require(
            type(result) is dict and "error" not in result, "N8N_EVIDENCE_NODE_FAILED"
        )
        return result
    except (KeyError, TypeError, IndexError):
        raise EvidenceError("N8N_EVIDENCE_DATA_MISSING") from None


def project_execution(
    value: dict,
    *,
    workflow_id: str,
    workflow_version: str,
    execution_id: str,
    observed_at: str,
) -> ProviderAcceptance:
    """Actual Send Email result, not webhook ACK or a copied DB callback.

    NodeMailer envelope.to and accepted must both equal the validated request
    recipients, with rejected empty. Partial acceptance is not seven valid sends.
    Non-success execution status is preserved for the separate delivery validator.
    """
    try:
        stored_versions = [
            version
            for version in (
                value.get("workflowData", {}).get("versionId"),
                value.get("workflowVersionId"),
            )
            if version is not None
        ]
        _require(
            value["id"] == execution_id
            and value["workflowId"] == workflow_id
            and value["workflowData"]["id"] == workflow_id
            and bool(stored_versions)
            and all(version == workflow_version for version in stored_versions)
            and value["mode"] == "webhook"
            and value.get("retryOf") is None
            and value.get("retrySuccessId") is None,
            "N8N_EVIDENCE_EXECUTION_MISMATCH",
        )
        _require(
            _timestamp(value["startedAt"])
            <= _timestamp(value["stoppedAt"])
            <= _timestamp(observed_at),
            "N8N_EVIDENCE_TIME_INVALID",
        )
        run_data = value["data"]["resultData"]["runData"]
        envelope = _node(run_data, "Validate Email Payload")
        _require(envelope.get("schema_ok") is True, "N8N_EVIDENCE_PAYLOAD_INVALID")
        payload = envelope["payload"]
        _require(
            payload["channel"] == "EMAIL" and payload["schema"] == "email-request-v1",
            "N8N_EVIDENCE_PAYLOAD_INVALID",
        )
        target_model = (
            EmailTargetV2
            if payload.get("email_kind") == "ACTION_NOTIFY"
            else EmailTarget
        )
        target = target_model.model_validate(
            {key: payload[key] for key in EmailTarget.model_fields}
        )
        result = _node(run_data, "Send Email")
        requested = canonical_recipients(payload["recipients"])
        recipients = canonical_recipients(result["envelope"]["to"])
        _require(
            requested == recipients == canonical_recipients(result["accepted"])
            and result["rejected"] == [],
            "N8N_EVIDENCE_RECIPIENT_MISMATCH",
        )
        message = result["messageId"]
        _require(
            type(message) is str and bool(message.strip()),
            "N8N_EVIDENCE_PROVIDER_ID_MISSING",
        )
        acceptance_model = (
            ProviderAcceptanceV2
            if isinstance(target, EmailTargetV2)
            else ProviderAcceptance
        )
        return acceptance_model(
            action_id=target.action_id,
            request_hash=target.request_hash,
            channel="EMAIL",
            email_kind=target.email_kind,
            n8n_execution_id=execution_id,
            n8n_status=value["status"],
            provider_message_id=message.strip(),
            recipients=recipients,
            recipient_hash=recipient_hash(recipients),
            observed_at=_timestamp(observed_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except EvidenceError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise EvidenceError("N8N_EVIDENCE_EXECUTION_INVALID") from None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def probe_evidence(
    api: N8nEvidenceApi,
    *,
    workflow_id: str,
    workflow_version: str,
    sample_execution_id: str,
    clock: Callable[[], str] = _now,
) -> N8nEvidenceProbe:
    """Prepare probe uses an EXISTING execution. It never sends a test email."""
    for value in (workflow_id, workflow_version, sample_execution_id):
        _id(value)
    before = _workflow(api.workflow(workflow_id), workflow_id, workflow_version)
    sample = project_execution(
        api.execution(sample_execution_id),
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        execution_id=sample_execution_id,
        observed_at=clock(),
    )
    _require(sample.n8n_status == "success", "N8N_EVIDENCE_PROBE_FAILED")
    _require(
        before == _workflow(api.workflow(workflow_id), workflow_id, workflow_version),
        "N8N_EVIDENCE_WORKFLOW_DRIFT",
    )
    return N8nEvidenceProbe(
        execution_data_retained=True,
        returns_action_id=True,
        returns_recipient=True,
        verdict="PASS",
    )


def _inventory(
    api: N8nEvidenceApi, workflow_id: str, resume_at: str
) -> dict[str, dict]:
    cursor = None
    cursors: set[str] = set()
    seen: set[str] = set()
    selected = {}
    for _page in range(MAX_PAGES):
        page = api.executions(workflow_id, cursor)
        _require(
            type(page.get("data")) is list
            and len(page["data"]) <= PAGE_SIZE
            and "nextCursor" in page,
            "N8N_EVIDENCE_PAGE_INVALID",
        )
        for row in page["data"]:
            try:
                identifier = _id(row["id"])
                _require(identifier not in seen, "N8N_EVIDENCE_EXECUTION_DUPLICATE")
                seen.add(identifier)
                _require(
                    row["workflowId"] == workflow_id, "N8N_EVIDENCE_WORKFLOW_MISMATCH"
                )
                # No status filter: running/failed/queued records cannot disappear
                # from the evidence population. Missing start time is ambiguous.
                if _timestamp(row["startedAt"]) >= utc(resume_at):
                    selected[identifier] = {
                        k: row[k]
                        for k in (
                            "id",
                            "workflowId",
                            "startedAt",
                            "stoppedAt",
                            "status",
                        )
                    }
            except (KeyError, TypeError, ValueError) as error:
                if isinstance(error, EvidenceError):
                    raise
                raise EvidenceError("N8N_EVIDENCE_PAGE_INVALID") from None
        cursor = page.get("nextCursor")
        if cursor is None:
            return selected
        _require(
            type(cursor) is str and bool(cursor) and cursor not in cursors,
            "N8N_EVIDENCE_CURSOR_INVALID",
        )
        cursors.add(cursor)
    raise EvidenceError("N8N_EVIDENCE_PAGINATION_INCOMPLETE")


def collect_acceptances(
    api: N8nEvidenceApi,
    *,
    workflow_id: str,
    workflow_version: str,
    targets: list[EmailTarget],
    resume_at: str,
    clock: Callable[[], str] = _now,
) -> list[ProviderAcceptance]:
    """Collect a closed, single-WF2 window after resume; never retry/send.

    Two complete inventories and two projected reads detect observed drift. This
    is NOT an atomic n8n snapshot nor proof against later executions. Stage2 must
    keep workload/HITL closed while collecting DB and Kafka evidence separately.
    Unexpected executions in this WF2 window fail closed; never silently discard
    a duplicate/retry or claim seven successes from a larger population.
    """
    _id(workflow_id)
    _id(workflow_version)
    try:
        utc(resume_at)
    except (ValueError, TypeError):
        raise EvidenceError("N8N_EVIDENCE_TIME_INVALID") from None
    try:
        targets = [
            (
                EmailTargetV2 if t.email_kind == "ACTION_NOTIFY" else EmailTarget
            ).model_validate(t.model_dump())
            for t in targets
        ]
    except (ValueError, AttributeError, TypeError):
        raise EvidenceError("N8N_EVIDENCE_TARGET_INVALID") from None
    _require(
        len(targets) == 7
        and Counter(t.action_code for t in targets) == {"WARNING": 4, "EQP_HOLD": 3}
        and len({t.action_id for t in targets})
        == len({t.request_hash for t in targets})
        == 7,
        "N8N_EVIDENCE_TARGET_INVALID",
    )
    before = _workflow(api.workflow(workflow_id), workflow_id, workflow_version)
    inventory = _inventory(api, workflow_id, resume_at)
    _require(len(inventory) == 7, "N8N_EVIDENCE_POPULATION_INVALID")
    observed = clock()
    _require(utc(resume_at) <= _timestamp(observed), "N8N_EVIDENCE_TIME_INVALID")

    def read(identifier: str) -> ProviderAcceptance:
        raw = api.execution(identifier)
        _require(
            all(raw.get(k) == v for k, v in inventory[identifier].items()),
            "N8N_EVIDENCE_EXECUTION_DRIFT",
        )
        return project_execution(
            raw,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            execution_id=identifier,
            observed_at=observed,
        )

    rows = [read(identifier) for identifier in sorted(inventory)]
    _require(
        Counter((r.action_id, r.request_hash, r.email_kind) for r in rows)
        == Counter((t.action_id, t.request_hash, t.email_kind) for t in targets),
        "N8N_EVIDENCE_TARGET_MISMATCH",
    )
    _require(
        len({r.provider_message_id for r in rows}) == 7,
        "N8N_EVIDENCE_PROVIDER_ID_DUPLICATE",
    )
    _require(
        rows == [read(identifier) for identifier in sorted(inventory)],
        "N8N_EVIDENCE_EXECUTION_DRIFT",
    )
    _require(
        inventory == _inventory(api, workflow_id, resume_at),
        "N8N_EVIDENCE_INVENTORY_DRIFT",
    )
    _require(
        before == _workflow(api.workflow(workflow_id), workflow_id, workflow_version),
        "N8N_EVIDENCE_WORKFLOW_DRIFT",
    )
    return rows
