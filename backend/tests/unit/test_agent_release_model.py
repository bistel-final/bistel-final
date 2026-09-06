"""Real config projection, no DB/network/provider request."""

from types import SimpleNamespace

import pytest

from app.agent import release_model as subject
from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.common import config, llm
from scripts.read_release_model import main


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(
        llm, "_resolve_endpoint", lambda: ("https://provider.invalid/v1", "private-key")
    )
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "test-model")
    monkeypatch.setattr(llm, "LLM_TEMPERATURE", 0.1)
    monkeypatch.setattr(config, "AGENT_FAULT_EVAL_ARTIFACT_PATH", None)
    monkeypatch.setattr(config, "AGENT_GOLDEN_FLOW_SUMMARY_PATH", None)


def test_production_seed_and_temperature_are_not_u10_experiment(model):
    result = subject.collect_model_context()
    assert result.llm.seed is None and result.llm.temperature == 0.1
    assert result.endpoint_sha256 == digest(b"https://provider.invalid/v1")
    assert result.model_config_digest == digest(
        canonical_json(
            dict(llm=result.llm.model_dump(), endpoint_sha256=result.endpoint_sha256)
        )
    )
    assert "private-key" not in result.model_dump_json()
    assert "provider.invalid" not in result.model_dump_json()


def test_reasoning_request_omits_temperature(model, monkeypatch):
    monkeypatch.setattr(llm, "LLM_MODEL_MAIN", "gpt-5-test")
    assert subject.collect_model_context().llm.temperature is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://test.invalid",
        "https://user:secret@test.invalid",
        "https://test.invalid?key=secret",
        "https://test.invalid#secret",
        "https://test.invalid/ bad",
    ],
)
def test_invalid_endpoint_is_not_exported(model, monkeypatch, url):
    monkeypatch.setattr(llm, "_resolve_endpoint", lambda: (url, "secret"))
    with pytest.raises(EvidenceError, match="^RELEASE_MODEL_CONTEXT_UNAVAILABLE$"):
        subject.collect_model_context()


def test_exact_published_attempt_pair(model, monkeypatch):
    attempt = "20260905T010000Z-aaaaaaaaaaaa"

    def hashes(value):
        assert value == attempt
        return {
            name: "a" * 64
            for name in ("attempt.json", "golden-flow.json", "fault-5class.json")
        }

    monkeypatch.setattr(subject, "published_hashes", hashes)
    monkeypatch.setattr(
        config,
        "AGENT_FAULT_EVAL_ARTIFACT_PATH",
        f"/reports/cm-5.2/{attempt}/fault-5class.json",
    )
    monkeypatch.setattr(
        config,
        "AGENT_GOLDEN_FLOW_SUMMARY_PATH",
        f"/reports/cm-5.2/{attempt}/golden-flow.json",
    )
    assert subject.collect_model_context().published_attempt_id == attempt
    assert subject.collect_model_context().published_artifact_sha256 == hashes(attempt)
    monkeypatch.setattr(config, "AGENT_GOLDEN_FLOW_SUMMARY_PATH", "/reports/other.json")
    with pytest.raises(EvidenceError):
        subject.collect_model_context()


def test_docker_read_is_pinned_bounded_and_secret_free(model, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=subject.collect_model_context().model_dump_json().encode(),
        )

    monkeypatch.setattr(subject.subprocess, "run", run)
    assert subject.docker_model_context("a" * 64) == subject.collect_model_context()
    assert calls[0][0] == [
        "docker",
        "exec",
        "a" * 64,
        "python",
        "-B",
        "/workspace/backend/scripts/read_release_model.py",
    ]
    assert calls[0][1]["timeout"] == 30
    with pytest.raises(EvidenceError):
        subject.docker_model_context("backend")
    assert len(calls) == 1


def test_cli_no_arguments_or_secret_error(model, capsys):
    assert main([]) == 0
    assert "private-key" not in capsys.readouterr().out
    assert main(["--key", "secret"]) == 2
    assert "secret" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "fault", [None, "leaf_symlink", "directory_symlink", "mode", "missing", "hardlink"]
)
def test_hashes_are_actual_mounted_bytes_not_just_an_attempt_name(tmp_path, fault):
    import os

    attempt = "20260905T010000Z-aaaaaaaaaaaa"
    directory = tmp_path / "cm-5.2" / attempt
    directory.mkdir(parents=True, mode=0o700)
    expected = {}
    for name in ("attempt.json", "golden-flow.json", "fault-5class.json"):
        p = directory / name
        p.write_bytes(name.encode())
        p.chmod(0o600)
        expected[name] = digest(name.encode())
    p = directory / "fault-5class.json"
    if fault == "leaf_symlink":
        p.rename(directory / "saved")
        p.symlink_to(directory / "saved")
    elif fault == "directory_symlink":
        directory.rename(directory.with_name("saved"))
        directory.symlink_to(directory.with_name("saved"))
    elif fault == "mode":
        p.chmod(0o644)
    elif fault == "missing":
        p.unlink()
    elif fault == "hardlink":
        os.link(p, directory / "saved")
    if fault:
        with pytest.raises(EvidenceError, match="^RELEASE_PUBLICATION_INVALID$"):
            subject.published_hashes(attempt, root=tmp_path)
    else:
        assert subject.published_hashes(attempt, root=tmp_path) == expected
