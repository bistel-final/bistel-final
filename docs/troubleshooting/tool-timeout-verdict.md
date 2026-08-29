# Agent read Tool timeout 판정표

> Task: `V5-CM-4.8` · 기준일: 2026-08-29  
> 범위: Agent read Tool 3종의 DB server 실행 구간 · 공용 서비스 접근 0회

## 판정

DB server 구간에는 hard 5초 제한이 실제 연결됐다. 다만 caller의 soft 8초는 실행 중인 Python
thread를 강제 종료하지 않으며, embedding·model은 process 격리되지 않았다. 따라서 WBS의
“Tool 시작 후 8초에 전체 작업 잔존 0”은 **부분 미충족**이다. 이 문서는 `V5-CM-5.3`이
그 제한을 숨기지 않고 그대로 인용하는 정본이다.

| Tool/구간 | caller 기준 | server 기준 | 실제 집행 API | 종료 증거 | Tool/audit 사상 | 전체 Tool hard | 잔여 한계 | sentinel 판정 |
|---|---|---|---|---|---|---|---|---|
| `get_fdc_summary` / PostgreSQL SQL 2개 | LangGraph soft 8s · `/agent/ask` 없음 | statement 시작 후 hard 5s | transaction-local `set_config('statement_timeout', ms, true)` | `test_fdc_summary_tool.py` 적용 순서·57014; PG container의 active 0·후속 `SELECT 1`·LOCAL 비누출 | `TIMEOUT: DB_STATEMENT_TIMEOUT` → audit `TIMEOUT`/`TIMEOUT` | 아니오 | SQL 뒤 anomaly model은 in-process | 자동 회수 없음 |
| `search_documents` / vector registration·SELECT | LangGraph soft 8s · `/agent/ask` 없음 | 각 statement 시작 후 hard 5s | connection open → local timeout → `register_vector` → SELECT | `test_document_search.py` exact 순서·registration 57014; `test_tool_timeout_postgres_container.py` 150ms 취소·active 0·후속 성공 | `TIMEOUT: DB_STATEMENT_TIMEOUT` → audit `TIMEOUT`/`TIMEOUT` | 아니오 | DB 앞 embedding은 in-process | 자동 회수 없음 |
| `get_equipment_context` / Neo4j query | LangGraph soft 8s · `/agent/ask` 없음 | transaction 시작 후 hard 5s | Neo4j `Query(text, timeout=5.0)` | `test_graph_context.py`; `test_tool_timeout_neo4j_container.py` actual code `TransactionTimedOutClientConfiguration`·metadata active 0·후속 `RETURN 1` | `TIMEOUT: NEO4J_TRANSACTION_TIMEOUT` → audit `TIMEOUT`/`TIMEOUT` | 아니오 | Python thread의 process hard 종료는 없음 | 자동 회수 없음 |
| `TOOL_EMBEDDING_TIMEOUT_SEC=15` | 미집행 | 미집행 | 없음(reserved) | config·`.env.example`에 미집행 명시 | 해당 없음 | 아니오 | 15초 soft/hard 제한으로 해석 금지 | 해당 없음 |
| 예약 sentinel | 해당 없음 | 해당 없음 | exact finalize predicate만 존재 | `test_tool_timeouts.py::test_reserved_tool_call_sentinel_has_no_automatic_recovery_writer`, 기존 repository unit/container | `ERROR` + `CALL_RESERVED_NOT_COMPLETED` + `output IS NULL` | 해당 없음 | process 종료와 외부 효과 부재를 증명할 identity 없음 | **비도입 유지** |

## 안전 경계

- `TOOL_DB_TIMEOUT_SEC`는 `1 <= value < 8`만 허용하며 위반하면 import 시 fail-closed다.
- PostgreSQL은 raw `QueryCanceled` 또는 SQLSTATE `57014`만, Neo4j는 exact transaction-timeout
  code만 TIMEOUT으로 분류한다. deadlock·권한·syntax·`LockClientStopped`는 오분류하지 않는다.
- timeout reason·증적에는 SQL/Cypher 원문, DSN/URI, credential, driver 원문을 남기지 않는다.
- Analytics Text2SQL·화면 조회·Agent UoW·delivery timeout 계약은 변경하지 않았다.

## 후속 재검토 조건

전체 Tool hard 종료와 sentinel 시간 기반 회수는 process 격리뿐 아니라 실행 identity,
idempotent finalize, 외부 효과 부재까지 함께 증명할 수 있을 때만 다시 검토한다.
