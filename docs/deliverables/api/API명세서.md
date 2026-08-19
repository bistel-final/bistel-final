# BISTel FDC API 명세서

> [!IMPORTANT]
> **정본 이관 고지 (2026-08-18)** — 외부 API 계약(경로·필드명·응답 형태)의 정본은
> 멘토 최종 패키지 `02_화면별_API_가이드.md`로 이관되었습니다. 본 명세서는 내부 DTO
> 상세 문서이며, 충돌 시 멘토 스펙이 우선합니다. 요약: `docs/ai-context/04`.


| 항목 | 내용 |
|---|---|
| 작성일 | 2026.08.10 |
| 최종 수정일 | 2026.08.11 |
| 팀명 | PhotoEtch |
| 팀원 | 강연권 · 방대혁 · 신동원 · 천승현 |

## 1. 공통 규약

| 항목 | 내용 |
|---|---|
| 기준 | 요구사항정의서 v1.9 · 시스템설계서 v1.10 · 역할분담 v9.6 |
| Base URL | 개발 http://localhost:8000 · 통합 배포 Nginx 상대 경로 /api |
| 인증 | 초기 폐쇄형 개발 범위에는 사용자 JWT/RBAC를 도입하지 않는다. n8n Webhook secret은 내부 연동 전용이다. |
| Content-Type | JSON Body는 application/json; charset=utf-8 |
| DTO | Pydantic v2 · extra='forbid' · 공통 Enum과 NonEmptyId 재사용 |
| 시간 | datetime은 timezone offset 포함 ISO 8601, 업무 기준 timezone은 Asia/Seoul |
| 페이지 | page >= 1, size 1..100, 응답은 items·total·page·size |
| 오류 본문 | {code, message, details}; 500·로그에 비밀번호·DSN·API Key·내부 SQL 원문을 노출하지 않는다. |
| Text2SQL 거부 | /analytics/query 정책 거부는 200 구조화 응답; malformed request는 422. Tool POLICY_REJECTED: 계약은 유지. |
| Null | `T | null`로 표기한 필드만 null 허용. Optional 파라미터는 생략 가능. |

### 1.1 공통 오류 코드

| code | HTTP | 의미 |
|---|---:|---|
| `RESOURCE_NOT_FOUND` | 404 | ID로 조회한 리소스가 없음 |
| `INCIDENT_ALREADY_RUNNING` | 409 | 같은 incident 실행이 RUNNING/WAITING_APPROVAL |
| `INCIDENT_ALREADY_PROCESSED` | 409 | 같은 incident 실행이 이미 COMPLETED |
| `APPROVAL_ALREADY_DECIDED` | 409 | 승인이 이미 결정됐거나 EXPIRED |
| `LEGACY_APPROVAL_NOT_LINKED` | 409 | legacy 승인 행에 action 연결 없음 |
| `IDEMPOTENCY_CONFLICT` | 409 | 같은 action_id의 효과 payload hash 충돌 |
| `VALIDATION_ERROR` | 422 | Body·path·query 형식 오류 |
| `POLICY_REJECTED` | 422 | Text2SQL 200 경로 밖의 명시적 HTTP 정책 예외 |
| `DEPENDENCY_NOT_READY` | 503 | PostgreSQL·Neo4j·n8n 준비 실패 |
| `MODEL_NOT_READY` | 503 | IsolationForest·Embedding 산출물 미준비 |
| `LLM_NOT_READY` | 503 | LLM credential·모델 미준비 |
| `INTERNAL_ERROR` | 500 | 예기치 못한 서버 오류 |

### 1.2 감사 이벤트 9종

`DETECTION_COMPLETED` · `AGENT_RUN_STARTED` · `CLASSIFICATION_COMPLETED` · `APPROVAL_REQUESTED` · `APPROVAL_DECIDED` · `ACTION_SENT` · `ACTION_SEND_FAILED` · `AGENT_RUN_COMPLETED` · `AGENT_RUN_FAILED`

## 2. 엔드포인트 목록

| # | 도메인 | 메소드 | URI | 설명 | 응답 |
|---:|---|:---:|---|---|---|
| 1 | A Detection | `GET` | `/dashboard/summary` | 알람 대시보드 요약 | 200 · 422 |
| 2 | A Detection | `GET` | `/summaries/{lot_hist_id}` | WAFER 센서 요약과 이상 점수 | 200 · 404 · 503 |
| 3 | A Detection | `GET` | `/alarms` | 알람 목록 | 200 · 422 |
| 4 | A Detection | `GET` | `/alarms/{alarm_id}` | 알람 상세 | 200 · 404 |
| 5 | A Detection | `GET` | `/traces/catalog` | Trace 필터 선택지와 센서 한계선 조회 | 200 |
| 6 | A Detection | `POST` | `/traces/search` | 파라미터·WAFER 다중 Trace 조회 | 200 · 422 |
| 7 | B Knowledge | `GET` | `/relations/chambers/{chamber_id}` | 챔버 기준 관계 조회 | 200 · 404 |
| 8 | B Knowledge | `GET` | `/relations/equipment/{equipment_id}` | 설비 기준 관계 조회 | 200 · 404 |
| 9 | B Knowledge | `POST` | `/documents/search` | 장비 문서 벡터 검색 | 200 · 422 · 503 |
| 10 | B Knowledge | `GET` | `/documents/{document_id}` | 문서 메타데이터와 청크 목록 | 200 · 404 |
| 11 | C Agent | `POST` | `/agent/runs` | 알람 1건으로 incident Agent 실행 생성 | 202 · 404 · 409 · 422 · 503 |
| 12 | C Agent | `GET` | `/agent/runs` | Agent 실행 목록 | 200 · 422 |
| 13 | C Agent | `GET` | `/agent/runs/{run_id}` | Agent 실행 상세·근거·Tool·조치·승인 조회 | 200 · 404 |
| 14 | C Agent | `GET` | `/approvals` | 승인 요청 목록 | 200 · 422 |
| 15 | C Agent | `POST` | `/approvals/{approval_id}/decision` | 승인 또는 반려 결정 | 200 · 404 · 409 · 422 |
| 16 | C Agent | `GET` | `/actions` | 조치 목록 | 200 · 422 |
| 17 | C Agent | `GET` | `/actions/{action_id}` | 조치 상세와 전송 상태 | 200 · 404 |
| 18 | D Analytics | `POST` | `/analytics/query` | 자연어 질의 실행 | 200 · 422 · 503 |
| 19 | D Analytics | `POST` | `/analytics/validate` | SQL 검증만 수행 | 200 · 422 |
| 20 | D Analytics | `GET` | `/analytics/history` | 자연어 질의 이력 조회 | 200 · 422 |
| 21 | D Analytics | `GET` | `/analytics/evaluations` | Text2SQL 골드·방어 평가 이력 | 200 · 422 |
| 22 | D Analytics | `GET` | `/audit-logs` | 감사로그 조회 | 200 · 422 |

## 3.1 A Detection

### 1. `GET /dashboard/summary`

