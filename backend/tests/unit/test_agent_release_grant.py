"""Grant reader binding tests, NOT valid round/recount or deployment evidence.

Minimal component projections deliberately test only the dormant runtime reader.
The future issuer must run the complete v2 validators before issuing a grant.
"""

import json
import os

import pytest

from app.agent import release_grant as subject
from app.agent.release_artifacts import canonical_json, digest, write_private
from app.agent.release_budget import budget_policy, profile_fields
from app.agent.release_round import budget_policy_sha256

REV = "a" * 40
ATTEMPT = "20260906T000000Z-aaaaaaaaaaaa"
AT = "2026-09-06T00:00:00Z"


@pytest.fixture
def layout(tmp_path, request):
    profile = getattr(request, "param", None)
    root = tmp_path.resolve() / "reports"
    root.mkdir(mode=0o700)
    (root / "cm-5.2").mkdir(mode=0o700)
    attempt = root / "cm-5.2" / ATTEMPT
    attempt.mkdir(mode=0o700)
    bundle = attempt / "robustness"
    bundle.mkdir(mode=0o700)
    public = {
        "attempt.json": {"attempt": ATTEMPT},
        "golden-flow.json": {"status": "PASS"},
        "fault-5class.json": {"policy_version": "MOCK-NOTIFY-V1"},
        subject.QUALIFICATION_NAME: {
            "test_only": "binding-not-recount",
            **profile_fields(profile),
        },
    }
    refs = {name: write_private(attempt, name, value) for name, value in public.items()}
    completion = dict(
        schema_version="level3-round1-completion-v2",
        cm52_attempt_id=ATTEMPT,
        final_status="PASS",
        attempt_artifact_sha256=refs["attempt.json"].sha256,
        golden_flow_sha256=refs["golden-flow.json"].sha256,
        fault_5class_sha256=refs["fault-5class.json"].sha256,
    )
    round1 = dict(
        **profile_fields(profile),
        runs=[profile_fields(profile) for _ in range(12)],
        budget_policy_sha256=budget_policy_sha256(profile),
        schema_version="level3-round1-v2",
        reset_attempt_id=ATTEMPT,
        R=REV,
        action_policy_version="MOCK-NOTIFY-V1",
        images={
            role: {"image_id": "sha256:" + char * 64, "label_revision": REV}
            for role, char in (("backend", "b"), ("frontend", "c"))
        },
    )
    prepared = dict(
        schema_version="level3-prepared-attempt-v2",
        attempt_id=ATTEMPT,
        R=REV,
        effective_env={
            "AGENT_ACTION_POLICY": "MOCK-NOTIFY-V1",
            **profile_fields(profile),
            **budget_policy(profile),
        },
    )
    pr = write_private(bundle, "prepared-attempt.json", prepared)
    round1["prepared_attempt"] = pr.model_dump()
    rr = write_private(bundle, "round1.json", round1)
    completion["round1"] = rr.model_dump()
    cr = write_private(bundle, "round1-completion.json", completion)
    aggregate = dict(
        schema_version="level3-aggregate-v2",
        attempt_id=ATTEMPT,
        R=REV,
        robustness_verdict="PASS",
        delivery_integrity="PASS",
        round1=rr.model_dump(),
        round1_completion=cr.model_dump(),
    )
    ar = write_private(bundle, "aggregate.json", aggregate)
    # Contents aren't parsed as a seal here: issuer owns complete seal validation.
    mr = write_private(bundle, "MANIFEST.sha256", {"test_only": "bound bytes"})
    grant = dict(
        **profile_fields(profile),
        schema_version="level3-release-grant-v1",
        attempt_id=ATTEMPT,
        R=REV,
        action_policy_version="MOCK-NOTIFY-V1",
        bundle=dict(
            relative_path="robustness",
            aggregate_sha256=ar.sha256,
            manifest_sha256=mr.sha256,
            round1_sha256=rr.sha256,
            round1_completion_sha256=cr.sha256,
            prepared_attempt_sha256=pr.sha256,
        ),
        publications=dict(
            attempt_json_sha256=refs["attempt.json"].sha256,
            golden_flow_sha256=refs["golden-flow.json"].sha256,
            fault_5class_sha256=refs["fault-5class.json"].sha256,
        ),
        images=dict(backend="sha256:" + "b" * 64, frontend="sha256:" + "c" * 64),
        verdicts=dict(integrity="PASS", robustness="PASS", delivery_integrity="PASS"),
        qualification_output_sha256=refs[subject.QUALIFICATION_NAME].sha256,
        issued_by="enable_production_level3",
        issued_at=AT,
    )
    write_private(attempt, subject.GRANT_NAME, grant)
    return dict(
        reports_root=root,
        expected_attempt_id=ATTEMPT,
        expected_revision=REV,
        expected_policy="MOCK-NOTIFY-V1",
    )


