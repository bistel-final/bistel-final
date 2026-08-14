# 05. Agent 워크플로

> [!CAUTION]
> **사용 중지 — 아래 본문은 v1.9/v1.10/v9.6 기준의 구 이력이며 구현 근거로 사용하면 안 됩니다.**
> v2 요약 문서가 재생성되기 전에는 `docs/specifications/요구사항정의서_v2_0_작업본.md`,
> `docs/specifications/시스템설계서_v2_0_작업본.md`,
> `docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md`와
> `docs/planning/Task분해_WBS_v4_작업본.md`의 해당 `V4-*` Task만 사용하십시오.
> 아래 본문은 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-11

조치 결정표·상향·하향 규칙은 `02-domain-rules.md` 4절, Tool 예산은 `01-project-rules.md` 5절에 있다.
이 문서는 그래프 구조·트랜잭션·복구를 다룬다.

---

## 1. incident와 대표 알람

```
incident key    (lot_id, chamber_id)
포함 알람       해당 key의 fdc_alarm 전체
대표 알람       occurred_at ASC, alarm_id ASC 첫 행
```

| 저장 위치 | 값 |
|---|---|
| `agent_run.alarm_id` | 대표 alarm_id |
| `agent_run.requested_alarm_id` | 수동 실행 시 사용자가 준 alarm_id (자동 배치는 대표) |
| `action_history.trigger_alarm_lot_hist_id` | 대표 알람의 lot_hist_id |
| `agent_run_alarm` + `evidence_json.incident.alarm_ids` | 전체 alarm_ids (양쪽 보존) |

**R03 알람을 무조건 대표로 고르지 않는다.** 위 규칙이 배포 fixture 10건의 `trigger_alarm_lot_hist_id`와 일치한다.

---

## 2. Agent State

```python
class AgentState(TypedDict):
    agent_run_id: str
    thread_id: str
    requested_alarm_id: str
    representative_alarm_id: str
    alarm_ids: list[str]
    lot_hist_ids: list[str]
    lot_id: str
    chamber_id: str
    equipment_id: str

    representative_fdc_evidence: FdcSummaryToolResult | None
    incident_alarm_evidence: IncidentAlarmEvidence
    equipment_context: EquipmentContextToolResult | None
    document_hits: list[DocumentHit]
    batch_incident_plans: list[BatchIncidentPlan]
    upstream_evidence: list[UpstreamEvidence]

    fault_code: str | None
    cause_summary: str | None
    confidence: float | None
    recommended_action: str | None
    action_reason: str | None
    severity: str | None
    approval_required: bool
    action_id: str | None
    approval_id: str | None
    approval_decision: Literal["APPROVE", "REJECT"] | None

    tool_call_count: int
    retry_counts: dict[str, int]
    active_latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    errors: list[dict]
```

**Checkpoint에 비밀번호·API 키·DB URL을 저장하지 않는다.**

원본 `agent_run`에 `failure_stage` 컬럼이 없다. 문서의 `failure_stage=...` 표기는
`evidence_json.runtime.failure_stage`와 `runtime.errors[]`에 저장한다는 뜻이다.
`AGENT_RUN_FAILED.after_json`에도 노출 가능한 요약 사유만 남기고 **DSN·stack trace는 저장하지 않는다.**

---

## 3. Node와 Edge

```
load_incident → get_fdc_summary → get_equipment_context → search_documents
              → classify_fault → decide_action
                                   ├─ 조치 생략 ─────────────→ finalize
                                   └─ persist_action
                                        ├─ EQP_HOLD → approval_gate(interrupt)
                                        │                ├─ 승인 → send_action → finalize
                                        │                └─ 반려 ───────────→ finalize
                                        └─ 자동 조치 → send_action → finalize

  실패 상한 도달 시 fail_run
```

| Node | 책임 | LLM |
|---|---|---|
| `load_incident` | incident 조회, 대표·전체 알람과 고유 OOS/OOC·R03 집계 확정 | 아니오 |
| `get_fdc_summary` | 대표 `lot_hist_id`의 A Tool 호출·로그 | 아니오 |
| `get_equipment_context` | B Tool 호출·로그 | 아니오 |
| `search_documents` | B Tool 호출·로그 | 아니오 |
| `classify_fault` | FOC·RFM·MFD·TMD 구조화 분류와 설명 | **예** |
| `decide_action` | 확정 결정표·상향·하향·충돌 우선순위 | 아니오 |
| `persist_action` | 유효 조치 1건 생성 또는 FAILED action 재사용 | 아니오 |
| `approval_gate` | 요청 생성 후 checkpoint interrupt | 아니오 |
| `send_action` | C Tool 호출, n8n 멱등 전송 | 아니오 |
| `finalize` / `fail_run` | 실행·감사·성능 기록 마감 | 아니오 |

