# C — Agent · HITL

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-11
> 담당: 방대혁 · 모듈 `backend/app/agent/` · `frontend/src/features/agent/`
> 추가 역할: 공통 통합 관리 · React 공통 골격 · release captain

LangGraph 에이전트, 조치 결정, HITL 승인, n8n 전송, 감사로그를 책임진다.
**프로젝트의 핵심 경로**이며 4주차 분기점(골든 시나리오 2번)의 주체다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-C-01 | LangGraph 에이전트 (incident 단위 실행) | 필수 |
| FR-C-02 | 자율성 Level 1·2 스위치 | 필수 |
| FR-C-03 | `decide_action` 규칙 판정 | 필수 |
| FR-C-04 | HITL 승인 흐름 (interrupt + Checkpoint) | 필수 |
| FR-C-05 | 승인 API (409 이중 처리 차단) | 필수 |
| FR-C-06 | Tool `send_action` 멱등 전송 | 필수 |
| FR-C-07 | 실행 기록 (llm_model·latency_ms 필수) | 필수 |
| FR-C-08 | 호출 상한·재시도 | 필수 |
| FR-C-09 | 배치 트리거 | 필수 |
| FR-C-10 | 연쇄 이상 판단 | 필수 |
| FR-C-11 | Level 3 ReAct | 도전 |
| FR-C-12 | n8n 알림 워크플로 | 필수 |
| FR-C-13 | Agent 분석·승인 큐 화면 | 필수 |
| FR-C-14 | 조치 중복 방지 | 필수 |
| FR-C-15 | Fault Code 분류 평가 | 필수 |

---

## 완료 기준

```
배치 재현    빈 Agent E2E DB에서 알람 51건 → agent_run 10건 · action_history 10건
             (lot_id, chamber_id) 10 incident와 1:1
             승인 전 EQP_HOLD 3건 PENDING/WAITING, approval_request 3건 PENDING
             expected fixture ACT-0001~0010의 조치 코드 전부 일치
             LOT-260005 알람 5건 → LOT_HOLD 1건 / LOT-260004 알람 6건 → EQP_HOLD 1건
             두 번째 자동 배치 실행 시 신규 run·조치·전송 각 0건
승인         동일 건 재승인 409, EXPIRED 승인·반려 409·무변경
             승인 시 approved_by=decided_by, approved_at=decided_at
             반려 행의 approved_by/approved_at NULL
전송         정상 중복·응답 유실·timeout 재시도에서 downstream 효과 각 1회
기록         llm_model·latency_ms NULL 없음 (성공·실패 모두)
             latency_ms에 HITL 사람 대기시간 미포함
분류 평가    오프라인 51건 전수 (FOC 22 / RFM 15 / MFD 14), agent_run 추가 생성 0건
```

---

## 절대 지킬 것

**`decide_action`은 순수 함수다.** DB·LLM에 접근하지 않는다. 입력은 구조화된 증거 DTO
(`IncidentDecisionInput`·`RepeatedLotEvidence`·`NormalRecoveryProbe`)로 받는다.
자연어 사유를 다시 파싱하지 않는다. (설계 7.7)

**승인 게이트를 설정으로 우회할 수 없다.** `HITL_REQUIRED_SEVERITY=HIGH` 외의 값은
설정 검증 오류로 기동을 거부한다.

**조치는 결정 시점에 선생성한다.** 승인·반려 때 새 행을 만들지 않고 기존 행을 갱신한다. `action_history.created_by_agent_run_id`에 최초 생성 실행을 한 번만 기록하고, 수동 재실행에서 같은 action을 재사용해도 바꾸지 않는다.

**전송 payload를 State에서 만들지 않는다.** `send_action`은 저장된 `action_history`를
`FOR UPDATE`로 다시 읽어 효과 필드로 `request_hash`를 계산한다. n8n은 요청 필드로
독립 재계산해 대조하며 `action_history`를 조회하지 않는다. (설계 3.2.5·7.8)

**Tool 8회 예산에서 최초 `send_action` 1회를 예약**한다. 진단이 소비할 수 없다.
호출 수의 영속 기준은 `agent_tool_call`이며 HITL 재개·checkpoint 유실 시 `COUNT(*)`로 복원한다.

