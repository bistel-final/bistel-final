# 04. API · Tool 계약

> [!CAUTION]
> **사용 중지 — 아래 본문은 v1.9/v1.10/v9.6 기준의 구 이력이며 구현 근거로 사용하면 안 됩니다.**
> v2 요약 문서가 재생성되기 전에는 `docs/specifications/요구사항정의서_v2_0_작업본.md`,
> `docs/specifications/시스템설계서_v2_0_작업본.md`,
> `docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md`와
> `docs/planning/Task분해_WBS_v4_작업본.md`의 해당 `V4-*` Task만 사용하십시오.
> 아래 본문은 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-12

Tool 반환 규격과 REST 상태코드 표는 `01-project-rules.md` 3·4절에 있다. 이 문서는 엔드포인트 목록과 DTO를 다룬다.

**계약을 바꾸려면 `docs/specifications/시스템설계서_v1_10_최종.md` 10장·10.6을 먼저 고치고 이 문서를 동기화한다.** 이 문서를 단독으로 바꾸지 않는다.

---

## 1. 공통 Pydantic v2 규칙

- 모든 모델은 `extra='forbid'`. 계약 밖 필드를 거부한다
- ID는 공백 제거 후 빈 문자열 불허
- datetime은 Asia/Seoul offset 포함 ISO 8601, date는 `YYYY-MM-DD`
- 목록 query는 `page: int = 1 (ge=1)`, `size: int = 20 (ge=1, le=100)`
- 페이지 응답은 `{items, total, page, size}`
- 숫자 집계는 `int >= 0`, 비율·점수는 `float | null`

## 2. 공통 Enum

**코드 기준은 `backend/app/common/enums.py` 하나다.** 도메인 `schemas.py`와 `tool_contracts.py`가 여기서 import하며 각자 재정의하지 않는다. 값을 바꾸려면 설계서 10.1을 먼저 고친다.

```
Judgement            IN_CONTROL | OOC | OOS
AgentRunStatus       RUNNING | WAITING_APPROVAL | COMPLETED | FAILED
ApprovalStatus       PENDING | APPROVED | REJECTED | EXPIRED
ActionApprovalStatus AUTO | PENDING | APPROVED | REJECTED
SendStatus           WAITING | SENDING | SENT | FAILED | CANCELED
Decision             APPROVE | REJECT
Severity             LOW | MEDIUM | HIGH
ToolCallStatus       SUCCESS | ERROR | TIMEOUT
ChartType            table | bar | line | histogram
ActionCode           MONITOR | NOTIFY | LOT_HOLD | EQP_HOLD
SendChannel          EMAIL | MES
FaultCode            FOC | RFM | MFD | TMD
ActorType            SYSTEM | AGENT | HUMAN
```

`ActionCode`에 딸린 `severity`·`send_channel`·승인 필요 여부는 `enums.py`의 순수 함수
`resolve_severity()`·`resolve_send_channel()`·`requires_approval()`로 고정한다. LLM 출력이 채우지 않는다.

---

## 3. 엔드포인트 목록

```http
# A Detection
GET  /dashboard/summary              date_from?, date_to?, area?, equipment_id?, chamber_id?
GET  /summaries/{lot_hist_id}
GET  /alarms                         date_from?, date_to?, area?, equipment_id?, chamber_id?,
                                     sensor_id?, rule_id?, judgement?, page, size
GET  /alarms/{alarm_id}
GET  /traces/catalog                 조회 선택지만. 시계열 없음
POST /traces/search                  area?, equipment_id?, chamber_id?, sensor_ids[], recipe_id?,
                                     lot_id?, wafer_nos[], from?, to?

# B Knowledge
GET  /relations/chambers/{chamber_id}
GET  /relations/equipment/{equipment_id}
POST /documents/search               query, model_code?, top_k=4
GET  /documents/{document_id}

# C Agent
POST /agent/runs                     {alarm_id} → 202
GET  /agent/runs                     status?, equipment_id?, chamber_id?, date_from?, date_to?, page, size
GET  /agent/runs/{run_id}
GET  /approvals                      status?, page, size
POST /approvals/{approval_id}/decision
GET  /actions                        approval_status?, send_status?, action_code?, equipment_id?,
                                     chamber_id?, date_from?, date_to?, page, size
GET  /actions/{action_id}

# D Analytics
POST /analytics/query                {question}  기록을 남기므로 GET 이 아니다
POST /analytics/validate             {sql}  검증만, 실행 안 함
GET  /analytics/history              is_valid?, is_rejected?, date_from?, date_to?, page, size
GET  /analytics/evaluations          latest=true, page, size
GET  /audit-logs                     event_type?, actor_type?, entity_type?, entity_id?,
                                     date_from?, date_to?, page, size
```

