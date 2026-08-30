"""V5-CM-4.4 implementation-independent API contract baseline."""

from __future__ import annotations

import ast
import copy
import csv
import importlib
import inspect
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.support.api_contract_baseline import (
    ContractValidationError,
    load_optional_contract,
    load_required_contract,
    load_team_release_contract,
    normalize_openapi_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FIXTURE_ROOT = BACKEND_ROOT / "tests" / "fixtures" / "v5_cm_4_4"
REQUIRED_FIXTURE = FIXTURE_ROOT / "api_contract_baseline.json"
OPTIONAL_FIXTURE = FIXTURE_ROOT / "api_contract_optional.json"
TEAM_RELEASE_FIXTURE = FIXTURE_ROOT / "api_contract_team_release.json"
SUPPORT_MODULE = BACKEND_ROOT / "tests" / "support" / "api_contract_baseline.py"
API_MARKDOWN = (
    REPOSITORY_ROOT / "docs" / "deliverables" / "api" / "API명세서_v3_작업본.md"
)
API_CSV = REPOSITORY_ROOT / "docs" / "deliverables" / "api" / "API명세서_v3_작업본.csv"
APP_ROOT = BACKEND_ROOT / "app"

REQUIRED_KEYS = {
    ("GET", "/alarms"),
    ("GET", "/trace"),
    ("GET", "/parameters"),
    ("POST", "/documents/search"),
    ("GET", "/relations/chambers/{chamber_id}"),
    ("GET", "/agent/runs"),
    ("POST", "/agent/ask"),
    ("GET", "/approvals"),
    ("POST", "/approvals/{approval_id}/decision"),
    ("POST", "/agent/runs"),
    ("POST", "/internal/actions/{action_id}/delivery"),
    ("GET", "/audit-logs"),
    ("GET", "/health"),
    ("GET", "/health/ready"),
}
BARE_ARRAY_KEYS = {
    ("GET", "/alarms"),
    ("GET", "/trace"),
    ("GET", "/parameters"),
    ("POST", "/documents/search"),
    ("GET", "/agent/runs"),
    ("GET", "/approvals"),
    ("GET", "/audit-logs"),
}
DEFERRED_AGENT_DETAIL_KEYS = {
    ("GET", "/agent/runs/{run_id}"),
    ("GET", "/actions"),
    ("GET", "/actions/{action_id}"),
}
TEAM_RELEASE_KEYS = {
    ("POST", "/analytics/query"),
    ("POST", "/analytics/validate"),
    ("GET", "/analytics/history"),
    ("GET", "/analytics/evaluations"),
    ("GET", "/audit-logs/paged"),
}


def _operation_map(fixture: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["method"], item["path"]): item for item in fixture["operations"]}


