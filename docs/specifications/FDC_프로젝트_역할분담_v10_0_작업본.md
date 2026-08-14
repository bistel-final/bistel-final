# LangGraph 기반 반도체 FDC 이상감지 에이전트 — 역할분담 v10.0 작업본

> 기준 데이터: `kosa_0813.zip`
> 기준 기획: `docs/planning/신규데이터_정답라벨제거_전환기획_v1.md`
> 문서 상태: 신규 데이터 전환 작업본. 멘토 확인 항목이 확정되기 전에는 최종본으로 배포하지 않는다.

## 0. 개정 목적

신규 데이터에는 공개 Fault 정답 라벨이 없다. 따라서 기존 역할분담 v9.6의 고정 Fault·알람·조치 정답과 supervised 분류 성능 합격선을 폐기하고 역할과 완료 기준을 다시 정의한다.

- 규칙으로 재현 가능한 계산은 제공 파생 CSV와 결정론적으로 비교한다.
- 비지도 이상감지 모델은 WAFER별 보조 점수를 제공한다. A가 이를 검증된 versioned
  `IncidentModelSignal`로 집계했을 때만 `[팀 잠정]` Summary-only `MONITORING`을 `WARNING`으로
  한 단계 상향하며, 기본 규칙을 하향하거나 R03 없이 `EQP_HOLD`를 만들 수 없다.
- 원본 ZIP·Generator는 수정하지 않고 corrected generator를 별도 관리한다. Generator에서 얻는
  합성 라벨은 `label_source=SYNTHETIC_GENERATOR`인 평가 전용 artifact로만 만들며 실제 Fault GT나
  Runtime·Agent 입력으로 사용하지 않는다.
- Agent는 Fault 정답을 맞히는 분류기가 아니라 근거를 제시하는 원인 가설 생성기로 정의한다.
- 제공 `action_history` 48건은 화면용 Mock/reference이며 Agent Runtime 정답이나 seed가 아니다.
- 조치는 `MONITORING | WARNING | EQP_HOLD` 3단계로 개발한다.
- 각 담당자는 Backend·AI/Tool·React·테스트·평가까지 끝내는 Full-stack 기능 책임을 유지한다.
- 공용 bootstrap과 Runtime migration은 특정 도메인의 부가 업무가 아니라 4명 공동 선행 작업으로 둔다.

### 0.1 상태 표기

| 표기 | 의미 | 처리 방식 |
|---|---|---|
| `[자료 확정]` | 신규 ZIP·DDL·CSV·실측으로 확인한 사실 | 구현·테스트 기준으로 사용 |
| `[팀 잠정]` | 개발을 멈추지 않기 위해 정한 가역적 정책 | 함수·설정·어댑터로 격리 |
| `[멘토 확인]` | 자료 충돌 또는 공식 기준 부재 | 외부 계약 고정 금지, 답변 후 교체 |

## 1. 공통 개발 원칙

### 1.1 Full-stack 기능 책임

```text
데이터·도메인 로직
→ Repository·Service
→ FastAPI Router·Pydantic DTO
→ Tool 또는 LLM 구조화 출력
→ React 화면·실제 API 연결
→ 단위·계약·통합 테스트
→ 역할별 평가 artifact
```

React 공통 골격과 통합 배포는 방대혁이 관리하지만, 각 기능의 화면 구현·API 연결·상태 처리·검증은 해당 담당자가 책임진다.

### 1.2 단일 모노레포와 계층

- 하나의 FastAPI 애플리케이션과 하나의 React 애플리케이션을 사용한다.
- Backend는 `Router → Service → Repository` 계층을 지킨다.
- 공유 Enum·Tool DTO·오류 계약은 `backend/app/common/`에서 재사용하고 도메인별로 다시 정의하지 않는다.
- 공용 PostgreSQL 서버를 사용하되 clean base Runtime DB와 immutable SQL 평가 snapshot을 논리적으로 분리한다.
- 원본 ZIP·압축 해제본·Generator는 수정하지 않고 corrected generator·corrected copy·manifest·검증
  보고서를 생성한다.

### 1.3 실제 GT 없는 평가와 합성 평가 원칙

