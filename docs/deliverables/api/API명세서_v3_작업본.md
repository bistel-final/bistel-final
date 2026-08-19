# API 명세서

**PhotoEtch FDC Agent Pilot — 최종 데이터 전환 작업본**

---

## 1. 문서 정보

| 항목 | 내용 |
|---|---|
| 버전 | v3 작업본 |
| 작성일 | 2026.08.19 |
| 기준 | 멘토님 제공 최종 `project.zip`의 실제 React 5화면, `검토질문_답변.html`, 최종 CSV·Generator |
| 목적 | 5개 화면의 호환 필수 API 9개, Ontology 보안 필수 API 1개와 선택 확장 경계를 고정 |
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
보안 필수로 추가한다. Text2SQL, 상세 조회, 페이지 조회, 재시도, 평가 API는 선택 확장으로 둔다.
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

2xx가 아닌 응답은 다음 구조를 사용한다.

```json
{
  "code": "RESOURCE_NOT_FOUND",
  "message": "요청한 리소스를 찾을 수 없습니다.",
  "details": {}
}
```

| HTTP | 의미 |
|---:|---|
| 401 | `/internal` endpoint의 인증·secret 검증 실패 |
| 404 | 식별자로 요청한 리소스 없음 |
| 409 | 이미 결정된 승인, 멱등성 충돌, 상태 전이 충돌 |
| 422 | 요청 형식·Enum·범위 오류 |
| 500 | 예상하지 못한 서버 오류. 응답·로그에 secret과 원문 prompt를 노출하지 않음 |
| 503 | PostgreSQL·Neo4j·RAG·LLM·n8n·Kafka 의존성 오류 |

선택 확장 Text2SQL을 구현할 경우 정책 거부는 요청 형식 오류가 아니다.
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
| ToolCallItem·AgentAsk tool | `tool_name`, `status`, `result_summary` | `name`, `n`, `s`, `result`, `detail` | `name`·`n`은 tool name, `s`는 status, `result`·`detail`은 canonical summary에서 파생 |
| AgentRunItem | `chamber_id` | `chamber` | 같은 chamber ID 복사 |
| AgentRunItem | `predicted_fault_code` | `fault_code`, `fault_name`, `fault_color` | `fault_code`는 예측값 복사. 등록된 UI metadata가 없으면 name·color는 null이며 임의로 생성하지 않음 |
| ApprovalItem | `lot_id`, `equipment_id`, `chamber_id` | `lot`, `equipment`, `chamber` | 각 canonical ID를 같은 순서의 alias로 1:1 복사 |
| ApprovalItem | `decided_by`, `decided_at` | `approved_by`, `approved_at` | 결정 상태와 무관하게 같은 값 복사하는 legacy 표시 alias |
| AuditLogItem | `occurred_at`, `actor_type`, `event_type`, `entity_type`+`entity_id` | `at`, `actor`, `event`, `entity` | 시각·actor는 복사, event는 3.8 mapping, entity는 `entity_type:entity_id` |
| AgentAskResponse | `evidence_items`, `limitations` | `evidence`, `limit` | 첫 DOCUMENT 근거 또는 null, limitations를 하나의 문자열로 결합 |
| AgentAsk `evidence` | `document_id` | `doc_id` | 호환 단일 DOCUMENT 객체 안에서만 같은 ID 복사 |

`fault`·`fault_code` alias를 `lot_history.fault_code`나 parameter→Fault 고정표에서 생성하는 것은
금지한다. alias가 없더라도 canonical field만으로 같은 화면을 렌더링하는 Frontend contract test를
추가한다. 예측 전에는 `predicted_fault_code`와 alias가 모두 null이며 화면은 이를 `분석 전`으로
표시하거나 예측 분포 집계에서 제외한다. null을 합성 GT로 채우지 않는다.

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
    "chunk_id": "DOC-SPEC-PH9000:cs1:<4-digit-seq>",
    "corpus_revision": "<active-corpus-revision>",
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
- corrected corpus는 원문 YAML의 `DOC-SPEC-PH9000`, `DOC-SPEC-ET7500`,
  `DOC-TROUBLE-FDC`를 canonical `document_id`로 그대로 승계한다. `chunk_id`는
  `<document_id>:<chunk_schema_version>:<4자리 순번>` 형식으로 결정론적으로 생성하며 최초
  `chunk_schema_version`은 `cs1`이다. 같은 원문·분할 규칙·순번에서는 재적재해도 바뀌지
  않고, `corpus_revision`은 별도로 반환한다.
- 예시의 `DOC-SPEC-PH9000:cs1:<4-digit-seq>`와 `<active-corpus-revision>`은 현재 패키지에
  ACTIVE corpus manifest가 없어 사용한 placeholder다. 형식과 생성은 결정론적이지만 예시
  chunk ID·revision 문자열 자체는 최종값이 아니며, 응답은 검증된 ACTIVE manifest의 실제
  값을 사용한다.
