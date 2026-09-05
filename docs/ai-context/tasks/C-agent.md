# C — Agent · HITL · n8n · Kafka

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-09-01
> 담당: 방대혁 · 모듈 `backend/app/agent/` · `frontend/src/features/agent/` · `deploy/n8n/`

LangGraph Level 1·2, 원인 가설, 3단계 규칙 조치, HITL 승인, n8n SMTP, Kafka MES Mock,
`send_action`과 화면 3 조립을 책임진다. 화면 3의 감사는 선택 run·action·approval 문맥이며,
D의 독립 화면 7 전역 감사 조회를 대체하거나 중복 구현하지 않는다.

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
| V5-C-2.2 | P0 | Tool 예산. 완료: DB run level별로 Level 1·2 `total 8/read 6/send 2`, Level 3 `total 10/read 8/send 2`, 동일 Tool 최대 4회와 Level 3 selector 최대 10 step을 강제하고 HITL 중단·재개 전후 누적 적용해 checkpoint·DB에서 복원한다. 읽기 Tool은 caller 대기 soft 8초를 적용하고 worker 포화·queue 대기를 실제 Tool timeout과 별도 code로 구분한다. 외부 효과 없는 성공 `send_action` no-call은 감사 행을 보존하되 예산에서만 제외하고, 실패·timeout·미종료·구형 output은 보수적으로 소비한다. hard 상한의 실제 집행·종료 검증은 `V5-CM-4.8`에 귀속한다. finalize 실패 예약 sentinel은 자동 회수·삭제·차감하지 않고 row를 보존해 예산에 계속 포함하며, hard 종료가 확보된 구간의 회수 가능성만 `V5-CM-4.8`에서 재평가한다 | FR-C-08, FR-C-11, NFR-03 | V5-C-2.1 | 4.0h |
| V5-C-2.3 | P0 | 원인 가설. 완료: `FOC\|RFM\|MFD\|TMD\|OTH` 구조화 출력과 실제 AlarmRef·chunk·relation 근거 인용을 생성하고 실제 LLM input/output token·model·prompt version을 run·prediction에 기록한다. `NRM`과 합성 라벨·Generator FAULTS는 query·State·Tool·prompt에 넣지 않는다 | FR-C-07, FR-C-15, NFR-19 | V5-C-2.2 | 4.0h |
| V5-C-3.1 | P0 | `decide_action`. 완료: SUMMARY OOC-only → MONITORING, TRACE OOS → WARNING, strict R03 → EQP_HOLD의 3단계 순수 규칙 함수를 만든다. LLM·score·metrology를 입력에서 제외한다 | FR-C-03 | V5-C-2.3 | 2.0h |
| V5-C-3.2 | P0 | action 생성 transaction. 완료: incident advisory lock→run row lock 아래 `action_history`·CREATED/REUSED link·approval·delivery와 policy provenance를 한 트랜잭션에서 만들고 incident당 유효 action 1건을 보장한다. `request_hash`는 stable identity의 raw 64 hex이며 같은 run 재호출과 자동 조치의 FAILED retry를 멱등 처리한다. EQP_HOLD는 approval의 상태·소유 run을 검증하며 새 run에 기존 approval을 재사용하지 않는다 | FR-C-14 | V5-C-3.1 | 4.0h |
| V5-C-3.3 | P0 | HITL 승인. 완료: EQP_HOLD bundle과 `WAITING_APPROVAL`을 같은 transaction에 저장하고 승인요청 email node 뒤 durable checkpoint에서 중단한다. 승인·action·MES delivery·감사를 한 조건부 UoW로 결정하며 session advisory lock 아래 동일 thread를 재개한다. DB↔checkpoint crash window는 같은 run catch-up으로 복구하고 checkpoint 상실은 fail-closed한다 | FR-C-04, FR-C-05 | V5-C-3.2, V5-C-0.2 | 4.0h |
| V5-C-3.4 | P0 | HITL checkpoint 상실 재수화. 완료: `persist_action` 직전 State snapshot을 EQP_HOLD bundle·`WAITING_APPROVAL`과 같은 transaction에 저장한다. checkpoint가 없을 때 DB run·snapshot provenance·prediction·bundle을 결속 검증해 `approval_email` 앞에 복원하고 기존 catch-up으로 `hitl_interrupt` 앞에서 중단한다. `start_incident_run`·외부 Tool·LLM·새 action 호출 0회, write 불확실 postcondition과 복원 불가 상태의 sanitized fail-closed를 증명한다 | FR-C-04, FR-C-14 | V5-C-3.3 | 4.5h |
| V5-C-4.1 | P0 | n8n workflow 제작. 완료: delivery·write-back용 `WF2-notify-email`·`WF3-mes-hold`·`WF4-result-writeback` JSON 3종만 `deploy/n8n/`에 둔다. 실행 시작은 source-aware `POST /agent/runs`가 소유하며 source-less `WF1-alarm-to-agent`는 만들지 않는다. raw body HMAC·timestamp 검증, `request_hash` 멱등성, Kafka key=`action_id`, channel=`MES_MOCK` 계약을 workflow fixture로 고정하고 secret·credential은 포함하지 않는다 | FR-C-12, NFR-02, NFR-20 | V5-C-3.3 | 3.0h |
| V5-C-4.2 | P0 | **공용 n8n import·연결**. 완료: workflow 3종을 학원 공용 n8n에 import해 typeVersion·crypto/env 허용·HTTP output 형상과 credential connector를 확인하고, credential·webhook URL·승인된 SMTP sender를 공용 환경에서 주입한다. Kafka Trigger의 top-level `resolveOffset=onSuccess`·`eachBatchAutoResolve=false`·`errorRetryDelay=5000`과 Producer의 `acks=true`·`timeout=10000`이 pin 소스·import/export에서 보존되는지 확인한다. connector-level stub smoke 뒤 모두 비활성화하며 실제 SMTP·Backend callback·Kafka 왕복과 영구 활성은 `V5-C-4.3`~`4.6`이 소유한다. 팀 compose의 n8n 컨테이너는 0건이다 | FR-C-12, FR-I-04, NFR-02 | V5-C-4.1 | 3.5h |
| V5-C-4.3 | P0 | SMTP delivery. 완료: WARNING 이메일 1회, EQP_HOLD 승인요청 이메일 1회를 서명 webhook으로 발송하고 실패·timeout을 기록한다. 동기 호출을 유지하면 Backend→n8n timeout을 workflow 최악 20초보다 큰 **25초 이상**으로 고정한다. 비동기로 바꾸면 202 acceptance와 DB delivery 조회를 정본으로 계약한다 | FR-C-06, FR-C-12 | V5-C-4.2 | 3.5h |
| V5-C-4.4 | P0 | write-back callback. 완료: `POST /internal/actions/{action_id}/delivery`가 timestamp·HMAC 서명·300초 replay window를 검증하고 channel별 상태를 갱신한다 | FR-C-06 | V5-C-4.3 | 3.0h |
| V5-C-4.5 | P0 | Kafka MES Mock. 완료: 승인된 EQP_HOLD만 n8n Kafka Producer로 `fdc.actions`에 발행하고, MES Mock consumer 결과를 `fdc.actions.result` → write-back으로 반영한다. 승인 전 발행 0건·반려 시 발행 0건을 음성 테스트로 고정한다. execution 저장이 꺼진 WF4 malformed discard는 직접 조회할 수 없으므로 의심 record 전후 consumer offset 비교로 처리 여부를 확인하고, callback 실패 시 미해결 offset과 broker retention 안의 offset-reset 복구 절차를 runbook으로 증명한다 | FR-C-06, FR-C-12 | V5-C-4.4 | 4.0h |
| V5-C-4.6 | P0 | 채널 멱등성. 완료: EMAIL·MES_MOCK 각각 `(action_id, channel)` 외부 효과 최대 1회, 동일 hash 재수신 동일 결과, 다른 hash 409, 응답 유실 `UNKNOWN`·자동 재발송 0회를 n8n·Kafka 경로에서 검증한다. CM-4.6 자동 lag 감지가 준비되기 전에는 runbook의 consumer group `kosa-fdc-wf4-writeback` lag 확인을 영구 활성 직전·직후 수동 수행하며, 확인할 수 없으면 영구 활성은 BLOCKED다 | FR-C-06, NFR-20 | V5-C-4.4, V5-C-4.5 | 3.0h |
| V5-C-4.6-1 | P0 | `send_action(action_id)` Tool. 완료: 단일 `action_id`의 저장된 delivery plan·승인 상태를 검증해 실행 가능한 EMAIL·MES_MOCK adapter만 호출하고 조치를 재결정하지 않는다. 예약은 `AuditedToolExecutor`의 공용 예산 guard를 경유하고 `reserve_tool_call()`을 직접 호출하지 않는다. graph node는 공용 nonterminal Tool 수집 경계를 경유하며 예산 차단으로 run을 FAILED 처리하지 않는다. 0건·정책 거부·timeout·중복은 공통 `ok`·`reason`·빈 deliveries 계약과 공통 reason prefix를 따른다 | FR-C-06, NFR-09, NFR-20 | V5-C-4.6 | 1.5h |
| V5-C-5.1 | P0 | 필수 API 5종. 완료: `GET /agent/runs`, `POST /agent/runs`, `POST /agent/ask`, `GET /approvals`, `POST /approvals/{approval_id}/decision`을 canonical DTO로 제공한다. 실행 시작은 `{alarm:{source,alarm_id}}`만 받아 202로 run을 만들고, run 응답의 `deliveries`는 action link에서 public `EMAIL\|MES` projection으로 만든다. 목록은 안정 정렬·bare array, 공개 승인 body는 `APPROVED\|REJECTED`다. Chat은 명시된 `lot_hist_id`·`lot_id`·`chamber_id`·`model_code`로만 A/B 읽기 Tool을 선택하고, 근거 ID citation·required-nullable 판단·5종 evidence union을 검증하며 Runtime·감사·action·approval 쓰기는 0이다 | FR-C-01, FR-C-05, FR-I-03, FR-I-07, NFR-10~11, NFR-19 | V5-C-3.3, V5-C-2.3, V5-C-1.3, V5-B-2.2, V5-CM-4.1 | 4.0h |
| V5-C-5.2 | P1 | 화면 3 Agent 조립. 완료: 실행·승인·action·delivery와 A/B 근거 deep link를 연결하고, D의 bare 감사 API를 소비하는 run-scoped 감사 subview를 공유 경계에 구현한다. GRAPH는 `chamber_id·relation_id·graph_revision`으로 Ontology에 이동해 동일 revision의 관계를 복원한다. Loading·Error·Empty·Success와 승인 충돌·오류 상태를 검증한다. Detection 3 route는 A-3.1·A-3.2 승계 전 화면 실행용 scaffold이며 A Task 완료로 간주하지 않는다 | FR-C-13, FR-I-02, NFR-17 | V5-C-5.1 | 6.0h |
| V5-C-5.2-1 | P1 | Agent 종합 진단·판단·조치 전달과 평가 보조 탭. 완료: final epoch의 canonical 12 incident와 R03 3건·persisted WAFER 3·AlarmRef 9·non-R03 고유 WAFER 전체·run당 target 최대 3을 구조 fixture로 고정하고 기존 FDC Tool로 조회한다. `1 WAFER×5·2×4·3×3`과 FDC 22회는 CM-5.2 실제 증적으로 확정한다. PG 실제 route·Neo4j·RAG의 충분성/충돌과 LLM 1차·대안 가설·영향 확인 범위·검증 절차를 저장하되 `ACTION-POLICY-V1` 규칙 조치와 HITL을 바꾸지 않는다. 유사 incident는 현재 Runtime DB의 canonical v2 최초 run 안에서 exact score로 비교하고, 사후 효과는 정적 데이터 한계 `NOT_AVAILABLE_STATIC_DATASET`으로 공개한다. rehearsal은 `kosa_agent_e2e`, 시연은 승인·백업·Runtime reset 뒤 v2 12건을 준비한 `kosa_agent`를 사용한다. 실행 문맥 Ask, 종합 진단 UI·7상태 delivery·bounded polling·화면 2·3 공용 Trace·aggregate-only 평가 API를 제공하며 v2 artifact는 attempt별 새 identity로 게시하고 시연 PC env·hash를 확인한다. canonical 36 operation을 유지하고 raw prompt·정답 label·credential·내부 hash·가짜 운영 성과는 노출하지 않는다 | FR-C-01~03, FR-C-07~08, FR-C-10, FR-C-13, FR-C-15, FR-I-02, NFR-03, NFR-17, NFR-19 | V5-C-5.2, V5-B-4.2 | 25.0h |
| V5-C-5.3 | P0 | incident 일회성 자동 배치 관리 명령. 완료: Runtime run 이력이 전혀 없는 incident만 stable order로 선택해 대표 `AlarmRef`로 기존 Agent runtime을 incident당 1회 실행하는 `run_pending_incidents.py --once`를 제공한다. start 뒤 continue 실패는 exact run을 FAILED로 보상하고 postcondition을 재조회하며, 이전 `RUNNING` run은 `INCOMPLETE_RUN`으로 정상 race와 구분한다. 기존 이력이 있으면 FAILED를 포함해 자동 재선택하지 않고 public 수동 재실행에 맡기며, 즉시 2회차 실행의 신규 run·action·delivery가 모두 0임을 검증한다. 상시 scheduler·public batch API/UI·n8n WF1은 만들지 않는다 | FR-C-09, FR-C-14 | V5-C-5.1 | 3.0h |
| V5-C-6.1 | P0 | golden flow E2E. 완료: `kosa_agent_e2e`에서 C-5.3 batch command 1회로 incident 12개를 실행해 MONITORING 5/WARNING 4/EQP_HOLD 3, n8n EMAIL, 승인 전 Kafka 0, 승인 후 MES Mock, 2회차 batch 신규 run·action·delivery 0, 수동 재실행·동시 승인·UNKNOWN·복구를 `send_action` 경유로 검증하고 동일 fixture의 Level 1·2 완료율·실제 Tool 호출·wall-clock 지연·LLM token 비교를 기록한다 | FR-C-02, FR-C-09, NFR-04, NFR-18, NFR-20 | V5-C-4.6-1, V5-C-5.1, V5-C-5.3, V5-C-3.4, V5-CM-4.7 | 4.0h |
| V5-C-6.2 | P1 | Fault 5-class 평가. 완료: C-6.1 원 evidence의 round-2 baseline run 12건에서 Runtime prediction hash를 label 접근 전에 고정한다. evaluation role로 각 incident 전체 member를 읽어 distinct non-NRM 1종 7건만 Accuracy·고정 5-class Macro-F1·class별 Precision/Recall/F1로 보고하고, 0종 5건은 `NO_INJECTED_FAULT`, 2종 이상은 `AMBIGUOUS_LABEL`로 제외한다. 구조화 prediction·run-scoped 근거·규칙 조치 일치 12/12만 hard Gate로 삼고 합성 GT metadata 4종·두 DB provenance/shared-key hash·분모·제외 사유를 불변 artifact에 기록한다 | FR-C-15, NFR-19 | V5-C-6.1, V5-A-2.3 | 4.0h |
| V5-C-7.1 | P2 | Level 3 ReAct — **자율 Tool 선택 조사 루프와 production 상시 전환**(개정 2026-09-04). 완료: ① 조사 Tool 확장(상류·하류 target·chamber 이력·형제·metrology) ② 가설 v3(`parameter_findings`·`origin_assessment`) ③ Agent 상세 판정 카드·조사 타임라인 ④ 2-key opt-in·DB allowlist ⑤ **Level 3 12-run 단일 배치 견고성**과 `integrity && robustness && delivery_integrity` 3-Gate 전환 ⑥ U10 고정 정책 비교(**보고 지표** · 배포 Gate 아님). 외부 효과는 **SMTP 실발송 승인(`SMTP_SEND_GRANT`) 뒤에만** 발생한다 (원문 P2 2.0h → 2026-09-04 범위 확장 · 전액 승인) | FR-C-11, FR-C-08, NFR-03 | V5-C-6.2, V5-CM-5.2 | 19.9h |
| V5-C-7.2 | P2 | Agent 실행 흐름 시각화. 완료: run 상세에 LangGraph 노드 흐름(`load_incident → collect → react 루프 → generate_hypothesis → decide_action → delivery/HITL → finalize`)을 **B-4.2와 같은 shared xyflow 표현**으로 그리고, 각 노드에 `react_trace` step·HITL 대기·delivery 상태를 결속한다. 승인 UI는 제거하지 않고 **축소**한다(9-03 결정). Loading·Error·Empty·Success 4상태·기존 polling 계약 유지. 읽기 전용이며 새 API를 만들지 않고 `GET /agent/runs/{run_id}` additive 필드만 소비한다 (공수는 추정 · 계획리뷰에서 확정 · U2-lite·U3 뒤) | FR-C-11 연계(**화면 요구는 요구사항 개정 대상 — 등록 시 FR 번호 확정**) | V5-C-7.1, V5-B-4.2 | 3.0h |
| V5-C-7.3 | P2 | 영향 규모 정량화(`impact_scope`). 완료: 하류를 `CHECKED`한 Level 3 run에 한해 **code가 SQL로** `{downstream_step, total_wafers, ooc_wafers, metrology_fail}`을 집계해 `agent_prediction.evidence`(additive)와 판정 카드 한 줄에 표시한다. LLM 호출·ReAct 루프·Tool 예산·lifecycle 계약 **무변경**. 실측 근거: PHOTO chamber별 하류 wafer 48~52장이 ETCH chamber 1곳으로 감(2026-09-05 `kosa_readonly`). 조치 규칙은 바꾸지 않는다(기준표 README §170) (⑬ U6 뒤 착수) | FR-C-11 연계 | V5-C-7.1 | 1.5h |
| V5-C-7.4 | P2 | 가설 반증 검증 루프(refutation). 완료: 가설 v3 생성 뒤 LLM이 "이 가설이 틀렸다면 나와야 할 관측"을 구조화 출력(반증 차원 ∈ {upstream, sibling, history, metrology, parameter_direction})으로 제안하고, **code가 기존 5 read Tool 호출로 매핑·실행**한 결과가 모순이면 가설 수정을 강제하며, 아니면 `hypothesis.verification.checked_dimensions`(additive)에 기록한다. 새 데이터·새 Tool 없음. 조치 규칙(`decide_action`) 무변경(기준표 README §170). selector step 예산 +2(Level 3 `12`)·prompt 1종 추가. 지표는 U10 CF-6/7/8(상류 이탈·형제 정상·이력 DRIFT)로 반증 단계 전후 가설 수정률·근거 recall을 재측정한다. 정답 라벨이 없으므로 원인 카테고리 정확도는 주장하지 않는다(2026-09-05 사용자 결정 "다끝내고 저것들도 추가하자" · ⑬ U6·7.2·7.3 뒤 착수) | FR-C-11 | V5-C-7.1, V5-C-7.3 | 3.0h |
| V5-C-7.5 | P2 | 재현성 측정. 완료: 같은 12 incident를 seed·실행 시각을 바꿔 Level 3로 N회(기본 3회) 반복 실행해 가설 카테고리·인용 근거 집합·조치의 회차 간 일치율을 `reproducibility.json`(immutable·SHA·validator)으로 산출하고 U6 한계 "재현성 미측정"을 실측치로 대체한다. 기존 U10 runner·E2E reset 도구 재사용, 새 데이터 없음. 실 LLM 비용·데이터 반출 재승인·`kosa_agent_e2e` reset은 실행 시 별도 승인(공수에 미포함). 조치 규칙 무변경(2026-09-05 사용자 결정 · V5-C-7.4 뒤 착수) | FR-C-11, NFR-03 | V5-C-7.4 | 1.5h |
> **V5-C-7.1 범위 개정(2026-09-04 · 2026-09-05 재적용)**: 원문은 `P2 · Level 3 ReAct 비교 · FR-C-11 · V5-C-6.2 · 2.0h`였다. 멘토 피드백("규칙 기반 DB 메소드 호출과 다를 게 없다")에 답하기 위해 조사 완결성과 production 상시 Level 3 전환까지 **같은 Task ID 안에서 확장**했다. 선행에 `V5-CM-5.2`가 추가된다 — 견고성 12-run이 CM-5.2 Stage2 attempt 안에서 수행되기 때문이다. **Common 소유 경계**: `deploy/compose/cm52_stage2.sh`의 mode 확장(`--prepare-only`/`--resume-workload`/`--abort-prepared`/`--recover-prepared`)과 `output/V5-CM-5.2_stage2_실행절차.md` 갱신은 **C-7.1이 수행하고 Common(방대혁)이 인계**받으며, **기존 Level 2 CM-5.2 계약 무변경**이 선행 Gate다. `backend/scripts/orchestrate_e2e_reset_evidence.py`(V5-CM-4.7)는 변경하지 않는다. 이 개정은 2026-09-04에 작업 트리에 적용됐으나 커밋 `c1278c2` 전 격리 정리에서 되돌려져 2026-09-05 재적용했다. 상세 계획은 `output/V5-C-7.1_작업계획.md`.
> **V5-C-7.2·7.3 신설(2026-09-05 · 사용자 지시 "7.2도 wbs에 넣어주고 영향규모 정량화도")**: 7.2는 계획 v4부터 "별 Task · 이 계획의 완료 판정에 포함하지 않음"으로 분리돼 있던 항목이고, 7.3은 2026-09-05 상류·하류 조사 범위 논의에서 후속으로 확정한 항목이다(여러 단계 추적은 route가 `CT-PHOTO → CT-ETCH` 2단계뿐이라 하지 않는다). 9-03에 "제외"로 기록된 구 C-7.3은 정의 없이 폐기된 ID라 재사용한다. 둘 다 C-7.1 ⑬ U6 뒤에 착수한다.


