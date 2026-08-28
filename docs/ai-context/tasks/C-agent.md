# C — Agent · HITL · n8n · Kafka

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-28
> 담당: 방대혁 · 모듈 `backend/app/agent/` · `frontend/src/features/agent/` · `deploy/n8n/`

LangGraph Level 1·2, 원인 가설, 3단계 규칙 조치, HITL 승인, n8n SMTP, Kafka MES Mock,
`send_action`과 화면 3 조립을 책임진다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-C-01 | Source-aware Agent | 필수 |
| FR-C-02 | 자율성 Level 1·2 | 필수 |
| FR-C-03 | `decide_action` | 필수 |
| FR-C-04 | HITL·checkpoint 재개 | 필수 |
| FR-C-05 | 승인 API | 필수 |
| FR-C-06 | 채널 전송·`send_action` | 필수 |
| FR-C-07 | 실행 기록·원인 가설 | 필수 |
| FR-C-08 | Tool 예산 | 필수 |
| FR-C-09 | 배치·재실행 | 필수 |
| FR-C-10 | 실제 routing·공정 연쇄 근거 | 필수 |
| FR-C-12 | n8n·Kafka workflow | 필수 |
| FR-C-13 | Agent 화면 | 필수 |
| FR-C-14 | 실행·action 중복 방지 | 필수 |
| FR-C-15 | Agent 평가 | 필수 |
| FR-C-11 | Level 3 ReAct | 도전·P2 |

관련 비기능 요구사항은 NFR-02~05, NFR-09~11, NFR-17~20이다.

## Task (WBS v5 정본)