- `lot_history.fault_code`는 전 행 `NRM` placeholder이므로 Fault 정답으로 사용하지 않는다.
- Generator의 주입 위치·Fault 상수·HTML Mock은 실제 Fault 정답으로 사용하지 않는다.
- `metrology.PASS/FAIL`은 제품 계측 결과이지 Fault Mode 정답이 아니다.
- `ground_truth_available=false`인 평가에서 Accuracy·Precision·Recall·F1을 계산하지 않는다.
- `[팀 잠정]` corrected generator에서 별도 생성한 합성 평가 artifact에 한해서
  `ground_truth_available=true`, `label_source=SYNTHETIC_GENERATOR`,
  `production_ground_truth_available=false`, `usage_scope=EVALUATION_ONLY`를 함께 기록한다.
- 합성 라벨 기반 지표는 "synthetic generator agreement"로만 표기하고 실제 공정 Fault 정확도나
  일반화 성능으로 해석하지 않는다.
- 합성 라벨 artifact는 학습 feature·threshold 적용 대상 Runtime·Agent Tool·프롬프트·PostgreSQL
  Runtime seed·Neo4j·RAG에 적재하지 않는다. 실행 시 명시적인 평가 artifact 경로에서만 읽는다.
- 멘토 또는 전문가가 독립 라벨셋을 제공한 경우에만 별도 평가 트랙을 추가한다.

### 1.4 React 정보구조

신규 자료의 5개 기능 영역을 기존 React의 8개 Page 컴포넌트에 매핑한다. 상세 URL과 legacy redirect는 별도 URL pattern이므로 “8개 라우트”로 세지 않는다.

| 기능 영역 | 기존 Page·대표 URL |
|---|---|
| 알람 대시보드 | Dashboard `/dashboard` |
| 알람 | Alarms `/alarms`, Trace `/traces` |
| Agent 분석 | Actions `/actions`, AgentRuns `/agent-runs/:runId`, AuditLogs `/audit-logs` |
| 문서 검색 | Knowledge `/knowledge` |
| 자연어 질의 | Analytics `/analytics` |

`/alarms/:alarmId` 같은 상세 URL과 legacy redirect는 기존 URL 호환을 위해 유지하되 Page 수에는 포함하지 않는다. 알람 상세의 `:alarmId`는 동일 ID의 source 충돌을 막기 위해 `TRACE:TAL-...`·`SUMMARY:SAL-...`·`R03:R03-...` 형태의 URL 직렬화된 `SOURCE:alarm_id` composite token으로 저장·복원한다. source 없는 legacy ID는 추측하지 않고 source 선택을 요구한다.

## 2. 최종 역할 요약

| 역할 | 담당자 | Backend·AI 핵심 | 담당 Tool | React 책임 | 평가 책임 | 예상 난이도·강도 |
|---|---|---|---|---|---|---|
| A. Detection Full-stack | 신동원 | Trace 요약·evaluation·알람 규칙·비지도 점수 | FDC 요약 조회 | 대시보드·알람·Trace | 규칙 재현·비지도 안정성·synthetic 평가 | 4.5/5 · 4.5/5 |
| B. Knowledge Full-stack | 강연권 | Neo4j 관계·수정 RAG·근거 provenance | 관계 조회·문서 검색 | 관계·문서 근거 | 관계 정확도·Recall@K·MRR | 4/5 · 4/5 |
| C. Agent Full-stack | 방대혁 | LangGraph·근거 기반 가설·조치·HITL·전송 | 조치 전송 | Agent 실행·조치·승인 | 근거 충실성·상태 전이·E2E | 5/5 · 5/5 |
| D. Analytics Full-stack | 천승현 | 신규 스키마 Text2SQL·통계·차트·감사 조회 | 분석 계획 | 자연어 분석·감사 | SQL 정확도·방어·차트 호환 | 4.5/5 · 4.5/5 |

C의 난이도가 가장 높지만 기존 팀 합의대로 유지한다. 공통 bootstrap·migration·배포 작업은 아래 7장의 공동 책임으로 분리하므로 C의 도메인 공수에 중복 산정하지 않는다.

## 3. A — Detection Full-stack

### 3.1 담당 범위