**P0·P1 25 Task / 107.5h** · **P2 별도 3 Task / 24.4h**(V5-C-7.1 19.9h 승인 · 7.2 3.0h 추정 · 7.3 1.5h)

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
  거부한다. checkpoint가 보존된 복구는 C-3.3의 동일 run/thread catch-up이, checkpoint
  자체가 사라진 재수화는 C-3.4가 소유한다.

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
GET  /agent/evaluations
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
- `V5-C-3.3` resume service가 PostgreSQL session advisory lock으로 같은 run/thread의
  동시 invoke를 막는다. `V5-C-5.1`은 이 서비스를 조립하며 별도 mutex를 재구현하지 않는다.
- `hitl_interrupt` port는 terminal 승인 상태를 `Decision`으로 변환하는 **순수 read**다.
  node 성공 checkpoint 전에 process가 죽으면 재호출되므로 DB 쓰기·메일·Kafka 등 외부
  효과를 절대 추가하지 않는다.
- action bundle과 `WAITING_APPROVAL`은 같은 transaction에 commit하고 승인요청 email node
  뒤에서 중단한다. DB↔checkpoint crash window는 같은 run/thread catch-up으로 수렴한다.
  checkpoint 자체가 없으면 C-3.3은 `CHECKPOINT_MISSING`으로 상태 변경 없이 막고,
  `V5-C-3.4`가 DB 정본 기반 재수화를 구현한다.
