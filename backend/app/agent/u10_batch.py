"""U10 in-memory 32-attempt coordinator, not a live CLI or artifact issuer.

Resource isolation, factual snapshots, source checks and a real approval verifier
must be supplied by the caller. No provider, connection or approval is fabricated.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_attempt import execute_fixed_attempt, execute_react_attempt
from app.agent.u10_comparison import (
    FIXTURE_IDS,
    Artifact,
    Attempt,
    Benchmark,
    LlmConfiguration,
    Policy,
    Safety,
    _check_attempt,
    evaluate,
    rules_sha256,
    validate_artifact,
)
from app.agent.u10_observations import ObservationContext
from app.agent.u10_read_adapter import Deadline, ReadPorts
from app.agent.u10_read_execution import DocumentContext, fixed_policy_sha256


@dataclass(frozen=True)
class AttemptKey:
    fixture_id: str
    attempt_no: int
    policy: Policy
    execution_order: int


@dataclass(frozen=True)
class BatchBinding:
    evaluated_revision: str
    benchmark_sha256: str
    llm_config_sha256: str
    tool_contract_sha256: str
    fixed_policy_sha256: str
    attempt_count: int = 32


@dataclass(frozen=True)
class AttemptEnvironment:
    context: ObservationContext
    verified_snapshot_sha256: str
    read_ports: ReadPorts
    deadline: Deadline
    generate: Callable[..., Any]
    observe_effects: Callable[[], tuple[Safety, int]]
    select: Callable[..., Any] | None = None
    bound_inputs: dict[str, dict[str, Any]] | None = None
    document_context: DocumentContext | None = None
    clock_ns: Callable[[], int] = time.monotonic_ns


def execution_plan() -> Iterator[AttemptKey]:
    """CF-1..8, fixed/react then react/fixed per fixture, with no filtering."""
    order = 0
    for fixture_id in FIXTURE_IDS:
        for number in (1, 2):
            policies = (
                ("FIXED_POLICY_V21", "REACT_V2")
                if number == 1
                else ("REACT_V2", "FIXED_POLICY_V21")
            )
            for policy in policies:
                order += 1
                yield AttemptKey(fixture_id, number, policy, order)


def execute_batch(
    *,
    benchmark: Benchmark,
    llm: LlmConfiguration,
    evaluated_revision: str,
    expected_benchmark_sha256: str,
    expected_tool_contract_sha256: str,
    authorize: Callable[[BatchBinding], bool],
    prepare: Callable[[AttemptKey], AbstractContextManager[AttemptEnvironment]],
) -> Artifact:
    """Run both existing single-attempt paths, then independently revalidate.

    authorize must verify real data-export authority, including expiry/revocation,
    before EACH resource scope. A user-approved implementation budget is not this
    authority. The factory gets only a key, never oracle or required-evidence IDs.
    """
    benchmark = Benchmark.model_validate(benchmark.model_dump()).model_copy(deep=True)
    llm = LlmConfiguration.model_validate(llm.model_dump()).model_copy(deep=True)
    if type(evaluated_revision) is not str or not re.fullmatch(
        r"[0-9a-f]{40}", evaluated_revision
    ):
        raise EvidenceError("U10_REVISION_INVALID")
    if any(
        len(model) > 64 or model != model.strip()
        for model in (llm.hypothesis_model_revision, llm.selector_model_revision)
    ):
        raise EvidenceError("LLM_CONFIG_MISMATCH")
    benchmark_sha = digest(canonical_json(benchmark))
    if benchmark_sha != expected_benchmark_sha256:
        raise EvidenceError("BENCHMARK_SHA_MISMATCH")
    if benchmark.tool_contract_sha256 != expected_tool_contract_sha256:
        raise EvidenceError("TOOL_CONTRACT_MISMATCH")
    if benchmark.fixed_policy_sha256 != fixed_policy_sha256():
        raise EvidenceError("FIXED_POLICY_MISMATCH")
    binding = BatchBinding(
        evaluated_revision,
        benchmark_sha,
        digest(canonical_json(llm)),
        benchmark.tool_contract_sha256,
        benchmark.fixed_policy_sha256,
    )
    fixtures = {f.fixture_id: f for f in benchmark.fixtures}
    attempts = []
    # Keep references alive: id() alone can be reused after a previous scope exits.
    contexts = []
    for key in execution_plan():
        if authorize(binding) is not True:
            raise EvidenceError("U10_DATA_EXPORT_NOT_AUTHORIZED")
        with prepare(key) as env:
            if not isinstance(env, AttemptEnvironment):
                raise EvidenceError("U10_ATTEMPT_ENVIRONMENT_INVALID")
            if any(env.context is previous for previous in contexts):
                raise EvidenceError("U10_ATTEMPT_CONTEXT_REUSED")
            contexts.append(env.context)
            common = dict(
                fixture=fixtures[key.fixture_id],
                attempt_no=key.attempt_no,
                verified_snapshot_sha256=env.verified_snapshot_sha256,
                context=env.context,
                llm=llm,
                read_ports=env.read_ports,
                deadline=env.deadline,
                generate=env.generate,
                observe_effects=env.observe_effects,
                clock_ns=env.clock_ns,
            )
            if key.policy == "FIXED_POLICY_V21":
                if env.bound_inputs is None or env.document_context is None:
                    raise EvidenceError("U10_ATTEMPT_ENVIRONMENT_INVALID")
                result = execute_fixed_attempt(
                    **common,
                    bound_inputs=env.bound_inputs,
                    document_context=env.document_context,
                )
            else:
                if env.select is None:
                    raise EvidenceError("U10_ATTEMPT_ENVIRONMENT_INVALID")
                result = execute_react_attempt(**common, select=env.select)
            row = Attempt.model_validate(result.attempt.model_dump()).model_copy(
                deep=True
            )
            if (row.fixture_id, row.attempt_no, row.policy, row.execution_order) != (
                key.fixture_id,
                key.attempt_no,
                key.policy,
                key.execution_order,
            ):
                raise EvidenceError("U10_ATTEMPT_BINDING_MISMATCH")
            if row.llm_config_sha256 != binding.llm_config_sha256:
                raise EvidenceError("LLM_CONFIG_MISMATCH")
            _check_attempt(row, fixtures[key.fixture_id], key.execution_order)
            # A real unexpected effect invalidates isolation. Stop further work,
            # rather than emit a complete-looking artifact from a partial batch.
            if row.external_effects or any(row.safety.model_dump().values()):
                raise EvidenceError("U10_UNEXPECTED_EXTERNAL_EFFECT")
            attempts.append(row)
        # Resource cleanup must finish before the next authorization/preparation.
    artifact = Artifact(
        schema_version="u10-comparison-v1",
        evaluated_revision=evaluated_revision,
        benchmark_sha256=benchmark_sha,
        verdict_rules_sha256=rules_sha256(),
        llm=llm,
        SYNTHETIC_COUNTERFACTUAL_BENCHMARK=True,
        PRODUCTION_PERFORMANCE_NOT_CLAIMED=True,
        EXPERIMENT_ONLY=True,
        attempts=attempts,
        result=evaluate(benchmark, attempts),
    )
    validate_artifact(artifact.model_dump(), benchmark.model_dump())
    return artifact.model_copy(deep=True)