전체 22개다. `GET /alarms`의 복수 파라미터는 **AND**로 적용한다.

`GET /health`와 `GET /health/ready`는 배포·운영 및 개발 진단용 내부 엔드포인트이므로 위 22개 업무 API와 API 명세서 개수에 포함하지 않는다. 내부 health 계약은 요구사항 FR-I-05와 설계 10.1을 따른다.

배열 조건이 있으면 `POST` + JSON body 를 쓴다(`/traces/search`·`/documents/search`). query string 에는 배열 표준 표기가 없다.

Agent 실행 식별자는 모든 응답에서 `agent_run_id` 다. `run_id` 로 쓰지 않는다. URI 의 path 이름 `/agent/runs/{run_id}` 는 응답 필드가 아니라 그대로 둔다.

### 정렬 (보조 키까지 고정)

```
/alarms          occurred_at DESC, alarm_id DESC
/agent/runs      started_at DESC, agent_run_id DESC
/approvals       requested_at DESC, approval_id DESC
/actions         created_at DESC, action_id DESC
/analytics/history  asked_at DESC, nl_query_log_id DESC
/audit-logs      occurred_at DESC, audit_id DESC
평가 이력        executed_at DESC, run_id DESC
```

동일 시각에도 페이지 중복·누락이 없도록 마지막 ID를 보조 키로 쓴다.

---

## 4. Tool 5종 고정 계약

`backend/app/common/tool_contracts.py`의 Pydantic 모델을 단일 기준으로 한다.
`ToolResult` 기반 클래스가 성공 시 `reason=""`, 실패 시 접두어 7종과 데이터 필드 비움을 검증하므로 각 Tool이 따로 확인하지 않는다.

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

## 5. 주요 DTO

### A Detection

```
AlarmItem              alarm_id, lot_hist_id, lot_id, wafer_no?, chamber_id?, equipment_id?,
                       sensor_id?, recipe_step_no?, recipe_step_name?, rule_id?,
                       judgement?, hit_cnt?, detail?(text), occurred_at,
                       incident{lot_id,chamber_id}, action_id?, action_code?, approval_status?,
                       latest_agent_run_id?, agent_run_status?
DashboardSummaryResponse
                       reference_date?, area?, date_range[2], hierarchy[], sensor_catalog[],
                       alarm_count, oos_count, ooc_count, daily_trend[], top_sensors[],
                       equipment_counts[], pending_approvals[], recent_alarms[]
FdcSummaryResponse     wafer: WaferContext, sensors[], anomaly_score, anomaly_threshold, is_anomaly
SensorSummaryItem      + unit?, point_cnt, ooc_point_cnt, oos_point_cnt
TraceCatalogResponse   areas[], equipments[], sensors[], recipes[], lots[], anomaly{threshold}
TraceSearchResponse    wafers[], limits{sensor_id: 한계선 5개 + unit + upper_only},
                       measured_step_stats[], total
```

목록과 상세는 **같은 `AlarmItem`** 이다. 목록에서 헤더 필터와 에이전트 처리상태를 함께 보여줘야 해서 상세 전용 DTO 를 두지 않는다.

대시보드는 챔버 상태 카드가 아니라 **파라미터·추이 중심**이다. `chamber_statuses`·`metrology_pass_rate`는 제거했고 `daily_trend`·`top_sensors`·`equipment_counts`로 대체했다.

`hierarchy`·`sensor_catalog`는 화면 필터 선택지를 서버가 내려주는 필드다. AREA·설비·챔버·파라미터를 프론트에 상수로 박지 않는다.

`reference_date=null`은 **필터 범위에 알람이 전혀 없을 때만** 허용한다.

