"""Release/live/document semantic sync gate regressions."""

from __future__ import annotations

import copy

from app.main import app
from scripts.api_sync_gate import (
    ApiSyncError,
    canonical_semantics,
    evaluate_sync,
    load_canonical,
)
from tests.support.api_contract_baseline import (
    load_optional_contract,
    load_required_contract,
    load_team_release_contract,
    normalize_openapi_contract,
)


def _inputs():
    return (
        load_canonical(),
        load_required_contract(),
        load_optional_contract(),
        load_team_release_contract(),
    )


def test_complete_30_semantic_contracts_pass_all_three_gates() -> None:
    spec, required, optional, team = _inputs()
    report = evaluate_sync(spec, canonical_semantics(spec), required, optional, team)
    assert report["overall"] == "PASS"
    assert report["release_pass_count"] == 20
    assert report["live_pass_count"] == 30
    assert report["document_catalog_count"] == 36


def test_release_removal_and_unclassified_live_route_are_red() -> None:
    spec, required, optional, team = _inputs()
    actual = canonical_semantics(spec)
    actual.pop(("GET", "/alarms"))
    actual[("GET", "/unclassified")] = next(iter(actual.values()))
    report = evaluate_sync(spec, actual, required, optional, team)
    assert report["overall"] == "BLOCKED"
    assert report["release_gate"] == "BLOCKED"
    assert report["unclassified_live"] == ["GET /unclassified"]
    assert report["undocumented_live"] == ["GET /unclassified"]


def test_nested_pattern_and_discriminator_mutations_are_drift() -> None:
    spec, required, optional, team = _inputs()
    expected = canonical_semantics(spec)
    actual = copy.deepcopy(expected)
    delivery = actual[("POST", "/internal/actions/{action_id}/delivery")]
    body_fields = delivery["request"]["body"]["fields"]
    body_fields["request_hash"]["schema"].pop("pattern")
    report = evaluate_sync(spec, actual, required, optional, team)
    statuses = {
        (item["method"], item["path"]): item["status"] for item in report["operations"]
    }
    assert statuses[("POST", "/internal/actions/{action_id}/delivery")] == "DRIFT"

    actual = copy.deepcopy(expected)
    ask = actual[("POST", "/agent/ask")]
    evidence = ask["responses"]["200"]["schema"]["fields"]["evidence_items"]
    evidence["schema"]["items"]["variants"].pop("GRAPH")
    report = evaluate_sync(spec, actual, required, optional, team)
    statuses = {
        (item["method"], item["path"]): item["status"] for item in report["operations"]
    }
    assert statuses[("POST", "/agent/ask")] == "DRIFT"


def test_live_openapi_normalizer_preserves_pattern_and_discriminated_union() -> None:
    actual = normalize_openapi_contract(app.openapi(), resolve_schemas=True)
    delivery = actual[("POST", "/internal/actions/{action_id}/delivery")]
    request_hash = delivery["request"]["body"]["fields"]["request_hash"]["schema"]
    assert request_hash["pattern"] == "^[0-9a-f]{64}$"

    ask = actual[("POST", "/agent/ask")]
    evidence = ask["responses"]["200"]["schema"]["fields"]["evidence_items"]
    items = evidence["schema"]["items"]
    assert items["type"] == "discriminated_union"
    assert items["discriminator"] == "type"
    assert set(items["variants"]) == {
        "AGENT_RUN",
        "ALARM",
        "DOCUMENT",
        "GRAPH",
        "METROLOGY",
        "TRACE",
    }
    assert "relation_id" in items["variants"]["GRAPH"]["fields"]


def test_canonical_semantics_rejects_unsorted_enum_storage() -> None:
    spec, _, _, _ = _inputs()
    mutated = copy.deepcopy(spec)
    enum = mutated["operations"][0]["semantic"]["request"]["query"]["area"]["schema"][
        "enum"
    ]
    enum.reverse()
    try:
        canonical_semantics(mutated)
    except ApiSyncError as exc:
        assert "sorted unique string list" in str(exc)
    else:
        raise AssertionError("sync gate must reject unsorted canonical enums")


def test_current_openapi_matches_the_final_canonical_contract() -> None:
    spec, required, optional, team = _inputs()
    actual = normalize_openapi_contract(app.openapi(), resolve_schemas=True)
    report = evaluate_sync(spec, actual, required, optional, team)
    drift = {
        (item["method"], item["path"])
        for item in report["operations"]
        if item["status"] == "DRIFT"
    }
    missing = {
        (item["method"], item["path"])
        for item in report["operations"]
        if item["status"] == "MISSING"
    }
    assert drift == set()
    assert missing == set()
    assert report["status_counts"] == {"PASS": 30, "DRIFT": 0, "MISSING": 0}
    assert report["release_pass_count"] == 20
    assert report["live_pass_count"] == 30
    assert report["overall"] == "PASS"