- `[자료 확정]` `fdc_trace` 14,400행을 입력으로 Trace 요약을 재계산한다.
- `(lot_hist_id, parameter_id, recipe_step_no)` 단위 mean·std·min·max·count를 계산한다.
- 신규 가이드의 window 판정식으로 `evaluation`의 IN·OOC·OOS를 재현한다.
- Trace 알람·Summary 알람·R03을 서로 다른 source로 산출한다.
- `[팀 잠정]` R03은 `(chamber_id, parameter_id, recipe_step_no)`별 연속 OOS를 계산하고 `run == 3`이 되는 시점에만 발행한다.
- IsolationForest 기반 0~1 `anomaly_score`를 선택적 보조 근거로 제공한다.
- corrected generator에서 합성 평가 라벨을 별도 artifact로 생성하고 모델 입력·Runtime 데이터와
  물리적으로 분리한다.
- 알람·Trace·대시보드 API와 React 화면을 실제 DB에 연결한다.

### 3.2 비지도 모델 계약

```text
입력    규칙 계산에 사용하는 요약 feature에서 파생하되 Fault·metrology 결과는 제외
출력    nullable AnomalySignal(score, model_version, score_method,
        display_threshold?, is_anomaly?, action_threshold?, threshold_version?, threshold_validation_status)
허용    VERIFIED action_threshold + SUMMARY-only MONITORING일 때 WARNING으로 한 단계 상향
금지    기본 조치 하향, R03 없는 EQP_HOLD 생성, 합성 라벨의 학습·Runtime·Agent 입력 사용
장애    모델 미준비여도 규칙 알람과 Agent 규칙 경로는 계속 동작
```

### 3.3 Tool·API·화면

- Tool: `get_fdc_summary(lot_hist_id)` — `AlarmRef`의 lot history 해석은 C의 resolver가 담당한다.
- Summary 조회와 Tool 계약은 모델·threshold 검증을 기다리지 않는다. 미준비 시 규칙 summary를 정상
  반환하고 `AnomalySignal=null`로 표시하며, V4-A-3.6 이후 검증된 signal population만 후속 연결한다.
- 내부 batch service: `build_incident_model_signal(lot_hist_ids, action_policy_version)` — 중복 제거·안정
  정렬한 incident member를 한 번에 조회해 공통 `IncidentModelSignal`을 만들며 member별 Tool 반복 호출은 금지한다.
- 필수 API: `GET /dataset/bounds`, `GET /dashboard/summary`, `GET /parameters`, `GET /alarms`, `GET /alarms/{source}/{alarm_id}`, `GET /trace`
- 확장 API: `GET /traces/catalog`, `POST /traces/search`
- 화면: 운영 대시보드, 알람 목록·상세, Trace viewer
- `AlarmRef {source, alarm_id}`를 사용하고 기존 단일 `fdc_alarm` ID를 가정하지 않는다.

### 3.4 완료·평가 기준

- Summary 4,800건·evaluation 4,800건·TRACE 126건·SUMMARY 47건을 제공 파생 fixture와 결정론적으로 비교한다.
- 화면 진입 시 `GET /dataset/bounds`로 corrected 전체 기간을 조회하고 `date_from=2026-08-01`, `date_to=2026-08-12`, `area=ALL`을 명시 전송한다. 이 조건의 기본 저장 알람은 TRACE+SUMMARY 173건이며 `include_derived=true`이면 176건이다. 파생 R03는 명시 조회하고 Agent incident에는 항상 포함한다.
- 같은 source·seed·feature·모델 버전에서 score와 순위가 재현된다.
- LOT 단위 train/evaluation 분리와 입력 누수 0건을 검증한다.
- score 분포, 상위 K 표본, threshold별 선택 비율을 artifact로 남긴다.
- 합성 평가 라벨은 corrected generator revision·seed·artifact hash와
  `label_source=SYNTHETIC_GENERATOR`를 기록하고 실제 GT와 구분한다.
- `action_threshold`는 versioned 합성 평가 protocol을 통과한 경우에만 `VERIFIED`로 배포한다. 검증 기준을
  통과하지 못했거나 artifact가 없으면 `UNVERIFIED`로 두고 조치 상향 gate를 비활성화한다.
- 규칙 알람과 metrology 결과의 연관성은 탐색 분석으로만 표현하고 정답률이라고 부르지 않는다.
- 모델 실행 전후 규칙 알람 집합이 불변이어야 한다.
- React는 Loading·Error·Empty·Success를 구분하고 Mock 데이터 없이 API 결과를 표시한다.

## 4. B — Knowledge Full-stack

### 4.1 담당 범위

