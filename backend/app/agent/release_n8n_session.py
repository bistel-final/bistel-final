"""Read-only editor-session adapter for the observed shared n8n 2.32.7.

The only POST is login. No key creation, credential test/export, workflow update,
execution retry or send endpoint exists. Password/cookie/raw execution stays in
memory. UI API is version-pinned and fails closed on a different server version.
"""

from __future__ import annotations

import re
from email.utils import parseaddr
from urllib.parse import urlsplit

import httpx

from app.agent.release_artifacts import EvidenceError, canonical_json, parse_json
from app.agent.release_n8n import MAX_RESPONSE_BYTES, PAGE_SIZE, _id, _require
from app.agent.release_prepared import canonical_recipients

SUPPORTED_VERSION = "2.32.7"


def decode_execution_data(raw: str) -> dict:
    """Bounded flatted object graph; reject cycles and invalid reference indices.

    n8n editor API returns a flatted JSON string, unlike the public API. Shared
    references are accepted, cycles are not needed by evidence DTO projection.
    Expanded size is bounded too, so compact repeated references cannot inflate
    into an unbounded evidence tree.
    """
    _require(
        type(raw) is str and len(raw.encode()) <= MAX_RESPONSE_BYTES,
        "N8N_SESSION_DATA_INVALID",
    )
    pool = parse_json(raw.encode())
    _require(type(pool) is list and 0 < len(pool) <= 100000, "N8N_SESSION_DATA_INVALID")
    active: set[int] = set()
    visited = 0
    expanded_bytes = 0

    def charge(size):
        nonlocal expanded_bytes
        expanded_bytes += size
        _require(expanded_bytes <= MAX_RESPONSE_BYTES, "N8N_SESSION_DATA_LIMIT")

    def reference(value, depth):
        nonlocal visited
        visited += 1
        _require(visited <= 100000 and depth <= 100, "N8N_SESSION_DATA_LIMIT")
        if type(value) is not str:
            _require(
                value is None or type(value) in (bool, int, float),
                "N8N_SESSION_DATA_INVALID",
            )
            charge(len(canonical_json(value)))
            return value
        _require(
            re.fullmatch(r"0|[1-9][0-9]{0,5}", value) is not None,
            "N8N_SESSION_DATA_INVALID",
        )
        return item(int(value), depth)

    def item(index, depth):
        _require(index < len(pool) and index not in active, "N8N_SESSION_DATA_INVALID")
        active.add(index)
        try:
            value = pool[index]
            if type(value) is dict:
                charge(2 + sum(len(canonical_json(key)) + 2 for key in value))
                return {
                    key: reference(child, depth + 1) for key, child in value.items()
                }
            if type(value) is list:
                charge(2 + len(value))
                return [reference(child, depth + 1) for child in value]
            _require(
                value is None or type(value) in (str, bool, int, float),
                "N8N_SESSION_DATA_INVALID",
            )
            charge(len(canonical_json(value)))
            return value
        finally:
            active.remove(index)

    result = item(0, 0)
    _require(
        type(result) is dict and len(canonical_json(result)) <= MAX_RESPONSE_BYTES,
        "N8N_SESSION_DATA_INVALID",
    )
    return result