| ID | P | 완료 기준 | FR/NFR | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-C-0.1 | P0 | Runtime Repository. 완료: 설계 §3.4의 9 table Repository와 ID 계약을 만들고 Common append-only helper로 같은 업무 transaction 안에 감사를 기록한다. Tool 결과와 분리된 실행 횟수·상태·`latency_ms`를 `agent_tool_call` metadata로 저장하며 `action`·`severity`는 항상 함께 채운다 | FR-C-07, NFR-05, NFR-09 | V5-CM-3.3, V5-CM-4.2 | 4.0h |
| V5-C-0.2 | P0 | thread·checkpoint 계약. 완료: `agent_run_id`와 독립인 thread UUID, 저장·interrupt·동일 thread 재개 fixture를 만든다 | FR-C-04 | V5-CM-3.4, V5-C-0.1 | 1.5h |
| V5-C-1.1 | P0 | incident 해석. 완료: source-aware `AlarmRef`를 `(lot_id, chamber_id)`로 묶고 대표 알람을 `occurred_at ASC, source priority, alarm_id ASC`로 결정한다 | FR-C-01 | V5-A-1.5, V5-C-0.1 | 2.0h |
| V5-C-1.2 | P0 | 실제 routing 결합. 완료: `lot_history` LOT/WAFER routing과 B `GraphService`·`get_equipment_context`의 Process Step 인접을 결합한다. public graph API를 내부 Tool 대신 호출하지 않으며 불일치는 `route_consistency=false`로 보존한다 | FR-C-10 | V5-C-1.1, V5-B-3.2 | 2.0h |
| V5-C-1.3 | P0 | 중복 실행 방지. 완료: 동일 incident 동시 요청에서 활성 run 1개만 만들고 처리 완료 incident는 재선택하지 않는다 | FR-C-09, FR-C-14 | V5-C-1.1 | 1.5h |
| V5-C-2.1 | P0 | LangGraph Level 1·2 골격. 완료: load_incident→A/B Tool 수집→가설→규칙 조치→저장→delivery/HITL→finalize를 같은 State·Tool로 구성하고 설정만으로 고정 흐름/조건 분기를 전환한다. fixture에서 Level별 완료와 `agent_tool_call` 기반 호출 수 차이를 고정한다 | FR-C-02 | V5-C-1.2, V5-A-3.2-1, V5-B-2.2, V5-B-3.2 | 6.5h |
| V5-C-2.2 | P0 | Tool 예산. 완료: 총 8회·동일 Tool 재시도 상한·전송 예약을 HITL 중단·재개 전후 누적 적용하고 checkpoint·DB에서 복원한다. 읽기 Tool은 caller 대기 soft 8초를 적용하고 worker 포화·queue 대기를 실제 Tool timeout과 별도 code로 구분한다. hard 상한의 실제 집행·종료 검증은 `V5-CM-4.8`에 귀속한다. finalize 실패 예약 sentinel은 자동 회수·삭제·차감하지 않고 row를 보존해 예산에 계속 포함하며, hard 종료가 확보된 구간의 회수 가능성만 `V5-CM-4.8`에서 재평가한다 | FR-C-08, NFR-03 | V5-C-2.1 | 4.0h |
| V5-C-2.3 | P0 | 원인 가설. 완료: `FOC\|RFM\|MFD\|TMD\|OTH` 구조화 출력과 실제 AlarmRef·chunk·relation 근거 인용을 생성하고 실제 LLM input/output token·model·prompt version을 run·prediction에 기록한다. `NRM`과 합성 라벨·Generator FAULTS는 query·State·Tool·prompt에 넣지 않는다 | FR-C-07, FR-C-15, NFR-19 | V5-C-2.2 | 4.0h |
| V5-C-3.1 | P0 | `decide_action`. 완료: SUMMARY OOC-only → MONITORING, TRACE OOS → WARNING, strict R03 → EQP_HOLD의 3단계 순수 규칙 함수를 만든다. LLM·score·metrology를 입력에서 제외한다 | FR-C-03 | V5-C-2.3 | 2.0h |
| V5-C-3.2 | P0 | action 생성 transaction. 완료: incident advisory lock→run row lock 아래 `action_history`·CREATED/REUSED link·approval·delivery와 policy provenance를 한 트랜잭션에서 만들고 incident당 유효 action 1건을 보장한다. `request_hash`는 stable identity의 raw 64 hex이며 같은 run 재호출과 자동 조치의 FAILED retry를 멱등 처리한다. EQP_HOLD는 approval의 상태·소유 run을 검증하며 새 run에 기존 approval을 재사용하지 않는다 | FR-C-14 | V5-C-3.1 | 4.0h |
| V5-C-3.3 | P0 | HITL 승인. 완료: EQP_HOLD에서 그래프를 중단하고 승인·반려 후 동일 thread를 재개한다. 조건부 갱신으로 중복 결정을 409로 막는다 | FR-C-04, FR-C-05 | V5-C-3.2, V5-C-0.2 | 2.0h |
| V5-C-4.1 | P0 | n8n workflow 제작. 완료: delivery·write-back용 `WF2-notify-email`·`WF3-mes-hold`·`WF4-result-writeback` JSON 3종만 `deploy/n8n/`에 둔다. 실행 시작은 source-aware `POST /agent/runs`가 소유하며 source-less `WF1-alarm-to-agent`는 만들지 않는다. raw body HMAC·timestamp 검증, `request_hash` 멱등성, Kafka key=`action_id`, channel=`MES_MOCK` 계약을 workflow fixture로 고정하고 secret·credential은 포함하지 않는다 | FR-C-12, NFR-02, NFR-20 | V5-C-3.3 | 2.0h |
| V5-C-4.2 | P0 | **공용 n8n import·연결**. 완료: workflow 3종을 학원 공용 n8n에 import하고 credential·webhook URL은 공용 환경에서 주입한다. Backend callback·SMTP·Kafka 연결 smoke와 workflow 활성 상태를 검증하며 팀 compose의 n8n 컨테이너는 0건이다 | FR-C-12, FR-I-04, NFR-02 | V5-C-4.1 | 1.0h |
| V5-C-4.3 | P0 | SMTP delivery. 완료: WARNING 이메일 1회, EQP_HOLD 승인요청 이메일 1회를 서명 webhook으로 발송하고 실패·timeout을 기록한다 | FR-C-06, FR-C-12 | V5-C-4.2 | 2.0h |
| V5-C-4.4 | P0 | write-back callback. 완료: `POST /internal/actions/{action_id}/delivery`가 timestamp·HMAC 서명·300초 replay window를 검증하고 channel별 상태를 갱신한다 | FR-C-06 | V5-C-4.3 | 1.5h |
| V5-C-4.5 | P0 | Kafka MES Mock. 완료: 승인된 EQP_HOLD만 n8n Kafka Producer로 `fdc.actions`에 발행하고, MES Mock consumer 결과를 `fdc.actions.result` → write-back으로 반영한다. 승인 전 발행 0건·반려 시 발행 0건을 음성 테스트로 고정한다 | FR-C-06, FR-C-12 | V5-C-4.4 | 2.0h |
| V5-C-4.6 | P0 | 채널 멱등성. 완료: EMAIL·MES_MOCK 각각 `(action_id, channel)` 외부 효과 최대 1회, 동일 hash 재수신 동일 결과, 다른 hash 409, 응답 유실 `UNKNOWN`·자동 재발송 0회를 n8n·Kafka 경로에서 검증한다 | FR-C-06, NFR-20 | V5-C-4.4, V5-C-4.5 | 1.5h |
| V5-C-4.6-1 | P0 | `send_action(action_id)` Tool. 완료: 단일 `action_id`의 저장된 delivery plan·승인 상태를 검증해 실행 가능한 EMAIL·MES_MOCK adapter만 호출하고 조치를 재결정하지 않는다. 예약은 `AuditedToolExecutor`의 공용 예산 guard를 경유하고 `reserve_tool_call()`을 직접 호출하지 않는다. graph node는 공용 nonterminal Tool 수집 경계를 경유하며 예산 차단으로 run을 FAILED 처리하지 않는다. 0건·정책 거부·timeout·중복은 공통 `ok`·`reason`·빈 deliveries 계약과 공통 reason prefix를 따른다 | FR-C-06, NFR-09, NFR-20 | V5-C-4.6 | 1.5h |
| V5-C-5.1 | P0 | 필수 API 5종. 완료: `GET /agent/runs`, `POST /agent/runs`, `POST /agent/ask`, `GET /approvals`, `POST /approvals/{approval_id}/decision`을 canonical DTO로 제공한다. 실행 시작은 `{alarm:{source,alarm_id}}`만 받아 202로 run을 만들고, run 응답의 `deliveries`는 action link에서 public `EMAIL\|MES` projection으로 만든다. 목록은 안정 정렬·bare array, 공개 승인 body는 `APPROVED\|REJECTED`이며 Chat은 A/B Tool만 사용한다 | FR-C-01, FR-C-05, FR-I-03, FR-I-07, NFR-10~11, NFR-19 | V5-C-3.3, V5-C-2.3, V5-C-1.3, V5-B-2.2, V5-CM-4.1 | 2.0h |
| V5-C-5.2 | P1 | 화면 3 Agent 조립. 완료: 실행·승인·action·delivery와 A/B 근거 deep link를 연결하고 D가 소유한 감사 subview를 탭에 조립한다. `api.audit()` 구현을 중복하지 않으며 Loading·Error·Empty·Success를 검증한다 | FR-C-13, FR-I-02, NFR-17 | V5-C-5.1, V5-D-1.3 | 2.0h |
| V5-C-6.1 | P0 | golden flow E2E. 완료: `kosa_agent_e2e`의 incident 12개에서 MONITORING 5/WARNING 4/EQP_HOLD 3, n8n EMAIL, 승인 전 Kafka 0, 승인 후 MES Mock, 중복 실행·동시 승인·UNKNOWN·복구를 `send_action` 경유로 검증하고 동일 fixture의 Level 1·2 완료율·실제 Tool 호출·wall-clock 지연·LLM token 비교를 기록한다 | FR-C-02, FR-C-09, NFR-04, NFR-18, NFR-20 | V5-C-4.6-1, V5-C-5.1, V5-CM-4.7 | 2.0h |
| V5-C-6.2 | P1 | Fault 5-class 평가. 완료: runtime·prompt·Tool 비노출 prediction hash를 먼저 고정하고 단일 non-NRM TRACE incident 7건의 Accuracy·Macro-F1·class별 Precision/Recall/F1·근거 유효율을 계산한다. SUMMARY-only 5건은 `NO_INJECTED_FAULT`, mixed는 `AMBIGUOUS_LABEL`로 제외하고 합성 GT metadata 4종·분모·제외 사유를 기록한다 | FR-C-15, NFR-19 | V5-C-6.1, V5-A-2.3 | 2.0h |
| V5-C-7.1 | P2 | Level 3 ReAct 비교 | FR-C-11 | V5-C-6.2 | 2.0h |

