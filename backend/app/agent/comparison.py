"""V5-C-7.1 비교 artifact의 no-clobber 저장과 독립 재계산 validator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
LEVELS: Final = (1, 2, 3)
OBSERVATIONAL_LEVELS: Final = (2, 3)
FIXTURES: Final = ("CF-1", "CF-2", "CF-3", "CF-4", "CF-5")
ATTEMPTS_PER_FIXTURE: Final = 2
DIRECTION_FEATURES: Final = {
    "OOC_ABOVE_UPPER": "DIRECTION_ABOVE",
    "OOS_ABOVE_UPPER": "DIRECTION_ABOVE",
    "OOC_BELOW_LOWER": "DIRECTION_BELOW",
    "OOS_BELOW_LOWER": "DIRECTION_BELOW",
}
EXPECTED_ACTION_DISTRIBUTION: Final = {
    "MONITORING": 5,
    "WARNING": 4,
    "EQP_HOLD": 3,
}
# Immutable v1 artifact validation pins, NOT current runtime/U10 configuration.
# Do not update these prompt versions to rerun the historical v1 experiment.
EXPECTED_LEVEL_CONFIGS: Final = [
    {
        "level": level,
        "config": {"autonomy_level": level},
        "hypothesis_prompt_version": "agent-hypothesis-v2-ko1",
        "react_prompt_version": "agent-react-v1-ko1" if level == 3 else None,
        "ports": "deterministic",
    }
    for level in LEVELS
]
DERIVATION_RULES: Final = {
    "rule_version": "v12",
    "delta_kind": "cumulative-a-c/adjacent-b",
    "tool_delta_min": -3,
    "cf4_fdc_delta_min": 0,
    "cf4_recall_non_regression": True,
    "delta_ratio_min": 0.5,
    "delta_ratio_fixtures": ["CF-1", "CF-2", "CF-3"],
}
DERIVATION_ALLOWED_BACKEND_FILES: Final = (
    "backend/app/agent/comparison.py",
    "backend/scripts/compare_autonomy_levels.py",
    "backend/scripts/derive_agent_justification.py",
    "backend/scripts/observe_agent_justification.py",
    "backend/scripts/verify_agent_justification.py",
    "backend/tests/unit/test_agent_comparison.py",
    "backend/tests/unit/test_agent_experiment.py",
    "backend/tests/unit/test_agent_observation.py",
)
DERIVATION_FIXTURE_PREFIX: Final = "backend/tests/fixtures/v5_c_7_1/derivation/"
DERIVATION_ALLOWED_BACKEND_PATTERNS: Final = (
    *DERIVATION_ALLOWED_BACKEND_FILES,
    f"{DERIVATION_FIXTURE_PREFIX}*",
)
DERIVATION_STRICT_SCRIPTS: Final = (
    "backend/scripts/compare_autonomy_levels.py",
    "backend/scripts/observe_agent_justification.py",
)
DERIVATION_ALLOWED_IMPORT_CHANGE_LINES: Final = (
    "-from app.detection.service import FdcSummaryService  # noqa: E402",
    "+        from app.detection.service import FdcSummaryService",
    "+",
)
V1_LEVEL2_ATTEMPT_KEYS: Final = frozenset(
    {
        "fixture_id",
        "attempt_no",
        "pair_id",
        "initial_snapshot_sha256",
        "terminal",
        "completion",
        "tool_path",
        "tool_input_digests",
        "document_query_sha256",
        "unsupported_count",
        "recall",
        "tokens",
        "latency_ms",
        "baseline_projection",
        "step_projections",
        "react_trace_summary",
        "cited_evidence_ids",
        "cited_evidence_ids_sha256",
        "available_evidence_ids",
        "available_evidence_ids_sha256",
    }
)
V1_LEVEL3_ATTEMPT_KEYS: Final = frozenset(
    {*V1_LEVEL2_ATTEMPT_KEYS, "baseline_delta_kind"}
)
V1_PAIR_KEYS: Final = frozenset(
    {"pair_id", "recall_delta", "completion_delta", "tool_delta"}
)
V2_COPIED_FIELDS: Final = (
    "level_comparison_sha256",
    "fixture_sha256",
    "oracle_sha256",
    "OBSERVATIONAL_REAL_LLM",
    "attempts_per_fixture",
    "llm",
    "oracle",
    "level2_attempts",
    "level3_attempts",
    "pairs",
    "safety",
)
V2_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "schema_version",
        "source_revision",
        "run_revision",
        "derived_from_sha256",
        "level_comparison_sha256",
        "fixture_sha256",
        "oracle_sha256",
        "OBSERVATIONAL_REAL_LLM",
        "attempts_per_fixture",
        "llm",
        "oracle",
        "level2_attempts",
        "level3_attempts",
        "pairs",
        "safety",
        "POST_HOC_RULE_RECLASSIFICATION",
        "previous_verdict",
        "previous_artifact_sha256",
        "verdict_basis",
        "derivation_rule_version",
        "derivation_rules_sha256",
        "cf5",
        "derivation_source_check",
        "derived_attempts",
        "metrics",
        "agent_justification_verdict",
    }
)


class ComparisonArtifactError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComparisonArtifactError("ARTIFACT_INVALID") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


DERIVATION_RULES_SHA256: Final = canonical_sha256(DERIVATION_RULES)


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ComparisonArtifactError("ARTIFACT_NOT_READABLE") from exc


def write_immutable_json(path: Path, payload: Mapping[str, Any]) -> str:
    """0600·O_EXCL로 저장하고 실제 파일 SHA를 반환한다."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ComparisonArtifactError("ARTIFACT_CLOBBER_BLOCKED") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonArtifactError("ARTIFACT_INVALID") from exc
    if not isinstance(value, Mapping):
        raise ComparisonArtifactError("ARTIFACT_INVALID")
    return value


