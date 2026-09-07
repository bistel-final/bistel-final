"""Observe Common-owned restoration; never modify shared n8n settings.

Common restores WF3/4 to each workflow's prepare-time declared values after
evidence collection. Cleanup still removes E2E first and restores production.
"""

import os

from app.agent.release_artifacts import (
    EvidenceError,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_n8n_session import N8nSessionEvidenceApi
from app.agent.release_stage2_inputs import n8n_settings


def verify_restored(a):
    if not (a / "preparation-capture.json").exists():
        raise EvidenceError("N8N_RETENTION_ORIGINAL_UNAVAILABLE")
    from app.agent.release_prepare import parse_capture

    capture = parse_capture(parse_json(read_private(a, "preparation-capture.json")))
    original = getattr(capture, "n8n_original_retention", None)
    if original is None:
        raise EvidenceError("N8N_RETENTION_ORIGINAL_UNAVAILABLE")
    expected = original.model_dump()
    values, workflows, _ = n8n_settings()
    with N8nSessionEvidenceApi(
        values["N8N_BASE_URL"],
        values["N8N_USERNAME"],
        values["N8N_PASSWORD"],
        allow_insecure_http=os.environ.get("CM52_ALLOW_INSECURE_N8N_HTTP") == "true",
    ) as api:
        observed = []
        for _ in range(2):
            rows = {}
            for name in ("WF3", "WF4"):
                data = api.workflow(workflows[name])
                try:
                    declared = data["_evidence_retention_source"]["declared"]
                    restored = {
                        key: declared.get(key)
                        for key in (
                            "saveDataSuccessExecution",
                            "saveDataErrorExecution",
                        )
                    }
                except (KeyError, AttributeError, TypeError):
                    raise EvidenceError("N8N_RETENTION_RESTORE_REQUIRED") from None
                if data.get("active") is not True or restored != expected[name]:
                    raise EvidenceError("N8N_RETENTION_RESTORE_REQUIRED")
                rows[name] = dict(
                    workflow_id=workflows[name],
                    version=data["versionId"],
                    success=restored["saveDataSuccessExecution"],
                    error=restored["saveDataErrorExecution"],
                )
            observed.append(rows)
        if observed[0] != observed[1]:
            raise EvidenceError("N8N_RETENTION_RESTORE_DRIFT")
    config_sha = None
    if (a / "preparation-capture.json").exists():
        from app.agent.release_prepared import config_digest
        from app.agent.release_stage2_prepare import observation_client

        previous = capture.smtp_config.model_dump()
        with observation_client(previous["recipient_allowlist"]) as (
            _,
            _,
            _,
            smtp,
            current_original,
        ):
            if current_original != expected:
                raise EvidenceError("N8N_RETENTION_RESTORE_CONFIG_DRIFT")
            current = smtp()
            # WF3/4 versions legitimately change when Common restores settings;
            # SMTP transport, recipients, callback URL and WF2 must not drift.
            if any(
                current[k] != previous[k]
                for k in previous
                if k != "n8n_workflow_versions"
            ):
                raise EvidenceError("N8N_RETENTION_RESTORE_CONFIG_DRIFT")
            versions = current["n8n_workflow_versions"]
            if (
                set(versions) != set(previous["n8n_workflow_versions"])
                or versions[workflows["WF2"]]
                != previous["n8n_workflow_versions"][workflows["WF2"]]
                or any(
                    versions[row["workflow_id"]] != row["version"]
                    for row in observed[0].values()
                )
                or config_digest(current) != config_digest(smtp())
            ):
                raise EvidenceError("N8N_RETENTION_RESTORE_CONFIG_DRIFT")
            config_sha = config_digest(current)
    result = dict(status="RESTORED", workflows=observed[0], config_digest=config_sha)
    if (a / "retention-restored.json").exists():
        if parse_json(read_private(a, "retention-restored.json")) != result:
            raise EvidenceError("N8N_RETENTION_RESTORE_DRIFT")
    else:
        write_private(a, "retention-restored.json", result)
    return result