**P0·P1 22 Task / 53.0h** · **P2 별도 1 Task / 2.0h**

---

## 실행·조치 불변식

```text
SUMMARY OOC-only          → MONITORING · 자동 · 외부 효과 없음
TRACE OOS · R03 없음      → WARNING    · 자동 · n8n SMTP
strict R03                → EQP_HOLD   · HITL 승인 · 승인 후 Kafka MES Mock
golden flow 12 incident   → MONITORING 5 / WARNING 4 / EQP_HOLD 3
```

- Level 1과 Level 2는 같은 State·Tool을 쓰며 설정만으로 고정 흐름과 조건 분기를 전환한다.
- `decide_action`은 DB·LLM·network·score·predicted fault·metrology를 받지 않는 순수 함수다.
- 원인 가설은 `FOC|RFM|MFD|TMD|OTH`만 허용한다. `NRM`, `fault_code`, Generator FAULTS는
  query·State·Tool·prompt에 넣지 않는다.
- 동일 incident 활성 run 1개, incident당 유효 action 1개를 보장한다. 자동 조치의 FAILED
  retry는 새 run을 만들되 기존 action을 `REUSED`로 연결한다. EQP_HOLD의 기존 approval은
  PENDING이어도 이전 run 소유이므로 새 run에서 재사용하지 않는다. terminal approval은
  `ACTION_APPROVAL_NOT_PENDING`, PENDING의 소유 run 불일치는 `ACTION_APPROVAL_RUN_MISMATCH`로
  거부하며 복구는 C-3.3의 동일 run/thread 재개가 소유한다.

