"""U10 contract fixtures only: no observations, database or provider calls."""

import json
import subprocess
import sys
from copy import deepcopy
from statistics import mean, median

import pytest

from app.agent import u10_comparison as u10
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    write_private,
)
from scripts.verify_u10_comparison import main

LLM = {
    "hypothesis_model_revision": "fixture-only-hypothesis",
    "selector_model_revision": "fixture-only-selector",
    "hypothesis_prompt_version": "agent-hypothesis-v3-ko1",
    "selector_prompt_version": "agent-react-v2-ko1",
    "temperature": 0.0,
    "seed": 0,
}


def ids(*values):
    values = sorted(set(values))
    return {"values": values, "sha256": digest(canonical_json(values))}


def benchmark_payload():
    fixtures = []
    for fixture_id in u10.FIXTURE_IDS:
        inventory = {
            "current_wafers": 1,
            "adjacent": {"relation": "UPSTREAM", "wafers": 1},
            "sibling_chamber_id": "fixture-sibling",
            "history_prior_lots": 3,
            "metrology_samples": 2,
            "documents": True,
        }
        fixtures.append(
            {
                "fixture_id": fixture_id,
                "initial_snapshot_sha256": "1" * 64,
                "initial_evidence_ids": ids(),
                "candidate_inventory": inventory,
                "expected_compared": u10.Inventory.model_validate(
                    inventory
                ).dimensions(),
                "required_evidence_ids": ids("CURRENT_FDC", "DOCUMENT_1"),
                "oracle_required_dimensions": [],
            }
        )
    result = {
        "schema_version": "u10-benchmark-v1",
        "fixture_sha256": "2" * 64,
        "oracle_sha256": "3" * 64,
        "inventory_sha256": "4" * 64,
        "tool_contract_sha256": "5" * 64,
        "fixed_policy_sha256": "6" * 64,
        "fixtures": fixtures,
    }
    seal_benchmark(result)
    return result


def seal_benchmark(b):
    b["inventory_sha256"] = digest(
        canonical_json(
            [
                {
                    k: f[k]
                    for k in ("fixture_id", "candidate_inventory", "expected_compared")
                }
                for f in b["fixtures"]
            ]
        )
    )
    b["oracle_sha256"] = digest(
        canonical_json(
            [
                {
                    k: f[k]
                    for k in (
                        "fixture_id",
                        "required_evidence_ids",
                        "oracle_required_dimensions",
                    )
                }
                for f in b["fixtures"]
            ]
        )
    )


def sync_attempt(a, fixture):
    calls = a["calls"]
    a["read_attempts"] = len(calls)
    a["successful_reads"] = sum(c["status"] == "SUCCESS" for c in calls)
    a["selector_calls"] = len(a["selector"])
    for kind in ("selector", "hypothesis"):
        a[kind + "_tokens"] = {
            k: sum(c["tokens"][k] for c in a[kind]) for k in ("input", "output")
        }
    a["tool_latency_ms"] = sum(c["latency_ms"] for c in calls)
    a["selector_latency_ms"] = sum(c["latency_ms"] for c in a["selector"])
    a["available_evidence_ids"] = ids(
        *a["initial_evidence_ids"]["values"],
        *[e for c in calls for e in c["evidence_ids"]["values"]],
    )
    inventory = u10.Inventory.model_validate(fixture["candidate_inventory"])
    checked = {c["slot"] for c in calls if c["status"] == "SUCCESS"}
    slots = {
        "upstream": "ADJACENT_FDC",
        "downstream": "ADJACENT_FDC",
        "sibling": "SIBLING",
        "history": "HISTORY",
        "metrology": "METROLOGY",
    }
    a["compared"] = {
        d: "NOT_AVAILABLE"
        if v == "NOT_AVAILABLE"
        else "CHECKED"
        if slots[d] in checked
        else "NOT_CHECKED"
        for d, v in inventory.dimensions().items()
    }


