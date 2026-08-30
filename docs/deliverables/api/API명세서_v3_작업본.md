# API 명세서

**PhotoEtch FDC Agent Pilot — 최종 데이터 전환 작업본**

---

## 1. 문서 정보

| 항목 | 내용 |
|---|---|
| 버전 | v3 작업본 |
| 작성일 | 2026.08.19 |
| 기준 | 멘토님 제공 최종 `project.zip`의 실제 React 5화면과 팀 합의 7화면, `검토질문_답변.html`, 최종 CSV·Generator |
| 목적 | 멘토 기준 public 필수 11개와 팀 release 필수 확장 5개의 독립 계약·수용 경계를 고정 |
| OpenAPI 상태 | 구현·Pydantic 동기화 전 작업 계약 |

### 1.1 계약 우선순위

1. 요구사항정의서 최종 데이터 전환 작업본
2. 시스템설계서 최종 데이터 전환 작업본
3. 멘토님 제공 최종 패키지의 실제 `frontend/src/App.jsx`와 `검토질문_답변.html`
4. 본 API 명세
5. `docs/02_화면별_API_가이드.md`
6. 기존 API·Frontend adapter

최종 패키지의 실제 화면은 Dashboard, Alarm History, Agent, Documents, Ontology다.
`02_화면별_API_가이드.md`의 화면 5 Text2SQL과 최소 API 표는 이보다 앞선 잔재이므로 해당 부분은
stale이다. 실제 `frontend/src/lib/api.js`가 노출하는 9개 wrapper를 호환 필수 범위로 고정한다.
그중 `api.audit()`는 참고 페이지의 소비가 없으므로 팀 구현의 Agent 감사 subview가 반드시
연결한다. Ontology는
API 없이 Neo4j Browser iframe과 기본 계정을 직접 노출하므로 이를 대체하는 read-only API 1개를
보안 필수로 추가한다. Alarm 화면의 source-aware `POST /agent/runs`는 프로젝트 실행 필수로
추가한다. 따라서 멘토 기준 public 필수는 11개다. 팀 최종 제품은 여기에 자연어 분석·이력·평가와
전역 감사 화면을 더한 7개 주 navigation으로 확정했으며, 이를 위한 5개 API는 5.3의
**팀 release 필수 확장**으로 별도 승격한다. 나머지 상세 조회·페이지 조회·재시도 API는 선택 확장이다.
확장 기능이 필수 endpoint의 path·요청·응답 의미를 바꾸면 안 된다.

---

## 2. 공통 계약

### 2.1 기본 규칙

- JSON field는 `snake_case`를 사용한다. 2.7에 명시한 `LSL|LCL|TARGET|UCL|USL` 전환 alias만
  예외이며 canonical field 전환 뒤 제거한다.
- 요청·응답 DTO는 알 수 없는 field를 거부한다(`extra="forbid"`).
- 날짜는 `YYYY-MM-DD`, 시각은 UTC offset을 포함한 ISO 8601 `date-time`으로 전송한다.
  기본 표시·예시는 Asia/Seoul의 `+09:00`을 사용한다.
- 비어 있는 목록은 200과 `[]`로 반환하며 404로 바꾸지 않는다.
- 문자열 ID는 앞뒤 공백을 제거한 뒤 빈 문자열을 거부한다. 단, `GET /alarms`의 선택
  query `equipment`·`chamber`·`parameter` 세 필터만 최종 참고 React 호환을 위해 trim 후
  빈 문자열을 미지정(unset)으로 정규화한다. 다른 ID·필터·body field에 이 예외를
  확장하지 않는다.
- 공개 합성 Fault 정답은 Runtime·Agent·일반 조회 API에서 반환하지 않는다.
- 목록은 명세에 정의된 보조키까지 사용해 안정 정렬한다.

### 2.2 오류 응답

2xx가 아닌 응답은 원칙적으로 다음 구조를 사용한다. 단, `GET /health/ready`의 503은 실패한
check를 운영자가 확인할 수 있도록 5.1의 `ReadinessResponse(status=NOT_READY)`를 그대로
반환하는 명시적 예외다.

```json
{
  "code": "RESOURCE_NOT_FOUND",
  "message": "요청한 리소스를 찾을 수 없습니다.",
  "details": {}
}
```

| HTTP | 의미 |
|---:|---|
| 401 | `/internal` endpoint의 인증·secret 검증 실패 (`UNAUTHORIZED`) |
| 404 | 식별자로 요청한 리소스 없음 |
| 409 | 이미 결정된 승인, 멱등성 충돌, 상태 전이 충돌 |
| 422 | 요청 형식·Enum·범위 오류 |
| 500 | 예상하지 못한 서버 오류. 응답·로그에 secret과 원문 prompt를 노출하지 않음 |
| 503 | PostgreSQL·Neo4j·RAG·LLM·n8n·Kafka 의존성 오류 |

팀 release Text2SQL의 정책 거부는 요청 형식 오류가 아니다.
`POST /analytics/query`가 HTTP 200과 `is_rejected=true`를 반환하고 SQL을 실행하지 않는다.

### 2.3 공통 식별자

```json
{
  "source": "TRACE",
  "alarm_id": "TAL-0001"
}
```

`AlarmRef.source`는 `TRACE|SUMMARY|R03`이다. 알람의 유일 식별자는 `(source, alarm_id)`이며
`alarm_id`만으로 상세·Agent 실행·deep link를 결정하지 않는다.

### 2.4 Fault field

| field | 의미 | 노출 범위 |
|---|---|---|
| `predicted_fault_code` | Agent가 근거로 생성한 `FOC\|RFM\|MFD\|TMD\|OTH` 가설 | Agent API·화면 |
| `fault_code` | 최소 가이드 호환 alias. 값은 `predicted_fault_code`와 같음 | `GET /agent/runs`, `GET /approvals`에 임시 제공 |
| `ground_truth_fault_code` | Generator 공개 합성 정답 | 격리 평가 artifact/API만 |

DB의 `lot_history.fault_code`를 Agent 결과인 `fault_code`로 직접 직렬화하지 않는다.

### 2.5 승인·채널 adapter

| 경계 | Public 값 | Internal 값 |
|---|---|---|
| 승인 요청 | `APPROVED` | `APPROVE` |
| 반려 요청 | `REJECTED` | `REJECT` |
| MES 표시 | `MES` | `MES_MOCK` |

public 요청은 `APPROVED|REJECTED`만 받는다. 내부 Enum 변환은 Router boundary adapter에서 하고
DB 상태와 public 응답은 `APPROVED|REJECTED`로 유지한다. `MES` 응답은 실제 MES가 아니라 승인 후
Kafka `fdc.actions`에 발행하는 MES Mock임을 화면에 표시한다.

### 2.6 배열·페이지 응답