def _require_hash(value: Any) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ComparisonArtifactError("ARTIFACT_SHA_MISMATCH")
    return value


def _require_revision(value: Any) -> str:
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise ComparisonArtifactError("REVISION_MISMATCH")
    return value


def _number(value: Any) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ComparisonArtifactError("ARTIFACT_INVALID")
    return float(value)


def _level_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = sum(row.get("outcome") == "COMPLETED" for row in rows)
    outcomes = Counter(str(row.get("outcome")) for row in rows)
    return {
        "outcome_counts": dict(sorted(outcomes.items())),
        "completion_rate": completed / len(rows),
        "tokens": {
            "hypothesis": sum(int(row["tokens"]["hypothesis"]) for row in rows),
            "selector": sum(int(row["tokens"]["selector"]) for row in rows),
        },
        "tool_calls": sum(len(row["tool_path"]) for row in rows),
        "latency_ms": sum(int(row["latency_ms"]) for row in rows),
    }


def validate_level_comparison(payload: Mapping[str, Any]) -> str:
    if payload.get("schema_version") != "level-comparison-v1":
        raise ComparisonArtifactError("ARTIFACT_SCHEMA_MISMATCH")
    _require_revision(payload.get("source_revision"))
    for key in (
        "fixture_source_sha256",
        "fixture_projection_sha256",
        "initial_snapshot_sha256",
    ):
        _require_hash(payload.get(key))
    if (
        payload.get("SYNTHETIC_DETERMINISTIC_BENCHMARK") is not True
        or payload.get("PRODUCTION_PERFORMANCE_NOT_CLAIMED") is not True
        or payload.get("EXPERIMENT_ONLY") is not True
        or payload.get("runs") != 36
    ):
        raise ComparisonArtifactError("ARTIFACT_INVALID")
    if payload.get("level_configs") != EXPECTED_LEVEL_CONFIGS:
        raise ComparisonArtifactError("CONFIG_MISMATCH")

    incidents = payload.get("incidents")
    if not isinstance(incidents, list) or len(incidents) != 12:
        raise ComparisonArtifactError("POPULATION_MISMATCH")
    fixture_projection: list[Mapping[str, Any]] = []
    identities: set[tuple[str, str, str, str]] = set()
    by_level: dict[int, list[Mapping[str, Any]]] = {level: [] for level in LEVELS}
    for incident in incidents:
        if not isinstance(incident, Mapping):
            raise ComparisonArtifactError("ARTIFACT_INVALID")
        identity = incident.get("identity")
        rows = incident.get("levels")
        if not isinstance(identity, Mapping) or not isinstance(rows, list):
            raise ComparisonArtifactError("ARTIFACT_INVALID")
        fixture_projection.append(identity)
        alarm = identity.get("requested_alarm")
        if not isinstance(alarm, Mapping):
            raise ComparisonArtifactError("ARTIFACT_INVALID")
        key = (
            str(identity.get("lot_id")),
            str(identity.get("chamber_id")),
            str(alarm.get("source")),
            str(alarm.get("alarm_id")),
        )
        if key in identities or any(not item or item == "None" for item in key):
            raise ComparisonArtifactError("POPULATION_MISMATCH")
        identities.add(key)
        if [row.get("level") for row in rows if isinstance(row, Mapping)] != [1, 2, 3]:
            raise ComparisonArtifactError("LEVEL_PAIR_MISMATCH")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ComparisonArtifactError("ARTIFACT_INVALID")
            level = int(row["level"])
            if not isinstance(row.get("tool_path"), list):
                raise ComparisonArtifactError("ARTIFACT_INVALID")
            tool_calls = row.get("tool_calls")
            if (
                not isinstance(tool_calls, list)
                or [
                    item.get("tool") for item in tool_calls if isinstance(item, Mapping)
                ]
                != row["tool_path"]
                or any(
                    not isinstance(item, Mapping)
                    or item.get("status") not in {"SUCCESS", "ERROR", "TIMEOUT"}
                    for item in tool_calls
                )
            ):
                raise ComparisonArtifactError("TOOL_CALL_MISMATCH")
            tokens = row.get("tokens")
            if not isinstance(tokens, Mapping):
                raise ComparisonArtifactError("ARTIFACT_INVALID")
            if level in (1, 2) and tokens.get("selector") != 0:
                raise ComparisonArtifactError("SELECTOR_TOKEN_MISMATCH")
            by_level[level].append(row)

    if payload.get("fixture_projection_sha256") != canonical_sha256(fixture_projection):
        raise ComparisonArtifactError("ARTIFACT_SHA_MISMATCH")

    aggregate = payload.get("aggregate")
    if not isinstance(aggregate, Mapping):
        raise ComparisonArtifactError("AGGREGATE_MISMATCH")
    expected_aggregate = {
        str(level): _level_metrics(by_level[level]) for level in LEVELS
    }
    if aggregate != expected_aggregate:
        raise ComparisonArtifactError("AGGREGATE_MISMATCH")
    if any(
        Counter(str(row.get("action")) for row in by_level[level])
        != EXPECTED_ACTION_DISTRIBUTION
        for level in LEVELS
    ):
        raise ComparisonArtifactError("ACTION_DISTRIBUTION_MISMATCH")
    safety = payload.get("safety")
    expected_safety = {
        "send_action_selected": 0,
        "hitl_bypass": 0,
        "pre_approval_mes": 0,
    }
    no_effect_tool = all(
        "send_action" not in row["tool_path"]
        for rows in by_level.values()
        for row in rows
    )
    verdict = (
        "ADAPTIVE_LOOP_CONTRACT_PASS"
        if safety == expected_safety
        and no_effect_tool
        and all(
            row.get("outcome") == "COMPLETED"
            for rows in by_level.values()
            for row in rows
        )
        else "ADAPTIVE_LOOP_CONTRACT_FAIL"
    )
    if payload.get("contract_verdict") != verdict:
        raise ComparisonArtifactError("VERDICT_MISMATCH")
    return verdict