- 신규 `master.cypher`의 38 nodes·81 relationships와 식별자·방향을 검증한다.
- Common이 destructive-safe loader로 적재한 raw graph를 B가 38/81·fingerprint로 검증하고 관계 Tool에 연결한다.
- 설비·챔버·공정·파라미터 관계를 parameterized Cypher로 조회한다.
- 기존 문서의 R02·R03·상하류·4단계 조치 표현을 신규 가이드와 대조해 corrected corpus를 만든다.
- 원본 문서 SHA-256, corrected 문서 SHA-256, 수정 근거, chunk 규칙, embedding model revision을 manifest로 남긴다.
- corrected corpus가 확정되기 전 구 임베딩을 Agent 근거로 사용하지 않는다.
- 관계 조회 Tool·문서 검색 Tool·API·React 관계/문서 화면을 구현한다.

### 4.2 Tool·API·화면

- Tool 1: `get_equipment_context(chamber_id)`
- Tool 2: `search_documents(query, model_code=None, top_k=4)`
- API: `GET /relations/chambers/{chamber_id}`, `GET /relations/equipment/{equipment_id}`, `POST /documents/search`, `GET /documents/{document_id}`
- 화면: 관계 그래프, 문서 검색, score·chunk·근거 내용, Agent 근거 deep link

문서 hit에는 최소 `document_id`, `chunk_id`, `title`, `model_code`, `score`, `content`, `corpus_revision`을 포함한다.
관계 결과의 `relation_id`는 방향을 보존한 canonical tuple `type|from_label:id|to_label:id`에서 결정론적으로 생성한다. `graph_revision`은 별도 provenance 필드로 반환하며, 같은 business edge의 `relation_id`는 revision이 바뀌어도 유지한다.

### 4.3 완료·평가 기준

- Neo4j 원본에서 독립적으로 계산한 관계 질의 fixture를 모두 통과한다.
- corrected corpus 이외의 revision이 로드되면 Tool은 `MODEL_NOT_READY`로 실패한다.
- 팀이 문서 내용에서 작성한 10문항 이상의 검색셋으로 Recall@4·MRR을 산출한다.
- Agent가 인용한 chunk·관계 ID가 해당 실행의 실제 Tool 결과에 존재해야 한다.
- Fault 정답이나 Generator의 내장 원인 정보를 검색 평가 정답으로 사용하지 않는다.
- React는 관계 없음·검색 0건·의존성 오류를 서로 다른 상태로 표시한다.

## 5. C — Agent·HITL·n8n Full-stack

### 5.1 담당 범위

- Trace·Summary·R03 알람을 `AlarmRef`로 resolve하고 `[팀 잠정] (lot_id, chamber_id)` incident로 집계한다.
- 실제 공정 경로는 각 AlarmRef에서 resolve한 `lot_id`+`wafer_no` 범위의 `lot_history`만 조회한다. 다른 wafer 근거는 해당 AlarmRef를 별도로 resolve해 조회한다.
- LangGraph State·Node·Edge, Tool 호출 예산, 실패·재시도·checkpoint 재개를 구현한다.
- A·B Tool 결과에서 근거를 수집하고 `predicted fault hypothesis`를 구조화해 생성한다.
- 조치 코드는 LLM이 아니라 순수 함수 `decide_action(base_policy, incident)`가 먼저 결정한다. 이 기본
  규칙은 A 모델·threshold를 기다리지 않고 구현·검증한다.
- anomaly 상향은 A의 VERIFIED threshold artifact가 준비된 뒤 별도 policy decorator로 주입한다.
- `EQP_HOLD`만 HITL로 중단하고 승인·반려 후 같은 thread를 재개한다.
- n8n을 통한 실제 이메일 발송과 MES Mock adapter를 채널별로 분리한다.
- Agent 실행·조치·승인·감사 API와 React 화면을 구현한다.

### 5.2 근거 기반 원인 가설 계약

```json
{
  "predicted_fault_code": "FOC | RFM | MFD | TMD | OTH",
  "hypothesis": "사람이 읽을 수 있는 원인 가설",
  "confidence": 0.0,
  "evidence": {
    "alarm_refs": [],
    "lot_hist_ids": [],
    "parameter_ids": [],
    "relation_ids": [],
    "graph_revision": null,
    "chunk_ids": [],
    "corpus_revision": null,
    "metrology_ids": []
  },
  "limitations": []
}
```