- C-3.4가 PENDING checkpoint를 재수화하면 `approval_email` node부터 다시 실행하므로 승인요청
  메일이 재발송될 수 있다. 두 메일은 동일한 `approval_id`를 가리키며 어느 메일에서 승인·반려해도
  같은 요청에 적용되고, 새 approval·action을 만들지 않는다.
- `rehydration_snapshot`의 실제 데이터 JSONB 크기는 12개 incident를 쓰는 `V5-C-6.1`에서
  `pg_column_size`로 기록한다. 단일 wafer 격리 fixture만으로 임의 상한을 정하지 않는다.
- Tool 예산은 Level 1·2 `total 8/read 6/send 2`, Level 3 `total 10/read 8/send 2`이며
  동일 Tool은 최대 4회, Level 3 selector는 최대 10 step이다. interrupt 전후 누적값을 checkpoint와 DB에서 복원한다. DB의
  `agent_run` row lock 아래 총·Tool별 호출 수를 다시 집계한 뒤 같은 transaction에서 예약한다.
- 실제 효과를 시도하는 `send_action` 2회를 위해 읽기 Tool은 Level 1·2 합계 6회,
  Level 3 합계 8회까지만 예약한다.
  성공 멱등 no-call은 감사 행을 남기되 예산에서 제외한다. 동일 Tool은 최초 호출을 포함해 최대
  4회이며, 차단은 실제 Tool을 호출하지 않고 nonterminal exact code로 State·run에 남긴다.