### V5-C-3.2 policy provenance 인계

- 저장 정본은 신규 column이 아닌 `agent_run.evidence` JSON이다.
- `action_policy_version`과 결정에 사용한 `member_alarms` AlarmRef snapshot을
  WAITING_APPROVAL·COMPLETED·FAILED 모든 terminal에서 보존한다.
- `finalize`·`fail_run`은 기존 evidence를 읽어 terminal evidence와 merge하며,
  `finish_agent_run()`이 action provenance를 덮어쓰지 않는 회귀를 둔다.
- `action_history.reason` 문자열에는 JSON이나 policy version을 넣지 않는다.
- action row·link·approval·delivery 생성 transaction과 위 evidence merge 구현은
  `V5-C-3.2`가 소유한다.
- delivery `request_hash`는 `delivery-request-v1` stable identity(action·channel·조치·incident·
  대표 AlarmRef)를 key 정렬·공백 없는 JSON으로 직렬화한 접두사 없는 lowercase 64 hex다.
  recipient·template·시각·서명은 포함하지 않고 C-4.x가 저장값을 그대로 재사용한다.

## Tool·API 계약과 선행조건

Level 1·2의 수집 단계는 다음 내부 Tool을 직접 호출한다. public API를 내부 Tool 대신 호출하지 않는다.

```text
A  get_fdc_summary(lot_hist_id)                         V5-A-3.2-1
B  search_documents(query, model_code=None, top_k=4)   V5-B-2.2
B  get_equipment_context(chamber_id)                   V5-B-3.2
C  send_action(action_id)                              V5-C-4.6-1
```

`get_fdc_summary`·`search_documents`·`get_equipment_context`가 모두 준비되기 전에는
`V5-C-2.1` 통합 완료로 보지 않는다. `send_action`은 저장된 delivery plan과 승인 상태만 실행하며
조치를 재결정하지 않는다. 모든 Tool은 0건·정책 거부·timeout·중복을 포함해 공통
`ok`·`reason`·빈 payload와 공통 reason prefix를 따른다. 실행 횟수·상태·`latency_ms`는 Tool
결과가 아니라 `agent_tool_call` metadata에 기록한다.