알람 대시보드 요약

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `DashboardSummaryResponse`
- 기간·계층 생략 시 date_range=2026-06-01..06-04, reference_date=06-04이며 알람/OOS/OOC는 51/37/14다.
- 한쪽 기간 경계만 생략하면 선택 계층의 데이터 최소일 또는 최대일로 보완한다.
- pending_approvals는 날짜·계층 필터와 무관한 전체 PENDING 목록이다.
- top_sensors와 recent_alarms는 각각 5건이다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `date_from` | query | `date` | N | - | 생략 시 선택 계층의 최소 데이터 일자 |
| `date_to` | query | `date` | N | - | 생략 시 선택 계층의 최대 데이터 일자 |
| `area` | query | `string` | N | - | - |
| `equipment_id` | query | `string` | N | - | - |
| `chamber_id` | query | `string` | N | - | - |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `reference_date` | `string | null` | N | default=None |
| `area` | `string | null` | N | default=None |
| `date_range` | `array<string>` | Y | - |
| `hierarchy` | `array<HierarchyNode>` | Y | - |
| `sensor_catalog` | `array<string>` | Y | - |
| `alarm_count` | `integer` | Y | >= 0 |
| `oos_count` | `integer` | Y | >= 0 |
| `ooc_count` | `integer` | Y | >= 0 |
| `daily_trend` | `array<DailyTrendItem>` | Y | - |
| `top_sensors` | `array<TopSensorItem>` | Y | - |
| `equipment_counts` | `array<EquipmentCountItem>` | Y | - |
| `pending_approvals` | `array<ApprovalItem>` | Y | - |
| `recent_alarms` | `array<AlarmItem>` | Y | - |
### 2. `GET /summaries/{lot_hist_id}`

WAFER 센서 요약과 이상 점수

- 응답 코드: **200 · 404 · 503**
- 요청 Body: 없음
- 응답 모델: `FdcSummaryResponse`
- is_anomaly = anomaly_score >= anomaly_threshold 규칙을 서버가 보장한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `lot_hist_id` | path | `string` | Y | - | 1..20 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `wafer` | `WaferContext` | Y | - |
| `sensors` | `array<SensorSummaryItem>` | Y | minItems 1 |
| `anomaly_score` | `number` | Y | >= 0.0, <= 1.0 |
| `anomaly_threshold` | `number` | Y | >= 0.0, <= 1.0 |
| `is_anomaly` | `boolean` | Y | - |
### 3. `GET /alarms`

알람 목록

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `AlarmPageResponse`
- occurred_at DESC, alarm_id DESC로 정렬한다. 복수 필터는 AND다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `date_from` | query | `date` | N | - | - |
| `date_to` | query | `date` | N | - | - |
| `area` | query | `string` | N | - | - |
| `equipment_id` | query | `string` | N | - | - |
| `chamber_id` | query | `string` | N | - | - |
| `sensor_id` | query | `string` | N | - | - |
| `rule_id` | query | `enum(R01_OOS|R02_OOC|R03_CONSEC)` | N | - | - |
| `judgement` | query | `enum(IN_CONTROL|OOC|OOS)` | N | - | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<AlarmItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
### 4. `GET /alarms/{alarm_id}`

알람 상세

- 응답 코드: **200 · 404**
- 요청 Body: 없음
- 응답 모델: `AlarmItem`
- 목록과 상세는 동일 AlarmItem 계약을 사용한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `alarm_id` | path | `string` | Y | - | 1..20 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `alarm_id` | `string` | Y | minLength 1 |
| `lot_hist_id` | `string` | Y | minLength 1 |
| `lot_id` | `string` | Y | minLength 1 |
| `wafer_no` | `integer | null` | N | default=None |
| `chamber_id` | `string | null` | N | default=None |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_no` | `integer | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `rule_id` | `string | null` | N | default=None |
| `judgement` | `Judgement | null` | N | default=None |
| `hit_cnt` | `integer | null` | N | default=None |
| `detail` | `string | null` | N | default=None |
| `occurred_at` | `string` | Y | - |
| `incident` | `IncidentRef` | Y | - |
| `action_id` | `string | null` | N | default=None |
| `action_code` | `ActionCode | null` | N | default=None |
| `approval_status` | `ActionApprovalStatus | null` | N | default=None |
| `latest_agent_run_id` | `string | null` | N | default=None |
| `agent_run_status` | `AgentRunStatus | null` | N | default=None |
### 5. `GET /traces/catalog`

Trace 필터 선택지와 센서 한계선 조회

- 응답 코드: **200**
- 요청 Body: 없음
- 응답 모델: `TraceCatalogResponse`
- 시계열 값은 포함하지 않는다. 실제 조회는 POST /traces/search를 사용한다.
- ET_REFL만 upper_only=true이며 하한값 null 여부로 추론하지 않는다.

**Path·Query 파라미터**

없음

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `areas` | `array<TraceCatalogArea>` | Y | - |
| `equipments` | `array<TraceCatalogEquipment>` | Y | - |
| `sensors` | `array<TraceCatalogSensor>` | Y | - |
| `recipes` | `array<TraceCatalogRecipe>` | Y | - |
| `lots` | `array<TraceCatalogLot>` | Y | - |
| `anomaly` | `TraceCatalogAnomaly` | Y | - |
### 6. `POST /traces/search`

파라미터·WAFER 다중 Trace 조회

- 응답 코드: **200 · 422**
- 요청 모델: `TraceSearchRequest`
- 응답 모델: `TraceSearchResponse`
- sensor_ids는 1개 이상이며 중복을 허용하지 않는다. wafer_nos도 중복 금지다.
- from과 to를 함께 주면 from < to여야 한다.
- total은 고유 WAFER 수가 아니라 (lot_hist_id, sensor_id) series 수다.

**Path·Query 파라미터**

없음

**요청 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `area` | `string | null` | N | default=None |
| `equipment_id` | `string | null` | N | default=None |
| `chamber_id` | `string | null` | N | default=None |
| `sensor_ids` | `array<string>` | Y | minItems 1 |
| `recipe_id` | `string | null` | N | default=None |
| `lot_id` | `string | null` | N | default=None |
| `wafer_nos` | `array<integer>` | N | - |
| `from` | `string | null` | N | default=None |
| `to` | `string | null` | N | default=None |

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `wafers` | `array<TraceWaferSeries>` | Y | - |
| `limits` | `map<string, SensorLimits>` | Y | - |
| `measured_step_stats` | `array<MeasuredStepStat>` | Y | - |
| `total` | `integer` | Y | >= 0 |

## 3.2 B Knowledge

### 7. `GET /relations/chambers/{chamber_id}`

챔버 기준 관계 조회