**LLM은 `classify_fault` 한 곳에서만 쓴다.**

incident 전체 판정 근거는 `load_incident`가 모든 `fdc_alarm` 행을 Repository로 읽어 집계한다.
`get_fdc_summary` Tool은 **대표 알람의 `lot_hist_id`에 정확히 1회**만 호출한다.
이 구분으로 알람이 여러 WAFER에 걸친 incident도 전체 계수하면서 Tool 호출 폭주를 피한다.
추가 WAFER 상세가 꼭 필요하면 Level 2에서 최대 1회 더 호출한다.

---

## 4. 자율성 Level

| Level | 동작 |
|---|---|
| 1 | Node를 고정 순서로 호출. 실패 시 정해진 재시도만 |
| 2 | 근거 충분성 조건에 따라 관계·문서 Tool 호출·재시도 분기 (**기본값**) |
| 3 | ReAct — 도전 과제. 초기 그래프에 연결하지 않는다 |

모든 Level이 **같은 State와 같은 Tool 함수**를 쓴다. 환경변수는 그래프 빌드 시 경로만 고른다.
`decide_action`과 승인 게이트는 Level과 무관하게 동일하다.

| 판정 | 조건 | 다음 |
|---|---|---|
| FDC 근거 부족 | 대표 요약 실패 또는 incident 알람 집계 없음 | 허용 재시도 후 `fail_run` |
| 관계 근거 필요 | 연쇄 이상 판단 또는 장비 문맥 없음 | `get_equipment_context` 1회 |
| 문서 근거 부족 | fault 후보를 뒷받침할 hit 없음 | 서로 다른 질의로 추가 호출, 전체 최대 3회 |
| 근거 충분 | 규칙 집계 + 필요한 근거 확보 | `classify_fault` 진행 |

근거 충분성은 **LLM 자유판단이 아니라** 위 조건과 hit 개수·score 유효성 규칙으로 정한다.

---

## 5. 분류 출력

```json
{"fault_code": "FOC",
 "cause_summary": "...",
 "confidence": 0.91,
 "evidence_ids": ["ALM-...", "CHK-..."]}
```

- `fault_code` ∈ `FOC | RFM | MFD | TMD`, `confidence` 0~1
- 입력: 알람·요약·관계·문서·계측 근거
- **입력 금지: `lot_history.fault_code`**
- 스키마 검증 실패는 `CLASSIFICATION_OUTPUT_RETRY=1`로 최대 1회 교정 요청. 두 번째도 실패하면 `FAILED` + `failure_stage='CLASSIFICATION_OUTPUT'`
- **임의 기본 fault로 대체하지 않는다**
- LLM 교정 요청은 Tool retry 수에 포함하지 않지만 토큰·지연시간에는 포함한다

---

## 6. 자동 배치 2단계 계산

처리 순서와 무관하게 연쇄 근거를 제공하기 위한 구조다.

```
1단계  미처리 알람 전체 조회 → (lot_id, chamber_id) 그룹화
       → 각 그룹의 기본 조치와 반복 LOT·계측·하류 전파 조건 계산
       → BatchIncidentPlan 메모리 객체 → 상하류 incident 연결

2단계  실제 실행 순서와 관계없이 1단계 전체 계획을 각 State에 제공
       → ALM-0031 분석은 상류 PHOTO incident의 계획된 LOT_HOLD를 근거로 사용
       → source="batch_plan" + 상류 incident key 기록
       → 최종 계획과 사용한 근거를 evidence_json 에 저장
```

이 구조로 **51건의 처리 순서를 섞어도 ALM-0031 결과가 동일**하게 유지된다.

실행 진입점은 `backend/scripts/run_pending_incidents.py --once` 관리 명령 1회 호출이다.
공개 REST API와 React에 전체 배치 엔드포인트·주기 실행 버튼을 만들지 않는다. 스케줄러·상시 polling도 초기 범위 밖이다.

---

## 7. 동시 실행 방지

```
1. (lot_id, chamber_id) → hashtextextended(...) 로 64-bit 키 변환
2. SELECT pg_advisory_xact_lock(...)
3. 같은 incident의 과거 agent_run_alarm 이력과 최신 agent_run.status 재조회
4. 자동 배치: 이력이 하나라도 있으면 상태와 무관하게 건너뜀
5. 수동 실행: 아래 상태표
6. agent_run 과 모든 agent_run_alarm 을 같은 트랜잭션에서 생성
   이때 설정 모델명을 llm_model 에 먼저 기록 (LLM 호출 전 실패에도 남게)
7. 커밋으로 lock 해제
```

