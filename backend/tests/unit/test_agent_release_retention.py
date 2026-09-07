"""Common restores metadata manually; code observes twice without a write API."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.agent import release_retention as m
from app.agent.release_artifacts import EvidenceError, write_private
from app.agent.release_prepared import SmtpConfigSnapshot

ORIGINAL = {
    "WF3": {
        "saveDataSuccessExecution": "DEFAULT",
        "saveDataErrorExecution": None,
    },
    "WF4": {
        "saveDataSuccessExecution": "DEFAULT",
        "saveDataErrorExecution": "DEFAULT",
    },
}


def configured_capture(tmp_path, monkeypatch, *, drift=None):
    from app.agent import release_prepare, release_stage2_prepare

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
            smtp_config=SmtpConfigSnapshot.model_validate(previous),
            n8n_original_retention=SimpleNamespace(
                model_dump=lambda: {k: dict(v) for k, v in ORIGINAL.items()}
            ),
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
        current_retention = {k: dict(v) for k, v in ORIGINAL.items()}
        if drift == "retention_reread":
            current_retention["WF4"]["saveDataSuccessExecution"] = "none"
        yield None, None, None, lambda: current, current_retention

    monkeypatch.setattr(release_stage2_prepare, "observation_client", observer)


def settings(monkeypatch):
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


@pytest.mark.parametrize("drift", [None, "smtp_host", "wf2", "retention_reread"])
def test_restoration_rechecks_config_and_retention_source(
    tmp_path, monkeypatch, drift
):
    tmp_path.chmod(0o700)
    configured_capture(tmp_path, monkeypatch, drift=drift)
    settings(monkeypatch)

    class Api:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def workflow(self, wid):
            workflow = "WF3" if wid == "wf3" else "WF4"
            return dict(
                active=True,
                versionId="restored",
                settings=dict(
                    saveDataSuccessExecution="all", saveDataErrorExecution="all"
                ),
                _evidence_retention_source={"declared": dict(ORIGINAL[workflow])},
            )

    monkeypatch.setattr(m, "N8nSessionEvidenceApi", lambda *a, **k: Api())
    if drift is None:
        value = m.verify_restored(tmp_path)
        assert len(value["config_digest"]) == 64
        assert value["workflows"]["WF3"]["success"] == "DEFAULT"
        assert value["workflows"]["WF3"]["error"] is None
    else:
        with pytest.raises(EvidenceError, match="N8N_RETENTION_RESTORE_CONFIG_DRIFT"):
            m.verify_restored(tmp_path)
        assert not (tmp_path / "retention-restored.json").exists()


@pytest.mark.parametrize("state", ["match", "mismatch", "source_missing", "drift"])
def test_retention_restoration_matches_prepare_declared_values(
    tmp_path, monkeypatch, state
):
    tmp_path.chmod(0o700)
    configured_capture(tmp_path, monkeypatch)
    settings(monkeypatch)
    calls = []

    class Api:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def workflow(self, wid):
            calls.append(wid)
            workflow = "WF3" if wid == "wf3" else "WF4"
            declared = dict(ORIGINAL[workflow])
            if state == "mismatch" and workflow == "WF3":
                declared["saveDataSuccessExecution"] = "none"
            value = dict(
                active=True,
                versionId="changed"
                if state == "drift" and len(calls) > 2
                else "restored",
                settings=dict(
                    saveDataSuccessExecution="all", saveDataErrorExecution="all"
                ),
            )
            if state != "source_missing":
                value["_evidence_retention_source"] = {"declared": declared}
            return value

    monkeypatch.setattr(m, "N8nSessionEvidenceApi", lambda *a, **k: Api())
    if state == "match":
        value = m.verify_restored(tmp_path)
        assert calls == ["wf3", "wf4", "wf3", "wf4"]
        before = (tmp_path / "retention-restored.json").read_bytes()
        assert m.verify_restored(tmp_path) == value
        assert (tmp_path / "retention-restored.json").read_bytes() == before
    else:
        code = "DRIFT" if state == "drift" else "REQUIRED"
        with pytest.raises(EvidenceError, match=f"N8N_RETENTION_RESTORE_{code}"):
            m.verify_restored(tmp_path)
        assert not (tmp_path / "retention-restored.json").exists()


def test_missing_original_capture_is_not_replaced_with_assumed_none(tmp_path):
    tmp_path.chmod(0o700)
    with pytest.raises(EvidenceError, match="N8N_RETENTION_ORIGINAL_UNAVAILABLE"):
        m.verify_restored(tmp_path)