- `predicted_fault_code`는 관측 근거에서 생성한 가설이며 `reviewed_fault_code`와 구분한다.
- 근거 ID가 Tool 결과에 없으면 구조 검증을 통과하지 못한다.
- 근거 부족 시 `OTH` 또는 낮은 confidence와 limitation을 허용하며 확정 원인처럼 표현하지 않는다.
- `lot_history.fault_code`, Generator 주입 위치, 제공 action Mock은 프롬프트·Tool 입력에서 제외한다.

### 5.3 조치·HITL·전송

| 조건 | 조치 | 승인 | 외부 경로 |
|---|---|---|---|
| Summary OOC만 존재 | `MONITORING` | 자동 | `[팀 잠정]` 외부 호출 없음 |
| Trace OOS가 있고 R03 없음 | `WARNING` | 자동 | 이메일 |
| R03 존재 | `EQP_HOLD` | HITL | 승인요청 이메일, 승인 후 MES Mock |

- 표는 신규 자료를 따라 진행하는 `[팀 잠정]` 정책이며 멘토 답변 후 policy table만 교체한다.
- `base_policy`와 기본 5/2/3 fixture는 A 모델·synthetic 평가·threshold 검증과 독립된 P0이다. 따라서
  Agent 기본 incident→action 흐름은 A-3.6을 기다리지 않고 진행한다.
- `[팀 잠정]` anomaly gate는 A의 threshold artifact가 준비된 뒤 **2단계 policy decorator**로 적용한다.
  C는 원본 합성 label이나 WAFER별 `AnomalySignal`을 직접 판정하지 않고 A의
  `build_incident_model_signal(lot_hist_ids, action_policy_version)` 결과만 입력으로 받는다.
- `IncidentModelSignal`이 `enabled=true`, `status=READY`, coverage 100%, 동일 `model_version`·
  `score_method`·`display_threshold`·`action_threshold`·`threshold_version`, 허용된
  `action_policy_version`이고
  `incident_score >= action_threshold`이며 기본 결과가 Summary-only `MONITORING`인 경우에만
  `WARNING`으로 상향한다.
- gate는 조치를 하향하지 않고, 기본 `WARNING`·`EQP_HOLD`를 바꾸지 않으며, R03가 없는 incident를
  `EQP_HOLD`로 만들지 않는다. threshold 미검증·artifact 불일치·score NULL이면 기본 규칙을 그대로 쓴다.
- 현재 corrected data의 gate 비활성 기본 회귀 fixture는 incident 10개와
  `MONITORING 5 / WARNING 2 / EQP_HOLD 3`이며 실제 조치 정답으로 해석하지 않는다. gate 활성 결과는
  `threshold_version`별 delta로 기록하고 고정 Gold로 사용하지 않는다.
- 합성 라벨 artifact와 WAFER별 `AnomalySignal`은 C의 조치 판정 입력으로 전달하지 않는다. C는 A가
  반환한 공통 `IncidentModelSignal`과 member provenance만 snapshot하고 원본 합성 라벨을 조회할 수 없다.
- `action_history` 48건을 Runtime seed나 expected action으로 사용하지 않는다.
- 전송은 `(action_id, channel)`별 delivery 상태와 payload hash로 멱등 처리한다.
- `DeliveryStatus`는 `BLOCKED | WAITING | SENDING | SENT | FAILED | CANCELED | UNKNOWN`으로 고정한다.
- 응답 유실 또는 stale `SENDING`은 `UNKNOWN`으로 전이하고 자동 재발송하지 않는다. provider ID 대조 등 운영자 reconciliation 뒤에만 `FAILED` 또는 `SENT`로 정리한다.
- 승인 전 MES 또는 MES Mock 호출을 금지한다.
- n8n 이메일 실패와 MES Mock 실패는 서로 다른 delivery 결과로 기록한다.

EQP_HOLD는 action·approval transaction을 먼저 커밋하고 승인 요청 이메일을 1회 보낸 뒤 HITL에서 중단한다. 승인된 경우에만 MES Mock을 호출하며 반려된 경우 MES delivery를 CANCELED로 종료한다.

### 5.4 Tool·API·화면