def attempt_dir(layout):
    return layout["reports_root"] / "cm-5.2" / ATTEMPT


def rewrite(path, mutate):
    value = json.loads(path.read_bytes())
    mutate(value)
    path.write_bytes(canonical_json(value) + b"\n")


def test_reader_accepts_bound_projection_without_claiming_full_recount(layout):
    result = subject.read_release_grant(**layout)
    assert result.R == REV
    assert subject.release_grant_matches(**layout)


@pytest.mark.parametrize("layout", [None, "PRODUCTION_WIDE_V1"], indirect=True)
def test_new_run_profile_expectation_cannot_reuse_other_profile_grant(layout):
    grant = subject.read_release_grant(**layout)
    own = grant.investigation_budget_profile
    assert subject.release_grant_matches(
        **layout, expected_investigation_budget_profile=own
    )
    other = "PRODUCTION_WIDE_V1" if own is None else None
    assert not subject.release_grant_matches(
        **layout, expected_investigation_budget_profile=other
    )
    assert not subject.release_grant_matches(
        **layout, expected_investigation_budget_profile="DEVELOPMENT_WIDE"
    )


@pytest.mark.parametrize("layout", ["PRODUCTION_WIDE_V1"], indirect=True)
@pytest.mark.parametrize(
    "component", ["run", "round", "prepared", "prepared_limits", "qualification"]
)
def test_even_resealed_component_profile_mix_is_rejected(layout, component):
    root = attempt_dir(layout)
    bundle = root / "robustness"
    if component in {"prepared", "prepared_limits"}:
        path = bundle / "prepared-attempt.json"
        rewrite(
            path,
            lambda v: v["effective_env"].pop("investigation_budget_profile")
            if component == "prepared"
            else v["effective_env"].update(level3_total=10),
        )
        sha = digest(path.read_bytes())
        rewrite(
            bundle / "round1.json", lambda v: v["prepared_attempt"].update(sha256=sha)
        )
        rewrite(
            root / subject.GRANT_NAME,
            lambda v: v["bundle"].update(prepared_attempt_sha256=sha),
        )
    elif component == "qualification":
        path = root / subject.QUALIFICATION_NAME
        rewrite(path, lambda v: v.pop("investigation_budget_profile"))
        rewrite(
            root / subject.GRANT_NAME,
            lambda v: v.update(qualification_output_sha256=digest(path.read_bytes())),
        )
    else:
        rewrite(
            bundle / "round1.json",
            lambda v: (v["runs"][0] if component == "run" else v).pop(
                "investigation_budget_profile"
            ),
        )
    # Update every downstream pointer, so this fails semantic profile checks,
    # not merely the first stale SHA encountered.
    rr = {
        "relative_path": "round1.json",
        "sha256": digest((bundle / "round1.json").read_bytes()),
    }
    rewrite(bundle / "round1-completion.json", lambda v: v.update(round1=rr))
    cr = {
        "relative_path": "round1-completion.json",
        "sha256": digest((bundle / "round1-completion.json").read_bytes()),
    }
    rewrite(
        bundle / "aggregate.json", lambda v: v.update(round1=rr, round1_completion=cr)
    )
    rewrite(
        root / subject.GRANT_NAME,
        lambda v: v["bundle"].update(
            round1_sha256=rr["sha256"],
            round1_completion_sha256=cr["sha256"],
            aggregate_sha256=digest((bundle / "aggregate.json").read_bytes()),
        ),
    )
    assert not subject.release_grant_matches(**layout)


def test_host_owned_files_do_not_require_container_getuid(layout, monkeypatch):
    actual_owner = layout["reports_root"].stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: actual_owner + 10000)
    assert subject.release_grant_matches(**layout)


@pytest.mark.parametrize(
    "name",
    [
        "attempt.json",
        "golden-flow.json",
        "fault-5class.json",
        "qualification-output.json",
        "robustness/aggregate.json",
        "robustness/MANIFEST.sha256",
        "robustness/round1.json",
        "robustness/round1-completion.json",
        "robustness/prepared-attempt.json",
    ],
)
def test_each_bound_file_replacement_or_missing_denied(layout, name):
    path = attempt_dir(layout) / name
    path.write_bytes(path.read_bytes() + b" ")
    assert not subject.release_grant_matches(**layout)
    path.unlink()
    assert not subject.release_grant_matches(**layout)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_revision", "e" * 40),
        ("expected_policy", "ACTION-POLICY-V1"),
        ("expected_attempt_id", "../escape"),
        ("expected_attempt_id", "20260906T000000Z-bbbbbbbbbbbb"),
    ],
)
def test_wrong_caller_binding_and_path_injection(layout, field, value):
    layout[field] = value
    assert not subject.release_grant_matches(**layout)