- 응답 코드: **200 · 404**
- 요청 Body: 없음
- 응답 모델: `ChamberRelationResponse`
- upstream/downstream은 equipment_id, sibling은 chamber_id 오름차순이다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `chamber_id` | path | `string` | Y | - | - |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chamber` | `ChamberNode` | Y | - |
| `equipment` | `EquipmentNode` | Y | - |
| `area` | `AreaNode | null` | N | default=None |
| `step` | `ProcessStepNode | null` | N | default=None |
| `sibling_chambers` | `array<ChamberNode>` | Y | - |
| `upstream` | `array<EquipmentNode>` | Y | - |
| `downstream` | `array<EquipmentNode>` | Y | - |
### 8. `GET /relations/equipment/{equipment_id}`

설비 기준 관계 조회

- 응답 코드: **200 · 404**
- 요청 Body: 없음
- 응답 모델: `EquipmentRelationResponse`

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `equipment_id` | path | `string` | Y | - | - |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `equipment` | `EquipmentNode` | Y | - |
| `chambers` | `array<ChamberNode>` | Y | - |
| `area` | `AreaNode | null` | N | default=None |
| `step` | `ProcessStepNode | null` | N | default=None |
| `upstream` | `array<EquipmentNode>` | Y | - |
| `downstream` | `array<EquipmentNode>` | Y | - |
### 9. `POST /documents/search`

장비 문서 벡터 검색

- 응답 코드: **200 · 422 · 503**
- 요청 모델: `DocumentSearchRequest`
- 응답 모델: `DocumentSearchResponse`
- top_k 기본 4, 허용 1..10이다. 결과 0건은 200 + 빈 hits다.
- score DESC, document_id ASC, chunk_id ASC로 안정 정렬한다.

**Path·Query 파라미터**

없음

**요청 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `query` | `string` | Y | minLength 1, maxLength 1000 |
| `model_code` | `string | null` | N | default=None |
| `top_k` | `integer` | N | >= 1, <= 10, default=4 |

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `query` | `string` | Y | - |
| `hits` | `array<DocumentHit>` | Y | - |
| `count` | `integer` | Y | >= 0 |
### 10. `GET /documents/{document_id}`

문서 메타데이터와 청크 목록

- 응답 코드: **200 · 404**
- 요청 Body: 없음
- 응답 모델: `DocumentDetailResponse`
- document_id는 DB document.doc_id/document_chunk.doc_id에 대응한다.
- doc_type은 SPEC·MANUAL·TROUBLESHOOT 또는 null이다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `document_id` | path | `string` | Y | - | - |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `document_id` | `string` | Y | minLength 1 |
| `title` | `string` | Y | - |
| `doc_type` | `DocumentType | null` | N | default=None |
| `model_code` | `string | null` | N | default=None |
| `source_path` | `string | null` | N | default=None |
| `version` | `string | null` | N | default=None |
| `chunks` | `array<DocumentChunkItem>` | Y | - |

## 3.3 C Agent

### 11. `POST /agent/runs`

알람 1건으로 incident Agent 실행 생성

- 응답 코드: **202 · 404 · 409 · 422 · 503**
- 요청 모델: `AgentRunCreateRequest`
- 응답 모델: `AgentRunAcceptedResponse`
- run·incident 연결을 커밋한 뒤 background 실행하고 즉시 202를 반환한다.
- 동일 incident가 진행 중이면 INCIDENT_ALREADY_RUNNING, 완료됐으면 INCIDENT_ALREADY_PROCESSED다.

**Path·Query 파라미터**

없음

**요청 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `alarm_id` | `string` | Y | minLength 1, maxLength 20 |

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `agent_run_id` | `string` | Y | minLength 1 |
| `thread_id` | `string` | Y | minLength 1 |
| `incident` | `IncidentRef` | Y | - |
| `requested_alarm_id` | `string` | Y | minLength 1 |
| `representative_alarm_id` | `string` | Y | minLength 1 |
| `status` | `AgentRunStatus` | Y | - |
### 12. `GET /agent/runs`

Agent 실행 목록

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `AgentRunPageResponse`
- started_at DESC, agent_run_id DESC로 정렬한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `status` | query | `enum(RUNNING|WAITING_APPROVAL|COMPLETED|FAILED)` | N | - | - |
| `equipment_id` | query | `string` | N | - | - |
| `chamber_id` | query | `string` | N | - | - |
| `date_from` | query | `datetime` | N | - | - |
| `date_to` | query | `datetime` | N | - | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<AgentRunItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
### 13. `GET /agent/runs/{run_id}`

Agent 실행 상세·근거·Tool·조치·승인 조회

- 응답 코드: **200 · 404**
- 요청 Body: 없음
- 응답 모델: `AgentRunDetailResponse`
- RUNNING 응답은 2초 polling하고 WAITING_APPROVAL·COMPLETED·FAILED에서 중지한다.
- fault_code는 FOC·RFM·MFD·TMD 또는 null이며 NRM은 런타임 계약에 없다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `run_id` | path | `string` | Y | - | 응답 필드명은 agent_run_id |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `agent_run_id` | `string` | Y | minLength 1 |
| `requested_alarm_id` | `string` | Y | minLength 1 |
| `representative_alarm_id` | `string` | Y | minLength 1 |
| `alarm_ids` | `array<string>` | Y | - |
| `alarm_count` | `integer` | Y | >= 0 |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `incident_first_at` | `string | null` | N | default=None |
| `incident_last_at` | `string | null` | N | default=None |
| `thread_id` | `string` | Y | minLength 1 |
| `status` | `AgentRunStatus` | Y | - |
| `fault_code` | `FaultCode | null` | N | default=None |
| `cause_summary` | `string | null` | N | default=None |
| `recommended_action` | `ActionCode | null` | N | default=None |
| `action_reason` | `string | null` | N | default=None |
| `severity` | `Severity | null` | N | default=None |
| `approval_required` | `boolean` | Y | - |
| `confidence` | `number | null` | N | default=None |
| `evidence` | `AgentEvidence | null` | N | default=None |
| `llm_model` | `string` | Y | minLength 1 |
| `input_tokens` | `integer | null` | N | default=None |
| `output_tokens` | `integer | null` | N | default=None |
| `latency_ms` | `integer` | Y | >= 0 |
| `started_at` | `string` | Y | - |
| `ended_at` | `string | null` | N | default=None |
| `tool_calls` | `array<ToolCallItem>` | Y | - |
| `r03_fdc_evidence` | `R03EvidenceRef | null` | N | default=None |
| `action` | `ActionDetailResponse | null` | N | default=None |
| `approval` | `ApprovalItem | null` | N | default=None |
### 14. `GET /approvals`

승인 요청 목록

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `ApprovalPageResponse`
- requested_at DESC, approval_id DESC로 정렬한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `status` | query | `enum(PENDING|APPROVED|REJECTED|EXPIRED)` | N | PENDING | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<ApprovalItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
### 15. `POST /approvals/{approval_id}/decision`

승인 또는 반려 결정

- 응답 코드: **200 · 404 · 409 · 422**
- 요청 모델: `ApprovalDecisionRequest`
- 응답 모델: `ApprovalDecisionResponse`
- decision은 APPROVE 또는 REJECT다. 성공 응답 status는 APPROVED 또는 REJECTED로 좁힌다.
- 이미 처리됐거나 EXPIRED면 409 APPROVAL_ALREADY_DECIDED다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `approval_id` | path | `string` | Y | - | - |

**요청 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `decision` | `Decision` | Y | - |
| `decided_by` | `string` | Y | minLength 1, maxLength 40 |
| `decision_comment` | `string | null` | N | default=None |

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `approval_id` | `string` | Y | minLength 1 |
| `action_id` | `string` | Y | minLength 1 |
| `approval_status` | `enum(APPROVED | REJECTED)` | Y | - |
| `send_status` | `SendStatus` | Y | - |
| `agent_run_status` | `AgentRunStatus` | Y | - |
| `decided_by` | `string` | Y | minLength 1, maxLength 40 |
| `decided_at` | `string` | Y | - |
| `decision_comment` | `string | null` | N | default=None |
### 16. `GET /actions`

조치 목록

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `ActionPageResponse`
- created_at DESC, action_id DESC로 정렬한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `approval_status` | query | `enum(AUTO|PENDING|APPROVED|REJECTED)` | N | - | - |
| `send_status` | query | `enum(WAITING|SENDING|SENT|FAILED|CANCELED)` | N | - | - |
| `action_code` | query | `enum(MONITOR|NOTIFY|LOT_HOLD|EQP_HOLD)` | N | - | - |
| `equipment_id` | query | `string` | N | - | - |
| `chamber_id` | query | `string` | N | - | - |
| `date_from` | query | `datetime` | N | - | - |
| `date_to` | query | `datetime` | N | - | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<ActionItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
### 17. `GET /actions/{action_id}`

조치 상세와 전송 상태

- 응답 코드: **200 · 404**
- 요청 Body: 없음
- 응답 모델: `ActionDetailResponse`
- created_by_agent_run_id는 조치를 최초 생성한 run이며 재실행에서도 바꾸지 않는다. legacy 조치는 null이다.
- DB 컬럼은 원본 01_schema.sql이 아니라 승인된 migration으로 추가한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `action_id` | path | `string` | Y | - | - |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `action_id` | `string` | Y | minLength 1 |
| `created_by_agent_run_id` | `string | null` | N | default=None |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `action_code` | `ActionCode` | Y | - |
| `severity` | `Severity | null` | N | default=None |
| `approval_status` | `ActionApprovalStatus | null` | N | default=None |
| `send_status` | `SendStatus | null` | N | default=None |
| `send_channel` | `SendChannel | null` | N | default=None |
| `alarm_count` | `integer` | Y | >= 0 |
| `created_at` | `string | null` | N | default=None |
| `trigger_alarm_lot_hist_id` | `string | null` | N | default=None |
| `reason` | `string | null` | N | default=None |
| `approval_required` | `boolean` | Y | - |
| `approved_by` | `string | null` | N | default=None |
| `approved_at` | `string | null` | N | default=None |
| `send_started_at` | `string | null` | N | default=None |
| `send_attempt_count` | `integer` | Y | >= 0 |
| `sent_at` | `string | null` | N | default=None |
| `delivery` | `DeliveryResult | null` | N | default=None |

## 3.4 D Analytics

### 18. `POST /analytics/query`

자연어 질의 실행

- 응답 코드: **200 · 422 · 503**
- 요청 모델: `AnalysisQueryRequest`
- 응답 모델: `AnalysisQueryResponse`
- 정책 거부는 HTTP 200 + is_rejected=true 구조화 응답이며 SQL을 실행하지 않는다.
- 공백·1000자 초과 등 요청 형식 오류는 422다.
- 프론트 요청 timeout은 최대 2회 LLM 시도를 포괄하도록 150초로 설정한다.

**Path·Query 파라미터**

없음

**요청 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `question` | `string` | Y | minLength 1, maxLength 1000 |

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `question` | `string` | Y | - |
| `sql` | `string | null` | N | default=None |
| `columns` | `array<string>` | Y | - |
| `rows` | `array<object>` | Y | - |
| `row_count` | `integer` | Y | >= 0 |
| `metric` | `MetricPlan | null` | N | default=None |
| `metric_result` | `integer | number | array<GroupedMetricResult> | null` | N | default=None |
| `group_by` | `array<string>` | Y | - |
| `visualization` | `VisualizationPlan | null` | N | default=None |
| `is_valid` | `boolean` | Y | - |
| `is_rejected` | `boolean` | Y | - |
| `reject_reason` | `string | null` | N | default=None |
| `error_msg` | `string | null` | N | default=None |
| `latency_ms` | `integer` | Y | >= 0 |
| `nl_query_log_id` | `integer` | Y | >= 1 |
### 19. `POST /analytics/validate`

SQL 검증만 수행

- 응답 코드: **200 · 422**
- 요청 모델: `SqlValidateRequest`
- 응답 모델: `SqlValidateResponse`
- SQL은 1..20000자이며 실행하지 않는다.

**Path·Query 파라미터**

없음

**요청 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `sql` | `string` | Y | minLength 1, maxLength 20000 |

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `valid` | `boolean` | Y | - |
| `normalized_sql` | `string | null` | N | default=None |
| `reason` | `string` | Y | - |
| `checks` | `array<ValidationCheck> | null` | N | default=None |
### 20. `GET /analytics/history`

자연어 질의 이력 조회

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `NlQueryHistoryResponse`
- asked_at DESC, nl_query_log_id DESC로 정렬한다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `is_valid` | query | `boolean` | N | - | - |
| `is_rejected` | query | `boolean` | N | - | - |
| `date_from` | query | `datetime` | N | - | - |
| `date_to` | query | `datetime` | N | - | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<NlQueryLogItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
### 21. `GET /analytics/evaluations`

Text2SQL 골드·방어 평가 이력

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `EvaluationListResponse`

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `latest` | query | `boolean` | N | true | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<EvaluationResponse>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
### 22. `GET /audit-logs`

감사로그 조회

- 응답 코드: **200 · 422**
- 요청 Body: 없음
- 응답 모델: `AuditLogResponse`
- occurred_at DESC, audit_id DESC로 정렬한다.
- event_type_counts는 현재 페이지가 아니라 동일 필터의 전체 집계다.
- audit_log는 append-only이며 UPDATE·DELETE API를 제공하지 않는다.

**Path·Query 파라미터**

| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |
|---|---|---|:---:|---|---|
| `event_type` | query | `canonical audit event enum` | N | - | - |
| `actor_type` | query | `enum(SYSTEM|AGENT|HUMAN)` | N | - | - |
| `entity_type` | query | `string` | N | - | - |
| `entity_id` | query | `string` | N | - | - |
| `date_from` | query | `datetime` | N | - | - |
| `date_to` | query | `datetime` | N | - | - |
| `page` | query | `integer` | N | 1 | >= 1 |
| `size` | query | `integer` | N | 20 | 1..100 |

**요청 Body 필드**

없음

**응답 Body 필드**

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<AuditLogItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
| `event_types` | `array<AuditEvent>` | Y | - |
| `event_type_counts` | `map<string, integer>` | Y | - |

## 4. DTO 상세

### `ActionApprovalStatus`

없음

### `ActionCode`

없음

### `ActionDetailResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `action_id` | `string` | Y | minLength 1 |
| `created_by_agent_run_id` | `string | null` | N | default=None |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `action_code` | `ActionCode` | Y | - |
| `severity` | `Severity | null` | N | default=None |
| `approval_status` | `ActionApprovalStatus | null` | N | default=None |
| `send_status` | `SendStatus | null` | N | default=None |
| `send_channel` | `SendChannel | null` | N | default=None |
| `alarm_count` | `integer` | Y | >= 0 |
| `created_at` | `string | null` | N | default=None |
| `trigger_alarm_lot_hist_id` | `string | null` | N | default=None |
| `reason` | `string | null` | N | default=None |
| `approval_required` | `boolean` | Y | - |
| `approved_by` | `string | null` | N | default=None |
| `approved_at` | `string | null` | N | default=None |
| `send_started_at` | `string | null` | N | default=None |
| `send_attempt_count` | `integer` | Y | >= 0 |
| `sent_at` | `string | null` | N | default=None |
| `delivery` | `DeliveryResult | null` | N | default=None |