def make_attempt(fixture, policy, number, order):
    inventory = u10.Inventory.model_validate(fixture["candidate_inventory"])
    slots = [s for s, ok in inventory.available_slots().items() if ok]
    if policy == "REACT_V2":
        slots = [s for s in slots if s not in ("METROLOGY", "DOCUMENT_2")]
    a = {
        "fixture_id": fixture["fixture_id"],
        "attempt_no": number,
        "policy": policy,
        "execution_order": order,
        "initial_snapshot_sha256": fixture["initial_snapshot_sha256"],
        "llm_config_sha256": digest(canonical_json(LLM)),
        "completion": True,
        "action": "WARNING",
        "external_effects": 0,
        "safety": {"send_action_selected": 0, "hitl_bypass": 0, "pre_approval_mes": 0},
        "calls": [
            {
                "slot": slot,
                "tool": u10.TOOLS[slot],
                "selection": i + 1,
                "retry": 0,
                "input_digest": digest(slot.encode()),
                "status": "SUCCESS",
                "latency_ms": 10,
                "evidence_ids": ids(slot),
            }
            for i, slot in enumerate(slots)
        ],
        "skipped_slots": [
            {"slot": s, "reason": "NO_CANDIDATE"}
            for s, ok in inventory.available_slots().items()
            if not ok and policy == "FIXED_POLICY_V21"
        ],
        "selector": (
            [{"tokens": {"input": 5, "output": 5}, "latency_ms": 10}]
            if policy == "REACT_V2"
            else []
        ),
        "hypothesis": [{"tokens": {"input": 90, "output": 10}, "latency_ms": 10}],
        "initial_evidence_ids": ids(),
        "cited_evidence_ids": ids("CURRENT_FDC", "DOCUMENT_1"),
        "end_to_end_latency_ms": 1250 if policy == "REACT_V2" else 1000,
    }
    sync_attempt(a, fixture)
    return a


def artifact_payload(b=None):
    b = b or benchmark_payload()
    attempts = []
    for f in b["fixtures"]:
        for n in (1, 2):
            policies = u10.POLICIES if n == 1 else tuple(reversed(u10.POLICIES))
            for p in policies:
                attempts.append(make_attempt(f, p, n, len(attempts) + 1))
    payload = {
        "schema_version": "u10-comparison-v1",
        "llm": LLM,
        "evaluated_revision": "a" * 40,
        "benchmark_sha256": digest(canonical_json(b)),
        "verdict_rules_sha256": u10.rules_sha256(),
        "SYNTHETIC_COUNTERFACTUAL_BENCHMARK": True,
        "PRODUCTION_PERFORMANCE_NOT_CLAIMED": True,
        "EXPERIMENT_ONLY": True,
        "attempts": attempts,
    }
    payload["result"] = recompute(payload, b)
    return payload, b


def recompute(a, b):
    return u10.evaluate(
        u10.Benchmark.model_validate(b),
        [u10.Attempt.model_validate(row) for row in a["attempts"]],
    )


def react_rows(a):
    return [r for r in a["attempts"] if r["policy"] == "REACT_V2"]


def test_efficiency_inclusive_cost_boundaries_and_full_breakdown():
    a, b = artifact_payload()
    r = u10.validate_artifact(a, b)
    assert r["agent_verdict"] == "AGENT_JUSTIFICATION_ESTABLISHED_V21"
    assert r["verdict_reason"] is None
    assert r["verdict_breakdown"]["branches"]["EFFICIENCY_GAIN"]
    assert len(r["verdict_breakdown"]["pairs"]) == 16
    assert all(p["read_attempt_delta"] == 2 for p in r["verdict_breakdown"]["pairs"])


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("action", "EQP_HOLD", "ACTION_MISMATCH"),
        ("completion", False, "COMPLETION"),
        ("external_effects", 1, "EXTERNAL_EFFECTS"),
    ],
)
def test_hard_gate_failures_are_negative_data_not_schema_success(field, value, code):
    a, b = artifact_payload()
    react_rows(a)[0][field] = value
    with pytest.raises(EvidenceError, match="VERDICT_RECALCULATION_MISMATCH"):
        u10.validate_artifact(a, b)
    a["result"] = recompute(a, b)
    assert u10.validate_artifact(a, b)["verdict_reason"] == "HARD_GATE_FAIL:" + code


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda a: a.update(verdict_rules_sha256="0" * 64), "RULE_VERSION_MISMATCH"),
        (lambda a: a["attempts"].pop(), "ATTEMPT_POPULATION_INVALID"),
        (
            lambda a: a["attempts"].append(deepcopy(a["attempts"][0])),
            "ATTEMPT_POPULATION_INVALID",
        ),
        (lambda a: a["attempts"].reverse(), "EXECUTION_ORDER_MISMATCH"),
        (lambda a: a["attempts"][0].update(read_attempts=1), "READ_ATTEMPTS_MISMATCH"),
        (
            lambda a: a["attempts"][0].update(successful_reads=1),
            "SUCCESSFUL_READS_MISMATCH",
        ),
        (
            lambda a: a["attempts"][0].update(tool_latency_ms=0),
            "LATENCY_TOTAL_MISMATCH",
        ),
        (
            lambda a: a["attempts"][0]["compared"].update(upstream="NOT_CHECKED"),
            "COMPARED_MISMATCH",
        ),
        (
            lambda a: a["attempts"][0]["available_evidence_ids"].update(
                sha256="0" * 64
            ),
            "U10_SCHEMA_INVALID",
        ),
        (lambda a: a.update(EXPERIMENT_ONLY=1), "U10_SCHEMA_INVALID"),
        (
            lambda a: a["attempts"][0].update(end_to_end_latency_ms=0),
            "METRIC_PRECONDITION_INVALID",
        ),
        (
            lambda a: a["attempts"][0].pop("selector_latency_ms"),
            "METRIC_PRECONDITION_INVALID",
        ),
    ],
)
def test_contract_mutations(change, code):
    a, b = artifact_payload()
    change(a)
    with pytest.raises(EvidenceError, match=code):
        u10.validate_artifact(a, b)