- Tool: `send_action(action_id)` — 실행할 channel과 Agent 실행 문맥은 저장된 action·policy·delivery에서 파생하며 LLM 입력으로 받지 않는다. 반환은 `{ok, action_id, reason}` 골격과 채널별 `deliveries:[{channel,status,sent,duplicate}]`를 사용한다. 단일 top-level `sent`는 EMAIL·MES Mock의 부분 성공과 `UNKNOWN`을 표현할 수 없어 사용하지 않는다.
- API: `POST /agent/runs`의 body는 `{alarm:{source,alarm_id}}`이고, 그 외 `GET /agent/runs`, `GET /agent/runs/{run_id}`, `POST /agent/runs/{run_id}/retry`, `GET /approvals`, `POST /approvals/{approval_id}/decision`, `GET /actions`, `GET /actions/{action_id}`를 제공한다.
- 내부 전송 API: n8n write-back `POST /internal/actions/{action_id}/delivery`, 운영자 FAILED 재시도 `POST /actions/{action_id}/deliveries/{channel}/retry`
- 화면: Agent 실행 근거, 원인 가설, 조치 목록, 승인·반려, 채널별 전송 결과

### 5.5 완료·평가 기준

- 구조화 출력 최초 성공률·1회 교정 성공률을 기록한다.
- 모든 주장과 근거 ID의 존재 여부를 자동 검증한다.
- 같은 입력·model·prompt·temperature에서 조치 코드와 필수 근거가 일관되어야 한다.
- anomaly gate의 비활성·score NULL·경계값·상향·비하향·R03 없는 EQP_HOLD 금지 fixture를 전부
  통과해야 한다.
- base policy·action Service 테스트는 모델 없이 먼저 통과하고, gated policy 테스트와 최종 E2E만
  VERIFIED threshold artifact를 선행으로 요구한다.
- incident당 활성 실행·유효 조치 중복 0건을 검증한다.
- FAILED 수동 재실행은 `retry_of_run_id`를 가진 새 run으로 기록한다. action 생성 전 실패만 새 action을 허용하고, 생성 후 실패는 기존 action을 `REUSED`로 연결해 action·approval·delivery를 추가하지 않는다.
- `send_action(action_id)`는 action ID만 입력받고 run·channel을 저장 상태에서 파생하는 계약 테스트, 채널별 반환 계약, 채널별 멱등 테스트를 통과해야 한다.
- 자동조치, 승인, 반려, Tool 실패, 이메일 실패, MES Mock 실패 시나리오를 검증한다.
- `EQP_HOLD` 승인 전 MES 호출 0회, 승인 후 1회, 반려 후 0회여야 한다.
- 공개 Fault GT가 없으므로 실제 공정 Accuracy·Macro-F1·혼동행렬 합격선을 두지 않는다. 합성 라벨을
  사용한 수치는 `SYNTHETIC_GENERATOR` agreement와 threshold 검증 근거로만 별도 표시한다.
- 팀 또는 멘토 블라인드 리뷰 시 표본·평가자·rubric·label source를 artifact에 기록한다.

## 6. D — Analytics Full-stack

### 6.1 담당 범위

- 신규 base schema와 Runtime schema를 pool별로 introspection해 allowlist를 만든다.
- 자연어 질문을 SQL·metric·grouping·chart plan으로 구조화한다.
- `sqlglot`으로 단일 SELECT, 테이블·컬럼 allowlist, 위험 함수, 시스템 카탈로그, CTE·서브쿼리를 검증한다.
- 운영 Runtime DB와 immutable SQL 평가 snapshot을 같은 공용 서버의 논리 DB로 분리한다.
- 신규 corrected DB에서 Text2SQL 질문·기대 SQL·기대 결과를 다시 작성한다.
- 자연어 분석·감사로그 API와 React 화면을 실제 데이터에 연결한다.

### 6.2 Tool·API·화면

- Tool: `generate_analysis_plan(question)` — 초기 범위에서는 LangGraph 호출 예산과 분리
- API: `POST /analytics/query`, `POST /analytics/validate`, `GET /analytics/history`, `GET /analytics/evaluations`, `GET /audit-logs`
- 화면: 질문, 생성 SQL, 표·metric·차트, 질의 이력, 평가 결과, 감사로그

### 6.3 완료·평가 기준