### `ActionItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `action_id` | `string` | Y | minLength 1 |
| `created_by_agent_run_id` | `string | null` | N | default=None |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `action_code` | `ActionCode` | Y | - |
| `severity` | `Severity | null` | N | default=None |
| `approval_status` | `ActionApprovalStatus | null` | N | default=None |
| `send_status` | `SendStatus | null` | N | default=None |
| `send_channel` | `SendChannel | null` | N | default=None |
| `alarm_count` | `integer` | Y | >= 0 |
| `created_at` | `string | null` | N | default=None |

### `ActionPageResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<ActionItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |

### `ActorType`

없음

### `AgentEvidence`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `representative_fdc` | `FdcSummaryToolResult | null` | N | default=None |
| `r03_fdc` | `FdcSummaryToolResult | null` | N | default=None |
| `incident` | `IncidentAlarmEvidence` | Y | - |
| `equipment_context` | `EquipmentContextToolResult | null` | N | default=None |
| `document_hits` | `array<DocumentHit>` | Y | - |
| `batch_incident_plans` | `array<BatchIncidentPlan>` | Y | - |
| `upstream` | `array<UpstreamEvidence>` | Y | - |
| `errors` | `array<EvidenceError>` | Y | - |

### `AgentRunAcceptedResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `agent_run_id` | `string` | Y | minLength 1 |
| `thread_id` | `string` | Y | minLength 1 |
| `incident` | `IncidentRef` | Y | - |
| `requested_alarm_id` | `string` | Y | minLength 1 |
| `representative_alarm_id` | `string` | Y | minLength 1 |
| `status` | `AgentRunStatus` | Y | - |

