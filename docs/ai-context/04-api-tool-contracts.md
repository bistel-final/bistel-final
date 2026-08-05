# 04. API · Tool 계약

> 기준 요구사항: v1.8 / 시스템설계서: v1.2 / 역할분담: v9.5
> 마지막 동기화: 2026-08-05

Tool 반환 규격과 REST 상태코드 표는 `01-project-rules.md` 3·4절에 있다. 이 문서는 엔드포인트 목록과 DTO를 다룬다.

**계약을 바꾸려면 `docs/specifications/시스템설계서_v1_2_최종.md` 10장·10.6을 먼저 고치고 이 문서를 동기화한다.** 이 문서를 단독으로 바꾸지 않는다.

---

## 1. 공통 Pydantic v2 규칙

- 모든 모델은 `extra='forbid'`. 계약 밖 필드를 거부한다
- ID는 공백 제거 후 빈 문자열 불허
- datetime은 Asia/Seoul offset 포함 ISO 8601, date는 `YYYY-MM-DD`
- 목록 query는 `page: int = 1 (ge=1)`, `size: int = 20 (ge=1, le=100)`
- 페이지 응답은 `{items, total, page, size}`
- 숫자 집계는 `int >= 0`, 비율·점수는 `float | null`

## 2. 공통 Enum

```
Judgement            IN_CONTROL | OOC | OOS
AgentRunStatus       RUNNING | WAITING_APPROVAL | COMPLETED | FAILED
ApprovalStatus       PENDING | APPROVED | REJECTED | EXPIRED
ActionApprovalStatus AUTO | PENDING | APPROVED | REJECTED
SendStatus           WAITING | SENDING | SENT | FAILED | CANCELED
Decision             APPROVE | REJECT
Severity             LOW | MEDIUM | HIGH
ChamberStatus        NORMAL | WARNING | ALARM | CRITICAL
ToolCallStatus       SUCCESS | ERROR | TIMEOUT
ChartType            table | bar | line | histogram
```

---

## 3. 엔드포인트 목록

```http
# Common
GET  /health                         생존. 외부 장애와 무관하게 200
GET  /health/ready                   PostgreSQL·Neo4j·n8n. 하나라도 실패 시 503

# A Detection
GET  /dashboard/summary              date?, area?
GET  /summaries/{lot_hist_id}
GET  /alarms                         date?, area?, equipment_id?, chamber_id?, sensor_id?, judgement?, page, size
GET  /alarms/{alarm_id}
GET  /traces/{lot_hist_id}           sensor_id?, recipe_step_no?

# B Knowledge
GET  /relations/chambers/{chamber_id}
GET  /relations/equipment/{equipment_id}
POST /documents/search               query, model_code?, top_k=4
GET  /documents/{document_id}

# C Agent
POST /agent/runs                     {alarm_id} → 202
GET  /agent/runs/{run_id}
GET  /approvals                      status=PENDING?, page, size
POST /approvals/{approval_id}/decision
GET  /actions/{action_id}

# D Analytics
POST /analytics/query                {question}
POST /analytics/validate             {sql}  검증만, 실행 안 함
GET  /analytics/evaluations          latest=true, page, size
GET  /audit-logs                     event_type?, actor_type?, date_from?, date_to?, page, size
```

`GET /alarms`의 복수 파라미터는 **AND**로 적용한다.

### 정렬 (보조 키까지 고정)

```
/alarms          occurred_at DESC, alarm_id DESC
/approvals       requested_at DESC, approval_id DESC
/audit-logs      occurred_at DESC, audit_id DESC
평가 이력        executed_at DESC, run_id DESC
```

동일 시각에도 페이지 중복·누락이 없도록 마지막 ID를 보조 키로 쓴다.

---

## 4. `/health/ready` 상세

```json
{"status": "ready",
 "dependencies": {"postgres": {"status":"up","latency_ms":4},
                  "neo4j":    {"status":"up","latency_ms":8},
                  "n8n":      {"status":"up","latency_ms":12}}}
```

| 의존성 | 점검 | 성공 기준 |
|---|---|---|
| PostgreSQL | app write · 운영 Text2SQL readonly · 운영 QueryLog write pool에 각각 `SELECT 1` | 세 값 모두 1 |
| Neo4j | `verify_connectivity()` 후 read session `RETURN 1` | 값 1 |
| n8n | `GET {N8N_BASE_URL}/healthz/readiness` | HTTP 200 |