| 최신 실행 | 수동 실행 |
|---|---|
| 이력 없음 | 신규 허용 |
| RUNNING·WAITING_APPROVAL | 409 `INCIDENT_ALREADY_RUNNING` |
| COMPLETED | 409 `INCIDENT_ALREADY_PROCESSED` |
| FAILED | 명시적 수동 재실행 허용 |

조치 생성 시에도 같은 advisory lock을 먼저 얻고 유효 `action_history`를 재조회한다.

---

## 8. 승인 트랜잭션

### 조치 생성

```
1. incident advisory lock
2. 유효 action_history 재조회
3. 없으면 1건 생성하고 `created_by_agent_run_id`에 현재 실행을 최초 1회 기록
4. 자동:     AUTO/WAITING, approved_by='system', approved_at=created_at
5. EQP_HOLD: PENDING/WAITING, 승인자 NULL
6. EQP_HOLD 는 action + approval_request(action_id 포함)를 같은 트랜잭션에서
7. 감사로그 후 커밋
8. EQP_HOLD 는 커밋 이후 interrupt
```

FAILED 재실행에서 유효 action이 이미 있으면 새 행을 만들지 않고 기존 `created_by_agent_run_id`도 갱신하지 않는다. 이 컬럼은 최신 처리 실행이 아니라 조치 생성 provenance다.
자동조치 `AUTO/FAILED`와 승인 완료 `APPROVED/FAILED`는 같은 action_id로 **approval_gate를 우회**해 곧바로 `send_action`으로 간다. 신규 `PENDING/WAITING`만 approval_request를 만들고 interrupt한다.

### 승인·반려 결정

```
1. approval_request 를 FOR UPDATE 로 잠금
2. PENDING 이 아니면 409 로 종료, 아무 행도 변경하지 않음
3. approval_request.action_id 로 action 을 FOR UPDATE 조회
4. APPROVE: approval APPROVED, action APPROVED/WAITING, 승인자·시각 동기화
5. REJECT:  approval REJECTED, action REJECTED/CANCELED, 승인자 NULL 유지
6. agent_run WAITING_APPROVAL → RUNNING, 감사로그 추가
7. 커밋 후 같은 thread_id 로 재개
```

커밋 후 재개 전에 `graph.update_state()`로 `approval_id`·`action_id`·`approval_decision`을 주입한다.
**승인 결과를 대화 메시지 문자열로 다시 해석하지 않고 구조화 필드로만 분기한다.**

---

## 9. 전송 멱등성

```
1. action 을 FOR UPDATE 조회하고 저장된 효과 필드로 canonical payload·request_hash 계산
   (State 의 조치 필드는 사용하지 않는다)
2. SENT 면 n8n 호출 없이 {ok:true, sent:true}
3. CANCELED 이거나 EQP_HOLD 가 APPROVED 가 아니면 호출 없이 {ok:false}
4. FAILED 재시도면 action_delivery 를 먼저 조회
     같은 hash 있음 → SENT 로 복구, 재호출 안 함
     다른 hash      → 409 IDEMPOTENCY_CONFLICT
     없음           → 진행
5. WAITING|FAILED → SENDING 조건부 UPDATE 후 커밋
6. n8n 에 효과 필드 + request_hash + 추적용 agent_run_id POST
7. n8n 은 요청 필드로 hash 재계산해 대조. 일치할 때만 action_delivery INSERT
   (n8n 은 action_history 를 조회하지 않는다)
8. 성공·중복 성공 → SENT, sent_at = action_delivery.delivered_at
9. 4xx/5xx → FAILED + Tool ERROR + ACTION_SEND_FAILED. 한도 남으면 4번부터 재시도
10. timeout → FAILED + Tool TIMEOUT. 신규 action 만들지 않고 4번부터 재시도
11. 상한 소진 후에만 agent_run=FAILED, failure_stage='ACTION_SEND'
```

`request_hash` 대상 필드는 `action_id`·`lot_id`·`equipment_id`·`chamber_id`·`action_code`·`send_channel`·`reason`.
NFC 정규화 + key 오름차순 + 공백 없음 + `ensure_ascii=false` canonical JSON의 SHA-256이다.
**추적용 `agent_run_id`는 payload에 넣되 hash에서 제외한다.**

