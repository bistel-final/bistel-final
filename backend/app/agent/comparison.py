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


__all__ = [
    "ComparisonArtifactError",
    "canonical_json",
    "canonical_sha256",
    "derive_baseline_delta_kind",
    "file_sha256",
    "load_json",
    "validate_agent_justification",
    "validate_level_comparison",
    "write_immutable_json",
]
