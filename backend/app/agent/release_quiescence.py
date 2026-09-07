"""Read-only count used while the operator holds the admission fence."""

from sqlalchemy import text

from app.agent.release_artifacts import EvidenceError


def read_quiescence(engine):
    try:
        if engine.url.database != "kosa_agent" or engine.url.username != "kosa_app":
            raise ValueError
        with engine.connect() as c:
            c.exec_driver_sql("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            c.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            row = c.execute(text("SELECT current_database(), current_user")).one()
            if tuple(row) != ("kosa_agent", "kosa_app"):
                raise ValueError
            count = c.execute(
                text(
                    "SELECT count(*) FROM agent_run "
                    "WHERE status IN ('RUNNING', 'WAITING_APPROVAL')"
                )
            ).scalar_one()
            if type(count) is not int or count < 0:
                raise ValueError
            return {"schema_version": "level3-quiescence-v1", "active_runs": count}
    except Exception:
        raise EvidenceError("RELEASE_QUIESCENCE_UNAVAILABLE") from None