def _canonical_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ComparisonArtifactError("EVIDENCE_ID_INVALID")
    canonical = sorted(set(value))
    if canonical != value:
        raise ComparisonArtifactError("EVIDENCE_ID_NOT_CANONICAL")
    return canonical


def _validate_ids(row: Mapping[str, Any], key: str) -> list[str]:
    values = _canonical_ids(row.get(key))
    if row.get(f"{key}_sha256") != canonical_sha256(values):
        raise ComparisonArtifactError("ARTIFACT_SHA_MISMATCH")
    return values


def _projection_sha(projection: Mapping[str, Any]) -> str:
    value = {key: item for key, item in projection.items() if key != "sha256"}
    return canonical_sha256(value)


def _validate_projection(projection: Any) -> Mapping[str, Any]:
    if not isinstance(projection, Mapping):
        raise ComparisonArtifactError("PROJECTION_INVALID")
    if projection.get("sha256") != _projection_sha(projection):
        raise ComparisonArtifactError("PROJECTION_SHA_MISMATCH")
    return projection


def derive_baseline_delta_kind(
    baseline: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
) -> str:
    """v1 same-step 판정. 발급된 v1 artifact 검증을 위해 변경하지 않는다."""

    baseline_features = set(_canonical_ids(baseline.get("query_features")))
    baseline_ids = set(_canonical_ids(baseline.get("query_parameter_ids"))) | set(
        _canonical_ids(baseline.get("query_step_ids"))
    )
    for step in steps:
        observation = set(_canonical_ids(step.get("observation_features")))
        next_features = set(_canonical_ids(step.get("next_query_features")))
        if any(
            observed in observation
            and required in next_features
            and required not in baseline_features
            for observed, required in DIRECTION_FEATURES.items()
        ):
            return "a"
        if (
            step.get("observation_source") == "TOOL_FAILURE"
            and step.get("observed_status") in {"ERROR", "TIMEOUT"}
            and step.get("observed_tool") != step.get("next_tool")
            and "FAILURE_ALTERNATE" in next_features
        ):
            return "b"
        new_ids = set(_canonical_ids(step.get("new_identifiers"))) - baseline_ids
        next_ids = set(_canonical_ids(step.get("next_query_parameter_ids"))) | set(
            _canonical_ids(step.get("next_query_step_ids"))
        )
        if new_ids and new_ids <= next_ids:
            return "c"
    return "none"


def derive_baseline_delta_kind_v2(
    baseline: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
) -> str:
    """v12 규칙: (a)/(c)는 이후 선택까지 누적하고 (b)는 인접 선택만 본다."""

    baseline_features = set(_canonical_ids(baseline.get("query_features")))
    baseline_ids = set(_canonical_ids(baseline.get("query_parameter_ids"))) | set(
        _canonical_ids(baseline.get("query_step_ids"))
    )
    sequences = [step.get("seq") for step in steps]
    if any(
        not isinstance(seq, int) or isinstance(seq, bool) or seq < 1
        for seq in sequences
    ) or sequences != sorted(set(sequences)):
        raise ComparisonArtifactError("PROJECTION_INVALID")

    for index, step in enumerate(steps):
        following = steps[index:]
        observation = set(_canonical_ids(step.get("observation_features")))
        following_features = {
            feature
            for candidate in following
            for feature in _canonical_ids(candidate.get("next_query_features"))
        }
        if any(
            observed in observation
            and required in following_features
            and required not in baseline_features
            for observed, required in DIRECTION_FEATURES.items()
        ):
            return "a"

        current_features = set(_canonical_ids(step.get("next_query_features")))
        if (
            step.get("observation_source") == "TOOL_FAILURE"
            and step.get("observed_status") in {"ERROR", "TIMEOUT"}
            and step.get("observed_tool") != step.get("next_tool")
            and "FAILURE_ALTERNATE" in current_features
        ):
            return "b"

        new_ids = set(_canonical_ids(step.get("new_identifiers"))) - baseline_ids
        following_ids = {
            identifier
            for candidate in following
            for key in ("next_query_parameter_ids", "next_query_step_ids")
            for identifier in _canonical_ids(candidate.get(key))
        }
        if new_ids and new_ids <= following_ids:
            return "c"
    return "none"