- 안정 정렬: `score DESC, document_id ASC, chunk_id ASC`.
- corrected corpus만 검색하며 구 조치·수치나 고정 설비 상하류 표현을 반환하지 않는다.

### 3.5 `GET /agent/runs`

#### Query

`date_from`, `date_to`는 선택이며 함께 주거나 함께 생략한다. 상태·가설 filter는 확장 계약이다.

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
        "name": "get_fdc_summary",
        "n": "get_fdc_summary",
        "s": "SUCCESS"
      }
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
- `status`는 `PENDING|RUNNING|WAITING_APPROVAL|COMPLETED|FAILED`다. `tools`는 항상 존재하며
  아직 호출이 없으면 빈 배열이다. 예측 전·실패 상태에서는 predicted fault·confidence·recommended
  action·표시 alias가 null일 수 있다.
- Tool canonical field는 `tool_name`, `status`, `result_summary`다. `name`, `n`, `s`는
  한 전환 revision 동안만 같은 값을 제공하는 deprecated alias다. Tool status는
  `SUCCESS|ERROR|TIMEOUT`이다.
- source-aware 식별자는 `alarm_source`와 `alarm_id`의 쌍이다.
- 안정 정렬: `created_at DESC, agent_run_id DESC`.

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

- `EQP_HOLD` action만 승인 대상이다.
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
      "n": "get_equipment_context",
      "s": "SUCCESS",
      "result": "OK",
      "detail": "topology evidence"
    }
  ],
  "predicted_fault_code": "RFM",
  "confidence": 0.84,
  "recommended_action": "EQP_HOLD",
  "evidence_items": [
    {
      "type": "DOCUMENT",
      "source_id": "DOC-TROUBLE-FDC:cs1:<4-digit-seq>",
      "title": "FDC 이상 유형 진단 및 조치 가이드",
      "excerpt": "RFM 관련 점검 근거 ...",
      "document_id": "DOC-TROUBLE-FDC",
      "chunk_id": "DOC-TROUBLE-FDC:cs1:<4-digit-seq>",
      "section": "3.2 RFM — RF Mismatch (RF 정합 불량)",
      "corpus_revision": "<active-corpus-revision>"
    }
  ],
  "limitations": ["Pilot scope; production ground truth unavailable"],
  "evidence": {
    "doc_id": "DOC-TROUBLE-FDC",
    "document_id": "DOC-TROUBLE-FDC",
    "chunk_id": "DOC-TROUBLE-FDC:cs1:<4-digit-seq>",
    "section": "3.2 RFM — RF Mismatch (RF 정합 불량)",
    "corpus_revision": "<active-corpus-revision>"
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
- Tool canonical field는 `tool_name`, `status`, `result_summary`다. `name`, `n`, `s`,
  `result`, `detail`은 최종 참고 React용 deprecated alias다.
- `evidence_items`의 공통 필수 field는 `type`, `source_id`, `title`, `excerpt`다. type은
  `ALARM|TRACE|GRAPH|DOCUMENT|METROLOGY`이고 DOCUMENT는 `document_id`, `chunk_id`,
  `corpus_revision`을 추가로 요구하고 `section` field는 선택(nullable)으로 선언한다.
  GRAPH는 `relation_id`, `graph_revision`을 추가로 요구한다. 해당 type이 아닌 provenance
  field는 null이며 unknown field를 임의로 추가하지 않는다.
- METROLOGY 근거에는 조회가 허용된 계측 정보만 담는다. `metrology.alarm_result`는
  PASS/FAIL 격리 평가 라벨이므로 State·Tool·`evidence_items`·호환 `evidence`·응답에
  노출하지 않는다.
- `evidence_items`와 `limitations`가 canonical이다. 단일 `evidence`는 첫 문서 근거 또는 null,
  `limit`은 limitations를 한 문자열로 결합한 deprecated alias다. evidence의 `doc_id`는 canonical
  `document_id`와 같은 값이다.
- Tool 결과는 같은 응답의 evidence ID와 실제 조회 결과를 참조한다.
- 공개 합성 `ground_truth_fault_code`를 State·Tool·prompt·response에 넣지 않는다.
- 예시의 chunk ID·corpus revision은 3.4에 선언한 결정론적 형식의 비최종
  placeholder며 실제 응답은 ACTIVE manifest 값을 사용한다.
- 승인 대기 질문은 조회 결과만 반환하며 승인 상태를 바꾸지 않는다.
- 의존성 실패는 503, 요청 형식 오류는 422다.

---

## 4. 프로젝트 필수 보안 API

### 4.1 `GET /ontology/graph`

최종 reference frontend의 Ontology 화면은 Neo4j Browser iframe을 직접 열고 기본 계정 정보를
화면에 표시한다. 이는 구조 확인용 reference이지 수용 가능한 서비스 경계가 아니다. 실제 React
화면은 Neo4j에 직접 접속하지 않고 이 read-only Backend API만 호출한다.

#### Query

| 이름 | 형식 | 필수 | 제약 |
|---|---|---:|---|
| `label` | string | 아니오 | allowlist: `Area, Recipe, RecipeStep, ProcessStep, EquipmentModel, Equipment, Chamber, Parameter` |
| `limit` | integer | 아니오 | 기본 500, 1..1000 |

#### Response 200 — `OntologyGraphResponse`

```json
{
  "graph_revision": "<active-graph-revision>",
  "nodes": [
    {
      "node_id": "Chamber:EQP01-PM1",
      "label": "Chamber",
      "business_id": "EQP01-PM1",
      "name": "EQP01-PM1",
      "properties": {}
    }
  ],
  "relationships": [
    {
      "relation_id": "MEASURED_ON|Parameter:PH_FOCUS|Chamber:EQP01-PM1",
      "type": "MEASURED_ON",
      "from_node_id": "Parameter:PH_FOCUS",
      "to_node_id": "Chamber:EQP01-PM1"
    }
  ],
  "node_count": 44,
  "relationship_count": 85
}
```

- 최종 active graph 전체 조회는 44 nodes / 85 relationships다.
- `RecipeStep.business_id`는 `recipe_id:recipe_step_no`, 그 밖의 node는 해당 label의 고유 업무
  ID를 사용한다.
- node 정렬: `label ASC, business_id ASC`.
- relationship 정렬: `type ASC, from_node_id ASC, to_node_id ASC`.
- Cypher 문자열, Neo4j URI·계정·비밀번호를 response에 포함하지 않는다.
- `relation_id`는 같은 business edge에서 graph revision이 바뀌어도 안정적으로 유지한다.
- `<active-graph-revision>`은 현재 패키지에 ACTIVE graph revision marker가 없어 사용한
  placeholder다. 실제 응답은 검증된 ACTIVE marker 값을 사용하며 placeholder 문자열을 반환하지
  않는다.

---

## 5. 필수 내부·운영 API와 선택 확장

### 5.1 필수 내부·운영 API

아래 endpoint는 사용자 화면 수와 호환 필수 9개 업무 API 수에는 포함하지 않지만, 자동화와
운영 수용 기준에는 필수다.

| 담당 | Method | Path | 용도 |
|---|---|---|---|
| C | POST | `/internal/actions/{action_id}/delivery` | n8n/Kafka worker 상태 write-back |
| Common | GET | `/health` | process liveness, 업무 API 수 제외 |
| Common | GET | `/health/ready` | PostgreSQL·Neo4j·n8n·Kafka readiness, 업무 API 수 제외 |

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
  "request_hash": "sha256:...",
  "completed_at": "2026-08-04T07:02:00+09:00",
  "error_code": null
}
```

`channel`은 내부 Enum `EMAIL|MES_MOCK`, callback `status`는 `SENT|FAILED`다. 같은
`SENT`는 non-empty `provider_message_id`와 null `error_code`, `FAILED`는 non-empty
`error_code`와 nullable `provider_message_id`를 요구한다. `request_hash`는
`sha256:<64-lowercase-hex>`, `completed_at`은 offset을 포함한 date-time이다. 같은
`action_id`·channel·request_hash의 재수신은 외부 효과나 감사로그를 중복 생성하지 않고 200과
같은 `DeliveryResult`를 반환한다. 같은 action/channel의 다른 hash나 터미널 상태 변경은 409,
서명 실패는 401, 대상 없음은 404, 형식 오류는 422, 저장소 장애는 503이다.

`GET /health/ready`는 다음을 병렬·timeout으로 확인한다.

- PostgreSQL Runtime profile의 dataset epoch·schema·권한
- reference migration compatibility marker
- Neo4j 44 nodes / 85 relationships success marker·fingerprint
- ACTIVE RAG corpus revision·embedding dimension
- n8n readiness
- Kafka metadata와 `fdc.actions`·`fdc.actions.result` topic 접근

필수 dependency나 marker가 미준비면 503을 반환한다. anomaly model artifact는 조치 규칙의 필수
dependency가 아니며, readiness 사유에 host·port·계정·secret·원문 exception을 노출하지 않는다.

### 5.2 선택 확장 API

선택 확장은 호환 필수 9개와 Ontology 보안 필수 1개 완료 후 구현한다.

| 담당 | Method | Path | 용도 |
|---|---|---|---|
| A | GET | `/dataset/bounds` | 데이터 epoch·날짜·filter option |
| A | GET | `/dashboard/summary` | 서버 집계 대시보드 |
| A | GET | `/alarms/{source}/{alarm_id}` | source-aware 알람 상세 |
| A | GET | `/alarms/paged` | 페이지 알람 목록 |
| A | GET | `/traces/catalog` | Trace 선택 목록 |
| A | POST | `/traces/search` | 복합 Trace 검색 |
| B | GET | `/relations/chambers/{chamber_id}` | 챔버 관계 |
| B | GET | `/relations/equipment/{equipment_id}` | 설비 관계 |
| B | GET | `/documents/{document_id}` | 문서 상세 |
| C | POST | `/agent/runs` | `{alarm:{source,alarm_id}}`로 분석 시작 |
| C | GET | `/agent/runs/{run_id}` | 실행 상세 |
| C | POST | `/agent/runs/{run_id}/retry` | 실패 실행 재시도 |
| C | GET | `/agent/runs/paged` | 페이지 실행 이력 |
| C | GET | `/approvals/paged` | 페이지 승인 이력 |
| C | GET | `/actions` | action 목록 |
| C | GET | `/actions/{action_id}` | action·channel delivery 상세 |
| C | POST | `/actions/{action_id}/deliveries/{channel}/retry` | 실패 channel 재전송 |
| D | POST | `/analytics/query` | 선택 자연어 Text2SQL |
| D | POST | `/analytics/validate` | SQL 실행 없는 검증 |
| D | GET | `/analytics/history` | 질의 이력 |
| D | GET | `/analytics/evaluations` | Text2SQL 평가 이력 |
| D | GET | `/audit-logs/paged` | 페이지 감사 조회·집계 |

### 5.3 확장 API 불변 조건

- `POST /agent/runs`는 `{ "alarm": { "source": "TRACE", "alarm_id": "..." } }`만 받는다.
- source 없는 legacy `{alarm_id}` 요청은 422로 거부하거나 명시적 legacy adapter에서 선택을 요구한다.
- `/paged`는 `PageEnvelope`만 반환한다.
- delivery 재시도의 public channel `MES`는 내부 `MES_MOCK`으로 변환한다.
- `/internal`은 public frontend가 호출하지 않으며 별도 인증·secret과 allowlist를 사용한다.

### 5.4 선택 Text2SQL 계약

`POST /analytics/query`는 `{ "question": "..." }`를 받고 `generated_sql`, `columns`, `rows`,
`row_count`, `is_valid`, `is_rejected`, `reject_reason`, `visualization`, `latency_ms`,
`nl_query_log_id`를 반환한다. question은 1..1000자이며 SQL은 allowlist 안의 단일 read-only
SELECT만 허용한다. 정책 거부는 SQL을 실행하지 않고 HTTP 200과 `is_rejected=true`를 반환한다.
합성 `ground_truth_fault_code`는 일반 Text2SQL allowlist에서 제외한다. 이 기능의 미구현은
최종 5화면 E2E를 막지 않는다.

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

- 호환 필수 9개와 Ontology 보안 필수 1개의 path·method가 OpenAPI에 존재한다.
- bare array endpoint와 `/paged` 응답이 섞이지 않는다.
- `APPROVED|REJECTED` boundary mapping을 양·음성 test로 검증한다.
- `AlarmRef` source 누락·잘못된 조합을 거부한다.
- `fault_code` 호환 alias가 합성 GT를 읽지 않는지 검증한다.
- `MES -> MES_MOCK` 매핑과 화면의 Mock 표시를 검증한다.
- 감사 event/entity mapping과 append-only 권한을 검증한다.
- 최소 화면 5개의 실제 API 연결에서 Mock·fixture fallback과 Neo4j Browser 직접 접속이 0건이다.
- 모든 `date-time` 응답은 UTC offset을 포함하고 Asia/Seoul 예시는 `+09:00`이다.
- `decision_comment`, run→action→approval ID link, AgentAsk required·nullable 계약을 정상·빈 근거·
  승인 조회 fixture로 검증한다.
- compatibility alias는 canonical field에서만 파생되며 공개 합성 GT를 읽지 않는다.
- `GET /alarms`의 빈 문자열→unset 예외은 선택 `equipment`·`chamber`·`parameter` 세
  필터에만 적용하고 다른 빈 ID는 422로 거부한다.
- R03 상세의 `member_wafer_refs`는 정확히 3개, `member_alarm_refs`는 최종 epoch에서
  정확히 9개 TRACE AlarmRef인지 검증한다.
- `AgentAskResponse`의 predicted·confidence·recommended field는 required-nullable이고 `fault_code`는
  없으며, DOCUMENT `section` 선택(nullable) 선언과 METROLOGY `alarm_result` 비노출을
  contract test한다.
- readiness는 reference compatibility marker와 ACTIVE RAG corpus revision·embedding dimension을
  PostgreSQL·Neo4j·n8n·Kafka 검사와 함께 검증한다.
