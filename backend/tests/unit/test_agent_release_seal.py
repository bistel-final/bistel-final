"""Nine-file private closure and public derivation using synthetic full lineage."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.agent import release_seal as subject
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    read_private,
    write_private_bytes,
)
from tests.unit.test_agent_release import REV
from tests.unit.test_agent_release_aggregate import (
    bundle,  # noqa: F401 - composed fixture
    delivery,  # noqa: F401 - composed fixture
    issue,
    template,  # noqa: F401 - composed fixtures
)


@pytest.fixture
def sealed(bundle, tmp_path):  # noqa: F811
    issue(bundle, tmp_path)
    args = {
        k: bundle[k]
        for k in (
            "root",
            "published_root",
            "expected_revision",
            "expected_attempt_id",
        )
    }
    reference = subject.seal_bundle(repository=tmp_path / "repository", **args)
    return args, reference


def public_dir(tmp_path):
    root = tmp_path / "repository" / "output" / "v5-c-7.1" / REV
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return root


def emit(sealed, tmp_path):
    args, _ = sealed
    return subject.emit_public(
        public_root=public_dir(tmp_path),
        repository=tmp_path / "repository",
        **args,
    )


def test_manifest_exact_nine_sorted_payloads_no_self_or_lock(sealed):
    args, reference = sealed
    actual, files = subject.verify_seal(**args)
    manifest = files[subject.MANIFEST_NAME]
    assert len(manifest.splitlines()) == 9
    assert manifest == b"".join(
        f"{digest(files[name])}  {name}\n".encode()
        for name in sorted(subject.PRIVATE_PAYLOAD_NAMES)
    )
    assert b".lifecycle.lock" not in manifest
    assert b"MANIFEST.sha256" not in manifest
    assert reference.sha256 == digest(manifest)
    assert actual.robustness_verdict == actual.delivery_integrity == "PASS"
    assert (args["root"] / subject.MANIFEST_NAME).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("operation", ["derive", "verify"])
def test_readonly_paths_do_not_create_lock(sealed, tmp_path, operation):
    args, _ = sealed
    if operation == "verify":
        emit(sealed, tmp_path)
    (args["root"] / ".lifecycle.lock").unlink()
    before = {p.name: p.read_bytes() for p in args["root"].iterdir()}
    if operation == "derive":
        subject.derive_public(**args)
    else:
        subject.verify_public(path=public_dir(tmp_path) / subject.PUBLIC_NAME, **args)
    assert before == {p.name: p.read_bytes() for p in args["root"].iterdir()}


@pytest.mark.parametrize("name", subject.PRIVATE_PAYLOAD_NAMES)
def test_every_payload_missing_rejects(sealed, name):
    args, _ = sealed
    (args["root"] / name).unlink()
    with pytest.raises(EvidenceError, match="SEAL_PAYLOAD_SET_INVALID"):
        subject.derive_public(**args)


@pytest.mark.parametrize("change", ["append", "reverse", "self", "lock", "crlf"])
def test_noncanonical_manifest_rejected(sealed, change):
    args, _ = sealed
    path = args["root"] / subject.MANIFEST_NAME
    raw = path.read_bytes()
    changed = {
        "append": raw + b"\n",
        "reverse": b"".join(reversed(raw.splitlines(True))),
        "self": raw + f"{'a' * 64}  MANIFEST.sha256\n".encode(),
        "lock": raw + f"{'a' * 64}  .lifecycle.lock\n".encode(),
        "crlf": raw.replace(b"\n", b"\r\n"),
    }[change]
    path.write_bytes(changed)
    with pytest.raises(EvidenceError, match="SEAL_MANIFEST_MISMATCH"):
        subject.verify_seal(**args)


@pytest.mark.parametrize(
    "name",
    [
        "smtp-debug.json",
        "round2.json",
        "lifecycle-claim.abort.json",
        "nested",
    ],
)
def test_extra_payload_or_directory_rejected(sealed, name):
    args, _ = sealed
    if name == "nested":
        (args["root"] / name).mkdir(mode=0o700)
    else:
        write_private_bytes(args["root"], name, b"{}\n")
    with pytest.raises(EvidenceError, match="SEAL_PAYLOAD_SET_INVALID"):
        subject.verify_seal(**args)


@pytest.mark.parametrize("change", ["permissions", "symlink", "hardlink", "content"])
def test_private_file_protection(sealed, tmp_path, change):
    args, _ = sealed
    path = args["root"] / "smtp-approval-grant.json"
    if change == "permissions":
        path.chmod(0o644)
    elif change == "symlink":
        target = tmp_path / "copied.json"
        path.rename(target)
        path.symlink_to(target)
    elif change == "hardlink":
        os.link(path, tmp_path / "link.json")
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EvidenceError):
        subject.verify_seal(**args)


def test_matching_manifest_does_not_bypass_transitive_recount(sealed):
    args, _ = sealed
    path = args["root"] / "lifecycle-claim.publish.json"
    value = json.loads(path.read_bytes())
    value["phase"] = "ABORT"
    path.write_bytes(canonical_json(value) + b"\n")
    files = subject._snapshot(args["root"], sealed=True)
    (args["root"] / subject.MANIFEST_NAME).write_bytes(subject._manifest(files))
    with pytest.raises(EvidenceError):
        subject.verify_seal(**args)


def test_seal_noclobber_and_recount_before_write(bundle, tmp_path, monkeypatch):  # noqa: F811
    issue(bundle, tmp_path)
    args = {
        k: bundle[k]
        for k in (
            "root",
            "published_root",
            "expected_revision",
            "expected_attempt_id",
        )
    }
    original = subject._recount

    def drift(*a):
        result = original(*a)
        path = args["root"] / "aggregate.json"
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(subject, "_recount", drift)
    with pytest.raises(EvidenceError, match="SEAL_EVIDENCE_DRIFT"):
        subject.seal_bundle(repository=tmp_path / "repository", **args)
    assert not (args["root"] / subject.MANIFEST_NAME).exists()


def test_existing_seal_is_never_replaced(sealed, tmp_path):
    args, _ = sealed
    before = read_private(args["root"], subject.MANIFEST_NAME)
    with pytest.raises(EvidenceError, match="SEAL_PAYLOAD_SET_INVALID"):
        subject.seal_bundle(repository=tmp_path / "repository", **args)
    assert read_private(args["root"], subject.MANIFEST_NAME) == before


def test_complete_byte_identical_private_copy_manifest_last(
    sealed, tmp_path, monkeypatch
):
    args, reference = sealed
    destination = tmp_path / "protected-copy"
    destination.mkdir(mode=0o700)
    writes = []
    original = subject.write_private_bytes

    def observed(root, name, payload):
        writes.append(name)
        return original(root, name, payload)

    monkeypatch.setattr(subject, "write_private_bytes", observed)
    result = subject.copy_sealed_bundle(
        destination=destination,
        repository=tmp_path / "repository",
        **args,
    )
    assert result == reference
    assert writes == [*subject.PRIVATE_PAYLOAD_NAMES, subject.MANIFEST_NAME]
    assert subject._snapshot(args["root"], sealed=True) == subject._snapshot(
        destination, sealed=True
    )
    with pytest.raises(EvidenceError, match="SEAL_COPY_DESTINATION_NOT_EMPTY"):
        subject.copy_sealed_bundle(
            destination=destination, repository=tmp_path / "repository", **args
        )


def test_copy_partial_failure_preserves_bytes_without_manifest(
    sealed, tmp_path, monkeypatch
):
    args, _ = sealed
    destination = tmp_path / "partial-copy"
    destination.mkdir(mode=0o700)
    original = subject.write_private_bytes

    def fail(root, name, payload):
        if name == subject.PRIVATE_PAYLOAD_NAMES[1]:
            raise EvidenceError("SIMULATED_IO_FAILURE")
        return original(root, name, payload)

    monkeypatch.setattr(subject, "write_private_bytes", fail)
    with pytest.raises(EvidenceError, match="SIMULATED_IO_FAILURE"):
        subject.copy_sealed_bundle(
            destination=destination, repository=tmp_path / "repository", **args
        )
    first = subject.PRIVATE_PAYLOAD_NAMES[0]
    assert read_private(destination, first) == read_private(args["root"], first)
    assert not (destination / subject.MANIFEST_NAME).exists()


@pytest.mark.parametrize("kind", ["same", "child", "in_repo", "permissive", "symlink"])
def test_copy_rejects_unsafe_destinations(sealed, tmp_path, kind):
    args, _ = sealed
    destination = tmp_path / "copy"
    if kind == "same":
        destination = args["root"]
    elif kind == "child":
        destination = args["root"] / "copy"
        destination.mkdir(mode=0o700)
    elif kind == "in_repo":
        destination = tmp_path / "repository" / "copy"
        destination.mkdir(mode=0o700)
    elif kind == "permissive":
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
    else:
        destination.symlink_to(args["root"], target_is_directory=True)
    with pytest.raises(EvidenceError):
        subject.copy_sealed_bundle(
            destination=destination, repository=tmp_path / "repository", **args
        )


def test_deterministic_public_projection_recounts_without_private_strings(
    sealed, tmp_path
):
    args, _ = sealed
    reference, report = emit(sealed, tmp_path)
    path = public_dir(tmp_path) / subject.PUBLIC_NAME
    assert subject.verify_public(path=path, **args) == report
    assert path.read_bytes() == canonical_json(report) + b"\n"
    assert digest(path.read_bytes()) == reference.sha256
    assert b"@" not in path.read_bytes()
    assert report.derivation_rules_sha256 == digest(
        canonical_json(subject.DERIVATION_RULES)
    )
    assert report.repeatability == "NOT_MEASURED"
    assert report.run_count == 12 and report.round_count == 1
    assert len(report.batch_summary.run_assessments) == 12
    before = path.read_bytes()
    with pytest.raises(EvidenceError, match="ARTIFACT_EXISTS"):
        emit(sealed, tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change", ["recipient", "secret", "rules", "manifest", "summary", "indent", "extra"]
)
def test_public_tamper_rejected(sealed, tmp_path, change):
    args, _ = sealed
    emit(sealed, tmp_path)
    path = public_dir(tmp_path) / subject.PUBLIC_NAME
    data = json.loads(path.read_bytes())
    if change in ("recipient", "secret"):
        data["batch_summary"]["run_assessments"][0]["lot_id"] = (
            "someone@example.invalid" if change == "recipient" else "api_key=EXPOSED"
        )
    elif change in ("rules", "manifest"):
        data[
            "derivation_rules_sha256"
            if change == "rules"
            else "private_manifest_sha256"
        ] = "0" * 64
    elif change == "summary":
        data["batch_summary"]["total_tokens"] += 1
    elif change == "extra":
        data["recipients"] = ["someone@example.invalid"]
    path.write_bytes(
        (
            json.dumps(data, indent=2).encode()
            if change == "indent"
            else canonical_json(data)
        )
        + b"\n"
    )
    with pytest.raises(EvidenceError):
        subject.verify_public(path=path, **args)


@pytest.mark.parametrize(
    "secret",
    [
        "@",
        "PASSWORD=abc",
        "Bearer value",
        "https://private.invalid",
        "credential",
        "-----BEGIN",
    ],
)
def test_emitter_scan_is_on_real_derivation_path(sealed, monkeypatch, secret):
    original = subject._recount

    def unsafe(*args):
        result = original(*args).model_copy(deep=True)
        result.batch_summary.run_assessments[0].compared["injected"] = secret
        return result

    monkeypatch.setattr(subject, "_recount", unsafe)
    with pytest.raises(EvidenceError, match="PUBLIC_SECRET_DETECTED"):
        subject.derive_public(**sealed[0])


def test_exact_recipient_scan():
    with pytest.raises(EvidenceError, match="PUBLIC_RECIPIENT_DETECTED"):
        subject._scan({"x": "To: OWNER@example.invalid"}, ["owner@example.invalid"])


def test_public_cannot_be_used_as_canonical_aggregate(sealed, tmp_path):
    from app.agent.release_aggregate import verify_aggregate

    args, _ = sealed
    emit(sealed, tmp_path)
    with pytest.raises(EvidenceError, match="AGGREGATE_COMPONENT_INVALID"):
        verify_aggregate(
            public_dir(tmp_path) / subject.PUBLIC_NAME,
            **{k: v for k, v in args.items() if k != "root"},
        )


def test_public_output_cannot_escape_fixed_revision_directory(sealed, tmp_path):
    other = tmp_path / "arbitrary"
    other.mkdir(mode=0o700)
    with pytest.raises(EvidenceError, match="PUBLIC_OUTPUT_PATH_INVALID"):
        subject.emit_public(
            public_root=other, repository=tmp_path / "repository", **sealed[0]
        )
    assert not list(other.iterdir())


def test_real_cli_public_emit_verify_safe_stdout(sealed, tmp_path):
    args, _ = sealed
    backend = Path(__file__).resolve().parents[2]
    common = [
        "--published-root",
        str(args["published_root"]),
        "--expect-revision",
        REV,
        "--expect-attempt-id",
        args["expected_attempt_id"],
    ]

    def run(script, options):
        result = subprocess.run(
            [sys.executable, str(backend / "scripts" / script), *options, *common],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr == ""
        assert "@" not in result.stdout
        assert json.loads(result.stdout)["deployment_authorized"] is False

    run(
        "emit_level3_robustness.py",
        [
            "--public",
            "--bundle-root",
            str(args["root"]),
            "--repository",
            str(tmp_path / "repository"),
            "--public-root",
            str(public_dir(tmp_path)),
        ],
    )
    run(
        "validate_level3_robustness.py",
        [
            "--artifact",
            str(public_dir(tmp_path) / subject.PUBLIC_NAME),
            "--public-bundle-root",
            str(args["root"]),
        ],
    )
    run(
        "validate_level3_robustness.py",
        ["--artifact", str(args["root"] / "aggregate.json"), "--sealed"],
    )


def assert_private_upload_excluded(workflows):
    # Fail closed for every future artifact uploader, even narrow positive globs.
    # The shared production payload set is the denylist, not a prose copy.
    excluded = {
        f"!**/{name}"
        for name in (*subject.PRIVATE_PAYLOAD_NAMES, subject.MANIFEST_NAME)
    }
    for workflow in workflows:
        for job in workflow.get("jobs", {}).values():
            for step in job.get("steps", []):
                if "upload-artifact" in str(step.get("uses", "")):
                    paths = step.get("with", {}).get("path", "")
                    assert isinstance(paths, str)
                    assert excluded <= {p.strip() for p in paths.splitlines()}


def test_checked_in_ci_never_uploads_private_payloads():
    root = Path(__file__).resolve().parents[3]
    workflows = [
        yaml.safe_load(p.read_text())
        for p in (root / ".github/workflows").glob("*.yml")
    ]
    assert workflows
    assert_private_upload_excluded(workflows)


@pytest.mark.parametrize(
    "missing", [None, *subject.PRIVATE_PAYLOAD_NAMES, subject.MANIFEST_NAME]
)
def test_ci_denylist_red_for_each_missing_exclusion(missing):
    exclusions = (
        []
        if missing is None
        else [
            f"!**/{name}"
            for name in (*subject.PRIVATE_PAYLOAD_NAMES, subject.MANIFEST_NAME)
            if name != missing
        ]
    )
    workflow = {
        "jobs": {
            "test": {
                "steps": [
                    {
                        "uses": "actions/upload-artifact@v4",
                        "with": {
                            "path": "\n".join(["output/**", *exclusions]),
                        },
                    }
                ]
            }
        }
    }
    with pytest.raises(AssertionError):
        assert_private_upload_excluded([workflow])
