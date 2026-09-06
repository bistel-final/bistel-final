"""Common restores metadata manually; code observes twice without a write API."""

import pytest

from app.agent import release_retention as m
from app.agent.release_artifacts import EvidenceError


@pytest.mark.parametrize("drift", [None, "smtp_host", "wf2"])
def test_restoration_rechecks_config_not_just_retention(tmp_path, monkeypatch, drift):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.agent import release_prepare, release_stage2_prepare
    from app.agent.release_artifacts import write_private
    from app.agent.release_prepared import SmtpConfigSnapshot

    tmp_path.chmod(0o700)
    write_private(tmp_path, "preparation-capture.json", {})
    previous = dict(
        n8n_workflow_versions=dict(wf2="old", wf3="old", wf4="old"),
        smtp_host="smtp.example.invalid",
        smtp_port=587,
        smtp_from="a@example.invalid",
        recipient_allowlist=["b@example.invalid"],
        wf2_callback_endpoint="https://example.invalid/internal/actions/{action_id}/delivery",
    )
    monkeypatch.setattr(
        release_prepare,
        "parse_capture",
        lambda _: SimpleNamespace(
            smtp_config=SmtpConfigSnapshot.model_validate(previous)
        ),
    )

    class Api:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def workflow(self, wid):
            return dict(
                active=True,
                versionId="restored",
                settings=dict(
                    saveDataSuccessExecution="none", saveDataErrorExecution="none"
                ),
            )

    monkeypatch.setattr(m, "N8nSessionEvidenceApi", lambda *a, **k: Api())
    monkeypatch.setattr(
        m,
        "n8n_settings",
        lambda: (
            dict(
                N8N_BASE_URL="https://example.invalid",
                N8N_USERNAME="test",
                N8N_PASSWORD="synthetic",
            ),
            dict(WF2="wf2", WF3="wf3", WF4="wf4"),
            {},
        ),
    )

    @contextmanager
    def observer(recipients):
        current = {
            **previous,
            "n8n_workflow_versions": dict(wf2="old", wf3="restored", wf4="restored"),
        }
        if drift == "wf2":
            current["n8n_workflow_versions"]["wf2"] = "changed"
        if drift == "smtp_host":
            current["smtp_host"] = "different.example.invalid"
        yield None, None, None, lambda: current

    monkeypatch.setattr(release_stage2_prepare, "observation_client", observer)
    if drift is None:
        assert len(m.verify_restored(tmp_path)["config_digest"]) == 64
    else:
        with pytest.raises(EvidenceError, match="N8N_RETENTION_RESTORE_CONFIG_DRIFT"):
            m.verify_restored(tmp_path)
        assert not (tmp_path / "retention-restored.json").exists()


@pytest.mark.parametrize("state", ["none", "all", "DEFAULT", None, "drift"])
def test_retention_restoration_reads_actual_both_workflows(
    tmp_path, monkeypatch, state
):
    tmp_path.chmod(0o700)
    calls = []

    class Api:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def workflow(self, wid):
            calls.append(wid)
            return dict(
                active=True,
                versionId="new" if state == "drift" and len(calls) > 2 else "v",
                settings=dict(
                    saveDataSuccessExecution="none" if state == "drift" else state,
                    saveDataErrorExecution="none",
                ),
            )

    monkeypatch.setattr(m, "N8nSessionEvidenceApi", lambda *a, **k: Api())
    monkeypatch.setattr(
        m,
        "n8n_settings",
        lambda: (
            dict(
                N8N_BASE_URL="https://example.invalid",
                N8N_USERNAME="test",
                N8N_PASSWORD="synthetic",
            ),
            dict(WF2="wf2", WF3="wf3", WF4="wf4"),
            {},
        ),
    )
    if state == "none":
        value = m.verify_restored(tmp_path)
        assert calls == ["wf3", "wf4", "wf3", "wf4"]
        before = (tmp_path / "retention-restored.json").read_bytes()
        assert m.verify_restored(tmp_path) == value
        assert (tmp_path / "retention-restored.json").read_bytes() == before
    else:
        with pytest.raises(EvidenceError, match="N8N_RETENTION_RESTORE"):
            m.verify_restored(tmp_path)
        assert not (tmp_path / "retention-restored.json").exists()