def _write_fixture(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "mutated.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _section(text: str, heading: str) -> str:
    if text.count(heading) != 1:
        raise AssertionError(f"heading은 정확히 한 번이어야 합니다: {heading}")
    start = text.index(heading) + len(heading)
    level = len(heading) - len(heading.lstrip("#"))
    tail = text[start:]
    boundary = re.search(rf"^#{{1,{level}}}\s", tail, re.MULTILINE)
    return tail[: boundary.start()] if boundary else tail


def _markdown_table(text: str, heading: str, header: str) -> list[list[str]]:
    section = _section(text, heading)
    if section.count(header) != 1:
        raise AssertionError(f"table header는 정확히 한 번이어야 합니다: {header}")
    lines = section[section.index(header) :].splitlines()
    if len(lines) < 3 or not re.fullmatch(r"\|(?:[-:]+\|)+", lines[1].replace(" ", "")):
        raise AssertionError(f"Markdown separator가 없습니다: {heading}")
    rows: list[list[str]] = []
    for line in lines[2:]:
        if not line.startswith("|"):
            break
        cells = [
            cell.strip().replace(r"\|", "|")
            for cell in re.split(r"(?<!\\)\|", line.strip("|"))
        ]
        rows.append(cells)
    if not rows:
        raise AssertionError(f"Markdown table이 비어 있습니다: {heading}")
    return rows


def _markdown_required_keys(text: str) -> set[tuple[str, str]]:
    compatibility = _markdown_table(
        text,
        "## 3. 필수 호환 API — 9개",
        "| 담당 | Method | Path | 용도 | 성공 응답 |",
    )
    internal = _markdown_table(
        text,
        "### 5.1 필수 내부·운영 API",
        "| 담당 | Method | Path | 용도 |",
    )
    required = {(row[1], row[2].strip("`")) for row in compatibility}
    required.update((row[1], row[2].strip("`")) for row in internal)
    headings = {
        "### 4.1 `GET /relations/chambers/{chamber_id}`": (
            "GET",
            "/relations/chambers/{chamber_id}",
        ),
        "### 4.2 `POST /agent/runs` — 프로젝트 필수 실행 API": (
            "POST",
            "/agent/runs",
        ),
    }
    for heading, key in headings.items():
        if text.count(heading) != 1:
            raise AssertionError(f"필수 endpoint heading 불일치: {heading}")
        required.add(key)
    return required


def _markdown_optional_keys(text: str) -> set[tuple[str, str]]:
    rows = _markdown_table(
        text,
        "### 5.2 선택 확장 API",
        "| 담당 | Method | Path | 용도 | 성공 응답 | 기타 상태 |",
    )
    return {(row[1], row[2].strip("`")) for row in rows}


def _markdown_team_release_keys(text: str) -> set[tuple[str, str]]:
    rows = _markdown_table(
        text,
        "### 5.3 팀 release 필수 확장 API",
        "| 담당 | Method | Path | 용도 | 성공 응답 | 기타 상태 |",
    )
    return {(row[1], row[2].strip("`")) for row in rows}


def _csv_rows() -> list[dict[str, str]]:
    with API_CSV.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or set(rows[0]) != {
        "구분",
        "담당",
        "Method",
        "Path",
        "요약",
        "요청",
        "성공 응답",
        "기타 상태",
        "정렬·제약",
        "호환·경계",
    }:
        raise AssertionError("API CSV exact header가 다릅니다")
    return rows


def _route_inventory_from_source(
    source: str, filename: str = "<router>"
) -> set[tuple[str, str]]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise AssertionError(f"router scan parse 실패: {filename}") from exc
    routes: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            method = decorator.func.attr.upper()
            owner = decorator.func.value
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not isinstance(owner, ast.Name) or owner.id not in {"app", "router"}:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                raise AssertionError(
                    f"dynamic route path는 허용하지 않습니다: {filename}:{node.lineno}"
                )
            path = decorator.args[0].value
            if not isinstance(path, str) or not path.startswith("/"):
                raise AssertionError(
                    f"literal route path가 올바르지 않습니다: {filename}:{node.lineno}"
                )
            key = (method, path)
            if key in routes:
                raise AssertionError(f"source file 내부 중복 route: {key}")
            routes.add(key)
    return routes


def _router_inventory() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        discovered = _route_inventory_from_source(
            path.read_text(encoding="utf-8"), path.as_posix()
        )
        duplicate = routes & discovered
        if duplicate:
            raise AssertionError(f"Backend 동일 Method+Path 중복: {sorted(duplicate)}")
        routes.update(discovered)
    return routes


def _assert_optional_exact_set(fixture: dict[str, Any]) -> None:
    allowlist = _markdown_optional_keys(API_MARKDOWN.read_text(encoding="utf-8"))
    expected = allowlist & _router_inventory()
    actual = set(_operation_map(fixture))
    assert actual == expected


def test_public_support_api_is_typed_and_import_side_effect_free() -> None:
    module = importlib.import_module("tests.support.api_contract_baseline")
    assert module.__all__ == [
        "ContractFixture",
        "ContractValidationError",
        "NormalizedContract",
        "load_optional_contract",
        "load_required_contract",
        "load_team_release_contract",
        "normalize_openapi_contract",
    ]
    for function_name in (
        "load_required_contract",
        "load_optional_contract",
        "load_team_release_contract",
        "normalize_openapi_contract",
    ):
        signature = inspect.signature(getattr(module, function_name))
        assert signature.return_annotation is not inspect.Signature.empty
        assert all(
            parameter.annotation is not inspect.Signature.empty
            for parameter in signature.parameters.values()
        )

    tree = ast.parse(SUPPORT_MODULE.read_text(encoding="utf-8"))
    import_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".")[0])
    assert import_roots <= {
        "__future__",
        "collections",
        "json",
        "pathlib",
        "re",
        "typing",
    }
    assert {"app", "fastapi", "sqlalchemy"}.isdisjoint(import_roots)


