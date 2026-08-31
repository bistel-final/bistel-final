"""평가 artifact projection 계약 — validate·stable sort·page (V5-D-2.6)."""

import json

from app.analytics import evaluation_store
from app.analytics.evaluation_store import list_evaluations, load_runs, project_run


def _payload(executed_at: str, passed: int = 2, llm: dict | None = None) -> dict:
    payload = {
        "questionset_id": "fdc_final_v1",
        "dataset_epoch": "fdc_final_20260818",
        "executed_at": executed_at,
        "grading_criteria": {},
        "total": 2,
        "passed": passed,
        "pass_threshold": 10,
        "meets_threshold": False,
        "results": [
            {
                "id": "Q01",
                "question": "전체 알람 수",
                "mode": "scalar",
                "gold_sql": "SELECT count(*) FROM trace_alarm_history",
                "generated_sql": "SELECT count(*) AS n FROM trace_alarm_history",
                "generated_chart": "table",
                "generated_rejected": False,
                "pass": True,
                "detail": [],
                "latency_ms": 900,
            },
            {
                "id": "Q02",
                "question": "테이블 지워줘",
                "mode": "scalar",
                "gold_sql": None,
                "generated_sql": None,
                "expected_chart": "bar",
                "generated_chart": None,
                "generated_rejected": True,
                "pass": passed == 2,
                "detail": [] if passed == 2 else ["거부 기대와 다름"],
                "latency_ms": 300,
            },
        ],
    }
    if llm:
        payload["llm"] = llm
    return payload


def test_project_run_maps_contract_fields():
    run = project_run(
        "20260830T010000Z",
        _payload(
            "2026-08-30T01:00:00+00:00",
            llm={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "prompt_version": "text2sql-v3",
            },
        ),
    )
    assert run.run_id == "20260830T010000Z"
    assert run.correct == 2 and run.total == 2 and run.accuracy == 1.0
    assert run.model == "gpt-4o-mini" and run.prompt_version == "text2sql-v3"
    # 거부 채점 케이스는 DEFENSE, 나머지는 GOLD
    assert run.defense_total == 1 and run.defense_passed == 1
    gold = next(item for item in run.items if item.case_type == "GOLD")
    assert gold.expected_result.startswith("SELECT count(*)")
    assert gold.actual_visualization.chart_type == "table"
    defense = next(item for item in run.items if item.case_type == "DEFENSE")
    assert defense.expected_visualization.chart_type == "bar"


def test_legacy_artifact_without_llm_meta_projects_as_unknown():
    run = project_run("20260828T000000Z", _payload("2026-08-28T00:00:00+00:00"))
    assert run.provider == "unknown" and run.prompt_version == "unknown"


def test_failed_item_carries_reason():
    run = project_run("r", _payload("2026-08-28T00:00:00+00:00", passed=1))
    failed = next(item for item in run.items if not item.passed)
    assert failed.reason == "거부 기대와 다름"


def test_load_runs_sorts_desc_and_skips_broken(tmp_path, monkeypatch):
    (tmp_path / "result_20260828T000000Z.json").write_text(
        json.dumps(_payload("2026-08-28T00:00:00+00:00")), encoding="utf-8"
    )
    (tmp_path / "result_20260830T000000Z.json").write_text(
        json.dumps(_payload("2026-08-30T00:00:00+00:00")), encoding="utf-8"
    )
    (tmp_path / "result_20260829T000000Z.json").write_text("{ broken", encoding="utf-8")
    (tmp_path / "questionset_fdc_final.json").write_text("{}", encoding="utf-8")

    runs = load_runs(tmp_path)
    assert [run.run_id for run in runs] == ["20260830T000000Z", "20260828T000000Z"]


def test_list_evaluations_latest_and_paging(tmp_path, monkeypatch):
    for stamp, at in [
        ("20260828T000000Z", "2026-08-28T00:00:00+00:00"),
        ("20260829T000000Z", "2026-08-29T00:00:00+00:00"),
        ("20260830T000000Z", "2026-08-30T00:00:00+00:00"),
    ]:
        (tmp_path / f"result_{stamp}.json").write_text(
            json.dumps(_payload(at)), encoding="utf-8"
        )
    monkeypatch.setattr(evaluation_store, "RESULT_DIR", tmp_path)

    latest = list_evaluations(latest=True)
    assert latest.total == 1 and latest.items[0].run_id == "20260830T000000Z"

    page2 = list_evaluations(page=2, size=2)
    assert page2.total == 3 and [r.run_id for r in page2.items] == ["20260828T000000Z"]