### `AgentRunCreateRequest`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `alarm_id` | `string` | Y | minLength 1, maxLength 20 |

### `AgentRunDetailResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `agent_run_id` | `string` | Y | minLength 1 |
| `requested_alarm_id` | `string` | Y | minLength 1 |
| `representative_alarm_id` | `string` | Y | minLength 1 |
| `alarm_ids` | `array<string>` | Y | - |
| `alarm_count` | `integer` | Y | >= 0 |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `incident_first_at` | `string | null` | N | default=None |
| `incident_last_at` | `string | null` | N | default=None |
| `thread_id` | `string` | Y | minLength 1 |
| `status` | `AgentRunStatus` | Y | - |
| `fault_code` | `FaultCode | null` | N | default=None |
| `cause_summary` | `string | null` | N | default=None |
| `recommended_action` | `ActionCode | null` | N | default=None |
| `action_reason` | `string | null` | N | default=None |
| `severity` | `Severity | null` | N | default=None |
| `approval_required` | `boolean` | Y | - |
| `confidence` | `number | null` | N | default=None |
| `evidence` | `AgentEvidence | null` | N | default=None |
| `llm_model` | `string` | Y | minLength 1 |
| `input_tokens` | `integer | null` | N | default=None |
| `output_tokens` | `integer | null` | N | default=None |
| `latency_ms` | `integer` | Y | >= 0 |
| `started_at` | `string` | Y | - |
| `ended_at` | `string | null` | N | default=None |
| `tool_calls` | `array<ToolCallItem>` | Y | - |
| `r03_fdc_evidence` | `R03EvidenceRef | null` | N | default=None |
| `action` | `ActionDetailResponse | null` | N | default=None |
| `approval` | `ApprovalItem | null` | N | default=None |

### `AgentRunItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `agent_run_id` | `string` | Y | minLength 1 |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `alarm_count` | `integer` | Y | >= 0 |
| `incident_first_at` | `string | null` | N | default=None |
| `incident_last_at` | `string | null` | N | default=None |
| `started_at` | `string` | Y | - |
| `ended_at` | `string | null` | N | default=None |
| `status` | `AgentRunStatus` | Y | - |
| `fault_code` | `FaultCode | null` | N | default=None |
| `recommended_action` | `ActionCode | null` | N | default=None |
| `severity` | `Severity | null` | N | default=None |

### `AgentRunPageResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<AgentRunItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |

### `AgentRunStatus`

없음

### `AlarmItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `alarm_id` | `string` | Y | minLength 1 |
| `lot_hist_id` | `string` | Y | minLength 1 |
| `lot_id` | `string` | Y | minLength 1 |
| `wafer_no` | `integer | null` | N | default=None |
| `chamber_id` | `string | null` | N | default=None |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `recipe_step_no` | `integer | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `rule_id` | `string | null` | N | default=None |
| `judgement` | `Judgement | null` | N | default=None |
| `hit_cnt` | `integer | null` | N | default=None |
| `detail` | `string | null` | N | default=None |
| `occurred_at` | `string` | Y | - |
| `incident` | `IncidentRef` | Y | - |
| `action_id` | `string | null` | N | default=None |
| `action_code` | `ActionCode | null` | N | default=None |
| `approval_status` | `ActionApprovalStatus | null` | N | default=None |
| `latest_agent_run_id` | `string | null` | N | default=None |
| `agent_run_status` | `AgentRunStatus | null` | N | default=None |

### `AlarmPageResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<AlarmItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |

### `AnalysisQueryRequest`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `question` | `string` | Y | minLength 1, maxLength 1000 |

### `AnalysisQueryResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `question` | `string` | Y | - |
| `sql` | `string | null` | N | default=None |
| `columns` | `array<string>` | Y | - |
| `rows` | `array<object>` | Y | - |
| `row_count` | `integer` | Y | >= 0 |
| `metric` | `MetricPlan | null` | N | default=None |
| `metric_result` | `integer | number | array<GroupedMetricResult> | null` | N | default=None |
| `group_by` | `array<string>` | Y | - |
| `visualization` | `VisualizationPlan | null` | N | default=None |
| `is_valid` | `boolean` | Y | - |
| `is_rejected` | `boolean` | Y | - |
| `reject_reason` | `string | null` | N | default=None |
| `error_msg` | `string | null` | N | default=None |
| `latency_ms` | `integer` | Y | >= 0 |
| `nl_query_log_id` | `integer` | Y | >= 1 |

### `ApprovalDecisionRequest`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `decision` | `Decision` | Y | - |
| `decided_by` | `string` | Y | minLength 1, maxLength 40 |
| `decision_comment` | `string | null` | N | default=None |

### `ApprovalDecisionResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `approval_id` | `string` | Y | minLength 1 |
| `action_id` | `string` | Y | minLength 1 |
| `approval_status` | `enum(APPROVED | REJECTED)` | Y | - |
| `send_status` | `SendStatus` | Y | - |
| `agent_run_status` | `AgentRunStatus` | Y | - |
| `decided_by` | `string` | Y | minLength 1, maxLength 40 |
| `decided_at` | `string` | Y | - |
| `decision_comment` | `string | null` | N | default=None |