- `asyncio.gather()` 병렬. 의존성별 2초, 전체 3초 제한
- 실패 reason은 `TIMEOUT`·`CONNECTION_FAILED`·`UNEXPECTED_RESPONSE`로 정규화. 접속 주소·계정·원본 예외문을 응답에 넣지 않는다
- **readiness 실패가 FastAPI 프로세스를 종료시키지 않는다**
- 평가 전용 pool 2개는 readiness에 포함하지 않는다. evaluation one-shot 시작 시 별도 점검한다
- IsolationForest·임베딩 모델·LLM credential은 readiness 항목이 **아니다.** 미준비 시 해당 기능 API만 503 `MODEL_NOT_READY`/`LLM_NOT_READY`

n8n은 liveness `/healthz`가 아니라 **`/healthz/readiness`** 를 쓴다.

---

## 5. Tool 5종 고정 계약

`backend/app/common/tool_contracts.py`의 Pydantic 모델을 단일 기준으로 한다.

| Tool | Input | Success 핵심 | timeout | 기록 |
|---|---|---|---|---|
| `get_fdc_summary` | `lot_hist_id(1..20)` | `wafer`, `sensors`, `anomaly_score`, `anomaly_threshold`, `is_anomaly` | `TOOL_DB_TIMEOUT_SEC=5` | `agent_tool_call` |
| `get_equipment_context` | `chamber_id(1..20)` | `equipment`, `area`, `step`, `sibling_chambers`, `upstream`, `downstream` | `TOOL_DB_TIMEOUT_SEC=5` | `agent_tool_call` |
| `search_documents` | `query(1..1000)`, `model_code?`, `top_k(1..10)=4` | `hits: list[DocumentHit]` | `TOOL_EMBEDDING_TIMEOUT_SEC=15` | `agent_tool_call` |
| `send_action` | `action_id(1..20)`, `agent_run_id(1..20)` | `action_id`, `sent: bool` | `N8N_WEBHOOK_TIMEOUT_SEC=10` | `agent_tool_call` + 감사로그 |
| `generate_analysis_plan` | `question(1..1000)` | `sql`, `metric`, `group_by`, `visualization` | LLM 60초 | `nl_query_log` |

모델명은 `FdcSummaryToolInput/Result` 형태로 고정한다.

**앞 4종이 멘토 원안의 Agent Tool이다.** `generate_analysis_plan`은 D의 독립 Analytics Tool로 추가한 5번째이며, 초기 범위에서 LangGraph가 호출하지 않으므로 `AGENT_MAX_TOOL_CALLS=8`과 `agent_tool_call` 집계에서 제외한다.

### 원안 대비 변경 사유

- `get_fdc_summary`: `anomaly_score`·`anomaly_threshold`·`is_anomaly`·`value_std` 추가 — 모델 판단 근거 제공
- `send_action`: 원안 5개 인자 → **`action_id`·`agent_run_id` 2개**. 조치 내용은 `action_history` 생성 시 확정되고 Tool은 조회해 전송만 한다. LLM이 전송 시점에 조치를 재조합하는 위험을 없애고 멱등성을 확보한다

`latency_ms`·호출 상태는 Tool 반환값에 넣지 않는다. Tool wrapper가 호출 직전·직후를 재어 `agent_tool_call.latency_ms`에 기록한다.

---

## 6. 주요 DTO

### A Detection

```
AlarmItem              alarm_id, lot_hist_id, lot_id, wafer_no?, chamber_id?, equipment_id?,
                       sensor_id?, recipe_step_no?, recipe_step_name?, rule_id?,
                       judgement?, hit_cnt?, detail?, occurred_at
AlarmDetailResponse    AlarmItem + incident{lot_id,chamber_id}, latest_agent_run_id?, agent_run_status?
ChamberStatusItem      chamber_id, equipment_id, area_id?, status: ChamberStatus, alarm_count
DashboardSummaryResponse
                       reference_date?, area?, alarm_count, oos_count, ooc_count,
                       metrology_pass_rate?, pending_approval_count,
                       chamber_statuses[], recent_alarms[]
FdcSummaryResponse     wafer: WaferContext, sensors[], anomaly_score, anomaly_threshold, is_anomaly
TraceResponse          wafer, series[] (한계선 5개 포함)
```

`reference_date=null`은 **필터 범위에 알람이 전혀 없을 때만** 허용한다.
`metrology_pass_rate`는 분모 0이면 `0`이 아니라 `null` (화면 `N/A`).

### B Knowledge

