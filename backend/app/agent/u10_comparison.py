"""V5-C-7.1 U10 offline comparison contract (not a production enable gate).

The caller must supply the independently pinned fixture inventory. This module
does not query a database, invoke an LLM, or attest that an execution occurred.
Historical comparison v1/v2 artifacts continue to use comparison.py unchanged.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    digest,
)

Count = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Policy = Literal["FIXED_POLICY_V21", "REACT_V2"]
Slot = Literal[
    "CURRENT_FDC",
    "ADJACENT_FDC",
    "EQUIPMENT",
    "HISTORY",
    "SIBLING",
    "METROLOGY",
    "DOCUMENT_1",
    "DOCUMENT_2",
]
Availability = Literal["AVAILABLE", "NOT_AVAILABLE"]
FIXTURE_IDS = tuple(f"CF-{i}" for i in range(1, 9))
POLICIES = ("FIXED_POLICY_V21", "REACT_V2")
SLOTS = (
    "CURRENT_FDC",
    "ADJACENT_FDC",
    "EQUIPMENT",
    "HISTORY",
    "SIBLING",
    "METROLOGY",
    "DOCUMENT_1",
    "DOCUMENT_2",
)
TOOLS = {
    "CURRENT_FDC": "get_fdc_summary",
    "ADJACENT_FDC": "get_fdc_summary",
    "EQUIPMENT": "get_equipment_context",
    "HISTORY": "get_chamber_parameter_history",
    "SIBLING": "get_chamber_parameter_history",
    "METROLOGY": "get_metrology_result",
    "DOCUMENT_1": "search_documents",
    "DOCUMENT_2": "search_documents",
}
U10_VERDICT_RULES = {
    "version": "u10-v21",
    "fixtures": list(FIXTURE_IDS),
    "attempts_per_fixture": 2,
    "policies": list(POLICIES),
    "budget": {"total": 10, "read": 8, "send": 2, "same_tool": 4},
    "retry_per_selection": 1,
    "selector_step_cap": 10,
    "recall_non_regression": 0,
    "efficiency": {"reads": 1, "token_ratio": 0.10, "latency_ratio": 0.25},
    "quality": {"mean_recall_delta": 0.125, "token_ratio": 0.50, "latency_ratio": 0.50},
    "failures": "aggregate-and-every-fixture-sum-non-regression",
    "reason_priority": ["HARD_GATE_FAIL", "COST_CAP_EXCEEDED", "NO_GAIN"],
}


def rules_sha256() -> str:
    return digest(canonical_json(U10_VERDICT_RULES))


class Dimensions(EvidenceModel):
    """Candidate availability, independent of oracle-required dimensions.

    History has no missing-candidate state: the current chamber can always be
    queried. Fewer than two prior lots means an INSUFFICIENT trend, not
    NOT_AVAILABLE history (including when there are zero prior lots).
    """

    upstream: Availability
    downstream: Availability
    sibling: Availability
    history: Availability
    metrology: Availability


class Compared(EvidenceModel):
    upstream: Literal["CHECKED", "NOT_CHECKED", "NOT_AVAILABLE"]
    downstream: Literal["CHECKED", "NOT_CHECKED", "NOT_AVAILABLE"]
    sibling: Literal["CHECKED", "NOT_CHECKED", "NOT_AVAILABLE"]
    history: Literal["CHECKED", "NOT_CHECKED", "NOT_AVAILABLE"]
    metrology: Literal["CHECKED", "NOT_CHECKED", "NOT_AVAILABLE"]


class Adjacent(EvidenceModel):
    relation: Literal["UPSTREAM", "DOWNSTREAM", "NONE"]
    wafers: Count

    @model_validator(mode="after")
    def relation_count(self) -> Adjacent:
        if (self.relation == "NONE") != (self.wafers == 0):
            raise ValueError("INVENTORY_INVALID")
        return self


class Inventory(EvidenceModel):
    current_wafers: Annotated[int, Field(ge=1)]
    adjacent: Adjacent
    sibling_chamber_id: Identifier | None
    history_prior_lots: Annotated[int, Field(ge=0, le=3)]
    metrology_samples: Count
    documents: Literal[True]

    def dimensions(self) -> dict[str, str]:
        available = {
            "upstream": self.adjacent.relation == "UPSTREAM",
            "downstream": self.adjacent.relation == "DOWNSTREAM",
            "sibling": self.sibling_chamber_id is not None,
            # An empty prior history is still an available query, not a missing tool.
            "history": True,
            "metrology": self.metrology_samples > 0,
        }
        return {k: "AVAILABLE" if v else "NOT_AVAILABLE" for k, v in available.items()}

    def available_slots(self) -> dict[str, bool]:
        return {
            "CURRENT_FDC": True,
            "ADJACENT_FDC": self.adjacent.wafers > 0,
            "EQUIPMENT": True,
            "HISTORY": True,
            "SIBLING": self.sibling_chamber_id is not None,
            "METROLOGY": self.metrology_samples > 0,
            "DOCUMENT_1": True,
            "DOCUMENT_2": True,
        }


class EvidenceIds(EvidenceModel):
    values: list[Identifier]
    sha256: Sha256

    @model_validator(mode="after")
    def canonical(self) -> EvidenceIds:
        if self.values != sorted(set(self.values)):
            raise ValueError("EVIDENCE_IDS_NOT_CANONICAL")
        if digest(canonical_json(self.values)) != self.sha256:
            raise ValueError("EVIDENCE_IDS_SHA_MISMATCH")
        return self


class Fixture(EvidenceModel):
    fixture_id: Identifier
    initial_snapshot_sha256: Sha256
    initial_evidence_ids: EvidenceIds
    candidate_inventory: Inventory
    expected_compared: Dimensions
    required_evidence_ids: EvidenceIds
    oracle_required_dimensions: list[
        Literal["upstream", "downstream", "sibling", "history", "metrology"]
    ]

    @model_validator(mode="after")
    def inventory_contract(self) -> Fixture:
        if self.expected_compared.model_dump() != self.candidate_inventory.dimensions():
            raise ValueError("EXPECTED_COMPARED_MISMATCH")
        if len(self.required_evidence_ids.values) < 2:
            raise ValueError("ORACLE_POPULATION_INVALID")
        if self.oracle_required_dimensions != sorted(
            set(self.oracle_required_dimensions)
        ):
            raise ValueError("ORACLE_DIMENSIONS_INVALID")
        if any(
            self.candidate_inventory.dimensions()[d] != "AVAILABLE"
            for d in self.oracle_required_dimensions
        ):
            raise ValueError("ORACLE_DIMENSIONS_INVALID")
        return self


class Benchmark(EvidenceModel):
    """Pinned input projection; factual DB verification belongs to the runner."""

    schema_version: Literal["u10-benchmark-v1"]
    fixture_sha256: Sha256
    oracle_sha256: Sha256
    inventory_sha256: Sha256
    tool_contract_sha256: Sha256
    fixed_policy_sha256: Sha256
    fixtures: list[Fixture]

    @model_validator(mode="after")
    def exact_population(self) -> Benchmark:
        if [f.fixture_id for f in self.fixtures] != list(FIXTURE_IDS):
            raise ValueError("FIXTURE_POPULATION_INVALID")
        inventory = [
            {
                "fixture_id": f.fixture_id,
                "candidate_inventory": f.candidate_inventory.model_dump(),
                "expected_compared": f.expected_compared.model_dump(),
            }
            for f in self.fixtures
        ]
        oracle = [
            {
                "fixture_id": f.fixture_id,
                "required_evidence_ids": f.required_evidence_ids.model_dump(),
                "oracle_required_dimensions": f.oracle_required_dimensions,
            }
            for f in self.fixtures
        ]
        if self.inventory_sha256 != digest(canonical_json(inventory)):
            raise ValueError("INVENTORY_SHA_MISMATCH")
        if self.oracle_sha256 != digest(canonical_json(oracle)):
            raise ValueError("ORACLE_SHA_MISMATCH")
        return self


class Tokens(EvidenceModel):
    input: Count
    output: Count

    def total(self) -> int:
        return self.input + self.output


class ReadCall(EvidenceModel):
    slot: Slot
    tool: Literal[
        "get_fdc_summary",
        "get_equipment_context",
        "get_chamber_parameter_history",
        "get_metrology_result",
        "search_documents",
    ]
    selection: Annotated[int, Field(ge=1, le=10)]
    retry: Literal[0, 1]
    input_digest: Sha256
    status: Literal["SUCCESS", "ERROR", "TIMEOUT"]
    latency_ms: Count
    evidence_ids: EvidenceIds

    @model_validator(mode="after")
    def tool_slot(self) -> ReadCall:
        if self.tool != TOOLS[self.slot]:
            raise ValueError("TOOL_SLOT_MISMATCH")
        if self.status != "SUCCESS" and self.evidence_ids.values:
            raise ValueError("FAILED_READ_EVIDENCE_INVALID")
        return self


class SkippedSlot(EvidenceModel):
    slot: Slot
    reason: Literal["NO_CANDIDATE"]


def derive_compared(inventory: Inventory, calls: list[ReadCall]) -> Compared:
    """Code-owned inventory matrix shared by attempt builder and verifier."""
    checked = {c.slot for c in calls if c.status == "SUCCESS"}
    slots = {
        "upstream": "ADJACENT_FDC",
        "downstream": "ADJACENT_FDC",
        "sibling": "SIBLING",
        "history": "HISTORY",
        "metrology": "METROLOGY",
    }
    return Compared(
        **{
            dimension: "NOT_AVAILABLE"
            if available == "NOT_AVAILABLE"
            else "CHECKED"
            if slots[dimension] in checked
            else "NOT_CHECKED"
            for dimension, available in inventory.dimensions().items()
        }
    )


class SelectorCall(EvidenceModel):
    tokens: Tokens
    latency_ms: Count


class Safety(EvidenceModel):
    send_action_selected: Count
    hitl_bypass: Count
    pre_approval_mes: Count


class Attempt(EvidenceModel):
    fixture_id: Identifier
    attempt_no: Literal[1, 2]
    policy: Policy
    execution_order: Annotated[int, Field(ge=1, le=32)]
    initial_snapshot_sha256: Sha256
    llm_config_sha256: Sha256
    completion: bool
    action: Literal["MONITORING", "WARNING", "EQP_HOLD"] | None
    external_effects: Count
    safety: Safety
    calls: Annotated[list[ReadCall], Field(max_length=8)]
    skipped_slots: list[SkippedSlot]
    selector: Annotated[list[SelectorCall], Field(max_length=20)]
    hypothesis: list[SelectorCall]
    initial_evidence_ids: EvidenceIds
    available_evidence_ids: EvidenceIds
    cited_evidence_ids: EvidenceIds
    compared: Compared
    read_attempts: Count
    successful_reads: Count
    selector_calls: Count
    selector_tokens: Tokens
    hypothesis_tokens: Tokens
    tool_latency_ms: Count
    selector_latency_ms: Count
    end_to_end_latency_ms: Annotated[int, Field(gt=0)]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceError(code)


def _sum_tokens(calls: list[SelectorCall]) -> dict[str, int]:
    return {k: sum(getattr(c.tokens, k) for c in calls) for k in ("input", "output")}


def _check_attempt(a: Attempt, fixture: Fixture, order: int) -> dict[str, Any]:
    _require(a.execution_order == order, "EXECUTION_ORDER_MISMATCH")
    _require(
        a.initial_snapshot_sha256 == fixture.initial_snapshot_sha256,
        "SNAPSHOT_MISMATCH",
    )
    _require(
        a.initial_evidence_ids == fixture.initial_evidence_ids,
        "INITIAL_EVIDENCE_MISMATCH",
    )
    _require(a.read_attempts == len(a.calls), "READ_ATTEMPTS_MISMATCH")
    counts = Counter(c.status for c in a.calls)
    _require(a.successful_reads == counts["SUCCESS"], "SUCCESSFUL_READS_MISMATCH")
    _require(a.selector_calls == len(a.selector), "SELECTOR_CALLS_MISMATCH")
    _require(
        a.selector_tokens.model_dump() == _sum_tokens(a.selector)
        and a.hypothesis_tokens.model_dump() == _sum_tokens(a.hypothesis),
        "TOKEN_TOTAL_MISMATCH",
    )
    _require(
        a.tool_latency_ms == sum(c.latency_ms for c in a.calls)
        and a.selector_latency_ms == sum(c.latency_ms for c in a.selector),
        "LATENCY_TOTAL_MISMATCH",
    )
    _require(
        a.end_to_end_latency_ms
        >= a.tool_latency_ms
        + a.selector_latency_ms
        + sum(c.latency_ms for c in a.hypothesis),
        "LATENCY_TOTAL_MISMATCH",
    )
    _require(
        max(Counter(c.tool for c in a.calls).values(), default=0) <= 4,
        "TOOL_BUDGET_EXCEEDED",
    )
    available = set(a.initial_evidence_ids.values)
    for index, call in enumerate(a.calls):
        previous = a.calls[index - 1] if index else None
        if call.retry:
            _require(
                previous is not None
                and previous.retry == 0
                and previous.status in ("ERROR", "TIMEOUT")
                and (previous.selection, previous.slot, previous.input_digest)
                == (call.selection, call.slot, call.input_digest),
                "READ_RETRY_INVALID",
            )
        elif previous:
            _require(call.selection > previous.selection, "READ_SELECTION_INVALID")
        if previous and previous.status != "SUCCESS" and previous.retry == 0:
            _require(call.retry == 1, "READ_RETRY_REQUIRED")
        _require(
            fixture.candidate_inventory.available_slots()[call.slot],
            "NO_CANDIDATE_CALL",
        )
        available.update(call.evidence_ids.values)
    if a.calls and len(a.calls) < 8 and a.completion:
        last = a.calls[-1]
        _require(last.status == "SUCCESS" or last.retry == 1, "READ_RETRY_REQUIRED")
    _require(
        sorted(available) == a.available_evidence_ids.values,
        "AVAILABLE_EVIDENCE_MISMATCH",
    )
    if a.policy == "FIXED_POLICY_V21":
        _require(not a.selector, "FIXED_SELECTOR_FORBIDDEN")
        expected_skips = [
            slot
            for slot, ok in fixture.candidate_inventory.available_slots().items()
            if not ok
        ]
        _require(
            [s.slot for s in a.skipped_slots] == expected_skips,
            "SKIPPED_SLOTS_MISMATCH",
        )
        expected_slots = [s for s in SLOTS if s not in expected_skips]
        selections = [c.slot for c in a.calls if c.retry == 0]
        _require(selections == expected_slots[: len(selections)], "FIXED_PATH_MISMATCH")
        if a.completion:
            _require(
                len(a.calls) == 8 or selections == expected_slots,
                "FIXED_PATH_INCOMPLETE",
            )
    else:
        _require(not a.skipped_slots, "REACT_FIXED_SKIPS_FORBIDDEN")
        _require(not a.completion or bool(a.selector), "REACT_SELECTOR_MISSING")
    _require(not a.completion or bool(a.hypothesis), "HYPOTHESIS_USAGE_MISSING")
    expected_compared = derive_compared(
        fixture.candidate_inventory, a.calls
    ).model_dump()
    _require(a.compared.model_dump() == expected_compared, "COMPARED_MISMATCH")
    cited = set(a.cited_evidence_ids.values)
    required = set(fixture.required_evidence_ids.values)
    return {
        "read_attempts": len(a.calls),
        "successful_reads": counts["SUCCESS"],
        "read_fail": {"ERROR": counts["ERROR"], "TIMEOUT": counts["TIMEOUT"]},
        "recall": len(cited & required) / len(required),
        "unsupported_count": len(cited - available),
        "tokens": a.selector_tokens.total() + a.hypothesis_tokens.total(),
    }


def evaluate(benchmark: Benchmark, attempts: list[Attempt]) -> dict[str, Any]:
    """Recalculate all pair metrics and verdict; valid negative results are data."""
    expected_keys = [(f, n, p) for f in FIXTURE_IDS for n in (1, 2) for p in POLICIES]
    keyed = {(a.fixture_id, a.attempt_no, a.policy): a for a in attempts}
    _require(
        len(attempts) == 32 and set(keyed) == set(expected_keys),
        "ATTEMPT_POPULATION_INVALID",
    )
    _require(
        [a.execution_order for a in attempts] == list(range(1, 33)),
        "EXECUTION_ORDER_MISMATCH",
    )
    pairs: list[dict[str, Any]] = []
    failures: list[str] = []
    for pair_index in range(16):
        fixture = benchmark.fixtures[pair_index // 2]
        number = pair_index % 2 + 1
        metrics = {}
        for policy_index, policy in enumerate(POLICIES):
            a = keyed[fixture.fixture_id, number, policy]
            position = policy_index if pair_index % 2 == 0 else 1 - policy_index
            m = _check_attempt(a, fixture, pair_index * 2 + position + 1)
            metrics[policy] = m
            for code, failed in (
                ("EXTERNAL_EFFECTS", a.external_effects != 0),
                ("SAFETY", any(a.safety.model_dump().values())),
                ("UNSUPPORTED_CITATION", m["unsupported_count"] != 0),
                ("COMPLETION", not a.completion),
            ):
                if failed:
                    failures.append(code)
        fixed = keyed[fixture.fixture_id, number, "FIXED_POLICY_V21"]
        react = keyed[fixture.fixture_id, number, "REACT_V2"]
        if fixed.action is None or fixed.action != react.action:
            failures.append("ACTION_MISMATCH")
        f, r = metrics[POLICIES[0]], metrics[POLICIES[1]]
        _require(f["tokens"] > 0, "METRIC_PRECONDITION_INVALID")
        pairs.append(
            {
                "pair_id": f"{fixture.fixture_id}:{number}",
                "fixture_id": fixture.fixture_id,
                "fixed": f,
                "react": r,
                "recall_delta": r["recall"] - f["recall"],
                "read_attempt_delta": f["read_attempts"] - r["read_attempts"],
                "read_fail_delta": sum(f["read_fail"].values())
                - sum(r["read_fail"].values()),
                "tok_ratio": (r["tokens"] - f["tokens"]) / f["tokens"],
                "lat_ratio": (react.end_to_end_latency_ms - fixed.end_to_end_latency_ms)
                / fixed.end_to_end_latency_ms,
            }
        )
    checks: list[dict[str, Any]] = []

    def check(name: str, threshold: float, observed: float, *, maximum=False) -> bool:
        passed = observed <= threshold if maximum else observed >= threshold
        checks.append(
            {"name": name, "threshold": threshold, "observed": observed, "pass": passed}
        )
        return passed

    med = {
        key: median(p[key] for p in pairs)
        for key in ("recall_delta", "read_attempt_delta", "tok_ratio", "lat_ratio")
    }
    non_regression = []
    fixture_metrics = []
    for fixture_id in FIXTURE_IDS:
        subset = [p for p in pairs if p["fixture_id"] == fixture_id]
        fixture_metrics.append(
            {
                "fixture_id": fixture_id,
                "recall_delta_median": median(p["recall_delta"] for p in subset),
                **{
                    policy: {
                        "read_attempts": sum(
                            p[policy]["read_attempts"] for p in subset
                        ),
                        "successful_reads": sum(
                            p[policy]["successful_reads"] for p in subset
                        ),
                        "read_fail": {
                            status: sum(p[policy]["read_fail"][status] for p in subset)
                            for status in ("ERROR", "TIMEOUT")
                        },
                    }
                    for policy in ("fixed", "react")
                },
            }
        )
        non_regression.extend(
            [
                check(
                    f"{fixture_id}.recall", 0, median(p["recall_delta"] for p in subset)
                ),
                check(
                    f"{fixture_id}.read_fail",
                    0,
                    sum(p["read_fail_delta"] for p in subset),
                ),
            ]
        )
    non_regression.append(
        check("aggregate.read_fail", 0, sum(p["read_fail_delta"] for p in pairs))
    )
    efficiency = U10_VERDICT_RULES["efficiency"]
    quality = U10_VERDICT_RULES["quality"]
    efficiency_checks = [
        check("efficiency.recall", 0, med["recall_delta"]),
        check("efficiency.reads", efficiency["reads"], med["read_attempt_delta"]),
        check(
            "efficiency.tokens",
            efficiency["token_ratio"],
            med["tok_ratio"],
            maximum=True,
        ),
        check(
            "efficiency.latency",
            efficiency["latency_ratio"],
            med["lat_ratio"],
            maximum=True,
        ),
    ]
    quality_gain = check(
        "quality.recall",
        quality["mean_recall_delta"],
        mean(p["recall_delta"] for p in pairs),
    )
    quality_costs = [
        check("quality.tokens", quality["token_ratio"], med["tok_ratio"], maximum=True),
        check(
            "quality.latency", quality["latency_ratio"], med["lat_ratio"], maximum=True
        ),
    ]
    branches = {
        "EFFICIENCY_GAIN": all(non_regression + efficiency_checks),
        "QUALITY_GAIN_WITHIN_COST_CAP": all(
            non_regression + [quality_gain] + quality_costs
        ),
    }
    reason = None
    if failures:
        priority = (
            "EXTERNAL_EFFECTS",
            "SAFETY",
            "UNSUPPORTED_CITATION",
            "COMPLETION",
            "ACTION_MISMATCH",
        )
        reason = "HARD_GATE_FAIL:" + next(c for c in priority if c in failures)
    elif not any(branches.values()):
        reason = (
            "COST_CAP_EXCEEDED"
            if (quality_gain and all(non_regression) and not all(quality_costs))
            else "NO_GAIN"
        )
    return {
        "agent_verdict": "AGENT_JUSTIFICATION_"
        + ("ESTABLISHED_V21" if reason is None else "NOT_ESTABLISHED_V21"),
        "verdict_reason": reason,
        "verdict_breakdown": {
            "checks": checks,
            "pairs": pairs,
            "fixtures": fixture_metrics,
            "branches": branches,
        },
    }


class LlmConfiguration(EvidenceModel):
    hypothesis_model_revision: Identifier
    selector_model_revision: Identifier
    hypothesis_prompt_version: Literal["agent-hypothesis-v3-ko1"]
    selector_prompt_version: Literal["agent-react-v2-ko1"]
    temperature: Annotated[float, Field(ge=0, le=0)]
    seed: Count


class Artifact(EvidenceModel):
    schema_version: Literal["u10-comparison-v1"]
    evaluated_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    benchmark_sha256: Sha256
    verdict_rules_sha256: Sha256
    llm: LlmConfiguration
    SYNTHETIC_COUNTERFACTUAL_BENCHMARK: Literal[True]
    PRODUCTION_PERFORMANCE_NOT_CLAIMED: Literal[True]
    EXPERIMENT_ONLY: Literal[True]
    attempts: list[Attempt]
    result: dict[str, Any]


def validate_artifact(payload: Any, benchmark_payload: Any) -> dict[str, Any]:
    """Reject structural/derived drift, but return a negative research verdict."""
    try:
        benchmark = Benchmark.model_validate(benchmark_payload)
        artifact = Artifact.model_validate(payload)
    except ValidationError as exc:
        metric_fields = {
            "end_to_end_latency_ms",
            "tool_latency_ms",
            "selector_latency_ms",
            "tokens",
            "selector_tokens",
            "hypothesis_tokens",
            "latency_ms",
        }
        code = (
            "METRIC_PRECONDITION_INVALID"
            if any(metric_fields.intersection(e["loc"]) for e in exc.errors())
            else "U10_SCHEMA_INVALID"
        )
        raise EvidenceError(code) from exc
    _require(
        artifact.benchmark_sha256 == digest(canonical_json(benchmark)),
        "BENCHMARK_SHA_MISMATCH",
    )
    _require(artifact.verdict_rules_sha256 == rules_sha256(), "RULE_VERSION_MISMATCH")
    _require(
        all(
            a.llm_config_sha256 == digest(canonical_json(artifact.llm))
            for a in artifact.attempts
        ),
        "LLM_CONFIG_MISMATCH",
    )
    result = evaluate(benchmark, artifact.attempts)
    _require(
        canonical_json(result) == canonical_json(artifact.result),
        "VERDICT_RECALCULATION_MISMATCH",
    )
    return result