def test_fixtures_are_canonical_utf8_sorted_json() -> None:
    for path in (REQUIRED_FIXTURE, OPTIONAL_FIXTURE, TEAM_RELEASE_FIXTURE):
        raw = path.read_text(encoding="utf-8")
        assert (
            raw
            == json.dumps(json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )


def test_required_fixture_matches_markdown_and_csv_inventory() -> None:
    fixture = load_required_contract()
    operations = _operation_map(fixture)
    markdown = API_MARKDOWN.read_text(encoding="utf-8")
    assert set(operations) == REQUIRED_KEYS == _markdown_required_keys(markdown)

    rows = _csv_rows()
    assert len(rows) == 34
    required_rows = [
        row
        for row in rows
        if row["구분"] in {"필수", "보안필수", "실행필수", "내부", "운영"}
    ]
    assert len(required_rows) == 14
    assert {(row["Method"], row["Path"]) for row in required_rows} == REQUIRED_KEYS
    for row in required_rows:
        key = row["Method"], row["Path"]
        operation = operations[key]
        assert operation["owner"] == row["담당"]
        success = 202 if row["성공 응답"].startswith("202 ") else 200
        other_statuses = {
            int(value.strip()) for value in row["기타 상태"].split(",") if value.strip()
        }
        assert operation["success_status"] == success
        assert {int(status) for status in operation["responses"]} == {
            success,
            *other_statuses,
        }


def test_required_shape_error_and_semantic_contracts() -> None:
    fixture = load_required_contract()
    operations = _operation_map(fixture)
    assert {
        key
        for key, operation in operations.items()
        if operation["response_shape"]["type"] == "array"
    } == BARE_ARRAY_KEYS
    assert operations[("POST", "/agent/runs")]["success_status"] == 202
    assert operations[("GET", "/health/ready")]["responses"]["503"] == {
        "shape": "object",
        "schema_ref": "ReadinessResponse",
    }
    for key, operation in operations.items():
        for status, response in operation["responses"].items():
            if int(status) == operation["success_status"]:
                continue
            if key == ("GET", "/health/ready") and status == "503":
                continue
            assert response == {"shape": "object", "schema_ref": "ErrorResponse"}

    components = fixture["components"]
    ask = components["AgentAskResponse"]["fields"]
    assert all(
        ask[name]["required"] and ask[name]["nullable"]
        for name in ("predicted_fault_code", "confidence", "recommended_action")
    )
    assert "fault_code" not in ask
    assert components["DocumentEvidence"]["fields"]["section"] == {
        "type": "string",
        "required": True,
        "nullable": True,
    }
    assert {"relation_id", "graph_revision"} <= set(
        components["GraphEvidence"]["fields"]
    )
    assert "alarm_result" not in components["MetrologyEvidence"]["fields"]

    run_fields = components["AgentRunItem"]["fields"]
    assert run_fields["latency_ms"] == {
        "minimum": 0,
        "nullable": False,
        "required": True,
        "type": "integer",
    }
    assert run_fields["llm_model"] == {
        "min_length": 1,
        "nullable": False,
        "required": True,
        "type": "string",
    }
    for name in ("fault_name", "fault_color"):
        assert run_fields[name] == {
            "nullable": False,
            "required": True,
            "type": "null",
        }

    for component_name, field_names in {
        "AutoToolCallItem": ("result_summary",),
        "ChatToolCallItem": ("result_summary", "result"),
    }.items():
        fields = components[component_name]["fields"]
        for name in field_names:
            assert fields[name] == {
                "min_length": 1,
                "nullable": False,
                "required": True,
                "type": "string",
            }

    approval_fields = components["ApprovalItem"]["fields"]
    assert all(
        approval_fields[name]["required"] and not approval_fields[name]["nullable"]
        for name in ("predicted_fault_code", "fault_code")
    )
    assert approval_fields["action_code"]["enum"] == [
        "MONITORING",
        "WARNING",
        "EQP_HOLD",
    ]


def test_markdown_alias_rows_are_all_owned_by_fixture() -> None:
    rows = _markdown_table(
        API_MARKDOWN.read_text(encoding="utf-8"),
        "### 2.7 최종 참고 React 호환 projection",
        "| 응답 | canonical | deprecated alias | 파생 규칙 |",
    )
    documented_aliases = Counter(
        alias for row in rows for alias in re.findall(r"`([^`]+)`", row[2])
    )
    unique_contract_rows = {
        (
            item["component"],
            tuple(item["aliases"]),
            tuple(item["canonical"]),
            item["rule"],
        )
        for operation in load_required_contract()["operations"]
        for item in operation["compatibility"]
    }
    fixture_aliases = Counter(
        alias for _, aliases, _, _ in unique_contract_rows for alias in aliases
    )
    assert fixture_aliases == documented_aliases


def test_optional_fixture_is_exact_implemented_allowlist() -> None:
    fixture = load_optional_contract()
    _assert_optional_exact_set(fixture)
    assert set(_operation_map(fixture)) == {
        ("GET", "/documents/{document_id}"),
        ("GET", "/agent/runs/{run_id}"),
        ("GET", "/actions"),
        ("GET", "/actions/{action_id}"),
        ("GET", "/audit-logs/paged"),
        ("POST", "/analytics/query"),
        ("POST", "/analytics/validate"),
        ("GET", "/analytics/history"),
    }


def test_team_release_fixture_is_exact_documented_inventory() -> None:
    fixture = load_team_release_contract()
    actual = set(_operation_map(fixture))
    markdown = API_MARKDOWN.read_text(encoding="utf-8")
    assert actual == TEAM_RELEASE_KEYS == _markdown_team_release_keys(markdown)
    csv_keys = {
        (row["Method"], row["Path"]) for row in _csv_rows() if row["구분"] == "팀필수"
    }
    assert csv_keys == TEAM_RELEASE_KEYS


def test_team_release_matches_implemented_optional_operation_contracts() -> None:
    optional = _operation_map(load_optional_contract())
    team_release = _operation_map(load_team_release_contract())
    common = set(optional) & set(team_release)
    assert common == TEAM_RELEASE_KEYS - {("GET", "/analytics/evaluations")}
    for key in common:
        assert team_release[key]["request"] == optional[key]["request"]
        assert team_release[key]["response_shape"] == optional[key]["response_shape"]
        assert team_release[key]["responses"] == optional[key]["responses"]
        assert team_release[key]["success_status"] == optional[key]["success_status"]


def test_agent_detail_extensions_are_promoted_when_routes_exist() -> None:
    optional_keys = set(_operation_map(load_optional_contract()))
    allowlist = _markdown_optional_keys(API_MARKDOWN.read_text(encoding="utf-8"))
    routes = _router_inventory()
    assert DEFERRED_AGENT_DETAIL_KEYS <= allowlist
    assert DEFERRED_AGENT_DETAIL_KEYS <= routes
    assert DEFERRED_AGENT_DETAIL_KEYS <= optional_keys


def test_required_fixture_does_not_require_current_router_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CM-4.4 freezes expectations; CM-5.1 owns actual endpoint PASS.  Prove the
    # required loader remains usable even when source inventory is unavailable,
    # instead of encoding today's temporary set of missing routes.
    def reject_router_scan(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("required baseline must not scan application routers")

    monkeypatch.setattr(Path, "rglob", reject_router_scan)
    assert set(_operation_map(load_required_contract())) == REQUIRED_KEYS


def test_common_components_are_self_contained_and_equal() -> None:
    required = load_required_contract()["components"]
    optional = load_optional_contract()["components"]
    common = set(required) & set(optional)
    assert common == {"ErrorResponse"}
    assert all(required[name] == optional[name] for name in common)

    team_release = load_team_release_contract()["components"]
    for baseline in (required, optional):
        shared = set(baseline) & set(team_release)
        assert all(baseline[name] == team_release[name] for name in shared)


def test_optional_loader_rejects_common_component_drift(tmp_path: Path) -> None:
    optional = copy.deepcopy(load_optional_contract())
    optional["components"]["ErrorResponse"]["fields"]["message"]["nullable"] = True
    with pytest.raises(ContractValidationError, match="공통 component 불일치"):
        load_optional_contract(_write_fixture(tmp_path, optional))


def test_team_release_loader_rejects_component_drift(tmp_path: Path) -> None:
    fixture = copy.deepcopy(load_team_release_contract())
    fixture["components"]["ErrorResponse"]["fields"]["message"]["nullable"] = True
    with pytest.raises(ContractValidationError, match="공통 component 불일치"):
        load_team_release_contract(_write_fixture(tmp_path, fixture))


@pytest.mark.parametrize(
    ("loader", "fixture_path"),
    [
        (load_required_contract, REQUIRED_FIXTURE),
        (load_optional_contract, OPTIONAL_FIXTURE),
        (load_team_release_contract, TEAM_RELEASE_FIXTURE),
    ],
)
def test_both_loaders_share_strict_envelope_validation(
    tmp_path: Path,
    loader: Any,
    fixture_path: Path,
) -> None:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    with pytest.raises(ContractValidationError, match="root key 불일치"):
        loader(_write_fixture(tmp_path, payload))


def test_router_scanner_fails_closed() -> None:
    with pytest.raises(AssertionError, match="dynamic route path"):
        _route_inventory_from_source(
            "from fastapi import APIRouter\n"
            "router=APIRouter()\n"
            "p='/x'\n"
            "@router.get(p)\n"
            "def f(): pass\n"
        )
    with pytest.raises(AssertionError, match="parse 실패"):
        _route_inventory_from_source("def broken(: pass")


def test_markdown_parser_fails_closed_on_missing_or_duplicate_table() -> None:
    markdown = API_MARKDOWN.read_text(encoding="utf-8")
    heading = "### 5.2 선택 확장 API"
    header = "| 담당 | Method | Path | 용도 | 성공 응답 | 기타 상태 |"
    with pytest.raises(AssertionError, match="table header"):
        _markdown_table(markdown.replace(header, "| broken |", 1), heading, header)
    section = _section(markdown, heading)
    duplicated = markdown.replace(
        section, section + "\n" + header + "\n|---|\n|x|\n", 1
    )
    with pytest.raises(AssertionError, match="table header"):
        _markdown_table(duplicated, heading, header)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("remove-operation", "필수 operation 집합"),
        ("duplicate-operation", "중복 Method\\+Path"),
        ("array-to-object", "response shape"),
        ("accepted-202-to-200", "success response|owner/status"),
        ("required-nullable-to-optional", "required-nullable"),
        ("readiness-error-schema", "오류 schema"),
        ("alias-drift", "alias/canonical field"),
        ("public-projection-drift", "공개 projection field"),
        ("implementation-source-link", "source.api_markdown"),
    ],
)
def test_required_contract_mutations_fail_closed(
    tmp_path: Path, mutation: str, message: str
) -> None:
    fixture = copy.deepcopy(load_required_contract())
    operations = _operation_map(fixture)
    if mutation == "remove-operation":
        fixture["operations"].pop()
    elif mutation == "duplicate-operation":
        fixture["operations"].append(copy.deepcopy(fixture["operations"][0]))
    elif mutation == "array-to-object":
        operation = operations[("GET", "/alarms")]
        operation["responses"]["200"]["shape"] = "object"
        operation["response_shape"] = {"type": "object", "schema_ref": "AlarmItem"}
    elif mutation == "accepted-202-to-200":
        operation = operations[("POST", "/agent/runs")]
        operation["success_status"] = 200
    elif mutation == "required-nullable-to-optional":
        fixture["components"]["AgentAskResponse"]["fields"]["predicted_fault_code"][
            "required"
        ] = False
    elif mutation == "readiness-error-schema":
        operations[("GET", "/health/ready")]["responses"]["503"]["schema_ref"] = (
            "ErrorResponse"
        )
    elif mutation == "alias-drift":
        operations[("GET", "/alarms")]["compatibility"][0]["aliases"][0] = (
            "equipment_legacy"
        )
    elif mutation == "public-projection-drift":
        fixture["components"]["AgentRunItem"]["fields"]["latency_ms"]["nullable"] = True
    elif mutation == "implementation-source-link":
        fixture["source"]["api_markdown"]["path"] = "backend/app/openapi.json"
    with pytest.raises(ContractValidationError, match=message):
        load_required_contract(_write_fixture(tmp_path, fixture))