---

## 10. 복구 스크립트 (자동 스케줄러 없음)

| 스크립트 | 담당 상황 | 임계 |
|---|---|---|
| `resume_approved_runs.py` | 승인 커밋 성공 후 그래프 재개 전 프로세스 종료 | — |
| `recover_stale_runs.py` | RUNNING 고착 (Node 사이 종료) | `AGENT_RUN_STALE_SEC=900` |
| `recover_stale_sending.py` | SENDING 고착 (응답 기록 전 종료) | `ACTION_SENDING_STALE_SEC=60` |

공통 원칙이다.

- **새 `agent_run`·`action`·`approval`을 만들지 않는다.** 기존 것만 전이시킨다
- incident advisory lock을 얻고 상태를 재조회한 뒤 진행한다
- 반복 실행해도 터미널 상태는 건너뛴다
- `send_action` 호출은 반드시 `ToolCallBudgetService`를 거친다. 기존 `agent_tool_call`에서 예산을 복원하고, 잔여 0이면 외부 호출 없이 FAILED로 마감한다
- SENDING 고착은 `recover_stale_sending.py`가 전담한다. `recover_stale_runs.py`는 action이 SENDING이면 넘긴다

---

## 11. 지연시간과 토큰

`latency_ms`는 **사람 승인 대기를 제외한 활성 구간의 합**이다.

```
최초 실행    graph 진입 직전 perf_counter() 시작
interrupt 전 첫 구간을 누적해 checkpoint 에 active_latency_ms 저장
승인 재개    새 perf_counter() 시작
종료·실패    마지막 구간을 더해 정수 ms 저장
```

- Tool별 시간은 각각 `agent_tool_call`에 저장한다. `agent_run` 시간에 Tool 시간을 다시 더하지 않는다
- 토큰은 모든 LLM 응답의 제공자 usage를 합산한다. 제공자가 주지 않으면 NULL
- **성공·실패 모두 `ended_at`·`llm_model`·`latency_ms`를 기록한다**

---

## 12. 감사 이벤트 9종 (고정)

```
DETECTION_COMPLETED   AGENT_RUN_STARTED   CLASSIFICATION_COMPLETED
APPROVAL_REQUESTED    APPROVAL_DECIDED
ACTION_SENT           ACTION_SEND_FAILED
AGENT_RUN_COMPLETED   AGENT_RUN_FAILED
```

| 이벤트 | entity_type / id | after_json 핵심 |
|---|---|---|
| DETECTION_COMPLETED | LOT_HIST / lot_hist_id | 규칙 결과·anomaly score |
| AGENT_RUN_STARTED | AGENT_RUN / run_id | incident, alarm_ids, level |
| CLASSIFICATION_COMPLETED | AGENT_RUN / run_id | fault_code, confidence, recommended_action, skip reason |
| APPROVAL_REQUESTED | APPROVAL / approval_id | action_id, action_code |
| APPROVAL_DECIDED | APPROVAL / approval_id | decision, decided_by, comment |
| ACTION_SENT | ACTION / action_id | channel, sent_at |
| ACTION_SEND_FAILED | ACTION / action_id | reason, retry count |
| AGENT_RUN_COMPLETED | AGENT_RUN / run_id | final status, latency_ms |
| AGENT_RUN_FAILED | AGENT_RUN / run_id | failure stage, reason, latency_ms |

**새 이벤트를 추가하지 않는다.** 조치를 생략해도 `ACTION_SKIPPED` 같은 것을 만들지 말고
`CLASSIFICATION_COMPLETED.after_json`과 `AGENT_RUN_COMPLETED.detail`에 사유를 남긴다.

`audit_log` 작성 실패 시 업무 트랜잭션도 롤백하는 것이 원칙이다.
n8n 전송처럼 외부 효과 후 기록되는 이벤트만 후속 트랜잭션에서 기록한다.

---

## 원본 절

```
설계 4.1~4.4  incident·동시성·2단계 배치·실행 위치
설계 7.1  State      설계 7.2  Node·Edge      설계 7.3  자율성 Level
설계 7.4·7.4.1  Tool 상한·예산 배분
설계 7.5  조치 생성·승인 트랜잭션·복구      설계 7.6  Fault 분류·오프라인 평가
설계 7.7  decide_action      설계 7.8  전송 멱등성·SENDING 복구
설계 7.9  지연시간·토큰      설계 7.10  Level 1·2 평가 프로토콜
설계 11장  감사로그
요구사항 5.3 FR-C-01~15 · 8.2 · 8.3 · 부록 B
```
