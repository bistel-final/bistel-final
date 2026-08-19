# C — Agent · HITL

> 기준: 멘토 최종 패키지 (2026-08-18) · 역할분담 v10.0 C · WBS `V4-C-*`
> 마지막 동기화: 2026-08-18

## 담당 범위

LangGraph Agent(incident 단위 실행), 조치 결정·승인 흐름(HITL), n8n 연동
(이메일·MES), Agent 분석 화면(3).

## 최소 구현 (골든 시나리오)

```
GET  /agent/runs                     실행 이력 (tools·latency_ms·llm_model 포함)
GET  /approvals                      EQP_HOLD 승인 목록
POST /approvals/{id}/decision        승인/반려 → 승인 시 n8n MES 워크플로 호출
GET  /audit-logs                     action_history 파생 이벤트 (D 와 담당 협의 중)
```

## 핵심 규칙

- incident = (lot_id, chamber_id) 알람 조합 = 실행 1건 = action_history 1행.
  `graph.invoke` 는 대표 alarm_id 를 받아 내부 확장.
- `decide_action` 은 규칙 기반 순수 함수. 3단계 결정표는 `02-domain-rules.md` 4장.
- fault_code 분류 = 대표 parameter → 고장모드 매핑 (매핑 불가 OTH).
  `lot_history.fault_code` 를 읽으면 데이터 누수다.
- 승인·반려는 기존 행 갱신 (`approval_status/approved_by/approved_at`).
  승인 후에만 MES: Kafka `fdc.actions` 발행 + 목업 컨슈머 (대안 REST).
- 이메일은 n8n 경유 실제 SMTP. 워크플로 WF1~WF4 는 패키지 06·07 가이드.
- 실행 로그에 `tools:[{name,status}]`·`latency_ms`·`llm_model` 을 남긴다 —
  `GET /agent/runs` 스펙이 요구.
- `langgraph==0.2.53` 고정 · Tool 계약 `{ok, ..., reason}`.