def test_optional_missing_and_unknown_mutations_break_exact_set() -> None:
    fixture = copy.deepcopy(load_optional_contract())
    fixture["operations"].pop()
    with pytest.raises(AssertionError):
        _assert_optional_exact_set(fixture)

    fixture = copy.deepcopy(load_optional_contract())
    fake = copy.deepcopy(fixture["operations"][0])
    fake["method"] = "GET"
    fake["path"] = "/agent/runs/{run_id}/retry"
    fixture["operations"].append(fake)
    with pytest.raises(AssertionError):
        _assert_optional_exact_set(fixture)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("remove-operation", "팀 release operation 집합"),
        ("weaken-field", "evaluation request 불일치"),
        ("delete-evaluation-component", "responses.200 ref가 없습니다"),
    ],
)
def test_team_release_mutations_fail_closed(
    tmp_path: Path, mutation: str, message: str
) -> None:
    fixture = copy.deepcopy(load_team_release_contract())
    if mutation == "remove-operation":
        fixture["operations"].pop()
    elif mutation == "weaken-field":
        operation = _operation_map(fixture)[("GET", "/analytics/evaluations")]
        operation["request"]["query"]["latest"]["default"] = False
    elif mutation == "delete-evaluation-component":
        del fixture["components"]["EvaluationListResponse"]
    with pytest.raises(ContractValidationError, match=message):
        load_team_release_contract(_write_fixture(tmp_path, fixture))