def test_inventory_is_separately_pinned_and_unavailable_is_not_not_required():
    b = benchmark_payload()
    for f in b["fixtures"]:
        f["candidate_inventory"]["adjacent"] = {"relation": "NONE", "wafers": 0}
        f["expected_compared"]["upstream"] = "NOT_AVAILABLE"
    seal_benchmark(b)
    a, b = artifact_payload(b)
    assert u10.validate_artifact(a, b)["verdict_reason"] is None
    a["attempts"][0]["skipped_slots"] = []
    with pytest.raises(EvidenceError, match="SKIPPED_SLOTS_MISMATCH"):
        u10.validate_artifact(a, b)
    a, b = artifact_payload()
    b["inventory_sha256"] = "0" * 64
    with pytest.raises(EvidenceError, match="U10_SCHEMA_INVALID"):
        u10.validate_artifact(a, b)


@pytest.mark.parametrize("extra_token,extra_latency", [(1, 0), (0, 1), (1, 1)])
def test_efficiency_above_either_cost_cap_is_no_gain(extra_token, extra_latency):
    a, b = artifact_payload()
    for r in react_rows(a):
        r["selector"][0]["tokens"]["input"] += extra_token
        r["end_to_end_latency_ms"] += extra_latency
        sync_attempt(r, b["fixtures"][u10.FIXTURE_IDS.index(r["fixture_id"])])
    a["result"] = recompute(a, b)
    assert u10.validate_artifact(a, b)["verdict_reason"] == "NO_GAIN"


def test_quality_gain_and_cost_cap_reason_priority():
    a, b = artifact_payload()
    for r in a["attempts"]:
        if r["policy"] == "FIXED_POLICY_V21":
            r["cited_evidence_ids"] = ids("CURRENT_FDC")
    a["result"] = recompute(a, b)
    assert a["result"]["verdict_breakdown"]["branches"]["QUALITY_GAIN_WITHIN_COST_CAP"]
    for r in react_rows(a):
        r["end_to_end_latency_ms"] = 1501
    a["result"] = recompute(a, b)
    assert u10.validate_artifact(a, b)["verdict_reason"] == "COST_CAP_EXCEEDED"
    react_rows(a)[0]["safety"]["hitl_bypass"] = 1
    a["result"] = recompute(a, b)
    assert u10.validate_artifact(a, b)["verdict_reason"] == "HARD_GATE_FAIL:SAFETY"


@pytest.mark.parametrize("extra_tokens,extra_latency", [(0, 0), (1, 0), (0, 1)])
def test_quality_exact_recall_and_cost_boundaries(extra_tokens, extra_latency):
    a, b = artifact_payload()
    # Four of 16 pairs improve by 1/2: mean delta is exactly 0.125.
    for row in a["attempts"]:
        if row["policy"] == "FIXED_POLICY_V21" and row["fixture_id"] in (
            "CF-1",
            "CF-2",
        ):
            row["cited_evidence_ids"] = ids("CURRENT_FDC")
    for row in react_rows(a):
        row["selector"][0]["tokens"]["input"] = 45 + extra_tokens
        row["end_to_end_latency_ms"] = 1500 + extra_latency
        sync_attempt(row, b["fixtures"][u10.FIXTURE_IDS.index(row["fixture_id"])])
    a["result"] = recompute(a, b)
    result = u10.validate_artifact(a, b)
    assert result["verdict_reason"] == (
        "COST_CAP_EXCEEDED" if extra_tokens or extra_latency else None
    )
    assert not result["verdict_breakdown"]["branches"]["EFFICIENCY_GAIN"]