- Fault 정답을 요구하지 않는 신규 질문셋을 corrected evaluation DB에서 확정한다.
- 질문은 `kosa_text2sql`의 실제 evaluation allowlist인 base/reference·R03·document·Mock action·`nl_query_log` 범위로 제한하고 runtime·audit table을 묻지 않는다.
- 질문마다 기대 SQL 의미, 결과 집합, 정렬 여부, 수치 오차, chart type·x·y를 기록한다.
- 단일 SELECT·쓰기·다중 문장·비허용 테이블/컬럼·위험 함수·시스템 카탈로그 방어를 검증한다.
- 운영/평가 pool의 schema context와 로그가 서로 섞이지 않아야 한다.
- SQL 실행 정확도, 방어 통과율, metric·chart 호환성을 artifact로 남긴다.
- 자연어 분석 화면이 Backend 확정 plan을 다시 해석하지 않고 그대로 렌더링한다.

## 7. 공통 — Bootstrap·Runtime·통합

4명이 공동 책임지고 방대혁이 통합 진행을 관리한다. 공통 변경은 최소 1명의 리뷰를 받는다.

### 7.1 신규 데이터 bootstrap

- 신규 ZIP 해시·파일 목록·행 수·컬럼 manifest 작성
- 원본 ZIP·Generator 불변 검증과 corrected generator·corrected copy 생성
- `dim_parameter` 8행 overlay, Trace `seq_no` 0~5 보정, Summary 알람 시각 보정
- 계측 시각은 공식 기준이 확정되기 전 NULL 유지
- 합성 평가 라벨은 corrected generator에서 별도 artifact로 생성하고 manifest에 generator revision·seed·
  hash·`label_source=SYNTHETIC_GENERATOR`·`production_ground_truth_available=false`·
  `usage_scope=EVALUATION_ONLY`를 기록한다. 세 PostgreSQL DB와
  Runtime·Agent·RAG 적재 경로에는 포함하지 않는다.
- 공용 PostgreSQL의 `kosa_agent`·`kosa_agent_e2e` runtime과 `kosa_text2sql` evaluation 논리 DB에 base schema를 먼저 생성한다.
- source/corrected file manifest와 runtime/evaluation bootstrap profile manifest를 분리하고, `nl_query_log` 같은 누적 테이블은 immutable content hash에서 제외해 별도 검증한다.
- Common이 raw `master.cypher`를 destructive-safe loader로 적재하고, B가 38/81·fingerprint를 검증해 관계 Tool에 연결한다. corrected RAG는 `001_reference_extensions.sql` 적용 후 B가 revision별 stage·검증·원자 swap·rollback으로 적재한다.

### 7.2 Profile별 reference·Runtime CREATE migration

- `001_reference_extensions.sql`은 `kosa_agent`·`kosa_agent_e2e`·`kosa_text2sql`에 R03·document corpus·`nl_query_log`·통합 view를 공통 생성한다.
- `001` 성공 후 세 DB에 corrected base data를 적재하되 `action_history=0`으로 두고 PK/FK·행 수·reference output을 검증한다.
- corrected base data 적재 후 `kosa_text2sql`에만 제공 action 48건을 추가하고, DB 컬럼이 아닌 profile metadata `fixture_type=MOCK`으로 표시해 Text2SQL·화면 계약 회귀에만 사용한다.
- `002_agent_runtime_clean.sql`은 `001`과 corrected base data 적재 성공 후 runtime 2개 DB에만 Agent·승인·조치·감사·delivery 구조를 생성하며 action 0건 guard를 통과해야 한다.
- 기존 구 DB의 ALTER·backfill 전용 migration을 재사용하지 않고 evaluation DB에 `002`를 적용하지 않는다.
- `AlarmRef`, incident 실행 연결, 승인·조치·감사·Tool 호출·channel별 delivery를 지원한다.
- 부분 고유 제약과 조건부 갱신으로 중복 실행·중복 승인·중복 전송을 차단한다.
- Checkpoint는 별도 1회 초기화하고 애플리케이션 시작 시 자동 setup하지 않는다.

### 7.3 공용 DB 안전 원칙

- 개인 로컬 DB는 구성하지 않는다.
- 모든 write 작업은 host·database allowlist와 팀 공유 절차를 통과한다.
- source 검증은 read-only transaction과 timeout을 사용한다.
- reset은 `kosa_agent_e2e`의 Runtime 실행 데이터에만 허용하고 `kosa_agent`·`kosa_text2sql` 대상 요청은 거부하며 source·reference fixture는 보존한다.
- E2E 전후 manifest를 비교하고 비밀정보·전체 DSN을 출력하지 않는다.
- `/health/ready`는 PostgreSQL·Neo4j·n8n을 병렬 timeout으로 검사하고 Neo4j 38/81 success marker와 ACTIVE `corpus_revision`을 확인한다.