def test_baseline_has_no_implementation_openapi_generation_link() -> None:
    support_source = SUPPORT_MODULE.read_text(encoding="utf-8")
    test_source = Path(__file__).read_text(encoding="utf-8")
    fixture_text = REQUIRED_FIXTURE.read_text(encoding="utf-8")
    forbidden = ("app.openapi()", "from app", "import app")
    assert all(token not in support_source for token in forbidden)
    assert all(token not in fixture_text for token in forbidden)
    assert "normalize_openapi_contract" in test_source  # comparison seam is intentional


def test_openapi_normalizer_has_stable_semantic_output() -> None:
    openapi = {
        "info": {"title": "ignored", "version": "1"},
        "paths": {
            "/agent/runs": {
                "get": {
                    "operationId": "ignored",
                    "description": "ignored",
                    "parameters": [
                        {
                            "name": "date_from",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "format": "date"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ignored",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/AgentRunItem"
                                        },
                                    }
                                }
                            },
                        },
                        "503": {
                            "description": "ignored",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/internal/actions/{action_id}/delivery": {
                "post": {
                    "parameters": [
                        {
                            "name": "action_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        },
                        {
                            "name": "X-Delivery-Signature",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^sha256="},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": (
                                        "#/components/schemas/DeliveryCallbackRequest"
                                    )
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "ignored",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/DeliveryResult"
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }
    normalized = normalize_openapi_contract(openapi)
    assert list(normalized) == [
        ("GET", "/agent/runs"),
        ("POST", "/internal/actions/{action_id}/delivery"),
    ]
    assert normalized[("GET", "/agent/runs")] == {
        "request": {
            "path": {},
            "query": {
                "date_from": {
                    "type": "string",
                    "required": False,
                    "nullable": False,
                    "format": "date",
                }
            },
            "header": {},
            "body": None,
        },
        "responses": {
            "200": {"shape": "array", "schema_ref": "AgentRunItem"},
            "503": {"shape": "object", "schema_ref": "ErrorResponse"},
        },
        "security_headers": [],
    }
    callback = normalized[("POST", "/internal/actions/{action_id}/delivery")]
    assert callback["request"]["body"] == {"ref": "DeliveryCallbackRequest"}
    assert callback["security_headers"] == ["X-Delivery-Signature"]
