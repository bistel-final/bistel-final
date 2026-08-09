// 감사로그 fixture — append-only, 조회 전용 (쓰기 UI 없음)
// TODO(api): entity_id 파라미터 제안 반영 대기 — 현 GET /audit-logs 명세에 대상 ID 필터가 없어 클라이언트 필터로 구현
//
// 이벤트 유형별 집계 10·8·3·1·0·8·8·0·0 (총 38건)은 다음과 자기정합한다:
//   런 10건 · 완료 8건 · EQP_HOLD 3건 승인 요청 · 승인 1건(ACT-0002) · 전송 8건 · 실패/반려 0건
// TODO(data): 시각은 분 단위. AGENT_RUN_STARTED는 incident 최초 알람 occurred_at(실측),
//   ACT-0002 승인은 §2가 제공한 06-02 07:21(실측), 나머지 전송 이벤트는 그 앵커에 정렬했다.
//   초 단위와 개별 전송 시각은 audit_log CSV 확보 후 교체 필요.

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
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "approval_request",
    "entity_id": "APR-0010",
    "at": "2026-06-04 07:11",
    "summary": "EQP_HOLD 승인 요청 (ACT-0010)",
    "before": null,
    "after": {
      "status": "PENDING"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260604-0010",
    "at": "2026-06-04 07:10",
    "summary": "ALM-0046 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0009",
    "at": "2026-06-03 23:14",
    "summary": "분석 완료 · ACT-0008",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0008",
    "at": "2026-06-03 23:14",
    "summary": "MES 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0008",
    "at": "2026-06-03 23:14",
    "summary": "MES 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0009",
    "at": "2026-06-03 23:12",
    "summary": "ALM-0041 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0008",
    "at": "2026-06-03 22:26",
    "summary": "ALM-0034 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0008",
    "at": "2026-06-03 22:26",
    "summary": "분석 완료 · ACT-0009",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0009",
    "at": "2026-06-03 22:26",
    "summary": "MES 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0009",
    "at": "2026-06-03 22:26",
    "summary": "MES 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0007",
    "at": "2026-06-03 15:43",
    "summary": "ALM-0031 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0007",
    "at": "2026-06-03 15:43",
    "summary": "분석 완료 · ACT-0006",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0006",
    "at": "2026-06-03 15:43",
    "summary": "EMAIL 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0006",
    "at": "2026-06-03 15:43",
    "summary": "EMAIL 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0006",
    "at": "2026-06-03 14:39",
    "summary": "ALM-0025 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0006",
    "at": "2026-06-03 14:39",
    "summary": "분석 완료 · ACT-0007",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0007",
    "at": "2026-06-03 14:39",
    "summary": "MES 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0007",
    "at": "2026-06-03 14:39",
    "summary": "MES 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "APPROVAL_REQUESTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "approval_request",
    "entity_id": "APR-0005",
    "at": "2026-06-03 06:39",
    "summary": "EQP_HOLD 승인 요청 (ACT-0005)",
    "before": null,
    "after": {
      "status": "PENDING"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260603-0005",
    "at": "2026-06-03 06:37",
    "summary": "ALM-0019 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0004",
    "at": "2026-06-02 22:38",
    "summary": "ALM-0016 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0004",
    "at": "2026-06-02 22:38",
    "summary": "분석 완료 · ACT-0004",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0004",
    "at": "2026-06-02 22:38",
    "summary": "EMAIL 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0004",
    "at": "2026-06-02 22:38",
    "summary": "EMAIL 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0003",
    "at": "2026-06-02 15:19",
    "summary": "ALM-0011 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0003",
    "at": "2026-06-02 15:19",
    "summary": "분석 완료 · ACT-0003",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0003",
    "at": "2026-06-02 15:19",
    "summary": "MES 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0003",
    "at": "2026-06-02 15:19",
    "summary": "MES 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0002",
    "at": "2026-06-02 07:21",
    "summary": "분석 완료 · ACT-0002",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_APPROVED",
    "actor": "bang",
    "ac": "HUMAN",
    "entity": "action_history",
    "entity_id": "ACT-0002",
    "at": "2026-06-02 07:21",
    "summary": "EQP_HOLD 승인",
    "before": {
      "approval_status": "PENDING"
    },
    "after": {
      "approval_status": "APPROVED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0002",
    "at": "2026-06-02 07:21",
    "summary": "MES 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0002",
    "at": "2026-06-02 07:21",
    "summary": "MES 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260602-0002",
    "at": "2026-06-02 07:20",
    "summary": "ALM-0005 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "APPROVAL_REQUESTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "approval_request",
    "entity_id": "APR-0002",
    "at": "2026-06-02 07:20",
    "summary": "EQP_HOLD 승인 요청 (ACT-0002)",
    "before": null,
    "after": {
      "status": "PENDING"
    }
  },
  {
    "ev": "AGENT_RUN_STARTED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260601-0001",
    "at": "2026-06-01 23:17",
    "summary": "ALM-0001 incident 분석 시작",
    "before": null,
    "after": {
      "status": "RUNNING"
    }
  },
  {
    "ev": "AGENT_RUN_COMPLETED",
    "actor": "fdc-agent",
    "ac": "AGENT",
    "entity": "agent_run",
    "entity_id": "RUN-20260601-0001",
    "at": "2026-06-01 23:17",
    "summary": "분석 완료 · ACT-0001",
    "before": {
      "status": "RUNNING"
    },
    "after": {
      "status": "COMPLETED"
    }
  },
  {
    "ev": "ACTION_SEND_STARTED",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0001",
    "at": "2026-06-01 23:17",
    "summary": "EMAIL 전송 시작",
    "before": {
      "send_status": "WAITING"
    },
    "after": {
      "send_status": "SENDING"
    }
  },
  {
    "ev": "ACTION_SENT",
    "actor": "SYSTEM",
    "ac": "SYSTEM",
    "entity": "action_history",
    "entity_id": "ACT-0001",
    "at": "2026-06-01 23:17",
    "summary": "EMAIL 전송 완료",
    "before": {
      "send_status": "SENDING"
    },
    "after": {
      "send_status": "SENT"
    }
  }
]