def inject_retry(r, fixture, status="TIMEOUT"):
    first = r["calls"][0]
    retry = deepcopy(first)
    retry["retry"] = 1
    first["status"] = status
    first["evidence_ids"] = ids()
    r["calls"].insert(1, retry)
    r["calls"] = r["calls"][:8]
    sync_attempt(r, fixture)


def test_sparse_failures_cannot_be_hidden_by_median_or_cf3_offsets():
    a, b = artifact_payload()
    for r in a["attempts"]:
        if r["policy"] == "FIXED_POLICY_V21" and r["fixture_id"] == "CF-3":
            inject_retry(r, b["fixtures"][2])
    # Five new failures vs two intentional CF-3 failures; median delta is still 0.
    for r in react_rows(a)[:5]:
        inject_retry(r, b["fixtures"][u10.FIXTURE_IDS.index(r["fixture_id"])])
    a["result"] = recompute(a, b)
    r = u10.validate_artifact(a, b)
    assert r["verdict_reason"] == "NO_GAIN"
    assert not any(r["verdict_breakdown"]["branches"].values())
    assert sum(p["read_fail_delta"] for p in r["verdict_breakdown"]["pairs"]) == -3
    # Even one new failure elsewhere cannot be offset by CF-3's two failures.
    a, b = artifact_payload()
    for row in a["attempts"]:
        if row["policy"] == "FIXED_POLICY_V21" and row["fixture_id"] == "CF-3":
            inject_retry(row, b["fixtures"][2])
    inject_retry(react_rows(a)[0], b["fixtures"][0])
    a["result"] = recompute(a, b)
    assert a["result"]["verdict_reason"] == "NO_GAIN"


def test_zero_fixed_tokens_and_retry_bypass_are_rejected():
    a, b = artifact_payload()
    a["attempts"][0]["hypothesis"][0]["tokens"] = {"input": 0, "output": 0}
    sync_attempt(a["attempts"][0], b["fixtures"][0])
    with pytest.raises(EvidenceError, match="METRIC_PRECONDITION_INVALID"):
        recompute(a, b)
    a, b = artifact_payload()
    r = react_rows(a)[0]
    r["calls"][0].update(status="TIMEOUT", evidence_ids=ids())
    sync_attempt(r, b["fixtures"][0])
    with pytest.raises(EvidenceError, match="READ_RETRY_REQUIRED"):
        recompute(a, b)


@pytest.mark.parametrize("same_tool_calls", [4, 5])
def test_same_tool_budget_accepts_four_and_rejects_five(same_tool_calls):
    a, b = artifact_payload()
    row = react_rows(a)[0]
    current = next(c for c in row["calls"] if c["slot"] == "CURRENT_FDC")
    document = next(c for c in row["calls"] if c["slot"] == "DOCUMENT_1")
    row["calls"] = [deepcopy(current) for _ in range(same_tool_calls)] + [document]
    for index, call in enumerate(row["calls"], start=1):
        call["selection"] = index
    sync_attempt(row, b["fixtures"][0])
    assert row["read_attempts"] == same_tool_calls + 1 <= 8
    if same_tool_calls == 5:
        with pytest.raises(EvidenceError, match="^TOOL_BUDGET_EXCEEDED$"):
            recompute(a, b)
    else:
        a["result"] = recompute(a, b)
        assert u10.validate_artifact(a, b)["verdict_reason"] is None


def test_one_fixture_recall_regression_blocks_both_gain_branches():
    a, b = artifact_payload()
    for row in a["attempts"]:
        # CF-1 loses 0.5 in both pairs; CF-2~4 each gain 0.5 in both pairs.
        # Aggregate median is 0 and mean is 0.125, so only CF-1 blocks both branches.
        if (row["fixture_id"] == "CF-1" and row["policy"] == "REACT_V2") or (
            row["fixture_id"] in ("CF-2", "CF-3", "CF-4")
            and row["policy"] == "FIXED_POLICY_V21"
        ):
            row["cited_evidence_ids"] = ids("CURRENT_FDC")
    a["result"] = recompute(a, b)
    result = u10.validate_artifact(a, b)
    breakdown = result["verdict_breakdown"]
    deltas = [p["recall_delta"] for p in breakdown["pairs"]]
    assert median(deltas) == 0 and mean(deltas) == 0.125
    assert breakdown["fixtures"][0]["recall_delta_median"] == -0.5
    assert [c["name"] for c in breakdown["checks"] if not c["pass"]] == ["CF-1.recall"]
    assert breakdown["branches"] == {
        "EFFICIENCY_GAIN": False,
        "QUALITY_GAIN_WITHIN_COST_CAP": False,
    }
    assert result["verdict_reason"] == "NO_GAIN"
    assert result["agent_verdict"] == "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21"


