"""Real offline recount and protected grant issuance; no live side effects."""

import pytest

from app.agent.release_aggregate import emit_aggregate
from app.agent.release_artifacts import EvidenceError, read_private
from app.agent.release_fence import ProductionFence, admit_new_run
from app.agent.release_grant import read_release_grant
from app.agent.release_qualification import issue_release_grant
from app.agent.release_seal import seal_bundle
from tests.unit.test_agent_release import AT, ATTEMPT, REV
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_bundle_v2 import mock_bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery  # noqa: F401
from tests.unit.test_agent_release_mock import evidence as mock_evidence  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401
from tests.unit.test_agent_u10_evaluation import bundle as u10_bundle


@pytest.fixture
def issuance(mock_bundle, tmp_path):  # noqa: F811
    repo = tmp_path / "repository"
    repo.mkdir()
    reports = tmp_path / "reports"
    reports.mkdir(mode=0o700)
    (reports / "cm-5.2").mkdir(mode=0o700)
    published = reports / "cm-5.2" / ATTEMPT
    mock_bundle["published_root"].rename(published)
    args = {
        **{
            k: v
            for k, v in mock_bundle.items()
            if k not in {"round1", "round1_completion"}
        },
        "root": published / "robustness",
        "published_root": published,
    }
    emit_aggregate(**args, repository=repo)
    seal_bundle(**args, repository=repo)
    u10 = tmp_path / "u10"
    u10.mkdir(mode=0o700)
    evaluation, receipt, _ = u10_bundle(u10)
    assert receipt["evaluated_revision"] == REV
    import json

    prepared = json.loads(read_private(args["root"], "prepared-attempt.json"))
    return dict(
        **args,
        **evaluation,
        reports_root=reports,
        repository=repo,
        image_ids={
            r: prepared["images"][r]["image_id"] for r in ("backend", "frontend")
        },
        now=AT,
    )


def test_actual_recount_grant_retry_is_immutable_and_not_admission(issuance):
    args = dict(issuance)
    root = args["reports_root"]
    with ProductionFence(root, REV, ATTEMPT) as fence:
        grant = issue_release_grant(fence=fence, **args)
        assert grant.action_policy_version == "MOCK-NOTIFY-V1"
        before = read_private(args["published_root"], "release-grant.json")
        args["now"] = "2026-09-05T02:00:00Z"
        assert issue_release_grant(fence=fence, **args) == grant
        assert read_private(args["published_root"], "release-grant.json") == before
        with pytest.raises(EvidenceError):
            with admit_new_run(root=root, required_binding=(REV, ATTEMPT)):
                pytest.fail("qualification cannot open admission")
    assert (
        read_release_grant(
            reports_root=root,
            expected_attempt_id=ATTEMPT,
            expected_revision=REV,
            expected_policy="MOCK-NOTIFY-V1",
        )
        == grant
    )


@pytest.mark.parametrize(
    "name",
    ["mock-sources.round1.json", "mock-results.round1.json", "round1-completion.json"],
)
def test_tampered_input_never_issues_grant(issuance, name):
    (issuance["root"] / name).write_bytes(b"{}\n")
    with ProductionFence(issuance["reports_root"], REV, ATTEMPT) as fence:
        with pytest.raises(EvidenceError):
            issue_release_grant(fence=fence, **issuance)
    assert not (issuance["published_root"] / "release-grant.json").exists()


def test_grant_requires_live_closed_lock(issuance):
    fence = ProductionFence(issuance["reports_root"], REV, ATTEMPT)
    with pytest.raises(EvidenceError, match="RELEASE_FENCE_NOT_LOCKED"):
        issue_release_grant(fence=fence, **issuance)


def test_open_fence_cannot_issue_grant_even_while_exclusively_locked(issuance):
    with ProductionFence(issuance["reports_root"], REV, ATTEMPT) as fence:
        fence.reopen(level=3)
        with pytest.raises(EvidenceError, match="^RELEASE_FENCE_NOT_LOCKED$"):
            issue_release_grant(fence=fence, **issuance)
    assert not (issuance["published_root"] / "release-grant.json").exists()
    assert not (issuance["published_root"] / "qualification-output.json").exists()


def test_runtime_and_readback_use_actual_grant_and_keep_fence_separate(
    issuance, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.agent import runtime_composition, runtime_readback
    from app.common import config, db

    root = issuance["reports_root"]
    monkeypatch.setattr(config, "AGENT_ACTION_POLICY", "MOCK-NOTIFY-V1")
    monkeypatch.setattr(config, "AGENT_AUTONOMY_LEVEL", 3)
    monkeypatch.setattr(config, "AGENT_LEVEL3_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_LEVEL3_DEMO_ACK", ATTEMPT)
    monkeypatch.setenv("BISTEL_SOURCE_REVISION", REV)
    monkeypatch.setattr(runtime_composition, "Path", lambda _: root)
    monkeypatch.setattr(runtime_readback, "Path", lambda _: root)

    @contextmanager
    def connect():
        yield SimpleNamespace(
            execute=lambda _: SimpleNamespace(one=lambda: ("kosa_agent", "kosa_app"))
        )

    monkeypatch.setattr(db, "get_app_engine", lambda: SimpleNamespace(connect=connect))
    runtime = runtime_composition.AgentRuntime(
        autonomy_level=3,
        level3_enabled=True,
        database_name="kosa_agent",
        demo_ack=ATTEMPT,
        demo_receipt_validator=lambda _: True,
    )
    assert runtime_readback.collect_readback()["ack_matches_receipt"] is False
    with pytest.raises(runtime_composition.AgentRuntimeError):
        runtime._require_autonomy_ready()
    with ProductionFence(root, REV, ATTEMPT) as fence:
        issue_release_grant(fence=fence, **issuance)
        observed = runtime_readback.collect_readback()
        assert observed["schema_version"] == "agent-runtime-readback-v2"
        runtime_readback.validate_readback(observed, "production_level3")
        runtime._require_autonomy_ready()
        with pytest.raises(EvidenceError):
            with admit_new_run(root=root, required_binding=(REV, ATTEMPT)):
                pytest.fail("qualification cannot release CLOSED fence")
        fence.reopen(level=3)
    with admit_new_run(root=root, required_binding=(REV, ATTEMPT)):
        runtime._require_autonomy_ready()
    # Byte mutation invalidates both entry paths even though the fence is OPEN.
    (issuance["root"] / "round1.json").write_bytes(b"{}\n")
    assert runtime_readback.collect_readback()["ack_matches_receipt"] is False
    with pytest.raises(runtime_composition.AgentRuntimeError):
        runtime._require_autonomy_ready()