**`POST /agent/runs`는 즉시 202를 반환**한다. HTTP 안에서 LLM·Tool을 동기 수행하지 않는다.
background task로 실행하고 React는 2초 간격 polling한다. (설계 10.4)

**`PostgresSaver.setup()`을 애플리케이션 시작 시 호출하지 않는다.**
`backend/scripts/init_checkpoint.py` 운영 명령으로만 최초 1회 실행한다. (설계 8장)

**incident key는 NOT NULL이다.** `agent_run.lot_id`·`chamber_id`, `action_history.chamber_id`를
반드시 채운다. 부분 고유 인덱스는 NULL에서 무력화된다. (설계 3.2.6·3.2.8)

---

## incident와 대표 알람

```
incident key   (lot_id, chamber_id)
대표 알람      occurred_at ASC, alarm_id ASC 첫 행
agent_run.alarm_id                     대표 alarm_id
agent_run.requested_alarm_id           수동 실행 시 사용자가 준 alarm_id
action_history.trigger_alarm_lot_hist_id   대표 알람의 lot_hist_id
전체 alarm_ids  agent_run_alarm + evidence_json.incident.alarm_ids 양쪽
```

**R03 알람을 무조건 대표로 고르지 않는다.** 위 규칙이 배포 fixture 10건과 일치한다. (설계 4.1)

---

## 수동 실행 상태표

| 최신 실행 | 처리 |
|---|---|
| 이력 없음 | 신규 실행 |
| RUNNING·WAITING_APPROVAL | 409 `INCIDENT_ALREADY_RUNNING` |
| COMPLETED | 409 `INCIDENT_ALREADY_PROCESSED` |
| FAILED | 명시적 수동 재실행 허용 |

자동 배치는 **이력이 하나라도 있으면 상태와 무관하게 건너뛴다.**

---

## 감사 이벤트 9종 (고정 — 새로 추가 금지)

```
DETECTION_COMPLETED  AGENT_RUN_STARTED  CLASSIFICATION_COMPLETED
APPROVAL_REQUESTED  APPROVAL_DECIDED  ACTION_SENT  ACTION_SEND_FAILED
AGENT_RUN_COMPLETED  AGENT_RUN_FAILED
```

조치를 생략해도 `ACTION_SKIPPED` 같은 이벤트를 만들지 않는다.
`CLASSIFICATION_COMPLETED.after_json`과 `AGENT_RUN_COMPLETED.detail`에 사유를 남긴다.

---

## API · 화면

```http
POST /agent/runs                      {alarm_id} → 202
GET  /agent/runs                      status?, equipment_id?, chamber_id?, date_from?, date_to?, page, size
GET  /agent/runs/{run_id}
GET  /approvals                       status?, page, size
POST /approvals/{approval_id}/decision  {decision, decided_by, decision_comment?}
GET  /actions                         approval_status?, send_status?, action_code?, equipment_id?,
                                      chamber_id?, date_from?, date_to?, page, size
GET  /actions/{action_id}
```

| 경로 | 내용 |
|---|---|
| `/actions` | 조치 목록 — 승인 대기 기본 필터, 전송 상태, Agent 실행 상세 이동 |
| `/agent-runs/:runId` | 센서·관계·문서 근거, 권고 조치, 승인/반려 |
| `/alarms/:alarmId` | Agent 분석 결과 요약 (A와 공동) |

---

## 원본 절

```
설계 3.2   신규 테이블·컬럼·인덱스        설계 4.1~4.4  incident·동시성·배치
설계 7.1~7.3  State·Node·Level             설계 7.4·7.4.1  Tool 예산
설계 7.5   조치 생성·승인 트랜잭션·복구    설계 7.6  Fault 분류·오프라인 평가
설계 7.7   decide_action                   설계 7.8  전송 멱등성·SENDING 복구
설계 8장   Checkpoint 초기화               설계 10.4  C API DTO
설계 11장  감사로그                        요구사항 5.3·8.2·8.3·부록 B
```
