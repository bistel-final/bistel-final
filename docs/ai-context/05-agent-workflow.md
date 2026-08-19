# 05. Agent 워크플로

> 기준 원천: 멘토 최종 패키지 02_API 가이드 화면 3 · 05_답변 Q3·Q4·Q6 (2026-08-18)
> 보조 기준: 시스템설계서 v2.0 6~7장 (State·Node 상세는 설계서가 정본)
> 마지막 동기화: 2026-08-18

---

## 1. 실행 단위

```
incident = 알람이 난 (lot_id, chamber_id) 조합 1건 = Agent 실행(agent_run) 1건
현 데이터 기준 10건. graph.invoke 는 대표 alarm_id 를 받아 내부에서 incident 로 확장
action_history 1행 = Agent 실행 1건 (GET /agent/runs 의 원천)
```

## 2. 그래프 골격

- `langgraph==0.2.53` 고정. State·Node·Level 구조의 정본은 설계 6.2~6.3.
- Tool 은 5종 계약(`{ok, ..., reason}`)만 통해 호출한다. 예외를 그래프 밖으로
  던지지 않는다. Tool 호출 예산·재시도는 설계 6.6 · FR-C-08.
- LLM 산출물(원인 가설·신뢰도)은 **참고 정보**다. 조치 결정에 직접 개입하지 않는다.

## 3. 조치 결정 — `decide_action`

- **규칙 기반 순수 함수.** LLM·DB 접근 금지.
- 3단계 결정표는 `02-domain-rules.md` 4장이 정본:
  OOC만→MONITORING · OOS 1~2→WARNING · R03(연속 3 OOS)→EQP_HOLD.
- `anomaly_score` 는 조치 규칙에 직접 반영하지 않는다 (보조 근거).
- fault_code 분류: 대표 parameter → 고장모드 매핑 (FOC/RFM/MFD/TMD, 매핑 불가 OTH).
  **`lot_history.fault_code`(정답 라벨)를 읽어 분류하면 안 된다** — 데이터 누수.

## 4. 승인·통지·MES (HITL)

```
MONITORING   자동 (approval_status=AUTO) · 통지 없음(관찰)
WARNING      자동 · n8n 이메일 통지 (notify_status=SENT)
EQP_HOLD     approval_required='Y' · approval_status=PENDING
             → 이메일 승인 요청 → 사람 결정
             → APPROVED: n8n MES 워크플로 호출, mes_status WAITING→SENT
                (Kafka fdc.actions 발행 + 목업 컨슈머 / 대안 REST)
             → REJECTED: MES 미발행
```

- 승인·반려는 `POST /approvals/{id}/decision` 이 기존 action 행을 **갱신**한다
  (`approval_status` · `approved_by` · `approved_at`). 새 행 생성 없음.
- 전송 실패·재시도·멱등성 처리의 정본은 설계 7장.

## 5. 실행 로그·노출

- `GET /agent/runs` 가 요구하는 필드를 실행 로그에 남긴다:
  `tools:[{name,status}]` · `latency_ms` · `llm_model` · `confidence` ·
  `recommended_action` · `status(COMPLETED|WAITING_APPROVAL)`.
- 감사 추적은 별도 테이블이 아니라 **action_history 의 이벤트 시각 컬럼 파생**이다
  (`GET /audit-logs`: ACTION_RECOMMEND/NOTIFY/APPROVE/REJECT/SEND × AGENT/HUMAN/SYSTEM).

## 6. 실시간 처리

- 실시간 스트리밍은 범위 밖 (요구사항 2.2). 시뮬레이터 + polling 이 정본 접근이다.
- MES 목업 컨슈머는 Kafka `fdc.actions` 토픽을 구독하는 형태로 구성한다 (패키지 n8n 가이드).