### `ApprovalItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `approval_id` | `string` | Y | minLength 1 |
| `agent_run_id` | `string` | Y | minLength 1 |
| `action_id` | `string | null` | N | default=None |
| `incident` | `IncidentRef` | Y | - |
| `equipment_id` | `string | null` | N | default=None |
| `sensor_id` | `string | null` | N | default=None |
| `rule_id` | `string | null` | N | default=None |
| `action_code` | `ActionCode` | Y | - |
| `severity` | `Severity | null` | N | default=None |
| `requested_at` | `string` | Y | - |
| `status` | `ApprovalStatus` | Y | - |
| `decided_by` | `string | null` | N | default=None |
| `decided_at` | `string | null` | N | default=None |
| `decision_comment` | `string | null` | N | default=None |

### `ApprovalPageResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<ApprovalItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |

### `ApprovalStatus`

없음

### `AreaNode`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `area_id` | `string` | Y | minLength 1 |
| `area_name` | `string | null` | N | default=None |

### `AuditEvent`

없음

### `AuditLogItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `audit_id` | `integer` | Y | >= 1 |
| `occurred_at` | `string` | Y | - |
| `actor_type` | `ActorType` | Y | - |
| `actor_id` | `string | null` | N | default=None |
| `event_type` | `AuditEvent` | Y | - |
| `entity_type` | `string | null` | N | default=None |
| `entity_id` | `string | null` | N | default=None |
| `before` | `object | null` | N | default=None |
| `after` | `object | null` | N | default=None |
| `detail` | `string | null` | N | default=None |

### `AuditLogResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<AuditLogItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |
| `event_types` | `array<AuditEvent>` | Y | - |
| `event_type_counts` | `map<string, integer>` | Y | - |

### `BatchIncidentPlan`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `incident` | `IncidentRef` | Y | - |
| `representative_alarm_id` | `string` | Y | minLength 1 |
| `alarm_ids` | `array<string>` | Y | - |
| `base_action_code` | `ActionCode | null` | N | default=None |
| `final_action_code` | `ActionCode | null` | N | default=None |
| `severity` | `Severity | null` | N | default=None |
| `action_reason` | `string` | Y | - |

### `ChamberAlarmCount`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chamber_id` | `string` | Y | minLength 1 |
| `alarm_count` | `integer` | Y | >= 0 |

### `ChamberNode`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chamber_id` | `string` | Y | minLength 1 |
| `equipment_id` | `string` | Y | minLength 1 |
| `chamber_no` | `integer | null` | N | default=None |
| `model_code` | `string | null` | N | default=None |
| `area_id` | `string | null` | N | default=None |
| `step_id` | `string | null` | N | default=None |

### `ChamberRelationResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chamber` | `ChamberNode` | Y | - |
| `equipment` | `EquipmentNode` | Y | - |
| `area` | `AreaNode | null` | N | default=None |
| `step` | `ProcessStepNode | null` | N | default=None |
| `sibling_chambers` | `array<ChamberNode>` | Y | - |
| `upstream` | `array<EquipmentNode>` | Y | - |
| `downstream` | `array<EquipmentNode>` | Y | - |

### `ChartType`

없음

### `DailyTrendItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `date` | `string` | Y | - |
| `oos_count` | `integer` | Y | >= 0 |
| `ooc_count` | `integer` | Y | >= 0 |
| `has_r03_consec` | `boolean` | Y | - |

### `DashboardSummaryResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `reference_date` | `string | null` | N | default=None |
| `area` | `string | null` | N | default=None |
| `date_range` | `array<string>` | Y | - |
| `hierarchy` | `array<HierarchyNode>` | Y | - |
| `sensor_catalog` | `array<string>` | Y | - |
| `alarm_count` | `integer` | Y | >= 0 |
| `oos_count` | `integer` | Y | >= 0 |
| `ooc_count` | `integer` | Y | >= 0 |
| `daily_trend` | `array<DailyTrendItem>` | Y | - |
| `top_sensors` | `array<TopSensorItem>` | Y | - |
| `equipment_counts` | `array<EquipmentCountItem>` | Y | - |
| `pending_approvals` | `array<ApprovalItem>` | Y | - |
| `recent_alarms` | `array<AlarmItem>` | Y | - |

### `Decision`

없음

### `DeliveryResult`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `action_id` | `string` | Y | minLength 1 |
| `send_channel` | `SendChannel` | Y | - |
| `request_hash` | `string` | Y | minLength 64, maxLength 64, pattern ^[0-9a-f]{64}$ |
| `delivered_at` | `string` | Y | - |
| `result` | `object | null` | N | default=None |

### `DocumentChunkItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chunk_id` | `string` | Y | minLength 1 |
| `chunk_seq` | `integer` | Y | >= 0 |
| `section_title` | `string | null` | N | default=None |
| `content` | `string` | Y | - |

### `DocumentDetailResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `document_id` | `string` | Y | minLength 1 |
| `title` | `string` | Y | - |
| `doc_type` | `DocumentType | null` | N | default=None |
| `model_code` | `string | null` | N | default=None |
| `source_path` | `string | null` | N | default=None |
| `version` | `string | null` | N | default=None |
| `chunks` | `array<DocumentChunkItem>` | Y | - |

### `DocumentHit`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chunk_id` | `string` | Y | minLength 1 |
| `document_id` | `string` | Y | minLength 1 |
| `title` | `string` | Y | - |
| `section` | `string | null` | N | default=None |
| `score` | `number` | Y | >= -1.0, <= 1.0 |
| `content` | `string` | Y | - |
| `model_code` | `string | null` | N | default=None |

### `DocumentSearchRequest`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `query` | `string` | Y | minLength 1, maxLength 1000 |
| `model_code` | `string | null` | N | default=None |
| `top_k` | `integer` | N | >= 1, <= 10, default=4 |

### `DocumentSearchResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `query` | `string` | Y | - |
| `hits` | `array<DocumentHit>` | Y | - |
| `count` | `integer` | Y | >= 0 |

### `DocumentType`

없음

### `EquipmentContextToolResult`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `ok` | `boolean` | Y | - |
| `reason` | `string` | N | default= |
| `equipment` | `EquipmentNode | null` | N | default=None |
| `area` | `AreaNode | null` | N | default=None |
| `step` | `ProcessStepNode | null` | N | default=None |
| `sibling_chambers` | `array<ChamberNode>` | N | - |
| `upstream` | `array<EquipmentNode>` | N | - |
| `downstream` | `array<EquipmentNode>` | N | - |

### `EquipmentCountItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `equipment_id` | `string` | Y | minLength 1 |
| `area_id` | `string | null` | N | default=None |
| `alarm_count` | `integer` | Y | >= 0 |
| `chambers` | `array<ChamberAlarmCount>` | Y | - |

### `EquipmentNode`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `equipment_id` | `string` | Y | minLength 1 |
| `equipment_name` | `string` | Y | - |
| `model_code` | `string` | Y | minLength 1 |
| `area_id` | `string` | Y | minLength 1 |
| `step_id` | `string | null` | N | default=None |

