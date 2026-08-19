# C — Agent · HITL · n8n · Kafka

> 기준 원천: 멘토 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-19
> 담당: 방대혁 · 모듈 `backend/app/agent/` · `frontend/src/features/agent/` · `deploy/n8n/`

LangGraph 실행, 원인 가설, 3단계 조치, HITL 승인, n8n SMTP, Kafka MES Mock, 화면 3을 책임진다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-C-01 | Source-aware Agent | 필수 |
| FR-C-02 | 자율성 Level | 필수 |
| FR-C-03 | `decide_action` | 필수 |
| FR-C-04 | HITL | 필수 |
| FR-C-05 | 승인 API | 필수 |
| FR-C-06 | 채널 전송 | 필수 |
| FR-C-07 | 실행 기록 | 필수 |
| FR-C-08 | Tool 예산 | 필수 |
| FR-C-09 | 배치·재실행 | 필수 |
| FR-C-10 | 공정 연쇄 근거 | 필수 |
| FR-C-12 | 자동화 workflow | 필수 |
| FR-C-13 | Agent 화면 | 필수 |
| FR-C-14 | 중복 방지 | 필수 |
| FR-C-15 | Agent 평가 | 필수 |
| FR-C-11 | Level 3 ReAct | 도전 |

## Task (WBS v5)

| ID | 내용 | 공수 |
|---|---|---:|
| V5-C-0.1 | Runtime Repository·ID·감사 계약 | 2.0h |
| V5-C-0.2 | thread·checkpoint 계약 | 1.5h |
| V5-C-1.1 | incident 해석·대표 알람 | 2.0h |
| V5-C-1.2 | 실제 routing 결합 | 2.0h |
| V5-C-1.3 | 중복 실행 방지 | 1.5h |
| V5-C-2.1 | LangGraph 골격 | 2.5h |
| V5-C-2.2 | Tool 예산 | 2.0h |
| V5-C-2.3 | 원인 가설 5-class | 2.5h |
| V5-C-3.1 | `decide_action` 3단계 규칙 | 1.5h |
| V5-C-3.2 | action 생성 transaction | 2.0h |
| V5-C-3.3 | HITL 승인·재개 | 2.5h |
| V5-C-4.1 | **n8n workflow 4종 제작** | 2.5h |
| V5-C-4.2 | **compose에 n8n 추가** | 1.0h |
| V5-C-4.3 | SMTP delivery | 2.0h |
| V5-C-4.4 | write-back callback | 1.5h |
| V5-C-4.5 | **Kafka MES Mock** | 2.0h |
| V5-C-4.6 | 채널 멱등성 | 1.5h |
| V5-C-5.1 | 필수 API 4종 | 2.0h |
| V5-C-5.2 | 화면 3 Agent | 3.0h |
| V5-C-6.1 | golden flow E2E | 2.5h |
| V5-C-6.2 | Fault 5-class 평가 | 2.5h |
| V5-C-7.1 | Level 3 ReAct (P2) | 2.0h |

**합계 42.5h** (P2 2.0h 제외)

---

## 완료 기준 (최종 실측값)

```text
조치 규칙       SUMMARY OOC만          → MONITORING · 자동 · 외부 효과 없음
               TRACE OOS · R03 없음    → WARNING    · 자동 · n8n SMTP
               strict R03              → EQP_HOLD   · 사람 승인 · 승인 후 Kafka MES Mock
golden flow    incident 12 기준 MONITORING 5 / WARNING 4 / EQP_HOLD 3
Fault 5-class  단일 distinct non-NRM 라벨 TRACE incident 7건에만 적용
               SUMMARY-only NRM 5건 → NO_INJECTED_FAULT
               혼합 라벨 → AMBIGUOUS_LABEL (제외·별도 보고)
멱등성         (action_id, channel)별 외부 효과 최대 1회
               응답 유실은 UNKNOWN 전이 · 자동 재발송 0회
n8n workflow   deploy/n8n/WF1-alarm-to-agent · WF2-notify-email
                        · WF3-mes-hold · WF4-result-writeback
Kafka          승인 전 fdc.actions 발행 0건 · 반려 시 발행 0건
```