호환 필수 9개 endpoint의 목록 응답은 최종 frontend 호환을 위해 bare array다. 페이지가 필요한 기존
기능은 별도 `/paged` endpoint에서 다음 구조를 사용한다.

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "size": 20
}
```

동일 path가 `page` query의 유무에 따라 배열과 객체를 번갈아 반환하는 방식은 금지한다.

### 2.7 최종 참고 React 호환 projection

최종 패키지의 React는 일부 축약 field를 직접 읽는다. 9개 호환 endpoint는 canonical field와 함께
아래 deprecated alias를 한 전환 revision 동안 제공한다. alias는 Router serializer 한 곳에서만
canonical 값으로 파생하며 DB column이나 공개 합성 GT를 직접 읽지 않는다. Frontend를 canonical
field로 전환하는 Task와 alias 제거 revision을 WBS v5에 각각 둔다.

| 응답 | canonical | deprecated alias | 파생 규칙 |
|---|---|---|---|
| AlarmItem | `equipment_id`, `chamber_id`, `recipe_id`, `lot_id`, `wafer_id`, `parameter_id`, `recipe_step_no` | `equipment`, `chamber`, `recipe`, `lot`, `wafer`, `parameter`, `step_no` | 각 canonical ID를 같은 순서의 alias로 1:1 복사 |
| AlarmItem | `predicted_fault_code` | `fault` | 연결된 Runtime Agent 예측이 있을 때만 같은 값, 없으면 null |
| AlarmItem | `notify_status` | `notify` | status가 SENT일 때만 true |
| AlarmItem | `mes_status` | `mes` | null이면 빈 문자열, 그 외 같은 상태 문자열 |
| ParameterItem | `parameter_name`, `spec_lower` 등 | `name`, `LSL`, `LCL`, `TARGET`, `UCL`, `USL` | 같은 기준정보 값 복사 |
| DocumentHit | `document_id` | `doc_id` | 같은 document ID 복사 |
| `GET /agent/runs` ToolCallItem | `tool_name`, `status`, `result_summary` | `n`, `s` | `n=tool_name`, `s=status`. Auto Analysis 전용 |
| `POST /agent/ask` tool | `tool_name`, `status`, `result_summary` | `name`, `result` | `name=tool_name`, `result=result_summary`. Chat 전용 |
| AgentRunItem | `chamber_id` | `chamber` | 같은 chamber ID 복사 |
| AgentRunItem | `predicted_fault_code` | `fault_code`, `fault_name`, `fault_color` | `fault_code`는 예측값 복사. 등록된 UI metadata가 없으면 name·color는 null이며 임의로 생성하지 않음 |
| ApprovalItem | `lot_id`, `equipment_id`, `chamber_id` | `lot`, `equipment`, `chamber` | 각 canonical ID를 같은 순서의 alias로 1:1 복사 |
| ApprovalItem | `predicted_fault_code` | `fault_code` | 예측값을 그대로 복사하며 합성 GT를 읽지 않음 |
| ApprovalItem | `decided_by`, `decided_at` | `approved_by`, `approved_at` | 결정 상태와 무관하게 같은 값 복사하는 legacy 표시 alias |
| AuditLogItem | `occurred_at`, `actor_type`, `event_type`, `entity_type`+`entity_id` | `at`, `actor`, `event`, `entity` | 시각·actor는 복사, event는 3.8 mapping, entity는 `entity_type:entity_id` |
| AgentAskResponse | `evidence_items`, `limitations` | `evidence`, `limit` | 첫 DOCUMENT 근거 또는 null, limitations를 하나의 문자열로 결합 |
| AgentAsk `evidence` | `document_id` | `doc_id` | 호환 단일 DOCUMENT 객체 안에서만 같은 ID 복사 |

`fault`·`fault_code` alias를 `lot_history.fault_code`나 parameter→Fault 고정표에서 생성하는 것은
금지한다. alias가 없더라도 canonical field만으로 같은 화면을 렌더링하는 Frontend contract test를
추가한다. 예측 전에는 `predicted_fault_code`와 alias가 모두 null이며 화면은 이를 `분석 전`으로
표시하거나 예측 분포 집계에서 제외한다. null을 합성 GT로 채우지 않는다.

### 2.8 내부 Tool 경계

```text
get_fdc_summary(lot_hist_id)
get_equipment_context(chamber_id)
search_documents(query, model_code=None, top_k=4)
send_action(action_id)
generate_analysis_plan(question)
```

Tool 결과는 `ok`, `reason`, domain payload만 반환한다. `latency_ms`·호출 status는 공통 wrapper가
`agent_tool_call`에 호출당 한 번 기록하며 Tool JSON에 넣지 않는다. 실패 reason은
`NOT_FOUND:|TIMEOUT:|MODEL_NOT_READY:|LLM_NOT_READY:|GRAPH_SHAPE_ERROR:|DEPENDENCY_ERROR:|POLICY_REJECTED:|IDEMPOTENCY_CONFLICT:`
여덟 prefix만 허용하고 실패 payload는 null 또는 빈 목록이다. `GRAPH_SHAPE_ERROR:`는 graph
연결은 됐으나 반환 형상이 계약과 다른 경우이고, `DEPENDENCY_ERROR:`는 조회 자체가 실패한
경우다. 이 목록은 HTTP 오류 code(§2.7)와 별개 계약이다.

---

## 3. 필수 호환 API — 9개

| 담당 | Method | Path | 용도 | 성공 응답 |
|---|---|---|---|---|
| A | GET | `/alarms` | 저장 알람 조회 | `AlarmItem[]` |
| A | GET | `/trace` | wafer parameter Trace | `TracePoint[]` |
| A | GET | `/parameters` | 5선·parameter 기준정보 | `ParameterItem[]` |
| B | POST | `/documents/search` | RAG 문서 검색 | `DocumentHit[]` |
| C | GET | `/agent/runs` | Agent 실행 이력 | `AgentRunItem[]` |
| C | POST | `/agent/ask` | 근거 기반 Agent 질의 | `AgentAskResponse` |
| C | GET | `/approvals` | 승인 대기·결정 이력 | `ApprovalItem[]` |
| C | POST | `/approvals/{approval_id}/decision` | 승인·반려 | `ApprovalItem` |
| D | GET | `/audit-logs` | append-only 감사 이력 | `AuditLogItem[]` |

### 3.1 `GET /alarms`

#### Query

| 이름 | 형식 | 필수 | 제약 |
|---|---|---:|---|
| `date_from` | date | 아니오 | date_to와 함께 사용, `date_from <= date_to` |
| `date_to` | date | 아니오 | date_from과 함께 사용, 범위 밖은 빈 결과 허용 |
| `area` | string | 아니오 | canonical `Photo\|Etch\|ALL`, 생략은 ALL |
| `equipment` | string | 아니오 | 빈 문자열은 미지정으로 정규화 |
| `chamber` | string | 아니오 | 빈 문자열은 미지정으로 정규화 |
| `parameter` | string | 아니오 | parameter ID, 빈 문자열은 미지정으로 정규화 |
| `source` | enum | 아니오 | `TRACE\|SUMMARY\|R03`; R03를 명시하면 파생 3건 조회 |
| `include_derived` | boolean | 아니오 | 기본 false. source 미지정 전체 목록에 R03 포함 여부 |

#### Response 200 — `AlarmItem[]`

```json
[
  {
    "alarm_id": "TAL-0001",
    "source": "TRACE",
    "occurred_at": "2026-08-04T06:52:29+09:00",
    "area": "Etch",
    "equipment_id": "EQP04",
    "equipment": "EQP04",
    "chamber_id": "EQP04-PM2",
    "chamber": "EQP04-PM2",
    "recipe_id": "RECIPE04",
    "recipe": "RECIPE04",
    "lot_id": "LOT004",
    "lot": "LOT004",
    "wafer_id": "LOT004W002",
    "wafer": "LOT004W002",
    "parameter_id": "ET_REFL",
    "parameter": "ET_REFL",
    "recipe_step_no": 1,
    "step_no": 1,
    "seq_no": 0,
    "value": 37.467,
    "alarm_type": "OOS",
    "rule_code": "TRACE_OOS",
    "predicted_fault_code": null,
    "fault": null,
    "action_code": null,
    "notify_status": null,
    "notify": false,
    "mes_status": null,
    "mes": "",
    "statistic_type": null,
    "cl": null,
    "ucl": null,
    "lcl": null
  }
]
```

위 `TAL-0001`은 최종 `trace_alarm_history.csv`의 실측 tuple이다. canonical ID field는 `_id`·
`recipe_step_no`를 사용하고 축약 field는 2.7의 deprecated projection으로만 제공한다.

R03 상세 `AlarmDetailResponse`는 다음 두 member 목록을 분리해 반환한다. 아래는 위
`TAL-0001`이 속한 최종 R03 incident의 실제 member다.

```json
{
  "source": "R03",
  "alarm_id": "R03-f41e6518529e8ed5e6a9",
  "occurred_at": "2026-08-04T07:00:04+09:00",
  "lot_hist_id": "LH-00181",
  "lot_id": "LOT004",
  "area": "Etch",
  "equipment_id": "EQP04",
  "chamber_id": "EQP04-PM2",
  "parameter_id": "ET_REFL",
  "recipe_id": "RECIPE04",
  "recipe_step_no": 1,
  "wafer_id": "LOT004W006",
  "trigger_wafer_no": 6,
  "seq_no": null,
  "alarm_type": "OOS",
  "rule_code": "R03_CONSEC",
  "value": null,
  "policy_version": "R03_CONSEC_V1",
  "member_wafer_refs": [
    {"lot_hist_id": "LH-00177", "wafer_id": "LOT004W002"},
    {"lot_hist_id": "LH-00179", "wafer_id": "LOT004W004"},
    {"lot_hist_id": "LH-00181", "wafer_id": "LOT004W006"}
  ],
  "member_alarm_refs": [
    {"source": "TRACE", "alarm_id": "TAL-0001"},
    {"source": "TRACE", "alarm_id": "TAL-0002"},
    {"source": "TRACE", "alarm_id": "TAL-0003"},
    {"source": "TRACE", "alarm_id": "TAL-0004"},
    {"source": "TRACE", "alarm_id": "TAL-0005"},
    {"source": "TRACE", "alarm_id": "TAL-0006"},
    {"source": "TRACE", "alarm_id": "TAL-0007"},
    {"source": "TRACE", "alarm_id": "TAL-0008"},
    {"source": "TRACE", "alarm_id": "TAL-0009"}
  ]
}
```

- `member_wafer_refs`는 연속 OOS를 구성한 `{lot_hist_id, wafer_id}` 정확히 3개다.
- `member_alarm_refs`는 그 세 WAFER의 같은 parameter·recipe step에서 발생한 raw OOS
  TRACE AlarmRef 전체다. 최종 epoch의 각 R03는 정확히 9개를 갖는다.
- WAFER는 R03 계산 순서, TRACE AlarmRef는 각 WAFER 안에서 `seq_no ASC, alarm_id ASC`다.

- `seq_no`는 SUMMARY·R03에서 null이다. SUMMARY는 `statistic_type`, `cl`, `ucl`, `lcl`을
  채운다. TRACE·R03는 네 field가 null이다.
- `rule_code`는 `TRACE_OOS|SUMMARY_OOC|R03_CONSEC`이며 source와 일치해야 한다.
- R03는 `alarm_type=OOS`, `rule_code=R03_CONSEC`, `value=null`로 표현한다. 별도 판단 Enum인
  `AlarmType(IN|OOC|OOS)`에 R03_CONSEC를 넣지 않는다. owner는 연속 3에 도달한 세 번째 WAFER이며
  상세 응답은 `member_wafer_refs` 3개와 `member_alarm_refs` 전체를 분리해 반환한다.
- `notify_status`와 `mes_status`는 같은 `(lot, chamber)` Runtime action이 없으면 null이다.
  `mes_status`는 Kafka MES Mock delivery 상태이며 실제 MES 결과가 아니다.
- 안정 정렬: `occurred_at DESC, source ASC, alarm_id DESC`.
- 무파라미터 호출은 final epoch 전체 기간·ALL이며 기본은 TRACE 138 + SUMMARY 51 = 189다.
- 같은 필터에 `include_derived=true`면 192, `source=R03`이면 3건이다.
- `source=R03` 명시는 `include_derived=false`보다 우선한다.
- `notify_status`는 `WAITING|SENDING|SENT|FAILED|UNKNOWN|null`, `mes_status`는
  `BLOCKED|WAITING|SENDING|SENT|FAILED|UNKNOWN|CANCELED|null`이다.

### 3.2 `GET /trace`

#### Query

`lot`, `wafer`, `chamber`, `parameter` 네 항목이 모두 필수다.

#### Response 200 — `TracePoint[]`

```json
[
  {
    "recipe_step_no": 1,
    "seq_no": 0,
    "measured_at": "2026-08-04T06:52:29+09:00",
    "value": 37.467
  }
]
```

- 안정 정렬: `measured_at ASC, recipe_step_no ASC, seq_no ASC`.
- 결과 없음은 200 `[]`다.
- 최종 Trace의 `seq_no`는 wafer·parameter 범위에서 Step 1의 0..2, Step 2의 3..5다.

### 3.3 `GET /parameters`

#### Response 200 — `ParameterItem[]`

```json
[
  {
    "parameter_id": "PH_FOCUS",
    "parameter_name": "Focus Offset",
    "name": "Focus Offset",
    "unit": "nm",
    "area": "Photo",
    "spec_lower": -60.0,
    "LSL": -60.0,
    "ctrl_lower": -36.0,
    "LCL": -36.0,
    "target_value": 0.0,
    "TARGET": 0.0,
    "ctrl_upper": 36.0,
    "UCL": 36.0,
    "spec_upper": 60.0,
    "USL": 60.0,
    "upper_only": false
  }
]
```

- 8개 parameter를 `area ASC, parameter_id ASC`로 반환한다.
- `upper_only`은 DDL·source metadata에 근거해 산출한다. 하한 null 여부만으로 임의 추정하지 않는다.

### 3.4 `POST /documents/search`

#### Request

```json
{
  "query": "포커스 이상 원인과 점검 절차",
  "model_code": "PH-9000",
  "top_k": 4
}
```

- `query`: 1..1000자
- `model_code`: 선택, 빈 문자열 불가
- `top_k`: 기본 4, 범위 1..10

#### Response 200 — `DocumentHit[]`

```json
[
  {
    "doc_id": "DOC-SPEC-PH9000",
    "document_id": "DOC-SPEC-PH9000",
    "chunk_id": "DOC-SPEC-PH9000:cs2:<4-digit-seq>",
    "title": "PH-9000 Photo Scanner 장비 스펙 및 운전 기준",
    "section": "4.2 Focus Offset (`PH_FOCUS`)",
    "score": 0.87,
    "content": "...",
    "model_code": "PH-9000"
  }
]
```

- 검색 0건은 성공이며 200 `[]`다.
- `doc_id`는 최소 가이드 호환 alias이며 canonical `document_id`와 같은 값이다.
- corrected RAG source는 원문 YAML의 `DOC-SPEC-PH9000`, `DOC-SPEC-ET7500`,
  `DOC-TROUBLE-FDC`를 canonical `document_id`로 그대로 승계한다. `chunk_id`는
  `<document_id>:<chunk_schema_version>:<4자리 순번>` 형식으로 결정론적으로 생성하며 최초
  `chunk_schema_version`은 `cs2`다. 같은 원문·분할 규칙·순번에서는 재적재해도 바뀌지
  않는다.
- 예시의 `DOC-SPEC-PH9000:cs2:<4-digit-seq>`는 순번을 생략한 문서 표기용 placeholder다.
  실제 응답은 적재 검증 artifact에 기록된 결정론적 chunk ID를 사용하고 placeholder 문자열을
  반환하지 않는다.
- 안정 정렬: `score DESC, document_id ASC, chunk_id ASC`.
- 검증된 corrected RAG source만 검색하며 구 조치·수치나 고정 설비 상하류 표현을 반환하지 않는다.
- `document`·`document_chunk`·vector extension과 loader·`BAAI/bge-m3`·1024는 교육생
  배포패키지①에서, RAG 원문 3종은 최종 패키지③에서 가져온다. loader adapter는 명시적 정정본
  입력 경로·DSN/target guard·단일 transaction·멱등 적재를 강제한다.
- 물리 `doc_id`·`section_title`은 Repository에서 `document_id`·`section`으로 한 번만 매핑한다.
  검색은 pgvector cosine만 사용하며 `pg_trgm`·trigram index는 계약 밖이다. `model_code`를
  주면 해당 모델과 `COMMON`을 함께 검색한다.

### 3.5 `GET /agent/runs`

#### Query

`date_from`, `date_to`는 선택이며 함께 주거나 함께 생략한다. `status`는
`RUNNING|WAITING_APPROVAL|COMPLETED|FAILED`, `predicted_fault_code`는
`FOC|RFM|MFD|TMD|OTH` 중 하나를 선택하는 서버 filter다. `equipment_id`·`chamber_id`·페이지
조건은 이 bare-array API의 서버 query가 아니며, 구 화면 호환 adapter가 응답을 client-side로
filter·정렬·slice한다.

#### Response 200 — `AgentRunItem[]`

```json
[
  {
    "agent_run_id": "RUN-000001",
    "created_at": "2026-08-04T07:00:30+09:00",
    "alarm_source": "R03",
    "alarm_id": "R03-f41e6518529e8ed5e6a9",
    "chamber_id": "EQP04-PM2",
    "chamber": "EQP04-PM2",
    "predicted_fault_code": "RFM",
    "fault_code": "RFM",
    "fault_name": null,
    "fault_color": null,
    "confidence": 0.84,
    "recommended_action": "EQP_HOLD",
    "status": "WAITING_APPROVAL",
    "action_id": "ACT-000003",
    "approval_id": "APR-000001",
    "tools": [
      {
        "tool_name": "get_fdc_summary",
        "status": "SUCCESS",
        "result_summary": "Summary context loaded",
        "n": "get_fdc_summary",
        "s": "SUCCESS"
      }
    ],
    "deliveries": [
      {"channel": "EMAIL", "status": "SENT"},
      {"channel": "MES", "status": "BLOCKED"}
    ],
    "latency_ms": 920,
    "llm_model": "configured-model"
  }
]
```

- `fault_code`는 `predicted_fault_code`의 deprecated 호환 alias다. 합성 GT가 아니다.
- `chamber_id`가 canonical ID이며 `chamber`는 같은 값의 deprecated alias다.
- 예시는 `LOT004`/`EQP04-PM2`의 실제 R03 incident를 사용한다. `RFM`은 Agent가 생성한
  예측 예시이며 합성 GT를 읽은 값이 아니다. RFM에 대한 확정된 UI metadata mapping이
  아직 계약으로 정해지지 않았으므로 `fault_name`·`fault_color`를 null로 둔다.
- `action_id`는 생성된 action이 없으면 null이고, `approval_id`는 EQP_HOLD 승인 요청이 없으면
  null이다. 화면은 chamber 검색이 아니라 이 ID로 action·approval을 연결한다.
- `status`는 `RUNNING|WAITING_APPROVAL|COMPLETED|FAILED`다. `tools`는 항상 존재하며
  아직 호출이 없으면 빈 배열이다. 예측 전·실패 상태에서는 predicted fault·confidence·recommended
  action·표시 alias가 null일 수 있다.
- `deliveries`는 항상 존재하고 없으면 빈 배열이다. item은 public `channel=EMAIL|MES`와
  `status=BLOCKED|WAITING|SENDING|SENT|FAILED|UNKNOWN|CANCELED`를 required로 가진다. 내부
  `MES_MOCK`을 `MES`로 projection하며 목록 DTO가 외부 전송을 실행하지 않는다.
- Tool canonical field는 `tool_name`, `status`, `result_summary`다. `n`, `s`는 Auto Analysis가
  읽는 한 전환 revision의 deprecated alias이며 `name/result`는 이 endpoint에 넣지 않는다. status는
  `SUCCESS|ERROR|TIMEOUT`이다. `result_summary`와 Chat projection의 `result`는 빈 문자열이
  아닌 required field다.
- `latency_ms`는 0 이상 정수, `llm_model`은 빈 문자열이 아닌 required field다.
  UI metadata mapping이 확정되기 전 `fault_name`·`fault_color`는 nullable string이 아니라
  **null만 허용**한다.
- source-aware 식별자는 `alarm_source`와 `alarm_id`의 쌍이다.
- 안정 정렬: `created_at DESC, agent_run_id DESC`.
- bare array 전환 계약 동안 서버는 위 정렬 기준의 최근 500건까지만 반환한다. 날짜 범위를
  지정해도 결과가 500건을 넘으면 같은 상한을 적용하며, 페이지네이션은 후속 확장 계약이다.

### 3.6 `GET /approvals`

#### Response 200 — `ApprovalItem[]`

```json
[
  {
    "approval_id": "APR-000001",
    "agent_run_id": "RUN-000001",
    "action_id": "ACT-000003",
    "created_at": "2026-08-04T07:00:40+09:00",
    "lot_id": "LOT004",
    "lot": "LOT004",
    "equipment_id": "EQP04",
    "equipment": "EQP04",
    "chamber_id": "EQP04-PM2",
    "chamber": "EQP04-PM2",
    "predicted_fault_code": "RFM",
    "fault_code": "RFM",
    "action_code": "EQP_HOLD",
    "reason": "R03_CONSEC: 같은 chamber·parameter·recipe step에서 연속 3 WAFER OOS",
    "status": "PENDING",
    "decided_by": null,
    "decided_at": null,
    "decision_comment": null,
    "approved_by": null,
    "approved_at": null
  }
]
```

- `action_code`는 공통 `ActionCode` 3값(`MONITORING|WARNING|EQP_HOLD`)을 타입으로
  공유하지만, 승인 projection validator는 `EQP_HOLD` action만 승인 대상으로 허용한다.
- `predicted_fault_code`·`fault_code`는 required non-null이며 두 값이 정확히 같아야 한다.
- `status`는 `PENDING|APPROVED|REJECTED`다. PENDING이면 결정 관련 field가 모두 null이고,
  결정 상태면 `decided_by`·`decided_at`이 필수이며 comment만 null일 수 있다.
- `fault_code`는 `predicted_fault_code`의 deprecated 호환 alias이며 합성 GT가 아니다.
- `lot_id`·`equipment_id`·`chamber_id`가 canonical ID이며 `lot`·`equipment`·`chamber`는 같은
  값의 deprecated alias다. 예시는 3.5의 같은 `LOT004`/`EQP04-PM2` R03 run·action을
  `agent_run_id`·`action_id`로 연결한다.
- 목록은 PENDING뿐 아니라 APPROVED·REJECTED 이력을 포함한다.
- `decided_by`, `decided_at`, `decision_comment`가 canonical 결정 정보다. `approved_by`,
  `approved_at`은 REJECTED에서도 같은 canonical 값을 복사하는 deprecated 표시 alias이며 업무
  의미 판정에 사용하지 않는다.
- 화면은 `agent_run_id`·`action_id`로 실행과 조치를 연결한다.
- 안정 정렬: `created_at DESC, approval_id DESC`.

### 3.7 `POST /approvals/{approval_id}/decision`

#### Request

```json
{
  "decision": "APPROVED",
  "decided_by": "operator",
  "decision_comment": "현장 확인 후 승인"
}
```

- `decision`: public enum `APPROVED|REJECTED`
- `decided_by`: 공백 제거 후 1자 이상
- `decision_comment`: 선택, null 또는 공백 제거 후 1..1000자
- 내부 `APPROVE|REJECT`는 public 요청에서 받지 않는다.

#### Response

- 200: 갱신된 `ApprovalItem`
- 404: 승인 요청 없음
- 409: 이미 결정됐거나 현재 상태에서 결정 불가
- 422: 요청 형식 오류
- 503: 승인 상태·delivery queue를 저장할 DB 의존성 오류

승인 결정과 감사로그는 같은 업무 트랜잭션에서 기록한다. `APPROVED`인 EQP_HOLD만 Kafka 전송
대상이 된다. 같은 트랜잭션에서 MES delivery를 WAITING으로 만들고, 트랜잭션 밖의 Backend
worker가 서명된 n8n webhook을 호출한다. Kafka 발행은 n8n Kafka Producer가 수행한다. Kafka
실패로 이미 저장된 승인 결정을 되돌리지 않고 delivery 실패와
`ACTION_SEND_FAILED`를 기록한다. `REJECTED`는 Kafka를 호출하지 않는다.

### 3.8 `GET /audit-logs`

#### Query

`date_from`, `date_to`, `event_type`, `actor_type`, `entity_type`, `entity_id`는 선택이다.

#### Response 200 — `AuditLogItem[]`

```json
[
  {
    "audit_id": 101,
    "occurred_at": "2026-08-04T10:21:10+09:00",
    "at": "2026-08-04T10:21:10+09:00",
    "actor_type": "HUMAN",
    "actor": "HUMAN",
    "actor_id": "operator",
    "event_type": "APPROVAL_DECIDED",
    "entity_type": "APPROVAL",
    "entity_id": "APR-000001",
    "event": "APPROVE",
    "entity": "APPROVAL:APR-000001",
    "before": {"status": "PENDING"},
    "after": {"status": "APPROVED"},
    "detail": null
  }
]
```

- 안정 정렬: `occurred_at DESC, audit_id DESC`.
- DB `before_json`, `after_json`은 API에서 `before`, `after`로 반환한다.
- `at`, `actor`, `event`, `entity`는 최소 화면 가이드용 compatibility alias다. `at`과
  `actor`는 canonical field를 그대로 복사하고, `entity`는 `entity_type:entity_id`다.
  `APPROVAL_DECIDED`는 after status에 따라 `APPROVE|REJECT`, EMAIL `ACTION_SENT`는
  `NOTIFY`, MES_MOCK `ACTION_SENT`는 `SEND`, `HYPOTHESIS_GENERATED`는
  `ACTION_RECOMMEND`로 직렬화한다. 그 밖의 event는 canonical 이름을 유지한다.
- 감사로그는 append-only이며 POST·PATCH·DELETE public API를 제공하지 않는다.
- Common이 append 계약을, 각 도메인이 자기 event 기록을, D가 조회 API를 소유한다.

### 3.9 `POST /agent/ask`

Agent 화면의 Chat 모드가 호출하는 읽기 전용 질의다. 질문에 필요한 PostgreSQL·Neo4j·RAG Tool을
호출하고 근거와 파일럿 한계를 함께 반환한다. 이 endpoint는 action·approval을 새로 만들지 않는다.

#### Request

```json
{
  "question": "Why was EQP04-PM2 held?"
}
```

`question`은 공백 제거 후 1..1000자다.

#### Response 200 — `AgentAskResponse`

```json
{
  "title": "EQP04-PM2 anomaly analysis",
  "answer": "...",
  "tools": [
    {
      "tool_name": "get_equipment_context",
      "status": "SUCCESS",
      "result_summary": "topology evidence loaded",
      "name": "get_equipment_context",
      "result": "topology evidence loaded"
    }
  ],
  "predicted_fault_code": "RFM",
  "confidence": 0.84,
  "recommended_action": "EQP_HOLD",
  "evidence_items": [
    {
      "type": "DOCUMENT",
      "source_id": "DOC-TROUBLE-FDC:cs2:<4-digit-seq>",
      "title": "FDC 이상 유형 진단 및 조치 가이드",
      "excerpt": "RFM 관련 점검 근거 ...",
      "document_id": "DOC-TROUBLE-FDC",
      "chunk_id": "DOC-TROUBLE-FDC:cs2:<4-digit-seq>",
      "section": "3.2 RFM — RF Mismatch (RF 정합 불량)"
    }
  ],
  "limitations": ["Pilot scope; production ground truth unavailable"],
  "evidence": {
    "doc_id": "DOC-TROUBLE-FDC",
    "document_id": "DOC-TROUBLE-FDC",
    "chunk_id": "DOC-TROUBLE-FDC:cs2:<4-digit-seq>",
    "section": "3.2 RFM — RF Mismatch (RF 정합 불량)"
  },
  "limit": "Pilot scope ..."
}
```

- `title`, `answer`, `tools`, `predicted_fault_code`, `confidence`, `recommended_action`,
  `evidence_items`, `limitations`는 전부 required이며 항상 응답에 존재한다. 근거·제약이
  없으면 목록은 빈 배열이다. `predicted_fault_code`·`confidence`·`recommended_action`은
  required-nullable이며 질문에 해당 판단 근거가 없으면 field를 생략하지 않고 null을 반환한다.
- `predicted_fault_code`는 `FOC|RFM|MFD|TMD|OTH|null`, `confidence`는 `0..1|null`,
  `recommended_action`은 `MONITORING|WARNING|EQP_HOLD|null`이다. `AgentAskResponse`는 deprecated
  `fault_code` alias를 노출하지 않는다.
- Tool canonical field는 `tool_name`, `status`, `result_summary`다. `name`, `result`만 Chat이
  읽는 deprecated alias다. Auto Analysis용 `n/s`와 정의되지 않은 `detail`은 이 endpoint에
  넣지 않는다.
- `evidence_items`는 `type`을 discriminator로 쓰는 union이다. 공통 필수 field는 `type`,
  `source_id`, `title`, `excerpt`다. type은
  `ALARM|TRACE|GRAPH|DOCUMENT|METROLOGY`다. DOCUMENT는 `document_id`·`chunk_id`가
  필수이고 `section`은 required-nullable이다. GRAPH는 `relation_id`·`graph_revision`이
  필수다. 해당 type이 아닌 provenance field는 직렬화하지 않으며 unknown field를 임의로
  추가하지 않는다.
- METROLOGY 근거에는 조회가 허용된 계측 정보만 담는다. `metrology.alarm_result`는
  PASS/FAIL 격리 평가 라벨이므로 State·Tool·`evidence_items`·호환 `evidence`·응답에
  노출하지 않는다.
- `evidence_items`와 `limitations`가 canonical이다. 단일 `evidence`는 첫 문서 근거 또는 null,
  `limit`은 limitations를 한 문자열로 결합한 deprecated alias다. evidence의 `doc_id`는 canonical
  `document_id`와 같은 값이다.
- Tool 결과는 같은 응답의 evidence ID와 실제 조회 결과를 참조한다.
- 공개 합성 `ground_truth_fault_code`를 State·Tool·prompt·response에 넣지 않는다.
- 예시의 chunk ID는 3.4에 선언한 결정론적 형식의 문서 표기용 placeholder며 실제 응답은 적재
  검증 artifact에 기록된 chunk ID를 사용한다.
- 승인 대기 질문은 조회 결과만 반환하며 승인 상태를 바꾸지 않는다.
- 의존성 실패는 503, 요청 형식 오류는 422다.

---

## 4. 프로젝트 필수 보안·실행 API

### 4.1 `GET /relations/chambers/{chamber_id}`

최종 reference frontend의 Ontology 화면은 Neo4j Browser iframe을 직접 열고 기본 계정 정보를
화면에 표시한다. 이는 구조 확인용 reference이지 수용 가능한 서비스 경계가 아니다. 실제 React
화면은 Neo4j에 직접 접속하지 않고 이 read-only Backend API만 호출한다.

#### Path

| 이름 | 형식 | 필수 | 제약 |
|---|---|---:|---|
| `chamber_id` | string | 예 | trim 후 1자 이상, 존재하지 않으면 404 |

#### Query

| 이름 | 형식 | 필수 | 제약 |
|---|---|---:|---|
| `label` | string | 아니오 | chamber component allowlist: `Area, ProcessStep, EquipmentModel, Equipment, Chamber, Parameter` |
| `limit` | integer | 아니오 | 기본 500, 1..1000 |

#### Response 200 — `ChamberGraphResponse`

아래는 선택 chamber와 `label=Parameter` 관계를 축약해 보여 주는 계약 예시다.

```json
{
  "graph_revision": "3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb",
  "context": {
    "chamber_id": "EQP01-PM1",
    "equipment_id": "EQP01",
    "area": "Photo",
    "model_code": "PH-9000",
    "process_step_id": "CT-PHOTO",
    "parameter_ids": ["PH_DEV", "PH_DOSE", "PH_FOCUS", "PH_PEB"],
    "adjacent_process_step_ids": ["CT-ETCH"],
    "relation_ids": [
      "REL-47fcae63de255c114f5d",
      "REL-d2de931b285063c7a8ef",
      "REL-9687560b5876022b2512",
      "REL-75e65f542c456ff70886"
    ]
  },
  "nodes": [
    {
      "node_id": "Chamber:EQP01-PM1",
      "label": "Chamber",
      "business_id": "EQP01-PM1",
      "name": "EQP01-PM1",
      "properties": {}
    },
    {
      "node_id": "Parameter:PH_DEV",
      "label": "Parameter",
      "business_id": "PH_DEV",
      "name": "PH_DEV",
      "properties": {}
    },
    {
      "node_id": "Parameter:PH_DOSE",
      "label": "Parameter",
      "business_id": "PH_DOSE",
      "name": "PH_DOSE",
      "properties": {}
    },
    {
      "node_id": "Parameter:PH_FOCUS",
      "label": "Parameter",
      "business_id": "PH_FOCUS",
      "name": "PH_FOCUS",
      "properties": {}
    },
    {
      "node_id": "Parameter:PH_PEB",
      "label": "Parameter",
      "business_id": "PH_PEB",
      "name": "PH_PEB",
      "properties": {}
    }
  ],
  "relationships": [
    {
      "relation_id": "REL-47fcae63de255c114f5d",
      "type": "MEASURED_ON",
      "from_node_id": "Parameter:PH_DEV",
      "to_node_id": "Chamber:EQP01-PM1"
    },
    {
      "relation_id": "REL-d2de931b285063c7a8ef",
      "type": "MEASURED_ON",
      "from_node_id": "Parameter:PH_DOSE",
      "to_node_id": "Chamber:EQP01-PM1"
    },
    {
      "relation_id": "REL-9687560b5876022b2512",
      "type": "MEASURED_ON",
      "from_node_id": "Parameter:PH_FOCUS",
      "to_node_id": "Chamber:EQP01-PM1"
    },
    {
      "relation_id": "REL-75e65f542c456ff70886",
      "type": "MEASURED_ON",
      "from_node_id": "Parameter:PH_PEB",
      "to_node_id": "Chamber:EQP01-PM1"
    }
  ],
  "node_count": 5,
  "relationship_count": 4
}
```

- 응답은 선택 chamber와 소속·모델·AREA·Parameter·Process Step 인접 관계로
  제한한 subgraph와 context다. `node_count == len(nodes)`,
  `relationship_count == len(relationships)`를 보장한다.
- Recipe·RecipeStep은 final graph에 존재하지만 chamber component와 연결되지 않으므로 이
  endpoint의 label filter·응답에는 포함하지 않는다.
- `context`는 label filter와 무관하게 선택 chamber의 canonical context를 반환한다. `label`은
  `nodes`·`relationships` projection만 좁히며 선택 chamber와 반환 edge의 양 endpoint는
  항상 보존한다. `limit`도 dangling edge를 만들지 않는다.
- 전체 graph의 44 nodes / 85 relationships는 적재·readiness gate이며 개별 chamber 응답
  count의 고정값이 아니다.
- node는 해당 label의 고유 업무 ID를 사용한다.
- node 정렬: `label ASC, business_id ASC`.
- relationship 정렬: `type ASC, from_node_id ASC, to_node_id ASC`.
- Cypher 문자열, Neo4j URI·계정·비밀번호를 response에 포함하지 않는다.
- `relation_id` 알고리즘 `REL-SHA20`은
  `<TYPE>|<FROM_LABEL>:<FROM_BUSINESS_ID>|<TO_LABEL>:<TO_BUSINESS_ID>` UTF-8 SHA-256의 앞
  20 lowercase hex에 `REL-`을 붙인다. business ID는 `bk-v1`의 key/type-tag 직렬화다. 예를
  들어 `MEASURED_ON|Parameter:parameter_id=s:PH_FOCUS|Chamber:chamber_id=s:EQP01-PM1`은
  `REL-9687560b5876022b2512`다.
- `graph_revision`은 성공 marker의 `actual_graph_fingerprint_sha256`과 정확히 같은 64 lowercase
  hex다. marker의 epoch·source SHA·44/85가 맞고 expected==actual이며 live graph 재계산값도
  actual과 같을 때만 응답한다. marker는 commit·live 검증 뒤 마지막으로 기록한다.
- `get_equipment_context`는 같은 repository 결과를 사용하는 내부 Tool DTO다. 이 public
  endpoint에 두 번째 응답 DTO를 등록하지 않는다.

### 4.2 `POST /agent/runs` — 프로젝트 필수 실행 API

Alarm History의 선택 행에서 source-aware 분석을 시작한다. 최종 참고의 단수형
`POST /agent/run`과 이를 호출하는 `WF1-alarm-to-agent`는 stale이며 이 계약의 alias가 아니다.

#### Request

```json
{
  "alarm": {"source": "TRACE", "alarm_id": "TAL-0001"}
}
```

#### Response 202 — `AgentRunAccepted`

```json
{
  "agent_run_id": "RUN-000002",
  "status": "RUNNING",
  "alarm": {"source": "TRACE", "alarm_id": "TAL-0001"}
}
```

accepted status는 `RUNNING`이다. run을 `RUNNING`으로 저장한 뒤 202를 반환하며, 별도 queue
상태를 두지 않는다 — `002_agent_runtime_clean.sql`의
`CHECK (status IN ('RUNNING','WAITING_APPROVAL','COMPLETED','FAILED'))`가 다른 값을 저장할 수
없다. body는 `agent_run_id`·`status`·`alarm` 3개이며 `thread_id`·incident·대표 AlarmRef 같은
내부 실행 문맥은 상세 조회가 반환한다.

같은 incident가 RUNNING·WAITING_APPROVAL이면 409 `INCIDENT_ALREADY_RUNNING`, 이미 완료되어
재실행 금지 상태면 409 `INCIDENT_ALREADY_PROCESSED`다. AlarmRef 없음은 404, 형식 오류는 422,
필수 dependency 미준비는 503이다. source 없는 `{alarm_id}` body는 422다.

---

## 5. 필수 내부·운영 API와 선택 확장

### 5.1 필수 내부·운영 API

아래 endpoint는 사용자 화면 수와 호환 필수 9개 업무 API 수에는 포함하지 않지만, 자동화와
운영 수용 기준에는 필수다.

| 담당 | Method | Path | 용도 |
|---|---|---|---|
| C | POST | `/internal/actions/{action_id}/delivery` | n8n/Kafka worker 상태 write-back |
| Common | GET | `/health` | process liveness, `HealthResponse`, 업무 API 수 제외 |
| Common | GET | `/health/ready` | PostgreSQL·migration·Neo4j·RAG·n8n·Kafka readiness, 업무 API 수 제외 |

이 세 endpoint는 선택 확장이 아니며 운영·자동화 수용을 위한 필수 계약이다.

`POST /internal/actions/{action_id}/delivery`는 다음 signed callback 계약을 사용한다.

- `X-Delivery-Timestamp`: Unix seconds. 서버 시각과 300초를 초과해 차이나면 401
- `X-Delivery-Signature`: `sha256=<hex>`. `N8N_WEBHOOK_SECRET`을 key로
  `timestamp + "." + raw_body`를 HMAC-SHA256한 값이며 constant-time으로 비교
- request body:

```json
{
  "event_id": "EVT-000001",
  "channel": "MES_MOCK",
  "status": "SENT",
  "provider_message_id": "kafka:fdc.actions.result:0:42",
  "request_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "completed_at": "2026-08-04T07:02:00+09:00",
  "error_code": null
}
```

`channel`은 내부 Enum `EMAIL|MES_MOCK`, callback `status`는 `SENT|FAILED`다. 같은
`SENT`는 non-empty `provider_message_id`와 null `error_code`, `FAILED`는 non-empty
`error_code`와 nullable `provider_message_id`를 요구한다. `request_hash`는 접두사 없는
`64-lowercase-hex`, `completed_at`은 offset을 포함한 date-time이다. 같은
`action_id`·channel·request_hash의 재수신은 외부 효과나 감사로그를 중복 생성하지 않고 200과
같은 `DeliveryResult`를 반환한다. 같은 action/channel의 다른 hash나 터미널 상태 변경은 409,
서명 실패는 401, 대상 없음은 404, 형식 오류는 422, 저장소 장애는 503이다.

`DeliveryResult`는 다음 required field를 반환한다.

```json
{
  "action_id": "ACT-000003",
  "channel": "MES_MOCK",
  "status": "SENT",
  "request_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "provider_message_id": "kafka:fdc.actions.result:0:42",
  "completed_at": "2026-08-04T07:02:00+09:00",
  "error_code": null,
  "duplicate": false
}
```

`status`는 `SENT|FAILED`다. `provider_message_id`와 `error_code`의 nullability는 요청
상태 규칙과 같고 `duplicate`는 같은 hash의 멱등 재수신이면 true다.

Backend→n8n webhook도 같은 timestamp/raw-body HMAC과 replay window를 사용한다.
`request_hash`는 n8n·Kafka·MES Mock이 변경하지 않고 왕복하며 Kafka request/result record key는
모두 `action_id`다. Kafka result는 path의 같은 action_id와 내부 `channel=MES_MOCK`으로
정규화한 뒤 이 callback을 호출한다. public 화면 projection에서만 `MES`로 바꾼다.

`GET /health`는 외부 의존성을 검사하지 않고 200을 반환한다.

```json
{
  "status": "UP"
}
```

`GET /health/ready`는 다음을 병렬·timeout으로 확인한다.

- PostgreSQL Runtime profile의 dataset epoch·schema·권한
- reference migration compatibility marker
- Neo4j 44 nodes / 85 relationships success marker·fingerprint
- runtime `kosa_agent`의 RAG `document`·`document_chunk` schema, canonical document ID
  `DOC-SPEC-PH9000|DOC-SPEC-ET7500|DOC-TROUBLE-FDC`, source·corrected SHA-256,
  `cs2` contract hash, 고정 model revision·weights hash, COMMITTED marker와 live DB fingerprint,
  chunk 1건 이상/문서, NULL embedding 0건, vector dimension 1024, 지정 검색 smoke 3건
- n8n readiness
- Kafka metadata와 `fdc.actions`·`fdc.actions.result` topic 접근

`ReadinessResponse`는 모든 check를 항상 포함한다.

```json
{
  "status": "READY",
  "dataset_epoch": "fdc_final_20260818",
  "checks": {
    "postgresql_runtime": {"status": "PASS", "reason_code": null, "latency_ms": 8},
    "reference_migration": {"status": "PASS", "reason_code": null, "latency_ms": 2},
    "neo4j": {"status": "PASS", "reason_code": null, "latency_ms": 15},
    "rag": {"status": "PASS", "reason_code": null, "latency_ms": 21},
    "n8n": {"status": "PASS", "reason_code": null, "latency_ms": 12},
    "kafka": {"status": "PASS", "reason_code": null, "latency_ms": 18}
  }
}
```

각 check의 `status`는 `PASS|FAIL`, `reason_code`는 allowlist string 또는 null,
`latency_ms`는 0 이상 정수다. 전부 PASS면 200과 `READY`, 하나라도 FAIL이면 같은 DTO를
담은 503과 `NOT_READY`를 반환한다. anomaly model artifact는 조치 규칙의 필수 dependency가
아니며, readiness 사유에 host·port·계정·secret·원문 exception을 노출하지 않는다.

### 5.2 선택 확장 API

이 표는 멘토 기준 public 필수 11개와 구분한 **원본 범위 분류**다. 팀은 이 중 D의 5개를
5.3에서 release 필수로 승격한다. 성공 응답·기타 상태는 CSV와 같은 계약이다.

| 담당 | Method | Path | 용도 | 성공 응답 | 기타 상태 |
|---|---|---|---|---|---|
| A | GET | `/dataset/bounds` | 데이터 epoch·날짜·filter option | `DatasetBoundsResponse` | 503 |
| A | GET | `/dashboard/summary` | 서버 집계 대시보드 | `DashboardSummaryResponse` | 422, 503 |
| A | GET | `/alarms/{source}/{alarm_id}` | source-aware 알람 상세 | `AlarmDetailResponse` | 404, 422, 503 |
| A | GET | `/alarms/paged` | 페이지 알람 목록 | `PageEnvelope<AlarmItem>` | 422, 503 |
| A | GET | `/traces/catalog` | Trace 선택 목록 | `TraceCatalogResponse` | 422, 503 |
| A | POST | `/traces/search` | 복합 Trace 검색 | `TraceSearchResponse` | 422, 503 |
| B | GET | `/relations/equipment/{equipment_id}` | 설비 관계 | `EquipmentRelationsResponse` | 404, 422, 503 |
| B | GET | `/documents/{document_id}` | 문서 상세 | `DocumentDetailResponse` | 404, 422, 503 |
| C | GET | `/agent/runs/{run_id}` | 실행 상세 | `AgentRunDetailResponse` | 404, 422, 503 |
| C | POST | `/agent/runs/{run_id}/retry` | 실패 실행 재시도 | 202 `AgentRunAccepted` | 404, 409, 422, 503 |
| C | GET | `/agent/runs/paged` | 페이지 실행 이력 | `PageEnvelope<AgentRunItem>` | 422, 503 |
| C | GET | `/approvals/paged` | 페이지 승인 이력 | `PageEnvelope<ApprovalItem>` | 422, 503 |
| C | GET | `/actions` | action 목록 | `ActionItem[]` | 422, 503 |
| C | GET | `/actions/{action_id}` | action·channel delivery 상세 | `ActionDetailResponse` | 404, 422, 503 |
| C | POST | `/actions/{action_id}/deliveries/{channel}/retry` | 실패 channel 재전송 | `PublicDeliveryResult` | 404, 409, 422, 503 |
| D | POST | `/analytics/query` | 선택 자연어 Text2SQL | `AnalysisQueryResponse` | 422, 503 |
| D | POST | `/analytics/validate` | SQL 실행 없는 검증 | `SqlValidateResponse` | 422 |
| D | GET | `/analytics/history` | 질의 이력 | `PageEnvelope<NlQueryLogItem>` | 422 |
| D | GET | `/analytics/evaluations` | Text2SQL 평가 이력 | `PageEnvelope<EvaluationResponse>` | 422 |
| D | GET | `/audit-logs/paged` | 페이지 감사 조회·집계 | `AuditLogPageResponse` | 422, 503 |

### 5.3 팀 release 필수 확장 API

다음 5개는 멘토 기준 분류상 확장이지만 팀의 7개 주 navigation과 최종 시연에서는 필수다.
구현 유무와 분리된 `api_contract_team_release.json`을 기준으로 수용하며, 이 표의 정확한 집합을
줄이거나 다른 선택 확장으로 대체하지 않는다.

| 담당 | Method | Path | 용도 | 성공 응답 | 기타 상태 |
|---|---|---|---|---|---|
| D | POST | `/analytics/query` | 자연어 Text2SQL | `AnalysisQueryResponse` | 422, 503 |
| D | POST | `/analytics/validate` | SQL 실행 없는 검증 | `SqlValidateResponse` | 422 |
| D | GET | `/analytics/history` | 질의 이력·재실행 원본 | `NlQueryHistoryResponse` | 422 |
| D | GET | `/analytics/evaluations` | immutable 평가 artifact 이력 | `EvaluationListResponse` | 422 |
| D | GET | `/audit-logs/paged` | 전역 페이지 감사 조회·집계 | `AuditLogPageResponse` | 422, 503 |

### 5.4 확장 API 불변 조건

- `/paged`는 `PageEnvelope`만 반환한다.
- delivery 재시도의 public channel `MES`는 내부 `MES_MOCK`으로 변환한다.
- public retry의 `PublicDeliveryResult`는 `DeliveryResult`와 같은 field를 사용하되
  response channel도 `EMAIL|MES`로 projection한다.
- `/internal`은 public frontend가 호출하지 않으며 별도 인증·secret과 allowlist를 사용한다.

### 5.5 팀 release Text2SQL 계약

`POST /analytics/query`는 `{ "question": "..." }`를 받고 `generated_sql`, `columns`, `rows`,
`row_count`, `is_valid`, `is_rejected`, `reject_reason`, `visualization`, `latency_ms`,
`nl_query_log_id`를 반환한다. question은 1..1000자이며 SQL은 allowlist 안의 단일 read-only
SELECT만 허용한다. 정책 거부는 SQL을 실행하지 않고 HTTP 200과 `is_rejected=true`를 반환한다.
합성 `ground_truth_fault_code`는 일반 Text2SQL allowlist에서 제외한다. 자연어 분석 화면 안의
이력·평가는 보조 탭이며 별도 8번째 navigation으로 세지 않는다. 이 5개 중 하나라도 미구현이면
팀의 최종 7화면 release gate를 통과하지 못한다.

---

## 6. 감사 이벤트

| Event | Entity | 기록 주체 |
|---|---|---|
| `DETECTION_COMPLETED` | `LOT_HIST` | A |
| `AGENT_RUN_STARTED` | `AGENT_RUN` | C |
| `HYPOTHESIS_GENERATED` | `AGENT_RUN` | C |
| `APPROVAL_REQUESTED` | `APPROVAL` | C |
| `APPROVAL_DECIDED` | `APPROVAL` | C |
| `ACTION_SENT` | `ACTION` | C delivery service |
| `ACTION_SEND_FAILED` | `ACTION` | C delivery service |
| `AGENT_RUN_COMPLETED` | `AGENT_RUN` | C |
| `AGENT_RUN_FAILED` | `AGENT_RUN` | C |

Event와 entity 조합은 입력으로 받지 않고 공통 mapping에서 파생한다. 로그에는 DSN, 비밀번호,
API key, SQL 원문 및 원본 LLM prompt를 기록하지 않는다.

---

## 7. Contract 검증 기준

- 호환 필수 9개, Ontology 보안 필수 1개, Agent 실행 필수 1개의 path·method가 OpenAPI에 존재한다.
- bare array endpoint와 `/paged` 응답이 섞이지 않는다.
- `APPROVED|REJECTED` boundary mapping을 양·음성 test로 검증한다.
- `AlarmRef` source 누락·잘못된 조합을 거부한다.
- `fault_code` 호환 alias가 합성 GT를 읽지 않는지 검증한다.
- `MES -> MES_MOCK` 매핑과 화면의 Mock 표시를 검증한다.
- 감사 event/entity mapping과 append-only 권한을 검증한다.
- 최소 화면 5개의 실제 API 연결에서 Mock·fixture fallback과 Neo4j Browser 직접 접속이 0건이다.
- Ontology API는 선택 chamber의 `ChamberGraphResponse` 하나만 OpenAPI에 등록하고,
  subset count·edge endpoint 보존·없는 chamber 404를 검증한다. 전체 44/85는 별도 graph gate다.
- 모든 `date-time` 응답은 UTC offset을 포함하고 Asia/Seoul 예시는 `+09:00`이다.
- `decision_comment`, run→action→approval ID link, AgentAsk required·nullable 계약을 정상·빈 근거·
  승인 조회 fixture로 검증한다.
- API CSV operation은 헤더 제외 34개를 유지하고 `POST /agent/runs`는 확장이 아니라 실행 필수로
  정확히 한 번만 존재한다.
- compatibility alias는 canonical field에서만 파생되며 공개 합성 GT를 읽지 않는다.
- `GET /alarms`의 빈 문자열→unset 예외은 선택 `equipment`·`chamber`·`parameter` 세
  필터에만 적용하고 다른 빈 ID는 422로 거부한다.
- R03 상세의 `member_wafer_refs`는 정확히 3개, `member_alarm_refs`는 최종 epoch에서
  정확히 9개 TRACE AlarmRef인지 검증한다.
- `AgentAskResponse`의 predicted·confidence·recommended field는 required-nullable이고
  `fault_code`는 없다. DOCUMENT의 `document_id`·`chunk_id` 필수 및 `section`
  required-nullable, GRAPH의 `relation_id`·`graph_revision` 필수, METROLOGY
  `alarm_result` 비노출을 contract test한다.
- `DeliveryResult` 멱등 duplicate와 SENT/FAILED nullability를 검증한다.
- readiness는 reference compatibility marker와 RAG canonical ID 3종·source/corrected hash·
  chunk·NULL embedding 0·dimension 1024·검색 smoke를 PostgreSQL·Neo4j·n8n·Kafka 검사와
  함께 검증하고, 실패 시에도 같은 `ReadinessResponse`와 503을 반환한다.
