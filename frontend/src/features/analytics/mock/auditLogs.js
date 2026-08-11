<<<<<<< Updated upstream
// 감사로그 fixture — 명세 AuditLogItem 스키마 (append-only, 조회 전용)
// TODO(api): entity_id 파라미터는 명세에 없다 — 클라이언트 필터로 처리
// before/after 는 dict 다 (문자열 아님). 화면은 "키 = 값"으로 렌더한다.
// TODO(data): 자동 조치 건의 초 단위 전송 시각은 audit_log CSV 확보 후 교체.
=======
// 감사로그 fixture — append-only, 조회 전용 (쓰기 UI 없음)
// 실 API와 동일한 감사 이벤트 9종만 사용한다. 전송 예약(SENDING)은 action_history
// 상태이며 별도 감사 이벤트가 아니다.
//
// ACT-0002 생애주기:
//   AGENT_RUN_STARTED 07:15:02 → APPROVAL_REQUESTED 07:21:23
//   → APPROVAL_DECIDED 07:31:23 (HUMAN·bang) → ACTION_SENT 07:31:43
//   → AGENT_RUN_COMPLETED 07:31:44
// 나머지 이벤트 시각은 분 단위 실측(incident 최초 알람 / action created_at).
// TODO(data): 자동 조치 8건의 초 단위 전송 시각은 audit_log CSV 확보 후 교체.
// 배포 fixture에 없는 이벤트는 집계 0건으로 표시한다.
>>>>>>> Stashed changes

export const AUDIT_EVENT_TYPES = [
  "DETECTION_COMPLETED",
  "AGENT_RUN_STARTED",
  "CLASSIFICATION_COMPLETED",
  "APPROVAL_REQUESTED",
  "APPROVAL_DECIDED",
  "ACTION_SENT",
  "ACTION_SEND_FAILED",
  "AGENT_RUN_COMPLETED",
  "AGENT_RUN_FAILED"
]

export const AUDIT_LOGS = [
  {
    "audit_id": 38,
    "occurred_at": "2026-06-04 07:11:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "APPROVAL_REQUESTED",
    "entity_type": "approval_request",
    "entity_id": "APR-0003",
    "before": null,
    "after": {
      "status": "PENDING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
    "audit_id": 37,
    "occurred_at": "2026-06-04 07:10:41",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260604-0010",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 32,
    "occurred_at": "2026-06-03 23:14:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260603-0009",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 31,
    "occurred_at": "2026-06-03 23:14:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0008",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 30,
    "occurred_at": "2026-06-03 23:14:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0008",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 29,
    "occurred_at": "2026-06-03 23:12:41",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260603-0009",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 36,
    "occurred_at": "2026-06-03 22:29:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260603-0008",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 35,
    "occurred_at": "2026-06-03 22:29:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0009",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 34,
    "occurred_at": "2026-06-03 22:29:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0009",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 33,
    "occurred_at": "2026-06-03 22:26:40",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260603-0008",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 24,
    "occurred_at": "2026-06-03 15:45:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260603-0007",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 23,
    "occurred_at": "2026-06-03 15:45:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0006",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 22,
    "occurred_at": "2026-06-03 15:45:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0006",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 21,
    "occurred_at": "2026-06-03 15:43:05",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260603-0007",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 28,
    "occurred_at": "2026-06-03 14:43:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260603-0006",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 27,
    "occurred_at": "2026-06-03 14:43:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0007",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 26,
    "occurred_at": "2026-06-03 14:43:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0007",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 25,
    "occurred_at": "2026-06-03 14:39:36",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260603-0006",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
    "audit_id": 20,
    "occurred_at": "2026-06-03 06:39:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "APPROVAL_REQUESTED",
    "entity_type": "approval_request",
    "entity_id": "APR-0002",
    "before": null,
    "after": {
      "status": "PENDING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
    "audit_id": 19,
    "occurred_at": "2026-06-03 06:37:47",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260603-0005",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 18,
    "occurred_at": "2026-06-02 22:42:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260602-0004",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 17,
    "occurred_at": "2026-06-02 22:42:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0004",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 16,
    "occurred_at": "2026-06-02 22:42:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0004",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 15,
    "occurred_at": "2026-06-02 22:38:32",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260602-0004",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 14,
    "occurred_at": "2026-06-02 15:20:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260602-0003",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 13,
    "occurred_at": "2026-06-02 15:20:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0003",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 12,
    "occurred_at": "2026-06-02 15:20:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0003",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 11,
    "occurred_at": "2026-06-02 15:19:49",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260602-0003",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
    "audit_id": 10,
    "occurred_at": "2026-06-02 07:31:44",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260602-0002",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 9,
    "occurred_at": "2026-06-02 07:31:43",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0002",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
<<<<<<< Updated upstream
    "audit_id": 8,
    "occurred_at": "2026-06-02 07:31:23",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0002",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 7,
    "occurred_at": "2026-06-02 07:31:23",
    "actor_type": "HUMAN",
    "actor_id": "bang",
    "event_type": "ACTION_APPROVED",
    "entity_type": "approval_request",
    "entity_id": "APR-0001",
    "before": {
      "status": "PENDING"
    },
    "after": {
      "status": "APPROVED"
    },
    "detail": null
  },
  {
    "audit_id": 6,
    "occurred_at": "2026-06-02 07:21:23",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "APPROVAL_REQUESTED",
    "entity_type": "approval_request",
=======
    "ev": "APPROVAL_DECIDED",
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
    "ev": "APPROVAL_REQUESTED",
    "ac": "AGENT",
    "subject": "system",
    "entity": "approval_request",
>>>>>>> Stashed changes
    "entity_id": "APR-0001",
    "before": null,
    "after": {
      "status": "PENDING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
    "audit_id": 5,
    "occurred_at": "2026-06-02 07:15:02",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260602-0002",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  },
  {
<<<<<<< Updated upstream
    "audit_id": 4,
    "occurred_at": "2026-06-01 23:20:00",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_COMPLETED",
    "entity_type": "agent_run",
=======
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
>>>>>>> Stashed changes
    "entity_id": "RUN-20260601-0001",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    },
    "detail": null
  },
  {
    "audit_id": 3,
    "occurred_at": "2026-06-01 23:20:00",
    "actor_type": "SYSTEM",
    "actor_id": "n8n",
    "event_type": "ACTION_SENT",
    "entity_type": "action_history",
    "entity_id": "ACT-0001",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    },
    "detail": null
  },
  {
    "audit_id": 2,
    "occurred_at": "2026-06-01 23:20:00",
    "actor_type": "SYSTEM",
    "actor_id": "backend",
    "event_type": "ACTION_SEND_STARTED",
    "entity_type": "action_history",
    "entity_id": "ACT-0001",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    },
    "detail": null
  },
  {
    "audit_id": 1,
    "occurred_at": "2026-06-01 23:17:23",
    "actor_type": "AGENT",
    "actor_id": "system",
    "event_type": "AGENT_RUN_STARTED",
    "entity_type": "agent_run",
    "entity_id": "RUN-20260601-0001",
    "before": null,
    "after": {
      "status": "RUNNING"
    },
    "detail": "신규 생성 — before 없음"
  }
]