- finalize 실패 sentinel은 자동 회수·삭제·차감하지 않고 row를 보존해 예산에 계속 포함한다.
  Tool별 hard 상한과 안전한 회수 재평가는 `V5-CM-4.8`이 소유하며 최종 `V5-CM-5.3` Gate가
  그 hard/soft 판정·종료 postcondition·잔여 미충족을 인용한다.
- 승인 전·반려 시 `fdc.actions` 발행은 0건이다. 승인된 EQP_HOLD만 n8n Kafka Producer가
  발행하고 `fdc.actions.result`를 write-back한다.
- `action_history.approved_by`는 자동 조치에서 `system`, 사람 승인에서 승인자 ID이며
  PENDING·REJECTED에는 `NULL`이다. 승인 상태와 함께 해석하고 이 값만으로 반려 여부를
  추론하지 않는다.
- EMAIL·MES_MOCK은 `(action_id, channel)`별 외부 효과 최대 1회다. 동일 hash는 같은 결과,
  다른 hash는 409, 응답 유실은 `UNKNOWN`이며 자동 재발송하지 않는다.
- WF3의 HTTP 200은 source 요청 처리가 끝났다는 뜻이다. 응답의 `published=true`는 Kafka publish
  성공, `published=false`는 publish 실패가 Backend callback에 기록됐음을 뜻하며 delivery 정본은
  DB다. 두 경우를 같은 `{ok:true}` body로 축약하지 않는다.