---

## 주의

**조치는 3단계 규칙만으로 결정한다.** `decide_action`은 DB·LLM·network·anomaly score·predicted
fault·metrology를 입력으로 받지 않는 순수 함수다. score는 근거 표시용이며 조치·incident·승인
게이트에 관여하지 않는다.

**`NRM`은 원인 가설 class가 아니다.** 이미 알람이 발생한 incident를 분류하므로 출력 도메인은
`FOC|RFM|MFD|TMD|OTH`다. `fault_code` label과 Generator FAULTS를 프롬프트에 넣지 않는다.

**Fault 평가에서 임의 정답을 만들지 않는다.** 다수결·OTH 대입 금지. 제외 사유별로 건수를 보고한다.

**승인 전 MES 이벤트를 발행하지 않는다.** 코드와 workflow 양쪽 조건으로 막고, Backend는 승인
트랜잭션에서 Kafka를 직접 발행하지 않고 서명된 n8n webhook을 호출한다.

**공개 승인 body는 `APPROVED|REJECTED`다.** 내부 `APPROVE|REJECT` Enum은 boundary adapter에서만
쓴다. 외부 채널 `MES`는 내부 `MES_MOCK`으로 매핑하며 실제 MES 연동으로 표현하지 않는다.

**n8n workflow는 팀 산출물이다.** 최종 패키지에 import 가능한 JSON이 없고
`docs/07_n8n_워크플로_제작가이드.md` §8이 제작 후 `deploy/n8n/` 커밋을 지정한다. 최종 패키지
compose에 n8n 서비스가 없으므로 팀 compose에 컨테이너 정의(5678·볼륨·basic auth)를 추가한다.

**FAILED 재실행은 새 run을 만든다.** 기존 run 상태를 되돌리지 않고 `retry_of_run_id`로 연결한다.
action 생성 후 실패면 기존 action을 `REUSED`로 연결하고 action·approval·delivery를 새로 만들지
않는다.

**`action_id`·`approval_id`로 run과 승인을 연결한다.** chamber-only 검색을 제거하고
`api.audit()` wrapper를 실제 감사 subview에서 소비한다.

**LLM 자격증명을 State·checkpoint·로그·artifact에 저장하지 않는다.** provider·model은 참고 구현
(`ChatAnthropic`, `claude-sonnet-4-5`)을 출발점으로 환경변수로 교체 가능하게 둔다.

---

## API

```http
GET  /agent/runs                              호환 필수
POST /agent/ask                               호환 필수
GET  /approvals                               호환 필수
POST /approvals/{approval_id}/decision        호환 필수. body는 APPROVED|REJECTED
POST /internal/actions/{action_id}/delivery   필수 내부. n8n·Kafka write-back
```

내부 callback은 `X-Delivery-Timestamp`·`X-Delivery-Signature`(HMAC-SHA256, 300초 replay window)를
검증하고 raw bytes로 서명을 확인한다.

선택 확장: Agent 실행·상세·재시도, action 상세, channel 재전송.

---

## 화면

| 화면 | 내용 |
|---|---|
| 3 Agent | 실행·승인·action·delivery 상태, 근거 deep link, 감사 subview |

---

## 원본 절

```text
요구사항 v2.1  5.3 FR-C-01~15
설계 v2.1      6. LangGraph Agent (6.1 incident ~ 6.6 Tool 예산)
               7. Action·HITL·자동화 (7.1 생성 ~ 7.5 delivery 상태)
               3.4 Runtime table · 13. Checkpoint·동시성·복구
역할분담 v10.1  8. C — Agent·HITL·n8n·Kafka Full-stack
기준표          4. 조치와 anomaly score · 5. 외부 연동
패키지          docs/07_n8n_워크플로_제작가이드.md
```