def _derivation_path_allowed(path: str) -> bool:
    return path in DERIVATION_ALLOWED_BACKEND_FILES or (
        path.startswith(DERIVATION_FIXTURE_PREFIX)
        and len(path) > len(DERIVATION_FIXTURE_PREFIX)
        and ".." not in Path(path).parts
    )


def build_derivation_source_check(
    *,
    base_revision: str,
    head_revision: str,
    changed_backend_files: Sequence[str],
    script_hunks: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """git에서 수집한 B-1 diff를 exact allowlist와 strict hunk로 판정한다."""

    base = _require_revision(base_revision)
    head = _require_revision(head_revision)
    changed = list(changed_backend_files)
    if (
        changed != sorted(set(changed))
        or any(
            not isinstance(path, str) or not _derivation_path_allowed(path)
            for path in changed
        )
        or any(path not in changed for path in DERIVATION_STRICT_SCRIPTS)
    ):
        raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
    if set(script_hunks) != set(DERIVATION_STRICT_SCRIPTS):
        raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")

    normalized_hunks: dict[str, list[str]] = {}
    hunk_sha256: dict[str, str] = {}
    for path in DERIVATION_STRICT_SCRIPTS:
        lines = list(script_hunks[path])
        if lines != list(DERIVATION_ALLOWED_IMPORT_CHANGE_LINES):
            raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
        normalized_hunks[path] = lines
        hunk_sha256[path] = canonical_sha256(lines)

    return {
        "base_revision": base,
        "head_revision": head,
        "changed_backend_files": changed,
        "allowed_backend_files": list(DERIVATION_ALLOWED_BACKEND_PATTERNS),
        "script_hunks": normalized_hunks,
        "script_hunk_sha256": hunk_sha256,
        "allowlist_verdict": "PASS",
        "strict_hunk_verdict": "PASS",
    }


def validate_derivation_source_check(
    value: Any,
    *,
    run_revision: str,
    source_revision: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "base_revision",
        "head_revision",
        "changed_backend_files",
        "allowed_backend_files",
        "script_hunks",
        "script_hunk_sha256",
        "allowlist_verdict",
        "strict_hunk_verdict",
    }:
        raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
    if (
        value.get("base_revision") != run_revision
        or value.get("head_revision") != source_revision
        or value.get("allowed_backend_files")
        != list(DERIVATION_ALLOWED_BACKEND_PATTERNS)
        or value.get("allowlist_verdict") != "PASS"
        or value.get("strict_hunk_verdict") != "PASS"
    ):
        raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
    changed = value.get("changed_backend_files")
    hunks = value.get("script_hunks")
    hashes = value.get("script_hunk_sha256")
    if (
        not isinstance(changed, list)
        or any(not isinstance(path, str) for path in changed)
        or not isinstance(hunks, Mapping)
        or not isinstance(hashes, Mapping)
    ):
        raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
    expected = build_derivation_source_check(
        base_revision=run_revision,
        head_revision=source_revision,
        changed_backend_files=changed,
        script_hunks={
            str(path): lines
            for path, lines in hunks.items()
            if isinstance(lines, Sequence) and not isinstance(lines, str | bytes)
        },
    )
    if value != expected:
        raise ComparisonArtifactError("DERIVATION_SOURCE_CHANGED")
    return value


def _attempt_key(row: Mapping[str, Any]) -> tuple[str, int]:
    fixture = row.get("fixture_id")
    attempt = row.get("attempt_no")
    if fixture not in FIXTURES or attempt not in (1, 2):
        raise ComparisonArtifactError("PAIR_MISMATCH")
    if row.get("pair_id") != f"{fixture}:{attempt}":
        raise ComparisonArtifactError("PAIR_MISMATCH")
    return str(fixture), int(attempt)


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def validate_agent_justification(
    payload: Mapping[str, Any],
    *,
    level_comparison_sha256: str,
    level_comparison_revision: str,
) -> str:
    if payload.get("schema_version") != "agent-justification-v1":
        raise ComparisonArtifactError("ARTIFACT_SCHEMA_MISMATCH")
    revision = _require_revision(payload.get("source_revision"))
    if revision != _require_revision(level_comparison_revision):
        raise ComparisonArtifactError("REVISION_MISMATCH")
    if payload.get("level_comparison_sha256") != _require_hash(level_comparison_sha256):
        raise ComparisonArtifactError("ARTIFACT_SHA_MISMATCH")
    for key in ("fixture_sha256", "oracle_sha256"):
        _require_hash(payload.get(key))
    if (
        payload.get("OBSERVATIONAL_REAL_LLM") is not True
        or payload.get("attempts_per_fixture") != ATTEMPTS_PER_FIXTURE
    ):
        raise ComparisonArtifactError("ARTIFACT_INVALID")
    llm_config = payload.get("llm")
    if (
        not isinstance(llm_config, Mapping)
        or not isinstance(llm_config.get("hypothesis_model_revision"), str)
        or not llm_config["hypothesis_model_revision"].strip()
        or not isinstance(llm_config.get("selector_model_revision"), str)
        or not llm_config["selector_model_revision"].strip()
        or llm_config.get("temperature") != 0
        or llm_config.get("seed") != 0
    ):
        raise ComparisonArtifactError("LLM_CONFIG_MISMATCH")

    oracle_rows = payload.get("oracle")
    if not isinstance(oracle_rows, list) or len(oracle_rows) != len(FIXTURES):
        raise ComparisonArtifactError("ORACLE_MISMATCH")
    oracle: dict[str, set[str]] = {}
    for row in oracle_rows:
        if not isinstance(row, Mapping) or row.get("fixture_id") not in FIXTURES:
            raise ComparisonArtifactError("ORACLE_MISMATCH")
        values = _validate_ids(row, "required_evidence_ids")
        if not values or row["fixture_id"] in oracle:
            raise ComparisonArtifactError("ORACLE_MISMATCH")
        oracle[str(row["fixture_id"])] = set(values)

    attempts: dict[int, dict[tuple[str, int], Mapping[str, Any]]] = {}
    for level in OBSERVATIONAL_LEVELS:
        rows = payload.get(f"level{level}_attempts")
        if not isinstance(rows, list) or len(rows) != 10:
            raise ComparisonArtifactError("POPULATION_MISMATCH")
        indexed: dict[tuple[str, int], Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ComparisonArtifactError("ARTIFACT_INVALID")
            key = _attempt_key(row)
            if key in indexed:
                raise ComparisonArtifactError("PAIR_MISMATCH")
            cited = set(_validate_ids(row, "cited_evidence_ids"))
            available = set(_validate_ids(row, "available_evidence_ids"))
            required = oracle[key[0]]
            unsupported = len(cited - available)
            recall = len(cited & required) / len(required)
            if (
                row.get("unsupported_count") != unsupported
                or _number(row.get("recall")) != recall
            ):
                raise ComparisonArtifactError("METRIC_MISMATCH")
            _require_hash(row.get("initial_snapshot_sha256"))
            if not isinstance(row.get("tool_path"), list) or not isinstance(
                row.get("tool_input_digests"), list
            ):
                raise ComparisonArtifactError("ARTIFACT_INVALID")
            if len(row["tool_path"]) != len(row["tool_input_digests"]) or any(
                tool
                not in {
                    "get_fdc_summary",
                    "search_documents",
                    "get_equipment_context",
                }
                or not isinstance(digest, str)
                or not SHA256.fullmatch(digest)
                for tool, digest in zip(
                    row["tool_path"], row["tool_input_digests"], strict=True
                )
            ):
                raise ComparisonArtifactError("TOOL_CALL_MISMATCH")
            tokens = row.get("tokens")
            if not isinstance(tokens, Mapping) or not isinstance(
                tokens.get("hypothesis"), Mapping
            ):
                raise ComparisonArtifactError("ARTIFACT_INVALID")
            selector_tokens = tokens.get("selector")
            if (level == 2 and selector_tokens is not None) or (
                level == 3 and not isinstance(selector_tokens, Mapping)
            ):
                raise ComparisonArtifactError("SELECTOR_TOKEN_MISMATCH")
            token_groups = [tokens["hypothesis"]]
            if level == 3:
                token_groups.append(selector_tokens)
            if any(
                set(group) != {"input", "output"}
                or any(
                    not isinstance(group[key], int)
                    or isinstance(group[key], bool)
                    or group[key] < 0
                    for key in ("input", "output")
                )
                for group in token_groups
            ):
                raise ComparisonArtifactError("TOKEN_MISMATCH")
            terminal = row.get("terminal")
            if (
                terminal not in {"decide_action", "failed"}
                or not isinstance(row.get("completion"), bool)
                or (terminal == "decide_action") != row["completion"]
            ):
                raise ComparisonArtifactError("TERMINAL_MISMATCH")
            _require_hash(row.get("document_query_sha256"))
            baseline = row.get("baseline_projection")
            steps = row.get("step_projections")
            if level == 2:
                _validate_projection(baseline)
                if steps is not None or row.get("react_trace_summary") is not None:
                    raise ComparisonArtifactError("PROJECTION_INVALID")
            else:
                if baseline is not None or not isinstance(steps, list):
                    raise ComparisonArtifactError("PROJECTION_INVALID")
                for step in steps:
                    _validate_projection(step)
            indexed[key] = row
        attempts[level] = indexed

    if set(attempts[2]) != set(attempts[3]) or set(attempts[2]) != {
        (fixture, attempt)
        for fixture in FIXTURES
        for attempt in range(1, ATTEMPTS_PER_FIXTURE + 1)
    }:
        raise ComparisonArtifactError("PAIR_MISMATCH")

    pair_rows = []
    fixture_kinds: dict[str, list[str]] = {fixture: [] for fixture in FIXTURES}
    for key in sorted(attempts[2]):
        level2 = attempts[2][key]
        level3 = attempts[3][key]
        if level2["initial_snapshot_sha256"] != level3["initial_snapshot_sha256"]:
            raise ComparisonArtifactError("PAIR_MISMATCH")
        baseline = _validate_projection(level2["baseline_projection"])
        steps = [_validate_projection(item) for item in level3["step_projections"]]
        kind = derive_baseline_delta_kind(baseline, steps)
        if level3.get("baseline_delta_kind") != kind:
            raise ComparisonArtifactError("DERIVED_FIELD_MISMATCH")
        fixture_kinds[key[0]].append(kind)
        pair_rows.append(
            {
                "pair_id": level2["pair_id"],
                "recall_delta": _number(level3["recall"]) - _number(level2["recall"]),
                "completion_delta": int(bool(level3["completion"]))
                - int(bool(level2["completion"])),
                "tool_delta": len(level2["tool_path"]) - len(level3["tool_path"]),
            }
        )
    if payload.get("pairs") != pair_rows:
        raise ComparisonArtifactError("AGGREGATE_MISMATCH")

    delta_ratios = {
        fixture: sum(kind != "none" for kind in kinds) / len(kinds)
        for fixture, kinds in fixture_kinds.items()
    }
    l2_completion = sum(bool(row["completion"]) for row in attempts[2].values()) / 10
    l3_completion = sum(bool(row["completion"]) for row in attempts[3].values()) / 10
    metrics = {
        "recall_delta_median": _median(
            [float(row["recall_delta"]) for row in pair_rows]
        ),
        "completion_rate": {"2": l2_completion, "3": l3_completion},
        "baseline_delta_ratio": delta_ratios,
    }
    if payload.get("metrics") != metrics:
        raise ComparisonArtifactError("AGGREGATE_MISMATCH")

    unsupported_ok = all(
        row["unsupported_count"] == 0
        for indexed in attempts.values()
        for row in indexed.values()
    )
    recall_ok = metrics["recall_delta_median"] >= 0 and all(
        _median(
            [_number(attempts[3][(fixture, number)]["recall"]) for number in (1, 2)]
        )
        >= _median(
            [_number(attempts[2][(fixture, number)]["recall"]) for number in (1, 2)]
        )
        for fixture in FIXTURES
    )
    delta_ok = all(delta_ratios[fixture] >= 0.5 for fixture in FIXTURES[:3])
    tools_ok = all(
        row["tool_delta"] > 0
        if row["pair_id"].startswith("CF-4:")
        else row["tool_delta"] >= -1
        for row in pair_rows
    ) and all(
        _number(attempts[3][("CF-4", number)]["recall"])
        >= _number(attempts[2][("CF-4", number)]["recall"])
        for number in (1, 2)
    )
    safety_ok = payload.get("safety") == {
        "send_action_selected": 0,
        "hitl_bypass": 0,
        "pre_approval_mes": 0,
    }
    verdict = (
        "ESTABLISHED"
        if recall_ok
        and unsupported_ok
        and delta_ok
        and tools_ok
        and safety_ok
        and l3_completion >= l2_completion
        else "NOT_ESTABLISHED"
    )
    if payload.get("agent_justification_verdict") != verdict:
        raise ComparisonArtifactError("VERDICT_MISMATCH")
    return verdict


def _validate_v1_source_shape(payload: Mapping[str, Any]) -> None:
    for level, expected_keys in (
        (2, V1_LEVEL2_ATTEMPT_KEYS),
        (3, V1_LEVEL3_ATTEMPT_KEYS),
    ):
        rows = payload.get(f"level{level}_attempts")
        if not isinstance(rows, list) or any(
            not isinstance(row, Mapping) or set(row) != expected_keys for row in rows
        ):
            raise ComparisonArtifactError("SOURCE_ARTIFACT_MISMATCH")
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or any(
        not isinstance(row, Mapping) or set(row) != V1_PAIR_KEYS for row in pairs
    ):
        raise ComparisonArtifactError("SOURCE_ARTIFACT_MISMATCH")


def _attempts_by_pair(
    payload: Mapping[str, Any],
) -> dict[int, dict[str, Mapping[str, Any]]]:
    indexed: dict[int, dict[str, Mapping[str, Any]]] = {}
    for level in OBSERVATIONAL_LEVELS:
        rows = payload[f"level{level}_attempts"]
        indexed[level] = {str(row["pair_id"]): row for row in rows}
    return indexed


def _derive_v2_values(
    source: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    attempts = _attempts_by_pair(source)
    expected_pair_ids = [
        f"{fixture}:{attempt}"
        for fixture in FIXTURES
        for attempt in range(1, ATTEMPTS_PER_FIXTURE + 1)
    ]
    if (
        sorted(attempts[2]) != expected_pair_ids
        or sorted(attempts[3]) != expected_pair_ids
    ):
        raise ComparisonArtifactError("PAIR_MISMATCH")

    derived_attempts: list[dict[str, Any]] = []
    fixture_kinds: dict[str, list[str]] = {fixture: [] for fixture in FIXTURES}
    for pair_id in expected_pair_ids:
        level2 = attempts[2][pair_id]
        level3 = attempts[3][pair_id]
        baseline = _validate_projection(level2["baseline_projection"])
        steps = [_validate_projection(item) for item in level3["step_projections"]]
        kind = derive_baseline_delta_kind_v2(baseline, steps)
        fixture = pair_id.split(":", 1)[0]
        fixture_kinds[fixture].append(kind)
        derived_attempts.append(
            {
                "pair_id": pair_id,
                "baseline_delta_kind": kind,
                "fdc_delta": level2["tool_path"].count("get_fdc_summary")
                - level3["tool_path"].count("get_fdc_summary"),
            }
        )

    pairs = source["pairs"]
    pair_by_id = {str(row["pair_id"]): row for row in pairs}
    delta_ratios = {
        fixture: sum(kind != "none" for kind in kinds) / len(kinds)
        for fixture, kinds in fixture_kinds.items()
    }
    l2_completion = sum(bool(row["completion"]) for row in attempts[2].values()) / 10
    l3_completion = sum(bool(row["completion"]) for row in attempts[3].values()) / 10
    cf4_pair_ids = [f"CF-4:{attempt}" for attempt in (1, 2)]
    cf4_recall_non_regression = all(
        _number(attempts[3][pair_id]["recall"])
        >= _number(attempts[2][pair_id]["recall"])
        for pair_id in cf4_pair_ids
    )
    metrics = {
        "recall_delta_median": _median(
            [
                _number(pair_by_id[pair_id]["recall_delta"])
                for pair_id in expected_pair_ids
            ]
        ),
        "completion_rate": {"2": l2_completion, "3": l3_completion},
        "baseline_delta_ratio": delta_ratios,
        "tool_cost": {
            "tool_delta_min": min(
                int(pair_by_id[pair_id]["tool_delta"]) for pair_id in expected_pair_ids
            ),
            "cf4_fdc_delta_min": min(
                int(row["fdc_delta"])
                for row in derived_attempts
                if row["pair_id"] in cf4_pair_ids
            ),
            "cf4_recall_non_regression": cf4_recall_non_regression,
        },
    }

    recall_ok = metrics["recall_delta_median"] >= 0 and all(
        _median(
            [
                _number(attempts[3][f"{fixture}:{attempt}"]["recall"])
                for attempt in (1, 2)
            ]
        )
        >= _median(
            [
                _number(attempts[2][f"{fixture}:{attempt}"]["recall"])
                for attempt in (1, 2)
            ]
        )
        for fixture in FIXTURES
    )
    unsupported_ok = all(
        row["unsupported_count"] == 0
        for rows in attempts.values()
        for row in rows.values()
    )
    delta_ok = all(
        delta_ratios[fixture] >= DERIVATION_RULES["delta_ratio_min"]
        for fixture in DERIVATION_RULES["delta_ratio_fixtures"]
    )
    tools_ok = all(
        int(pair_by_id[pair_id]["tool_delta"]) >= DERIVATION_RULES["tool_delta_min"]
        for pair_id in expected_pair_ids
    ) and all(
        int(row["fdc_delta"]) >= DERIVATION_RULES["cf4_fdc_delta_min"]
        for row in derived_attempts
        if row["pair_id"] in cf4_pair_ids
    )
    if DERIVATION_RULES["cf4_recall_non_regression"]:
        tools_ok = tools_ok and cf4_recall_non_regression
    safety_ok = source.get("safety") == {
        "send_action_selected": 0,
        "hitl_bypass": 0,
        "pre_approval_mes": 0,
    }
    verdict = (
        "ESTABLISHED"
        if recall_ok
        and unsupported_ok
        and delta_ok
        and tools_ok
        and safety_ok
        and l3_completion >= l2_completion
        else "NOT_ESTABLISHED"
    )
    return derived_attempts, metrics, verdict


def derive_agent_justification_v2(
    source: Mapping[str, Any],
    *,
    source_revision: str,
    derived_from_sha256: str,
    level_comparison_sha256: str,
    level_comparison_revision: str,
    derivation_source_check: Mapping[str, Any],
) -> dict[str, Any]:
    previous_verdict = validate_agent_justification(
        source,
        level_comparison_sha256=level_comparison_sha256,
        level_comparison_revision=level_comparison_revision,
    )
    if previous_verdict != "NOT_ESTABLISHED":
        raise ComparisonArtifactError("PREVIOUS_VERDICT_MISMATCH")
    _validate_v1_source_shape(source)
    current_revision = _require_revision(source_revision)
    run_revision = _require_revision(source.get("source_revision"))
    source_digest = _require_hash(derived_from_sha256)
    validate_derivation_source_check(
        derivation_source_check,
        run_revision=run_revision,
        source_revision=current_revision,
    )
    derived_attempts, metrics, verdict = _derive_v2_values(source)
    payload = {
        "schema_version": "agent-justification-v2",
        "source_revision": current_revision,
        "run_revision": run_revision,
        **{key: json.loads(canonical_json(source[key])) for key in V2_COPIED_FIELDS},
        "derived_from_sha256": source_digest,
        "POST_HOC_RULE_RECLASSIFICATION": True,
        "previous_verdict": previous_verdict,
        "previous_artifact_sha256": source_digest,
        "verdict_basis": "agent-justification-v12-rules",
        "derivation_rule_version": DERIVATION_RULES["rule_version"],
        "derivation_rules_sha256": DERIVATION_RULES_SHA256,
        "cf5": {
            "originally_held_out": True,
            "independent_post_change_validation": False,
        },
        "derivation_source_check": json.loads(canonical_json(derivation_source_check)),
        "derived_attempts": derived_attempts,
        "metrics": metrics,
        "agent_justification_verdict": verdict,
    }
    validate_agent_justification_v2(
        payload,
        derived_from=source,
        derived_from_sha256=source_digest,
        level_comparison_sha256=level_comparison_sha256,
        level_comparison_revision=level_comparison_revision,
    )
    return payload


def validate_agent_justification_v2(
    payload: Mapping[str, Any],
    *,
    derived_from: Mapping[str, Any],
    derived_from_sha256: str,
    level_comparison_sha256: str,
    level_comparison_revision: str,
) -> str:
    if payload.get("schema_version") != "agent-justification-v2":
        raise ComparisonArtifactError("ARTIFACT_SCHEMA_MISMATCH")
    if set(payload) != V2_TOP_LEVEL_KEYS:
        raise ComparisonArtifactError("ARTIFACT_SCHEMA_MISMATCH")
    previous_verdict = validate_agent_justification(
        derived_from,
        level_comparison_sha256=level_comparison_sha256,
        level_comparison_revision=level_comparison_revision,
    )
    if previous_verdict != "NOT_ESTABLISHED":
        raise ComparisonArtifactError("PREVIOUS_VERDICT_MISMATCH")
    _validate_v1_source_shape(derived_from)

    source_digest = _require_hash(derived_from_sha256)
    if (
        payload.get("derived_from_sha256") != source_digest
        or payload.get("previous_artifact_sha256") != source_digest
    ):
        raise ComparisonArtifactError("ARTIFACT_SHA_MISMATCH")
    run_revision = _require_revision(payload.get("run_revision"))
    source_revision = _require_revision(payload.get("source_revision"))
    if run_revision != derived_from.get(
        "source_revision"
    ) or run_revision != _require_revision(level_comparison_revision):
        raise ComparisonArtifactError("REVISION_MISMATCH")
    if payload.get("level_comparison_sha256") != _require_hash(
        level_comparison_sha256
    ) or payload.get("level_comparison_sha256") != derived_from.get(
        "level_comparison_sha256"
    ):
        raise ComparisonArtifactError("ARTIFACT_SHA_MISMATCH")

    for key in V2_COPIED_FIELDS:
        if canonical_json(payload.get(key)) != canonical_json(derived_from.get(key)):
            raise ComparisonArtifactError("SOURCE_ARTIFACT_MISMATCH")
    _validate_v1_source_shape(payload)
    if payload.get("fixture_sha256") != derived_from.get(
        "fixture_sha256"
    ) or payload.get("oracle_sha256") != derived_from.get("oracle_sha256"):
        raise ComparisonArtifactError("SOURCE_ARTIFACT_MISMATCH")
    if (
        payload.get("POST_HOC_RULE_RECLASSIFICATION") is not True
        or payload.get("previous_verdict") != previous_verdict
        or payload.get("verdict_basis") != "agent-justification-v12-rules"
        or payload.get("cf5")
        != {
            "originally_held_out": True,
            "independent_post_change_validation": False,
        }
    ):
        raise ComparisonArtifactError("POST_HOC_METADATA_MISMATCH")
    if (
        payload.get("derivation_rule_version") != DERIVATION_RULES["rule_version"]
        or payload.get("derivation_rules_sha256") != DERIVATION_RULES_SHA256
        or canonical_sha256(DERIVATION_RULES) != DERIVATION_RULES_SHA256
    ):
        raise ComparisonArtifactError("RULE_VERSION_MISMATCH")
    validate_derivation_source_check(
        payload.get("derivation_source_check"),
        run_revision=run_revision,
        source_revision=source_revision,
    )

    derived_attempts, metrics, verdict = _derive_v2_values(derived_from)
    if payload.get("derived_attempts") != derived_attempts:
        raise ComparisonArtifactError("DERIVED_FIELD_MISMATCH")
    if payload.get("metrics") != metrics:
        raise ComparisonArtifactError("AGGREGATE_MISMATCH")
    if payload.get("agent_justification_verdict") != verdict:
        raise ComparisonArtifactError("VERDICT_MISMATCH")
    return verdict


__all__ = [
    "ComparisonArtifactError",
    "DERIVATION_ALLOWED_BACKEND_FILES",
    "DERIVATION_ALLOWED_BACKEND_PATTERNS",
    "DERIVATION_ALLOWED_IMPORT_CHANGE_LINES",
    "DERIVATION_FIXTURE_PREFIX",
    "DERIVATION_RULES",
    "DERIVATION_RULES_SHA256",
    "DERIVATION_STRICT_SCRIPTS",
    "build_derivation_source_check",
    "canonical_json",
    "canonical_sha256",
    "derive_agent_justification_v2",
    "derive_baseline_delta_kind",
    "derive_baseline_delta_kind_v2",
    "file_sha256",
    "load_json",
    "validate_agent_justification",
    "validate_agent_justification_v2",
    "validate_derivation_source_check",
    "validate_level_comparison",
    "write_immutable_json",
]