트레이스 검색 응답은 센서가 아니라 **WAFER 단위**다. `(WAFER, 센서)` 조합 하나가 차트 하나에 대응한다. `upper_only=true`(`ET_REFL`)면 하한선을 그리지 않는다 — 프론트가 센서 ID로 분기하지 않는다. `ET_REFL` 규칙은 `spec_lower IS NULL` 같은 값 유무 파생이 아니라 Backend의 명시적 센서 메타데이터에서 산출하며, 원본 DB 스키마를 추가하지 않는다.

### B Knowledge

```
ChamberRelationResponse   chamber, equipment, area?, step?, sibling_chambers[], upstream[], downstream[]
EquipmentRelationResponse equipment, chambers[], area?, step?, upstream[], downstream[]
DocumentHit               chunk_id, document_id, title, section?, score(-1..1), content, model_code?
DocumentSearchResponse    query, hits[], count
DocumentDetailResponse    document_id, title, doc_type?(SPEC|MANUAL|TROUBLESHOOT), model_code?,
                          source_path?, version?, chunks[]
```

없는 ID는 404. **검색 결과 0건은 오류가 아니다** — 200 + 빈 hits.
API `document_id`는 DB `document.doc_id`·`document_chunk.doc_id`에 대응한다. `doc_type`은 `SPEC | MANUAL | TROUBLESHOOT | null`이며, 현재 데이터에 `MANUAL`이 없어도 허용 값에서 제거하지 않는다.

### C Agent

```
AgentRunAcceptedResponse  agent_run_id, thread_id, incident, requested_alarm_id,
                          representative_alarm_id, status        ← HTTP 202
IncidentAlarmEvidence     alarm_ids[], lot_hist_ids[], rule_ids[], distinct_oos_wafer_count,
                          distinct_ooc_wafer_count, has_r03_consec, sibling_alarm_counts{chamber_id:n}
BatchIncidentPlan         incident, representative_alarm_id, alarm_ids[],
                          base_action_code?, final_action_code?, severity?, action_reason
UpstreamEvidence          source(batch_plan|action_history), upstream_incident, downstream_incident,
                          relationship, same_wafer, action_id?, action_code?
AgentEvidence             representative_fdc?, r03_fdc?, incident, equipment_context?, document_hits[],
                          batch_incident_plans[], upstream[], errors[]
R03EvidenceRef            alarm_id, lot_hist_id, wafer_no, sensor_id, recipe_step_name?
AgentRunItem              agent_run_id, incident, equipment_id?, sensor_id?, recipe_step_name?,
                          alarm_count, incident_first_at?, incident_last_at?, started_at, ended_at?,
                          status, fault_code?, recommended_action?, severity?
AgentRunDetailResponse    실행·alarm_ids·tool_calls·evidence·r03_fdc_evidence?·action·approval
                          + llm_model, latency_ms
ApprovalItem              approval_id, agent_run_id, action_id?, incident, equipment_id?, sensor_id?,
                          rule_id?, action_code, severity?, requested_at, status, decided_*
ApprovalDecisionRequest   decision: Decision, decided_by(1..40), decision_comment?(<=1000)
ApprovalDecisionResponse  approval_id, action_id, agent_run_id, status(APPROVED|REJECTED),
                          agent_run_status, send_status
ActionItem                action_id, created_by_agent_run_id?, incident, equipment_id?, sensor_id?,
                          recipe_step_name?, action_code, severity?, approval_status?,
                          send_status?, send_channel?, alarm_count, created_at?
ActionDetailResponse      ActionItem + trigger_alarm_lot_hist_id?, reason?, approval_required,
                          approved_by?, approved_at?, send_started_at?, send_attempt_count,
                          sent_at?, delivery?
```

목록(`AgentRunItem`·`ActionItem`)에는 `tool_calls`·`evidence` 같은 무거운 필드를 담지 않는다. 대신 정렬·필터에 쓰는 `sensor_id`·`recipe_step_name`·`alarm_count`·구간 시각은 목록에 넣어 상세를 N번 호출하지 않게 한다.

`r03_fdc_evidence`는 `evidence.r03_fdc`(요약 전문)가 **어느 WAFER 에서 나왔는지** 가리키는 참조다. 조건부 추가 조회를 하지 않은 실행에서는 둘 다 `null`.

