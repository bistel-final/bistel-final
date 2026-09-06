"""V5-C-7.1 U7: pinned final reference와 경계 fixture의 실제 PostgreSQL 조회."""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.agent import investigation
from app.common.tool_contracts import (
    ChamberParameterHistoryToolInput,
    MetrologyResultToolInput,
)
from tests.unit import test_agent_runtime_v5_container as runtime

pytestmark = pytest.mark.container
NOW = datetime(2026, 9, 4, 9)


@pytest.fixture(scope="module")
def engine():
    runtime._final_archive()
    with runtime.postgres.one_off_postgres(database="c71_investigation") as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname="c71_investigation",
            user=endpoint.username,
            password=endpoint.password,
            autocommit=True,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute((runtime.FIXTURES / "legacy_base_schema.sql").read_text())
            cursor.execute(runtime.WAFER_ALTER)
            runtime._load_final_dataset(cursor)
            cursor.execute(runtime.v5.CANONICAL_SQL.read_text())
        value = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/c71_investigation"
        )
        try:
            yield value
        finally:
            value.dispose()


@pytest.fixture
def db(engine, monkeypatch):
    # 매 fixture의 synthetic 행은 rollback된다. final reference는 그대로 남는다.
    with engine.connect() as connection, connection.begin():

        class BoundEngine:
            def connect(self):
                from contextlib import nullcontext

                return nullcontext(connection)

        monkeypatch.setattr(investigation, "get_readonly_engine", BoundEngine)
        yield connection
        connection.rollback()


def _history():
    return investigation._load_history(
        ChamberParameterHistoryToolInput(
            chamber_id="TEST-PM1",
            parameter_id="TEST",
            step_no=1,
            before=NOW,
            n_lots=3,
        ),
        investigation.HistoryLookupContext(
            current_lot_id="TEST-C",
            incident_step_id="CT-PHOTO",
            scope="CURRENT",
        ),
    )


def _row(db, lot, wafer, value, offset, *, evaluation=True):
    key = f"{lot}-{wafer}"
    db.execute(
        text(
            "INSERT INTO lot_history (lot_hist_id, lot_id, wafer_no, wafer_id, "
            "chamber_id, step_id, track_in_at) VALUES (:id,:lot,:wafer,:id,"
            "'TEST-PM1','CT-PHOTO',:at)"
        ),
        {"id": key, "lot": lot, "wafer": wafer, "at": NOW + timedelta(hours=offset)},
    )
    db.execute(
        text(
            "INSERT INTO summary_data (lot_hist_id,parameter,step_no,"
            "value_mean,value_min,value_max) VALUES (:id,'TEST',1,:v,:v,:v)"
        ),
        {"id": key, "v": value},
    )
    if evaluation:
        db.execute(
            text(
                "INSERT INTO evaluation (lot_hist_id,parameter,step_no,"
                "ooc_point_cnt,oos_point_cnt) VALUES (:id,'TEST',1,1,0)"
            ),
            {"id": key},
        )


@pytest.mark.parametrize(
    ("prior", "current", "trend"),
    [
        ([], 10, "INSUFFICIENT"),
        ([1, 1, 1], 1, "STABLE"),
        ([1, 1, 1], 10, "SUDDEN"),
        ([1, 2, 3], 10, "DRIFT_UP"),
        ([3, 2, 1], -10, "DRIFT_DOWN"),
        ([1, 3, 2], 10, "SUDDEN"),
    ],
)
def test_real_history_trend(db, prior, current, trend):
    for index, value in enumerate(prior):
        _row(db, f"TEST-{index}", 1, value, index - 5)
    _row(db, "TEST-C", 1, current, 0)
    result = _history()
    assert result.ok and result.trend == trend
    assert result.current.wafer_count == 1
    assert result.current.lot_std is None


def test_prior_cutoff_ties_null_and_missing_evaluation(db):
    for lot, value in [("TEST-A", 1), ("TEST-B", 2), ("TEST-D", 3), ("TEST-E", 4)]:
        _row(db, lot, 1, value, -2)
    _row(db, "TEST-OVERLAP", 1, 100, -1)
    _row(db, "TEST-OVERLAP", 2, 100, 1)
    _row(db, "TEST-C", 1, 8, 0, evaluation=False)
    _row(db, "TEST-C", 2, None, 1)
    result = _history()
    assert [item.lot_id for item in result.prior] == ["TEST-E", "TEST-D", "TEST-B"]
    assert result.current.wafer_count == 1
    assert result.current.evaluation_missing == 1
    assert result.current.ooc_wafers == 0


def test_pinned_reference_history_sibling_and_all_metrology(db):
    targets = (
        db.execute(
            text(
                "SELECT h.lot_id,h.chamber_id,h.step_id,s.parameter,s.step_no,"
                "min(h.track_in_at) AS first_in "
                "FROM lot_history h JOIN summary_data s USING(lot_hist_id) "
                "GROUP BY h.lot_id,h.chamber_id,h.step_id,s.parameter,s.step_no"
            )
        )
        .mappings()
        .all()
    )
    assert len(targets) > 0
    for target in targets:
        for scope in ("CURRENT", "SIBLING"):
            result = investigation._load_history(
                ChamberParameterHistoryToolInput(
                    chamber_id=target["chamber_id"],
                    parameter_id=target["parameter"],
                    step_no=target["step_no"],
                    before=target["first_in"],
                    n_lots=3,
                ),
                investigation.HistoryLookupContext(
                    current_lot_id=target["lot_id"],
                    incident_step_id=target["step_id"],
                    scope=scope,
                ),
            )
            assert result.ok and result.current.wafer_count in (12, 13)
            assert len(result.prior) <= 3
            if scope == "SIBLING":
                assert result.prior == [] and result.trend == "INSUFFICIENT"
    metrology_targets = (
        db.execute(text("SELECT DISTINCT lot_id,step_id FROM metrology"))
        .mappings()
        .all()
    )
    results = [
        investigation._load_metrology(MetrologyResultToolInput(**row))
        for row in metrology_targets
    ]
    assert len(results) == 24
    assert all(result.ok and len(result.results) == 2 for result in results)
    assert sum(result.fail_count for result in results) == 9
    assert all(
        result.disclaimer == investigation.METROLOGY_DISCLAIMER for result in results
    )
    assert not investigation._load_metrology(
        MetrologyResultToolInput(lot_id="MISSING", step_id="CT-PHOTO")
    ).ok
