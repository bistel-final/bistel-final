"""Generate the structural JSON schemas and failure tables from code + fixture.

The offline Python models additionally enforce SHA/time/cross-file bindings.
Use --sync-planning to copy the same generated table into the local Plan/Task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_lifecycle import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    LifecycleClaim,
    LifecycleOutcome,
    RoundCompletion,
    resolve_failure_code,
)
from app.agent.release_prepared import (  # noqa: E402
    PreparedAttempt,
    SmtpConfigSnapshot,
    SmtpGrant,
)

FIXTURE = BACKEND_ROOT / "tests/fixtures/v5_c_7_1/lifecycle_policy.json"
DESTINATION = REPO / "docs/deliverables/agent"
BEGIN = "<!-- level3-lifecycle-generated:start -->"
END = "<!-- level3-lifecycle-generated:end -->"


def render() -> tuple[str, str]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if {
        key: frozenset(value) for key, value in fixture["transitions"].items()
    } != ALLOWED_TRANSITIONS:
        raise ValueError("LIFECYCLE_FIXTURE_DRIFT")
    models = (
        SmtpConfigSnapshot,
        PreparedAttempt,
        SmtpGrant,
        LifecycleClaim,
        LifecycleOutcome,
        RoundCompletion,
    )
    schemas = {model.__name__: model.model_json_schema() for model in models}
    encoded = json.dumps(schemas, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    lines = [
        BEGIN,
        "",
        "### 코드·fixture 파생 lifecycle 계약",
        "",
        "생성: `backend/scripts/render_level3_lifecycle_contract.py`. "
        "구조는 JSON Schema, nullable·시각·SHA 결속은 "
        "Python validator로 함께 검사한다.",
        "기본 생성·CI 검사 범위는 docs/deliverables/agent의 두 산출물이다. "
        "로컬 Plan·Task는 --sync-planning을 명시한 경우에만 동기화·검사한다.",
        "",
        "SMTP 설정 digest는 SmtpConfigSnapshot의 workflow ID→version, "
        "SMTP host/port/from, recipient allowlist, WF2 callback endpoint를 "
        "키 정렬 canonical JSON으로 SHA-256 계산한다. recipient는 v2로 정규화하며 "
        "credential 등 미정의 필드는 거부한다. validate_runtime은 실제 관측 digest "
        "한 개를 받아 prepared의 approved_config_digest_allowlist에 "
        "포함되는지 검사한다. "
        "실측 수집·Stage2 연결은 별도 후속 구현이다.",
        "",
        "| source state | 허용 mode |",
        "|---|---|",
    ]
    for state, modes in fixture["transitions"].items():
        lines.append(
            f"| `{state}` | "
            + (" · ".join(f"`{mode}`" for mode in modes) or "없음")
            + " |"
        )
    lines += ["", "| phase | issued_by | primary_failure_code |", "|---|---|---|"]
    for item in fixture["failures"]:
        lines.append(
            f"| `{item['phase']}` | `{item['issued_by']}` | "
            f"`{item['primary_failure_code'] or 'null'}` |"
        )
    lines += ["", "| cleanup_result | restore_result | failure_code |", "|---|---|---|"]
    for result in fixture["results"]:
        for case in fixture["failures"]:
            expected = (
                case["primary_failure_code"]
                if result["failure"] == "PRIMARY"
                else result["failure"]
            )
            if (
                resolve_failure_code(
                    case["issued_by"],
                    case["primary_failure_code"],
                    result["cleanup_result"],
                    result["restore_result"],
                )
                != expected
            ):
                raise ValueError("LIFECYCLE_FIXTURE_DRIFT")
        label = (
            "primary_failure_code(null 포함)"
            if result["failure"] == "PRIMARY"
            else result["failure"]
        )
        lines.append(
            f"| `{result['cleanup_result']}` | `{result['restore_result']}` "
            f"| `{label}` |"
        )
    lines += [
        "",
        "`NOT_ATTEMPTED`는 FAILED가 아니며 primary를 유지한다. "
        "HELD를 제외하면 `basis`에 `CLEANUP_E2E_ABSENT`, "
        "`RESTORE_PREV_EMPTY` 또는 `RESTORE_ALREADY_TARGET` 근거가 필요하다.",
        "",
        END,
        "",
    ]
    return encoded, "\n".join(lines)


def planning_text(original: str, table: str) -> str:
    if BEGIN in original:
        prefix, rest = original.split(BEGIN, 1)
        _old, suffix = rest.split(END, 1)
        return prefix + table.rstrip() + suffix
    return original.rstrip() + "\n\n" + table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--sync-planning", action="store_true")
    args = parser.parse_args(argv)
    schema, table = render()
    outputs = {
        DESTINATION / "level3_lifecycle.schema.json": schema,
        DESTINATION / "level3_lifecycle_contract.md": table,
    }
    if args.sync_planning:
        for name in ("V5-C-7.1_작업계획.md", "V5-C-7.1_Task.md"):
            path = REPO / "output" / name
            outputs[path] = planning_text(path.read_text(encoding="utf-8"), table)
    for path, expected in outputs.items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                print("LIFECYCLE_DOCUMENT_DRIFT")
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