`evidence`·`delivery`에 임의 dict로 새 필드를 추가하지 않는다. 각각 모델을 정의하고 OpenAPI·checkpoint 직렬화 테스트로 호환성을 확인한다.

`llm_model`·`latency_ms`는 **DB 종료 시점에 필수**다. 진행 중 응답의 `latency_ms`는 현재까지 누적 활성시간을 반환한다.

`created_by_agent_run_id`는 조치를 **최초 생성한 실행**이다. 같은 `action_id`를 재실행에서 재사용해도 바꾸지 않고 legacy 조치는 null을 허용한다. 최신 실행을 추정하는 `latest_agent_run_id`로 대체하지 않는다.

legacy approval(`action_id=null`)은 목록에서는 직렬화하되 결정 API에서 409 `LEGACY_APPROVAL_NOT_LINKED`로 안전 실패한다.

### D Analytics

```
AnalysisQueryResponse  question, sql?, columns[], rows[], row_count, metric?, metric_result,
                       group_by[], visualization?, is_valid, is_rejected, reject_reason?,
                       error_msg?, latency_ms, nl_query_log_id
MetricPlan             type(count|sum|mean|median|std|min|max|percentile|ratio), column?, p?(0..100)
VisualizationPlan      chart_type: ChartType, x?, y?
SqlValidateResponse    valid, normalized_sql?, reason, checks[]?
NlQueryLogItem         nl_query_log_id, asked_at, question, generated_sql?, is_valid, is_rejected,
                       reject_reason?, row_cnt?, latency_ms?, error_msg?
EvaluationResponse     run_id, executed_at, provider, model, temperature, prompt_version,
                       correct, total, accuracy, defense_passed, defense_total, items[]
EvaluationItem         case_type(GOLD|DEFENSE), case_id, question?, passed, generated_sql?,
                       attempt_count, expected_result?, actual_result?,
                       expected_visualization?, actual_visualization?, reason?, latency_ms?
AuditLogResponse       items[], total, page, size, event_types[], event_type_counts
```

`metric.type`·`chart_type`은 **요구사항정의서 v1.9 FR-D-04·8.4가 고정한 값**이다. `avg`·`pie`·`scatter`를 쓰지 않는다.

`group_by=[]`이면 `metric_result`는 단일 숫자 또는 null, 그룹이 있으면 `[{group, value}]`.
`event_type_counts`는 현재 페이지가 아니라 **동일 필터 전체 결과**의 집계다. `event_types`는 11장 이벤트 9종 전체 목록(필터 선택지)이라 결과에 따라 줄지 않는다.
`nl_query_log_id` ↔ DB 컬럼 `nl_query_log.query_id`. `detail`(`fdc_alarm`·`audit_log`)은 jsonb 가 아니라 **text** 다.
`GET /analytics/evaluations`는 결과 파일이 없어도 200 + `items=[]`, `total=0`.

`POST /analytics/query`의 정책 거부는 SQL 실행 0회의 정상 안전 판정이므로 HTTP 200에 `is_valid=false`, `is_rejected=true`, `reject_reason`을 반환한다. 이때 `sql`·`metric`·`visualization`은 null일 수 있다. 요청 JSON 누락·타입·길이 오류만 422다. `PolicyRejectedError`와 Tool 실패 `POLICY_REJECTED:` 계약은 유지한다.

---

## 6. Agent 실행은 비동기다

`POST /agent/runs`는 HTTP 안에서 LLM·Tool 전체를 동기 수행하지 **않는다.**

```
POST /agent/runs → run·incident 연결 행 커밋 → background task 등록 → 즉시 202 RUNNING
React → GET /agent/runs/{run_id} 를 2초 간격 polling
      → WAITING_APPROVAL · COMPLETED · FAILED 에서 중지
```

승인 결정 API도 커밋 후 `RUNNING`을 반환하고 같은 방식으로 background 재개한다.
초기 범위에서 외부 broker를 추가하지 않는다.

---

## 7. OpenAPI

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
설계 10.1  공통 규칙·Enum
설계 10.2  A DTO    설계 10.3  B DTO
설계 10.4  C DTO·비동기 실행    설계 10.5  D DTO
설계 10.6  Tool 5종 고정 계약
요구사항 6장 Tool 명세 · 11.3 API 목록 · FR-I-05·07
```