- n8n JSON은 delivery·write-back 3종만 `deploy/n8n/`에 두고 secret·credential을 포함하지
  않는다. 실행 시작은 `POST /agent/runs`가 소유한다. 학원 공용 n8n에 import·연결하며 팀
  compose에는 n8n 컨테이너를 추가하지 않는다.
- repository JSON의 SMTP sender는 외부 발송을 막는 `.invalid` placeholder이며 실제 sender를
  Git에 반영하지 않는다. `V5-C-4.2`에서 승인된 sender와 credential을 공용 n8n에만 주입하고
  typeVersion·crypto allowlist·env access·non-empty secret·Backend URL·HTTP output 형상과 Kafka
  Trigger의 top-level `resolveOffset=onSuccess`·`eachBatchAutoResolve=false`·`errorRetryDelay=5000`,
  Producer의 `acks=true`·`timeout=10000` 보존을 확인한다. n8n v1 `acks=true`는 공식 구현상
  KafkaJS `acks=1`로 변환되므로 그 pin 소스 매핑을 증적하고, 실제 Kafka 효과는 `V5-C-4.5`가
  왕복 검증한다. connector-level stub smoke 뒤 비활성화한다.
- WF4 consumer group은 `kosa-fdc-wf4-writeback`으로 고정한다. callback 2xx에서만 offset을
  resolve하고, malformed record만 discard 후 resolve한다. execution 저장이 모두 `none`이므로
  discard payload 자체는 사후 조회할 수 없으며 `V5-C-4.5` runbook에서 의심 record 전후 offset을
  비교한다. `V5-CM-4.6` 자동 감지 전에는 `V5-C-4.6` 영구 활성 직전·직후 lag를 수동 확인하며
  확인 불가 시 활성화를 막는다.

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
