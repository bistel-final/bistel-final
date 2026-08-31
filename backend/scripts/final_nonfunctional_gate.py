"""V5-CM-5.3 final non-functional evidence gate.

Stage 1 is deliberately offline.  It validates repository contracts and writes a
deterministic ``SKELETON`` report, but it never connects to PostgreSQL, Neo4j,
n8n, or Kafka and therefore cannot mark CM-5.3 complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
for _root in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
REPOSITORY_ROOT = BACKEND_ROOT.parent

REPORT_JSON_PATH = REPOSITORY_ROOT / "docs/deliverables/final-nonfunctional-gate.json"
REPORT_MARKDOWN_PATH = REPOSITORY_ROOT / "docs/deliverables/final-nonfunctional-gate.md"
MAX_TEXT_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KST = ZoneInfo("Asia/Seoul")

RuleStatus = Literal["PASS", "FAIL", "EVIDENCE_MISSING", "NOT_EXERCISED", "RESIDUAL"]
OverallVerdict = Literal["INCOMPLETE", "FAIL", "BLOCKED", "PASS_WITH_RESIDUALS", "PASS"]


class FinalGateError(ValueError):
    """An input is malformed or a fail-closed gate did not pass."""


@dataclass(frozen=True, slots=True)
class BinaryBaseline:
    path: str
    sha256: str
    rule_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SecretAllowlist:
    path: str
    rule_id: str
    reason: str
    scope_owner: str


@dataclass(frozen=True, slots=True)
class SecretFinding:
    path: str
    rule_id: str


BINARY_BASELINE = (
    BinaryBaseline(
        "docs/deliverables/fonts/D2Coding-Bold.ttf",
        "dde75df435f061eaa0f6db84b1c30866aaa442d7038aaa62ea3c2be92f15d87d",
        "BINARY_OR_LARGE",
        "문서 PDF 빌드용 tracked font",
    ),
    BinaryBaseline(
        "docs/deliverables/fonts/Pretendard-Bold.ttf",
        "0eab405f611a20a6949ad8a52f58b80e0b3f96bf4a94b1c8d150eacbbdebf756",
        "BINARY_OR_LARGE",
        "문서 PDF 빌드용 tracked font",
    ),
    BinaryBaseline(
        "docs/deliverables/fonts/Pretendard-Regular.ttf",
        "97656c44f06d7590d2c5a4c31bd0107aa3561b770befef3ebd279cce96ad0b5d",
        "BINARY_OR_LARGE",
        "문서 PDF 빌드용 tracked font",
    ),
    BinaryBaseline(
        "docs/deliverables/requirements-spec/요구사항정의서.pdf",
        "63876d7a5f6f37ae12bd100608dc49b3d19d2afd302d1675fbe47c210b01967e",
        "BINARY_OR_LARGE",
        "동일 디렉터리 Markdown 정본의 배포 PDF",
    ),
    BinaryBaseline(
        "docs/deliverables/system-design/시스템설계서.pdf",
        "8e532ee422a3d5100855e70bbbae56a7263421d5e250e55811aa2dc748924ccf",
        "BINARY_OR_LARGE",
        "동일 디렉터리 Markdown 정본의 배포 PDF",
    ),
    BinaryBaseline(
        "frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/.thumbnail",
        "d0a27d500045678afcf040a9b5930fd9c5c9780ee610c2b4e13be0ca3e1016b0",
        "BINARY_OR_LARGE",
        "멘토님 제공 참고 UI export thumbnail",
    ),
    BinaryBaseline(
        "frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/uploads/"
        "pasted-1785889915990-0.png",
        "aea07d64debaa69ec31dc4418f8eda3c2409927310778ea18b045c0821dd7f84",
        "BINARY_OR_LARGE",
        "멘토님 제공 참고 UI export image",
    ),
    BinaryBaseline(
        "frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/uploads/"
        "pasted-1785892444670-0.png",
        "7f402c908004879522342c6550e4ec8397192b8b9de1c997de34eb74e75636e7",
        "BINARY_OR_LARGE",
        "멘토님 제공 참고 UI export image",
    ),
    BinaryBaseline(
        "frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/uploads/"
        "pasted-1785892452612-0.png",
        "c9142b5dc6c79d8c2b46da36eef14eeef98fcab802da83fd2f6a7655cfaa3fc4",
        "BINARY_OR_LARGE",
        "멘토님 제공 참고 UI export image",
    ),
    BinaryBaseline(
        "frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/uploads/"
        "pasted-1785892460243-0.png",
        "40b581608e97dbc1a6ebbe16fd222e5f9ada89b0ecc6ea2202893a576d870205",
        "BINARY_OR_LARGE",
        "멘토님 제공 참고 UI export image",
    ),
)

# Exact file/rule exceptions only.  Values are never stored in this list.
SECRET_ALLOWLIST = (
    SecretAllowlist(
        "backend/scripts/manifest_v3.py",
        "CREDENTIAL_URI",
        "manifest sanitizer 계약 설명의 red 예시",
        "V5-CM-1.7/Common",
    ),
    SecretAllowlist(
        "backend/tests/unit/test_manifest_v3.py",
        "CREDENTIAL_URI",
        "manifest sanitizer의 의도적 red fixture",
        "V5-CM-1.7/Common",
    ),
    SecretAllowlist(
        "backend/tests/unit/test_final_nonfunctional_gate.py",
        "CREDENTIAL_URI",
        "final gate mutation red fixture",
        "V5-CM-5.3/Common",
    ),
    SecretAllowlist(
        "backend/tests/unit/test_final_nonfunctional_gate.py",
        "PRIVATE_KEY_HEADER",
        "final gate mutation red fixture",
        "V5-CM-5.3/Common",
    ),
    SecretAllowlist(
        "backend/tests/unit/test_final_nonfunctional_gate.py",
        "SENSITIVE_ASSIGNMENT",
        "final gate mutation red fixture",
        "V5-CM-5.3/Common",
    ),
    SecretAllowlist(
        "backend/tests/unit/test_agent_checkpoint.py",
        "SENSITIVE_ASSIGNMENT",
        "checkpoint sanitize의 의도적 secret red fixture",
        "V5-C-1.2/Agent",
    ),
    SecretAllowlist(
        "backend/tests/unit/test_run_pending_incidents.py",
        "SENSITIVE_ASSIGNMENT",
        "batch runner sanitize의 의도적 secret red fixture",
        "V5-C-4.4/Agent",
    ),
    *(
        SecretAllowlist(
            path,
            "SENSITIVE_ASSIGNMENT",
            "credential guard·CLI 회귀의 의도적 non-production fixture",
            "기존 보안 회귀/해당 Task owner",
        )
        for path in (
            "backend/tests/unit/test_agent_runtime_v5_container.py",
            "backend/tests/unit/test_apply_agent_runtime.py",
            "backend/tests/unit/test_bootstrap_neo4j_graph.py",
            "backend/tests/unit/test_checkpoint_backup.py",
            "backend/tests/unit/test_checkpoint_contract.py",
            "backend/tests/unit/test_e2e_reset_guard.py",
            "backend/tests/unit/test_email_delivery.py",
            "backend/tests/unit/test_postgres_backup.py",
            "backend/tests/unit/test_postgres_role_matrix.py",
            "backend/tests/unit/test_rebuild_runner.py",
            "backend/tests/unit/test_register_final_manifests.py",
            "backend/tests/unit/test_rehearse_recovery_cli.py",
            "backend/tests/unit/test_rehearse_schema.py",
            "backend/tests/unit/test_severity_pair_guard.py",
            "backend/tests/unit/test_severity_pair_guard_container.py",
            "backend/tests/unit/test_transition_orchestration.py",
        )
    ),
    *(
        SecretAllowlist(
            path,
            "CREDENTIAL_URI",
            "credential 원문 sanitize·target guard의 의도적 red fixture",
            "기존 보안 회귀/해당 Task owner",
        )
        for path in (
            "backend/tests/unit/checkpoint_state_guard.py",
            "backend/tests/unit/test_agent_checkpoint.py",
            "backend/tests/unit/test_agent_graph.py",
            "backend/tests/unit/test_agent_routing.py",
            "backend/tests/unit/test_bootstrap_base_schema.py",
            "backend/tests/unit/test_bootstrap_neo4j_graph.py",
            "backend/tests/unit/test_config_validation.py",
            "backend/tests/unit/test_document_search.py",
            "backend/tests/unit/test_graph_context.py",
            "backend/tests/unit/test_postgres_role_matrix_container.py",
            "backend/tests/unit/test_readiness.py",
            "backend/tests/unit/test_reference_extensions_v5.py",
            "backend/tests/unit/test_register_final_manifests.py",
            "backend/tests/unit/test_run_pending_incidents.py",
        )
    ),
)

_SECRET_RULES = (
    (
        "CREDENTIAL_URI",
        re.compile(
            r"(?i)\b(?:postgres(?:ql)?|neo4j|bolt|https?)://"
            r"[^\s/@:'\"]+:[^\s/@'\"]+@"
        ),
    ),
    (
        "PRIVATE_KEY_HEADER",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE " r"KEY-----"),
    ),
    (
        "HIGH_CONFIDENCE_TOKEN",
        re.compile(
            r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})"
        ),
    ),
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)\b(?:password|secret|api[_-]?key|access[_-]?token|dsn|"
    r"database[_-]?url)\b[ \t]*[:=][ \t]*(?P<value>[^\s,;}\]]+)"
)
_DUMMY_VALUE_PARTS = (
    "${",
    "<",
    "none",
    "null",
    "dummy",
    "example",
    "not-a-secret",
    "change-me",
    "localhost",
    "%s",
)

TIMESTAMP_REGISTRY: Mapping[str, tuple[str, ...]] = {
    "action_history": ("approved_at", "notify_at", "mes_at", "created_at"),
    "fdc_trace": ("measured_at",),
    "lot_history": ("track_in_at", "track_out_at"),
    "metrology": ("measured_at",),
    "summary_alarm_history": ("occurred_at",),
    "trace_alarm_history": ("occurred_at",),
}
# ``action_history`` is a final-data legacy table: its naive values are KST
# wall time. Runtime writes normalize aware instants at the repository boundary.

OPENAPI_DATETIME_FIELDS = frozenset(
    {
        "components/schemas/ActionDeliveryDetailItem/properties/completed_at/anyOf/0",
        "components/schemas/ActionDeliveryDetailItem/properties/started_at/anyOf/0",
        "components/schemas/ActionDetailResponse/properties/created_at",
        "components/schemas/ActionItem/properties/created_at",
        "components/schemas/AgentRunApprovalItem/properties/decided_at/anyOf/0",
        "components/schemas/AgentRunDetailResponse/properties/created_at",
        "components/schemas/AlarmItem/properties/occurred_at",
        "components/schemas/AuditLogItem/properties/at",
        "components/schemas/AuditLogItem/properties/occurred_at",
        "components/schemas/DeliveryResult/properties/completed_at",
        "components/schemas/EvaluationResponse/properties/executed_at",
        "components/schemas/NlQueryLogItem/properties/asked_at",
        "components/schemas/PublicAgentRunItem/properties/created_at",
        "components/schemas/PublicApprovalItem/properties/approved_at/anyOf/0",
        "components/schemas/PublicApprovalItem/properties/created_at",
        "components/schemas/PublicApprovalItem/properties/decided_at/anyOf/0",
        "components/schemas/TracePoint/properties/measured_at",
        "paths//internal/actions/{action_id}/delivery/post/requestBody/content/"
        "application/json/schema/properties/completed_at",
    }
)
INTERNAL_DATETIME_EXCEPTIONS = frozenset(
    {
        "components/schemas/DeliveryResult/properties/completed_at",
        "paths//internal/actions/{action_id}/delivery/post/requestBody/content/"
        "application/json/schema/properties/completed_at",
    }
)

RULE_STATUSES = {
    "PASS",
    "FAIL",
    "EVIDENCE_MISSING",
    "NOT_EXERCISED",
    "RESIDUAL",
}
OVERALL_VERDICTS = {
    "INCOMPLETE",
    "FAIL",
    "BLOCKED",
    "PASS_WITH_RESIDUALS",
    "PASS",
}
EVIDENCE_KEYS = {"kind", "reference", "sha256", "note", "target", "result"}
RULE_KEYS = {"rule_id", "required", "status", "evidence", "residual"}


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_paths(repository_root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    """Return Git tracked paths without interpreting whitespace or shell syntax."""

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    return tuple(item.decode("utf-8") for item in result.stdout.split(b"\0") if item)


def scan_text(path: str, text: str) -> tuple[SecretFinding, ...]:
    """Return path/rule metadata without returning matched secret values."""

    findings = []
    for rule_id, pattern in _SECRET_RULES:
        if pattern.search(text):
            findings.append(SecretFinding(path, rule_id))
    for match in _SENSITIVE_ASSIGNMENT.finditer(text):
        raw = match.group("value")
        literal_value = raw.strip("\"'`")
        value = literal_value.casefold()
        if any(part in value for part in _DUMMY_VALUE_PARTS):
            continue
        # 변수 전달·함수 호출·환경변수 key는 secret 자체가 아니다. 반면 따옴표로
        # 감싼 non-dummy literal은 짧더라도 실제 비밀번호일 수 있으므로 fail closed다.
        is_literal = raw[:1] in {'"', "'", "`"}
        is_expression_literal = literal_value.startswith(("$", "{", "["))
        unique_sentinel = (
            not is_expression_literal and "sentinel" in value and len(value) >= 12
        )
        literal_secret = (
            is_literal
            and len(literal_value) >= 4
            and not is_expression_literal
            and not re.fullmatch(r"[A-Z][A-Z0-9_]+", literal_value)
        )
        if unique_sentinel or literal_secret:
            findings.append(SecretFinding(path, "SENSITIVE_ASSIGNMENT"))
    return tuple(findings)


def scan_tracked_repository(
    repository_root: Path = REPOSITORY_ROOT,
    *,
    paths: Sequence[str] | None = None,
    binary_baseline: Sequence[BinaryBaseline] = BINARY_BASELINE,
    secret_allowlist: Sequence[SecretAllowlist] = SECRET_ALLOWLIST,
) -> dict[str, int]:
    """Fail closed across every tracked path, including symlink target strings."""

    tracked = tuple(paths if paths is not None else tracked_paths(repository_root))
    if len(tracked) != len(set(tracked)):
        raise FinalGateError("tracked path inventory contains duplicates")
    baseline = {entry.path: entry for entry in binary_baseline}
    if len(baseline) != len(binary_baseline):
        raise FinalGateError("binary baseline path is duplicated")
    allowed = {(entry.path, entry.rule_id) for entry in secret_allowlist}
    if len(allowed) != len(secret_allowlist):
        raise FinalGateError("secret allowlist tuple is duplicated")
    if any(
        not entry.reason or not entry.scope_owner or "*" in entry.path
        for entry in secret_allowlist
    ):
        raise FinalGateError("secret allowlist must be exact and owned")

    scanned = 0
    allowlisted_binary = 0
    findings: list[SecretFinding] = []
    seen_binary: set[str] = set()
    for relative in tracked:
        path = repository_root / relative
        if path.is_symlink():
            raw = os.readlink(path).encode("utf-8")
        else:
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise FinalGateError(
                    f"tracked path cannot be read: {relative}"
                ) from exc
        is_binary_or_large = b"\0" in raw or len(raw) > MAX_TEXT_BYTES
        if is_binary_or_large:
            entry = baseline.get(relative)
            if entry is None or entry.rule_id != "BINARY_OR_LARGE":
                raise FinalGateError(
                    f"unreviewed binary/large tracked path: {relative}"
                )
            if _sha256_bytes(raw) != entry.sha256:
                raise FinalGateError(f"binary/large baseline digest drift: {relative}")
            seen_binary.add(relative)
            allowlisted_binary += 1
            continue
        # NUL/size가 binary baseline의 판정 정본이다. NUL이 없는 소형 PDF처럼
        # 일부 byte만 UTF-8이 아닌 파일은 replacement decode해 ASCII secret
        # signature를 계속 검사하며, 조용히 skip하지 않는다.
        text = raw.decode("utf-8", errors="replace")
        scanned += 1
        findings.extend(scan_text(relative, text))

    missing_baseline = sorted(set(baseline) - seen_binary)
    if missing_baseline:
        raise FinalGateError(
            "binary baseline no longer maps to binary/large tracked paths: "
            + ", ".join(missing_baseline)
        )
    unapproved = sorted({(item.path, item.rule_id) for item in findings} - allowed)
    if unapproved:
        rendered = ", ".join(f"{path}:{rule}" for path, rule in unapproved)
        raise FinalGateError(f"sensitive pattern found (value redacted): {rendered}")
    if scanned + allowlisted_binary != len(tracked):
        raise FinalGateError("tracked scan accounting mismatch")
    return {
        "tracked": len(tracked),
        "scanned_text": scanned,
        "exact_binary_allowlisted": allowlisted_binary,
        "allowed_findings": len(findings),
    }


def validate_python_requirements(text: str) -> tuple[str, ...]:
    """Parse logical PEP 508 requirements and require one exact ``==`` pin."""

    from packaging.requirements import InvalidRequirement, Requirement

    names: list[str] = []
    logical = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            logical += stripped[:-1].strip() + " "
            continue
        logical += stripped
        requirement_text = logical.split(" #", 1)[0].strip()
        logical = ""
        if requirement_text.startswith(("-e ", "--editable ")):
            raise FinalGateError("editable Python requirement is not pinned")
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement as exc:
            raise FinalGateError("invalid Python requirement") from exc
        specifiers = list(requirement.specifier)
        if (
            requirement.url is not None
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            raise FinalGateError(f"Python requirement is not exact: {requirement.name}")
        names.append(requirement.name.casefold())
    if logical:
        raise FinalGateError("unterminated Python requirement continuation")
    if not names or len(names) != len(set(names)):
        raise FinalGateError("Python requirement inventory is empty or duplicated")
    return tuple(names)


def validate_node_lock(payload: Mapping[str, Any]) -> int:
    """Validate npm lockfile v3 resolved entries, not root semver requests."""

    if payload.get("lockfileVersion") != 3:
        raise FinalGateError("npm lockfileVersion must be 3")
    packages = payload.get("packages")
    if not isinstance(packages, Mapping):
        raise FinalGateError("npm packages inventory is missing")
    resolved_count = 0
    for path, value in packages.items():
        if not isinstance(path, str) or not path.startswith("node_modules/"):
            continue
        if not isinstance(value, Mapping):
            raise FinalGateError(f"npm lock entry is invalid: {path}")
        version = value.get("version")
        resolved = value.get("resolved")
        integrity = value.get("integrity")
        if not isinstance(version, str) or not re.fullmatch(
            r"\d+\.\d+\.\d+[^\s]*", version
        ):
            raise FinalGateError(f"npm resolved version is not exact: {path}")
        if not isinstance(resolved, str) or not resolved.startswith(
            "https://registry.npmjs.org/"
        ):
            raise FinalGateError(f"npm resolved registry is not pinned: {path}")
        if not isinstance(integrity, str) or not re.fullmatch(
            r"sha(?:256|384|512)-[A-Za-z0-9+/=]+", integrity
        ):
            raise FinalGateError(f"npm integrity is missing: {path}")
        resolved_count += 1
    if resolved_count == 0:
        raise FinalGateError("npm lock has no resolved packages")
    return resolved_count


def _image_has_exact_tag(image: str) -> bool:
    tail = image.rsplit("/", 1)[-1]
    if "@sha256:" in image:
        return bool(re.search(r"@sha256:[0-9a-f]{64}$", image))
    if ":" not in tail:
        return False
    tag = tail.rsplit(":", 1)[1]
    return bool(tag and tag != "latest" and "${" not in tag)


def validate_container_pins(
    repository_root: Path = REPOSITORY_ROOT,
    *,
    team_image_tag: str,
) -> tuple[str, ...]:
    """Validate the exact production Docker/Compose path inventory."""

    if not team_image_tag or team_image_tag == "latest" or ":" in team_image_tag:
        raise FinalGateError("TEAM_IMAGE_TAG must render to one non-latest tag")
    dockerfiles = (
        "backend/Dockerfile",
        "frontend/Dockerfile",
    )
    compose_paths = (
        "deploy/compose/docker-compose.team.yml",
        "deploy/compose/docker-compose.e2e-backend.yml",
    )
    images: list[str] = []
    for relative in dockerfiles:
        text = (repository_root / relative).read_text(encoding="utf-8")
        images.extend(
            line.split()[1]
            for line in text.splitlines()
            if line.strip().upper().startswith("FROM ")
        )
    expected_base = {
        "python:3.12.8-slim",
        "node:22.14.0-alpine",
        "nginx:1.27.3-alpine",
    }
    if set(images) != expected_base:
        raise FinalGateError("production Docker base image inventory drift")

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is CI-pinned
        raise FinalGateError("PyYAML is required for Compose validation") from exc
    compose_images: list[str] = []
    for relative in compose_paths:
        payload = yaml.safe_load(
            (repository_root / relative).read_text(encoding="utf-8")
        )
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("services"), Mapping
        ):
            raise FinalGateError(f"Compose services are missing: {relative}")
        for service in payload["services"].values():
            if not isinstance(service, Mapping) or "image" not in service:
                continue
            image = str(service["image"])
            image = re.sub(r"\$\{TEAM_IMAGE_TAG[^}]*\}", team_image_tag, image)
            if not _image_has_exact_tag(image):
                raise FinalGateError(f"Compose image is not pinned: {relative}")
            compose_images.append(image)
    if "apache/kafka:3.9.1" not in compose_images:
        raise FinalGateError("Kafka production image pin is missing")
    return tuple(sorted(set((*images, *compose_images))))


def validate_timestamp_contract(
    manifest: Mapping[str, Any], schema_sql: str
) -> Mapping[str, tuple[str, ...]]:
    """Validate the final 6-table/10-column naive timestamp registry."""

    tables = manifest.get("tables")
    if not isinstance(tables, Mapping):
        raise FinalGateError("source manifest tables are missing")
    actual: dict[str, tuple[str, ...]] = {}
    for table, value in tables.items():
        if not isinstance(value, Mapping):
            continue
        types = value.get("column_types")
        if not isinstance(types, Mapping):
            continue
        columns = tuple(
            column for column, logical in types.items() if logical == "timestamp"
        )
        if columns:
            actual[str(table)] = columns
    if actual != dict(TIMESTAMP_REGISTRY):
        raise FinalGateError(
            "final timestamp registry is not exact 6 tables/10 columns"
        )
    for table, columns in TIMESTAMP_REGISTRY.items():
        match = re.search(
            rf"(?is)CREATE\s+TABLE\s+{re.escape(table)}\s*\((.*?)\);", schema_sql
        )
        if match is None:
            raise FinalGateError(f"timestamp table DDL is missing: {table}")
        body = match.group(1)
        for column in columns:
            type_match = re.search(
                rf"(?im)^\s*{re.escape(column)}\s+timestamp(?:\s+without\s+time\s+zone)?\b",
                body,
            )
            if type_match is None or re.search(
                rf"(?im)^\s*{re.escape(column)}\s+timestamptz\b", body
            ):
                raise FinalGateError(f"timestamp DDL type drift: {table}.{column}")
    return actual


def validate_api_datetime_samples(
    samples: Mapping[str, tuple[datetime, str | None]],
    *,
    exceptions: Iterable[str] = (),
) -> None:
    """Validate public offsets and source-to-response instant preservation."""

    exception_set = set(exceptions)
    if not exception_set.issubset(samples):
        raise FinalGateError("date-time exception is not present in inventory")
    for field, (source, value) in samples.items():
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise FinalGateError(f"API date-time sample is invalid: {field}") from exc
        if parsed.utcoffset() is None:
            raise FinalGateError(f"API date-time sample has no offset: {field}")
        if field not in exception_set and not value.endswith("+09:00"):
            raise FinalGateError(f"API date-time sample is not +09:00: {field}")
        expected = (
            source.replace(tzinfo=KST)
            if source.tzinfo is None
            else source.astimezone(KST)
        )
        if parsed != expected:
            raise FinalGateError(f"API date-time sample has wall-time drift: {field}")


def collect_openapi_datetime_fields(openapi: Mapping[str, Any]) -> tuple[str, ...]:
    """Create a stable JSON-pointer-like inventory of OpenAPI date-time fields."""

    found: list[str] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            if value.get("format") == "date-time":
                found.append("/".join(path))
            for key in sorted(value):
                walk(value[key], (*path, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))

    walk(openapi, ())
    return tuple(found)


def validate_openapi_datetime_inventory(openapi: Mapping[str, Any]) -> tuple[str, ...]:
    """Freeze public date-time coverage and explicit internal HMAC exceptions."""

    actual = frozenset(collect_openapi_datetime_fields(openapi))
    if actual != OPENAPI_DATETIME_FIELDS:
        raise FinalGateError(
            "OpenAPI date-time inventory drift requires explicit review"
        )
    if not INTERNAL_DATETIME_EXCEPTIONS < actual:
        raise FinalGateError("internal date-time exception inventory is invalid")
    return tuple(sorted(actual - INTERNAL_DATETIME_EXCEPTIONS))


def aggregate_verdict(stage: str, rules: Sequence[Mapping[str, Any]]) -> OverallVerdict:
    if stage == "SKELETON":
        return "INCOMPLETE"
    if stage != "FINAL":
        raise FinalGateError("report stage is invalid")
    statuses = {str(rule.get("status")) for rule in rules}
    if "FAIL" in statuses:
        return "FAIL"
    if statuses & {"EVIDENCE_MISSING", "NOT_EXERCISED"}:
        return "BLOCKED"
    if "RESIDUAL" in statuses:
        return "PASS_WITH_RESIDUALS"
    return "PASS"


def _validate_evidence(entry: Any, *, external: bool) -> None:
    if not isinstance(entry, Mapping) or set(entry) != EVIDENCE_KEYS:
        raise FinalGateError("evidence entry schema drift")
    if entry["kind"] not in {"test", "file", "marker"}:
        raise FinalGateError("evidence kind is invalid")
    if not isinstance(entry["reference"], str) or not entry["reference"]:
        raise FinalGateError("evidence reference is invalid")
    if Path(entry["reference"]).is_absolute():
        raise FinalGateError("evidence reference must not expose an absolute path")
    digest = entry["sha256"]
    if digest is not None and (
        not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)
    ):
        raise FinalGateError("evidence sha256 is invalid")
    if not isinstance(entry["note"], str) or not entry["note"]:
        raise FinalGateError("evidence note is invalid")
    for key in ("target", "result"):
        if entry[key] is not None and not isinstance(entry[key], str):
            raise FinalGateError(f"evidence {key} is invalid")
    if not external and (entry["target"] is not None or entry["result"] is not None):
        raise FinalGateError("rule evidence cannot carry external target/result")


def validate_report(report: Mapping[str, Any]) -> None:
    if set(report) != {"stage", "overall_verdict", "rules", "external_evidence"}:
        raise FinalGateError("report top-level schema drift")
    stage = report["stage"]
    verdict = report["overall_verdict"]
    rules = report["rules"]
    external = report["external_evidence"]
    if stage not in {"SKELETON", "FINAL"} or verdict not in OVERALL_VERDICTS:
        raise FinalGateError("report stage/verdict enum is invalid")
    if not isinstance(rules, list) or not rules:
        raise FinalGateError("report rules must be a non-empty array")
    ids: list[str] = []
    for rule in rules:
        if not isinstance(rule, Mapping) or set(rule) != RULE_KEYS:
            raise FinalGateError("report rule schema drift")
        rule_id = rule["rule_id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise FinalGateError("report rule_id is invalid")
        ids.append(rule_id)
        if (
            not isinstance(rule["required"], bool)
            or rule["status"] not in RULE_STATUSES
        ):
            raise FinalGateError("report rule status/required is invalid")
        if not isinstance(rule["evidence"], list):
            raise FinalGateError("report rule evidence must be an array")
        for entry in rule["evidence"]:
            _validate_evidence(entry, external=False)
        if not isinstance(rule["residual"], list) or any(
            not isinstance(item, str) or not item for item in rule["residual"]
        ):
            raise FinalGateError("report residual must be a string array")
    if len(ids) != len(set(ids)):
        raise FinalGateError("report rule_id is duplicated")
    if not isinstance(external, list):
        raise FinalGateError("external_evidence must be an array")
    for entry in external:
        _validate_evidence(entry, external=True)
    if aggregate_verdict(str(stage), rules) != verdict:
        raise FinalGateError("report aggregate verdict drift")


def _evidence(
    kind: str,
    reference: str,
    note: str,
    *,
    sha256: str | None = None,
    target: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "reference": reference,
        "sha256": sha256,
        "note": note,
        "target": target,
        "result": result,
    }


def _file_evidence(relative: str, note: str) -> dict[str, Any]:
    return _evidence(
        "file",
        relative,
        note,
        sha256=sha256_file(REPOSITORY_ROOT / relative),
    )


def build_skeleton_report() -> dict[str, Any]:
    """Build stage-1 evidence without reading any external backup directory."""

    rules = [
        {
            "rule_id": "NFR-02",
            "required": True,
            "status": "PASS",
            "evidence": [
                _file_evidence(
                    "backend/scripts/final_nonfunctional_gate.py",
                    "tracked 전수 secret scan과 exact binary baseline",
                )
            ],
            "residual": [],
        },
        {
            "rule_id": "NFR-03",
            "required": True,
            "status": "RESIDUAL",
            "evidence": [
                _file_evidence(
                    "docs/troubleshooting/tool-timeout-verdict.md",
                    "CM-4.8 Tool hard/soft timeout 판정 정본",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_agent_tool_budget.py::"
                    "test_budget_policy_uses_fixed_precedence_and_limits",
                    "HITL 전후 누적 Tool budget 상한",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_tool_timeouts.py::"
                    "test_reserved_tool_call_sentinel_has_no_automatic_recovery_writer",
                    "미완료 sentinel 자동 회수 금지",
                ),
            ],
            "residual": [
                "embedding·anomaly model은 process hard cancellation이 없다.",
                "/agent/ask에는 caller soft deadline이 없다.",
                "예약 sentinel은 실행 identity가 없어 자동 회수하지 않는다.",
            ],
        },
        {
            "rule_id": "NFR-12",
            "required": True,
            "status": "PASS",
            "evidence": [
                _evidence(
                    "test",
                    "tests/test_health.py::test_cors_preflight_allows_configured_origin",
                    "명시 Origin 허용",
                ),
                _evidence(
                    "test",
                    "tests/test_health.py::test_cors_preflight_rejects_unconfigured_origin",
                    "비허용 Origin 거부",
                ),
            ],
            "residual": [],
        },
        {
            "rule_id": "NFR-13",
            "required": True,
            "status": "RESIDUAL",
            "evidence": [
                _file_evidence(
                    "infra/bootstrap/source-manifest-v4.json",
                    "6테이블·10 timestamp 컬럼 logical type 정본",
                ),
                _file_evidence(
                    "infra/bootstrap/001_base_schema.sql",
                    "timestamp without time zone 물리 DDL 정본",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_rehearsal_profile_verifier.py::"
                    "test_naive_timestamp_projects_to_kst",
                    "source wall time의 Asia/Seoul +09:00 투영",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_rehearsal_profile_verifier.py::"
                    "test_row_count_and_typed_hash_match",
                    "source row count와 typed content hash 보존",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_detection_public_api.py::"
                    "test_alarm_projection_is_canonical_offset_aware_and_stably_queried",
                    "public API date-time +09:00 serialization",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_agent_public_api.py::"
                    "test_public_run_json_has_exact_allowlist_and_canonical_aliases",
                    "Agent public API date-time +09:00 serialization",
                ),
                _file_evidence(
                    "backend/scripts/run_analytics_eval.py",
                    "평가 artifact executed_at 생성 지점(UTC 기록 — 잔여 근거)",
                ),
            ],
            "residual": [
                "V5-D-2.6 GET /analytics/evaluations의 EvaluationResponse."
                "executed_at은 평가 artifact가 UTC로 기록한 값을 그대로 직렬화해"
                " +09:00이 아니다. D 소유 경계이므로 이 Task에서 수정하지 않고"
                " owner 확인 뒤 최종화 전 해소한다.",
            ],
        },
        {
            "rule_id": "NFR-14",
            "required": True,
            "status": "EVIDENCE_MISSING",
            "evidence": [
                _file_evidence(
                    "infra/bootstrap/markers/postgres_profile.kosa_agent_e2e.json",
                    "CM-2.6 GH-108 tracked marker",
                ),
                _file_evidence(
                    "infra/bootstrap/markers/postgres_profile.kosa_agent.json",
                    "CM-2.6 GH-108 tracked marker",
                ),
                _file_evidence(
                    "infra/bootstrap/markers/postgres_profile.kosa_text2sql.json",
                    "CM-2.6 GH-108 tracked marker",
                ),
                _file_evidence(
                    "infra/bootstrap/markers/neo4j_graph.neo4j.json",
                    "CM-2.7 GH-128 ADOPTED_EXISTING tracked marker",
                ),
            ],
            "residual": [
                "저장소 밖 CM-2.6·2.7 backup/restore receipt는 FINAL 단계에서 "
                "read-only 대조한다."
            ],
        },
        {
            "rule_id": "NFR-15",
            "required": True,
            "status": "PASS",
            "evidence": [
                _file_evidence("backend/requirements.txt", "Python exact pins"),
                _file_evidence("frontend/package-lock.json", "npm lockfile v3"),
                _file_evidence("backend/Dockerfile", "Python base image pin"),
                _file_evidence("frontend/Dockerfile", "Node/nginx base image pins"),
                _file_evidence(
                    "deploy/compose/docker-compose.team.yml",
                    "team app/Kafka image pin contract",
                ),
                _file_evidence(
                    "deploy/compose/docker-compose.e2e-backend.yml",
                    "E2E backend production override inventory",
                ),
            ],
            "residual": [],
        },
        {
            "rule_id": "NFR-16",
            "required": True,
            "status": "PASS",
            "evidence": [
                _evidence(
                    "test",
                    f"tests/unit/test_final_gate_fault_isolation.py::{name}",
                    note,
                )
                for name, note in (
                    (
                        "test_postgres_fault_keeps_process_alive_and_recovers",
                        "두 PostgreSQL readiness check 격리·복구",
                    ),
                    (
                        "test_neo4j_fault_keeps_process_alive_and_recovers",
                        "Neo4j readiness 격리·복구",
                    ),
                    (
                        "test_llm_fault_returns_sanitized_503_then_recovers",
                        "LLM sanitized 503·동일 process 복구",
                    ),
                    (
                        "test_n8n_fault_keeps_process_alive_and_recovers",
                        "n8n readiness 격리·복구",
                    ),
                    (
                        "test_kafka_fault_keeps_process_alive_and_recovers",
                        "Kafka readiness 격리·복구",
                    ),
                )
            ]
            + [
                _evidence(
                    "test",
                    "tests/unit/test_tool_timeout_postgres_container.py::"
                    "test_registration_query_is_canceled_and_pool_state_is_clean",
                    "PostgreSQL server timeout·pool 후속 성공 보조 증적",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_tool_timeout_neo4j_container.py::"
                    "test_query_timeout_terminates_marker_transaction_and_driver_recovers",
                    "Neo4j transaction timeout·driver 후속 성공 보조 증적",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_common_llm.py::"
                    "test_preflight_fails_closed_when_configured_model_is_absent",
                    "LLM preflight fail-closed 보조 증적",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_readiness.py::"
                    "test_n8n_readiness_fails_closed_before_network_for_invalid_origin",
                    "n8n invalid origin fail-closed 보조 증적",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_readiness.py::"
                    "test_kafka_lag_tracker_rejects_late_observation_and_tracks_stale_lag",
                    "Kafka stale lag fail-closed 보조 증적",
                ),
                _evidence(
                    "test",
                    "tests/unit/test_mes_kafka_container.py::"
                    "test_wrong_sasl_credential_cannot_publish",
                    "Kafka 잘못된 credential 거부 보조 증적",
                ),
            ],
            "residual": [],
        },
    ]
    external = [
        _evidence(
            "marker",
            ".fdc_final_20260818.GH-108.closure.json",
            "저장소 밖 PostgreSQL 3-target closure bundle; stage 1에서는 탐색하지 않음",
            target="kosa_agent_e2e,kosa_agent,kosa_text2sql",
            result="EVIDENCE_MISSING",
        ),
        _evidence(
            "marker",
            "neo4j_graph.neo4j.<timestamp>.manifest.json",
            "저장소 밖 Neo4j backup/restore bundle; stage 1에서는 탐색하지 않음",
            target="neo4j",
            result="EVIDENCE_MISSING",
        ),
    ]
    report = {
        "stage": "SKELETON",
        "overall_verdict": aggregate_verdict("SKELETON", rules),
        "rules": rules,
        "external_evidence": external,
    }
    validate_report(report)
    return report


def canonical_report_bytes(report: Mapping[str, Any]) -> bytes:
    validate_report(report)
    return (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def project_markdown(report: Mapping[str, Any]) -> str:
    raw = canonical_report_bytes(report)
    lines = [
        "# 최종 비기능·증적 Gate",
        "",
        "> Task: `V5-CM-5.3` · 정본: `final-nonfunctional-gate.json`",
        f"> Stage: **{report['stage']}** · Verdict: **{report['overall_verdict']}**",
        f"> JSON SHA-256: `{_sha256_bytes(raw)}`",
        "",
        "## 규칙",
        "",
        "| Rule | Required | Status | Evidence | Residual |",
        "|---|---:|---|---:|---:|",
    ]
    for rule in report["rules"]:
        lines.append(
            f"| `{rule['rule_id']}` | {str(rule['required']).lower()} | "
            f"**{rule['status']}** | {len(rule['evidence'])} | "
            f"{len(rule['residual'])} |"
        )
    for rule in report["rules"]:
        lines.extend(("", f"### {rule['rule_id']}", ""))
        for entry in rule["evidence"]:
            digest = entry["sha256"] or "test reference"
            lines.append(
                f"- `{entry['kind']}` `{entry['reference']}` — {entry['note']} "
                f"(`{digest}`)"
            )
        for residual in rule["residual"]:
            lines.append(f"- 잔여: {residual}")
    lines.extend(("", "## 외부 증적 index", ""))
    for entry in report["external_evidence"]:
        lines.append(
            f"- `{entry['reference']}` · target `{entry['target']}` · "
            f"result **{entry['result']}** — {entry['note']}"
        )
    lines.extend(
        (
            "",
            "> SKELETON은 공용 전환을 재수행하지 않은 1단 산출물이다. 외부 증적과 "
            "CM-5.2 2단 실행보고를 대조하기 전에는 완료·PASS로 해석하지 않는다.",
            "",
        )
    )
    return "\n".join(lines)


def write_report_pair(report: Mapping[str, Any]) -> None:
    REPORT_JSON_PATH.write_bytes(canonical_report_bytes(report))
    REPORT_MARKDOWN_PATH.write_text(project_markdown(report), encoding="utf-8")


def verify_report_pair(report: Mapping[str, Any]) -> None:
    expected_json = canonical_report_bytes(report)
    expected_markdown = project_markdown(report).encode("utf-8")
    if REPORT_JSON_PATH.read_bytes() != expected_json:
        raise FinalGateError("canonical JSON report drift")
    if REPORT_MARKDOWN_PATH.read_bytes() != expected_markdown:
        raise FinalGateError("Markdown projection drift")


def validate_postgres_historical_evidence(
    approval_path: Path,
    backup_root: Path,
    *,
    change_ref: str = "GH-108",
) -> dict[str, dict[str, str]]:
    """Read-only CM-2.6 closure validation; no evidence writer is reachable here."""

    import postgres_backup
    import transition_public_postgres as transition

    approval, approval_sha256 = transition.read_approval(approval_path)
    mode, rejection = postgres_backup.backup_root_trust(
        backup_root, change_ref=change_ref
    )
    if rejection is not None:
        raise FinalGateError(f"PostgreSQL backup root is not trusted: {rejection[0]}")
    return transition.collect_closure_digests(
        backup_root,
        change_ref,
        approval=approval,
        root_trust=mode,
        approval_sha256=approval_sha256,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalGateError(f"evidence artifact cannot be read: {path.name}") from exc
    if not isinstance(payload, dict):
        raise FinalGateError(f"evidence artifact is not an object: {path.name}")
    return payload


def validate_neo4j_historical_evidence(
    *,
    preflight_path: Path,
    backup_manifest_path: Path,
    restore_receipt_path: Path,
    backup_root: Path,
    tracked_marker_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate CM-2.7 receipts without the replace gate's 24-hour TTL."""

    import bootstrap_neo4j_graph as bootstrap

    preflight = _load_json_object(preflight_path)
    manifest = _load_json_object(backup_manifest_path)
    restore = _load_json_object(restore_receipt_path)
    marker = _load_json_object(tracked_marker_path)
    bootstrap.validate_receipt(preflight, "neo4j_preflight_receipt")
    bootstrap.validate_receipt(manifest, "neo4j_backup_manifest")
    bootstrap.validate_receipt(restore, "neo4j_restore_verification_receipt")
    loaded_manifest, _, snapshot, _ = bootstrap.load_backup_bundle(
        backup_manifest_path, backup_root
    )
    pairs = (
        (loaded_manifest, manifest),
        (preflight["database"], manifest["database"]),
        (restore["database"], manifest["database"]),
        (
            preflight["target_fingerprint_sha256"],
            manifest["target_fingerprint_sha256"],
        ),
        (
            restore["target_fingerprint_sha256"],
            manifest["target_fingerprint_sha256"],
        ),
        (preflight["schema_fingerprint_sha256"], manifest["schema_fingerprint_sha256"]),
        (restore["schema_fingerprint_sha256"], manifest["schema_fingerprint_sha256"]),
        (
            preflight["existing_graph_fingerprint_sha256"],
            manifest["backup_graph_fingerprint_sha256"],
        ),
        (
            restore["backup_graph_fingerprint_sha256"],
            manifest["backup_graph_fingerprint_sha256"],
        ),
        (restore["backup_file_sha256"], manifest["backup_file_sha256"]),
        (restore["backup_manifest_sha256"], sha256_file(backup_manifest_path)),
        (snapshot.node_count, manifest["node_count"]),
        (snapshot.relationship_count, manifest["relationship_count"]),
        (marker["database"], manifest["database"]),
        (marker["approval_ref"], "GH-128"),
        (marker["status"], "ADOPTED_EXISTING"),
        (marker["node_count"], manifest["node_count"]),
        (marker["relationship_count"], manifest["relationship_count"]),
        (
            marker["actual_graph_fingerprint_sha256"],
            manifest["backup_graph_fingerprint_sha256"],
        ),
    )
    if any(actual != expected for actual, expected in pairs):
        raise FinalGateError("Neo4j historical evidence cross-binding mismatch")
    recorded = datetime.fromisoformat(
        str(preflight["recorded_at"]).replace("Z", "+00:00")
    )
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    age_seconds = max(0, int((reference - recorded.astimezone(UTC)).total_seconds()))
    return {
        "database": manifest["database"],
        "node_count": manifest["node_count"],
        "relationship_count": manifest["relationship_count"],
        "preflight_age_seconds": age_seconds,
        "preflight_receipt_sha256": sha256_file(preflight_path),
        "backup_manifest_sha256": sha256_file(backup_manifest_path),
        "restore_receipt_sha256": sha256_file(restore_receipt_path),
    }