class N8nSessionEvidenceApi:
    """Explicit login credentials in memory, no environment lookup or retries.

    HTTP credentials require explicit caller authorization. The application does
    not infer that authorization merely from an HTTP URL in a dotenv file.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        allow_insecure_http: bool = False,
        transport=None,
    ):
        try:
            parsed = urlsplit(base_url)
            _require(
                parsed.scheme in ("http", "https")
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and not re.search(r"[\s\\%]", base_url)
                and all(p not in (".", "..") for p in parsed.path.split("/"))
                and type(allow_insecure_http) is bool
                and (parsed.scheme == "https" or allow_insecure_http)
                and type(username) is str
                and 0 < len(username) <= 512
                and type(password) is str
                and 0 < len(password) <= 8192,
                "N8N_SESSION_CONFIG_INVALID",
            )
            self._client = httpx.Client(
                base_url=base_url.rstrip("/") + "/rest/",
                timeout=15,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            )
        except (TypeError, ValueError):
            raise EvidenceError("N8N_SESSION_CONFIG_INVALID") from None
        try:
            self._request(
                "POST",
                "login",
                payload={
                    "emailOrLdapLoginId": username,
                    "password": password,
                },
            )
            self._settings()
        except Exception:
            self.close()
            raise

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _request(self, method, path, *, params=None, payload=None):
        _require(
            (method == "POST" and path == "login")
            or (
                method == "GET"
                and re.fullmatch(
                    r"settings|workflows/[A-Za-z0-9_-]+|executions(?:/[0-9]+)?|credentials/[A-Za-z0-9_-]+",
                    path,
                )
                is not None
            ),
            "N8N_SESSION_ENDPOINT_INVALID",
        )
        try:
            with self._client.stream(
                method, path, params=params, json=payload
            ) as response:
                _require(response.status_code == 200, "N8N_SESSION_READ_FAILED")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    _require(
                        len(raw) + len(chunk) <= MAX_RESPONSE_BYTES,
                        "N8N_SESSION_RESPONSE_LIMIT",
                    )
                    raw.extend(chunk)
                value = parse_json(bytes(raw))
                _require(
                    type(value) is dict and "data" in value,
                    "N8N_SESSION_RESPONSE_INVALID",
                )
                return value["data"]
        except (httpx.HTTPError, TypeError, ValueError) as error:
            if isinstance(error, EvidenceError) and re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,95}", str(error)
            ):
                raise EvidenceError(str(error)) from None
            raise EvidenceError("N8N_SESSION_READ_FAILED") from None

    def _settings(self):
        data = self._request("GET", "settings")
        _require(
            type(data) is dict and data.get("versionCli") == SUPPORTED_VERSION,
            "N8N_SESSION_VERSION_UNSUPPORTED",
        )
        return data

    def workflow(self, identifier):
        # Resolve actual inherited retention, not an assumed success default.
        settings = self._settings()
        data = self._request("GET", "workflows/" + _id(identifier))
        _require(
            type(data) is dict
            and data.get("id") == identifier
            and data.get("activeVersionId") == data.get("versionId")
            and type(data.get("versionId")) is str,
            "N8N_SESSION_WORKFLOW_INVALID",
        )
        declared = data.get("settings", {})
        _require(type(declared) is dict, "N8N_SESSION_WORKFLOW_INVALID")
        effective = declared.copy()
        for key in ("saveDataSuccessExecution", "saveDataErrorExecution"):
            if effective.get(key) is None or effective.get(key) == "DEFAULT":
                effective[key] = settings.get(key)
        data["settings"] = effective
        data["_evidence_retention_source"] = {
            "version": SUPPORTED_VERSION,
            "declared": declared,
            "instance": {
                k: settings.get(k)
                for k in (
                    "saveDataSuccessExecution",
                    "saveDataErrorExecution",
                )
            },
        }
        return data

    def execution(self, identifier):
        _require(
            type(identifier) is str
            and re.fullmatch(r"[1-9][0-9]{0,19}", identifier) is not None,
            "N8N_SESSION_EXECUTION_ID_INVALID",
        )
        data = self._request("GET", "executions/" + identifier)
        _require(
            type(data) is dict and data.get("id") == identifier,
            "N8N_SESSION_EXECUTION_INVALID",
        )
        data["data"] = decode_execution_data(data.get("data"))
        return data

    def executions(self, workflow_id, cursor):
        # Editor pagination differs from the public API. Accept only an exact,
        # non-estimated complete population in one bounded page; never truncate.
        _require(cursor is None, "N8N_SESSION_INVENTORY_LIMIT")
        data = self._request(
            "GET",
            "executions",
            params={
                "filter": canonical_json({"workflowId": _id(workflow_id)}).decode(),
                "limit": str(PAGE_SIZE),
            },
        )
        _require(
            type(data) is dict
            and type(data.get("results")) is list
            and type(data.get("count")) is int
            and data.get("estimated") is False
            and len(data["results"]) == data["count"] <= PAGE_SIZE,
            "N8N_SESSION_INVENTORY_LIMIT",
        )
        return {"data": data["results"], "nextCursor": None}

    def smtp_transport(self, workflow_id):
        """Non-secret SMTP transport only, NOT a complete SmtpConfigSnapshot.

        WF2's current BACKEND_BASE_URL must still be read from the actual n8n
        environment by the caller. An old execution URL is not current config.
        Ports/SSL absent from editor storage use the pinned SMTP type defaults.
        """
        workflow = self.workflow(workflow_id)
        nodes = workflow.get("nodes")
        _require(
            type(nodes) is list and workflow.get("active") is True,
            "N8N_SESSION_SMTP_INVALID",
        )
        matches = [
            n
            for n in nodes
            if type(n) is dict and n.get("type") == "n8n-nodes-base.emailSend"
        ]
        _require(len(matches) == 1, "N8N_SESSION_SMTP_INVALID")
        try:
            node = matches[0]
            identifier = _id(node["credentials"]["smtp"]["id"])
            credential = self._request(
                "GET", "credentials/" + identifier, params={"includeData": "true"}
            )
            _require(
                credential.get("id") == identifier and credential.get("type") == "smtp",
                "N8N_SESSION_SMTP_INVALID",
            )
            data = credential["data"]
            host, port = data["host"], data.get("port", 465)
            secure = data.get("secure", True)
            sender = node["parameters"]["fromEmail"]
            _require(
                type(host) is str
                and re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) is not None
                and type(port) is int
                and 1 <= port <= 65535
                and type(secure) is bool
                and type(sender) is str
                and not sender.startswith("=")
                and not any(c in sender for c in "\r\n,;"),
                "N8N_SESSION_SMTP_INVALID",
            )
            address = canonical_recipients([parseaddr(sender)[1]])[0]
            _require(
                sender.strip() == address
                or re.fullmatch(
                    r"[^<>\r\n,;]+<" + re.escape(address) + r">", sender.strip()
                )
                is not None,
                "N8N_SESSION_SMTP_INVALID",
            )
            return {
                "workflow_id": workflow_id,
                "workflow_version": workflow["versionId"],
                "smtp_host": host,
                "smtp_port": port,
                "smtp_from": address,
                "smtp_secure": secure,
            }
        except (KeyError, AttributeError, TypeError, ValueError):
            raise EvidenceError("N8N_SESSION_SMTP_INVALID") from None