@pytest.mark.parametrize(
    "field,value",
    [
        ("R", "d" * 40),
        ("action_policy_version", "ACTION-POLICY-V1"),
        ("issued_by", "someone"),
        ("schema_version", "level3-release-grant-v0"),
        ("issued_at", "2026-99-06T00:00:00Z"),
        ("extra", True),
    ],
)
def test_grant_strict_schema(layout, field, value):
    rewrite(
        attempt_dir(layout) / subject.GRANT_NAME, lambda g: g.update({field: value})
    )
    assert not subject.release_grant_matches(**layout)


@pytest.mark.parametrize(
    "file,field,value,pin",
    [
        ("aggregate.json", "robustness_verdict", "FAIL", "aggregate_sha256"),
        ("aggregate.json", "delivery_integrity", "FAIL", "aggregate_sha256"),
        ("aggregate.json", "R", "d" * 40, "aggregate_sha256"),
        ("aggregate.json", "schema_version", "level3-aggregate-v1", "aggregate_sha256"),
        ("round1.json", "action_policy_version", "ACTION-POLICY-V1", "round1_sha256"),
        ("round1-completion.json", "final_status", "FAIL", "round1_completion_sha256"),
        (
            "round1-completion.json",
            "attempt_artifact_sha256",
            "f" * 64,
            "round1_completion_sha256",
        ),
    ],
)
def test_self_declared_grant_pass_does_not_override_bound_component(
    layout, file, field, value, pin
):
    root = attempt_dir(layout)
    path = root / "robustness" / file
    rewrite(path, lambda g: g.update({field: value}))
    rewrite(
        root / subject.GRANT_NAME,
        lambda g: g["bundle"].update({pin: digest(path.read_bytes())}),
    )
    assert not subject.release_grant_matches(**layout)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "mode", "oversized", "directory"]
)
def test_unsafe_grant_file_denied(layout, kind):
    path = attempt_dir(layout) / subject.GRANT_NAME
    if kind == "symlink":
        moved = path.with_name("moved.json")
        path.rename(moved)
        path.symlink_to(moved)
    elif kind == "hardlink":
        os.link(path, path.with_name("linked.json"))
    elif kind == "mode":
        path.chmod(0o644)
    elif kind == "oversized":
        path.write_bytes(b" " * (64 * 1024 + 1))
    else:
        path.unlink()
        path.mkdir()
    assert not subject.release_grant_matches(**layout)


def test_symlink_bundle_rejected(layout):
    bundle = attempt_dir(layout) / "robustness"
    moved = bundle.with_name("elsewhere")
    bundle.rename(moved)
    bundle.symlink_to(moved, target_is_directory=True)
    assert not subject.release_grant_matches(**layout)


def test_writable_mount_or_subdirectory_rejected(layout):
    attempt_dir(layout).chmod(0o777)
    assert not subject.release_grant_matches(**layout)


def test_changed_file_during_read_rejected(layout, monkeypatch):
    original = subject._read
    count = 0

    def mutate(*args, **kwargs):
        nonlocal count
        payload = original(*args, **kwargs)
        count += 1
        if count == 10:
            path = attempt_dir(layout) / "attempt.json"
            path.write_bytes(path.read_bytes() + b" ")
        return payload

    monkeypatch.setattr(subject, "_read", mutate)
    assert not subject.release_grant_matches(**layout)


def test_failures_are_bounded_without_raw_paths(layout):
    (attempt_dir(layout) / subject.GRANT_NAME).write_text("PRIVATE_INVALID_CONTENT")
    with pytest.raises(Exception, match="^RELEASE_GRANT_MISMATCH$") as exc:
        subject.read_release_grant(**layout)
    assert str(layout["reports_root"]) not in str(exc.value)


def test_reader_never_opens_fence_or_mutates_artifacts(layout):
    root = layout["reports_root"]
    fence = root / "level3-run-fence.json"
    fence.write_text('{"state":"CLOSED"}')
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert subject.release_grant_matches(**layout)
    assert {p: p.read_bytes() for p in before} == before