```
ChamberRelationResponse   chamber, equipment, area?, step?, sibling_chambers[], upstream[], downstream[]
EquipmentRelationResponse equipment, chambers[], area?, step?, upstream[], downstream[]
DocumentHit               chunk_id, document_id, title, section?, score(-1..1), content, model_code?
DocumentSearchResponse    hits[], count
```

없는 ID는 404. **검색 결과 0건은 오류가 아니다** — 200 + 빈 hits.

### C Agent

```
AgentRunAcceptedResponse  agent_run_id, thread_id, incident, requested_alarm_id,
                          representative_alarm_id, status        ← HTTP 202
IncidentAlarmEvidence     alarm_ids[], lot_hist_ids[], rule_ids[],
                          distinct_oos_wafer_count, distinct_ooc_wafer_count, has_r03_consec
BatchIncidentPlan         incident, representative_alarm_id, alarm_ids[],
                          base_action_code?, final_action_code?, severity?, action_reason
UpstreamEvidence          source(batch_plan|action_history), upstream_incident, downstream_incident,
                          relationship, same_wafer, action_id?, action_code?
AgentEvidence             representative_fdc?, incident, equipment_context?, document_hits[],
                          batch_incident_plans[], upstream[], errors[]
AgentRunDetailResponse    실행·alarm_ids·tool_calls·evidence·action·approval + llm_model, latency_ms
ApprovalDecisionRequest   decision: Decision, decided_by(1..40), decision_comment?(<=1000)
ActionDetailResponse      action_id, incident, action_code, approval_status?, approved_by?,
                          send_channel?, send_status?, send_started_at?, send_attempt_count,
                          sent_at?, delivery?
```

`evidence`·`delivery`에 임의 dict로 새 필드를 추가하지 않는다. 각각 모델을 정의하고 OpenAPI·checkpoint 직렬화 테스트로 호환성을 확인한다.

`llm_model`·`latency_ms`는 **DB 종료 시점에 필수**다. 진행 중 응답의 `latency_ms`는 현재까지 누적 활성시간을 반환한다.

legacy approval(`action_id=null`)은 목록에서는 직렬화하되 결정 API에서 409 `LEGACY_APPROVAL_NOT_LINKED`로 안전 실패한다.

### D Analytics

```
AnalysisQueryResponse  question, sql, columns[], rows[], row_count, metric,
                       metric_result, group_by[], visualization, latency_ms
MetricPlan             type(count|sum|mean|median|std|min|max|percentile|ratio), column?, p?(0..100)
VisualizationPlan      chart_type: ChartType, x?, y?
SqlValidateResponse    valid, normalized_sql?, reason
EvaluationResponse     run_id, executed_at, provider, model, temperature, prompt_version,
                       correct, total, accuracy, defense_passed, defense_total, items[]
AuditLogResponse       items[], total, page, size, event_type_counts
```

`group_by=[]`이면 `metric_result`는 단일 숫자 또는 null, 그룹이 있으면 `[{group, value}]`.
`event_type_counts`는 현재 페이지가 아니라 **동일 필터 전체 결과**의 집계다.
`GET /analytics/evaluations`는 결과 파일이 없어도 200 + `items=[]`, `total=0`.

---

## 7. Agent 실행은 비동기다

`POST /agent/runs`는 HTTP 안에서 LLM·Tool 전체를 동기 수행하지 **않는다.**

```
POST /agent/runs → run·incident 연결 행 커밋 → background task 등록 → 즉시 202 RUNNING
React → GET /agent/runs/{run_id} 를 2초 간격 polling
      → WAITING_APPROVAL · COMPLETED · FAILED 에서 중지
```

승인 결정 API도 커밋 후 `RUNNING`을 반환하고 같은 방식으로 background 재개한다.
초기 범위에서 외부 broker를 추가하지 않는다.

---

## 8. OpenAPI

FastAPI OpenAPI를 비활성화하지 않는다.

```
호스트 개발      /docs        /openapi.json
최종 Nginx 경유  /api/docs    /api/openapi.json
```

`API_ROOT_PATH`는 Compose Backend에서 `/api`, 호스트 Uvicorn 개발에서는 빈 값이다.

---

## 원본 절

```
설계 2.3   공통 예외·REST 오류 계약
설계 10.1  공통 규칙·Enum·health
설계 10.2  A DTO    설계 10.3  B DTO
설계 10.4  C DTO·비동기 실행    설계 10.5  D DTO
설계 10.6  Tool 5종 고정 계약
요구사항 6장 Tool 명세 · 11.3 API 목록 · FR-I-05·07
```
