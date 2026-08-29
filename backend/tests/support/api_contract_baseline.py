"""Strict loader and semantic OpenAPI normalizer for the V5 API baseline.

This module deliberately lives below ``tests``.  It is a reusable seam for the
V5-CM-5.1 sync gate, but it must never import the application, configuration,
database, or web framework.  The fixture is authored from the API Markdown and
CSV contracts; application OpenAPI is comparison input only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)
type ContractFixture = dict[str, Any]
type OperationKey = tuple[str, str]
type NormalizedOperation = dict[str, Any]
type NormalizedContract = dict[OperationKey, NormalizedOperation]

__all__ = [
    "ContractFixture",
    "ContractValidationError",
    "NormalizedContract",
    "load_optional_contract",
    "load_required_contract",
    "load_team_release_contract",
    "normalize_openapi_contract",
]

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "v5_cm_4_4"
_REQUIRED_FIXTURE = _FIXTURE_ROOT / "api_contract_baseline.json"
_OPTIONAL_FIXTURE = _FIXTURE_ROOT / "api_contract_optional.json"
_TEAM_RELEASE_FIXTURE = _FIXTURE_ROOT / "api_contract_team_release.json"

_HTTP_METHODS = {"DELETE", "GET", "PATCH", "POST", "PUT"}
_OWNERS = {"A", "B", "C", "D", "Common"}
_FIELD_TYPES = {
    "any",
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
}
_FIELD_KEYS = {
    "additional_properties",
    "default",
    "enum",
    "format",
    "items",
    "max_length",
    "max_items",
    "maximum",
    "min_items",
    "min_length",
    "minimum",
    "nullable",
    "pattern",
    "ref",
    "required",
    "type",
}
_OPERATION_KEYS = {
    "compatibility",
    "method",
    "owner",
    "path",
    "request",
    "response_shape",
    "responses",
    "sort",
    "success_status",
}
_REQUEST_KEYS = {"body", "header", "path", "query"}
_COMPATIBILITY_KEYS = {"aliases", "canonical", "component", "rule"}
_SOURCE_FILES = {
    "api_markdown": "docs/deliverables/api/API명세서_v3_작업본.md",
    "api_csv": "docs/deliverables/api/API명세서_v3_작업본.csv",
}

_REQUIRED_OPERATION_CONTRACT: dict[OperationKey, tuple[str, int, set[int]]] = {
    ("GET", "/alarms"): ("A", 200, {200, 422, 503}),
    ("GET", "/trace"): ("A", 200, {200, 422, 503}),
    ("GET", "/parameters"): ("A", 200, {200, 503}),
    ("POST", "/documents/search"): ("B", 200, {200, 422, 503}),
    ("GET", "/relations/chambers/{chamber_id}"): (
        "B",
        200,
        {200, 404, 422, 503},
    ),
    ("GET", "/agent/runs"): ("C", 200, {200, 422, 503}),
    ("POST", "/agent/ask"): ("C", 200, {200, 422, 503}),
    ("GET", "/approvals"): ("C", 200, {200, 503}),
    ("POST", "/approvals/{approval_id}/decision"): (
        "C",
        200,
        {200, 404, 409, 422, 503},
    ),
    ("POST", "/agent/runs"): ("C", 202, {202, 404, 409, 422, 503}),
    ("POST", "/internal/actions/{action_id}/delivery"): (
        "C",
        200,
        {200, 401, 404, 409, 422, 503},
    ),
    ("GET", "/audit-logs"): ("D", 200, {200, 422, 503}),
    ("GET", "/health"): ("Common", 200, {200, 500}),
    ("GET", "/health/ready"): ("Common", 200, {200, 503}),
}
_BARE_ARRAYS = {
    ("GET", "/alarms"),
    ("GET", "/trace"),
    ("GET", "/parameters"),
    ("POST", "/documents/search"),
    ("GET", "/agent/runs"),
    ("GET", "/approvals"),
    ("GET", "/audit-logs"),
}
_TEAM_RELEASE_OPERATION_CONTRACT: dict[OperationKey, tuple[str, int, set[int]]] = {
    ("POST", "/analytics/query"): ("D", 200, {200, 422, 503}),
    ("POST", "/analytics/validate"): ("D", 200, {200, 422}),
    ("GET", "/analytics/history"): ("D", 200, {200, 422}),
    ("GET", "/analytics/evaluations"): ("D", 200, {200, 422}),
    ("GET", "/audit-logs/paged"): ("D", 200, {200, 422, 503}),
}
_TEAM_RELEASE_SUCCESS_REFS: dict[OperationKey, str] = {
    ("POST", "/analytics/query"): "AnalysisQueryResponse",
    ("POST", "/analytics/validate"): "SqlValidateResponse",
    ("GET", "/analytics/history"): "NlQueryHistoryResponse",
    ("GET", "/analytics/evaluations"): "EvaluationListResponse",
    ("GET", "/audit-logs/paged"): "AuditLogPageResponse",
}


class ContractValidationError(ValueError):
    """A fixture is malformed or violates the frozen V5 contract."""


def load_required_contract(path: Path | None = None) -> ContractFixture:
    """Load the required 14-operation contract with fail-closed validation."""

    fixture = _load_contract(path or _REQUIRED_FIXTURE)
    _validate_required_semantics(fixture)
    return fixture


def load_optional_contract(path: Path | None = None) -> ContractFixture:
    """Load the implemented optional-operation contract with the same validator."""

    optional = _load_contract(path or _OPTIONAL_FIXTURE)
    required = load_required_contract()
    _validate_common_components(required, optional)
    return optional


def load_team_release_contract(path: Path | None = None) -> ContractFixture:
    """Load the five-operation team release contract independently of routers."""

    team_release = _load_contract(path or _TEAM_RELEASE_FIXTURE)
    required = load_required_contract()
    optional = load_optional_contract()
    _validate_common_components(required, team_release)
    _validate_common_components(optional, team_release)
    _validate_common_operations(optional, team_release)
    _validate_team_release_semantics(team_release)
    return team_release


def _validate_common_components(
    required: ContractFixture,
    optional: ContractFixture,
) -> None:
    required_components = required["components"]
    optional_components = optional["components"]
    common = set(required_components) & set(optional_components)
    mismatched = sorted(
        name
        for name in common
        if required_components[name] != optional_components[name]
    )
    if mismatched:
        raise ContractValidationError(f"공통 component 불일치: {mismatched}")


def _validate_common_operations(
    reference: ContractFixture,
    candidate: ContractFixture,
) -> None:
    reference_operations = {
        (item["method"], item["path"]): item for item in reference["operations"]
    }
    candidate_operations = {
        (item["method"], item["path"]): item for item in candidate["operations"]
    }
    common = set(reference_operations) & set(candidate_operations)
    mismatched = sorted(
        key for key in common if reference_operations[key] != candidate_operations[key]
    )
    if mismatched:
        raise ContractValidationError(f"공통 operation 불일치: {mismatched}")


def _load_contract(path: Path) -> ContractFixture:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(
            f"계약 fixture를 읽을 수 없습니다: {path}"
        ) from exc
    if not isinstance(raw, dict):
        raise ContractValidationError("계약 fixture 최상위 값은 object여야 합니다")
    _validate_envelope(raw)
    return raw


def _exact_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ContractValidationError(
            f"{where} key 불일치: missing={missing}, unknown={unknown}"
        )


def _validate_envelope(fixture: ContractFixture) -> None:
    _exact_keys(
        fixture, {"format_version", "source", "components", "operations"}, "root"
    )
    if fixture["format_version"] != 1:
        raise ContractValidationError("format_version은 1이어야 합니다")

    source = fixture["source"]
    if not isinstance(source, dict):
        raise ContractValidationError("source는 object여야 합니다")
    _exact_keys(source, set(_SOURCE_FILES), "source")
    for key, expected_path in _SOURCE_FILES.items():
        item = source[key]
        if not isinstance(item, dict):
            raise ContractValidationError(f"source.{key}는 object여야 합니다")
        _exact_keys(item, {"path", "role"}, f"source.{key}")
        if item["path"] != expected_path or not isinstance(item["role"], str):
            raise ContractValidationError(
                f"source.{key}의 path/role이 올바르지 않습니다"
            )
        if not item["role"].strip():
            raise ContractValidationError(f"source.{key}.role은 비어 있을 수 없습니다")

    components = fixture["components"]
    if not isinstance(components, dict) or not components:
        raise ContractValidationError("components는 비어 있지 않은 object여야 합니다")
    for name, schema in components.items():
        if not isinstance(name, str) or not name:
            raise ContractValidationError(
                "component 이름은 비어 있지 않은 문자열이어야 합니다"
            )
        _validate_component(schema, f"components.{name}")

    operations = fixture["operations"]
    if not isinstance(operations, list) or not operations:
        raise ContractValidationError("operations는 비어 있지 않은 array여야 합니다")
    seen: set[OperationKey] = set()
    for index, operation in enumerate(operations):
        key = _validate_operation(operation, components, index)
        if key in seen:
            raise ContractValidationError(f"중복 Method+Path: {key[0]} {key[1]}")
        seen.add(key)

    _validate_component_refs(components, operations)


def _validate_component(schema: Any, where: str) -> None:
    if not isinstance(schema, dict):
        raise ContractValidationError(f"{where}는 object여야 합니다")
    schema_type = schema.get("type")
    if schema_type == "named":
        _exact_keys(schema, {"type"}, where)
        return
    if schema_type == "object":
        allowed = {"type", "additional_properties", "fields"}
        if "rules" in schema:
            allowed.add("rules")
        _exact_keys(schema, allowed, where)
        if not isinstance(schema["additional_properties"], bool):
            raise ContractValidationError(
                f"{where}.additional_properties는 bool이어야 합니다"
            )
        fields = schema["fields"]
        if not isinstance(fields, dict) or not fields:
            raise ContractValidationError(
                f"{where}.fields는 비어 있지 않은 object여야 합니다"
            )
        for field_name, field in fields.items():
            if not isinstance(field_name, str) or not field_name:
                raise ContractValidationError(f"{where} field 이름이 올바르지 않습니다")
            _validate_field(field, f"{where}.fields.{field_name}")
        rules = schema.get("rules", [])
        if not isinstance(rules, list) or any(
            not isinstance(rule, str) or not rule.strip() for rule in rules
        ):
            raise ContractValidationError(f"{where}.rules는 문자열 array여야 합니다")
        return
    if schema_type == "discriminated_union":
        _exact_keys(schema, {"type", "discriminator", "variants"}, where)
        if not isinstance(schema["discriminator"], str) or not schema["discriminator"]:
            raise ContractValidationError(f"{where}.discriminator가 올바르지 않습니다")
        variants = schema["variants"]
        if not isinstance(variants, dict) or not variants:
            raise ContractValidationError(
                f"{where}.variants는 비어 있지 않은 object여야 합니다"
            )
        if any(not isinstance(ref, str) or not ref for ref in variants.values()):
            raise ContractValidationError(f"{where}.variants ref가 올바르지 않습니다")
        return
    raise ContractValidationError(f"{where}.type이 지원되지 않습니다: {schema_type!r}")


def _validate_field(field: Any, where: str) -> None:
    if not isinstance(field, dict):
        raise ContractValidationError(f"{where}는 object여야 합니다")
    unknown = set(field) - _FIELD_KEYS
    if unknown:
        raise ContractValidationError(f"{where} unknown key: {sorted(unknown)}")
    if not {"required", "nullable"} <= set(field):
        raise ContractValidationError(f"{where}에 required·nullable이 필요합니다")
    if not isinstance(field["required"], bool) or not isinstance(
        field["nullable"], bool
    ):
        raise ContractValidationError(f"{where} required·nullable은 bool이어야 합니다")
    has_ref = "ref" in field
    has_type = "type" in field
    if has_ref == has_type:
        raise ContractValidationError(
            f"{where}는 ref 또는 type 중 하나만 가져야 합니다"
        )
    if has_ref:
        if not isinstance(field["ref"], str) or not field["ref"]:
            raise ContractValidationError(f"{where}.ref가 올바르지 않습니다")
        if set(field) != {"ref", "required", "nullable"}:
            raise ContractValidationError(
                f"{where} ref field에는 제약을 추가할 수 없습니다"
            )
        return

    field_type = field["type"]
    if field_type not in _FIELD_TYPES:
        raise ContractValidationError(
            f"{where}.type이 지원되지 않습니다: {field_type!r}"
        )
    if field_type == "null" and (
        field["nullable"] or set(field) != {"type", "required", "nullable"}
    ):
        raise ContractValidationError(
            f"{where} null 전용 field는 추가 제약·nullable을 허용하지 않습니다"
        )
    if "enum" in field:
        enum = field["enum"]
        if not isinstance(enum, list) or not enum or len(enum) != len(set(enum)):
            raise ContractValidationError(
                f"{where}.enum은 비어 있지 않은 고유 array여야 합니다"
            )
    for low, high in (
        ("minimum", "maximum"),
        ("min_length", "max_length"),
        ("min_items", "max_items"),
    ):
        if low in field and high in field and field[low] > field[high]:
            raise ContractValidationError(f"{where}의 {low}>{high}입니다")
    if field_type == "array":
        items = field.get("items")
        if not isinstance(items, dict) or set(items) not in ({"ref"}, {"type"}):
            raise ContractValidationError(f"{where}.items는 단일 ref/type이어야 합니다")
        if "ref" in items and (not isinstance(items["ref"], str) or not items["ref"]):
            raise ContractValidationError(f"{where}.items.ref가 올바르지 않습니다")
        if "type" in items and items["type"] not in _FIELD_TYPES - {"array"}:
            raise ContractValidationError(f"{where}.items.type이 올바르지 않습니다")
    elif "items" in field or "min_items" in field or "max_items" in field:
        raise ContractValidationError(
            f"{where}의 items/min_items/max_items는 array 전용입니다"
        )
    if field_type == "object" and "additional_properties" not in field:
        raise ContractValidationError(
            f"{where} object에는 additional_properties가 필요합니다"
        )
    if field_type != "object" and "additional_properties" in field:
        raise ContractValidationError(
            f"{where}.additional_properties는 object 전용입니다"
        )


def _validate_operation(
    operation: Any, components: Mapping[str, Any], index: int
) -> OperationKey:
    where = f"operations[{index}]"
    if not isinstance(operation, dict):
        raise ContractValidationError(f"{where}는 object여야 합니다")
    _exact_keys(operation, _OPERATION_KEYS, where)
    method = operation["method"]
    path = operation["path"]
    if method not in _HTTP_METHODS:
        raise ContractValidationError(
            f"{where}.method는 지원되는 uppercase HTTP method여야 합니다"
        )
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.count("{") != path.count("}")
    ):
        raise ContractValidationError(f"{where}.path가 올바르지 않습니다")
    if operation["owner"] not in _OWNERS:
        raise ContractValidationError(f"{where}.owner가 올바르지 않습니다")
    status = operation["success_status"]
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 200 <= status < 300
    ):
        raise ContractValidationError(f"{where}.success_status가 올바르지 않습니다")

    request = operation["request"]
    if not isinstance(request, dict):
        raise ContractValidationError(f"{where}.request는 object여야 합니다")
    _exact_keys(request, _REQUEST_KEYS, f"{where}.request")
    for location in ("path", "query", "header"):
        fields = request[location]
        if not isinstance(fields, dict):
            raise ContractValidationError(
                f"{where}.request.{location}는 object여야 합니다"
            )
        for name, field in fields.items():
            _validate_field(field, f"{where}.request.{location}.{name}")
    body = request["body"]
    if body is not None:
        if not isinstance(body, dict) or set(body) != {"ref"}:
            raise ContractValidationError(
                f"{where}.request.body는 null 또는 단일 ref여야 합니다"
            )

    responses = operation["responses"]
    if not isinstance(responses, dict) or not responses:
        raise ContractValidationError(
            f"{where}.responses는 비어 있지 않은 object여야 합니다"
        )
    for response_status, response in responses.items():
        if not re.fullmatch(r"[1-5][0-9]{2}", str(response_status)):
            raise ContractValidationError(
                f"{where}.responses status가 올바르지 않습니다"
            )
        if not isinstance(response, dict):
            raise ContractValidationError(
                f"{where}.responses.{response_status}가 올바르지 않습니다"
            )
        _exact_keys(
            response, {"schema_ref", "shape"}, f"{where}.responses.{response_status}"
        )
        if response["shape"] not in {"array", "object"}:
            raise ContractValidationError(
                f"{where}.responses.{response_status}.shape가 올바르지 않습니다"
            )
        if response["schema_ref"] not in components:
            raise ContractValidationError(
                f"{where}.responses.{response_status} ref가 없습니다"
            )
    if str(status) not in responses:
        raise ContractValidationError(f"{where}에 success response가 없습니다")

    response_shape = operation["response_shape"]
    if not isinstance(response_shape, dict):
        raise ContractValidationError(f"{where}.response_shape는 object여야 합니다")
    expected_shape_keys = (
        {"item_ref", "type"}
        if response_shape.get("type") == "array"
        else {"schema_ref", "type"}
    )
    _exact_keys(response_shape, expected_shape_keys, f"{where}.response_shape")
    success = responses[str(status)]
    ref_key = "item_ref" if response_shape["type"] == "array" else "schema_ref"
    if (
        response_shape["type"] != success["shape"]
        or response_shape[ref_key] != success["schema_ref"]
    ):
        raise ContractValidationError(f"{where} success shape가 중복 선언과 다릅니다")

    sort = operation["sort"]
    if not isinstance(sort, list) or any(not isinstance(item, str) for item in sort):
        raise ContractValidationError(f"{where}.sort는 문자열 array여야 합니다")
    if len(sort) != len(set(sort)):
        raise ContractValidationError(f"{where}.sort에 중복이 있습니다")

    compatibility = operation["compatibility"]
    if not isinstance(compatibility, list):
        raise ContractValidationError(f"{where}.compatibility는 array여야 합니다")
    for alias_index, item in enumerate(compatibility):
        alias_where = f"{where}.compatibility[{alias_index}]"
        if not isinstance(item, dict):
            raise ContractValidationError(f"{alias_where}는 object여야 합니다")
        _exact_keys(item, _COMPATIBILITY_KEYS, alias_where)
        if item["component"] not in components:
            raise ContractValidationError(f"{alias_where}.component ref가 없습니다")
        for key in ("aliases", "canonical"):
            values = item[key]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ContractValidationError(
                    f"{alias_where}.{key}가 올바르지 않습니다"
                )
        if not isinstance(item["rule"], str) or not item["rule"].strip():
            raise ContractValidationError(f"{alias_where}.rule이 비어 있습니다")
        component = components[item["component"]]
        if component["type"] != "object":
            raise ContractValidationError(
                f"{alias_where}.component는 object component여야 합니다"
            )
        component_fields = set(component["fields"])
        referenced_fields = set(item["aliases"]) | set(item["canonical"])
        missing_fields = referenced_fields - component_fields
        if missing_fields:
            message = (
                f"{alias_where} alias/canonical field가 없습니다: "
                f"{sorted(missing_fields)}"
            )
            raise ContractValidationError(message)
        if set(item["aliases"]) & set(item["canonical"]):
            raise ContractValidationError(
                f"{alias_where} alias와 canonical field가 겹칩니다"
            )

    return method, path


def _field_refs(field: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    if "ref" in field:
        refs.add(field["ref"])
    items = field.get("items")
    if isinstance(items, dict) and "ref" in items:
        refs.add(items["ref"])
    return refs


def _validate_component_refs(
    components: Mapping[str, Any], operations: list[Mapping[str, Any]]
) -> None:
    graph: dict[str, set[str]] = {name: set() for name in components}
    for name, schema in components.items():
        if schema["type"] == "object":
            for field in schema["fields"].values():
                graph[name].update(_field_refs(field))
        elif schema["type"] == "discriminated_union":
            graph[name].update(schema["variants"].values())

    for operation in operations:
        for location in ("path", "query", "header"):
            for field in operation["request"][location].values():
                missing = _field_refs(field) - set(components)
                if missing:
                    raise ContractValidationError(
                        f"operation request ref가 없습니다: {sorted(missing)}"
                    )
        body = operation["request"]["body"]
        if body is not None and body["ref"] not in components:
            raise ContractValidationError(
                f"operation body ref가 없습니다: {body['ref']}"
            )

    for name, refs in graph.items():
        missing = refs - set(components)
        if missing:
            raise ContractValidationError(
                f"components.{name} ref가 없습니다: {sorted(missing)}"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ContractValidationError(f"component ref cycle이 있습니다: {name}")
        if name in visited:
            return
        visiting.add(name)
        for ref in graph[name]:
            visit(ref)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)


def _validate_required_semantics(fixture: ContractFixture) -> None:
    operations = {
        (item["method"], item["path"]): item for item in fixture["operations"]
    }
    if set(operations) != set(_REQUIRED_OPERATION_CONTRACT):
        missing = sorted(set(_REQUIRED_OPERATION_CONTRACT) - set(operations))
        extra = sorted(set(operations) - set(_REQUIRED_OPERATION_CONTRACT))
        raise ContractValidationError(
            f"필수 operation 집합 불일치: missing={missing}, extra={extra}"
        )

    for key, (owner, success_status, statuses) in _REQUIRED_OPERATION_CONTRACT.items():
        operation = operations[key]
        if operation["owner"] != owner or operation["success_status"] != success_status:
            raise ContractValidationError(f"필수 owner/status 불일치: {key}")
        actual_statuses = {int(status) for status in operation["responses"]}
        if actual_statuses != statuses:
            raise ContractValidationError(f"필수 response status 불일치: {key}")
        expected_shape = "array" if key in _BARE_ARRAYS else "object"
        if operation["response_shape"]["type"] != expected_shape:
            raise ContractValidationError(f"필수 response shape 불일치: {key}")
        for status, response in operation["responses"].items():
            if int(status) == success_status:
                continue
            expected_ref = (
                "ReadinessResponse"
                if key == ("GET", "/health/ready") and status == "503"
                else "ErrorResponse"
            )
            if response != {"shape": "object", "schema_ref": expected_ref}:
                raise ContractValidationError(
                    f"필수 오류 schema 불일치: {key} {status}"
                )

    components = fixture["components"]
    ask_fields = components["AgentAskResponse"]["fields"]
    for name in ("predicted_fault_code", "confidence", "recommended_action"):
        field = ask_fields[name]
        if not field["required"] or not field["nullable"]:
            raise ContractValidationError(
                f"AgentAskResponse.{name}은 required-nullable입니다"
            )
    if components["ApprovalDecisionRequest"]["fields"]["decision"].get("enum") != [
        "APPROVED",
        "REJECTED",
    ]:
        raise ContractValidationError("공개 승인 Enum은 APPROVED|REJECTED입니다")
    document_request = components["DocumentSearchRequest"]["fields"]
    if (
        document_request["query"].get("min_length"),
        document_request["query"].get("max_length"),
    ) != (1, 1000):
        raise ContractValidationError(
            "DocumentSearchRequest.query 범위가 올바르지 않습니다"
        )
    if (
        document_request["top_k"].get("minimum"),
        document_request["top_k"].get("maximum"),
    ) != (1, 10):
        raise ContractValidationError(
            "DocumentSearchRequest.top_k 범위가 올바르지 않습니다"
        )
    ask_request = components["AgentAskRequest"]["fields"]["question"]
    if (ask_request.get("min_length"), ask_request.get("max_length")) != (1, 1000):
        raise ContractValidationError(
            "AgentAskRequest.question 범위가 올바르지 않습니다"
        )
    evidence = components["EvidenceItem"]
    if evidence != {
        "type": "discriminated_union",
        "discriminator": "type",
        "variants": {
            "ALARM": "AlarmEvidence",
            "DOCUMENT": "DocumentEvidence",
            "GRAPH": "GraphEvidence",
            "METROLOGY": "MetrologyEvidence",
            "TRACE": "TraceEvidence",
        },
    }:
        raise ContractValidationError(
            "AgentAsk evidence 5종 discriminator 계약이 다릅니다"
        )

    delivery = components["DeliveryCallbackRequest"]["fields"]
    if delivery["request_hash"].get("pattern") != "^[0-9a-f]{64}$":
        raise ContractValidationError(
            "delivery request_hash는 64 lowercase hex여야 합니다"
        )
    if set(delivery["status"].get("enum", [])) != {"SENT", "FAILED"}:
        raise ContractValidationError("delivery status는 SENT|FAILED여야 합니다")

    projection_fields = {
        ("AgentRunItem", "latency_ms"): {
            "minimum": 0,
            "nullable": False,
            "required": True,
            "type": "integer",
        },
        ("AgentRunItem", "llm_model"): {
            "min_length": 1,
            "nullable": False,
            "required": True,
            "type": "string",
        },
        ("AgentRunItem", "fault_name"): {
            "nullable": False,
            "required": True,
            "type": "null",
        },
        ("AgentRunItem", "fault_color"): {
            "nullable": False,
            "required": True,
            "type": "null",
        },
        ("AutoToolCallItem", "result_summary"): {
            "min_length": 1,
            "nullable": False,
            "required": True,
            "type": "string",
        },
        ("ChatToolCallItem", "result_summary"): {
            "min_length": 1,
            "nullable": False,
            "required": True,
            "type": "string",
        },
        ("ChatToolCallItem", "result"): {
            "min_length": 1,
            "nullable": False,
            "required": True,
            "type": "string",
        },
    }
    for (component_name, field_name), expected in projection_fields.items():
        actual = components[component_name]["fields"][field_name]
        if actual != expected:
            raise ContractValidationError(
                f"공개 projection field 계약 불일치: {component_name}.{field_name}"
            )

    approval = components["ApprovalItem"]["fields"]
    if any(
        not approval[name]["required"] or approval[name]["nullable"]
        for name in ("predicted_fault_code", "fault_code")
    ):
        raise ContractValidationError("승인 예측·alias는 required non-null입니다")
    if approval["action_code"].get("enum") != [
        "MONITORING",
        "WARNING",
        "EQP_HOLD",
    ]:
        raise ContractValidationError("승인 action_code는 ActionCode 3값이어야 합니다")


def _validate_team_release_semantics(fixture: ContractFixture) -> None:
    operations = {
        (item["method"], item["path"]): item for item in fixture["operations"]
    }
    expected = set(_TEAM_RELEASE_OPERATION_CONTRACT)
    if set(operations) != expected:
        missing = sorted(expected - set(operations))
        extra = sorted(set(operations) - expected)
        raise ContractValidationError(
            f"팀 release operation 집합 불일치: missing={missing}, extra={extra}"
        )

    for key, (
        owner,
        success_status,
        statuses,
    ) in _TEAM_RELEASE_OPERATION_CONTRACT.items():
        operation = operations[key]
        if operation["owner"] != owner or operation["success_status"] != success_status:
            raise ContractValidationError(f"팀 release owner/status 불일치: {key}")
        if operation["response_shape"]["type"] != "object":
            raise ContractValidationError(f"팀 release response shape 불일치: {key}")
        expected_ref = _TEAM_RELEASE_SUCCESS_REFS[key]
        if operation["response_shape"]["schema_ref"] != expected_ref:
            raise ContractValidationError(f"팀 release success schema 불일치: {key}")
        actual_statuses = {int(status) for status in operation["responses"]}
        if actual_statuses != statuses:
            raise ContractValidationError(f"팀 release response status 불일치: {key}")
        for status, response in operation["responses"].items():
            if int(status) == success_status:
                continue
            if response != {"shape": "object", "schema_ref": "ErrorResponse"}:
                raise ContractValidationError(
                    f"팀 release 오류 schema 불일치: {key} {status}"
                )

    evaluation = operations[("GET", "/analytics/evaluations")]
    if evaluation["request"] != {
        "body": None,
        "header": {},
        "path": {},
        "query": {
            "latest": {
                "default": True,
                "nullable": False,
                "required": False,
                "type": "boolean",
            },
            "page": {
                "default": 1,
                "minimum": 1,
                "nullable": False,
                "required": False,
                "type": "integer",
            },
            "size": {
                "default": 20,
                "maximum": 100,
                "minimum": 1,
                "nullable": False,
                "required": False,
                "type": "integer",
            },
        },
    }:
        raise ContractValidationError("팀 release evaluation request 불일치")
    if evaluation["sort"] != ["executed_at DESC", "run_id DESC"]:
        raise ContractValidationError("팀 release evaluation sort 불일치")


def normalize_openapi_contract(openapi: Mapping[str, Any]) -> NormalizedContract:
    """Return deterministic semantic operations from an OpenAPI document.

    Descriptive metadata (title, description, examples, operationId) is ignored.
    Array order remains meaningful; enum and required sets are sorted.
    """

    paths = openapi.get("paths")
    if not isinstance(paths, Mapping):
        raise ContractValidationError("OpenAPI paths가 없습니다")
    normalized: NormalizedContract = {}
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, Mapping):
            raise ContractValidationError(
                f"OpenAPI path item이 object가 아닙니다: {path}"
            )
        inherited = path_item.get("parameters", [])
        for raw_method, operation in path_item.items():
            method = raw_method.upper()
            if method not in _HTTP_METHODS:
                continue
            if not isinstance(operation, Mapping):
                raise ContractValidationError(
                    f"OpenAPI operation이 object가 아닙니다: {method} {path}"
                )
            parameters = list(inherited) + list(operation.get("parameters", []))
            request = {"path": {}, "query": {}, "header": {}, "body": None}
            security_headers: list[str] = []
            for parameter in parameters:
                if not isinstance(parameter, Mapping) or "$ref" in parameter:
                    raise ContractValidationError(
                        "parameter $ref/비object는 아직 허용하지 않습니다"
                    )
                location = parameter.get("in")
                name = parameter.get("name")
                if location not in {"path", "query", "header"} or not isinstance(
                    name, str
                ):
                    raise ContractValidationError(
                        "OpenAPI parameter 위치/이름이 올바르지 않습니다"
                    )
                request[location][name] = _normalize_openapi_field(
                    parameter.get("schema", {}),
                    required=bool(parameter.get("required")),
                )
                if location == "header":
                    security_headers.append(name)

            request_body = operation.get("requestBody")
            if request_body is not None:
                if not isinstance(request_body, Mapping):
                    raise ContractValidationError("requestBody가 object가 아닙니다")
                schema = _json_content_schema(request_body)
                request["body"] = _normalize_body_ref(schema)

            raw_responses = operation.get("responses")
            if not isinstance(raw_responses, Mapping) or not raw_responses:
                raise ContractValidationError(
                    f"OpenAPI response가 없습니다: {method} {path}"
                )
            responses: dict[str, dict[str, str]] = {}
            for status, response in sorted(
                raw_responses.items(), key=lambda item: str(item[0])
            ):
                if not re.fullmatch(r"[1-5][0-9]{2}", str(status)):
                    continue
                if not isinstance(response, Mapping):
                    raise ContractValidationError(
                        "OpenAPI response item이 object가 아닙니다"
                    )
                schema = _json_content_schema(response)
                shape, schema_ref = _response_shape_and_ref(schema)
                responses[str(status)] = {"shape": shape, "schema_ref": schema_ref}

            normalized[(method, path)] = {
                "request": request,
                "responses": responses,
                "security_headers": sorted(set(security_headers)),
            }
    return dict(sorted(normalized.items()))


def _json_content_schema(value: Mapping[str, Any]) -> Mapping[str, Any]:
    content = value.get("content")
    if not isinstance(content, Mapping):
        raise ContractValidationError("application/json content가 없습니다")
    media = content.get("application/json")
    if not isinstance(media, Mapping) or not isinstance(media.get("schema"), Mapping):
        raise ContractValidationError("application/json schema가 없습니다")
    return media["schema"]


def _ref_name(ref: Any) -> str:
    prefix = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefix) or not ref[len(prefix) :]:
        raise ContractValidationError(f"지원하지 않는 OpenAPI ref입니다: {ref!r}")
    return ref[len(prefix) :]


def _nullable_schema(schema: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    if schema.get("nullable") is True:
        return schema, True
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        non_null = [
            item
            for item in any_of
            if isinstance(item, Mapping) and item.get("type") != "null"
        ]
        null_count = sum(
            1
            for item in any_of
            if isinstance(item, Mapping) and item.get("type") == "null"
        )
        if len(non_null) == 1 and null_count == 1:
            return non_null[0], True
    return schema, False


def _normalize_openapi_field(
    schema: Mapping[str, Any], *, required: bool
) -> dict[str, Any]:
    schema, nullable = _nullable_schema(schema)
    if "$ref" in schema:
        return {
            "ref": _ref_name(schema["$ref"]),
            "required": required,
            "nullable": nullable,
        }
    field_type = schema.get("type", "any")
    result: dict[str, Any] = {
        "type": field_type,
        "required": required,
        "nullable": nullable,
    }
    key_map = {
        "default": "default",
        "enum": "enum",
        "format": "format",
        "maxItems": "max_items",
        "maxLength": "max_length",
        "maximum": "maximum",
        "minItems": "min_items",
        "minLength": "min_length",
        "minimum": "minimum",
        "pattern": "pattern",
    }
    for source_key, target_key in key_map.items():
        if source_key in schema:
            value = schema[source_key]
            result[target_key] = sorted(value) if source_key == "enum" else value
    if field_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ContractValidationError("OpenAPI array items가 없습니다")
        result["items"] = (
            {"ref": _ref_name(items["$ref"])}
            if "$ref" in items
            else {"type": items.get("type", "any")}
        )
    if field_type == "object":
        result["additional_properties"] = (
            schema.get("additionalProperties", True) is not False
        )
    return result


def _normalize_body_ref(schema: Mapping[str, Any]) -> dict[str, str]:
    schema, _ = _nullable_schema(schema)
    if "$ref" not in schema:
        raise ContractValidationError("request body는 named component ref여야 합니다")
    return {"ref": _ref_name(schema["$ref"])}


def _response_shape_and_ref(schema: Mapping[str, Any]) -> tuple[str, str]:
    schema, _ = _nullable_schema(schema)
    if schema.get("type") == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping) or "$ref" not in items:
            raise ContractValidationError(
                "array response item은 named component ref여야 합니다"
            )
        return "array", _ref_name(items["$ref"])
    if "$ref" not in schema:
        raise ContractValidationError(
            "object response는 named component ref여야 합니다"
        )
    return "object", _ref_name(schema["$ref"])
