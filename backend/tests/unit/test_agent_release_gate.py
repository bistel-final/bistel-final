"""Actual offline evidence chain; only live model readback is a test double."""

from dataclasses import replace as change_axes
from types import SimpleNamespace

import pytest

from app.agent import release_gate as subject
from app.agent.release_artifacts import digest
from app.agent.release_model import ModelContext
from app.agent.u10_preflight_report import preflight_report
from tests.unit.test_agent_release import ATTEMPT, REV, S
from tests.unit.test_agent_release_aggregate import (  # noqa: F401
    bundle,
    issue,
    read,
    reseal,
)
from tests.unit.test_agent_release_evidence import delivery, replace  # noqa: F401
from tests.unit.test_agent_release_round import CONFIG, LLM, template  # noqa: F401


@pytest.fixture
def gate(bundle, tmp_path):  # noqa: F811
    issue(bundle, tmp_path)
    model = ModelContext(
        schema_version="level3-model-context-v1",
        llm=LLM,
        endpoint_sha256=S,
        model_config_digest=CONFIG,
        published_attempt_id=ATTEMPT,
        published_artifact_sha256={
            name: digest((bundle["published_root"] / name).read_bytes())
            for name in subject.PUBLICATION_NAMES
        },
    )
    images = read(bundle, "round1.json")["images"]
    return dict(
        artifact=bundle["root"] / "aggregate.json",
        published_root=bundle["published_root"],
        repository=tmp_path / "repository",
        revision=REV,
        attempt_id=ATTEMPT,
        image_ids={r: images[r]["image_id"] for r in ("backend", "frontend")},
        backend_container_id="a" * 64,
        read_model=lambda _: model,
    )


def test_real_transitive_production_axes(gate):
    before = {p.name: p.read_bytes() for p in gate["artifact"].parent.iterdir()}
    result = subject.verify_release_axes(**gate)
    assert result.robustness == result.delivery_integrity == "PASS"
    assert result.artifact_sha256 == digest(gate["artifact"].read_bytes())
    assert result.reset_attempt_id == ATTEMPT
    assert {p.name: p.read_bytes() for p in gate["artifact"].parent.iterdir()} == before


@pytest.mark.parametrize(
    "name", ["smtp-approval-grant.json", "delivery-receipts.round1.json"]
)
@pytest.mark.parametrize("fault", ["missing", "sha", "mode", "symlink"])
def test_delivery_component_failure_does_not_change_robustness(gate, name, fault):
    p = gate["artifact"].parent / name
    if fault == "missing":
        p.unlink()
    elif fault == "sha":
        p.write_bytes(p.read_bytes() + b" ")
    elif fault == "mode":
        p.chmod(0o644)
    else:
        raw = p.read_bytes()
        p.unlink()
        target = p.with_suffix(".saved")
        target.write_bytes(raw)
        target.chmod(0o600)
        p.symlink_to(target)
    result = subject.verify_release_axes(**gate)
    assert result.robustness == "PASS"
    assert result.delivery_integrity == "FAIL" and result.delivery_failed_checks


@pytest.mark.parametrize(
    "fault",
    [
        "round",
        "completion",
        "claim",
        "schema",
        "attempt",
        "revision",
        "image",
        "model",
        "endpoint",
        "model_digest",
        "published_attempt",
        "published_bytes",
        "model_drift",
        "aggregate_drift",
    ],
)
def test_required_proof_and_live_bindings_deny_production(gate, fault):
    root = gate["artifact"].parent
    if fault in {"round", "completion", "claim"}:
        name = {
            "round": "round1.json",
            "completion": "round1-completion.json",
            "claim": "lifecycle-claim.publish.json",
        }[fault]
        (root / name).unlink()
    elif fault == "schema":
        gate["artifact"].write_bytes(b"{}")
    elif fault in {"attempt", "revision"}:
        gate["attempt_id" if fault == "attempt" else "revision"] = (
            "20260905T010000Z-bbbbbbbbbbbb" if fault == "attempt" else "b" * 40
        )
    elif fault == "image":
        gate["image_ids"]["backend"] = "sha256:" + "f" * 64
    else:
        original = gate["read_model"](None)
        value = original.model_dump()
        if fault == "model":
            value["llm"]["selector_model_revision"] = "different"
        if fault == "endpoint":
            value["endpoint_sha256"] = "f" * 64
        if fault == "model_digest":
            value["model_config_digest"] = "f" * 64
        if fault in {"published_attempt", "model_drift"}:
            value["published_attempt_id"] = None
        if fault == "published_bytes":
            value["published_artifact_sha256"]["fault-5class.json"] = "f" * 64
        changed = ModelContext.model_validate(value)
        calls = []

        def reader(_):
            calls.append(1)
            if fault == "aggregate_drift" and len(calls) == 2:
                gate["artifact"].write_bytes(gate["artifact"].read_bytes() + b" ")
            return original if fault == "model_drift" and len(calls) == 1 else changed

        gate["read_model"] = reader
    result = subject.verify_release_axes(**gate)
    assert result.robustness == "FAIL"
    assert result.robustness_failed_checks


@pytest.mark.parametrize("name", ["artifact", "published_root"])
def test_missing_argument_blocks_without_live_read(gate, name):
    gate[name] = None
    gate["read_model"] = lambda _: pytest.fail("must not read runtime")
    result = subject.verify_release_axes(**gate)
    assert result.robustness == result.delivery_integrity == "FAIL"


def test_error_text_never_exposes_secrets(gate):
    def fail(_):
        raise RuntimeError("postgresql://user:secret@example.invalid")

    gate["read_model"] = fail
    assert "secret" not in repr(subject.verify_release_axes(**gate))


@pytest.mark.parametrize(
    "robust,delivery_axis", [("PASS", "PASS"), ("FAIL", "PASS"), ("PASS", "FAIL")]
)
def test_research_verdict_is_not_a_production_gate(gate, robust, delivery_axis):
    axes = change_axes(
        subject.verify_release_axes(**gate),
        robustness=robust,
        delivery_integrity=delivery_axis,
    )
    for verdict in ("ESTABLISHED_V21", "NOT_ESTABLISHED_V21"):
        obs = SimpleNamespace(
            profile="production_level3",
            phase="post_start_pre_enable",
            checked_at="2026-09-05T02:00:00Z",
            repository_root="/test",
            head=REV,
            evaluation=SimpleNamespace(
                receipt=SimpleNamespace(evaluated_revision=REV),
                agent_verdict=verdict,
                verdict_reason="NO_GAIN" if verdict.startswith("NOT") else None,
            ),
            deployment=SimpleNamespace(image_bindings=SimpleNamespace(images={})),
        )
        result = preflight_report(
            obs,
            profile=obs.profile,
            phase=obs.phase,
            checked_at=obs.checked_at,
            failed_checks=[],
            release=axes,
        )
        assert result["integrity"] == "PASS" and result["agent_verdict"] == verdict
        assert result["allowed_actions"]["production_level3"] == (
            robust == delivery_axis == "PASS"
        )