def test_unsupported_ids_and_fake_available_union_cannot_be_accepted():
    a, b = artifact_payload()
    r = react_rows(a)[0]
    r["cited_evidence_ids"] = ids("not-returned")
    a["result"] = recompute(a, b)
    assert a["result"]["verdict_reason"] == "HARD_GATE_FAIL:UNSUPPORTED_CITATION"
    r["available_evidence_ids"] = ids(
        *r["available_evidence_ids"]["values"], "not-returned"
    )
    with pytest.raises(EvidenceError, match="AVAILABLE_EVIDENCE_MISMATCH"):
        recompute(a, b)


def test_fixed_policy_cannot_use_a_selector_or_reorder_slots():
    a, b = artifact_payload()
    f = a["attempts"][0]
    f["selector"] = deepcopy(react_rows(a)[0]["selector"])
    sync_attempt(f, b["fixtures"][0])
    with pytest.raises(EvidenceError, match="FIXED_SELECTOR_FORBIDDEN"):
        recompute(a, b)
    a, b = artifact_payload()
    f = a["attempts"][0]
    f["calls"][0]["slot"] = "ADJACENT_FDC"
    with pytest.raises(EvidenceError, match="FIXED_PATH_MISMATCH"):
        recompute(a, b)


def test_failed_reads_are_not_a_discount_and_initial_ids_are_pinned():
    a, b = artifact_payload()
    r = react_rows(a)[0]
    inject_retry(r, b["fixtures"][0])
    assert r["read_attempts"] == 7 and r["successful_reads"] == 6
    r["read_attempts"] = 6
    with pytest.raises(EvidenceError, match="READ_ATTEMPTS_MISMATCH"):
        recompute(a, b)
    a, b = artifact_payload()
    r = react_rows(a)[0]
    r["initial_evidence_ids"] = ids("fabricated")
    sync_attempt(r, b["fixtures"][0])
    with pytest.raises(EvidenceError, match="INITIAL_EVIDENCE_MISMATCH"):
        recompute(a, b)


def test_attempt_model_config_drift_is_rejected():
    a, b = artifact_payload()
    react_rows(a)[0]["llm_config_sha256"] = "0" * 64
    with pytest.raises(EvidenceError, match="LLM_CONFIG_MISMATCH"):
        u10.validate_artifact(a, b)


def test_cli_negative_verdict_is_exit_zero_and_read_only(tmp_path, capsys):
    tmp_path.chmod(0o700)
    a, b = artifact_payload()
    for row in react_rows(a):
        row["end_to_end_latency_ms"] = 2000
    a["result"] = recompute(a, b)
    ref = write_private(tmp_path, "benchmark.json", b)
    write_private(tmp_path, "comparison.json", a)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args = [
        "--artifact",
        str(tmp_path / "comparison.json"),
        "--benchmark",
        str(tmp_path / "benchmark.json"),
        "--benchmark-sha256",
        ref.sha256,
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["integrity"] == "PASS"
    assert result["agent_verdict"] == "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21"
    assert result["verdict_reason"] == "NO_GAIN"
    assert "allowed_actions" not in result
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    args[-1] = "0" * 64
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == (
        "PINNED_BENCHMARK_SHA_MISMATCH"
    )


def test_cli_rejects_unprotected_files_and_sanitizes_parse_errors(tmp_path, capsys):
    tmp_path.chmod(0o700)
    a, b = artifact_payload()
    ref = write_private(tmp_path, "benchmark.json", b)
    artifact = tmp_path / "comparison.json"
    artifact.write_text('{"password":"must-not-appear", broken')
    artifact.chmod(0o600)
    args = [
        "--artifact",
        str(artifact),
        "--benchmark",
        str(tmp_path / "benchmark.json"),
        "--benchmark-sha256",
        ref.sha256,
    ]
    assert main(args) == 1
    output = capsys.readouterr().out
    assert "must-not-appear" not in output and "password" not in output
    assert json.loads(output)["reason_code"] == "COMPONENT_JSON_INVALID"
    artifact.chmod(0o644)
    assert main(args) == 1
    assert (
        json.loads(capsys.readouterr().out)["reason_code"] == "COMPONENT_FILE_INVALID"
    )


def test_verifier_import_is_offline_and_lightweight():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from scripts import verify_u10_comparison; "
            "assert not {'sklearn', 'numpy', 'psycopg', 'app.common.config', "
            "'app.agent.runtime_composition'} & set(sys.modules)",
        ],
        cwd=u10.__file__.rsplit("/app/", 1)[0],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