```http
GET  /agent/runs
POST /agent/runs
POST /agent/ask
GET  /approvals
POST /approvals/{approval_id}/decision
POST /internal/actions/{action_id}/delivery
```

실행 시작 body는 `{alarm:{source,alarm_id}}`이고 run의 `deliveries`는 action link에서 public
`EMAIL|MES`로 projection한다. 공개 승인 body는 `APPROVED|REJECTED`다. 내부 callback은 raw
bytes 기준 HMAC-SHA256, timestamp, 300초 replay window를 검증한다.

## HITL·Checkpoint·전송

- checkpoint 기준 절은 **시스템설계서 §12 동시성·Checkpoint·복구**다. §13이 아니다.
- `thread_id`는 `agent_run_id`와 독립인 UUID다. interrupt 전에 action·approval·Tool budget을
  commit하고 동일 thread로 재개한다.
- `V5-C-5.1` 실행자는 같은 run/thread의 동시 invoke를 막는다. 그 전 저장
  경쟁은 prediction 1행을 유지하되 실제로 소비한 각 usage를 run에 가산한다.
- Tool 예산은 총 8회이며 interrupt 전후 누적값을 checkpoint와 DB에서 복원한다. DB의
  `agent_run` row lock 아래 총·Tool별 호출 수를 다시 집계한 뒤 같은 transaction에서 예약한다.
- `send_action` 2회를 위해 읽기 Tool은 합계 6회까지만 예약한다. 동일 Tool은 최초 호출을 포함해
  최대 4회이며, 차단은 실제 Tool을 호출하지 않고 nonterminal exact code로 State·run에 남긴다.
- finalize 실패 sentinel은 자동 회수·삭제·차감하지 않고 row를 보존해 예산에 계속 포함한다.
  Tool별 hard 상한과 안전한 회수 재평가는 `V5-CM-4.8`이 소유하며 최종 `V5-CM-5.3` Gate가
  그 hard/soft 판정·종료 postcondition·잔여 미충족을 인용한다.
- 승인 전·반려 시 `fdc.actions` 발행은 0건이다. 승인된 EQP_HOLD만 n8n Kafka Producer가
  발행하고 `fdc.actions.result`를 write-back한다.
- EMAIL·MES_MOCK은 `(action_id, channel)`별 외부 효과 최대 1회다. 동일 hash는 같은 결과,
  다른 hash는 409, 응답 유실은 `UNKNOWN`이며 자동 재발송하지 않는다.
- n8n JSON은 delivery·write-back 3종만 `deploy/n8n/`에 두고 secret·credential을 포함하지
  않는다. 실행 시작은 `POST /agent/runs`가 소유한다. 학원 공용 n8n에 import·연결하며 팀
  compose에는 n8n 컨테이너를 추가하지 않는다.

## 평가·화면 완료 기준

Fault 5-class 평가는 단일 non-NRM TRACE incident 7건만 대상으로 한다. SUMMARY-only 5건은
`NO_INJECTED_FAULT`, mixed label은 `AMBIGUOUS_LABEL`로 제외하고 분모·사유를 기록한다.
prediction hash를 label 접근 전에 고정하며 artifact에는 다음 **합성 GT metadata 4종**을 반드시
기록한다.

```text
public_fault_ground_truth_available = true
label_source = SYNTHETIC_GENERATOR
production_ground_truth_available = false
usage_scope = EVALUATION_ONLY
```

추가로 dataset epoch·source hash·prediction hash·feature/model/prompt version·split manifest를
기록한다. 화면 3은 실행·승인·action·delivery와 A/B deep link를 연결하고 D 소유 감사 subview를
조립만 한다. `api.audit()`를 중복 구현하지 않으며 Loading·Error·Empty·Success 네 상태를 검증한다.

## 원본 절

```text
요구사항 v2.1  5.3 FR-C-01~15
설계 v2.1      6. LangGraph Agent · 7. Action·HITL·자동화
               3.4 Runtime table · 12. 동시성·Checkpoint·복구
역할분담 v10.1  8. C — Agent·HITL·n8n·Kafka Full-stack
기준표          4. 조치와 anomaly score · 5. 외부 연동
패키지          docs/07_n8n_워크플로_제작가이드.md
```
