"""Synthetic private bundle through the real offline validators, no live IO."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace as dc_replace

import pytest

from app.agent import release_aggregate as subject
from app.agent.golden_flow import GoldenPhase
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    component_ref,
    digest,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_round import RoundEvidence, build_round
from app.evaluation import fault_5class as fault_model
from tests.unit.test_agent_release import ATTEMPT, REV, S, claim, completion, outcome
from tests.unit.test_agent_release_evidence import delivery, replace  # noqa: F401
from tests.unit.test_agent_release_round import LLM, template  # noqa: F401
from tests.unit.test_fault_5class import _faults, _predictions, _provenance


def publications(evidence):
    # Fault labels belong ONLY to the offline publication fixture, not the round.
    keys = [
        fault_model.IncidentKey(
            r["route"]["incident"]["lot_id"], r["route"]["incident"]["chamber_id"]
        )
        for r in evidence["runs"]
    ]
    records = [
        dc_replace(
            p,
            incident=key,
            agent_run_id=run["run_id"],
            actual_action=run["action_code"],
            model_version=LLM["hypothesis_model_revision"],
            prompt_version=LLM["hypothesis_prompt_version"],
        )
        for p, key, run in zip(_predictions(), keys, evidence["runs"], strict=True)
    ]
    labels = [
        fault_model.IncidentFaultLabelRow(key, code or "NRM")
        for key, code in zip(keys, _faults(), strict=True)
    ]
    frozen = fault_model.freeze_predictions(records)
    result = fault_model.evaluate_fault_5class(
        frozen, labels, {r.incident: r.actual_action for r in records}
    )
    fault = fault_model.artifact_to_dict(
        result,
        dc_replace(
            _provenance(frozen.prediction_hash),
            code_revision=REV,
            golden_evidence_sha256=S,
        ),
    )
    return {
        "attempt.json": dict(
            attempt=ATTEMPT,
            revision=REV,
            project="bistel-team-e2e",
            host="test.invalid",
            **{
                f"{role}_image": f"{evidence['images'][role]['image_id']} {REV}"
                for role in ("backend", "frontend")
            },
        ),
        "golden-flow.json": dict(
            format_version=1,
            artifact_type="golden_flow_summary",
            dataset_epoch=evidence["dataset_epoch"],
            source_manifest_sha256=S,
            evidence_manifest_sha256=S,
            status="PASS",
            phases=[
                dict(phase=p.value, status="PASS", reasons=[], metrics={})
                for p in GoldenPhase
            ],
        ),
        "fault-5class.json": fault,
    }


@pytest.fixture
def bundle(delivery, template, tmp_path):  # noqa: F811 - imported pytest fixtures
    args, _, _ = delivery
    root = args["root"]
    published = tmp_path / "published"
    published.mkdir(mode=0o700)
    evidence = deepcopy(template)
    for field in ("prepared_attempt", "smtp_approval", "delivery_receipts"):
        evidence[field] = args[field].model_dump()
    round_ref = write_private(
        root, "round1.json", build_round(RoundEvidence.model_validate(evidence))
    )
    for name, value in publications(evidence).items():
        write_private(published, name, value)
    files = {
        name: read_private(root, name)
        for name in ("prepared-attempt.json", "round1.json")
    }
    for phase in ("RESUME_WORKLOAD", "PUBLISH"):
        name = f"lifecycle-claim.{phase.lower()}.json"
        data = claim(files, phase)
        if phase == "PUBLISH":
            data["claimed_at"] = "2026-09-05T02:00:00Z"
        write_private(root, name, data)
        files[name] = read_private(root, name)
        if phase == "RESUME_WORKLOAD":
            name = "lifecycle-outcome.resume_workload.json"
            write_private(root, name, outcome(files))
            files[name] = read_private(root, name)
    data = completion(files)
    data["completed_at"] = "2026-09-05T02:01:00Z"
    for field, name in (
        ("attempt_artifact_sha256", "attempt.json"),
        ("golden_flow_sha256", "golden-flow.json"),
        ("fault_5class_sha256", "fault-5class.json"),
    ):
        data[field] = component_ref(published, name).sha256
    completed_ref = write_private(root, "round1-completion.json", data)
    return dict(
        root=root,
        published_root=published,
        round1=round_ref,
        round1_completion=completed_ref,
        expected_revision=REV,
        expected_attempt_id=ATTEMPT,
    )


def read(bundle, name, *, published=False):
    return parse_json(
        read_private(bundle["published_root" if published else "root"], name)
    )


def reseal(bundle, *, round_data=None):
    root = bundle["root"]
    if round_data is not None:
        evidence = {k: v for k, v in round_data.items() if k != "batch_summary"}
        bundle["round1"] = replace(
            root, "round1.json", build_round(RoundEvidence.model_validate(evidence))
        )
    data = read(bundle, "round1-completion.json")
    data["round1"] = bundle["round1"].model_dump()
    for field, name in (
        ("attempt_artifact_sha256", "attempt.json"),
        ("golden_flow_sha256", "golden-flow.json"),
        ("fault_5class_sha256", "fault-5class.json"),
    ):
        data[field] = component_ref(bundle["published_root"], name).sha256
    bundle["round1_completion"] = replace(root, "round1-completion.json", data)


def issue(bundle, tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    return subject.emit_aggregate(
        **{k: v for k, v in bundle.items() if k not in {"round1", "round1_completion"}},
        repository=repository,
    )


def verify(bundle):
    return subject.verify_aggregate(
        bundle["root"] / "aggregate.json",
        **{
            k: bundle[k]
            for k in ("published_root", "expected_revision", "expected_attempt_id")
        },
    )


def test_real_transitive_recount_and_historic_grant_qualification(bundle, tmp_path):
    before = {p.name: p.read_bytes() for p in bundle["root"].iterdir()}
    result = subject.assess_aggregate(**bundle)
    assert result.robustness_verdict == result.delivery_integrity == "PASS"
    assert (result.round_count, result.run_count, result.repeatability) == (
        1,
        12,
        "NOT_MEASURED",
    )
    assert result.qualification_scope == "SINGLE_STAGE2_BATCH"
    assert result.provider_acceptances == 7 and result.recipient_count == 1
    assert b"Team@" not in canonical_json(result)
    assert {p.name: p.read_bytes() for p in bundle["root"].iterdir()} == before
    ref, issued = issue(bundle, tmp_path)
    assert issued == result == verify(bundle)
    assert ref.sha256 == digest(read_private(bundle["root"], "aggregate.json"))
    with pytest.raises(EvidenceError, match="AGGREGATE_ALREADY_EXISTS"):
        issue(bundle, tmp_path)


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("expected_revision", "b" * 40, "AGGREGATE_EXPECTED_BINDING_MISMATCH"),
        (
            "expected_attempt_id",
            "20260906T010000Z-aaaaaaaaaaaa",
            "AGGREGATE_EXPECTED_BINDING_MISMATCH",
        ),
    ],
)
def test_caller_pins_are_not_read_back_from_artifact(bundle, key, value, code):
    bundle[key] = value
    with pytest.raises(EvidenceError, match=code):
        subject.assess_aggregate(**bundle)


@pytest.mark.parametrize("kind", ["image", "preflight"])
def test_resealed_round_cannot_rebind_prepared_runtime(bundle, kind):
    data = read(bundle, "round1.json")
    if kind == "image":
        data["images"]["runner"]["image_id"] = "sha256:" + "f" * 64
    else:
        data["preflight_output_sha256"] = "f" * 64
    reseal(bundle, round_data=data)
    with pytest.raises(EvidenceError, match="AGGREGATE_PREPARED_BINDING_MISMATCH"):
        subject.assess_aggregate(**bundle)


@pytest.mark.parametrize(
    "name",
    [
        "round1.json",
        "round1-completion.json",
        "prepared-attempt.json",
        "lifecycle-claim.resume_workload.json",
        "lifecycle-outcome.resume_workload.json",
        "lifecycle-claim.publish.json",
    ],
)
def test_missing_proof_cannot_emit_a_pass_aggregate(bundle, tmp_path, name):
    (bundle["root"] / name).unlink()
    with pytest.raises(EvidenceError):
        issue(bundle, tmp_path)
    assert not (bundle["root"] / "aggregate.json").exists()


@pytest.mark.parametrize(
    "kind", ["message", "recipient", "receipt_missing", "grant_missing", "snapshot"]
)
def test_smtp_failure_preserves_independent_robustness_axis(bundle, kind, tmp_path):
    round_data = read(bundle, "round1.json")
    if kind in {"message", "recipient"}:
        data = read(bundle, "delivery-receipts.round1.json")
        data["executions"][0][
            "provider_message_id" if kind == "message" else "recipients"
        ] = "other" if kind == "message" else ["other@example.invalid"]
        ref = replace(bundle["root"], "delivery-receipts.round1.json", data)
        round_data["delivery_receipts"] = ref.model_dump()
    elif kind == "snapshot":
        round_data["runs"][5]["deliveries"][0]["status"] = "UNKNOWN"
    else:
        (
            bundle["root"]
            / (
                "delivery-receipts.round1.json"
                if kind == "receipt_missing"
                else "smtp-approval-grant.json"
            )
        ).unlink()
    reseal(bundle, round_data=round_data)
    _, result = issue(bundle, tmp_path)
    assert result == verify(bundle)
    assert result.robustness_verdict == "PASS" and result.delivery_integrity == "FAIL"
    assert result.delivery_failed_checks


def test_investigation_failure_does_not_invalidate_valid_acceptances(bundle, tmp_path):
    data = read(bundle, "round1.json")
    data["runs"][0]["hypothesis"]["parameter_findings"][0]["excursion_ratio"] = 999.0
    reseal(bundle, round_data=data)
    _, result = issue(bundle, tmp_path)
    assert result == verify(bundle)
    assert result.robustness_verdict == "FAIL" and result.delivery_integrity == "PASS"
    assert result.provider_acceptances == 7


@pytest.mark.parametrize(
    "kind,code",
    [
        ("attempt", "AGGREGATE_ATTEMPT_BINDING_MISMATCH"),
        ("image", "AGGREGATE_ATTEMPT_BINDING_MISMATCH"),
        ("model", "AGGREGATE_PUBLICATION_BINDING_MISMATCH"),
        ("pair", "AGGREGATE_PUBLICATION_BINDING_MISMATCH"),
        ("golden_fail", "AGGREGATE_PUBLICATION_BINDING_MISMATCH"),
        ("fault_schema", "AGGREGATE_PUBLICATION_SCHEMA_INVALID"),
    ],
)
def test_published_bytes_need_semantics_even_after_resealing_completion(
    bundle, kind, code
):
    name = (
        "attempt.json"
        if kind in {"attempt", "image"}
        else "golden-flow.json"
        if kind == "golden_fail"
        else "fault-5class.json"
    )
    data = read(bundle, name, published=True)
    if kind == "attempt":
        data["attempt"] = "20260906T010000Z-aaaaaaaaaaaa"
    elif kind == "image":
        data["backend_image"] = "sha256:" + "f" * 64 + " " + REV
    elif kind == "model":
        data["model_version"] = "other"
    elif kind == "pair":
        data["golden_evidence_sha256"] = "f" * 64
    elif kind == "golden_fail":
        data["status"] = data["phases"][0]["status"] = "FAIL"
    else:
        data["structured_prediction"]["numerator"] = 0
    replace(bundle["published_root"], name, data)
    reseal(bundle)
    with pytest.raises(EvidenceError, match=code):
        subject.assess_aggregate(**bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("robustness_verdict", "FAIL"),
        ("delivery_integrity", "FAIL"),
        ("recipient_count", 2),
        ("provider_acceptances", 6),
        ("round_count", 2),
        ("run_count", 11),
        ("repeatability", "MEASURED"),
    ],
)
def test_stored_aggregate_claims_are_always_recounted(bundle, tmp_path, field, value):
    _, result = issue(bundle, tmp_path)
    data = result.model_dump()
    data[field] = value
    replace(bundle["root"], "aggregate.json", data)
    with pytest.raises(
        EvidenceError, match="AGGREGATE_(RECOUNT_MISMATCH|SCHEMA_INVALID)"
    ):
        verify(bundle)


def test_sealed_bundle_and_repository_root_reject_emission(bundle, tmp_path):
    with pytest.raises(EvidenceError, match="LEVEL3_REPORT_ROOT_INSIDE_REPO"):
        subject.emit_aggregate(
            **{
                k: v
                for k, v in bundle.items()
                if k not in {"round1", "round1_completion"}
            },
            repository=tmp_path,
        )
    write_private(bundle["root"], "MANIFEST.sha256", {})
    with pytest.raises(EvidenceError, match="AGGREGATE_BUNDLE_SEALED"):
        issue(bundle, tmp_path)
    assert not (bundle["root"] / "aggregate.json").exists()


def test_new_lifecycle_file_during_delivery_recheck_is_drift(bundle, monkeypatch):
    original = subject.verify_delivery

    def changed(**kwargs):
        result = original(**kwargs)
        write_private(bundle["root"], "lifecycle-claim.abort.json", {})
        return result

    monkeypatch.setattr(subject, "verify_delivery", changed)
    with pytest.raises(EvidenceError, match="AGGREGATE_EVIDENCE_DRIFT"):
        subject.assess_aggregate(**bundle)


def test_publication_changed_between_completion_and_semantic_check_is_rejected(
    bundle, monkeypatch
):
    original = subject.verify_completion

    def changed(**kwargs):
        result = original(**kwargs)
        data = read(bundle, "golden-flow.json", published=True)
        data["source_manifest_sha256"] = "f" * 64
        replace(bundle["published_root"], "golden-flow.json", data)
        return result

    monkeypatch.setattr(subject, "verify_completion", changed)
    with pytest.raises(EvidenceError, match="AGGREGATE_PUBLICATION_SHA_MISMATCH"):
        subject.assess_aggregate(**bundle)


def test_cli_emits_then_revalidates_without_deployment_permission(bundle, tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    common = [
        "--published-root",
        str(bundle["published_root"]),
        "--expect-revision",
        REV,
        "--expect-attempt-id",
        ATTEMPT,
    ]
    commands = [
        [
            "scripts/emit_level3_robustness.py",
            "--aggregate",
            "--bundle-root",
            str(bundle["root"]),
            "--repository",
            str(repository),
            *common,
        ],
        [
            "scripts/validate_level3_robustness.py",
            "--artifact",
            str(bundle["root"] / "aggregate.json"),
            *common,
        ],
    ]
    for command in commands:
        run = subprocess.run([sys.executable, *command], capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        data = json.loads(run.stdout)
        assert data["status"] == "PASS" and data["deployment_authorized"] is False
        assert "Team@" not in run.stdout + run.stderr
    repeated = subprocess.run(
        [sys.executable, *commands[0]], capture_output=True, text=True
    )
    assert repeated.returncode == 1
    assert json.loads(repeated.stdout)["code"] == "AGGREGATE_ALREADY_EXISTS"


@pytest.mark.parametrize(
    "script", ["emit_level3_robustness.py", "validate_level3_robustness.py"]
)
def test_cli_bad_arguments_do_not_echo_secrets(script):
    result = subprocess.run(
        [sys.executable, f"scripts/{script}", "--secret=credential-value"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "credential-value" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["deployment_authorized"] is False


def test_import_is_lazy():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from app.agent import release_aggregate
for name in ('httpx', 'sqlalchemy', 'app.common.config', 'app.common.db'):
    assert name not in sys.modules, name
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "kind",
    ["fail_completion", "wrong_phase", "failed_outcome", "claim_sha", "extra_abort"],
)
def test_invalid_lifecycle_cannot_qualify_even_with_valid_round(bundle, tmp_path, kind):
    if kind == "fail_completion":
        data = read(bundle, "round1-completion.json")
        data.update(
            final_status="FAIL", cleanup_result="FAILED", failure_code="CLEANUP_FAILED"
        )
        bundle["round1_completion"] = replace(
            bundle["root"], "round1-completion.json", data
        )
    elif kind in {"wrong_phase", "claim_sha"}:
        data = read(bundle, "lifecycle-claim.resume_workload.json")
        if kind == "wrong_phase":
            data["phase"] = "ABORT"
        else:
            data["prepared_attempt"]["sha256"] = "f" * 64
        replace(bundle["root"], "lifecycle-claim.resume_workload.json", data)
    elif kind == "failed_outcome":
        data = read(bundle, "lifecycle-outcome.resume_workload.json")
        data.update(
            outcome="FAILED",
            cleanup_result="OK",
            restore_result="OK",
            primary_failure_code="WORKLOAD_FAILED",
            failure_code="WORKLOAD_FAILED",
        )
        replace(bundle["root"], "lifecycle-outcome.resume_workload.json", data)
    else:
        write_private(bundle["root"], "lifecycle-claim.abort.json", {})
    with pytest.raises(EvidenceError):
        issue(bundle, tmp_path)
    assert not (bundle["root"] / "aggregate.json").exists()


@pytest.mark.parametrize(
    "kind", ["escape", "absolute", "wrong_name", "hash", "symlink"]
)
def test_component_resolver_is_enforced_from_the_aggregate_entrypoint(
    bundle, tmp_path, kind
):
    _, result = issue(bundle, tmp_path)
    data = result.model_dump()
    if kind == "escape":
        data["round1"]["relative_path"] = "../round1.json"
    elif kind == "absolute":
        data["round1"]["relative_path"] = str(bundle["root"] / "round1.json")
    elif kind == "wrong_name":
        data["round1"]["relative_path"] = "another.json"
    elif kind == "hash":
        data["round1"]["sha256"] = "f" * 64
    else:
        source = bundle["root"] / "round1.json"
        copied = bundle["root"] / "another.json"
        copied.write_bytes(source.read_bytes())
        copied.chmod(0o600)
        source.unlink()
        source.symlink_to(copied)
    replace(bundle["root"], "aggregate.json", data)
    with pytest.raises(EvidenceError):
        verify(bundle)


@pytest.mark.parametrize(
    "field,value",
    [("provider_acceptances", 7.0), ("round_count", True), ("run_count", 12.0)],
)
def test_aggregate_fixed_counts_reject_type_aliases(bundle, tmp_path, field, value):
    _, result = issue(bundle, tmp_path)
    data = result.model_dump()
    data[field] = value
    replace(bundle["root"], "aggregate.json", data)
    with pytest.raises(EvidenceError, match="AGGREGATE_SCHEMA_INVALID"):
        verify(bundle)


def test_two_emitters_only_publish_once_and_never_touch_grants(bundle, tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    before = {
        name: read_private(bundle["root"], name) for name in subject.PAYLOAD_NAMES
    }
    command = [
        sys.executable,
        "scripts/emit_level3_robustness.py",
        "--aggregate",
        "--repository",
        str(repository),
        "--bundle-root",
        str(bundle["root"]),
        "--published-root",
        str(bundle["published_root"]),
        "--expect-revision",
        REV,
        "--expect-attempt-id",
        ATTEMPT,
    ]
    children = [
        subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(2)
    ]
    outputs = [child.communicate(timeout=30) for child in children]
    assert sorted(child.returncode for child in children) == [0, 1], outputs
    failed = next(
        json.loads(out[0])
        for child, out in zip(children, outputs, strict=True)
        if child.returncode == 1
    )
    assert failed["code"] in {"LIFECYCLE_LOCK_BUSY", "AGGREGATE_ALREADY_EXISTS"}
    assert {
        name: read_private(bundle["root"], name) for name in subject.PAYLOAD_NAMES
    } == before
    assert verify(bundle).delivery_integrity == "PASS"


def test_negative_axis_cli_result_is_nonzero_but_preserves_failure_artifact(
    bundle, tmp_path
):
    data = read(bundle, "round1.json")
    data["kafka_after"]["test-topic/0"] = 2
    reseal(bundle, round_data=data)
    issue(bundle, tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_level3_robustness.py",
            "--artifact",
            str(bundle["root"] / "aggregate.json"),
            "--published-root",
            str(bundle["published_root"]),
            "--expect-revision",
            REV,
            "--expect-attempt-id",
            ATTEMPT,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["robustness_verdict"] == "PASS" and body["delivery_integrity"] == "FAIL"
    assert body["deployment_authorized"] is False
    assert (bundle["root"] / "aggregate.json").is_file()


def test_declared_pass_cannot_mask_a_recounted_delivery_failure(bundle, tmp_path):
    data = read(bundle, "round1.json")
    data["kafka_after"]["test-topic/0"] = 2
    reseal(bundle, round_data=data)
    _, result = issue(bundle, tmp_path)
    assert result.delivery_integrity == "FAIL"
    forged = result.model_dump()
    forged.update(delivery_integrity="PASS", delivery_failed_checks=[])
    replace(bundle["root"], "aggregate.json", forged)
    with pytest.raises(EvidenceError, match="AGGREGATE_RECOUNT_MISMATCH"):
        verify(bundle)


def test_final_publication_recheck_rejects_late_drift(bundle, monkeypatch):
    original = subject.verify_delivery

    def changed(**kwargs):
        result = original(**kwargs)
        data = read(bundle, "golden-flow.json", published=True)
        data["source_manifest_sha256"] = "f" * 64
        replace(bundle["published_root"], "golden-flow.json", data)
        return result

    monkeypatch.setattr(subject, "verify_delivery", changed)
    with pytest.raises(EvidenceError, match="AGGREGATE_PUBLICATION_DRIFT"):
        subject.assess_aggregate(**bundle)


def test_lock_creation_is_exclusive_and_reopens_the_same_inode(bundle, monkeypatch):
    from app.agent.release_lifecycle import lifecycle_lock

    original = os.open
    seen = []

    def opened(path, flags, *args, **kwargs):
        if path == ".lifecycle.lock":
            seen.append(flags)
            # Reproduce the ambiguous create-or-open kernel race deterministically.
            if flags & os.O_CREAT and not flags & os.O_EXCL:
                raise FileNotFoundError("synthetic concurrent create race")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    with lifecycle_lock(bundle["root"]):
        inode = (bundle["root"] / ".lifecycle.lock").stat().st_ino
    with lifecycle_lock(bundle["root"]):
        assert (bundle["root"] / ".lifecycle.lock").stat().st_ino == inode
    # Each acquired lock now also opens an independent non-creating SH probe
    # to verify exclusive ownership; creation remains O_EXCL and reopen never creates.
    assert [bool(flags & os.O_EXCL) for flags in seen] == [
        True,
        False,
        True,
        False,
        False,
    ]
    assert not seen[-1] & os.O_CREAT
