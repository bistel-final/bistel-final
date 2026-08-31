"""Generate API artifacts from the checked-in full-schema canonical model.

Runtime OpenAPI is comparison input for a separate sync gate.  This generator
intentionally does not import ``app``, Pydantic, or FastAPI.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parent
CANONICAL_PATH = API_ROOT / "api_spec_v3.json"
SUMMARY_CSV_PATH = API_ROOT / "API명세서_v3_작업본.csv"
CSV_PATH = API_ROOT / "API명세서.csv"
MARKDOWN_PATH = API_ROOT / "API명세서.md"
PDF_PATH = API_ROOT / "API명세서.pdf"

SUMMARY_HEADERS = [
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
]
GENERATED_HEADERS = [*SUMMARY_HEADERS, "Operation Rules", "Semantic JSON Schema"]
ROOT_KEYS = {
    "audit_events",
    "common_rules",
    "components",
    "error_codes",
    "format_version",
    "metadata",
    "operations",
}
OPERATION_KEYS = {
    "catalog",
    "contract_level",
    "implemented",
    "method",
    "order",
    "path",
    "release_required",
    "rules",
    "semantic",
}
CATALOG_KEYS = {
    "category",
    "constraints",
    "description",
    "notes",
    "other_statuses",
    "owner",
    "request_summary",
    "response_summary",
}


class ApiSpecError(ValueError):
    """The canonical model or one of its projections is invalid."""


def _exact_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    if set(value) != expected:
        raise ApiSpecError(
            f"{where} key mismatch: missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )


def load_spec(path: Path = CANONICAL_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApiSpecError(f"canonical model cannot be read: {path}") from exc
    if not isinstance(value, dict):
        raise ApiSpecError("canonical root must be an object")
    validate_spec(value)
    return value


def validate_spec(spec: Mapping[str, Any]) -> None:
    _exact_keys(spec, ROOT_KEYS, "root")
    if spec["format_version"] != 1:
        raise ApiSpecError("format_version must be 1")
    metadata = spec["metadata"]
    operations = spec["operations"]
    if not isinstance(metadata, Mapping):
        raise ApiSpecError("metadata must be an object")
    if not isinstance(operations, list) or len(operations) != 35:
        raise ApiSpecError("operations must contain exactly 35 entries")

    keys: set[tuple[str, str]] = set()
    orders: list[int] = []
    semantic_count = 0
    release_count = 0
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise ApiSpecError(f"operations[{index}] must be an object")
        _exact_keys(operation, OPERATION_KEYS, f"operations[{index}]")
        catalog = operation["catalog"]
        if not isinstance(catalog, Mapping):
            raise ApiSpecError(f"operations[{index}].catalog must be an object")
        _exact_keys(catalog, CATALOG_KEYS, f"operations[{index}].catalog")
        method, path = operation["method"], operation["path"]
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ApiSpecError(f"unsupported method: {method!r}")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ApiSpecError(f"invalid path: {path!r}")
        key = method, path
        if key in keys:
            raise ApiSpecError(f"duplicate operation: {method} {path}")
        keys.add(key)
        orders.append(operation["order"])
        rules = operation["rules"]
        if (
            not isinstance(rules, list)
            or any(not isinstance(rule, str) or not rule for rule in rules)
            or len(rules) != len(set(rules))
            or rules != sorted(rules)
        ):
            raise ApiSpecError(
                f"operation rules must be a sorted unique string list: {method} {path}"
            )
        level, semantic = operation["contract_level"], operation["semantic"]
        if level == "semantic":
            if not isinstance(semantic, Mapping):
                raise ApiSpecError(f"semantic operation has no schema: {method} {path}")
            _reject_semantic_rules(semantic, f"{method} {path}")
            _validate_semantic_operation(semantic, f"{method} {path}")
            semantic_count += 1
        elif level != "inventory" or semantic is not None:
            raise ApiSpecError(f"invalid contract level: {method} {path}")
        release_count += int(operation["release_required"] is True)
    if orders != list(range(1, 36)):
        raise ApiSpecError("operation order must be the exact sequence 1..35")
    if (metadata.get("operation_count"), metadata.get("semantic_operation_count")) != (
        35,
        semantic_count,
    ):
        raise ApiSpecError("metadata operation counts drift")
    if metadata.get("release_required_count") != release_count or release_count != 19:
        raise ApiSpecError("release-required operation count must be 19")

    components = spec["components"]
    if not isinstance(components, Mapping) or not components:
        raise ApiSpecError("components must be a non-empty object")
    for name, schema in components.items():
        if not isinstance(name, str) or not name or not isinstance(schema, Mapping):
            raise ApiSpecError("component entries must be named schemas")
        _validate_schema(schema, f"components.{name}")
    for list_name in ("common_rules", "error_codes", "audit_events"):
        if not isinstance(spec[list_name], list) or not spec[list_name]:
            raise ApiSpecError(f"{list_name} must be a non-empty list")


def _validate_semantic_operation(value: Mapping[str, Any], where: str) -> None:
    _exact_keys(value, {"request", "responses"}, where)
    request = value["request"]
    if not isinstance(request, Mapping):
        raise ApiSpecError(f"{where}.request must be an object")
    _exact_keys(request, {"body", "header", "path", "query"}, f"{where}.request")
    for location in ("header", "path", "query"):
        fields = request[location]
        if not isinstance(fields, Mapping):
            raise ApiSpecError(f"{where}.request.{location} must be an object")
        for name, field in fields.items():
            _validate_field(field, f"{where}.request.{location}.{name}")
    if request["body"] is not None:
        _validate_schema(request["body"], f"{where}.request.body")
    responses = value["responses"]
    if not isinstance(responses, Mapping) or not responses:
        raise ApiSpecError(f"{where}.responses must be non-empty")
    for status, response in responses.items():
        if not str(status).isdigit() or not isinstance(response, Mapping):
            raise ApiSpecError(f"{where}.responses has an invalid status")
        _exact_keys(response, {"schema", "shape"}, f"{where}.responses.{status}")
        if response["shape"] not in {"array", "object"}:
            raise ApiSpecError(f"{where}.responses.{status}.shape is invalid")
        _validate_schema(response["schema"], f"{where}.responses.{status}.schema")


def _validate_field(value: Any, where: str) -> None:
    if not isinstance(value, Mapping):
        raise ApiSpecError(f"{where} must be an object")
    _exact_keys(value, {"nullable", "required", "schema"}, where)
    if not isinstance(value["nullable"], bool) or not isinstance(
        value["required"], bool
    ):
        raise ApiSpecError(f"{where} nullable/required must be boolean")
    _validate_schema(value["schema"], f"{where}.schema")


def _reject_semantic_rules(value: Any, where: str) -> None:
    """Keep documentation-only rules outside the live-comparable semantic tree."""

    if isinstance(value, Mapping):
        if "rules" in value:
            raise ApiSpecError(f"{where}.rules must be operation-level metadata")
        for key, child in value.items():
            _reject_semantic_rules(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_semantic_rules(child, f"{where}[{index}]")


def _validate_schema(value: Any, where: str) -> None:
    if isinstance(value, Mapping) and set(value) == {"nullable", "schema"}:
        if value["nullable"] is not True:
            raise ApiSpecError(f"{where}.nullable wrapper must be true")
        _validate_schema(value["schema"], f"{where}.schema")
        return
    if not isinstance(value, Mapping) or not isinstance(value.get("type"), str):
        raise ApiSpecError(f"{where} must contain a schema type")
    enum = value.get("enum")
    if enum is not None and (
        not isinstance(enum, list)
        or not enum
        or any(not isinstance(item, str) or not item for item in enum)
        or len(enum) != len(set(enum))
        or enum != sorted(enum)
    ):
        raise ApiSpecError(f"{where}.enum must be a sorted unique string list")
    schema_type = value["type"]
    if schema_type == "object":
        fields = value.get("fields")
        if not isinstance(fields, Mapping):
            raise ApiSpecError(f"{where}.fields must be an object")
        for name, field in fields.items():
            _validate_field(field, f"{where}.fields.{name}")
    elif schema_type == "array":
        _validate_schema(value.get("items"), f"{where}.items")
    elif schema_type in {"union", "discriminated_union"}:
        variants = value.get("variants")
        sequence: Sequence[Any]
        if isinstance(variants, Mapping):
            sequence = list(variants.values())
        elif isinstance(variants, list):
            sequence = variants
        else:
            raise ApiSpecError(f"{where}.variants must be an object or list")
        for index, variant in enumerate(sequence):
            _validate_schema(variant, f"{where}.variants[{index}]")


def summary_rows(spec: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for operation in spec["operations"]:
        catalog = operation["catalog"]
        rows.append(
            {
                "구분": catalog["category"],
                "담당": catalog["owner"],
                "Method": operation["method"],
                "Path": operation["path"],
                "요약": catalog["description"],
                "요청": catalog["request_summary"],
                "성공 응답": catalog["response_summary"],
                "기타 상태": catalog["other_statuses"],
                "정렬·제약": catalog["constraints"],
                "호환·경계": catalog["notes"],
            }
        )
    return rows


def _csv_text(headers: list[str], rows: list[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + stream.getvalue()


def validate_summary_projection(spec: Mapping[str, Any]) -> None:
    with SUMMARY_CSV_PATH.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != SUMMARY_HEADERS:
            raise ApiSpecError("summary CSV header drift")
        actual = list(reader)
    if actual != summary_rows(spec):
        raise ApiSpecError("summary CSV projection drift")


def render_csv(spec: Mapping[str, Any]) -> str:
    rows = []
    for operation, summary in zip(spec["operations"], summary_rows(spec), strict=True):
        rows.append(
            {
                **summary,
                "Operation Rules": json.dumps(
                    operation["rules"], ensure_ascii=False, sort_keys=True
                ),
                "Semantic JSON Schema": json.dumps(
                    operation["semantic"], ensure_ascii=False, sort_keys=True
                )
                if operation["semantic"] is not None
                else "",
            }
        )
    return _csv_text(GENERATED_HEADERS, rows)


def render_markdown(spec: Mapping[str, Any]) -> str:
    metadata = spec["metadata"]
    lines = [
        f"# {metadata['title']}",
        "",
        f"> 버전: {metadata['version']}",
        "> machine canonical: `api_spec_v3.json`",
        "> canonical JSON만으로 재생성하며 live OpenAPI로 빈 계약을 보충하지 않는다.",
        "",
        "## 1. 공통 규칙",
        "",
    ]
    lines.extend(f"- {rule}" for rule in spec["common_rules"])
    lines.extend(["", "## 2. 오류 응답", "", "| HTTP | 의미 |", "|---:|---|"])
    lines.extend(
        f"| {item['http']} | {item['meaning']} |" for item in spec["error_codes"]
    )
    lines.extend(
        [
            "",
            "## 3. API inventory — 35개",
            "",
            "| # | 구분 | 담당 | Method | Path | 요약 | 계약 |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for operation in spec["operations"]:
        catalog = operation["catalog"]
        lines.append(
            f"| {operation['order']} | {catalog['category']} | {catalog['owner']} | "
            f"{operation['method']} | `{operation['path']}` | {catalog['description']} | "
            f"{operation['contract_level']} |"
        )
    lines.extend(["", "## 4. Operation 상세", ""])
    for operation in spec["operations"]:
        catalog = operation["catalog"]
        lines.extend(
            [
                f"### 4.{operation['order']} `{operation['method']} {operation['path']}`",
                "",
                f"- 구분/담당: {catalog['category']} / {catalog['owner']}",
                f"- 요청: {catalog['request_summary']}",
                f"- 성공 응답: {catalog['response_summary']}",
                f"- 기타 상태: {catalog['other_statuses'] or '-'}",
                f"- 정렬·제약: {catalog['constraints'] or '-'}",
                f"- 호환·경계: {catalog['notes'] or '-'}",
                "- 계약 규칙:",
                *([f"  - {rule}" for rule in operation["rules"]] or ["  - 없음"]),
                "",
            ]
        )
        if operation["semantic"] is None:
            lines.append(
                "> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다."
            )
        else:
            lines.extend(
                [
                    "```json",
                    json.dumps(
                        operation["semantic"],
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    "```",
                ]
            )
        lines.append("")
    lines.extend(["## 5. DTO 상세", ""])
    for number, (name, schema) in enumerate(spec["components"].items(), start=1):
        lines.extend(
            [
                f"### 5.{number} `{name}`",
                "",
                "```json",
                json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.extend(
        ["## 6. 감사 이벤트", "", "| Event | Entity | 기록 주체 |", "|---|---|---|"]
    )
    lines.extend(
        f"| `{item['event']}` | `{item['entity']}` | {item['writer']} |"
        for item in spec["audit_events"]
    )
    lines.append("")
    return "\n".join(lines)


def write_pdf(spec: Mapping[str, Any], path: Path = PDF_PATH) -> None:
    """Write a deterministic, dependency-free text PDF.

    ``ensure_ascii`` keeps every canonical schema byte representable by Helvetica;
    Korean prose remains fully available in the Markdown/CSV artifacts.  The PDF is
    intentionally an audit projection whose extracted text retains every semantic key
    and constraint.
    """

    lines = ["BISTel FDC Agent API Specification v3", "canonical: api_spec_v3.json"]
    for operation in spec["operations"]:
        lines.append(
            f"{operation['order']:02d} {operation['method']} {operation['path']}"
        )
        encoded_rules = json.dumps(
            operation["rules"], ensure_ascii=True, sort_keys=True
        )
        lines.extend(
            f"RULES {encoded_rules}"[start : start + 105]
            for start in range(0, len(f"RULES {encoded_rules}"), 105)
        )
        semantic = operation["semantic"]
        encoded = (
            "deferred inventory"
            if semantic is None
            else json.dumps(semantic, ensure_ascii=True, sort_keys=True)
        )
        lines.extend(
            encoded[start : start + 105] for start in range(0, len(encoded), 105)
        )
    lines.append("DTO COMPONENTS")
    for name, schema in spec["components"].items():
        lines.append(name)
        encoded = json.dumps(schema, ensure_ascii=True, sort_keys=True)
        lines.extend(
            encoded[start : start + 105] for start in range(0, len(encoded), 105)
        )
    lines.append("AUDIT EVENTS")
    lines.extend(
        f"{item['event']} / {item['entity']} / {item['writer']}"
        for item in spec["audit_events"]
    )

    pages = [lines[start : start + 74] for start in range(0, len(lines), 74)]
    objects: list[bytes] = []

    def add_object(value: bytes) -> int:
        objects.append(value)
        return len(objects)

    catalog_id = add_object(b"")
    pages_id = add_object(b"")
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    for page_lines in pages:
        commands = [b"BT /F1 7 Tf 28 814 Td 9 TL"]
        for line in page_lines:
            escaped = (
                line.encode("ascii")
                .replace(b"\\", b"\\\\")
                .replace(b"(", b"\\(")
                .replace(b")", b"\\)")
            )
            commands.append(b"(" + escaped + b") Tj T*")
        commands.append(b"ET")
        stream = b"\n".join(commands)
        content_id = add_object(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        page_id = add_object(
            (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        page_ids.append(page_id)
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    )

    raw = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, value in enumerate(objects, start=1):
        offsets.append(len(raw))
        raw.extend(f"{object_id} 0 obj\n".encode())
        raw.extend(value)
        raw.extend(b"\nendobj\n")
    xref = len(raw)
    raw.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    raw.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        raw.extend(f"{offset:010d} 00000 n \n".encode())
    raw.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(raw)


def validate_outputs(spec: Mapping[str, Any]) -> None:
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 35 or not all(row["Method"] and row["Path"] for row in rows):
        raise ApiSpecError("generated CSV inventory drift")
    markdown = MARKDOWN_PATH.read_text(encoding="utf-8")
    for operation in spec["operations"]:
        marker = f"{operation['method']} {operation['path']}"
        if marker not in markdown:
            raise ApiSpecError(f"generated Markdown missing: {marker}")
    if "discriminated_union" not in markdown or "^[0-9a-f]{64}$" not in markdown:
        raise ApiSpecError("generated Markdown lost union or pattern constraints")
    if PDF_PATH.stat().st_size < 10_000:
        raise ApiSpecError("generated PDF is unexpectedly small")


def build(*, check: bool = False) -> None:
    spec = load_spec()
    validate_summary_projection(spec)
    csv_text, markdown = render_csv(spec), render_markdown(spec)
    if check:
        if CSV_PATH.read_text(encoding="utf-8") != csv_text:
            raise ApiSpecError("generated CSV is stale")
        if MARKDOWN_PATH.read_text(encoding="utf-8") != markdown:
            raise ApiSpecError("generated Markdown is stale")
        validate_outputs(spec)
        return
    CSV_PATH.write_text(csv_text, encoding="utf-8")
    MARKDOWN_PATH.write_text(markdown, encoding="utf-8")
    write_pdf(spec)
    validate_outputs(spec)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    build(check=args.check)
    print(
        f"API spec {'validated' if args.check else 'generated'}: 35 operations; source={CANONICAL_PATH.name}"
    )


if __name__ == "__main__":
    main()
