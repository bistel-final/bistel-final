// 조치 fixture — 명세 ActionDetailResponse / ApprovalItem 스키마
// TODO(api): 실서버 연결 시 이 파일만 교체.
// incident = (lot_id, chamber_id). send_channel 은 channel 이 아니다.

export const ACTIONS = [
  {
    "action_id": "ACT-0001",
    "created_by_agent_run_id": "RUN-20260601-0001",
    "incident": {
      "lot_id": "LOT-260003",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "alarm_ids": [
      "ALM-0001",
      "ALM-0002",
      "ALM-0003",
      "ALM-0004"
    ],
    "alarm_count": 4,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "MONITOR",
    "reason": "OOC만 발생",
    "severity": "LOW",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-01 23:20",
    "send_channel": "EMAIL",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-01 23:20",
    "created_at": "2026-06-01 23:20"
  },
  {
    "action_id": "ACT-0002",
    "created_by_agent_run_id": "RUN-20260602-0002",
    "incident": {
      "lot_id": "LOT-260004",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "alarm_ids": [
      "ALM-0005",
      "ALM-0006",
      "ALM-0007",
      "ALM-0008",
      "ALM-0009",
      "ALM-0010"
    ],
    "alarm_count": 6,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "EQP_HOLD",
    "reason": "R03_CONSEC — 연속 3 WAFER OOS",
    "severity": "HIGH",
    "approval_required": true,
    "approval_status": "APPROVED",
    "approved_by": "bang",
    "approved_at": "2026-06-02 07:31:23",
    "send_channel": "MES",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-02 07:21:23",
    "created_at": "2026-06-02 07:21:23"
  },
  {
    "action_id": "ACT-0003",
    "created_by_agent_run_id": "RUN-20260602-0003",
    "incident": {
      "lot_id": "LOT-260005",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "alarm_ids": [
      "ALM-0011",
      "ALM-0012",
      "ALM-0013",
      "ALM-0014",
      "ALM-0015"
    ],
    "alarm_count": 5,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "LOT_HOLD",
    "reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-02 15:20",
    "send_channel": "MES",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-02 15:20",
    "created_at": "2026-06-02 15:20"
  },
  {
    "action_id": "ACT-0004",
    "created_by_agent_run_id": "RUN-20260602-0004",
    "incident": {
      "lot_id": "LOT-260006",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "alarm_ids": [
      "ALM-0016",
      "ALM-0017",
      "ALM-0018"
    ],
    "alarm_count": 3,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "MONITOR",
    "reason": "OOC만 발생",
    "severity": "LOW",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-02 22:42",
    "send_channel": "EMAIL",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-02 22:42",
    "created_at": "2026-06-02 22:42"
  },
  {
    "action_id": "ACT-0005",
    "created_by_agent_run_id": "RUN-20260603-0005",
    "incident": {
      "lot_id": "LOT-260007",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "alarm_ids": [
      "ALM-0019",
      "ALM-0020",
      "ALM-0021",
      "ALM-0022",
      "ALM-0023",
      "ALM-0024"
    ],
    "alarm_count": 6,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "EQP_HOLD",
    "reason": "R03_CONSEC — 연속 3 WAFER OOS",
    "severity": "HIGH",
    "approval_required": true,
    "approval_status": "PENDING",
    "approved_by": null,
    "approved_at": null,
    "send_channel": "MES",
    "send_status": "WAITING",
    "send_attempt_count": 0,
    "sent_at": null,
    "created_at": "2026-06-03 06:39"
  },
  {
    "action_id": "ACT-0006",
    "created_by_agent_run_id": "RUN-20260603-0007",
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "alarm_ids": [
      "ALM-0031",
      "ALM-0032",
      "ALM-0033"
    ],
    "alarm_count": 3,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "MONITOR",
    "reason": "OOC만 발생",
    "severity": "LOW",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-03 15:45",
    "send_channel": "EMAIL",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-03 15:45",
    "created_at": "2026-06-03 15:45"
  },
  {
    "action_id": "ACT-0007",
    "created_by_agent_run_id": "RUN-20260603-0006",
    "incident": {
      "lot_id": "LOT-260008",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "alarm_ids": [
      "ALM-0025",
      "ALM-0026",
      "ALM-0027",
      "ALM-0028",
      "ALM-0029",
      "ALM-0030"
    ],
    "alarm_count": 6,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "LOT_HOLD",
    "reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-03 14:43",
    "send_channel": "MES",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-03 14:43",
    "created_at": "2026-06-03 14:43"
  },
  {
    "action_id": "ACT-0008",
    "created_by_agent_run_id": "RUN-20260603-0009",
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "alarm_ids": [
      "ALM-0041",
      "ALM-0042",
      "ALM-0043",
      "ALM-0044",
      "ALM-0045"
    ],
    "alarm_count": 5,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "LOT_HOLD",
    "reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-03 23:14",
    "send_channel": "MES",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-03 23:14",
    "created_at": "2026-06-03 23:14"
  },
  {
    "action_id": "ACT-0009",
    "created_by_agent_run_id": "RUN-20260603-0008",
    "incident": {
      "lot_id": "LOT-260009",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "alarm_ids": [
      "ALM-0034",
      "ALM-0035",
      "ALM-0036",
      "ALM-0037",
      "ALM-0038",
      "ALM-0039",
      "ALM-0040"
    ],
    "alarm_count": 7,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "LOT_HOLD",
    "reason": "DISTINCT wafer_no 기준 OOS 3장 이상",
    "severity": "MEDIUM",
    "approval_required": false,
    "approval_status": "AUTO",
    "approved_by": "system",
    "approved_at": "2026-06-03 22:29",
    "send_channel": "MES",
    "send_status": "SENT",
    "send_attempt_count": 1,
    "sent_at": "2026-06-03 22:29",
    "created_at": "2026-06-03 22:29"
  },
  {
    "action_id": "ACT-0010",
    "created_by_agent_run_id": "RUN-20260604-0010",
    "incident": {
      "lot_id": "LOT-260010",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "alarm_ids": [
      "ALM-0046",
      "ALM-0047",
      "ALM-0048",
      "ALM-0049",
      "ALM-0050",
      "ALM-0051"
    ],
    "alarm_count": 6,
    "trigger_alarm_lot_hist_id": null,
    "action_code": "EQP_HOLD",
    "reason": "R03_CONSEC — 연속 3 WAFER OOS",
    "severity": "HIGH",
    "approval_required": true,
    "approval_status": "PENDING",
    "approved_by": null,
    "approved_at": null,
    "send_channel": "MES",
    "send_status": "WAITING",
    "send_attempt_count": 0,
    "sent_at": null,
    "created_at": "2026-06-04 07:11"
  }
]

// 승인 요청 — APR ID 는 요청 시각 오름차순
export const APPROVALS = [
  {
    "approval_id": "APR-0001",
    "agent_run_id": "RUN-20260602-0002",
    "action_id": "ACT-0002",
    "incident": {
      "lot_id": "LOT-260004",
      "chamber_id": "ETC-01-C2"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "action_code": "EQP_HOLD",
    "severity": "HIGH",
    "requested_at": "2026-06-02 07:21:23",
    "status": "APPROVED",
    "decided_by": "bang",
    "decided_at": "2026-06-02 07:31:23",
    "decision_comment": "설비 정지 승인"
  },
  {
    "approval_id": "APR-0002",
    "agent_run_id": "RUN-20260603-0005",
    "action_id": "ACT-0005",
    "incident": {
      "lot_id": "LOT-260007",
      "chamber_id": "PHO-01-C1"
    },
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "action_code": "EQP_HOLD",
    "severity": "HIGH",
    "requested_at": "2026-06-03 06:39",
    "status": "PENDING",
    "decided_by": null,
    "decided_at": null,
    "decision_comment": null
  },
  {
    "approval_id": "APR-0003",
    "agent_run_id": "RUN-20260604-0010",
    "action_id": "ACT-0010",
    "incident": {
      "lot_id": "LOT-260010",
      "chamber_id": "ETC-01-C1"
    },
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "action_code": "EQP_HOLD",
    "severity": "HIGH",
    "requested_at": "2026-06-04 07:11",
    "status": "PENDING",
    "decided_by": null,
    "decided_at": null,
    "decision_comment": null
  }
]

export const ACTION_TABS = [
  { key: 'PENDING', label: '승인 대기' },
  { key: 'FAILED', label: '전송 실패' },
  { key: 'SENDING', label: '진행 중' },
  { key: 'SENT', label: '완료' },
  { key: 'ALL', label: '전체' },
]