def run_repository_gates(repository_root: Path = REPOSITORY_ROOT) -> dict[str, Any]:
    secret = scan_tracked_repository(repository_root)
    requirements = validate_python_requirements(
        (repository_root / "backend/requirements.txt").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (repository_root / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    resolved = validate_node_lock(lock)
    images = validate_container_pins(repository_root, team_image_tag="cm-5.3-gate")
    manifest = json.loads(
        (repository_root / "infra/bootstrap/source-manifest-v4.json").read_text(
            encoding="utf-8"
        )
    )
    timestamps = validate_timestamp_contract(
        manifest,
        (repository_root / "infra/bootstrap/001_base_schema.sql").read_text(
            encoding="utf-8"
        ),
    )
    from app.main import app

    public_datetime_fields = validate_openapi_datetime_inventory(app.openapi())
    return {
        "secret_scan": secret,
        "python_requirements": len(requirements),
        "node_resolved_packages": resolved,
        "container_images": list(images),
        "timestamp_columns": sum(len(value) for value in timestamps.values()),
        "public_api_datetime_fields": len(public_datetime_fields),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-skeleton", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        gates = run_repository_gates()
        report = build_skeleton_report()
        if args.write_skeleton:
            write_report_pair(report)
        else:
            verify_report_pair(report)
    except (FinalGateError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"FINAL_NONFUNCTIONAL_GATE_FAILED: {exc}\n")
    print(json.dumps(gates, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