### `EquipmentRelationResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `equipment` | `EquipmentNode` | Y | - |
| `chambers` | `array<ChamberNode>` | Y | - |
| `area` | `AreaNode | null` | N | default=None |
| `step` | `ProcessStepNode | null` | N | default=None |
| `upstream` | `array<EquipmentNode>` | Y | - |
| `downstream` | `array<EquipmentNode>` | Y | - |

### `EvaluationItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `case_type` | `enum(GOLD | DEFENSE)` | Y | - |
| `case_id` | `string` | Y | minLength 1 |
| `question` | `string | null` | N | default=None |
| `passed` | `boolean` | Y | - |
| `generated_sql` | `string | null` | N | default=None |
| `attempt_count` | `integer` | Y | >= 0 |
| `expected_result` | `any | null` | N | default=None |
| `actual_result` | `any | null` | N | default=None |
| `expected_visualization` | `VisualizationPlan | null` | N | default=None |
| `actual_visualization` | `VisualizationPlan | null` | N | default=None |
| `reason` | `string | null` | N | default=None |
| `latency_ms` | `integer | null` | N | default=None |

### `EvaluationListResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<EvaluationResponse>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |

### `EvaluationResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `run_id` | `string` | Y | minLength 1 |
| `executed_at` | `string` | Y | - |
| `provider` | `string` | Y | minLength 1 |
| `model` | `string` | Y | minLength 1 |
| `temperature` | `number` | Y | - |
| `prompt_version` | `string` | Y | minLength 1 |
| `correct` | `integer` | Y | >= 0 |
| `total` | `integer` | Y | >= 0 |
| `accuracy` | `number` | Y | >= 0.0, <= 1.0 |
| `defense_passed` | `integer` | Y | >= 0 |
| `defense_total` | `integer` | Y | >= 0 |
| `items` | `array<EvaluationItem>` | Y | - |

### `EvidenceError`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `stage` | `string` | Y | minLength 1 |
| `code` | `string` | Y | minLength 1 |
| `message` | `string` | Y | minLength 1 |
| `retryable` | `boolean` | Y | - |

### `FaultCode`

없음

### `FdcSummaryResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `wafer` | `WaferContext` | Y | - |
| `sensors` | `array<SensorSummaryItem>` | Y | minItems 1 |
| `anomaly_score` | `number` | Y | >= 0.0, <= 1.0 |
| `anomaly_threshold` | `number` | Y | >= 0.0, <= 1.0 |
| `is_anomaly` | `boolean` | Y | - |

### `FdcSummaryToolResult`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `ok` | `boolean` | Y | - |
| `reason` | `string` | N | default= |
| `wafer` | `WaferContext | null` | N | default=None |
| `sensors` | `array<SensorSummaryItem>` | N | - |
| `anomaly_score` | `number | null` | N | default=None |
| `anomaly_threshold` | `number | null` | N | default=None |
| `is_anomaly` | `boolean` | N | default=False |

### `GroupedMetricResult`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `group` | `object` | Y | - |
| `value` | `integer | number | null` | N | default=None |

### `HierarchyNode`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `area_id` | `string` | Y | minLength 1 |
| `equipment_id` | `string` | Y | minLength 1 |
| `model_code` | `string | null` | N | default=None |
| `chambers` | `array<string>` | Y | - |

### `IncidentAlarmEvidence`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `alarm_ids` | `array<string>` | Y | - |
| `lot_hist_ids` | `array<string>` | Y | - |
| `rule_ids` | `array<string>` | Y | - |
| `distinct_oos_wafer_count` | `integer` | Y | >= 0 |
| `distinct_ooc_wafer_count` | `integer` | Y | >= 0 |
| `has_r03_consec` | `boolean` | Y | - |
| `sibling_alarm_counts` | `map<string, integer>` | Y | - |

### `IncidentRef`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `lot_id` | `string` | Y | minLength 1 |
| `chamber_id` | `string` | Y | minLength 1 |

### `Judgement`

없음

### `MeasuredStepStat`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `lot_hist_id` | `string` | Y | minLength 1 |
| `sensor_id` | `string` | Y | minLength 1 |
| `recipe_step_no` | `integer` | Y | >= 1 |
| `recipe_step_name` | `string | null` | N | default=None |
| `value_mean` | `number | null` | N | default=None |
| `value_std` | `number | null` | N | default=None |
| `value_min` | `number | null` | N | default=None |
| `value_max` | `number | null` | N | default=None |
| `point_cnt` | `integer` | Y | >= 0 |
| `ooc_point_cnt` | `integer` | Y | >= 0 |
| `oos_point_cnt` | `integer` | Y | >= 0 |
| `judgement` | `Judgement` | Y | - |

### `MetricPlan`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `type` | `enum(count | sum | mean | median | std | min | max | percentile | ratio)` | Y | - |
| `column` | `string | null` | N | default=None |
| `p` | `number | null` | N | default=None |

### `NlQueryHistoryResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `items` | `array<NlQueryLogItem>` | Y | - |
| `total` | `integer` | Y | >= 0 |
| `page` | `integer` | Y | >= 1 |
| `size` | `integer` | Y | >= 1, <= 100 |

### `NlQueryLogItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `nl_query_log_id` | `integer` | Y | >= 1 |
| `asked_at` | `string` | Y | - |
| `question` | `string` | Y | - |
| `generated_sql` | `string | null` | N | default=None |
| `is_valid` | `boolean` | Y | - |
| `is_rejected` | `boolean` | Y | - |
| `reject_reason` | `string | null` | N | default=None |
| `row_cnt` | `integer | null` | N | default=None |
| `latency_ms` | `integer | null` | N | default=None |
| `error_msg` | `string | null` | N | default=None |

### `ProcessStepNode`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `step_id` | `string` | Y | minLength 1 |
| `step_name` | `string` | Y | - |
| `step_seq` | `integer | null` | N | default=None |
| `layer` | `string | null` | N | default=None |

### `R03EvidenceRef`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `alarm_id` | `string` | Y | minLength 1 |
| `lot_hist_id` | `string` | Y | minLength 1 |
| `wafer_no` | `integer` | Y | >= 1 |
| `sensor_id` | `string` | Y | minLength 1 |
| `recipe_step_name` | `string | null` | N | default=None |

### `SendChannel`

없음

### `SendStatus`

없음

### `SensorLimits`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `unit` | `string | null` | N | default=None |
| `spec_lower` | `number | null` | N | default=None |
| `ctrl_lower` | `number | null` | N | default=None |
| `target` | `number | null` | N | default=None |
| `ctrl_upper` | `number | null` | N | default=None |
| `spec_upper` | `number | null` | N | default=None |
| `upper_only` | `boolean` | Y | - |

