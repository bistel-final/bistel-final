// 감사로그 fixture — append-only, 조회 전용 (쓰기 UI 없음)
// TODO(api): entity_id 파라미터 제안 반영 대기 — 클라이언트 필터로 구현
//
// ACT-0002 생애주기는 디자인 v2 감사로그 시안의 실측 시각 그대로:
//   RUN_STARTED 07:15:02 → APPROVAL_REQUESTED 07:21:23(=action created_at, 동일 트랜잭션)
//   → ACTION_APPROVED 07:31:23 (HUMAN·bang) → SEND_STARTED 07:31:23 → SENT 07:31:43 → RUN_COMPLETED 07:31:44
// 나머지 이벤트 시각은 분 단위 실측(incident 최초 알람 / action created_at).
// TODO(data): 자동 조치 8건의 초 단위 전송 시각은 audit_log CSV 확보 후 교체.
// 이벤트 유형별 집계 10·8·3·1·0·8·8·0·0 (총 38건) — 시안과 일치 검증.

export const AUDIT_EVENT_TYPES = [
  "AGENT_RUN_STARTED",
  "AGENT_RUN_COMPLETED",
  "APPROVAL_REQUESTED",
  "ACTION_APPROVED",
  "ACTION_REJECTED",
  "ACTION_SEND_STARTED",
  "ACTION_SENT",
  "ACTION_SEND_FAILED",
  "AGENT_RUN_FAILED"
]

export const AUDIT_LOGS = [
  {
    "ev": "APPROVAL_REQUESTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "approval_request",
    "entity_id": "APR-0003",
    "at": "2026-06-04 07:11",
    "before": null,
    "after": "status = PENDING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260604-0010",
    "at": "2026-06-04 07:10",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0008",
    "at": "2026-06-03 23:14",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0008",
    "at": "2026-06-03 23:14",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0009",
    "at": "2026-06-03 23:14",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0009",
    "at": "2026-06-03 23:12",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0009",
    "at": "2026-06-03 22:29",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0009",
    "at": "2026-06-03 22:29",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0008",
    "at": "2026-06-03 22:29",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0008",
    "at": "2026-06-03 22:26",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0006",
    "at": "2026-06-03 15:45",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0006",
    "at": "2026-06-03 15:45",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0007",
    "at": "2026-06-03 15:45",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0007",
    "at": "2026-06-03 15:43",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0007",
    "at": "2026-06-03 14:43",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0007",
    "at": "2026-06-03 14:43",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0006",
    "at": "2026-06-03 14:43",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0006",
    "at": "2026-06-03 14:39",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "APPROVAL_REQUESTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "approval_request",
    "entity_id": "APR-0002",
    "at": "2026-06-03 06:39",
    "before": null,
    "after": "status = PENDING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0005",
    "at": "2026-06-03 06:37",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0004",
    "at": "2026-06-02 22:42",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0004",
    "at": "2026-06-02 22:42",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0004",
    "at": "2026-06-02 22:42",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0004",
    "at": "2026-06-02 22:38",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0003",
    "at": "2026-06-02 15:20",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0003",
    "at": "2026-06-02 15:20",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0003",
    "at": "2026-06-02 15:20",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0003",
    "at": "2026-06-02 15:19",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0002",
    "at": "2026-06-02 07:31:44",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0002",
    "at": "2026-06-02 07:31:43",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "ACTION_APPROVED",
    "ac": "HUMAN",
    "subject": "bang",
    "entity": "approval_request",
    "entity_id": "APR-0001",
    "at": "2026-06-02 07:31:23",
    "before": "status = PENDING",
    "after": "status = APPROVED",
    "note": null
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0002",
    "at": "2026-06-02 07:31:23",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "APPROVAL_REQUESTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "approval_request",
    "entity_id": "APR-0001",
    "at": "2026-06-02 07:21:23",
    "before": null,
    "after": "status = PENDING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0002",
    "at": "2026-06-02 07:15:02",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "ac": "SYSTEM",
    "subject": "backend",
    "entity": "action_history",
    "entity_id": "ACT-0001",
    "at": "2026-06-01 23:20",
    "before": "send_status = WAITING",
    "after": "send_status = SENDING",
    "note": null
  },
  {
    "ev": "ACTION_SENT",
    "ac": "SYSTEM",
    "subject": "n8n",
    "entity": "action_history",
    "entity_id": "ACT-0001",
    "at": "2026-06-01 23:20",
    "before": "send_status = SENDING",
    "after": "send_status = SENT",
    "note": null
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260601-0001",
    "at": "2026-06-01 23:20",
    "before": "status = RUNNING",
    "after": "status = COMPLETED",
    "note": null
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "agent_run",
    "entity_id": "RUN-20260601-0001",
    "at": "2026-06-01 23:17",
    "before": null,
    "after": "status = RUNNING",
    "note": "신규 생성 — before 없음"
  }
]