## 8. 역할 간 계약과 선행관계

| 제공자 | 소비자 | 계약 |
|---|---|---|
| Common | 전원 | corrected source manifest, 합성 평가 artifact 경계, clean DB, Runtime schema, Enum·DTO |
| A | C·D | AlarmRef, FDC summary, 규칙 알람, nullable anomaly score·threshold 검증 상태; 합성 라벨 원문 제외 |
| B | C | relation IDs, document chunk IDs, corpus revision |
| C | A·D | ApprovalService, action·agent·audit 상태 |
| D | 전원 | 신규 schema allowlist 검증, 평가 snapshot·질의 artifact |

1. Common source correction과 계약 동결이 모든 역할의 G0이다.
2. A 규칙 알람과 B corrected corpus가 준비되면 C의 실제 evidence graph를 연결한다.
3. C의 approval/action API가 준비되면 A 대시보드와 D 감사 화면을 연결한다.
4. 각 역할은 의존 기능이 준비되기 전 versioned contract fixture로 병렬 개발하되 최종 완료는 실제 통합으로 판정한다.

## 9. 우선순위

### P0 — 반드시 먼저

- 신규 source baseline·corrected copy·manifest
- 구 데이터의 Fault·알람·조치 고정 정답 기준 차단
- 공통 Enum·AlarmRef·3단계 action·channel delivery 계약
- clean base Runtime CREATE migration
- A의 결정론적 계산, B의 corpus 정정, D의 신규 schema allowlist
- C의 모델 비의존 base action policy·5/2/3 fixture

### P1 — 프로젝트 필수

- 비지도 score, 라벨 없는 평가, 평가 전용 synthetic gold와 2단계 `action_threshold` policy decorator
- 관계·RAG Tool과 provenance
- LangGraph 근거 기반 가설·HITL·이메일·MES Mock
- Text2SQL·통계·차트·감사
- 각 역할의 FastAPI·React 실연동과 통합 E2E

### P2 — 핵심 완료 후

- 전문가 라벨셋 기반 분류 성능 평가
- Level 3 ReAct, MCP wrapping, hybrid retrieval, 대체 비지도 모델 비교

## 10. 역할 확정과 협업 규칙

- A 신동원, B 강연권, C 방대혁, D 천승현 배정은 유지한다.
- 담당 영역 밖 파일 변경은 사전 공유하고 공통 계약 변경은 같은 PR에서 문서·테스트를 동기화한다.
- 각 역할은 자기 도메인의 요구사항·설계·API·테스트·Trouble Shooting을 작성한다.
- 최종 요구사항정의서·시스템설계서·테스트 결과서·실행 가이드는 4명이 공동 검토한다.
- `ground_truth_available`, `label_source`, `production_ground_truth_available`, `usage_scope`, source/corpus/model/prompt/
  generator revision을 평가 artifact에 기록한다.
- 멘토 확인 항목은 구현 코드에 하드코딩하지 않고 policy·config·adapter로 분리한다.

## 11. v9.6 대비 변경 요약

- 기존 고정 Fault 분류와 supervised 성능 평가 삭제
- 구 데이터의 고정 조치와 제공 action 48건의 Runtime 정답 사용 삭제
- A를 실제 Fault supervised 성능 평가에서 비지도 안정성·누수·분포 평가로 전환하고,
  corrected generator 기반 합성 평가 gold를 별도 artifact로 제한
- C의 Fault 분류를 evidence-based predicted hypothesis로 전환
- 조치를 4단계에서 3단계로 전환하고, 검증된 anomaly `action_threshold`의 제한적
  `MONITORING → WARNING` 상향 gate와 이메일·MES Mock channel별 delivery를 분리
- B에 RAG 문서 정정·provenance·재임베딩 책임 추가
- D에 신규 schema allowlist·질문셋 재작성·공용 논리 DB 분리 책임 추가
- 기존 완료 기반구축 전제를 폐기하고 corrected source bootstrap과 clean Runtime migration을 공동 선행 작업으로 재편
- 기존 고정 ID·날짜·수치 수용 기준을 신규 reference output·상태 전이·근거 무결성 기준으로 교체