### `SensorSummaryItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `sensor_id` | `string` | Y | minLength 1 |
| `sensor_name` | `string` | Y | - |
| `unit` | `string | null` | N | default=None |
| `recipe_step_no` | `integer` | Y | - |
| `recipe_step_name` | `string | null` | N | default=None |
| `value_mean` | `number | null` | N | default=None |
| `value_std` | `number | null` | N | default=None |
| `value_min` | `number | null` | N | default=None |
| `value_max` | `number | null` | N | default=None |
| `point_cnt` | `integer` | Y | >= 0 |
| `ooc_point_cnt` | `integer` | Y | >= 0 |
| `oos_point_cnt` | `integer` | Y | >= 0 |
| `spec_lower` | `number | null` | N | default=None |
| `ctrl_lower` | `number | null` | N | default=None |
| `target` | `number | null` | N | default=None |
| `ctrl_upper` | `number | null` | N | default=None |
| `spec_upper` | `number | null` | N | default=None |
| `judgement` | `Judgement` | Y | - |

### `Severity`

없음

### `SqlValidateRequest`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `sql` | `string` | Y | minLength 1, maxLength 20000 |

### `SqlValidateResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `valid` | `boolean` | Y | - |
| `normalized_sql` | `string | null` | N | default=None |
| `reason` | `string` | Y | - |
| `checks` | `array<ValidationCheck> | null` | N | default=None |

### `ToolCallItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `tool_call_id` | `string` | Y | minLength 1 |
| `call_seq` | `integer` | Y | >= 1 |
| `tool_name` | `string` | Y | minLength 1 |
| `input` | `object | null` | N | default=None |
| `output` | `object | null` | N | default=None |
| `status` | `ToolCallStatus` | Y | - |
| `latency_ms` | `integer | null` | N | default=None |
| `called_at` | `string` | Y | - |
| `error_msg` | `string | null` | N | default=None |

### `ToolCallStatus`

없음

### `TopSensorItem`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `sensor_id` | `string` | Y | minLength 1 |
| `alarm_count` | `integer` | Y | >= 0 |
| `chamber_ids` | `array<string>` | Y | - |

### `TraceCatalogAnomaly`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `threshold` | `number` | Y | >= 0.0, <= 1.0 |

### `TraceCatalogArea`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `area_id` | `string` | Y | minLength 1 |

### `TraceCatalogEquipment`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `equipment_id` | `string` | Y | minLength 1 |
| `area_id` | `string` | Y | minLength 1 |
| `model_code` | `string | null` | N | default=None |
| `chambers` | `array<string>` | Y | - |

### `TraceCatalogLot`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `lot_id` | `string` | Y | minLength 1 |
| `wafer_nos` | `array<integer>` | Y | - |

### `TraceCatalogRecipe`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `recipe_id` | `string` | Y | minLength 1 |
| `step_id` | `string` | Y | minLength 1 |

### `TraceCatalogResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `areas` | `array<TraceCatalogArea>` | Y | - |
| `equipments` | `array<TraceCatalogEquipment>` | Y | - |
| `sensors` | `array<TraceCatalogSensor>` | Y | - |
| `recipes` | `array<TraceCatalogRecipe>` | Y | - |
| `lots` | `array<TraceCatalogLot>` | Y | - |
| `anomaly` | `TraceCatalogAnomaly` | Y | - |

### `TraceCatalogSensor`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `unit` | `string | null` | N | default=None |
| `spec_lower` | `number | null` | N | default=None |
| `ctrl_lower` | `number | null` | N | default=None |
| `target` | `number | null` | N | default=None |
| `ctrl_upper` | `number | null` | N | default=None |
| `spec_upper` | `number | null` | N | default=None |
| `upper_only` | `boolean` | Y | - |
| `sensor_id` | `string` | Y | minLength 1 |
| `sensor_name` | `string` | Y | - |

### `TracePoint`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `seq_no` | `integer` | Y | >= 0 |
| `recipe_step_no` | `integer | null` | N | default=None |
| `recipe_step_name` | `string | null` | N | default=None |
| `measured_at` | `string | null` | N | default=None |
| `value` | `number` | Y | - |

### `TraceSearchRequest`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `area` | `string | null` | N | default=None |
| `equipment_id` | `string | null` | N | default=None |
| `chamber_id` | `string | null` | N | default=None |
| `sensor_ids` | `array<string>` | Y | minItems 1 |
| `recipe_id` | `string | null` | N | default=None |
| `lot_id` | `string | null` | N | default=None |
| `wafer_nos` | `array<integer>` | N | - |
| `from` | `string | null` | N | default=None |
| `to` | `string | null` | N | default=None |

### `TraceSearchResponse`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `wafers` | `array<TraceWaferSeries>` | Y | - |
| `limits` | `map<string, SensorLimits>` | Y | - |
| `measured_step_stats` | `array<MeasuredStepStat>` | Y | - |
| `total` | `integer` | Y | >= 0 |

### `TraceWaferSeries`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `lot_hist_id` | `string` | Y | minLength 1 |
| `lot_id` | `string` | Y | minLength 1 |
| `wafer_no` | `integer` | Y | >= 1 |
| `chamber_id` | `string` | Y | minLength 1 |
| `equipment_id` | `string` | Y | minLength 1 |
| `recipe_id` | `string | null` | N | default=None |
| `sensor_id` | `string` | Y | minLength 1 |
| `occurred_at` | `string | null` | N | default=None |
| `points` | `array<TracePoint>` | Y | - |

### `UpstreamEvidence`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `source` | `enum(batch_plan | action_history)` | Y | - |
| `upstream_incident` | `IncidentRef` | Y | - |
| `downstream_incident` | `IncidentRef` | Y | - |
| `relationship` | `string` | Y | minLength 1 |
| `same_wafer` | `boolean` | Y | - |
| `action_id` | `string | null` | N | default=None |
| `action_code` | `ActionCode | null` | N | default=None |

### `ValidationCheck`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `key` | `string` | Y | minLength 1 |
| `label` | `string` | Y | minLength 1 |
| `ok` | `boolean` | Y | - |

### `VisualizationPlan`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `chart_type` | `ChartType` | Y | - |
| `x` | `string | null` | N | default=None |
| `y` | `string | null` | N | default=None |

### `WaferContext`

| 필드 | 타입 | 필수 | 제약·기본값 |
|---|---|:---:|---|
| `lot_hist_id` | `string` | Y | minLength 1 |
| `lot_id` | `string` | Y | minLength 1 |
| `wafer_no` | `integer` | Y | - |
| `chamber_id` | `string` | Y | minLength 1 |
| `equipment_id` | `string` | Y | minLength 1 |
| `step_id` | `string` | Y | minLength 1 |
| `recipe_id` | `string | null` | N | default=None |

## 5. DB/API 이름 대응과 구현 주의

- `document_id` ↔ DB `document.doc_id`·`document_chunk.doc_id`
- `nl_query_log_id` ↔ DB `nl_query_log.query_id`
- `AlarmItem.detail`·`AuditLogItem.detail`은 text/string이며 감사 before/after만 JSON 객체다.
- `ActionItem.created_by_agent_run_id`는 승인된 migration으로 추가할 nullable provenance 컬럼이다. 신규 조치 생성 시 한 번 기록하고 재실행에서 갱신하지 않는다.
- `ET_REFL.upper_only=true`는 도메인 메타데이터 규칙이며 `spec_lower IS NULL`로 계산하지 않는다.
- OpenAPI 경로는 개발 `/docs`·`/openapi.json`, Nginx 통합 배포 `/api/docs`·`/api/openapi.json`이다.
