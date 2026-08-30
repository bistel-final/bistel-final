"""Neo4j transaction timeout termination contract on an isolated container."""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest
from neo4j import GraphDatabase, Query
from neo4j.exceptions import Neo4jError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_neo4j as neo4j_rehearsal  # noqa: E402

from app.common.tool_timeouts import (  # noqa: E402
    NEO4J_TRANSACTION_TIMEOUT_CODES,
    neo4j_timeout_error,
)

pytestmark = pytest.mark.container


def test_query_timeout_terminates_marker_transaction_and_driver_recovers() -> None:
    with neo4j_rehearsal.one_off_neo4j() as endpoint:
        driver = GraphDatabase.driver(
            endpoint.uri,
            auth=(endpoint.username, endpoint.password),
        )
        marker = f"cm48-timeout-{uuid.uuid4().hex}"
        try:
            started = time.monotonic()
            with pytest.raises(Neo4jError) as raised:
                with driver.session(database=endpoint.database) as waiter:
                    waiter.run(
                        Query(
                            "UNWIND range(1, 1000000000) AS value "
                            "WITH value, rand() AS entropy "
                            "RETURN sum(value + entropy)",
                            metadata={"cm48_marker": marker},
                            timeout=0.2,
                        )
                    ).consume()
            elapsed = time.monotonic() - started

            assert raised.value.code in NEO4J_TRANSACTION_TIMEOUT_CODES
            mapped = neo4j_timeout_error(raised.value)
            assert mapped is not None
            assert mapped.reason_code == "NEO4J_TRANSACTION_TIMEOUT"
            assert 0.08 <= elapsed < 2.0

            deadline = time.monotonic() + 2.0
            active = 1
            while active and time.monotonic() < deadline:
                with driver.session(database=endpoint.database) as observer:
                    record = observer.run(
                        "SHOW TRANSACTIONS YIELD metaData "
                        "WHERE metaData.cm48_marker = $marker "
                        "RETURN count(*) AS active",
                        {"marker": marker},
                    ).single()
                    active = int(record["active"])
                if active:
                    time.sleep(0.05)
            assert active == 0

            with driver.session(database=endpoint.database) as followup:
                assert followup.run("RETURN 1 AS value").single()["value"] == 1
        finally:
            driver.close()
