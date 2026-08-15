#!/usr/bin/env python3
"""Issue Forms(.github/ISSUE_TEMPLATE/*.yml) -> 이슈 본문 마크다운 변환기.

gh CLI 의 `issue create --body` 는 REST API 를 직접 호출하므로 Issue Forms 를
거치지 않는다. 그대로 두면 웹 UI 로 만든 이슈와 구조가 달라진다.
이 스크립트는 템플릿 yml 을 런타임에 읽어 GitHub 이 폼 제출 결과로 생성하는 것과
같은 형태(`### <label>` + 빈 줄 + 값)를 만든다.

yml 을 읽어서 렌더링하므로 템플릿 항목이 바뀌어도 이 파일을 고칠 필요가 없다.

사용 예:
    python3 _issue_body.py --template feature --show-fields
    python3 _issue_body.py --template feature \
        -F area="D - Analytics" \
        -F summary="Text2SQL 검증기를 구현한다" \
        -F work="- [ ] sqlglot AST 검사" \
        -F done="정책 위반 SQL 에서 POLICY_REJECTED 반환"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "ISSUE_TEMPLATE"


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ModuleNotFoundError:
        sys.exit(
            "PyYAML 이 필요합니다.\n"
            "  pip install pyyaml\n"
            "또는 backend 가상환경을 활성화한 뒤 다시 실행하세요."
        )
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _fields(spec: dict) -> list[dict]:
    """id 가 있는 입력 항목만 순서대로 돌려준다 (markdown 안내 블록 제외)."""
    return [
        item
        for item in spec.get("body", [])
        if item.get("type") != "markdown" and item.get("id")
    ]


def _is_required(item: dict) -> bool:
    return bool(item.get("validations", {}).get("required"))


def _render_checkboxes(item: dict) -> str:
    lines = []
    for option in item.get("attributes", {}).get("options", []):
        mark = "x" if option.get("required") else " "
        lines.append(f"- [{mark}] {option['label']}")
    return "\n".join(lines)


def show_fields(spec: dict) -> None:
    print(f"템플릿: {spec.get('name')}  (제목 접두사: {spec.get('title', '')!r})")
    for item in _fields(spec):
        attributes = item.get("attributes", {})
        flag = "필수" if _is_required(item) else "선택"
        line = f"  -F {item['id']}=...    [{flag}] {attributes.get('label')}"
        if item.get("type") == "dropdown":
            options = " | ".join(attributes.get("options", []))
            line += f"\n        선택지: {options}"
        if item.get("type") == "checkboxes":
            line += "\n        (자동 생성 — 값을 넘기지 않습니다)"
        print(line)


def render(spec: dict, values: dict[str, str]) -> str:
    blocks: list[str] = []
    known = {item["id"] for item in _fields(spec)}

    for unknown in sorted(set(values) - known):
        sys.exit(f"템플릿에 없는 항목입니다: {unknown}\n--show-fields 로 확인하세요.")

    for item in _fields(spec):
        field_id = item["id"]
        attributes = item.get("attributes", {})
        label = attributes.get("label", field_id)

        if item.get("type") == "checkboxes":
            blocks.append(f"### {label}\n\n{_render_checkboxes(item)}")
            continue

        value = values.get(field_id, "").strip()

        if not value:
            if _is_required(item):
                sys.exit(f"필수 항목이 비어 있습니다: {field_id} ({label})")
            blocks.append(f"### {label}\n\n_No response_")
            continue

        if item.get("type") == "dropdown":
            options = attributes.get("options", [])
            if value not in options:
                sys.exit(
                    f"'{field_id}' 값이 선택지에 없습니다: {value}\n"
                    f"선택지: {' | '.join(options)}"
                )

        blocks.append(f"### {label}\n\n{value}")

    return "\n\n".join(blocks) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, help="feature | bug | task")
    parser.add_argument(
        "--show-fields",
        action="store_true",
        help="템플릿이 요구하는 항목을 출력하고 종료한다",
    )
    parser.add_argument(
        "--title-prefix",
        action="store_true",
        help="본문 대신 제목 접두사(예: '[Feat] ')만 출력한다",
    )
    parser.add_argument(
        "-F",
        dest="fields",
        action="append",
        default=[],
        metavar="id=value",
        help="항목 값 (반복 지정)",
    )
    args = parser.parse_args()

    path = TEMPLATE_DIR / f"{args.template}.yml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in TEMPLATE_DIR.glob("*.yml")))
        sys.exit(f"템플릿을 찾을 수 없습니다: {path}\n사용 가능: {available}")

    spec = _load_yaml(path)

    if args.show_fields:
        show_fields(spec)
        return

    if args.title_prefix:
        print(spec.get("title", ""), end="")
        return

    values: dict[str, str] = {}
    for raw in args.fields:
        if "=" not in raw:
            sys.exit(f"-F 형식이 잘못되었습니다: {raw} (id=value 형태여야 합니다)")
        key, _, value = raw.partition("=")
        values[key.strip()] = value

    print(render(spec, values), end="")


if __name__ == "__main__":
    main()
